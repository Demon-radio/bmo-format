"""XLSX <-> BMO (RFC-0005). Zero dependencies.
Scope v0.1: values (n/s/b/e/str/inlineStr), formulas + derived deps
(incl. range expansion), merges, number formats. Other drawings,
comments, and pivots are counted as skipped; anchored pictures are preserved.
Cross-sheet refs stay in formula text;
deps cover same-sheet refs only (reported, never faked).
"""
import hashlib
import posixpath
import re
from xml.etree import ElementTree as ET

from .common import (CT_XML, PIC, R, SML, bmu_to_emu, ct_item, emu_to_bmu,
                     local, mime_for, opc_read, opc_write, qn, rel_item,
                     RELS_XML, xml_bytes)

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
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"

def _resolve_part(base_part, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))

def _drawing_anchor(anchor_el):
    kind = local(anchor_el.tag)

    def point(name):
        el = anchor_el.find(qn(XDR, name))
        if el is None:
            raise ValueError(f"missing drawing anchor {name}")
        out = {}
        for attr in ("col", "row", "colOff", "rowOff"):
            value = el.find(qn(XDR, attr))
            if value is None:
                continue
            text = value.text or ""
            if not re.fullmatch(r"-?\d+", text):
                raise ValueError(f"invalid drawing anchor {attr}")
            out[attr] = int(text)
        if not all(k in out for k in ("col", "row")):
            raise ValueError(f"incomplete drawing anchor {name}")
        if out["col"] < 0 or out["row"] < 0:
            raise ValueError(f"invalid drawing anchor {name}")
        return out

    def extent():
        el = anchor_el.find(qn(XDR, "ext"))
        if el is None:
            raise ValueError("missing drawing anchor extent")
        values = {}
        for source, target in (("cx", "w"), ("cy", "h")):
            text = el.get(source, "")
            if not text.isdigit() or int(text) <= 0:
                raise ValueError(f"invalid drawing anchor extent {source}")
            values[target] = int(text)
        return values

    if kind == "twoCellAnchor":
        out = {"kind": "twoCell", "from": point("from"), "to": point("to")}
        edit_as = anchor_el.get("editAs")
        if edit_as:
            out["editAs"] = edit_as
        return out
    if kind == "oneCellAnchor":
        return {"kind": "oneCell", "from": point("from"),
                "extent": extent()}
    if kind == "absoluteAnchor":
        pos = anchor_el.find(qn(XDR, "pos"))
        if pos is None:
            raise ValueError("missing absolute drawing position")
        values = {}
        for attr in ("x", "y"):
            text = pos.get(attr, "")
            if not re.fullmatch(r"-?\d+", text):
                raise ValueError(f"invalid drawing position {attr}")
            values[attr] = int(text)
        return {"kind": "absolute", "pos": values, "extent": extent()}
    raise ValueError(f"unsupported drawing anchor {kind}")

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
        # Drawing images: resolve worksheet rels -> drawing -> media parts.
        sheet_rels_key = f"xl/worksheets/_rels/{fname}.rels"
        sheet_rels_xml = parts.get(sheet_rels_key)
        if sheet_rels_xml:
            drawing_targets = []
            for rel_el in ET.fromstring(sheet_rels_xml).iter():
                if local(rel_el.tag) == "Relationship":
                    if (rel_el.get("Type", "").endswith("/drawing")
                            and rel_el.get("Target")):
                        drawing_targets.append(rel_el.get("Target"))
            sheet_part = f"xl/worksheets/{fname}"
            for target in drawing_targets:
                drawing_part = _resolve_part(sheet_part, target)
                drawing_xml = parts.get(drawing_part)
                if not drawing_xml:
                    ctx.stats["skipped"]["drawings_unresolved"] = ctx.stats["skipped"].get("drawings_unresolved", 0) + 1
                    continue
                draw_dir, draw_name = posixpath.split(drawing_part)
                draw_rels_key_a = f"{draw_dir}/_rels/{draw_name}.rels"
                draw_rels_xml = parts.get(draw_rels_key_a)
                draw_rid2tgt = {}
                if draw_rels_xml:
                    for rel_el in ET.fromstring(draw_rels_xml).iter():
                        if local(rel_el.tag) == "Relationship":
                            draw_rid2tgt[rel_el.get("Id")] = rel_el.get("Target", "")
                for anchor_el in ET.fromstring(drawing_xml):
                    if local(anchor_el.tag) not in (
                            "twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
                        continue
                    try:
                        anchor = _drawing_anchor(anchor_el)
                    except ValueError:
                        ctx.stats["skipped"]["drawings_invalid_anchor"] = ctx.stats["skipped"].get("drawings_invalid_anchor", 0) + 1
                        continue
                    for pic_el in anchor_el.iter():
                        if local(pic_el.tag) != "pic":
                            continue
                        blip = next((child for child in pic_el.iter()
                                     if local(child.tag) == "blip"), None)
                        embed_rid = blip.get(qn(R, "embed")) if blip is not None else None
                        media_target = draw_rid2tgt.get(embed_rid, "") if embed_rid else ""
                        media_part = _resolve_part(drawing_part, media_target) if media_target else ""
                        blob = parts.get(media_part)
                        if not blob:
                            ctx.stats["skipped"]["drawings_unresolved"] = ctx.stats["skipped"].get("drawings_unresolved", 0) + 1
                            continue
                        ext_el = pic_el.find(f".//{qn(DML, 'ext')}")
                        try:
                            w = int(ext_el.get("cx", "")) if ext_el is not None else 0
                            h = int(ext_el.get("cy", "")) if ext_el is not None else 0
                            if w <= 0 or h <= 0:
                                raise ValueError("invalid picture dimensions")
                        except ValueError:
                            ctx.stats["skipped"]["drawings_invalid_extent"] = ctx.stats["skipped"].get("drawings_invalid_extent", 0) + 1
                            continue
                        sha = hashlib.sha256(blob).digest()
                        mime = mime_for(media_part)
                        ctx.assets.setdefault(sha, (mime, blob))
                        ctx.new("image", wb, {
                            "asset": {"$asset": sha}, "mime": mime,
                            "w": emu_to_bmu(w), "h": emu_to_bmu(h),
                            "sheet": name, "anchor": anchor,
                        })
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

def _xml_attr(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def _anchor_xml(anchor):
    def point_xml(tag, point):
        if not isinstance(point, dict):
            raise ValueError(f"missing {tag} point")
        values = {}
        for key in ("col", "row"):
            value = point.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"invalid anchor {tag}.{key}")
            values[key] = value
        for key in ("colOff", "rowOff"):
            value = point.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"invalid anchor {tag}.{key}")
            values[key] = value
        return (f"<xdr:{tag}><xdr:col>{values['col']}</xdr:col>"
                f"<xdr:colOff>{values['colOff']}</xdr:colOff>"
                f"<xdr:row>{values['row']}</xdr:row>"
                f"<xdr:rowOff>{values['rowOff']}</xdr:rowOff></xdr:{tag}>")

    if not isinstance(anchor, dict):
        raise ValueError("missing worksheet image anchor")
    kind = anchor.get("kind")
    if kind == "twoCell":
        edit_as = anchor.get("editAs")
        edit_attr = f' editAs="{_xml_attr(edit_as)}"' if isinstance(edit_as, str) else ""
        return (f"<xdr:twoCellAnchor{edit_attr}>"
                f"{point_xml('from', anchor.get('from'))}"
                f"{point_xml('to', anchor.get('to'))}")
    if kind == "oneCell":
        start = point_xml("from", anchor.get("from"))
        extent = anchor.get("extent")
        if not isinstance(extent, dict):
            raise ValueError("missing one-cell extent")
        width, height = extent.get("w"), extent.get("h")
        if (not isinstance(width, int) or isinstance(width, bool) or width <= 0
                or not isinstance(height, int) or isinstance(height, bool) or height <= 0):
            raise ValueError("invalid one-cell extent")
        return (f"<xdr:oneCellAnchor>{start}"
                f'<xdr:ext cx="{width}" cy="{height}"/>')
    if kind == "absolute":
        pos = anchor.get("pos")
        extent = anchor.get("extent")
        if not isinstance(pos, dict) or not isinstance(extent, dict):
            raise ValueError("missing absolute position or extent")
        x, y = pos.get("x"), pos.get("y")
        width, height = extent.get("w"), extent.get("h")
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (x, y)):
            raise ValueError("invalid absolute position")
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0
               for v in (width, height)):
            raise ValueError("invalid absolute extent")
        return (f'<xdr:absoluteAnchor><xdr:pos x="{x}" y="{y}"/>'
                f'<xdr:ext cx="{width}" cy="{height}"/>')
    raise ValueError(f"unsupported worksheet image anchor {kind!r}")

