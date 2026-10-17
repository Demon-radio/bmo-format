"""BMO -> SVG (documents, graphics, slides). Reference renderer v0.10.
Honest limits (see RFC-0010): one line per run (no line breaking without
font metrics — MUST NOT fake-wrap), logical order + bidi attributes and
let the text engine shape; static slides (transitions declared, not played).
"""
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # local package (load/sheet_html siblings)

PAGE_W, MARGIN, LINE_H = 800, 60, 26
BMU_PX = 0.0037795  # thousandths of mm -> px @96dpi
SLIDE_W, SLIDE_H = 960, 540

def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

class Ctx:
    def __init__(self, model):
        self.m = model
        self.by_id = model["by_id"]
        self.children = model["children"]
        self.list_counters = {}

    def style_of(self, body):
        st = body.get("style") if isinstance(body, dict) else None
        if isinstance(st, dict) and "$ref" in st:
            t = self.by_id.get(st["$ref"], {})
            rm = (t.get("body") or {}).get("run", {}) if isinstance(t, dict) else {}
            return rm if isinstance(rm, dict) else {}
        return {}

    def font_attrs(self, rm):
        a = ""
        if rm.get("b"):
            a += ' font-weight="bold"'
        if rm.get("i"):
            a += ' font-style="italic"'
        if isinstance(rm.get("size"), int):
            a += f' font-size="{rm["size"] * 96 // 72}"'
        if isinstance(rm.get("color"), str) and len(rm["color"]) == 6:
            a += f' fill="#{rm["color"]}"'
        return a

    def text_el(self, text, x, y, rm, direction=None, anchor=None, lang=None):
        a = f' x="{x}" y="{y}"'
        if anchor:
            a += f' text-anchor="{anchor}"'
        if direction and direction != "ltr":
            a += f' direction="{direction}" unicode-bidi="embed"'
        if lang:
            a += f' xml:lang="{esc(lang)}"'
        return f"<text{a}{self.font_attrs(rm)}>{esc(text)}</text>"

    def paragraph(self, p, x, y, width):
        b = p["body"]
        align = b.get("align", "start")
        anchor, xx = None, x
        if align == "center":
            anchor, xx = "middle", x + width // 2
        elif align in ("right", "end"):
            anchor, xx = "end", x + width
        direction = {"rtl": "rtl", "ltr": "ltr"}.get(b.get("dir"), None)
        marker = ""
        indent = 0
        li = b.get("list")
        if isinstance(li, dict) and isinstance(li.get("id"), str):
            n = self.list_counters.get(li["id"], 0) + 1
            self.list_counters[li["id"]] = n
            det = next((e for e in self.m["envs"] if e["kind"] == "list-def"
                        and e["body"].get("id") == li["id"]), None)
            style = (det or {}).get("body", {}).get("style", "bullet")
            mark = "•" if style == "bullet" else f"{n}."
            indent = li.get("level", 0) * 24 + 24
            marker = self.text_el(mark, x + li.get("level", 0) * 24, y, {}, direction)
        # ONE text element per paragraph: tspans flow inline with real font
        # metrics (no overlap, correct bidi) — the renderer lays out, we don't.
        spans = "".join(
            self.tspan_el(r["body"].get("text", ""), self.style_of(r["body"]),
                          r["body"].get("lang"))
            for r in [c for c in self.children.get(p["oid"], []) if c["kind"] == "run"])
        attrs = f' x="{xx + indent}" y="{y}"'
        if anchor:
            attrs += f' text-anchor="{anchor}"'
        if direction and direction != "ltr":
            attrs += f' direction="{direction}" unicode-bidi="embed"'
        return marker + f"<text{attrs}>{spans}</text>", LINE_H

    def tspan_el(self, text, rm, lang=None):
        a = self.font_attrs(rm)
        if lang:
            a += f' xml:lang="{esc(lang)}"'
        return f"<tspan{a}>{esc(text)}</tspan>"

    def table(self, t, x, y, width):
        rows = [c for c in self.children.get(t["oid"], []) if c["kind"] == "row"]
        cols = max((len([c for c in self.children.get(r["oid"], []) if c["kind"] == "cell"]) for r in rows), default=1)
        cw = width // max(cols, 1)
        out, yy = [], y
        for r in rows:
            cells = [c for c in self.children.get(r["oid"], []) if c["kind"] == "cell"]
            rh = LINE_H
            for c in cells:
                paras = [p for p in self.children.get(c["oid"], []) if p["kind"] == "paragraph"]
                rh = max(rh, LINE_H * max(1, len(paras)))
            for i, c in enumerate(cells):
                xx = x + i * cw
                out.append(f'<rect x="{xx}" y="{yy}" width="{cw}" height="{rh}" fill="none" stroke="#888"/>')
                py = yy + 19
                for p in [pp for pp in self.children.get(c["oid"], []) if pp["kind"] == "paragraph"]:
                    pel, _ = self.paragraph(p, xx + 6, py, cw - 12)
                    out.append(pel)
                    py += LINE_H
            yy += rh
        return "".join(out), yy - y

    def flow(self, parent_oid, x, y, width):
        out = []
        for e in self.children.get(parent_oid, []):
            if e["kind"] == "paragraph":
                pel, h = self.paragraph(e, x, y, width)
                out.append(pel)
                y += h
            elif e["kind"] == "table":
                tel, h = self.table(e, x, y, width)
                out.append(tel)
                y += h + 8
            elif e["kind"] in ("section", "page"):
                sub, y = self.flow(e["oid"], x, y, width)
                out.append(sub)
            elif e["kind"] in ("note", "text"):  # legacy + model notes: one line
                b = e["body"]
                out.append(self.text_el(b.get("text", ""), x, y, self.style_of(b),
                                        "rtl" if b.get("script") == "Arab" else None,
                                        None, b.get("lang")))
                y += LINE_H
        return "".join(out), y

    # -- graphics --
    def color(self, c, default="#000"):
        if c == "none" or c is None:
            return "none"
        if isinstance(c, dict) and isinstance(c.get("srgb"), bytes) and len(c["srgb"]) == 3:
            return "#" + c["srgb"].hex()
        return default

    def shape(self, e):
        b = e["body"]
        g = b.get("geom", {})
        fill = self.color(b.get("fill", "#000") if b.get("shape") != "line" else "none", "#000")
        ln = b.get("line", {}) if isinstance(b.get("line"), dict) else {}
        stroke = self.color(ln.get("color", "none"), "none")
        w = ln.get("w", 0) if isinstance(ln.get("w"), int) else 0
        sattr = f' fill="{fill}" stroke="{stroke}" stroke-width="{w * BMU_PX:.2f}"'
        k = b.get("shape")
        if k == "rect":
            return (f'<rect x="{g["x"] * BMU_PX:.1f}" y="{g["y"] * BMU_PX:.1f}" '
                    f'width="{g["w"] * BMU_PX:.1f}" height="{g["h"] * BMU_PX:.1f}"{sattr}/>')
        if k == "ellipse":
            return (f'<ellipse cx="{g["cx"] * BMU_PX:.1f}" cy="{g["cy"] * BMU_PX:.1f}" '
                    f'rx="{g["rx"] * BMU_PX:.1f}" ry="{g["ry"] * BMU_PX:.1f}"{sattr}/>')
        if k == "line":
            return (f'<line x1="{g["x1"] * BMU_PX:.1f}" y1="{g["y1"] * BMU_PX:.1f}" '
                    f'x2="{g["x2"] * BMU_PX:.1f}" y2="{g["y2"] * BMU_PX:.1f}"{sattr}/>')
        if k == "path":
            d = " ".join(("M" if op[0] == "M" else op[0]) + " " + " ".join(
                f"{v * BMU_PX:.1f}" for v in op[1:]) if op[0] != "Z" else "Z" for op in g["d"])
            return f'<path d="{d}"{sattr}/>'
        return ""

    def drawing(self, oid):
        out = []
        for e in self.children.get(oid, []):
            if e["kind"] == "shape":
                out.append(self.shape(e))
            elif e["kind"] == "image":
                b = e["body"]
                wpx, hpx = b.get("w", 1000) * BMU_PX, b.get("h", 1000) * BMU_PX
                sha = (b.get("asset") or {}).get("$asset", b"") if isinstance(b.get("asset"), dict) else b""
                label = f'{b.get("mime", "?")} {len(self.m["assets"].get(sha, b""))}B {sha.hex()[:12] if isinstance(sha, bytes) else "?"}…'
                out.append(f'<rect x="10" y="10" width="{wpx:.0f}" height="{hpx:.0f}" fill="#eee" stroke="#888" stroke-dasharray="6 4"/>'
                           f'<text x="18" y="30">[{esc(label)}]</text>')
            elif e["kind"] == "group":
                out.append(f"<g>{self.drawing(e['oid'])}</g>")
        return "".join(out)

