"""Converter round-trips with MEASURED fidelity (never asserted in prose).
OOXML -> BMO -> OOXML -> BMO; canonical models compared per category.
Run: python tests/converters/test_converters.py
"""
import json
import sys
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "converters" / "python"))
sys.path.insert(0, str(HERE.parent / "conformance"))
sys.path.insert(0, str(HERE.parent / "vectors"))
from bmo_ooxml import container, common, docx, pptx, xlsx
from bmo_reader import open_file, read_chunk
from model_check import parse_envelope, validate_structure
from domains_check import validate_bodies

passed = failed = 0
MATRIX = {}

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

def to_bmo(doc_type, features, title, file_id, envs, assets=None):
    objs = [(e["oid"], {"kind": e["kind"], "parent": e["parent"],
                        "index": e["index"], "body": e["body"]}) for e in envs]
    raw = container.genesis(file_id, container.document_meta(
        doc_type, file_id, title, features), objs,
        container.assets_extra(assets or {}))
    f = open_file(raw)
    got, ids, tables = [], set(), []
    for oid, ct, fo, tl in f["entries"]:
        ids.add(oid)
        if ct == 0x0010:
            got.append((oid, read_chunk(raw, fo, len(raw))[1]))
    off, end = 64, len(raw)
    from bmo_reader import parse_chunk_hdr, parse_footer
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64])
                off += 64
                continue
            except Exception:
                pass
        try:
            h = parse_chunk_hdr(raw[off:off + 40])
        except Exception:
            break
        total = 40 + h["slen"] + 4
        if off + total > end:
            break
        if h["type"] == 0x0004:
            tables.append(raw[off + 40:off + 40 + h["slen"]])
        off += total
    validate_structure(got, ids, tables)
    validate_bodies([parse_envelope(o, p) for o, p in got])
    return raw

def canon(envs):
    return common.normalize(envs)

def t_docx():
    data = docx.sample_docx()
    title1, envs1, st1, assets1 = docx.import_docx(data)
    assert not assets1, "sample drawing is empty (no blip)"
    assert st1["skipped"].get("images", 0) == 1, st1
    assert st1["paragraphs"] == 9, st1
    assert st1["tables"] == 1 and st1["cells"] == 4, st1
    raw = to_bmo("document", ["model.envelope-v1", "text.v1"], title1,
                 bytes(15) + b"\x10", envs1)
    approx = []
    out2 = docx.export_docx(envs1, title1, approx)
    assert approx == [], approx
    title2, envs2, st2, _ = docx.import_docx(out2)
    assert title2 == title1
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["docx"] = {"fidelity": fib, "import_stats": st1,
                      "export_approximations": approx}
    assert fib["overall_pct"] == 100.0, json.dumps(fib, indent=1)[:2000]

def t_xlsx():
    data = xlsx.sample_xlsx()
    title1, envs1, st1, _ = xlsx.import_xlsx(data)
    assert st1["sheets"] == 2 and st1["formulas"] == 2, st1
    assert st1["cross_sheet_refs"] == 1, st1
    d1 = next(e for e in envs1 if e["kind"] == "sheet" and e["body"]["name"] == "Summary")
    assert d1["body"]["cells"]["D1"]["deps"] == ["A1"], d1["body"]["cells"]["D1"]
    c2 = next(e for e in envs1 if e["kind"] == "sheet" and e["body"]["name"] == "Summary")
    deps = c2["body"]["cells"]["C2"]["deps"]
    assert "A1" not in deps and "A2" in deps and "A10" in deps, deps  # no Q1 leakage
    raw = to_bmo("spreadsheet", ["model.envelope-v1", "sheet.v1"], title1,
                 bytes(15) + b"\x11", envs1)
    approx = []
    out2 = xlsx.export_xlsx(envs1, title1, approx)
    title2, envs2, st2, _ = xlsx.import_xlsx(out2)
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["xlsx"] = {"fidelity": fib, "import_stats": st1,
                      "export_approximations": approx}
    assert fib["overall_pct"] == 100.0, json.dumps(fib, indent=1)[:2000]

def t_docx_complex():
    data = docx.sample_docx_complex()
    title1, envs1, st1, assets1 = docx.import_docx(data)
    assert st1["images_imported"] == 1, st1
    assert st1["tables"] == 2 and st1["rows"] == 3 and st1["cells"] == 4, st1
    assert st1["skipped"].get("nested_tables", 0) == 0, st1
    assert st1["skipped"].get("hyperlinks", 0) == 1, st1
    assert any(e["kind"] == "paragraph" and e["body"].get("dir") == "rtl" for e in envs1)
    assert any(e["kind"] == "run" and e["body"].get("lang") == "ar-SA" for e in envs1)
    assert any(e["kind"] == "run" and e["body"].get("script") == "Arab" for e in envs1)
    by_id = {e["oid"]: e for e in envs1}
    assert any(e["kind"] == "table" and by_id[e["parent"]]["kind"] == "cell"
               for e in envs1), "nested table must remain inside its containing cell"
    raw = to_bmo("document", ["model.envelope-v1", "text.v1"], title1,
                 bytes(15) + b"\x15", envs1, assets1)
    approx = []
    out2 = docx.export_docx(envs1, title1, approx, assets1)
    assert approx == [], approx
    title2, envs2, st2, assets2 = docx.import_docx(out2)
    assert assets1.keys() == assets2.keys() and all(
        assets1[k][1] == assets2[k][1] for k in assets1), "image bytes must round-trip"
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["docx-complex"] = {"fidelity": fib, "import_stats": st1}
    assert fib["overall_pct"] == 100.0, json.dumps(fib, indent=1)[:2000]

