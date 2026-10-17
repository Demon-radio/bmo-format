"""PPTX <-> BMO (RFC-0006, composing RFC-0003/0004). Zero dependencies.
Scope v0.2: slides, text shapes -> paragraph/run, tables in slides,
transitions, notes, pictures, media (video/audio) and charts as $asset blobs.
Masters/layouts import as stubs; export emits a minimal complete package.
"""
import hashlib
from xml.etree import ElementTree as ET

from .common import A, CT_XML, P, R, RELS_XML, bmu_to_emu, ct_item, emu_to_bmu, local, mime_for, opc_read, opc_write, qn, rel_item, sniff_script

MEDIA_MIMES = {
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime",
    "avi": "video/avi", "wmv": "video/x-ms-wmv", "webm": "video/webm",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
    "ogg": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac",
}

MEDIA_EXTS = {
    "video/mp4": "mp4", "video/quicktime": "mov", "video/avi": "avi",
    "video/x-ms-wmv": "wmv", "video/webm": "webm", "audio/mpeg": "mp3",
    "audio/mp4": "m4a", "audio/wav": "wav", "audio/ogg": "ogg",
    "audio/flac": "flac", "audio/aac": "aac",
}

SPD_MS = {"slow": 750, "med": 500, "fast": 250}

class Ctx:
    def __init__(self):
        self.next_id = 1
        self.counters = {}
        self.envs = []
        self.style_n = 0
        self.lists = set()
        self.assets = {}
        self.stats = {"slides": 0, "paragraphs": 0, "runs": 0, "tables": 0,
                      "masters": 0, "layouts": 0, "notes": 0,
                      "skipped": {"pictures": 0, "connectors": 0, "media": 0,
                                  "charts": 0, "smartart": 0, "ole": 0}}

    def new(self, kind, parent, body):
        i = self.counters.get(parent, 0)
        self.counters[parent] = i + 1
        oid = self.next_id
        self.next_id += 1
        self.envs.append({"oid": oid, "kind": kind, "parent": parent,
                          "index": i, "body": body})
        return oid

    def style_for(self, run):
        self.style_n += 1
        return self.new("style", 1, {"name": f"pptx-s{self.style_n}", "run": run})

def _import_paragraph(ctx, a_p, parent):
    ppr = a_p.find(qn(A, "pPr"))
    body = {}
    if ppr is not None:
        if ppr.get("algn") in ("l", "ctr", "r", "just"):
            body["align"] = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}[ppr.get("algn")]
        if ppr.get("rtl") == "1":
            body["dir"] = "rtl"
        lvl = ppr.get("lvl", "0")
        buchar = ppr.find(qn(A, "buChar"))
        bunum = ppr.find(qn(A, "buAutoNum"))
        if buchar is not None or bunum is not None:
            lid = f"pptx-lvl{lvl}"
            if lid not in ctx.lists:
                st = "bullet" if buchar is not None else (bunum.get("type", "arabicPeriod") if bunum is not None else "bullet")
                st = "decimal" if st.startswith("arabic") else ("bullet" if st == "bullet" else st)
                ctx.new("list-def", 1, {"id": lid, "style": st})
                ctx.lists.add(lid)
            body["list"] = {"id": lid, "level": int(lvl) if lvl.isdigit() else 0}
    oid = ctx.new("paragraph", parent, body)
    ctx.stats["paragraphs"] += 1
    for r in a_p.findall(qn(A, "r")):
        text = "".join(t.text or "" for t in r.findall(qn(A, "t")))
        brs = r.findall(qn(A, "br"))
        text += "\n" * len(brs)
        if not text:
            continue
        rpr = r.find(qn(A, "rPr"))
        run = {}
        if rpr is not None:
            if rpr.get("b") == "1":
                run["b"] = 1
            if rpr.get("i") == "1":
                run["i"] = 1
            if (rpr.get("sz") or "").isdigit():
                run["size"] = int(rpr.get("sz")) // 100
        body2 = {"text": text, "script": sniff_script(text)}
        if run:
            body2["style"] = {"$ref": ctx.style_for(run)}
        ctx.new("run", oid, body2)
        ctx.stats["runs"] += 1
    for fld in a_p.findall(qn(A, "fld")):
        text = "".join(t.text or "" for t in fld.findall(qn(A, "t")))
        if text:
            ctx.new("run", oid, {"text": text})
            ctx.stats["runs"] += 1
    return oid

