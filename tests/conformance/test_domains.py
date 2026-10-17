"""Domain conformance: text/graphics/sheet/deck goldens + negatives.
Mirrors core/rust/tests/domains.rs. Run: python tests/conformance/test_domains.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vectors"))
from bmo_reader import open_file, read_chunk
from make_hello import cbor_encode
from model_check import ModelErr, parse_envelope, validate_structure
from domains_check import validate_bodies

GOLDEN = HERE.parent / "golden"
passed = failed = 0

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

def load_all(name):
    raw = (GOLDEN / name).read_bytes()
    f = open_file(raw)
    objs, ids, tables = [], set(), []
    for oid, ct, fo, tl in f["entries"]:
        ids.add(oid)
        if ct == 0x0010:
            objs.append((oid, read_chunk(raw, fo, len(raw))[1]))
    off, end = 64, len(raw)
    from bmo_reader import parse_chunk_hdr, parse_footer
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64]); off += 64; continue
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
    rep = validate_structure(objs, ids, tables)
    envs = [parse_envelope(o, p) for o, p in objs]
    validate_bodies(envs)
    return raw, rep, envs

def t_legacy_goldens_full_pipeline():
    # hello.bmo (v0.1 envelope + legacy "text" under page) and model.bmo
    # must pass the CURRENT domains validator: backward compat is forever.
    for name in ("hello.bmo", "model.bmo"):
        load_all(name)

def t_table():
    raw, rep, envs = load_all("table.bmo")
    assert rep["kinds"]["row"] == [3, 10]
    assert rep["kinds"]["cell"] == [4, 7, 11, 14]
    r13 = next(e for e in envs if e["oid"] == 13)
    assert r13["body"]["text"] == "R2C1"

def t_text():
    raw, rep, envs = load_all("text.bmo")
    assert rep["root"] == 1
    assert sorted(rep["kinds"]) == ["document", "list-def", "paragraph", "run", "style"], rep["kinds"]
    assert rep["kinds"]["run"] == [5, 6, 8, 10]
    rtl = next(e for e in envs if e["oid"] == 8)
    assert rtl["body"]["script"] == "Arab" and rtl["body"]["lang"] == "ar"

def t_graphics():
    raw, rep, envs = load_all("graphics.bmo")
    assert rep["kinds"]["image"] == [4]
    img = next(e for e in envs if e["oid"] == 4)
    assert img["body"]["mime"] == "image/png"
    # asset bytes resolve through table -> data chunk
    from model_check import table_shas
    assert img["body"]["asset"]["$asset"] in table_shas(open_file(raw) and tables_of(raw)[0])

def tables_of(raw):
    off, end, out = 64, len(raw), []
    from bmo_reader import parse_chunk_hdr, parse_footer
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64]); off += 64; continue
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
            out.append(raw[off + 40:off + 40 + h["slen"]])
        off += total
    return out

def t_sheet():
    raw, rep, envs = load_all("sheet.bmo")
    assert rep["kinds"]["sheet"] == [3, 4]
    s3 = next(e for e in envs if e["oid"] == 3)
    assert s3["body"]["cells"]["D4"]["deps"] == ["A1"]
    assert s3["body"]["cells"]["C3"]["v"] == 3.14
    assert s3["body"]["merges"] == [[0, 0, 0, 1]]

def t_deck():
    raw, rep, envs = load_all("deck.bmo")
    assert rep["kinds"]["slide"] == [4, 7]
    s4 = next(e for e in envs if e["oid"] == 4)
    assert s4["body"]["transition"] == {"type": "fade", "durMs": 300}
    assert "Second line." in s4["body"]["notes"]

def t_slide_media():
    asset = {"$asset": bytes(32)}
    valid = [(1, "deck", 0, {}), (2, "slide", 1, {}),
             (3, "media", 2, {"asset": asset, "mime": "audio/mpeg"})]
    validate_bodies([parse_envelope(oid, E(kind, parent, body))
                     for oid, kind, parent, body in valid])
    for parent, mime in ((2, "image/png"), (1, "video/mp4")):
        try:
            invalid = [(1, "deck", 0, {}), (2, "slide", 1, {}),
                       (3, "media", parent, {"asset": asset, "mime": mime})]
            validate_bodies([parse_envelope(oid, E(kind, parent_id, body))
                             for oid, kind, parent_id, body in invalid])
        except ModelErr as e:
            assert str(e) == "STRUCTURE_INVALID", str(e)
        else:
            raise AssertionError("invalid media MIME or parent accepted")

def E(kind, parent, body):
    return cbor_encode({"kind": kind, "parent": parent, "body": body})

def V(objs):
    envs = [parse_envelope(o, p) for o, p in objs]
    validate_structure(objs, {o for o, _ in objs}, [])
    validate_bodies(envs)

def t_bad_align():
    try:
        V([(1, E("document", 0, {})), (2, E("paragraph", 1, {"align": "diagonal"}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("bad align accepted!")

def t_u2029_rejected():
    evil = "a" + chr(0x2029) + "b"  # PARAGRAPH SEPARATOR must not hide in runs
    try:
        V([(1, E("document", 0, {})), (2, E("paragraph", 1, {})),
           (3, E("run", 2, {"text": evil}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("U+2029 accepted!")

def t_bad_addr():
    try:
        V([(1, E("workbook", 0, {})), (2, E("sheet", 1, {"name": "S", "cells": {"ZZZ99999999": {"v": 1}}}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("bad addr accepted!")

def t_formula_without_deps():
    try:
        V([(1, E("workbook", 0, {})), (2, E("sheet", 1, {"name": "S", "cells": {"A1": {"v": 1, "f": "1+1"}}}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("formula without deps accepted!")

def t_run_under_page():
    try:
        V([(1, E("document", 0, {})), (2, E("page", 1, {"number": 1})),
           (3, E("run", 2, {"text": "x"}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("run under page accepted!")

def t_cell_under_paragraph():
    try:
        V([(1, E("document", 0, {})), (2, E("paragraph", 1, {})),
           (3, E("cell", 2, {}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("cell under paragraph accepted!")

def t_row_under_page():
    try:
        V([(1, E("document", 0, {})), (2, E("page", 1, {"number": 1})),
           (3, E("row", 2, {}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("row under page accepted!")

def t_nested_table_in_cell():
    V([
        (1, E("document", 0, {})),
        (2, E("table", 1, {})),
        (3, E("row", 2, {})),
        (4, E("cell", 3, {})),
        (5, E("table", 4, {})),
        (6, E("row", 5, {})),
        (7, E("cell", 6, {})),
        (8, E("paragraph", 7, {})),
        (9, E("run", 8, {"text": "nested"})),
    ])

def t_list_def_level_formatting():
    V([
        (1, E("document", 0, {})),
        (2, E("list-def", 1, {
            "id": "L1", "style": "decimal", "start": 5, "text": "%1.",
            "levels": [{"level": 1, "style": "lowerLetter",
                        "start": 3, "text": "%2)"}],
        })),
    ])
    for levels in (
        [{"level": 1}, {"level": 1}],
        [{"level": 0}],
        [{"level": 9}],
        [{"level": 1, "start": -1}],
        [{"level": 1, "text": 3}],
    ):
        try:
            V([
                (1, E("document", 0, {})),
                (2, E("list-def", 1, {"id": "L1", "levels": levels})),
            ])
        except ModelErr as e:
            assert str(e) == "STRUCTURE_INVALID", str(e)
        else:
            raise AssertionError(f"invalid list level formatting accepted: {levels}")

def t_table_under_paragraph_rejected():
    try:
        V([(1, E("document", 0, {})), (2, E("paragraph", 1, {})),
           (3, E("table", 2, {}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("table under paragraph accepted!")

def t_dup_sheet_name():
    try:
        V([(1, E("workbook", 0, {})), (2, E("sheet", 1, {"name": "S", "cells": {}})),
           (3, E("sheet", 1, {"name": "S", "cells": {}}))])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("dup sheet name accepted!")

if __name__ == "__main__":
    print("domain conformance:")
    for name, fn in [("legacy_pipeline", t_legacy_goldens_full_pipeline),
                     ("text", t_text), ("graphics", t_graphics), ("sheet", t_sheet),
                     ("deck", t_deck), ("slide_media", t_slide_media),
                     ("table", t_table), ("bad_align", t_bad_align),
                     ("u2029", t_u2029_rejected), ("bad_addr", t_bad_addr),
                     ("formula_no_deps", t_formula_without_deps),
                     ("run_under_page", t_run_under_page),
                     ("cell_under_paragraph", t_cell_under_paragraph),
                     ("row_under_page", t_row_under_page),
                     ("nested_table_in_cell", t_nested_table_in_cell),
                     ("list_def_level_formatting", t_list_def_level_formatting),
                     ("table_under_paragraph", t_table_under_paragraph_rejected),
                     ("dup_sheet", t_dup_sheet_name)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
