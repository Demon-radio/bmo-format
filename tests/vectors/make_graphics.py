"""Build deterministic tests/golden/graphics.bmo (RFC-0004 vector).
Exercises real content addressing: image -> $asset -> ASSET_TABLE -> ASSET_DATA.
"""
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
    file_id = bytes(15) + b"\x04"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    asset = b"BMO-TEST-IMAGE:" + bytes((i * 7) & 0xFF for i in range(64))
    sha = hashlib.sha256(asset).digest()
    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "document", "docId": file_id, "created": 0, "modified": 0,
        "features": ["model.envelope-v1", "graphics.v1"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Graphics Demo"},
    })
    objs = [
        (1, env("document", 0, 0, {"title": "Graphics Demo"})),
        (2, env("canvas", 1, 0, {"w": 210000, "h": 297000})),
        (3, env("shape", 2, 0, {"shape": "rect",
             "geom": {"x": 10000, "y": 10000, "w": 50000, "h": 30000},
             "fill": {"srgb": b"\xff\x00\x00"},
             "line": {"w": 500, "color": {"srgb": b"\x00\x00\x00"}}})),
        (4, env("image", 2, 1, {"asset": {"$asset": sha}, "mime": "image/png",
                                "w": 64000, "h": 48000})),
        (5, env("group", 2, 2, {})),
        (6, env("shape", 5, 0, {"shape": "ellipse",
             "geom": {"cx": 40000, "cy": 40000, "rx": 15000, "ry": 10000},
             "fill": "none"})),
    ]
    table = cbor_encode({sha: {"size": len(asset), "mime": "image/png", "chunks": [100]}})

    blob = bytearray(header)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    offs = {}
    for oid, pay in objs:
        offs[oid] = len(blob)
        blob.extend(make_chunk(0x0010, 1, 0x000C, oid, pay))
    offs["table"] = len(blob)
    table_chunk = make_chunk(0x0004, 1, 0x0008, 0, table)
    blob.extend(table_chunk)
    offs[100] = len(blob)
    data_chunk = make_chunk(0x0005, 1, 0x0004, 100, asset)
    blob.extend(data_chunk)

    # Directory: model objects + the ASSET_DATA slice (oid 100).
    # ASSET_TABLE has oid 0 -> found via region scan, never indexed.
    dpay = struct.pack("<IIQ", 1, len(objs) + 1, 1)
    for oid, pay in objs:
        dpay += struct.pack("<QIIQQ", oid, 0x0010, 0, offs[oid], 40 + len(pay) + 4)
    dpay += struct.pack("<QIIQQ", 100, 0x0005, 0, offs[100], len(data_chunk))
    # NOTE: ASSET_TABLE (oid 0) is found via scan, not the directory.
    dir_off = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "graphics.bmo").write_bytes(bytes(blob))
    man = {"file": "graphics.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "asset_sha256": sha.hex(), "objects": len(objs),
           "dir_offset": dir_off, "root_offset": root_off}
    (out_dir / "graphics.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote graphics.bmo ({len(blob)} bytes) asset={sha.hex()[:16]}...")

if __name__ == "__main__":
    sys.exit(main())