def _import_table(ctx, atbl, parent):
    rows = atbl.findall(qn(A, "tr"))
    cols = max((len(r.findall(qn(A, "tc"))) for r in rows), default=0)
    t_oid = ctx.new("table", parent, {"cols": cols} if cols else {})
    ctx.stats["tables"] += 1
    for tr in rows:
        r_oid = ctx.new("row", t_oid, {})
        for tc in tr.findall(qn(A, "tc")):
            c_oid = ctx.new("cell", r_oid, {})
            # NOTE: table cells carry a:txBody (DrawingML), shapes p:txBody.
            tx = tc.find(qn(P, "txBody"))
            if tx is None:
                tx = tc.find(qn(A, "txBody"))
            if tx is not None:
                for a_p in tx.findall(qn(A, "p")):
                    _import_paragraph(ctx, a_p, c_oid)
    return t_oid

def _slide_parts(parts, slide_name):
    base = f"ppt/slides/{slide_name}"
    xml = ET.fromstring(parts[base])
    rels = {}
    rkey = f"ppt/slides/_rels/{slide_name}.rels"
    if rkey in parts:
        for r in ET.fromstring(parts[rkey]).iter():
            if local(r.tag) == "Relationship":
                rels[r.get("Id")] = r.get("Target", "")
    return xml, rels

def _import_media(ctx, node, rels, parts, parent):
    media_node = None
    for ns in (P, A):
        for tag in ("videoFile", "audioFile"):
            media_node = node.find(f".//{{{ns}}}{tag}")
            if media_node is not None:
                break
        if media_node is not None:
            break
    if media_node is None:
        return False

    rid = media_node.get(qn(R, "link")) or media_node.get(qn(R, "id"))
    target = rels.get(rid, "") if rid else ""
    normalized = target.replace("../", "").lstrip("/")
    blob = parts.get("ppt/" + normalized) if normalized else None
    if not blob:
        ctx.stats["skipped"]["media"] += 1
        return True

    mime = mime_for(target)
    if mime == "application/octet-stream":
        extension = target.rsplit(".", 1)[-1].lower()
        mime = MEDIA_MIMES.get(extension, mime)
    sha = hashlib.sha256(blob).digest()
    ctx.assets.setdefault(sha, (mime, blob))
    ctx.new("media", parent, {"asset": {"$asset": sha}, "mime": mime})
    ctx.stats.setdefault("media_imported", 0)
    ctx.stats["media_imported"] += 1
    return True

