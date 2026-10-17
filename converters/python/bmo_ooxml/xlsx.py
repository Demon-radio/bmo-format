"""XLSX <-> BMO (RFC-0005). Zero dependencies.
Scope v0.1: values (n/s/b/e/str/inlineStr), formulas + derived deps
(incl. range expansion), merges, number formats. Drawings/comments/
pivots are counted as skipped. Cross-sheet refs stay in formula text;
deps cover same-sheet refs only (reported, never faked).
"""
import re
from xml.etree import ElementTree as ET

from .common import CT_XML, R, SML, ct_item, emu_to_bmu, local, mime_for, opc_read, opc_write, qn, rel_item, RELS_XML, xml_bytes

BUILTIN_NUMFMTS = {0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00",
                   9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?", 13: "# ??/??",
                   14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
                   18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
                   22: "m/d/yy h:mm", 37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)",
                   39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
                   45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@"}

ADDR = r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}"
RANGE_RE = re.compile(rf"({ADDR}):({ADDR})")
REF_RE = re.compile(ADDR)
FUNC_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\(")
STR_RE = re.compile(r'"[^"]*"')

def _col_letters_to_n(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n

def _split_addr(addr: str):
    a = addr.replace("$", "")
    m = re.match(r"^([A-Za-z]{1,3})([0-9]{1,7})$", a)
    return (m.group(1).upper(), int(m.group(2))) if m else (None, None)

def _norm_addr(addr: str) -> str:
    return addr.replace("$", "")

SHEETQ = r"(?:'[^']*'|[A-Za-z_][A-Za-z0-9_.]*)!"

def extract_refs(formula: str):
    """(same_sheet_addrs, cross_sheet_count, partial). Ranges expanded."""
    text = STR_RE.sub('""', formula)
    # Strip sheet-qualified refs (with optional range tail) entirely:
    # they stay in the formula text but never become same-sheet deps.
    cross = len(re.findall(SHEETQ, text))
    text = re.sub(SHEETQ + ADDR + r"(:" + ADDR + r")?", " ", text)
    text = re.sub(SHEETQ, " ", text)
    ranges = []
    def _range(m):
        ranges.append((m.group(1), m.group(2)))
        return " "
    text = RANGE_RE.sub(_range, text)
    text = FUNC_RE.sub("(", text)
    singles = {_norm_addr(m.group(0)) for m in REF_RE.finditer(text)}
    deps, partial = set(singles), False
    for r1, r2 in ranges:
        c1, rr1 = _split_addr(r1)
        c2, rr2 = _split_addr(r2)
        if not c1 or not c2 or c1 != c2:
            partial = True
            deps.add(_norm_addr(r1))
            deps.add(_norm_addr(r2))
            continue
        n1, n2 = _col_letters_to_n(c1), _col_letters_to_n(c2)
        lo, hi = min(rr1, rr2), max(rr1, rr2)
        if (hi - lo + 1) > 10000:
            partial = True
            deps.add(_norm_addr(r1))
            deps.add(_norm_addr(r2))
            continue
        col = c1
        for r in range(lo, hi + 1):
            deps.add(f"{col}{r}")
    return sorted(deps), cross, partial

class Ctx:
    def __init__(self):
        self.next_id = 1
        self.counters = {}
        self.envs = []
        self.numfmts = {}
        self.assets = {}  # sha -> (mime, bytes)
        self.stats = {"sheets": 0, "cells": 0, "cell_types": {},
                      "formulas": 0, "merges": 0, "numfmts": 0,
                      "cross_sheet_refs": 0, "partial_ranges": 0,
                      "images_imported": 0,
                      "skipped": {}}

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

    def numfmt_for(self, wb_oid, code):
        if code in (None, "General"):
            return None
        if code not in self.numfmts:
            oid = self.new("numfmt", wb_oid, {"code": code})
            self.numfmts[code] = oid
            self.stats["numfmts"] += 1
        return {"$ref": self.numfmts[code]}

def _parse_shared_strings(parts):
    raw = parts.get("xl/sharedStrings.xml")
    if not raw:
        return []
    out = []
    for si in ET.fromstring(raw).findall(qn(SML, "si")):
        out.append("".join(t.text or "" for t in si.iter() if local(t.tag) == "t"))
    return out

def _parse_styles(parts):
    """-> (numfmt_code_by_xf_index)."""
    raw = parts.get("xl/styles.xml")
    codes = {i: BUILTIN_NUMFMTS.get(i, "General") for i in range(200)}
    xfs = []
    if raw:
        root = ET.fromstring(raw)
        for nf in root.iter():
            if local(nf.tag) == "numFmt" and nf.get("numFmtId") and nf.get("formatCode") is not None:
                try:
                    codes[int(nf.get("numFmtId"))] = nf.get("formatCode")
                except ValueError:
                    pass
        # NOTE: only cellXfs (not cellStyleXfs) indexes cell s="N" attributes.
        for cfs in root.iter():
            if local(cfs.tag) != "cellXfs":
                continue
            for cf in cfs.findall(qn(SML, "xf")):
                try:
                    xfs.append(int(cf.get("numFmtId", "0")))
                except ValueError:
                    xfs.append(0)
    return codes, xfs

def _num_value(text):
    if re.match(r"^-?\d+$", text or ""):
        try:
            return int(text)
        except ValueError:
            pass
    try:
        f = float(text)
        if f != f:
            return text
        return f
    except (ValueError, TypeError):
        return text

def import_xlsx(data: bytes):
    parts = opc_read(data)
    wb_xml = ET.fromstring(parts["xl/workbook.xml"])
    rels = ET.fromstring(parts.get("xl/_rels/workbook.xml.rels", b"<r/>"))
    tgt = {}
    for r in rels.iter():
        if local(r.tag) == "Relationship":
            tgt[r.get("Id")] = (r.get("Target") or "").split("/")[-1]
    ss = _parse_shared_strings(parts)
    codes, xfs = _parse_styles(parts)
    title = "Imported XLSX"
    ctx = Ctx()
    wb = ctx.new("workbook", 0, {"calc": {"mode": "auto"}})
    for sh in wb_xml.iter():
        if local(sh.tag) != "sheet":
            continue
        name = sh.get("name", f"Sheet{ctx.stats['sheets'] + 1}")
        fname = tgt.get(sh.get(f"{{{R}}}id", ""), "")
        sheet_xml = parts.get(f"xl/worksheets/{fname}") or parts.get(fname, b"")
        if not sheet_xml:
            ctx.skip("missing_sheet")
            continue
        sroot = ET.fromstring(sheet_xml)
        cells = {}
        for c in sroot.iter():
            if local(c.tag) != "c":
                continue
            addr = (c.get("r") or "").replace("$", "")
            if not re.match(r"^[A-Za-z]{1,3}[0-9]{1,7}$", addr):
                ctx.skip("bad_addr")
                continue
            t = c.get("t", "n")
            v_el = c.find(qn(SML, "v"))
            f_el = c.find(qn(SML, "f"))
            v_text = v_el.text if v_el is not None else None
            cell = {}
            if t == "s":
                try:
                    cell["v"] = ss[int(v_text)]
                    ctx.stats["cell_types"]["text"] = ctx.stats["cell_types"].get("text", 0) + 1
                except (ValueError, IndexError, TypeError):
                    ctx.skip("bad_sst_ref")
                    continue
            elif t == "b":
                cell["v"] = v_text == "1"
                ctx.stats["cell_types"]["bool"] = ctx.stats["cell_types"].get("bool", 0) + 1
            elif t == "e":
                cell["v"] = None
                cell["err"] = v_text or "#ERROR"
                del cell["v"]
                ctx.stats["cell_types"]["err"] = ctx.stats["cell_types"].get("err", 0) + 1
            elif t == "str":
                cell["v"] = v_text or ""
                ctx.stats["cell_types"]["text"] = ctx.stats["cell_types"].get("text", 0) + 1
            elif t == "inlineStr":
                cell["v"] = "".join(x.text or "" for x in c.iter() if local(x.tag) == "t")
                ctx.stats["cell_types"]["text"] = ctx.stats["cell_types"].get("text", 0) + 1
            else:
                if v_text is None:
                    cell["v"] = None
                else:
                    cell["v"] = _num_value(v_text)
                ctx.stats["cell_types"]["number"] = ctx.stats["cell_types"].get("number", 0) + 1
            if f_el is not None and (f_el.text or ""):
                cell["f"] = f_el.text
                deps, cross, partial = extract_refs(f_el.text)
                cell["deps"] = deps
                ctx.stats["formulas"] += 1
                ctx.stats["cross_sheet_refs"] += cross
                if partial:
                    ctx.stats["partial_ranges"] += 1
            s_idx = c.get("s")
            if s_idx is not None and s_idx.isdigit():
                xf = xfs[int(s_idx)] if int(s_idx) < len(xfs) else 0
                ref = ctx.numfmt_for(wb, codes.get(xf, "General"))
                if ref:
                    cell["s"] = ref
            cells[addr] = cell
            ctx.stats["cells"] += 1
        merges = []
        for mc in sroot.iter():
            if local(mc.tag) == "mergeCell":
                ref = mc.get("ref", "")
                if ":" in ref:
                    a, b = ref.split(":")
                    ca, ra = _split_addr(a)
                    cb, rb = _split_addr(b)
                    if ca and cb:
                        merges.append([ra - 1, _col_letters_to_n(ca) - 1,
                                       rb - 1, _col_letters_to_n(cb) - 1])
                        ctx.stats["merges"] += 1
        # Drawing images: resolve sheet rels -> drawingN.xml -> pic blips
        sheet_rels_key = f"xl/worksheets/_rels/{fname}.rels"
        sheet_rels_xml = parts.get(sheet_rels_key)
        if sheet_rels_xml:
            import hashlib
            DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
            XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
            sheet_rid2tgt = {}
            for rel_el in ET.fromstring(sheet_rels_xml).iter():
                if local(rel_el.tag) == "Relationship":
                    sheet_rid2tgt[rel_el.get("Id")] = rel_el.get("Target", "")
            for stgt in sheet_rid2tgt.values():
                # Normalize target path (may be relative like ../drawings/drawing1.xml)
                dnorm = stgt.replace("../", "").lstrip("/")
                drawing_xml = parts.get(f"xl/{dnorm}") or parts.get(dnorm)
                if not drawing_xml:
                    ctx.stats["skipped"]["drawings_unresolved"] = ctx.stats["skipped"].get("drawings_unresolved", 0) + 1
                    continue
                # Parse drawing rels for blip targets
                draw_base = dnorm  # e.g. "drawings/drawing1.xml"
                draw_rels_key_a = f"xl/{draw_base.rsplit('/', 1)[0]}/_rels/{draw_base.rsplit('/', 1)[-1]}.rels"
                draw_rels_xml = parts.get(draw_rels_key_a)
                draw_rid2tgt = {}
                if draw_rels_xml:
                    for rel_el in ET.fromstring(draw_rels_xml).iter():
                        if local(rel_el.tag) == "Relationship":
                            draw_rid2tgt[rel_el.get("Id")] = rel_el.get("Target", "")
                for pic_el in ET.fromstring(drawing_xml).iter():
                    if local(pic_el.tag) != "pic":
                        continue
                    blip = None
                    for child in pic_el.iter():
                        if local(child.tag) == "blip":
                            blip = child
                            break
                    if blip is None:
                        continue
                    embed_rid = blip.get(qn(R, "embed"))
                    media_tgt = draw_rid2tgt.get(embed_rid, "") if embed_rid else ""
                    mnorm = media_tgt.replace("../", "").lstrip("/")
                    blob = parts.get(f"xl/{mnorm}") or parts.get(mnorm)
                    if not blob:
                        ctx.stats["skipped"]["drawings_unresolved"] = ctx.stats["skipped"].get("drawings_unresolved", 0) + 1
                        continue
                    sha = hashlib.sha256(blob).digest()
                    ctx.assets.setdefault(sha, (mime_for(media_tgt), blob))
                    # Find extent for width/height
                    ext_el = None
                    for child in pic_el.iter():
                        if local(child.tag) == "ext":
                            ext_el = child
                            break
                    w = h = 0
                    if ext_el is not None:
                        try:
                            w, h = int(ext_el.get("cx", "0")), int(ext_el.get("cy", "0"))
                        except ValueError:
                            pass
                    # Images are siblings of the sheet (under workbook), not under the sheet itself
                    ctx.new("image", wb, {"asset": {"$asset": sha},
                                          "mime": mime_for(media_tgt),
                                          "w": emu_to_bmu(w), "h": emu_to_bmu(h)})
                    ctx.stats["images_imported"] += 1
        ctx.new("sheet", wb, {"name": name, "cells": cells,
                              **({"merges": merges} if merges else {})})
        ctx.stats["sheets"] += 1
    return title, ctx.envs, ctx.stats, ctx.assets

# --- export -----------------------------------------------------------------

def _a1(col1: int, row1: int) -> str:
    letters = ""
    c = col1
    while c:
        c, r = divmod(c - 1, 26)
        letters = chr(65 + r) + letters
    return f"{letters}{row1}"

def export_xlsx(envs, title, approximations):
    by_id = {e["oid"]: e for e in envs}
    kids = {}
    for e in envs:
        kids.setdefault(e["parent"], []).append(e)
    for v in kids.values():
        v.sort(key=lambda e: e["index"])
    wb = next(e for e in envs if e["kind"] == "workbook")
    sheets = [c for c in kids.get(wb["oid"], []) if c["kind"] == "sheet"]
    fmt_codes, fmt_idx = [], {}
    for s in sheets:
        for cell in s["body"].get("cells", {}).values():
            st = cell.get("s") if isinstance(cell, dict) else None
            if isinstance(st, dict) and "$ref" in st:
                code = (by_id.get(st["$ref"], {}).get("body") or {}).get("code")
                if code and code not in fmt_idx:
                    fmt_idx[code] = len(fmt_codes)
                    fmt_codes.append(code)
    xf_for = {}
    for code, i in fmt_idx.items():
        xf_for[code] = len(xf_for) + 1  # xf 0 = default General
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
              f"<styleSheet xmlns=\"{SML}\">"
              + ("<numFmts count=\"%d\">" % len(fmt_codes) + "".join(
                  f"<numFmt numFmtId=\"{164 + i}\" formatCode=\"{c}\"/>" for i, c in enumerate(fmt_codes))
                 + "</numFmts>" if fmt_codes else "")
              + "<fonts count=\"1\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>"
              + "<fills count=\"1\"><fill><patternFill patternType=\"none\"/></fill></fills>"
              + "<borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>"
              + "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>"
              + "<cellXfs count=\"%d\">" % (1 + len(fmt_codes))
              + "<xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/>" + "".join(
                  f"<xf numFmtId=\"{164 + i}\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\"/>"
                  for i in range(len(fmt_codes)))
              + "</cellXfs></styleSheet>")
    files = {}
    sheet_parts = []
    for n, s in enumerate(sheets, 1):
        body = s["body"]
        rows = {}
        for addr, cell in body.get("cells", {}).items():
            m = re.match(r"^([A-Za-z]{1,3})([0-9]+)$", addr)
            col = _col_letters_to_n(m.group(1))
            row = int(m.group(2))
            st = cell.get("s") if isinstance(cell, dict) else None
            code = None
            if isinstance(st, dict) and "$ref" in st:
                code = (by_id.get(st["$ref"], {}).get("body") or {}).get("code")
            sinline = f" s=\"{xf_for[code]}\"" if code in xf_for else ""
            v, f = cell.get("v"), cell.get("f")
            if f:
                # Cached type decides the cell type attr (Excel semantics);
                # the cached value is ALWAYS written back when present so
                # re-import reproduces it exactly (None = no cache element).
                esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")
                if isinstance(v, bool):
                    fcell = f"<c r=\"{addr}\"{sinline} t=\"b\"><f>{esc(f)}</f><v>{1 if v else 0}</v></c>"
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    fcell = f"<c r=\"{addr}\"{sinline}><f>{esc(f)}</f><v>{v}</v></c>"
                elif isinstance(v, str):
                    fcell = f"<c r=\"{addr}\"{sinline} t=\"str\"><f>{esc(f)}</f><v>{esc(v)}</v></c>"
                elif "err" in cell:
                    fcell = f"<c r=\"{addr}\"{sinline} t=\"e\"><f>{esc(f)}</f><v>{esc(cell['err'])}</v></c>"
                else:
                    fcell = f"<c r=\"{addr}\"{sinline}><f>{esc(f)}</f></c>"
                rows.setdefault(row, []).append(fcell)
            elif isinstance(v, bool):
                rows.setdefault(row, []).append(f"<c r=\"{addr}\"{sinline} t=\"b\"><v>{1 if v else 0}</v></c>")
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                rows.setdefault(row, []).append(f"<c r=\"{addr}\"{sinline}><v>{v}</v></c>")
            elif isinstance(v, str):
                esc = v.replace("&", "&amp;").replace("<", "&lt;")
                rows.setdefault(row, []).append(
                    f"<c r=\"{addr}\"{sinline} t=\"inlineStr\"><is><t>{esc}</t></is></c>")
            elif "err" in cell:
                rows.setdefault(row, []).append(
                    f"<c r=\"{addr}\"{sinline} t=\"e\"><v>{cell['err']}</v></c>")
            else:
                rows.setdefault(row, []).append(f"<c r=\"{addr}\"{sinline}/>")
        sheet_data = "".join(f"<row r=\"{r}\">" + "".join(rows[r]) + "</row>" for r in sorted(rows))
        merges = "".join(f"<mergeCell ref=\"{_a1(c1 + 1, r1 + 1)}:{_a1(c2 + 1, r2 + 1)}\"/>"
                         for r1, c1, r2, c2 in body.get("merges", []))
        sheet_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                     f"<worksheet xmlns=\"{SML}\"><sheetData>{sheet_data}</sheetData>"
                     + (f"<mergeCells>{merges}</mergeCells>" if merges else "") + "</worksheet>")
        pname = f"/xl/worksheets/sheet{n}.xml"
        sheet_parts.append((n, s["body"]["name"], pname))
        files[pname[1:]] = sheet_xml.encode()
    wb_rels = "".join(rel_item(f"rId{i}", f"{R}/worksheet", f"worksheets/sheet{n}.xml")
                      for i, (n, _, _) in enumerate(sheet_parts, 2))
    wb_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
              f"<workbook xmlns=\"{SML}\" xmlns:r=\"{R}\"><sheets>"
              + "".join(f"<sheet name=\"{nm}\" sheetId=\"{n}\" r:id=\"rId{i + 2}\"/>"
                        for i, (n, nm, _) in enumerate(sheet_parts))
              + "</sheets></workbook>")
    ct = (CT_XML % (
        ct_item("/_rels/.rels", "application/vnd.openxmlformats-package.relationships+xml")
        + ct_item("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
        + "".join(ct_item(p, "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml") for _, _, p in sheet_parts)
        + ct_item("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"))).encode()
    files.update({
        "[Content_Types].xml": ct,
        "_rels/.rels": (RELS_XML % rel_item("rId1", f"{R}/officeDocument", "xl/workbook.xml")).encode(),
        "xl/workbook.xml": wb_xml.encode(),
        "xl/_rels/workbook.xml.rels": (RELS_XML % (
            rel_item("rId1", f"{R}/styles", "styles.xml") + wb_rels)).encode(),
        "xl/styles.xml": styles.encode(),
    })
    return opc_write(files)

def sample_xlsx_complex() -> bytes:
    """Dates, percents, bools, errors, cross-sheet + range formulas, merges."""
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<styleSheet xmlns=\"{SML}\">"
              "<numFmts count=\"1\"><numFmt numFmtId=\"165\" formatCode=\"0.0%\"/></numFmts>"
              "<cellXfs count=\"3\"><xf numFmtId=\"0\"/><xf numFmtId=\"14\"/><xf numFmtId=\"165\" applyNumberFormat=\"1\"/></cellXfs></styleSheet>")
    s1 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<worksheet xmlns=\"{SML}\"><sheetData>"
          "<row r=\"1\"><c r=\"A1\" s=\"1\"><v>44562</v></c><c r=\"B1\" s=\"2\"><v>0.07</v></c>"
          "<c r=\"C1\" t=\"b\"><v>0</v></c><c r=\"D1\" t=\"e\"><v>#N/A</v></c></row>"
          "<row r=\"2\"><c r=\"A2\"><v>10</v></c><c r=\"A3\"><v>20</v></c>"
          "<c r=\"B2\"><f>SUM(A2:A3)*2+Second!B1</f></c></row></sheetData>"
          "<mergeCells><mergeCell ref=\"A1:B1\"/></mergeCells></worksheet>")
    s2 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<worksheet xmlns=\"{SML}\"><sheetData>"
          "<row r=\"1\"><c r=\"B1\"><v>5</v></c></row></sheetData></worksheet>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
            + ct_item("/xl/worksheets/sheet1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            + ct_item("/xl/worksheets/sheet2.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            + ct_item("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"))).encode(),
        "xl/workbook.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            f"<workbook xmlns=\"{SML}\" xmlns:r=\"{R}\"><sheets>"
            "<sheet name=\"First\" sheetId=\"1\" r:id=\"rId2\"/>"
            "<sheet name=\"Second\" sheetId=\"2\" r:id=\"rId3\"/></sheets></workbook>").encode(),
        "xl/_rels/workbook.xml.rels": (RELS_XML % (
            rel_item("rId1", f"{R}/styles", "styles.xml")
            + rel_item("rId2", f"{R}/worksheet", "worksheets/sheet1.xml")
            + rel_item("rId3", f"{R}/worksheet", "worksheets/sheet2.xml"))).encode(),
        "xl/styles.xml": styles.encode(),
        "xl/worksheets/sheet1.xml": s1.encode(),
        "xl/worksheets/sheet2.xml": s2.encode(),
    })