def _picture_xml(image_id, rel_id, width, height):
    return (
        f'<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{image_id}" name="Image {image_id}"/>'
        "<xdr:cNvPicPr/></xdr:nvPicPr>"
        f'<xdr:blipFill><a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>"
        f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic>')

def export_xlsx(envs, title, approximations, assets=None):
    assets = assets or {}
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
    extra_content_types = []
    image_exts = {"image/png": "png", "image/jpeg": "jpg",
                  "image/gif": "gif", "image/bmp": "bmp",
                  "image/tiff": "tif", "image/webp": "webp"}
    image_id = 0
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
        sheet_name = body["name"]
        drawing_file = f"xl/drawings/drawing{n}.xml"
        drawing_images = []
        drawing_rels = []
        for image in kids.get(wb["oid"], []):
            if image["kind"] != "image":
                continue
            ib = image.get("body", {})
            if not isinstance(ib, dict) or ib.get("sheet") != sheet_name:
                continue
            asset = ib.get("asset")
            sha = asset.get("$asset") if isinstance(asset, dict) else None
            entry = assets.get(sha) if isinstance(sha, bytes) else None
            blob = entry[1] if isinstance(entry, tuple) and len(entry) > 1 else None
            if not isinstance(blob, bytes):
                approximations.append(
                    f"image {image['oid']}: bytes unavailable, dropped")
                continue
            mime = ib.get("mime", entry[0] if isinstance(entry, tuple) else "")
            ext = image_exts.get(mime)
            if ext is None:
                approximations.append(
                    f"image {image['oid']}: unsupported MIME {mime!r}, dropped")
                continue
            width, height = ib.get("w"), ib.get("h")
            if (not isinstance(width, int) or isinstance(width, bool) or width <= 0
                    or not isinstance(height, int) or isinstance(height, bool) or height <= 0):
                approximations.append(
                    f"image {image['oid']}: invalid dimensions, dropped")
                continue
            try:
                anchor_xml = _anchor_xml(ib.get("anchor"))
            except ValueError as exc:
                approximations.append(
                    f"image {image['oid']}: {exc}, dropped")
                continue
            image_id += 1
            rel_id = f"rIdImage{len(drawing_rels) + 1}"
            media_part = f"xl/media/image{image_id}.{ext}"
            files[media_part] = blob
            drawing_rels.append(
                rel_item(rel_id, f"{R}/image", f"../media/image{image_id}.{ext}"))
            extra_content_types.append(ct_item(f"/{media_part}", mime))
            picture = _picture_xml(image_id, rel_id, bmu_to_emu(width),
                                   bmu_to_emu(height))
            anchor_tag = {
                "twoCell": "twoCellAnchor",
                "oneCell": "oneCellAnchor",
                "absolute": "absoluteAnchor",
            }[ib["anchor"]["kind"]]
            drawing_images.append(
                f"{anchor_xml}{picture}<xdr:clientData/></xdr:{anchor_tag}>")
        drawing_markup = ""
        if drawing_images:
            drawing_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<xdr:wsDr xmlns:xdr="{XDR}" xmlns:a="{DML}" xmlns:r="{R}">'
                f'{"".join(drawing_images)}</xdr:wsDr>')
            files[drawing_file] = drawing_xml.encode()
            files[f"xl/drawings/_rels/drawing{n}.xml.rels"] = (
                RELS_XML % "".join(drawing_rels)).encode()
            extra_content_types.append(ct_item(
                f"/{drawing_file}",
                "application/vnd.openxmlformats-officedocument.drawing+xml"))
            files[f"xl/worksheets/_rels/sheet{n}.xml.rels"] = (
                RELS_XML % rel_item("rIdDrawing", f"{R}/drawing",
                                    f"../drawings/drawing{n}.xml")).encode()
            drawing_markup = '<drawing r:id="rIdDrawing"/>'
        sheet_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                     f'<worksheet xmlns="{SML}" xmlns:r="{R}"><sheetData>{sheet_data}</sheetData>'
                     + (f"<mergeCells>{merges}</mergeCells>" if merges else "")
                     + drawing_markup + "</worksheet>")
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
        + ct_item("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")
        + "".join(extra_content_types))).encode()
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