def import_pptx(data: bytes):
    parts = opc_read(data)
    pres = ET.fromstring(parts["ppt/presentation.xml"])
    order = []
    for sldId in pres.iter():
        if local(sldId.tag) == "sldId":
            order.append((sldId.get(f"{{{R}}}id"), None))
    prel = ET.fromstring(parts.get("ppt/_rels/presentation.xml.rels", b"<r/>"))
    rid2t = {}
    for r in prel.iter():
        if local(r.tag) == "Relationship":
            rid2t[r.get("Id")] = (r.get("Target") or "").split("/")[-1]
    ctx = Ctx()
    deck = ctx.new("deck", 0, {})
    master = ctx.new("master", deck, {"name": "Office Master"})
    ctx.stats["masters"] += 1
    layout = ctx.new("layout", deck, {"name": "Title and Content"})
    ctx.stats["layouts"] += 1
    for rid, _ in order:
        sname = rid2t.get(rid, "")
        if not sname:
            continue
        sld, rels = _slide_parts(parts, sname)
        body = {"layout": {"$ref": layout}}
        trans = sld.find(qn(P, "transition"))
        if trans is not None:
            ttype, dur = "fade", SPD_MS.get(trans.get("spd", "med"), 500)
            for ch in trans:
                ttype = local(ch.tag)
                break
            if trans.get("dur") and trans.get("dur").isdigit():
                dur = int(trans.get("dur"))
            if trans.get("spd") == "slow" and not trans.get("dur"):
                dur = 750
            body["transition"] = {"type": ttype, "durMs": dur}
        # notes via slide rels -> notesSlides
        for rid2, tgt in rels.items():
            if "notesSlide" in tgt:
                nname = tgt.split("/")[-1]
                nxml = parts.get(f"ppt/notesSlides/{nname}")
                if nxml:
                    texts = []
                    for sp in ET.fromstring(nxml).iter():
                        if local(sp.tag) == "sp":
                            for tx in sp.findall(qn(P, "txBody")):
                                for a_p in tx.findall(qn(A, "p")):
                                    run_texts = []
                                    for r in a_p.findall(qn(A, "r")):
                                        run_texts.append("".join(
                                            t.text or "" for t in r.findall(qn(A, "t"))))
                                    if "".join(run_texts).strip():
                                        texts.append("".join(run_texts))
                    if texts:
                        body["notes"] = "\n".join(texts)
                        ctx.stats["notes"] += 1
        s_oid = ctx.new("slide", deck, body)
        ctx.stats["slides"] += 1
        tree = sld.find(qn(P, "cSld")).find(qn(P, "spTree"))
        if tree is None:
            continue
        for sp in tree:
            t = local(sp.tag)
            if t == "sp":
                tx = sp.find(qn(P, "txBody"))  # NOTE: p:txBody, children are a:p
                if tx is not None:
                    for a_p in tx.findall(qn(A, "p")):
                        _import_paragraph(ctx, a_p, s_oid)
            elif t == "graphicFrame":
                gd = sp.find(qn(A, "graphic"))
                gdata = gd.find(qn(A, "graphicData")) if gd is not None else None
                tbl = gdata.find(qn(A, "tbl")) if gdata is not None else None
                if tbl is not None:
                    _import_table(ctx, tbl, s_oid)
                elif _import_media(ctx, sp, rels, parts, s_oid):
                    continue
                else:
                    # Chart or other graphic: store blob as $asset with kind=chart
                    chart_rid = None
                    if gdata is not None:
                        for ch in gdata.iter():
                            if local(ch.tag) == "chart":
                                chart_rid = ch.get(qn(R, "id"))
                                break
                    chart_tgt = rels.get(chart_rid, "") if chart_rid else ""
                    chart_norm = chart_tgt.replace("../", "").lstrip("/")
                    chart_blob = parts.get("ppt/" + chart_norm) if chart_norm else None
                    if chart_blob:
                        sha = hashlib.sha256(chart_blob).digest()
                        ctx.assets.setdefault(sha, ("application/vnd.openxmlformats-officedocument.drawingml.chart+xml", chart_blob))
                        ctx.new("chart", s_oid, {"asset": {"$asset": sha},
                                                 "mime": "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"})
                        ctx.stats.setdefault("charts_imported", 0)
                        ctx.stats["charts_imported"] += 1
                    else:
                        ctx.stats["skipped"]["charts"] += 1
            elif t == "pic":
                if not _import_media(ctx, sp, rels, parts, s_oid):
                    blip = sp.find(f".//{{{A}}}blip")
                    rid = blip.get(qn(R, "embed")) if blip is not None else None
                    tgt = rels.get(rid, "") if rid else ""
                    norm = tgt.replace("../", "").lstrip("/")
                    blob = parts.get("ppt/" + norm) if norm else None
                    if blob:
                        sha = hashlib.sha256(blob).digest()
                        ctx.assets.setdefault(sha, (mime_for(tgt), blob))
                        ext = next((e for e in sp.iter() if local(e.tag) == "ext"), None)
                        w = h = 0
                        if ext is not None:
                            try:
                                w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
                            except ValueError:
                                pass
                        ctx.new("image", s_oid, {"asset": {"$asset": sha},
                                                 "mime": mime_for(tgt),
                                                 "w": emu_to_bmu(w), "h": emu_to_bmu(h)})
                        ctx.stats.setdefault("images_imported", 0)
                        ctx.stats["images_imported"] += 1
                    else:
                        ctx.stats["skipped"]["pictures"] += 1
            elif t == "cxnSp":
                ctx.stats["skipped"]["connectors"] += 1
            elif t in ("sp",) and False:  # handled above
                pass
            elif t == "oleObj":
                ctx.stats["skipped"]["ole"] += 1
            else:
                if _import_media(ctx, sp, rels, parts, s_oid):
                    continue
                else:
                    ctx.stats["skipped"].setdefault(t, 0)
                    ctx.stats["skipped"][t] += 1
    return "Imported PPTX", ctx.envs, ctx.stats, ctx.assets

