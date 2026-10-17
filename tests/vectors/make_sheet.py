"""Build deterministic tests/golden/sheet.bmo (RFC-0005 vector)."""
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
    file_id = bytes(15) + b"\x05"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "spreadsheet", "docId": file_id, "created": 0, "modified": 0,
        "features": ["model.envelope-v1", "sheet.v1"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Sheet Demo"},
    })
    objs = [
        (1, env("workbook", 0, 0, {"calc": {"mode": "auto"}})),
        (2, env("numfmt", 1, 2, {"code": "#,##0.00"})),
        (3, env("sheet", 1, 0, {"name": "Summary", "cells": {
            "A1": {"v": 42},
            "B2": {"v": "Revenue", "s": {"$ref": 2}},
            "C3": {"v": 3.14},
            "D4": {"v": None, "f": "A1*2", "deps": ["A1"]},
            "E5": {"v": True},
            "F6": {"err": "#DIV/0!"},
        }, "merges": [[0, 0, 0, 1]]})),
        (4, env("sheet", 1, 1, {"name": "Q1", "cells": {
            "A1": {"v": 2.5, "f": "SUM(A2:A10)", "deps": ["A2", "A10"]},
        }})),
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

    (out_dir / "sheet.bmo").write_bytes(bytes(blob))
    man = {"file": "sheet.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "objects": len(objs), "dir_offset": dir_off, "root_offset": root_off}
    (out_dir / "sheet.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote sheet.bmo ({len(blob)} bytes)")

if __name__ == "__main__":
    sys.exit(main())