def _svg(body, w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f"<desc>BMO reference renderer v0.10 — no line breaking; logical order preserved</desc>"
            f"<title>{esc(title)}</title>{body}</svg>")

def render_document(model, title="BMO"):
    ctx = Ctx(model)
    roots = [e for e in model["envs"] if e["parent"] == 0]
    body, y = ctx.flow(roots[0]["oid"] if roots else 0, MARGIN, 40, PAGE_W - 2 * MARGIN)
    h = max(y + 40, 200)
    return _svg(body, PAGE_W, h, title)

def render_deck(model, title="BMO"):
    ctx = Ctx(model)
    slides = [e for e in model["envs"] if e["kind"] == "slide"]
    slides.sort(key=lambda e: (e["index"] if e["index"] is not None else e["oid"]))
    out = []
    for s in slides:
        parts = []
        y = 60
        for c in ctx.children.get(s["oid"], []):
            if c["kind"] == "paragraph":
                pel, _ = ctx.paragraph(c, MARGIN, y, SLIDE_W - 2 * MARGIN)
                parts.append(pel)
                y += LINE_H
        # drawing children (shapes/images/groups) in one pass:
        parts.append(ctx.drawing(s["oid"]))
        out.append(_svg("".join(parts), SLIDE_W, SLIDE_H, title))
    return out

def render_canvas_page(model, title="BMO"):
    """Single page for the first canvas found (graphics demo)."""
    ctx = Ctx(model)
    canvas = next((e for e in model["envs"] if e["kind"] == "canvas"), None)
    if canvas is None:
        return render_document(model, title)
    w = max(int(canvas["body"].get("w", 210000) * BMU_PX), 200)
    h = max(int(canvas["body"].get("h", 297000) * BMU_PX), 200)
    return _svg(ctx.drawing(canvas["oid"]), w, h, title)

def check_wellformed(svg: str):
    ET.fromstring(svg)