# --- export (minimal complete package) --------------------------------------

def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def export_pptx(envs, title, approximations, assets=None):
    assets = assets or {}
    by_id = {e["oid"]: e for e in envs}
    kids = {}
    for e in envs:
        kids.setdefault(e["parent"], []).append(e)
    for v in kids.values():
        v.sort(key=lambda e: e["index"])
    deck = next(e for e in envs if e["kind"] == "deck")
    slides = [c for c in kids.get(deck["oid"], []) if c["kind"] == "slide"]

    def run_xml(r):
        rm = {}
        st = r["body"].get("style")
        if isinstance(st, dict) and "$ref" in st:
            t = by_id.get(st["$ref"], {})
            rm = (t.get("body") or {}).get("run", {})
        attrs = ""
        if rm.get("b"):
            attrs += ' b="1"'
        if rm.get("i"):
            attrs += ' i="1"'
        if isinstance(rm.get("size"), int):
            attrs += f' sz="{rm["size"] * 100}"'
        return f"<a:r><a:rPr{attrs}/><a:t>{_esc(r['body'].get('text', ''))}</a:t></a:r>"

    def para_xml(p):
        b = p["body"]
        alg = {"left": "l", "center": "ctr", "right": "r",
               "justify": "just", "start": "l", "end": "r"}.get(b.get("align"), "")
        rtl = ' rtl="1"' if b.get("dir") == "rtl" else ""
        ppr = f"<a:pPr{f' algn=\"{alg}\"' if alg else ''}{rtl}/>" if (alg or rtl or b.get("list")) else ""
        if b.get("list"):
            lv = b["list"].get("level", 0)
            rtl = ' rtl="1"' if b.get("dir") == "rtl" else ""
            ppr = f"<a:pPr lvl=\"{lv}\"{rtl}><a:buChar char=\"•\"/></a:pPr>"
        runs = "".join(run_xml(r) for r in kids.get(p["oid"], []) if r["kind"] == "run")
        return f"<a:p>{ppr}{runs}<a:endParaRPr/></a:p>"

    slide_files, slide_rels, media, media_ct = {}, {}, {}, []
    chart_n = 0
    img_n = 0
    EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
                   "image/bmp": "bmp"}
    for n, s in enumerate(slides, 1):
        rels = [rel_item("rS", f"{R}/slideLayout", "../slideLayouts/slideLayout1.xml")]
        b = s["body"]
        trans = ""
        tr = b.get("transition")
        if isinstance(tr, dict) and isinstance(tr.get("type"), str) and tr["type"] != "none":
            dur = tr.get("durMs", 500) if isinstance(tr.get("durMs"), int) else 500
            spd = "fast" if dur <= 300 else ("med" if dur <= 600 else "slow")
            trans = f"<p:transition spd=\"{spd}\" dur=\"{dur}\"><p:{tr['type']}/></p:transition>"
        shapes = ""
        nid = 2
        for c in kids.get(s["oid"], []):
            if c["kind"] == "paragraph":
                tx = f"<p:txBody><a:bodyPr/><a:lstStyle/>{para_xml(c)}</p:txBody>"
                shapes += (f"<p:sp><p:nvSpPr><p:cNvPr id=\"{nid}\" name=\"s{nid}\"/>"
                           "<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/>" + tx + "</p:sp>")
                nid += 1
            elif c["kind"] == "table":
                rows = ""
                for r in [x for x in kids.get(c["oid"], []) if x["kind"] == "row"]:
                    cells = ""
                    for cc in [x for x in kids.get(r["oid"], []) if x["kind"] == "cell"]:
                        ps = "".join(para_xml(p) for p in kids.get(cc["oid"], []) if p["kind"] == "paragraph")
                        cells += f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{ps}</a:txBody></a:tc>"
                    rows += f"<a:tr>{cells}</a:tr>"
                shapes += (f"<p:graphicFrame><p:nvGraphicFramePr/><a:graphic><a:graphicData>"
                           f"<a:tbl>{rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>")
            elif c["kind"] == "chart":
                b = c["body"] if isinstance(c["body"], dict) else {}
                sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
                entry = assets.get(sha) if isinstance(sha, bytes) else None
                blob = entry[1] if isinstance(entry, tuple) else None
                if blob:
                    chart_n += 1
                    cpath = f"ppt/charts/chart{chart_n}.xml"
                    media[cpath] = blob
                    media_ct.append(ct_item(f"/{cpath}", "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"))
                    rels.append(rel_item(f"rCh{chart_n}", f"{R}/chart", f"../charts/chart{chart_n}.xml"))
                    shapes += (f"<p:graphicFrame><p:nvGraphicFramePr/><a:graphic>"
                               f"<a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/chart\">"
                               f"<c:chart xmlns:c=\"http://schemas.openxmlformats.org/drawingml/2006/chart\" r:id=\"rCh{chart_n}\"/>"
                               f"</a:graphicData></a:graphic></p:graphicFrame>")
                else:
                    approximations.append(f"slide {n} chart {c['oid']}: bytes unavailable, dropped")
            elif c["kind"] == "media":
                b = c["body"] if isinstance(c["body"], dict) else {}
                sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
                entry = assets.get(sha) if isinstance(sha, bytes) else None
                blob = entry[1] if isinstance(entry, tuple) else None
                if blob:
                    img_n += 1
                    mmime = b.get("mime", "video/mp4")
                    mext = MEDIA_EXTS.get(mmime, "bin")
                    mpath = f"ppt/media/media{img_n}.{mext}"
                    media[mpath] = blob
                    media_ct.append(ct_item(f"/{mpath}", mmime))
                    media_tag = "videoFile" if mmime.startswith("video/") else "audioFile"
                    rels.append(rel_item(f"rMed{img_n}", f"{R}/video" if media_tag == "videoFile" else f"{R}/audio",
                                        f"../media/media{img_n}.{mext}"))
                    shapes += (f"<p:pic><p:nvPicPr><p:cNvPr id=\"{nid}\" name=\"med{img_n}\"/>"
                               f"<p:cNvPicPr/><p:nvPr><a:{media_tag} r:link=\"rMed{img_n}\"/>"
                               "</p:nvPr></p:nvPicPr><p:blipFill><a:blip/>"
                               "<a:stretch><a:fillRect/></a:stretch></p:blipFill>"
                               "<p:spPr><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic>")
                    nid += 1
                else:
                    approximations.append(f"slide {n} media {c['oid']}: bytes unavailable, dropped")
            elif c["kind"] == "image":
                b = c["body"] if isinstance(c["body"], dict) else {}
                sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
                entry = assets.get(sha) if isinstance(sha, bytes) else None
                blob = entry[1] if isinstance(entry, tuple) else None
                if blob is None:
                    approximations.append(f"slide {n} image {c['oid']}: bytes unavailable, dropped")
                    continue
                img_n += 1
                mime = b.get("mime", "application/octet-stream")
                ext = EXT_BY_MIME.get(mime, "bin")
                media[f"ppt/media/img{img_n}.{ext}"] = blob
                media_ct.append(ct_item(f"/ppt/media/img{img_n}.{ext}", mime))
                rels.append(rel_item(f"rImg{img_n}", f"{R}/image", f"../media/img{img_n}.{ext}"))
                ew, eh = bmu_to_emu(b.get("w", 1000)), bmu_to_emu(b.get("h", 1000))
                shapes += (f"<p:pic><p:nvPicPr><p:cNvPr id=\"{nid}\" name=\"img{nid}\"/>"
                           "<p:cNvPicPr/></p:nvPicPr><p:blipFill>"
                           f"<a:blip r:embed=\"rImg{img_n}\"/>"
                           "<a:stretch><a:fillRect/></a:stretch></p:blipFill>"
                           f"<p:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/>"
                           f"<a:ext cx=\"{ew}\" cy=\"{eh}\"/></a:xfrm>"
                           "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic>")
                nid += 1
        slide_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                     f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\">"
                     f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld>{trans}</p:sld>")
        pname = f"ppt/slides/slide{n}.xml"
        slide_files[pname] = slide_xml.encode()
        slide_rels[pname] = (RELS_XML % "".join(rels)).encode()

    master_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                  f"<p:sldMaster xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree/></p:cSld></p:sldMaster>")
    layout_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                  f"<p:sldLayout xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree/></p:cSld></p:sldLayout>")
    theme_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                 f"<a:theme xmlns:a=\"{A}\" name=\"BMO\"><a:themeElements><a:clrScheme name=\"x\"/></a:themeElements></a:theme>")
    pres_rels = "".join(rel_item(f"rS{i + 1}", f"{R}/slide", f"slides/slide{n}.xml")
                        for i, n in enumerate(range(1, len(slides) + 1)))
    pres_xml = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
                f"<p:presentation xmlns:p=\"{P}\" xmlns:r=\"{R}\"><p:sldIdLst>"
                + "".join(f"<p:sldId id=\"{256 + n}\" r:id=\"rS{n}\"/>" for n in range(1, len(slides) + 1))
                + "</p:sldIdLst></p:presentation>")
    ct = (CT_XML % (
        ct_item("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
        + "".join(ct_item(f"/{p}", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml") for p in slide_files)
        + ct_item("/ppt/slideMasters/slideMaster1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml")
        + ct_item("/ppt/slideLayouts/slideLayout1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml")
        + ct_item("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml")
        + "".join(media_ct))).encode()
    files = {
        "[Content_Types].xml": ct,
        "_rels/.rels": (RELS_XML % rel_item("rId1", f"{R}/officeDocument", "ppt/presentation.xml")).encode(),
        "ppt/presentation.xml": pres_xml.encode(),
        "ppt/_rels/presentation.xml.rels": (RELS_XML % (
            rel_item("rM", f"{R}/slideMaster", "slideMasters/slideMaster1.xml") + pres_rels)).encode(),
        "ppt/slideMasters/slideMaster1.xml": master_xml.encode(),
        "ppt/slideLayouts/slideLayout1.xml": layout_xml.encode(),
        "ppt/theme/theme1.xml": theme_xml.encode(),
    }
    files.update(slide_files)
    files.update(media)
    for p, r in slide_rels.items():
        files[p.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"] = r
    approximations.append("notes not exported (needs notesMaster); re-import drops notes")
    approximations.append("masters/layouts exported as stubs")
    return opc_write(files)

def sample_pptx_media() -> bytes:
    """Two embedded audio/video relationships with deterministic test bytes."""
    video = b"BMO video fixture bytes"
    audio = b"BMO audio fixture bytes"
    slide = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
             f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\">"
             "<p:cSld><p:spTree>"
             "<p:pic><p:nvPicPr><p:cNvPr id=\"2\" name=\"video\"/>"
             "<p:cNvPicPr/><p:nvPr><a:videoFile r:link=\"rVid\"/></p:nvPr></p:nvPicPr>"
             "<p:blipFill><a:blip/></p:blipFill><p:spPr/></p:pic>"
             "<p:pic><p:nvPicPr><p:cNvPr id=\"3\" name=\"audio\"/>"
             "<p:cNvPicPr/><p:nvPr><a:audioFile r:link=\"rAud\"/></p:nvPr></p:nvPicPr>"
             "<p:blipFill><a:blip/></p:blipFill><p:spPr/></p:pic>"
             "</p:spTree></p:cSld></p:sld>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
            + ct_item("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            + ct_item("/ppt/media/video1.mp4", "video/mp4")
            + ct_item("/ppt/media/audio1.mp3", "audio/mpeg"))).encode(),
        "ppt/presentation.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            f"<p:presentation xmlns:p=\"{P}\" xmlns:r=\"{R}\"><p:sldIdLst>"
            "<p:sldId id=\"256\" r:id=\"rS1\"/></p:sldIdLst></p:presentation>").encode(),
        "ppt/_rels/presentation.xml.rels": (RELS_XML % rel_item(
            "rS1", f"{R}/slide", "slides/slide1.xml")).encode(),
        "ppt/slides/slide1.xml": slide.encode(),
        "ppt/slides/_rels/slide1.xml.rels": (RELS_XML % (
            rel_item("rVid", f"{R}/video", "../media/video1.mp4")
            + rel_item("rAud", f"{R}/audio", "../media/audio1.mp3"))).encode(),
        "ppt/media/video1.mp4": video,
        "ppt/media/audio1.mp3": audio,
    })

