"""Executable conformance checks (no Rust toolchain needed here).
Mirrors core/rust/tests/*.rs so `cargo test` elsewhere asserts the same.
Run: python tests/conformance/test_container.py
"""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bmo_reader import (BmoErr, append_commit, crc32c, make_chunk, open_file)

GOLDEN = HERE.parent / "golden" / "hello.bmo"
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

def t_hello_opens():
    raw = GOLDEN.read_bytes()
    assert len(raw) == 829, f"size {len(raw)}"
    f = open_file(raw)
    assert [o for o, _, _, _ in f["entries"]] == [1, 2, 3]
    assert f["footer"]["txn"] == 1

def t_tamper_rejected():
    raw = bytearray(GOLDEN.read_bytes())
    raw[489] ^= 0xFF
    try:
        open_file(bytes(raw))
    except BmoErr as e:
        assert str(e) in ("PAYLOAD_CRC_MISMATCH", "CORRUPT", "CHUNK_HEADER_CRC_MISMATCH"), str(e)
        return
    raise AssertionError("tampered file opened!")

def t_bad_magic():
    raw = bytearray(GOLDEN.read_bytes())
    raw[0] = 0x58
    try:
        open_file(bytes(raw))
    except BmoErr as e:
        assert str(e) == "BAD_MAGIC", str(e)
        return
    raise AssertionError("bad magic opened!")

def t_cow_delete():
    raw = GOLDEN.read_bytes()
    v2 = append_commit(raw, {}, None, [3])
    f2 = open_file(v2)
    assert f2["footer"]["txn"] == 2
    assert [o for o, _, _, _ in f2["entries"]] == [1, 2]
    assert len(v2) > len(raw)  # history bytes stay, index moves on

def t_cow_append():
    raw = GOLDEN.read_bytes()
    # new payload for object 3 (content irrelevant to container layer)
    new3 = raw[449 + 40:449 + 40 + 48][:-1] + b"\x00"
    v2 = append_commit(raw, {3: new3})
    assert len(v2) > len(raw)
    assert v2[:len(raw) - 64] == raw[:len(raw) - 64], "prefix must be intact"
    f2 = open_file(v2)
    assert f2["footer"]["txn"] == 2
    # old footer still present mid-file
    assert v2.count(b"BMOF") >= 4  # 2 footers x 2 magics

def t_unknown_ancillary_preserved():
    from bmo_reader import HEADER_LEN, FOOTER_LEN
    raw = GOLDEN.read_bytes()
    alien = make_chunk(0x8001, 1, 0x0000, 0, b"future-feature")
    # rebuild genesis with the alien spliced after the objects, then append:
    # append_commit only appends, so the alien must survive byte-exact.
    f0 = open_file(raw)
    import bmo_reader as R
    # collect original object payloads via directory
    payloads = {}
    for oid, ct, fo, tl in f0["entries"]:
        payloads[oid] = R.read_chunk(raw, fo, len(raw))[1]
    body = raw[:64]
    body += make_chunk(0x0001, 1, 0x0008, 0, R.read_chunk(raw, 64, len(raw))[1])
    for oid in (1, 2, 3):
        body += make_chunk(0x0010, 1, 0x000C, oid, payloads[oid])
    body += alien
    dpay = struct.pack("<IIQ", 1, 3, 1)
    # recompute offsets by walking body
    chunks = []
    o = 64
    while o < len(body):
        h = R.parse_chunk_hdr(body[o:o + 40])
        chunks.append((h, o))
        o += 40 + h["slen"] + 4
    # entries for oid != 0 with corrected offsets
    entries = [(h["oid"], h["type"], base, 40 + h["slen"] + 4)
               for h, base in chunks if h["oid"] != 0]
    dpay = struct.pack("<IIQ", 1, len(entries), 1)
    for oid, ct, fo, tl in sorted(entries):
        dpay += struct.pack("<QIIQQ", oid, ct, 0, fo, tl)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    doff2 = len(body)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, doff2, len(dc)))
    roff2 = doff2 + len(dc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, roff2, len(rc), 1, 0)
    f1 = body + dc + rc + ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF"
    open_file(f1)  # must open with alien aboard
    new3 = payloads[3][:-1] + b"\x00"
    v2 = append_commit(f1, {3: new3})
    assert alien in v2, "alien chunk must survive append byte-exact"
    open_file(v2)

def t_unknown_critical_refused():
    raw = GOLDEN.read_bytes()
    evil = make_chunk(0x0FFF, 1, 0x0008, 0, b"nope")
    split = len(raw) - 64
    tampered = raw[:split] + evil + raw[split:]
    try:
        open_file(tampered)
    except BmoErr as e:
        assert str(e) == "UNKNOWN_CRITICAL", str(e)
        return
    raise AssertionError("unknown critical opened!")

def t_footer_recovery():
    raw = GOLDEN.read_bytes() + b"\x00" * 17  # torn tail: EOF footer invalid
    f = open_file(raw)  # backward scan must find the intact tip footer
    assert f["footer"]["txn"] == 1  # found via backward scan

if __name__ == "__main__":
    print("conformance (python mirror of cargo tests):")
    for name, fn in [("hello_opens", t_hello_opens), ("tamper_rejected", t_tamper_rejected),
                     ("bad_magic", t_bad_magic), ("cow_append", t_cow_append),
                     ("cow_delete", t_cow_delete),
                     ("unknown_ancillary", t_unknown_ancillary_preserved),
                     ("unknown_critical", t_unknown_critical_refused),
                     ("footer_recovery", t_footer_recovery)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