def sample_xlsx() -> bytes:
    ss = ("<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"2\" uniqueCount=\"2\">"
          "<si><t>Revenue</t></si><si><t>Q1</t></si></sst>")
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<styleSheet xmlns=\"{SML}\">"
              "<numFmts count=\"1\"><numFmt numFmtId=\"165\" formatCode=\"0.00%\"/></numFmts>"
              "<cellXfs count=\"2\"><xf numFmtId=\"0\"/><xf numFmtId=\"165\" applyNumberFormat=\"1\"/></cellXfs></styleSheet>")
    s1 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<worksheet xmlns=\"{SML}\"><sheetData>"
          "<row r=\"1\"><c r=\"A1\"><v>42</v></c><c r=\"B1\" t=\"s\"><v>0</v></c>"
          "<c r=\"C1\" s=\"1\"><v>0.15</v></c><c r=\"D1\"><f>A1*2</f><v>84</v></c></row>"
          "<row r=\"2\"><c r=\"A2\" t=\"b\"><v>1</v></c><c r=\"B2\" t=\"e\"><v>#DIV/0!</v></c>"
          "<c r=\"C2\"><f>SUM(A2:A10)+Q1!A1</f></c></row></sheetData>"
          "<mergeCells><mergeCell ref=\"A1:B1\"/></mergeCells></worksheet>")
    s2 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<worksheet xmlns=\"{SML}\"><sheetData>"
          "<row r=\"1\"><c r=\"A1\"><v>7</v></c></row></sheetData></worksheet>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
            + ct_item("/xl/worksheets/sheet1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            + ct_item("/xl/worksheets/sheet2.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            + ct_item("/xl/sharedStrings.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml")
            + ct_item("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"))).encode(),
        "xl/workbook.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            f"<workbook xmlns=\"{SML}\" xmlns:r=\"{R}\"><sheets>"
            "<sheet name=\"Summary\" sheetId=\"1\" r:id=\"rId2\"/>"
            "<sheet name=\"Q1\" sheetId=\"2\" r:id=\"rId3\"/></sheets></workbook>").encode(),
        "xl/_rels/workbook.xml.rels": (RELS_XML % (
            rel_item("rId1", f"{R}/styles", "styles.xml")
            + rel_item("rId2", f"{R}/worksheet", "worksheets/sheet1.xml")
            + rel_item("rId3", f"{R}/worksheet", "worksheets/sheet2.xml")
            + rel_item("rId4", f"{R}/sharedStrings", "sharedStrings.xml"))).encode(),
        "xl/sharedStrings.xml": ss.encode(),
        "xl/styles.xml": styles.encode(),
        "xl/worksheets/sheet1.xml": s1.encode(),
        "xl/worksheets/sheet2.xml": s2.encode(),
    })
