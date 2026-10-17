"""DOCX <-> BMO (RFC-0003 tables included). Zero dependencies.
Scope v0.1: paragraphs, runs (b/i/size/color), alignment, lists,
tables, named styles. Images/drawings/OLE/fields are counted as skipped.
"""
import re
from xml.etree import ElementTree as ET

from .common import A, CT_XML, P, PIC, R, RELS_XML, W, WP, bmu_to_emu, ct_item, emu_to_bmu, local, mime_for, opc_read, opc_write, qn, rel_item, sniff_script, xml_bytes

ALIGN_IN = {"left": "left", "center": "center", "right": "right",
            "both": "justify", "distribute": "justify",
            "start": "start", "end": "end"}
ALIGN_OUT = {"left": "left", "center": "center", "right": "right",
             "justify": "both", "start": "start", "end": "end"}
NUMFMT_IN = {"bullet": "bullet", "decimal": "decimal"}
NUMFMT_OUT = {"bullet": "bullet", "decimal": "decimal"}

class Ctx:
    def __init__(self):
        self.next_id = 1
        self.counters = {}
        self.envs = []
        self.styles = {}       # key -> oid
        self.style_n = 0
        self.listdefs = {}     # numId -> oid
        self.assets = {}       # sha -> (mime, bytes)
        self.pending_images = []  # (sha, mime, w_bmu, h_bmu) flushed after each paragraph
        self.stats = {"paragraphs": 0, "runs": 0, "tables": 0, "rows": 0,
                      "cells": 0, "styles": 0, "list_defs": 0,
                      "images_imported": 0,
                      "skipped": {"images": 0, "nested_tables": 0, "fields": 0,
                                  "hyperlinks": 0, "drawings": 0}}

    def new(self, kind, parent, body):
        i = self.counters.get(parent, 0)
        self.counters[parent] = i + 1
        oid = self.next_id
        self.next_id += 1
        self.envs.append({"oid": oid, "kind": kind, "parent": parent,
                          "index": i, "body": body})
        return oid

    def style_for(self, name=None, run=None, paragraph=None):
        key = (name, tuple(sorted((run or {}).items())),
               tuple(sorted((paragraph or {}).items())))
        if key in self.styles:
            return self.styles[key]
        body = {}
        if name:
            body["name"] = name
        if run:
            body["run"] = run
        if paragraph:
            body["paragraph"] = paragraph
        oid = self.new("style", 1, body)
        self.styles[key] = oid
        self.stats["styles"] += 1
        return oid

def _bool_attr(el):
    if el is None:
        return False
    v = el.get(qn(W, "val"), "true")
    return v not in ("false", "0", "off")

def _drawing_image(drawing_el, parts, rels):
    """(sha, mime, blob, w_bmu, h_bmu) or None for a w:drawing with blip."""
    import hashlib
    blip = drawing_el.find(f".//{{{A}}}blip")
    if blip is None:
        return None
    rid = blip.get(qn(R, "embed"))
    tgt = (rels.get(rid) or "") if rid else ""
    norm = tgt.replace("../", "").lstrip("/")
    blob = parts.get("word/" + norm) if norm else None
    if not blob:
        return None
    ext = next((e for e in drawing_el.iter()
                if local(e.tag) == "extent"), None)
    w = h = 0
    if ext is not None:
        try:
            w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        except ValueError:
            pass
    sha = hashlib.sha256(blob).digest()
    return sha, mime_for(tgt), blob, emu_to_bmu(w), emu_to_bmu(h)

def _run_text(r):
    parts = []
    for child in r:
        t = local(child.tag)
        if t == "t":
            parts.append(child.text or "")
        elif t == "tab":
            parts.append("\t")
        elif t == "br":
            parts.append("\n")
        elif t in ("drawing", "pict", "object"):
            parts.append("\uFFFC")
    return "".join(parts), any(local(c.tag) in ("drawing", "pict", "object") for c in r)

def _run_style_oid(ctx, r):
    rpr = r.find(qn(W, "rPr"))
    if rpr is None:
        return None
    run = {}
    if _bool_attr(rpr.find(qn(W, "b"))):
        run["b"] = 1
    if _bool_attr(rpr.find(qn(W, "i"))):
        run["i"] = 1
    sz = rpr.find(qn(W, "sz"))
    if sz is not None and sz.get(qn(W, "val"), "").isdigit():
        run["size"] = int(sz.get(qn(W, "val"))) // 2
    color = rpr.find(qn(W, "color"))
    if color is not None and color.get(qn(W, "val")):
        run["color"] = color.get(qn(W, "val"))
    if not run:
        return None
    ctx.style_n += 1
    return ctx.style_for(name=f"docx-s{ctx.style_n}", run=run)