def sample_pptx_complex() -> bytes:
    """RTL text + table + notes + wipe transition + real PNG picture."""
    from .common import png1x1
    img = png1x1((30, 160, 60))
    tx_rtl = ("<p:txBody><a:bodyPr/><a:lstStyle/>"
              "<a:p><a:pPr rtl=\"1\" algn=\"r\"/>"
              "<a:r><a:t>عرض تقديمي عربي</a:t></a:r></a:p></p:txBody>")
    s1 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
          f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
          "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"t\"/></p:nvSpPr><p:spPr/>"
          + tx_rtl + "</p:sp>"
          "<p:pic><p:nvPicPr><p:cNvPr id=\"3\" name=\"img\"/></p:nvPicPr><p:blipFill>"
          f"<a:blip r:embed=\"rImg1\"/></p:blipFill>"
          "<p:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"914400\" cy=\"914400\"/></a:xfrm>"
          "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic>"
          "</p:spTree></p:cSld>"
          "<p:transition spd=\"slow\" dur=\"900\"><p:wipe/></p:transition></p:sld>")
    s2 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
          f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
          "<p:graphicFrame><p:nvGraphicFramePr/><a:graphic><a:graphicData>"
          "<a:tbl><a:tr><a:tc><p:txBody><a:bodyPr/><a:lstStyle/>"
          "<a:p><a:r><a:t>H1</a:t></a:r></a:p></p:txBody></a:tc>"
          "<a:tc><p:txBody><a:bodyPr/><a:lstStyle/>"
          "<a:p><a:r><a:t>H2</a:t></a:r></a:p></p:txBody></a:tc></a:tr></a:tbl>"
          "</a:graphicData></a:graphic></p:graphicFrame>"
          "</p:spTree></p:cSld></p:sld>")
    notes = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
             f"<p:notes xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
             "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"n\"/></p:nvSpPr><p:spPr/>"
             "<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>note one</a:t></a:r></a:p>"
             "<a:p><a:r><a:t>note two</a:t></a:r></a:p></p:txBody>"
             "</p:sp></p:spTree></p:cSld></p:notes>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
            + ct_item("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            + ct_item("/ppt/slides/slide2.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            + ct_item("/ppt/notesSlides/notesSlide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml")
            + ct_item("/ppt/media/img1.png", "image/png"))).encode(),
        "ppt/presentation.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            f"<p:presentation xmlns:p=\"{P}\" xmlns:r=\"{R}\"><p:sldIdLst>"
            "<p:sldId id=\"256\" r:id=\"rS1\"/><p:sldId id=\"257\" r:id=\"rS2\"/></p:sldIdLst></p:presentation>").encode(),
        "ppt/_rels/presentation.xml.rels": (RELS_XML % (
            rel_item("rS1", f"{R}/slide", "slides/slide1.xml")
            + rel_item("rS2", f"{R}/slide", "slides/slide2.xml"))).encode(),
        "ppt/slides/slide1.xml": s1.encode(),
        "ppt/slides/slide2.xml": s2.encode(),
        "ppt/slides/_rels/slide1.xml.rels": (RELS_XML % (
            rel_item("rN", f"{R}/notesSlide", "../notesSlides/notesSlide1.xml")
            + rel_item("rImg1", f"{R}/image", "../media/img1.png"))).encode(),
        "ppt/notesSlides/notesSlide1.xml": notes.encode(),
        "ppt/media/img1.png": img,
    })

