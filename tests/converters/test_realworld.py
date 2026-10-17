"""Real-world files: genuine Word/Excel/PowerPoint binaries in, valid BMO
out. No 100% claims (wild files exceed v0.1 scope by design); asserts are:
no crash, valid container+structure+domains, byte-identical assets,
deterministic imports, re-importable exports. Run: python tests/converters/test_realworld.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "converters" / "python"))
sys.path.insert(0, str(HERE.parent / "conformance"))
from bmo_ooxml import container, common, docx, pptx, xlsx  # noqa: E402
from bmo_reader import open_file, read_chunk  # noqa: E402
from model_check import parse_envelope, validate_structure  # noqa: E402
from domains_check import validate_bodies  # noqa: E402

SAMPLES = HERE / "samples"
passed = failed = 0
REPORT = {}

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

def full_validate(raw):
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
    rep = validate_structure(got, ids, tables)
    validate_bodies([parse_envelope(o, p) for o, p in got])
    return rep

def build_bmo(doc_type, features, title, fid, envs, assets):
    objs = [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs]
    return container.genesis(fid, container.document_meta(
        doc_type, fid, title, features), objs, container.assets_extra(assets or {}))

def t_real_docx_images():
    data = (SAMPLES / "real-images.docx").read_bytes()
    t1, envs1, st1, assets1 = docx.import_docx(data)
    assert st1["images_imported"] == 5, st1
    assert len(assets1) >= 1, "dedup may merge identical bytes"
    raw = build_bmo("document", ["model.envelope-v1", "text.v1"], t1,
                    bytes(15) + b"\x40", envs1, assets1)
    rep = full_validate(raw)
    assert "image" in rep["kinds"], rep["kinds"]
    # asset bytes round-trip identical through table+data chunks
    f = open_file(raw)
    seen = {}
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
        h = parse_chunk_hdr(raw[off:off + 40])
        if h["type"] == 0x0005:
            seen[h["oid"]] = raw[off + 40:off + 40 + h["slen"]]
        off += 40 + h["slen"] + 4
        if off + 40 > end:
            break
    assert sorted(seen.values()) == sorted(v for _, v in assets1.values()), "asset bytes must survive chunking"
    # determinism: import twice -> identical models
    _, envs1b, _, _ = docx.import_docx(data)
    assert common.normalize(envs1) == common.normalize(envs1b)
    REPORT["real-docx"] = {"objects": len(envs1), "kinds": sorted(rep["kinds"]),
                           "images": st1["images_imported"], "valid_bmo": True}

def t_real_xlsx():
    data = (SAMPLES / "real-test1.xlsx").read_bytes()
    t1, envs1, st1, _ = xlsx.import_xlsx(data)
    assert st1["sheets"] == 4 and st1["cells"] == 96, st1
    raw = build_bmo("spreadsheet", ["model.envelope-v1", "sheet.v1"], t1,
                    bytes(15) + b"\x41", envs1, None)
    rep = full_validate(raw)
    assert rep["kinds"]["sheet"] and len(rep["kinds"]["sheet"]) == 4
    _, envs1b, _, _ = xlsx.import_xlsx(data)
    assert common.normalize(envs1) == common.normalize(envs1b)
    REPORT["real-xlsx"] = {"objects": len(envs1), "cells": st1["cells"], "valid_bmo": True}

def t_real_pptx():
    data = (SAMPLES / "real-test.pptx").read_bytes()
    t1, envs1, st1, assets1 = pptx.import_pptx(data)
    raw = build_bmo("presentation", ["model.envelope-v1", "text.v1", "deck.v1"], t1,
                    bytes(15) + b"\x42", envs1, assets1)
    rep = full_validate(raw)
    assert rep["kinds"]["slide"] == [e["oid"] for e in envs1 if e["kind"] == "slide"]
    _, envs1b, _, _ = pptx.import_pptx(data)
    assert common.normalize(envs1) == common.normalize(envs1b)
    REPORT["real-pptx"] = {"objects": len(envs1), "valid_bmo": True}

def t_real_pdf_corpus():
    """Multilingual PDF corpus (real binary, built deterministically):
    Arabic/Hebrew/CJK/Latin + ruled table with Arabic cells + image.
    Asserts are gated on which system fonts exist; Arabic extracts as
    shaped presentation forms in visual order (counted, not corrected).
    """
    import tempfile
    from bmo_ooxml import pdf_imp
    tmp = Path(tempfile.mkdtemp(prefix="bmo-pdfcorpus-"))
    pdf_path, avail = pdf_imp.sample_pdf_multilingual(str(tmp / "corpus.pdf"))
    data = Path(pdf_path).read_bytes()
    title, envs1, st1, assets1 = pdf_imp.import_pdf(data)
    assert title == "Multilingual Corpus", title
    assert st1["pages"] == 2 and st1["tables"] == 1, st1
    assert st1["images_imported"] == 1, st1
    runs = [e["body"].get("script") for e in envs1 if e["kind"] == "run"]
    texts = [e["body"].get("text", "") for e in envs1 if e["kind"] == "run"]
    if avail["ar"]:
        assert "Arab" in runs, runs
        assert any(any(0x0600 <= ord(ch) <= 0x06FF or 0xFE70 <= ord(ch) <= 0xFEFF
                       for ch in t) for t in texts), texts
    if avail["he"]:
        assert "Hebr" in runs, runs
    if avail["cjk"]:
        assert "Hani" in runs, runs
    assert any("Multilingual Corpus" == t for t in texts)
    raw = build_bmo("document", ["model.envelope-v1", "text.v1"], title,
                    bytes(15) + b"\x43", envs1, assets1)
    full_validate(raw)
    _, envs1b, _, _ = pdf_imp.import_pdf(data)
    assert common.normalize(envs1) == common.normalize(envs1b)
    REPORT["real-pdf-corpus"] = {
        "pages": st1["pages"], "tables": st1["tables"],
        "images": st1["images_imported"], "objects": len(envs1),
        "scripts": sorted(set(runs)), "fonts": avail, "valid_bmo": True,
    }

if __name__ == "__main__":
    print("real-world files (validity + determinism, no 100% claims):")
    for name, fn in [("real-docx-images", t_real_docx_images),
                     ("real-xlsx", t_real_xlsx), ("real-pptx", t_real_pptx),
                     ("real-pdf-corpus", t_real_pdf_corpus)]:
        check(name, fn)
    mpath = HERE / "compat_matrix.json"
    m = json.loads(mpath.read_text()) if mpath.exists() else {}
    m["realworld"] = REPORT
    mpath.write_text(json.dumps(m, indent=2) + "\n")
    print(f"{passed} passed, {failed} failed (+realworld in compat_matrix.json)")
    sys.exit(1 if failed else 0)