def t_xlsx_complex():
    data = xlsx.sample_xlsx_complex()
    title1, envs1, st1, _ = xlsx.import_xlsx(data)
    first = next(e for e in envs1 if e["kind"] == "sheet" and e["body"]["name"] == "First")
    a1 = first["body"]["cells"]["A1"]
    assert a1["v"] == 44562 and set(a1.get("s", {})) == {"$ref"}, a1  # date numfmt ref
    b2 = first["body"]["cells"]["B2"]
    assert b2["deps"] == ["A2", "A3"] and st1["cross_sheet_refs"] == 1, b2
    assert first["body"]["merges"] == [[0, 0, 0, 1]]
    raw = to_bmo("spreadsheet", ["model.envelope-v1", "sheet.v1"], title1,
                 bytes(15) + b"\x16", envs1)
    approx = []
    out2 = xlsx.export_xlsx(envs1, title1, approx)
    title2, envs2, st2, _ = xlsx.import_xlsx(out2)
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["xlsx-complex"] = {"fidelity": fib, "import_stats": st1}
    assert fib["overall_pct"] == 100.0, json.dumps(fib, indent=1)[:2000]

def t_pdf_import():
    import tempfile
    from bmo_ooxml import pdf_imp
    from bmo_ooxml.container import assets_extra
    tmp = Path(tempfile.mkdtemp(prefix="bmo-pdf-"))
    pdf = pdf_imp.sample_pdf(str(tmp / "sample.pdf"))
    data = Path(pdf).read_bytes()
    (tmp / "img.png").unlink(missing_ok=True)
    title, envs1, st1, assets1 = pdf_imp.import_pdf(data)
    assert title == "PDF Demo", title
    assert st1["pages"] == 1 and st1["tables"] == 1, st1
    assert st1["images_imported"] == 1, st1
    texts = [r["body"]["text"] for r in envs1 if r["kind"] == "run"]
    assert any("Hello from a real PDF" in t for t in texts), texts
    assert "R1C1" in texts and "R2C2" in texts
    kinds = {e["kind"] for e in envs1}
    assert {"paragraph", "table", "row", "cell", "image"} <= kinds, kinds
    raw = to_bmo_extras("document", ["model.envelope-v1", "text.v1", "graphics.v1"],
                        title, bytes(15) + b"\x18", envs1, assets1)
    MATRIX["pdf-import"] = {"import_stats": st1, "objects": len(envs1),
                            "valid_bmo": True}

def to_bmo_extras(doc_type, features, title, file_id, envs, assets):
    return to_bmo(doc_type, features, title, file_id, envs, assets)

def t_pptx_complex():
    data = pptx.sample_pptx_complex()
    title1, envs1, st1, assets1 = pptx.import_pptx(data)
    assert st1["images_imported"] == 1 and st1["notes"] == 1, st1
    assert any(e["kind"] == "paragraph" and e["body"].get("dir") == "rtl" for e in envs1)
    assert any(e["kind"] == "table" for e in envs1)
    s1 = next(e for e in envs1 if e["kind"] == "slide" and e["index"] == 2)
    assert s1["body"]["transition"] == {"type": "wipe", "durMs": 900}, s1["body"]
    raw = to_bmo("presentation",
                 ["model.envelope-v1", "text.v1", "graphics.v1", "deck.v1"],
                 title1, bytes(15) + b"\x17", envs1, assets1)
    approx = []
    out2 = pptx.export_pptx(envs1, title1, approx, assets1)
    title2, envs2, st2, _ = pptx.import_pptx(out2)
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["pptx-complex"] = {"fidelity": fib, "import_stats": st1,
                              "export_approximations": approx}
    non_slide = {k: v for k, v in fib["by_kind"].items() if k != "slide"}
    assert all(v["pct"] == 100.0 for v in non_slide.values()), fib["by_kind"]
    assert any(e["kind"] == "image" for e in envs2), "picture must survive"

