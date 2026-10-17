"""RFC-0002 conformance (executable here; mirrors core/rust/tests/model.rs).
Run: python tests/conformance/test_model.py
"""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vectors"))
from bmo_reader import open_file, read_chunk
from make_hello import cbor_encode
from model_check import (ModelErr, parse_kind_index, table_shas,
                         validate_structure)

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

def generic_payloads(raw):
    f = open_file(raw)
    out, tables, ids = [], [], set()
    for oid, ct, fo, tl in f["entries"]:
        ids.add(oid)
        if ct == 0x0010:
            out.append((oid, read_chunk(raw, fo, len(raw))[1]))
    # collect asset tables by region scan
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
    return out, ids, tables

def t_model_golden():
    raw = (GOLDEN / "model.bmo").read_bytes()
    assert len(raw) == 1250, len(raw)
    objs, ids, tables = generic_payloads(raw)
    rep = validate_structure(objs, ids, tables)
    assert rep["root"] == 1
    assert rep["kinds"] == {"annotation": [4], "document": [1], "note": [3], "page": [2]}, rep["kinds"]
    # KIND_INDEX cross-check: find index chunk, compare groups
    f = open_file(raw)
    from bmo_reader import parse_chunk_hdr
    rh, rpay, _ = read_chunk(raw, f["footer"]["roff"], len(raw))
    (rv, m) = struct.unpack_from("<II", rpay, 0)
    assert (rv, m) == (1, 1)
    itype, _, ioff, ilen = struct.unpack_from("<IIQQ", rpay, 24)
    assert itype == 1
    ih, ipay, _ = read_chunk(raw, ioff, len(raw))
    assert parse_kind_index(ipay) == rep["kinds"]

def t_hello_backward_compat():
    raw = (GOLDEN / "hello.bmo").read_bytes()
    objs, ids, tables = generic_payloads(raw)
    rep = validate_structure(objs, ids, tables)  # defaults: index by oid, rev 0
    assert rep["root"] == 1 and rep["kinds"]["text"] == [3]

def E(**kw):
    d = {"kind": "note", "parent": 1}
    d.update(kw)
    return cbor_encode(d)

def t_two_roots_rejected():
    objs = [(1, E(parent=0)), (2, E(parent=0))]
    try:
        validate_structure(objs, {1, 2}, [])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("two roots accepted!")

def t_cycle_rejected():
    objs = [(1, E(parent=0)), (2, E(parent=3)), (3, E(parent=2))]
    try:
        validate_structure(objs, {1, 2, 3}, [])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("cycle accepted!")

def t_dangling_parent_rejected():
    objs = [(1, E(parent=0)), (2, E(parent=99))]
    try:
        validate_structure(objs, {1, 2, 99} - {99}, [])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("dangling parent accepted!")

def t_dangling_ref_rejected():
    objs = [(1, E(parent=0, body={"target": {"$ref": 77}}))]
    try:
        validate_structure(objs, {1}, [])
    except ModelErr as e:
        assert str(e) == "DANGLING_REF", str(e)
        return
    raise AssertionError("dangling $ref accepted!")

def t_mixed_index_rejected():
    objs = [(1, E(parent=0)), (2, E(parent=1, index=0)), (3, E(parent=1))]
    try:
        validate_structure(objs, {1, 2, 3}, [])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("mixed index accepted!")

def t_sparse_index_rejected():
    objs = [(1, E(parent=0)), (2, E(parent=1, index=0)), (3, E(parent=1, index=5))]
    try:
        validate_structure(objs, {1, 2, 3}, [])
    except ModelErr as e:
        assert str(e) == "STRUCTURE_INVALID", str(e)
        return
    raise AssertionError("sparse index accepted!")

if __name__ == "__main__":
    print("model conformance (python mirror of cargo model tests):")
    for name, fn in [("model_golden", t_model_golden),
                     ("hello_backward_compat", t_hello_backward_compat),
                     ("two_roots", t_two_roots_rejected),
                     ("cycle", t_cycle_rejected),
                     ("dangling_parent", t_dangling_parent_rejected),
                     ("dangling_ref", t_dangling_ref_rejected),
                     ("mixed_index", t_mixed_index_rejected),
                     ("sparse_index", t_sparse_index_rejected)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
