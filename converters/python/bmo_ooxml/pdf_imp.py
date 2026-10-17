"""PDF -> BMO (text + tables + images + metadata). Zero hand-rolled parsing:
pdfplumber for layout-aware text/tables, pypdf for images/metadata.
Scope v0.1: digital (born-digital) PDFs. Scanned/image-only pages yield
no text (counted, never OCR-hallucinated). Reading order = pdfplumber's
line order; table text is excluded from the flow (no duplication).
"""
import io

from .common import mime_for, sniff_script

class Ctx:
    def __init__(self):
        self.next_id = 1
        self.counters = {}
        self.envs = []
        self.assets = {}
        self.stats = {"pages": 0, "paragraphs": 0, "runs": 0, "tables": 0,
                      "images_imported": 0,
                      "skipped": {"empty_pages": 0, "images": 0, "annotations": 0}}

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

def _in_bbox(x0, top, x1, bottom, bbox):
    bx0, btop, bx1, bbottom = bbox
    return x0 >= bx0 - 1 and x1 <= bx1 + 1 and top >= btop - 1 and bottom <= bbottom + 1

def import_pdf(data: bytes):
    """-> (title, envs, stats, assets). Never raises on malformed content
    beyond zip-level failure; per-page errors are counted and skipped? No:
    structural errors propagate (fail closed); only *features* are skipped."""
    import pdfplumber
    from pypdf import PdfReader
    title = "Imported PDF"
    try:
        meta = PdfReader(io.BytesIO(data)).metadata
        if meta and meta.title:
            title = str(meta.title)
    except Exception:
        pass
    ctx = Ctx()
    doc = ctx.new("document", 0, {"title": title})
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            ctx.stats["pages"] += 1
            tables = []
            try:
                found = page.find_tables()
            except Exception:
                found = []
            for t in found:
                try:
                    rows = t.extract() or []  # pdfplumber Table API
                    rows = [[c or "" for c in r] for r in rows]
                except Exception:
                    continue
                if not rows or not any(any(c.strip() for c in r) for r in rows):
                    continue
                tables.append((t.bbox, rows))
            # Let pdfplumber group glyphs so rotated text and producer-specific
            # character spacing follow the PDF text matrix rather than page axes.
            text_page = page.filter(
                lambda obj: obj["object_type"] != "char"
                or not any(_in_bbox(
                    obj["x0"], obj["top"], obj["x1"], obj["bottom"], bbox)
                    for bbox, _ in tables))
            lines = [line for line in (text_page.extract_text() or "").splitlines()
                     if line.strip()]
            if not lines and not tables:
                ctx.stats["skipped"]["empty_pages"] += 1
            for line in lines:
                p = ctx.new("paragraph", doc, {})
                ctx.stats["paragraphs"] += 1
                ctx.new("run", p, {"text": line, "script": sniff_script(line)})
                ctx.stats["runs"] += 1
            for _bbox, rows in tables:
                ncols = max(len(r) for r in rows)
                t_oid = ctx.new("table", doc, {"cols": ncols})
                ctx.stats["tables"] += 1
                for cells in rows:
                    cells = (list(cells) + [""] * ncols)[:ncols]
                    r_oid = ctx.new("row", t_oid, {})
                    for cv in cells:
                        c_oid = ctx.new("cell", r_oid, {})
                        p = ctx.new("paragraph", c_oid, {})
                        ctx.stats["paragraphs"] += 1
                        if (cv or "").strip():
                            ctx.new("run", p, {"text": cv.strip(), "script": sniff_script(cv)})
                            ctx.stats["runs"] += 1
    # images + annotations via pypdf (stream-level, no layout guessing)
    import hashlib
    try:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            for img in getattr(page, "images", []):
                try:
                    blob = img.data
                except Exception:
                    ctx.skip("images")
                    continue
                if not blob:
                    ctx.skip("images")
                    continue
                sha = hashlib.sha256(blob).digest()
                mime = mime_for(img.name or "")
                if mime == "application/octet-stream":
                    mime = "image/png" if blob[:8] == b"\x89PNG\r\n\x1a\n" else mime
                ctx.assets.setdefault(sha, (mime, blob))
                ctx.new("image", doc, {"asset": {"$asset": sha}, "mime": mime,
                                       "w": 100000, "h": 100000})
                ctx.stats["images_imported"] += 1
            try:
                annots = list(page.get("/Annots") or [])
            except Exception:
                annots = []
            if annots:
                ctx.skip("annotations", len(annots))
    except Exception:
        pass
    return title, ctx.envs, ctx.stats, ctx.assets