def _import_runs(ctx, p_el, para_oid, parent, parts, rels):
    for r in p_el.findall(qn(W, "r")):
        text, has_obj = _run_text(r)
        if has_obj:
            for d in r.iter():
                if local(d.tag) == "drawing":
                    img = _drawing_image(d, parts, rels)
                    if img:
                        sha, mime, blob, w, h = img
                        ctx.assets.setdefault(sha, (mime, blob))
                        ctx.pending_images.append((sha, mime, w, h))
                        ctx.stats["images_imported"] += 1
                    else:
                        ctx.stats["skipped"]["images"] += 1
                elif local(d.tag) in ("pict", "object"):
                    ctx.stats["skipped"]["images"] += 1
            text = text.replace("\uFFFC", "")
        if not text:
            continue
        body = {"text": text, "script": sniff_script(text)}
        lang = r.find(f"{qn(W, 'rPr')}/{qn(W, 'lang')}")
        if lang is not None and lang.get(qn(W, "val")):
            body["lang"] = lang.get(qn(W, "val"))
        oid = _run_style_oid(ctx, r)
        if oid:
            body["style"] = {"$ref": oid}
        ctx.new("run", para_oid, body)
        ctx.stats["runs"] += 1
    for hyp in p_el.findall(qn(W, "hyperlink")):
        ctx.stats["skipped"]["hyperlinks"] += 1
        for r in hyp.findall(qn(W, "r")):
            text, _ = _run_text(r)
            if text:
                ctx.new("run", para_oid, {"text": text, "script": sniff_script(text)})
                ctx.stats["runs"] += 1
    for fld in list(p_el.findall(qn(W, "fldSimple"))) + list(p_el.findall(qn(W, "fldChar"))):
        ctx.stats["skipped"]["fields"] += 1

def _para_has_text(p_el):
    for r in list(p_el.findall(qn(W, "r"))) + [x for h in p_el.findall(qn(W, "hyperlink")) for x in h.findall(qn(W, "r"))]:
        text, _ = _run_text(r)
        if text:
            return True
    return False

def _import_paragraph(ctx, p_el, parent, numbering, parts, rels):
    ppr = p_el.find(qn(W, "pPr"))
    body = {}
    if ppr is not None:
        jc = ppr.find(qn(W, "jc"))
        if jc is not None and jc.get(qn(W, "val")) in ALIGN_IN:
            body["align"] = ALIGN_IN[jc.get(qn(W, "val"))]
        if ppr.find(qn(W, "bidi")) is not None:
            body["dir"] = "rtl"
        ps = ppr.find(qn(W, "pStyle"))
        if ps is not None and ps.get(qn(W, "val")):
            body["style"] = {"$ref": ctx.style_for(name=ps.get(qn(W, "val")))}
        num = ppr.find(qn(W, "numPr"))
        if num is not None:
            numId_el = num.find(qn(W, "numId"))
            nid = numId_el.get(qn(W, "val"), "0") if numId_el is not None else "0"
            ilvl = num.find(qn(W, "ilvl"))
            lvl = int(ilvl.get(qn(W, "val"), "0")) if ilvl is not None else 0
            if nid not in ctx.listdefs:
                fmt = (numbering.get(nid) or "bullet")
                ctx.new("list-def", 1, {"id": f"num{nid}", "style": fmt})
                ctx.listdefs[nid] = True
                ctx.stats["list_defs"] += 1
            body["list"] = {"id": f"num{nid}", "level": lvl}
    oid = None
    if _para_has_text(p_el) or body != {}:
        oid = ctx.new("paragraph", parent, body)
        ctx.stats["paragraphs"] += 1
    _import_runs(ctx, p_el, oid, parent, parts, rels)
    # inline images become block siblings right after their paragraph
    # (documented approximation: OOXML anchors inline, BMO places blocks)
    for sha, mime, w, h in ctx.pending_images:
        ctx.new("image", parent, {"asset": {"$asset": sha}, "mime": mime,
                                  "w": w, "h": h})
    ctx.pending_images.clear()
    return oid

