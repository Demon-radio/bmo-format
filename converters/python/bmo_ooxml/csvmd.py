"""CSV/Markdown -> BMO model objects (import only). Zero dependencies.
CSV: type-sniffed grid into one sheet. Markdown: headings, lists, tables,
code fences, quotes, bold/italic/code spans -> paragraphs/runs/styles.
"""
import csv
import io
import re
from .common import fidelity  # noqa: F401 (re-export for tests)

class Ctx:
    def __init__(self):
        self.next_id = 1
        self.counters = {}
        self.envs = []
        self.styles = {}
        self.stats = {"cells": 0, "paragraphs": 0, "runs": 0, "styles": 0,
                      "tables": 0, "skipped": {}}

    def new(self, kind, parent, body):
        i = self.counters.get(parent, 0)
        self.counters[parent] = i + 1
        oid = self.next_id
        self.next_id += 1
        self.envs.append({"oid": oid, "kind": kind, "parent": parent,
                          "index": i, "body": body})
        return oid

    def skip(self, what, n=1):
        self.stats["skipped"][what] = self.stats["skipped"].get(what, 0) + n

    def style(self, name, run=None):
        key = (name, tuple(sorted((run or {}).items())))
        if key not in self.styles:
            body = {"name": name}
            if run:
                body["run"] = run
            self.styles[key] = self.new("style", 1, body)
            self.stats["styles"] += 1
        return {"$ref": self.styles[key]}

def _colname(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def _sniff(raw: str):
    s = raw.strip()
    if not s:
        return None, True
    if s.lower() in ("true", "false"):
        return s.lower() == "true", False
    if re.match(r"^-?\d+$", s):
        try:
            return int(s), False
        except ValueError:
            pass
    try:
        f = float(s)
        if f == f and f not in (float("inf"), float("-inf")):
            return f, False
    except ValueError:
        pass
    return s, False

def import_csv(text: str, name="Sheet1"):
    """-> (title, envs, stats). Blank cells stay sparse (omitted)."""
    ctx = Ctx()
    wb = ctx.new("workbook", 0, {})
    cells = {}
    for r, row in enumerate(csv.reader(io.StringIO(text)), 1):
        for c, raw in enumerate(row, 1):
            v, blank = _sniff(raw)
            if blank:
                continue
            cells[f"{_colname(c)}{r}"] = {"v": v}
            ctx.stats["cells"] += 1
    sheet_name = re.sub(r"[^\w\- ]", "_", name)[:31] or "Sheet1"
    ctx.new("sheet", wb, {"name": sheet_name, "cells": cells})
    return sheet_name, ctx.envs, ctx.stats

# --- markdown ---------------------------------------------------------------

INLINE_RE = re.compile(r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|\*[^*\n]+?\*|`[^`\n]+?|\[.+?\]\(.+?\)|!\[[^\]]*?\]\(.+?\))")

def _inline(ctx, para_oid, text):
    pos, runs = 0, 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            ctx.new("run", para_oid, {"text": text[pos:m.start()]})
            runs += 1
        tok = m.group(0)
        if tok.startswith("***") and tok.endswith("***"):
            ctx.new("run", para_oid, {"text": tok[3:-3],
                                      "style": ctx.style("Emphasis", {"b": 1, "i": 1})})
            runs += 1
        elif tok.startswith("**"):
            ctx.new("run", para_oid, {"text": tok[2:-2],
                                      "style": ctx.style("Strong", {"b": 1})})
            runs += 1
        elif tok.startswith("*"):
            ctx.new("run", para_oid, {"text": tok[1:-1],
                                      "style": ctx.style("Emphasis", {"i": 1})})
            runs += 1
        elif tok.startswith("`"):
            ctx.new("run", para_oid, {"text": tok[1:-1],
                                      "style": ctx.style("CodeSpan")})
            runs += 1
        elif tok.startswith("!["):
            alt = tok[2:tok.find("]")]
            if alt:
                ctx.new("run", para_oid, {"text": alt})
                runs += 1
            ctx.skip("images")
        else:  # [text](url): keep text, drop target (counted)
            label = tok[1:tok.find("]")]
            if label:
                ctx.new("run", para_oid, {"text": label})
                runs += 1
            ctx.skip("links")
        pos = m.end()
    if pos < len(text):
        ctx.new("run", para_oid, {"text": text[pos:]})
        runs += 1
    ctx.stats["runs"] += runs

def _para(ctx, parent, text, style=None):
    body = {}
    if style:
        body["style"] = ctx.style(style)  # already a {"$ref": oid} link
    oid = ctx.new("paragraph", parent, body)
    ctx.stats["paragraphs"] += 1
    _inline(ctx, oid, text)
    return oid

def import_markdown(text: str, title="Imported Markdown"):
    ctx = Ctx()
    doc = ctx.new("document", 0, {"title": title})
    lines = text.replace("\t", "    ").split("\n")
    i, list_ids = 0, {}
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            _para(ctx, doc, m.group(2).strip(), style=f"Heading{m.group(1).count('#')}")
            i += 1
            continue
        if re.match(r"^(\*\*\*|---|___)\s*$", line):
            ctx.skip("thematic_break")
            i += 1
            continue
        if line.strip().startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            _para(ctx, doc, "\n".join(buf), style="Code")
            continue
        if line.lstrip().startswith(">"):
            _para(ctx, doc, line.lstrip()[1:].strip(), style="Quote")
            i += 1
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            j = i
            while j < len(lines) and re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[j]):
                lm = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", lines[j])
                indent, marker, content = len(lm.group(1)), lm.group(2), lm.group(3)
                ordered = bool(re.match(r"\d", marker))
                lid = "md-decimal" if ordered else "md-bullet"
                if lid not in list_ids:
                    list_ids[lid] = ctx.new("list-def", doc,
                                            {"id": lid, "style": "decimal" if ordered else "bullet"})
                p = ctx.new("paragraph", doc,
                            {"list": {"id": lid, "level": min(indent // 2, 8)}})
                ctx.stats["paragraphs"] += 1
                _inline(ctx, p, content.strip())
                j += 1
            i = j
            continue
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s*\|?[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            # rows[0]=header, rows[1]=|---| separator (guaranteed above), rest=data
            ncols = max(len(r) for r in rows)
            t = ctx.new("table", doc, {"cols": ncols})
            ctx.stats["tables"] += 1
            head, data = [rows[0]], rows[2:]  # rows[1] is the |---| separator
            cells0 = (head[0] + [""] * ncols)[:ncols]
            r0 = ctx.new("row", t, {})
            for cv in cells0:
                cc = ctx.new("cell", r0, {})
                _para(ctx, cc, cv, style="TableHeader")
            for cells in data:
                rr = ctx.new("row", t, {})
                for cv in (cells + [""] * ncols)[:ncols]:
                    cc = ctx.new("cell", rr, {})
                    _para(ctx, cc, cv)
            continue
        _para(ctx, doc, line.strip())
        i += 1
    return title, ctx.envs, ctx.stats
