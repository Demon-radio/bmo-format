"""Verify tests/golden/hello.bmo against RFC-0001.
Recomputes every CRC, checks header/footer magic, directory sort,
docId match, offsets chain. Exit 0 on success, 1 on failure.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

_POLY = 0x82F63B78
_TABLE = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ _POLY if c & 1 else c >> 1
    _TABLE.append(c & 0xFFFFFFFF)

def crc32c(data: bytes, crc: int = 0xFFFFFFFF) -> int:
    for b in data:
        crc = _TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF

def fail(msg):
    print(f"FAIL: {msg}")
    raise SystemExit(1)

def main():
    golden = Path(__file__).resolve().parents[1] / "golden"
    raw = (golden / "hello.bmo").read_bytes()
    manifest = json.loads((golden / "hello.json").read_text(encoding="utf-8"))
    print(f"file size: {len(raw)} bytes (manifest {manifest['size']})")
    if len(raw) != manifest["size"]:
        fail("size mismatch vs manifest")
    if len(raw) < 128:
        fail("too small")

    # Header
    magic, major, minor, hlen, hver, flags = struct.unpack_from("<4sHHHHI", raw, 0)
    file_id = raw[16:32]
    (hcrc,) = struct.unpack_from("<I", raw, 32)
    if magic != b"BMO\x1a":
        fail(f"bad magic {magic!r}")
    if (major, minor) != (0, 1):
        fail(f"bad version {major}.{minor}")
    if (hlen, hver) != (64, 1):
        fail("bad header len/ver")
    if crc32c(raw[0:32] + raw[36:64]) != hcrc:
        fail("header CRC mismatch")
    if file_id.hex() != manifest["file_id"]:
        fail("file_id mismatch vs manifest")
    print(f"header OK: BMO {major}.{minor} file_id={file_id.hex()} crc={hcrc:#010x}")

    # Footer
    foff = len(raw) - 64
    m1, fver, fflags, roff, rlen, txn, prev = struct.unpack_from("<4sHHQQQQ", raw, foff)
    (fcrc,) = struct.unpack_from("<I", raw, foff + 40)
    m2 = raw[foff + 60:foff + 64]
    if m1 != b"BMOF" or m2 != b"BMOF":
        fail("bad footer magic")
    if fver != 1 or txn != 1 or prev != 0:
        fail(f"bad footer fields v={fver} txn={txn} prev={prev}")
    if crc32c(raw[foff:foff + 40]) != fcrc:
        fail("footer CRC mismatch")
    if not (64 <= roff < len(raw)) or roff + rlen > len(raw):
        fail("root pointer out of bounds")
    print(f"footer OK: txn={txn} root@{roff} len={rlen} crc={fcrc:#010x}")

    # Walk chunks from offset 64 to footer
    off = 64
    seen = []
    while off < foff:
        if off + 40 > foff:
            fail(f"truncated chunk header at {off}")
        ctype, cver, cflags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", raw, off)
        (hc,) = struct.unpack_from("<I", raw, off + 36)
        if crc32c(raw[off:off + 36]) != hc:
            fail(f"chunk header CRC mismatch at {off} type={ctype:#x}")
        if slen > 2**32 - 1 or dlen > 2**32 - 1:
            fail("limit exceeded")
        pend = off + 40 + slen
        if pend + 4 > foff:
            fail(f"chunk payload overruns at {off}")
        payload = raw[off + 40:pend]
        (pc,) = struct.unpack_from("<I", raw, pend)
        if crc32c(payload) != pc:
            fail(f"payload CRC mismatch at {off}")
        if codec != 0:
            fail(f"hello vector must be plain codec=0 at {off}")
        if slen != len(payload) or dlen != len(payload):
            fail("len mismatch (plain chunk)")
        seen.append((ctype, oid, off, 40 + slen + 4))
        off = pend + 4
    if off != foff:
        fail(f"gap before footer: {off} != {foff}")
    print(f"chunks OK: {len(seen)} chunks")

    # Expect 6 chunks: DOC, OBJx3, DIR, ROOT
    if len(seen) != 6:
        fail(f"expected 6 chunks, got {len(seen)}")
    types = [t for t, _, _, _ in seen]
    if types != [0x0001, 0x0010, 0x0010, 0x0010, 0x0002, 0x0003]:
        fail(f"unexpected chunk order {list(map(hex, types))}")

    # Directory checks
    dtype, _, doff, dtot = seen[4]
    dhdr = raw[doff:doff + 40]
    _, _, _, _, dslen, _, _ = struct.unpack("<IHHQQQI", dhdr[:36])
    dpay = raw[doff + 40:doff + 40 + dslen]
    ver, cnt, dtxn = struct.unpack_from("<IIQ", dpay, 0)
    if (ver, dtxn) != (1, 1):
        fail("bad directory version/txn")
    if cnt != 3:
        fail(f"expected 3 dir entries, got {cnt}")
    entries = []
    for i in range(cnt):
        oid, ct, _, fo, tl = struct.unpack_from("<QIIQQ", dpay, 16 + 32 * i)
        entries.append((oid, ct, fo, tl))
    oids = [e[0] for e in entries]
    if oids != sorted(oids):
        fail("directory not sorted")
    if len(set(oids)) != len(oids):
        fail("duplicate object_id")
    if oids != [1, 2, 3]:
        fail(f"unexpected object ids {oids}")
    for oid, ct, fo, tl in entries:
        match = [s for s in seen if s[1] == oid]
        if not match or match[0][2] != fo or match[0][3] != tl:
            fail(f"directory entry oid={oid} does not match chunk location")
    print("directory OK: sorted, 3 entries, offsets match")

    # Root checks
    _, _, roff2, rtot = seen[5]
    if roff2 != roff or rtot != rlen:
        fail("root location mismatch vs footer")
    rhdr = raw[roff:roff + 40]
    _, _, _, _, rslen, _, _ = struct.unpack("<IHHQQQI", rhdr[:36])
    rpay = raw[roff + 40:roff + 40 + rslen]
    rv, m, doff2, dlen2 = struct.unpack("<IIQQ", rpay)
    if (rv, m) != (1, 0) or doff2 != doff or dlen2 != dtot:
        fail("root payload mismatch vs directory")
    print("root index OK")

    # Manifest cross-check
    for mc, s in zip(manifest["chunks"], seen):
        if mc["offset"] != s[2] or mc["total_len"] != s[3]:
            fail(f"manifest mismatch for {mc['name']}")
    print(f"manifest OK: sha256(file)={hashlib.sha256(raw).hexdigest()}")
    print("PASS: hello.bmo conforms to RFC-0001 container rules")
    return 0

if __name__ == "__main__":
    sys.exit(main())
