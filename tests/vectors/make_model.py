"""Build deterministic tests/golden/model.bmo (RFC-0002 vector).
New-envelope objects (kind/parent/index/role/rev/body) + KIND_INDEX.
Fixed file_id ..02, created 0, txn 1.
"""
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "conformance"))
from make_hello import cbor_encode, crc32c, make_chunk
from model_check import build_kind_index

F_DOCUMENT, F_DIR, F_ROOT = 0x0001, 0x0002, 0x0003
F_INDEX, F_GENERIC = 0x0006, 0x0010

def env(kind, parent, index, role, rev, body):
    return cbor_encode({"kind": kind, "parent": parent, "index": index,
                        "role": role, "rev": rev, "body": body})

def main():
    out_dir = HERE.parent / "golden"
    file_id = bytes(15) + b"\x02"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": ["model.envelope-v1", "model.kind-index"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Model Demo"},
    })
    o1 = env("document", 0, 0, "", 1, {"title": "Model Demo"})
    o2 = env("page", 1, 0, "", 1, {"number": 1})
    o3 = env("note", 2, 0, "", 1, {"lang": "en", "text": "Hello model"})
    o4 = env("annotation", 1, 1, "review", 2,
             {"text": "second pair of eyes", "target": {"$ref": 3}})

    blob = bytearray(header)
    offsets = {}
    def put(ctype, flags, oid, payload, name):
        offsets[name] = len(blob)
        blob.extend(make_chunk(ctype, 1, flags, oid, payload))

    put(F_DOCUMENT, 0x0008, 0, doc, "doc")
    put(F_GENERIC, 0x000C, 1, o1, "o1")
    put(F_GENERIC, 0x000C, 2, o2, "o2")
    put(F_GENERIC, 0x000C, 3, o3, "o3")
    put(F_GENERIC, 0x000C, 4, o4, "o4")

    kinds = {"annotation": [4], "document": [1], "note": [3], "page": [2]}
    kidx = build_kind_index(kinds)
    put(F_INDEX, 0x0000, 0, kidx, "kindidx")
    idx_off, idx_total = offsets["kindidx"], 40 + len(kidx) + 4

    import hashlib
    entries = [(1, F_GENERIC), (2, F_GENERIC), (3, F_GENERIC), (4, F_GENERIC)]
    dpay = struct.pack("<IIQ", 1, 4, 1)
    for oid, ct in entries:
        name = {1: "o1", 2: "o2", 3: "o3", 4: "o4"}[oid]
        # total len lookup: recompute from known payloads
        pay = {1: o1, 2: o2, 3: o3, 4: o4}[oid]
        dpay += struct.pack("<QIIQQ", oid, ct, 0, offsets[name], 40 + len(pay) + 4)
    dir_off = len(blob)
    dir_chunk = make_chunk(F_DIR, 1, 0x0008, 0, dpay)
    blob.extend(dir_chunk)

    rpay = struct.pack("<IIQQ", 1, 1, dir_off, len(dir_chunk))
    rpay += struct.pack("<IIQQ", 1, 0, idx_off, idx_total)
    root_off = len(blob)
    root_chunk = make_chunk(F_ROOT, 1, 0x0008, 0, rpay)
    blob.extend(root_chunk)

    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(root_chunk), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "model.bmo").write_bytes(bytes(blob))
    manifest = {"file": "model.bmo", "size": len(blob),
                "file_id": file_id.hex(),
                "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
                "offsets": offsets, "dir_offset": dir_off, "root_offset": root_off,
                "objects": 4, "kinds": kinds}
    (out_dir / "model.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote model.bmo ({len(blob)} bytes) sha256={manifest['sha256_file']}")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    sys.exit(main())