def sample_pdf_multilingual(path):
    """Build a deterministic 2-page multilingual corpus PDF (real binary):
    Latin/Arabic/Hebrew/CJK lines, ruled table with Arabic cells, PNG image.
    -> (path, available): available[script] says whether a system font was
    embedded for that script (callers gate their asserts on it).
    Note: PyMuPDF writes Arabic as shaped presentation forms in visual
    order — extraction (and thus import) preserves the producer's order;
    bidi reordering is a renderer concern, counted not corrected."""
    import pymupdf
    from .common import png1x1
    fonts = {s: _corpus_font(s) for s in ("ar", "he", "cjk")}
    avail = {s: bool(p) for s, p in fonts.items()}
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    page, page2 = doc[0], doc[1]  # fresh handles: new_page() orphans earlier ones
    for pg in (page, page2):
        for s in ("ar", "he", "cjk"):
            if fonts[s]:
                pg.insert_font(fontname=s, fontfile=fonts[s])
    page.insert_text((72, 80), "Multilingual Corpus", fontsize=18)
    if avail["ar"]:
        page.insert_text((72, 120), "مرحبا بالعالم نص عربي", fontsize=14, fontname="ar")
        page.insert_text((72, 210), "Mixed: Hello + مرحبا 42", fontsize=12, fontname="ar")
    if avail["he"]:
        page.insert_text((72, 150), "שלום עולם טקסט עברי", fontsize=14, fontname="he")
    if avail["cjk"]:
        page.insert_text((72, 180), "你好世界 中文文本", fontsize=14, fontname="cjk")
    x0, y0, cw, rh = 72, 100, 120, 30
    for i in range(3):
        page2.draw_line((x0, y0 + i * rh), (x0 + 2 * cw, y0 + i * rh))
    for j in range(3):
        page2.draw_line((x0 + j * cw, y0), (x0 + j * cw, y0 + 2 * rh))
    for j, txt in enumerate(("الاسم", "القيمة")):
        kw = {"fontname": "ar"} if avail["ar"] else {}
        page2.insert_text((x0 + j * cw + 4, y0 + 20), txt, fontsize=12, **kw)
    for j, txt in enumerate(("القاهرة", "42")):
        kw = {"fontname": "ar"} if (j == 0 and avail["ar"]) else {}
        page2.insert_text((x0 + j * cw + 4, y0 + 50), txt, fontsize=12, **kw)
    img_path = path + ".png"
    open(img_path, "wb").write(png1x1((140, 40, 40)))
    page2.insert_image((72, 300, 172, 400), filename=img_path)
    doc.set_metadata({"title": "Multilingual Corpus"})
    doc.save(path, deflate=True, garbage=3)
    doc.close()
    return path, avail

_CORPUS_FONTS = {
    "ar": ["arial.ttf", "tahoma.ttf", "segoeui.ttf"],
    "he": ["arial.ttf", "tahoma.ttf", "segoeui.ttf"],
    "cjk": ["msyh.ttc", "simsun.ttc", "msjh.ttc"],
}

def _corpus_font(script):
    import os
    win = os.environ.get("WINDIR", r"C:\Windows")
    for name in _CORPUS_FONTS[script]:
        p = os.path.join(win, "Fonts", name)
        if os.path.exists(p):
            return p
    return None

def sample_pdf(path: str):
    """Build a deterministic test PDF (text + ruled table + PNG image)."""
    import pymupdf  # PyMuPDF (test fixture builder only)
    from .common import png1x1
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "PDF Demo Title", fontsize=20)
    page.insert_text((72, 140), "Hello from a real PDF file.")
    page.insert_text((72, 170), "Second line here.")
    # ruled 2x2 table
    x0, y0, cw, rh = 72, 220, 120, 30
    for i in range(3):
        page.draw_line((x0, y0 + i * rh), (x0 + 2 * cw, y0 + i * rh))
    for j in range(3):
        page.draw_line((x0 + j * cw, y0), (x0 + j * cw, y0 + 2 * rh))
    page.insert_text((x0 + 4, y0 + 20), "R1C1")
    page.insert_text((x0 + cw + 4, y0 + 20), "R1C2")
    page.insert_text((x0 + 4, y0 + 50), "R2C1")
    page.insert_text((x0 + cw + 4, y0 + 50), "R2C2")
    img_path = path + ".png"
    open(img_path, "wb").write(png1x1((40, 140, 40)))
    page.insert_image((72, 320, 172, 420), filename=img_path)
    doc.set_metadata({"title": "PDF Demo"})
    doc.save(path, deflate=True, garbage=3)
    doc.close()
    return path