def sample_pptx() -> bytes:
    def txParas(*args):
        return ("<p:txBody><a:bodyPr/><a:lstStyle/>" + "".join(
            "<a:p>" + "".join(
                f"<a:r><a:rPr{' b=\"1\"' if b else ''}/><a:t>{t}</a:t></a:r>" for t, b in runs)
            + "</a:p>" for runs in args) + "</p:txBody>")
    s1 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
          f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
          "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"t\"/></p:nvSpPr><p:spPr/>"
          + txParas([("Hello BMO", True)]) + "</p:sp>"
          "<p:pic><p:nvPicPr><p:cNvPr id=\"3\" name=\"img\"/></p:nvPicPr></p:pic>"
          "</p:spTree></p:cSld>"
          "<p:transition spd=\"med\"><p:fade thruBlk=\"1\"/></p:transition></p:sld>")
    s2 = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
          f"<p:sld xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
          "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"t2\"/></p:nvSpPr><p:spPr/>"
          + txParas([("Second", False)]) + "</p:sp>"
          "</p:spTree></p:cSld></p:sld>")
    notes = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
             f"<p:notes xmlns:p=\"{P}\" xmlns:a=\"{A}\" xmlns:r=\"{R}\"><p:cSld><p:spTree>"
             "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"n\"/></p:nvSpPr><p:spPr/>"
             "<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Say hello slowly.</a:t></a:r></a:p></p:txBody>"
             "</p:sp></p:spTree></p:cSld></p:notes>")
    return opc_write({
        "[Content_Types].xml": (CT_XML % (
            ct_item("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml")
            + ct_item("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            + ct_item("/ppt/slides/slide2.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            + ct_item("/ppt/notesSlides/notesSlide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"))).encode(),
        "ppt/presentation.xml": (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            f"<p:presentation xmlns:p=\"{P}\" xmlns:r=\"{R}\"><p:sldIdLst>"
            "<p:sldId id=\"256\" r:id=\"rS1\"/><p:sldId id=\"257\" r:id=\"rS2\"/></p:sldIdLst></p:presentation>").encode(),
        "ppt/_rels/presentation.xml.rels": (RELS_XML % (
            rel_item("rS1", f"{R}/slide", "slides/slide1.xml")
            + rel_item("rS2", f"{R}/slide", "slides/slide2.xml"))).encode(),
        "ppt/slides/slide1.xml": s1.encode(),
        "ppt/slides/slide2.xml": s2.encode(),
        "ppt/slides/_rels/slide1.xml.rels": (RELS_XML % rel_item("rN", f"{R}/notesSlide", "../notesSlides/notesSlide1.xml")).encode(),
        "ppt/notesSlides/notesSlide1.xml": notes.encode(),
    })