def _import_table(ctx, tbl_el, parent, numbering, parts, rels):
    grid = tbl_el.find(qn(W, "tblGrid"))
    cols = len(grid.findall(qn(W, "gridCol"))) if grid is not None else 0
    t_oid = ctx.new("table", parent, {"cols": cols} if cols else {})
    ctx.stats["tables"] += 1
    for tr in tbl_el.findall(qn(W, "tr")):
        r_oid = ctx.new("row", t_oid, {})
        ctx.stats["rows"] += 1
        for tc in tr.findall(qn(W, "tc")):
            gs = tc.find(f"{qn(W, 'tcPr')}/{qn(W, 'gridSpan')}")
            span = int(gs.get(qn(W, "val"), "1")) if gs is not None else 1
            c_oid = ctx.new("cell", r_oid, {"gridSpan": span} if span != 1 else {})
            ctx.stats["cells"] += 1
            for child in tc:
                if local(child.tag) == "p":
                    _import_paragraph(ctx, child, c_oid, numbering, parts, rels)
                elif local(child.tag) == "tbl":
                    _import_table(ctx, child, c_oid, numbering, parts, rels)
    return t_oid

def _run_props_from_rpr(rpr):
    if rpr is None:
        return {}
    run = {}
    if _bool_attr(rpr.find(qn(W, "b"))):
        run["b"] = 1
    if _bool_attr(rpr.find(qn(W, "i"))):
        run["i"] = 1
    sz = rpr.find(qn(W, "sz"))
    if sz is not None and sz.get(qn(W, "val"), "").isdigit():
        run["size"] = int(sz.get(qn(W, "val"))) // 2
    color = rpr.find(qn(W, "color"))
    if color is not None and color.get(qn(W, "val")):
        run["color"] = color.get(qn(W, "val"))
    return run

def _spacing_from_ppr(ppr):
    if ppr is None:
        return {}
    sp = ppr.find(qn(W, "spacing"))
    if sp is None:
        return {}
    out = {}
    if sp.get(qn(W, "before"), "").isdigit():
        out["spaceBefore"] = int(sp.get(qn(W, "before")))
    if sp.get(qn(W, "after"), "").isdigit():
        out["spaceAfter"] = int(sp.get(qn(W, "after")))
    return out

