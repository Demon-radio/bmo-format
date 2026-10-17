"""Build deterministic tests/golden/table.bmo (RFC-0003 §3.5 vector)."""
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_hello import cbor_encode, crc32c, make_chunk

def env(kind, parent, index, body, **kw):
    d = {"kind": kind, "parent": parent, "index": index, "body": body}
    d.update(kw)
    return cbor_encode(d)

def main():
    out_dir = HERE.parent / "golden"
    file_id = bytes(15) + b"\x08"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "document", "docId": file_id, "created": 0, "modified": 0,
        "features": ["model.envelope-v1", "text.v1"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Table Demo"},
    })
    objs = [
        (1, env("document", 0, 0, {"title": "Table Demo"})),
        (2, env("table", 1, 0, {"cols": 2})),
        (3, env("row", 2, 0, {})),
        (4, env("cell", 3, 0, {"gridSpan": 1})),
        (5, env("paragraph", 4, 0, {})),
        (6, env("run", 5, 0, {"text": "R1C1"})),
        (7, env("cell", 3, 1, {})),
        (8, env("paragraph", 7, 0, {})),
        (9, env("run", 8, 0, {"text": "R1C2"})),
        (10, env("row", 2, 1, {})),
        (11, env("cell", 10, 0, {})),
        (12, env("paragraph", 11, 0, {})),
        (13, env("run", 12, 0, {"text": "R2C1"})),
        (14, env("cell", 10, 1, {})),
        (15, env("paragraph", 14, 0, {})),
        (16, env("run", 15, 0, {"text": "R2C2"})),
    ]
    blob = bytearray(header)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    offs = {}
    for oid, pay in objs:
        offs[oid] = len(blob)
        blob.extend(make_chunk(0x0010, 1, 0x000C, oid, pay))
    dpay = struct.pack("<IIQ", 1, len(objs), 1)
    for oid, pay in objs:
        dpay += struct.pack("<QIIQQ", oid, 0x0010, 0, offs[oid], 40 + len(pay) + 4)
    dir_off = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "table.bmo").write_bytes(bytes(blob))
    man = {"file": "table.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "objects": len(objs), "dir_offset": dir_off, "root_offset": root_off}
    (out_dir / "table.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote table.bmo ({len(blob)} bytes)")

if __name__ == "__main__":
    sys.exit(main())
