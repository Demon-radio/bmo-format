"""Viewer conformance: pure view-model helpers + Tk smoke.
Logic tests need no display. Run: python tests/test_viewer.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "apps" / "desktop"))
sys.path.insert(0, str(HERE.parent / "renderer" / "python"))
sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))
import viewer  # noqa: E402 (imports Tk only inside run_app)
from bmo_render.load import load  # noqa: E402

GOLDEN = HERE / "golden"
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

def t_flow_order():
    m = load((GOLDEN / "text.bmo").read_bytes())
    items = viewer.flow_items(m, 1)
    # resources (style/list-def) are resolved by reference, not flowed
    assert [e["kind"] for e in items] == ["paragraph"] * 3
    assert [e["oid"] for e in items] == [4, 7, 9]

def t_table_grid():
    m = load((GOLDEN / "table.bmo").read_bytes())
    rows = viewer.table_grid(m, 2)
    assert len(rows) == 2 and all(len(r) == 2 for r in rows)
    assert [c["oid"] for r in rows for c in r] == [4, 7, 11, 14]

def t_sheet_grid():
    maxr, maxc, grid, covered, spans = viewer.sheet_grid(
        {"A1": {"v": 1}, "B2": {"v": 2}}, [[0, 0, 0, 1]])
    assert (maxr, maxc) == (2, 2)
    assert spans[(1, 1)] == (1, 2) and (1, 2) in covered
    assert grid[(2, 2)][0] == "B2"

def t_deck_order():
    m = load((GOLDEN / "deck.bmo").read_bytes())
    assert [e["oid"] for e in viewer.deck_slides(m)] == [4, 7]

def t_styles_markers():
    m = load((GOLDEN / "text.bmo").read_bytes())
    by_id = m["by_id"]
    run6 = next(e for e in m["envs"] if e["oid"] == 6)
    assert viewer.run_style(by_id, run6["body"]) == {"size": 24}
    st = {}
    p4 = next(e for e in m["envs"] if e["oid"] == 4)
    assert viewer.list_marker(m, st, p4["body"]) == ""
    p9 = next(e for e in m["envs"] if e["oid"] == 9)
    assert viewer.list_marker(m, st, p9["body"]) == "•  "
    assert st == {"L1": 1}

def t_load_errors():
    m = viewer.load_document(str(GOLDEN / "hello.bmo"))
    assert len(m["envs"]) == 3
    try:
        viewer.load_document(str(GOLDEN / "hello.json"))
    except viewer.FriendlyError as e:
        assert "valid" in str(e) and "convert" in str(e).lower()
    else:
        raise AssertionError("renamed file accepted!")
    # encrypted file (ChaCha20, wired codec): must fail closed asking for a
    # key — NOT "not a valid .bmo file", NOT a codec-upgrade message.
    try:
        viewer.load_document(str(GOLDEN / "encrypted.bmo"))
    except viewer.FriendlyError as e:
        assert "NEED_KEY" in str(e), str(e)
        assert "encrypted" in str(e).lower(), str(e)
        assert "valid .bmo" not in str(e), str(e)
    else:
        raise AssertionError("encrypted file accepted without a key!")
    try:
        viewer.load_document(str(GOLDEN / "does-not-exist.bmo"))
    except viewer.FriendlyError as e:
        assert "read" in str(e).lower()
    else:
        raise AssertionError("missing file accepted!")

def t_plan_manage_flows():
    import sys as _s
    _s.path.insert(0, str(HERE.parent / "converters" / "python"))
    from bmo_ooxml.container import cbor_encode
    from bmo_reader import append_commit, open_file, read_chunk
    from model_check import cbor_decode, parse_envelope
    envs_of = lambda raw: [parse_envelope(o, read_chunk(raw, fo, len(raw))[1])
                           for o, c, fo, _ in open_file(raw)["entries"] if c == 0x0010]
    lite = lambda e: {"oid": e["oid"], "kind": e["kind"], "parent": e["parent"],
                      "index": e["index"], "body": e["body"]}
    # clone: deck slide 4 (para 5 + run 6) to fresh oids
    denvs = [lite(e) for e in envs_of((GOLDEN / "deck.bmo").read_bytes())]
    new_envs, mapping = viewer.clone_subtree(denvs, 4, 100)
    assert mapping == {4: 100, 5: 101, 6: 102}, mapping
    assert new_envs[100]["parent"] == 1 and new_envs[101]["parent"] == 100
    assert new_envs[102]["parent"] == 101
    assert new_envs[100]["index"] == 2  # original slot kept; caller re-slots
    try:
        viewer.clone_subtree(denvs, 999, 100)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown root cloned!")
    # move: text.bmo paragraphs leapfrog resources
    tenvs = [lite(e) for e in envs_of((GOLDEN / "text.bmo").read_bytes())]
    moved = viewer.plan_move_sibling(tenvs, 7, -1, cbor_encode, kind="paragraph")
    assert set(moved) == {7, 4}
    d7, _ = cbor_decode(moved[7], 0)
    d4, _ = cbor_decode(moved[4], 0)
    assert (d7["index"], d4["index"]) == (2, 3)
    try:
        viewer.plan_move_sibling(tenvs, 9, +1, cbor_encode, kind="paragraph")
    except ValueError:
        pass
    else:
        raise AssertionError("move past edge accepted!")
    # run styles: exclusive update / shared clone / fresh create
    run = {"kind": "run", "parent": 5, "index": 1, "body": {"text": "x"}}
    st = {"oid": 2, "kind": "style", "parent": 1, "index": 0,
          "body": {"name": "T", "run": {"size": 24}}}
    rp, upd, new, ref = viewer.plan_run_style(run, st, 1, True, False, 1, 5, 50, cbor_encode)
    assert set(upd) == {2} and new == {} and ref == 2
    ds, _ = cbor_decode(upd[2], 0)
    assert ds["body"]["run"] == {"size": 24, "b": 1}, ds["body"]
    rp2, upd2, new2, ref2 = viewer.plan_run_style(run, st, 5, False, True, 1, 5, 50, cbor_encode)
    assert upd2 == {} and set(new2) == {50} and ref2 == 50
    ds2, _ = cbor_decode(new2[50], 0)
    assert ds2["body"]["run"] == {"size": 24, "i": 1} and ds2["parent"] == 1
    rp3, upd3, new3, ref3 = viewer.plan_run_style(run, None, 0, True, True, 1, 5, 50, cbor_encode)
    assert ref3 == 50 and set(new3) == {50}
    assert viewer.count_refs(tenvs, 2) == 2  # p4 + run6 reference style 2
    # end-to-end: duplicate slide 7 in deck.bmo bytes, reopen, verify
    draw = (GOLDEN / "deck.bmo").read_bytes()
    denvs2 = [lite(e) for e in envs_of(draw)]
    fresh = max(e["oid"] for e in denvs2) + 1
    clones, mapping2 = viewer.clone_subtree(denvs2, 7, fresh)
    slide_new = mapping2[7]
    clones[slide_new]["index"] = max(e["index"] for e in denvs2 if e["parent"] == 1) + 1
    new_payloads = {o: cbor_encode(e) for o, e in clones.items()}
    v2 = append_commit(draw, {}, new_payloads)
    f2 = open_file(v2)
    assert f2["footer"]["txn"] == 2
    kinds = [e for e in denvs2]  # original untouched
    assert len([o for o, _, _, _ in f2["entries"]]) == len(denvs2) + len(clones)

def t_editor_manage_roundtrips():
    import sys as _s
    _s.path.insert(0, str(HERE.parent / "converters" / "python"))
    from bmo_ooxml.container import cbor_encode
    from bmo_reader import append_commit, open_file, read_chunk
    from model_check import cbor_decode, parse_envelope, validate_structure
    from domains_check import validate_bodies

    def load_all(raw):
        f = open_file(raw)
        got = [(o, read_chunk(raw, fo, len(raw))[1]) for o, c, fo, _ in f["entries"] if c == 0x0010]
        envs = [parse_envelope(o, p) for o, p in got]
        return f, got, envs

    def check(raw):
        f, got, envs = load_all(raw)
        ids = {o for o, _ in got}
        validate_structure(got, ids, [])
        validate_bodies([parse_envelope(o, p) for o, p in got])
        return f, envs

    lite = lambda e: {"oid": e["oid"], "kind": e["kind"], "parent": e["parent"],
                      "index": e["index"], "body": e["body"]}
    # --- slides on deck.bmo ---
    draw = (GOLDEN / "deck.bmo").read_bytes()
    _, denvs = load_all(draw)[0], [lite(e) for e in load_all(draw)[2]]
    fresh = max(e["oid"] for e in denvs) + 1
    v = append_commit(draw, {}, {fresh: cbor_encode(
        {"kind": "slide", "parent": 1, "index": 4, "body": {}})})
    f, envs = check(v)
    assert f["footer"]["txn"] == 2 and len([e for e in envs if e["kind"] == "slide"]) == 3
    # duplicate slide 4 (para 5 + run 6)
    clones, mapping = viewer.clone_subtree([lite(e) for e in envs], 4, fresh + 10)
    clones[mapping[4]]["index"] = 5
    v = append_commit(v, {}, {o: cbor_encode(e) for o, e in clones.items()})
    f, envs = check(v)
    assert f["footer"]["txn"] == 3
    assert len([e for e in envs if e["kind"] == "slide"]) == 4
    assert len([e for e in envs if e.get("parent") == mapping[4]]) >= 1
    # delete original slide 7 (shape 8) + cascade, compacting the gap
    lites = [lite(e) for e in envs]
    doomed = sorted(viewer.descendants(lites, 7))
    assert set(doomed) == {7, 8}
    assert viewer.find_referrers(lites, doomed) - set(doomed) == set()
    bumped = viewer.plan_compact_siblings(lites, 1, doomed, cbor_encode)
    assert set(bumped) == {9, 19}, set(bumped)  # slides re-densified 4->3, 5->4
    v = append_commit(v, bumped, {}, doomed)
    f, envs = check(v)
    assert f["footer"]["txn"] == 4
    assert {e["oid"] for e in envs} & {7, 8} == set()
    assert len([e for e in envs if e["kind"] == "slide"]) == 3
    # --- sheets on sheet.bmo ---
    sraw = (GOLDEN / "sheet.bmo").read_bytes()
    _, senvs = load_all(sraw)[0], [lite(e) for e in load_all(sraw)[2]]
    wb = next(e for e in senvs if e["kind"] == "workbook")
    fresh = max(e["oid"] for e in senvs) + 1
    widx = max(e["index"] for e in senvs if e["parent"] == wb["oid"]) + 1
    v = append_commit(sraw, {}, {fresh: cbor_encode(
        {"kind": "sheet", "parent": wb["oid"], "index": widx,
         "body": {"name": "New", "cells": {"A1": {"v": 1}}}})})
    f, envs = check(v)
    assert any(e["kind"] == "sheet" and e["body"]["name"] == "New" for e in envs)
    sh3 = next(e for e in envs if e["body"].get("name") == "Summary")
    ren = viewer.env_for_write(next(e for e in senvs if e["oid"] == sh3["oid"]))
    ren["body"] = dict(ren["body"])
    ren["body"]["name"] = "Renamed"
    v = append_commit(v, {sh3["oid"]: cbor_encode(ren)})
    f, envs = check(v)
    assert any(e["body"].get("name") == "Renamed" for e in envs)
    assert not any(e["body"].get("name") == "Summary" for e in envs)
    # delete the MIDDLE sheet (Summary->Renamed was idx 0): gap must compact
    gone = next(e for e in envs if e["body"].get("name") == "Renamed")
    lites2 = [{"oid": e["oid"], "kind": e["kind"], "parent": e["parent"],
               "index": e["index"], "body": e["body"]} for e in envs]
    bumped2 = viewer.plan_compact_siblings(lites2, wb["oid"], [gone["oid"]], cbor_encode)
    v = append_commit(v, bumped2, {}, [gone["oid"]])
    f, envs = check(v)
    assert not any(e["body"].get("name") == "Renamed" for e in envs)
    got_idx = sorted(e["index"] for e in envs if e["parent"] == wb["oid"])
    assert got_idx == list(range(len(got_idx))), got_idx
    # --- move + styled edit on text.bmo ---
    traw = (GOLDEN / "text.bmo").read_bytes()
    _, tenvs = load_all(traw)[0], [lite(e) for e in load_all(traw)[2]]
    upd = viewer.plan_move_sibling(tenvs, 9, -1, cbor_encode, kind="paragraph")
    v = append_commit(traw, upd)
    f, envs = check(v)
    got = {e["oid"]: e["index"] for e in envs if e["kind"] == "paragraph"}
    assert got[9] == 3 and got[7] == 4, got

def t_tk_smoke():
    try:
        import tkinter as tk
    except ImportError:
        print("  SKIP tk_smoke (no tkinter)")
        return
    try:
        r = tk.Tk()
        r.withdraw()
        r.update()
        r.destroy()
    except Exception as e:
        print(f"  SKIP tk_smoke ({type(e).__name__}: no display)")
        return

def t_plan_edit_flows():
    import sys as _s
    _s.path.insert(0, str(HERE.parent / "converters" / "python"))
    _s.path.insert(0, str(HERE.parent / "calc" / "python"))
    from bmo_ooxml.container import cbor_encode
    from bmo_ooxml.common import sniff_script
    from bmo_ooxml.xlsx import extract_refs
    from bmo_reader import append_commit, open_file
    from model_check import cbor_decode, parse_envelope
    # paragraph edit: first-run style kept, extras removed
    runs = [(5, {"kind": "run", "parent": 4, "index": 0,
                 "body": {"text": "old", "style": {"$ref": 2}}}),
            (6, {"kind": "run", "parent": 4, "index": 1, "body": {"text": "drop" }})]
    upd, new, delete = viewer.plan_paragraph_edit(4, runs, "hello عربي", 99,
                                                  cbor_encode, sniff_script)
    assert set(upd) == {5} and new == {} and delete == [6], (upd, new, delete)
    from model_check import cbor_decode
    dec, n = cbor_decode(upd[5], 0)
    assert n == len(upd[5]) and dec["body"]["text"] == "hello عربي"
    assert dec["body"]["script"] == "Arab" and dec["body"]["style"] == {"$ref": 2}
    # empty paragraph gains a run
    upd2, new2, delete2 = viewer.plan_paragraph_edit(4, [], "hi", 99, cbor_encode, sniff_script)
    assert upd2 == {} and delete2 == [] and set(new2) == {99}
    # delete plan + referrer guard
    assert viewer.plan_paragraph_delete(4, [5, 6]) == [4, 5, 6]
    envs = [{"oid": 4, "kind": "paragraph", "body": {}},
            {"oid": 7, "kind": "annotation", "body": {"target": {"$ref": 4}}}]
    assert viewer.find_referrers(envs, {4}) == {7}
    assert viewer.find_referrers(envs, {9}) == set()
    # sheet cell set + clear
    body = viewer.plan_sheet_cell({"name": "S", "cells": {}}, "b2", 3, "A1+1", extract_refs)
    assert body["cells"]["B2"] == {"v": 3, "f": "A1+1", "deps": ["A1"]}, body["cells"]
    cleared = viewer.plan_sheet_cell({"name": "S", "cells": {"B2": {"v": 3}}}, "b2", None, None, extract_refs)
    assert cleared["cells"] == {}
    try:
        viewer.plan_sheet_cell({"name": "S", "cells": {}}, "1A", 1, None, extract_refs)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed address accepted!")
    # out-of-bounds shape is a planner pass but a save-time refusal (domains):
    wide = viewer.plan_sheet_cell({"name": "S", "cells": {}}, "ZZZ9", 1, None, extract_refs)
    assert wide["cells"] == {"ZZZ9": {"v": 1}}
    # slide body surgery
    assert viewer.plan_slide_body({"notes": "x"}, "", None, None) == {}
    assert viewer.plan_slide_body({}, "n", "fade", 300) == {"notes": "n", "transition": {"type": "fade", "durMs": 300}}
    assert viewer.plan_slide_body({"transition": {"type": "x"}}, None, "none", None) == {}
    # end-to-end: edit text.bmo paragraph #4 run #5 in memory, reopen, verify
    raw = (GOLDEN / "text.bmo").read_bytes()
    f = open_file(raw)
    from bmo_reader import read_chunk
    pay = {o: read_chunk(raw, fo, len(raw))[1] for o, c, fo, _ in f["entries"] if c == 0x0010}
    renv = [(o, parse_envelope(o, p)) for o, p in pay.items() if o in (5, 6)]
    run_envs = [(o, {"kind": e["kind"], "parent": e["parent"], "index": e["index"], "body": e["body"]}) for o, e in renv]
    upd3, new3, delete3 = viewer.plan_paragraph_edit(4, run_envs, "Hi BMO", 999, cbor_encode, sniff_script)
    v2 = append_commit(raw, upd3, new3, delete3)
    f2 = open_file(v2)
    assert f2["footer"]["txn"] == 2
    assert sorted(o for o, _, _, _ in f2["entries"] if o in (5, 6)) == [5]
    dec2, _ = cbor_decode(read_chunk(v2, dict((o, fo) for o, _, fo, _ in f2["entries"])[5], len(v2))[1], 0)
    assert dec2["body"]["text"] == "Hi BMO"

if __name__ == "__main__":
    print("viewer conformance:")
    for name, fn in [("flow_order", t_flow_order), ("table_grid", t_table_grid),
                     ("sheet_grid", t_sheet_grid), ("deck_order", t_deck_order),
                     ("styles_markers", t_styles_markers),
                     ("load_errors", t_load_errors), ("tk_smoke", t_tk_smoke),
                     ("editor_manage_roundtrips", t_editor_manage_roundtrips),
                     ("plan_manage_flows", t_plan_manage_flows),
                     ("plan_edit_flows", t_plan_edit_flows)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