def t_pptx():
    data = pptx.sample_pptx()
    title1, envs1, st1, assets1 = pptx.import_pptx(data)
    assert not assets1, "sample pic has no blip target"
    assert st1["slides"] == 2 and st1["notes"] == 1, st1
    assert st1["skipped"].get("pictures", 0) == 1, st1
    s1 = next(e for e in envs1 if e["kind"] == "slide" and e["index"] == 2)
    assert s1["body"]["transition"] == {"type": "fade", "durMs": 500}, s1["body"]
    raw = to_bmo("presentation",
                 ["model.envelope-v1", "text.v1", "graphics.v1", "deck.v1"],
                 title1, bytes(15) + b"\x12", envs1)
    approx = []
    out2 = pptx.export_pptx(envs1, title1, approx)
    assert any("notes" in a for a in approx), approx
    title2, envs2, st2, _ = pptx.import_pptx(out2)
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["pptx"] = {"fidelity": fib, "import_stats": st1,
                      "export_approximations": approx}
    non_slide = {k: v for k, v in fib["by_kind"].items() if k != "slide"}
    assert all(v["pct"] == 100.0 for v in non_slide.values()), fib["by_kind"]
    assert any("notes" in str(x) for x in fib["dropped"]), fib["dropped"]

def t_pptx_media():
    title1, envs1, st1, assets1 = pptx.import_pptx(pptx.sample_pptx_media())
    assert st1["media_imported"] == 2 and st1["skipped"]["media"] == 0, st1
    media1 = [e for e in envs1 if e["kind"] == "media"]
    assert len(media1) == 2
    assert {assets1[e["body"]["asset"]["$asset"]][1] for e in media1} == {
        b"BMO video fixture bytes", b"BMO audio fixture bytes"}
    to_bmo("presentation", ["model.envelope-v1", "deck.v1"], title1,
           bytes(15) + b"\x19", envs1, assets1)
    approx = []
    out = pptx.export_pptx(envs1, title1, approx, assets1)
    from pptx import Presentation
    assert len(Presentation(BytesIO(out)).slides) == 1
    title2, envs2, st2, assets2 = pptx.import_pptx(out)
    assert st2["media_imported"] == 2, st2
    assert {assets2[e["body"]["asset"]["$asset"]][1] for e in envs2
            if e["kind"] == "media"} == {
        b"BMO video fixture bytes", b"BMO audio fixture bytes"}
    fib = common.fidelity(canon(envs1), canon(envs2))
    MATRIX["pptx-media"] = {
        "fidelity": fib,
        "import_stats": st1,
        "assets_preserved": len(media1),
        "export_approximations": approx,
    }
    assert fib["by_kind"]["media"] == {"kept": 2, "of": 2, "pct": 100.0}, fib

def t_csv():
    from bmo_ooxml import csvmd
    title, envs, st = csvmd.import_csv("name,age,active,score\namy,30,true,9.5\nbo,,false,\n", "People")
    assert st["cells"] == 10, st  # blanks stay sparse (2 omitted of 12)
    raw = to_bmo("spreadsheet", ["model.envelope-v1", "sheet.v1"], title,
                 bytes(15) + b"\x13", envs)
    sh = next(e for e in envs if e["kind"] == "sheet")
    assert sh["body"]["cells"]["A2"] == {"v": "amy"}, sh["body"]["cells"]
    assert sh["body"]["cells"]["B2"] == {"v": 30}
    assert sh["body"]["cells"]["C2"] == {"v": True}
    assert sh["body"]["cells"]["D2"]["v"] == 9.5
    assert "B3" not in sh["body"]["cells"] and "D3" not in sh["body"]["cells"]

SAMPLE_MD = """# Report

Hello **bold** and *italic* world.

- first item
- second item
  - nested one

| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |

```py
print(1)
```

> quoted line
"""

def t_markdown():
    from bmo_ooxml import csvmd
    title, envs, st = csvmd.import_markdown(SAMPLE_MD, "MD Demo")
    raw = to_bmo("document", ["model.envelope-v1", "text.v1"], title,
                 bytes(15) + b"\x14", envs)
    kinds = sorted({e["kind"] for e in envs})
    for k in ("paragraph", "run", "style", "list-def", "table", "row", "cell"):
        assert k in kinds, kinds
    heads = [e for e in envs if e["kind"] == "paragraph"
             and isinstance(e["body"].get("style"), dict)]
    assert heads, "styled blocks (heading/code/quote) expected"
    assert st["tables"] == 1, st

if __name__ == "__main__":
    print("converter round-trips (measured):")
    for name, fn in [("docx", t_docx), ("xlsx", t_xlsx), ("pptx", t_pptx),
                     ("csv", t_csv), ("markdown", t_markdown),
                     ("pdf-import", t_pdf_import),
                     ("docx-complex", t_docx_complex), ("xlsx-complex", t_xlsx_complex),
                     ("pptx-complex", t_pptx_complex),
                     ("pptx-media", t_pptx_media)]:
        check(name, fn)
    (HERE / "compat_matrix.json").write_text(json.dumps(MATRIX, indent=2) + "\n")
    print(f"{passed} passed, {failed} failed (matrix -> compat_matrix.json)")
    sys.exit(1 if failed else 0)