def sample_xlsx_images() -> bytes:
    """Pictures covering each supported worksheet anchor representation."""
    from .common import png1x1

    def picture(rid, pic_id, cx, cy):
        return (
            f'<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{pic_id}" name="Fixture {pic_id}"/>'
            '<xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill>'
            f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
            '</xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
            f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</xdr:spPr></xdr:pic>')

    drawing = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<xdr:wsDr xmlns:xdr="{XDR}" xmlns:a="{DML}" xmlns:r="{R}">'
        '<xdr:twoCellAnchor editAs="oneCell">'
        '<xdr:from><xdr:col>1</xdr:col><xdr:colOff>-12700</xdr:colOff>'
        '<xdr:row>2</xdr:row><xdr:rowOff>25400</xdr:rowOff></xdr:from>'
        '<xdr:to><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff>'
        '<xdr:row>5</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
        f'{picture("rId1", 1, 9525, 9525)}<xdr:clientData/></xdr:twoCellAnchor>'
        '<xdr:oneCellAnchor><xdr:from><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff>'
        '<xdr:row>6</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
        '<xdr:ext cx="19050" cy="28575"/>'
        f'{picture("rId2", 2, 19050, 28575)}<xdr:clientData/></xdr:oneCellAnchor>'
        '<xdr:absoluteAnchor><xdr:pos x="-12700" y="25400"/>'
        '<xdr:ext cx="38100" cy="19050"/>'
        f'{picture("rId3", 3, 38100, 19050)}<xdr:clientData/></xdr:absoluteAnchor>'
        '</xdr:wsDr>')
    pngs = [png1x1((200, 30, 30)), png1x1((30, 200, 30)),
            png1x1((30, 30, 200))]
    return opc_write({
        "[Content_Types].xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
            '</Types>').encode(),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{SML}" xmlns:r="{R}"><sheets>'
            '<sheet name="Images" sheetId="1" r:id="rId1"/>'
            '</sheets></workbook>').encode(),
        "xl/_rels/workbook.xml.rels": (RELS_XML % rel_item(
            "rId1", f"{R}/worksheet", "worksheets/sheet1.xml")).encode(),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{SML}" xmlns:r="{R}"><sheetData/>'
            '<drawing r:id="rIdDrawing"/></worksheet>').encode(),
        "xl/worksheets/_rels/sheet1.xml.rels": (RELS_XML % rel_item(
            "rIdDrawing", f"{R}/drawing", "../drawings/drawing1.xml")).encode(),
        "xl/drawings/drawing1.xml": drawing.encode(),
        "xl/drawings/_rels/drawing1.xml.rels": (RELS_XML % "".join(
            rel_item(f"rId{i}", f"{R}/image", f"../media/image{i}.png")
            for i in range(1, 4))).encode(),
        **{f"xl/media/image{i}.png": png for i, png in enumerate(pngs, 1)},
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