def _parse_styles_xml(parts):
    """styleId -> {run?, paragraph?} from word/styles.xml."""
    xml = parts.get("word/styles.xml")
    if not xml:
        return {}
    out = {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    for st in root.findall(qn(W, "style")):
        sid = st.get(qn(W, "styleId"))
        if not sid:
            continue
        entry = {}
        para = _spacing_from_ppr(st.find(qn(W, "pPr")))
        if para:
            entry["paragraph"] = para
        run = _run_props_from_rpr(st.find(qn(W, "rPr")))
        if run:
            entry["run"] = run
        if entry:
            out[sid] = entry
    return out

def _parse_numbering(numbering_xml):
    out = {}
    if not numbering_xml:
        return out
    root = ET.fromstring(numbering_xml)
    abs_map = {}
    for a in root.findall(qn(W, "abstractNum")):
        aid = a.get(qn(W, "abstractNumId"))
        for lvl in a.findall(qn(W, "lvl")):
            if lvl.get(qn(W, "ilvl")) == "0":
                fmt = lvl.find(qn(W, "numFmt"))
                abs_map[aid] = fmt.get(qn(W, "val")) if fmt is not None else "bullet"
    for n in root.findall(qn(W, "num")):
        nid = n.get(qn(W, "numId"))
        aid = n.find(qn(W, "abstractNumId"))
        fmt = abs_map.get(aid.get(qn(W, "val")) if aid is not None else "", "bullet")
        out[nid] = NUMFMT_IN.get(fmt, fmt)
    return out

def import_docx(data: bytes):
    """-> (title, envs, stats, assets). env[0] is the document root (oid 1).
    assets: {sha256: (mime, bytes)} for ASSET_TABLE/DATA assembly."""
    parts = opc_read(data)
    doc_xml = ET.fromstring(parts["word/document.xml"])
    rels = {}
    try:
        for r in ET.fromstring(parts.get("word/_rels/document.xml.rels", b"<r/>")).iter():
            if local(r.tag) == "Relationship":
                rels[r.get("Id")] = r.get("Target", "")
    except ET.ParseError:
        pass
    body_el = doc_xml.find(qn(W, "body"))
    title = "Imported DOCX"
    try:
        core = ET.fromstring(parts.get("docProps/core.xml", b"<x/>"))
        for el in core.iter():
            if local(el.tag) == "title" and el.text:
                title = el.text
    except ET.ParseError:
        pass
    numbering = _parse_numbering(parts.get("word/numbering.xml"))
    ctx = Ctx()
    ctx.new("document", 0, {"title": title})
    for child in body_el:
        t = local(child.tag)
        if t == "p":
            _import_paragraph(ctx, child, 1, numbering, parts, rels)
        elif t == "tbl":
            _import_table(ctx, child, 1, numbering, parts, rels)
        elif t == "sectPr":
            pass
        else:
            ctx.stats["skipped"].setdefault(t, 0)
            ctx.stats["skipped"][t] += 1
    return title, ctx.envs, ctx.stats, ctx.assets

# --- export ---------------------------------------------------------------

def _style_run_map(envs, oid):
    by_id = {e["oid"]: e for e in envs}
    t = by_id.get(oid, {})
    body = t.get("body", {}) if isinstance(t, dict) else {}
    return body.get("run", {}) if isinstance(body, dict) else {}, \
        body.get("name") if isinstance(body, dict) else None

def export_docx(envs, title, approximations, assets=None):
    assets = assets or {}
    by_id = {e["oid"]: e for e in envs}
    kids = {}
    for e in envs:
        kids.setdefault(e["parent"], []).append(e)
    for v in kids.values():
        v.sort(key=lambda e: e["index"])

    def rpr_xml(runmap, lang=None):
        if not runmap and not lang:
            return ""
        s = "<w:rPr>"
        if runmap.get("b"):
            s += "<w:b/>"
        if runmap.get("i"):
            s += "<w:i/>"
        if isinstance(runmap.get("size"), int):
            s += f"<w:sz w:val=\"{runmap['size'] * 2}\"/>"
        if isinstance(runmap.get("color"), str):
            s += f"<w:color w:val=\"{runmap['color']}\"/>"
        if isinstance(lang, str) and lang:
            s += f"<w:lang w:val=\"{lang}\"/>"
        return s + "</w:rPr>"

    num_ids, num_xml, abs_xml = {}, [], []
    def num_id_for(list_id, style):
        if list_id in num_ids:
            return num_ids[list_id]
        # Preserve the original numId when the id carries it ("num7" -> 7)
        # so export->import reproduces identical list-def identities.
        m = re.search(r"(\d+)$", list_id)
        nid = int(m.group(1)) if m else len(num_ids) + 1
        while nid in num_ids.values():
            nid += 1
        aid = nid
        num_ids[list_id] = nid
        fmt = NUMFMT_OUT.get(style, "decimal")
        if style not in NUMFMT_OUT:
            approximations.append(f"list style {style!r} -> decimal")
        abs_xml.append(
            f"<w:abstractNum w:abstractNumId=\"{aid}\"><w:lvl w:ilvl=\"0\">"
            f"<w:numFmt w:val=\"{fmt}\"/><w:lvlText w:val=\"%1.\"/></w:lvl></w:abstractNum>")
        num_xml.append(
            f"<w:num w:numId=\"{nid}\"><w:abstractNumId w:val=\"{aid}\"/></w:num>")
        return nid

    style_names = set()
    for e in envs:
        if e["kind"] == "style" and isinstance(e["body"].get("name"), str):
            style_names.add(e["body"]["name"])

    def para_xml(e):
        b = e["body"]
        ppr = ""
        if isinstance(b.get("align"), str) and b["align"] in ALIGN_OUT:
            ppr += f"<w:jc w:val=\"{ALIGN_OUT[b['align']]}\"/>"
        if b.get("dir") == "rtl":
            ppr += "<w:bidi/>"
        st = b.get("style")
        if isinstance(st, dict) and "$ref" in st:
            _, name = _style_run_map(envs, st["$ref"])
            if name in style_names:
                ppr += f"<w:pStyle w:val=\"{name}\"/>"
        li = b.get("list")
        if isinstance(li, dict) and isinstance(li.get("id"), str):
            ldef = next((x for x in envs if x["kind"] == "list-def"
                         and x["body"].get("id") == li["id"]), None)
            f = (ldef or {}).get("body", {}).get("style", "bullet")
            nid = num_id_for(li["id"], f if isinstance(f, str) else "bullet")
            ppr += (f"<w:numPr><w:ilvl w:val=\"{li.get('level', 0)}\"/>"
                    f"<w:numId w:val=\"{nid}\"/></w:numPr>")
        runs = "".join(run_xml(r) for r in kids.get(e["oid"], []) if r["kind"] == "run")
        return f"<w:p>{('<w:pPr>' + ppr + '</w:pPr>') if ppr else ''}{runs}</w:p>"

    def run_xml(e):
        b = e["body"]
        rm = {}
        st = b.get("style")
        if isinstance(st, dict) and "$ref" in st:
            rm, _ = _style_run_map(envs, st["$ref"])
        raw = b.get("text") or ""
        xml = "<w:r>" + rpr_xml(rm if isinstance(rm, dict) else {},
                                b.get("lang") if isinstance(b.get("lang"), str) else None)
        buf = ""

        def esc(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def flush():
            nonlocal buf, xml
            if buf:
                sp = " xml:space=\"preserve\"" if buf[:1] == " " or buf[-1:] == " " else ""
                xml += f"<w:t{sp}>{esc(buf)}</w:t>"
                buf = ""

        for ch in raw:
            if ch == "\t":
                flush()
                xml += "<w:tab/>"
            elif ch == "\n":
                flush()
                xml += "<w:br/>"
            else:
                buf += ch
        flush()
        return xml + "</w:r>"

    def table_xml(e, depth=0):
        cols = e["body"].get("cols", 0) if isinstance(e["body"], dict) else 0
        s = "<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>"
        if cols:
            s += "<w:tblGrid>" + "<w:gridCol/>" * cols + "</w:tblGrid>"
        for r in [c for c in kids.get(e["oid"], []) if c["kind"] == "row"]:
            s += "<w:tr>"
            for c in [x for x in kids.get(r["oid"], []) if x["kind"] == "cell"]:
                gs = c["body"].get("gridSpan", 1) if isinstance(c["body"], dict) else 1
                s += "<w:tc><w:tcPr>"
                if gs != 1:
                    s += f"<w:gridSpan w:val=\"{gs}\"/>"
                s += "</w:tcPr>"
                cell_kids = kids.get(c["oid"], [])
                # Emit paragraphs and nested tables in index order
                for ck in cell_kids:
                    if ck["kind"] == "paragraph":
                        s += para_xml(ck)
                    elif ck["kind"] == "table" and depth < 8:  # guard against runaway nesting
                        s += table_xml(ck, depth + 1)
                if not any(ck["kind"] == "paragraph" for ck in cell_kids):
                    s += "<w:p/>"  # every cell needs >= 1 paragraph (OOXML spec)
                s += "</w:tc>"
            s += "</w:tr>"
        return s + "</w:tbl>"

    # Build named-style -> paragraph spacing map for export
    style_spacing = {}
    for e in envs:
        if e["kind"] == "style" and isinstance(e["body"].get("name"), str):
            para = e["body"].get("paragraph", {})
            if para:
                style_spacing[e["body"]["name"]] = para

    content = []
    media, img_rels, img_ct = {}, [], []
    img_n = 0
    EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
                   "image/bmp": "bmp", "image/tiff": "tif", "image/webp": "webp"}
    for e in kids.get(1, []):
        if e["kind"] == "paragraph":
            content.append(para_xml(e))
        elif e["kind"] == "table":
            content.append(table_xml(e))
        elif e["kind"] == "image":
            b = e["body"] if isinstance(e["body"], dict) else {}
            sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
            entry = assets.get(sha) if isinstance(sha, bytes) else None
            blob = entry[1] if isinstance(entry, tuple) else None
            if blob is None:
                approximations.append(f"image {e['oid']}: bytes unavailable, dropped")
                continue
            img_n += 1
            mime = b.get("mime", "application/octet-stream")
            ext = EXT_BY_MIME.get(mime, "bin")
            media[f"word/media/img{img_n}.{ext}"] = blob
            img_ct.append(ct_item(f"/word/media/img{img_n}.{ext}", mime))
            img_rels.append(rel_item(f"rImg{img_n}",
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                f"media/img{img_n}.{ext}"))
            ew, eh = bmu_to_emu(b.get("w", 1000)), bmu_to_emu(b.get("h", 1000))
            content.append(
                f"<w:p><w:r><w:drawing><wp:inline xmlns:wp=\"{WP}\" distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
                f"<wp:extent cx=\"{ew}\" cy=\"{eh}\"/>"
                f"<wp:docPr id=\"{img_n}\" name=\"img{img_n}\"/>"
                f"<a:graphic xmlns:a=\"{A}\"><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
                f"<pic:pic xmlns:pic=\"{PIC}\"><pic:nvPicPr><pic:cNvPr id=\"{img_n}\" name=\"img{img_n}\"/>"
                "<pic:cNvPicPr/></pic:nvPicPr>"
                f"<pic:blipFill><a:blip r:embed=\"rImg{img_n}\" xmlns:r=\"{R}\"/>"
                "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
                f"<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"{ew}\" cy=\"{eh}\"/></a:xfrm>"
                "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
                "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>")
    content = "".join(content)
    document = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                f"<w:document xmlns:w=\"{W}\"><w:body>{content}</w:body></w:document>")
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
              f"<w:styles xmlns:w=\"{W}\">"
              "<w:style w:type=\"paragraph\" w:styleId=\"Normal\" w:default=\"1\"><w:name w:val=\"Normal\"/></w:style>"
              + "".join(
                  f"<w:style w:type=\"paragraph\" w:styleId=\"{n}\"><w:name w:val=\"{n}\"/>"
                  "<w:basedOn w:val=\"Normal\"/>"
                  + ("<w:pPr><w:spacing"
                     + (f" w:before=\"{style_spacing[n]['spaceBefore']}\"" if 'spaceBefore' in style_spacing.get(n, {}) else "")
                     + (f" w:after=\"{style_spacing[n]['spaceAfter']}\"" if 'spaceAfter' in style_spacing.get(n, {}) else "")
                     + "/></w:pPr>" if n in style_spacing else "")
                  + "</w:style>" for n in sorted(style_names))
              + "</w:styles>")
    numbering = ""
    if num_xml:
        numbering = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                     f"<w:numbering xmlns:w=\"{W}\">{''.join(abs_xml)}{''.join(num_xml)}</w:numbering>")
    files = {
        "[Content_Types].xml": (CT_XML % (
            ct_item("/_rels/.rels", "application/vnd.openxmlformats-package.relationships+xml")
            + ct_item("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml")
            + ct_item("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
            + ct_item("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml")
            + (ct_item("/word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml") if num_xml else "")
            + "".join(img_ct))).encode(),
        "_rels/.rels": (RELS_XML % rel_item("rId1",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "word/document.xml")).encode(),
        "word/_rels/document.xml.rels": (RELS_XML % (
            rel_item("rS", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", "styles.xml")
            + (rel_item("rN", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering", "numbering.xml") if num_xml else "")
            + "".join(img_rels))).encode(),
        "word/document.xml": document.encode(),
        "word/styles.xml": styles.encode(),
        "docProps/core.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            "<cp:coreProperties xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\" "
            f"xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:title>{title}</dc:title></cp:coreProperties>").encode(),
    }
    if num_xml:
        files["word/numbering.xml"] = numbering.encode()
    files.update(media)
    return opc_write(files)

def sample_docx_complex() -> bytes:
    """RTL Arabic + real PNG + gridSpan + nested table + hyperlink + 2-level list."""
    from .common import png1x1
    img = png1x1((30, 120, 200))
    numbering = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:numbering xmlns:w=\"{W}\">"
        "<w:abstractNum w:abstractNumId=\"0\">"
        "<w:lvl w:ilvl=\"0\"><w:numFmt w:val=\"bullet\"/></w:lvl>"
        "<w:lvl w:ilvl=\"1\"><w:numFmt w:val=\"decimal\"/></w:lvl></w:abstractNum>"
        "<w:num w:numId=\"3\"><w:abstractNumId w:val=\"0\"/></w:num></w:numbering>")
    body = (
        "<w:p><w:r><w:t>Complex report</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:bidi/><w:jc w:val=\"right\"/></w:pPr>"
        "<w:r><w:rPr><w:lang w:val=\"ar-SA\"/><w:b/></w:rPr>"
        "<w:t>تقرير المبيعات السنوي</w:t></w:r></w:p>"
        "<w:p><w:r><w:t xml:space=\"preserve\">Mixed </w:t></w:r>"
        "<w:r><w:t>عربي and English 123</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"3\"/></w:numPr></w:pPr>"
        "<w:r><w:t>outer</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"1\"/><w:numId w:val=\"3\"/></w:numPr></w:pPr>"
        "<w:r><w:t>nested</w:t></w:r></w:p>"
        "<w:p><w:hyperlink r:id=\"rLink1\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<w:r><w:t>click here</w:t></w:r></w:hyperlink></w:p>"
        "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr>"
        "<w:p><w:r><w:t>spanning</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p>"
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>deep</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:drawing><wp:inline xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
        "<wp:extent cx=\"914400\" cy=\"457200\"/><wp:docPr id=\"1\" name=\"img1\"/>"
        f"<a:graphic xmlns:a=\"{A}\"><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        "<pic:pic xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        "<pic:nvPicPr><pic:cNvPr id=\"1\" name=\"img1\"/><pic:cNvPicPr/></pic:nvPicPr>"
        f"<pic:blipFill><a:blip r:embed=\"rImg1\" xmlns:r=\"{R}\"/>"
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"914400\" cy=\"457200\"/></a:xfrm>"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>")
    document = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                f"<w:document xmlns:w=\"{W}\"><w:body>{body}</w:body></w:document>")
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:styles xmlns:w=\"{W}\">"
        "<w:style w:type=\"paragraph\" w:styleId=\"Normal\" w:default=\"1\"/></w:styles>")
    files = {
        "[Content_Types].xml": (CT_XML % (
            ct_item("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
            + ct_item("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml")
            + ct_item("/word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml")
            + ct_item("/word/media/img1.png", "image/png"))).encode(),
        "word/_rels/document.xml.rels": (RELS_XML % (
            rel_item("rImg1", f"{R}/image", "media/img1.png")
            + rel_item("rLink1", f"{R}/hyperlink", "https://example.invalid", "External"))).encode(),
        "word/document.xml": document.encode(),
        "word/styles.xml": styles.encode(),
        "word/numbering.xml": numbering.encode(),
        "word/media/img1.png": img,
    }
    return opc_write(files)

def sample_docx() -> bytes:
    numbering = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:numbering xmlns:w=\"{W}\">"
        "<w:abstractNum w:abstractNumId=\"0\"><w:lvl w:ilvl=\"0\"><w:numFmt w:val=\"bullet\"/></w:lvl></w:abstractNum>"
        "<w:num w:numId=\"7\"><w:abstractNumId w:val=\"0\"/></w:num></w:numbering>")
    body = (
        "<w:p><w:pPr><w:pStyle w:val=\"Title\"/><w:jc w:val=\"center\"/></w:pPr>"
        "<w:r><w:rPr><w:b/><w:sz w:val=\"48\"/></w:rPr><w:t>Annual Report</w:t></w:r></w:p>"
        "<w:p><w:r><w:t xml:space=\"preserve\">Hello </w:t></w:r>"
        "<w:r><w:rPr><w:i/></w:rPr><w:t>BMO</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"7\"/></w:numPr></w:pPr>"
        "<w:r><w:t>First item</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"7\"/></w:numPr></w:pPr>"
        "<w:r><w:t>Second item</w:t></w:r></w:p>"
        "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
        "<w:tr><w:tc><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>R1C1</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>R1C2</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>R2C1</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>R2C2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>End.</w:t></w:r><w:r><w:drawing/></w:r></w:p>")
    document = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                f"<w:document xmlns:w=\"{W}\"><w:body>{body}</w:body></w:document>")
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:styles xmlns:w=\"{W}\">"
        "<w:style w:type=\"paragraph\" w:styleId=\"Normal\" w:default=\"1\"/>"
        "<w:style w:type=\"paragraph\" w:styleId=\"Title\"><w:basedOn w:val=\"Normal\"/></w:style></w:styles>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
            + ct_item("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml")
            + ct_item("/word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"))).encode(),
        "word/document.xml": document.encode(),
        "word/styles.xml": styles.encode(),
        "word/numbering.xml": numbering.encode(),
    })
