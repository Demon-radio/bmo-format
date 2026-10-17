"""Build deterministic tests/golden/encrypted.bmo (RFC-0007 vector).
One ChaCha20-Poly1305 chunk, SECURITY method:none (TEST ONLY per spec).
Fixed key/nonce so the vector is reproducible.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "conformance"))
from make_hello import cbor_encode, crc32c, make_chunk
from chacha20poly1305 import encrypt

KEY = hashlib.sha256(b"bmo-test-content-key-v1").digest()
NONCE = bytes(range(12))

def main():
    out_dir = HERE.parent / "golden"
    file_id = bytes(15) + b"\x07"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": ["encryption.chacha20-poly1305"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Encrypted Demo"},
    })
    sec = cbor_encode({
        "encrypted": True, "contentAlg": "ChaCha20-Poly1305",
        "keyWrap": {"method": "none", "wrappedKey": KEY},
    })
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "secret hello"}})
    ct_tag = encrypt(KEY, NONCE, plain)
    stored = NONCE + ct_tag

    blob = bytearray(header)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    sec_off = len(blob)
    sec_chunk = make_chunk(0x0007, 1, 0x0008, 0, sec)
    blob.extend(sec_chunk)
    obj_off = len(blob)
    # encrypted chunk: hand-framed (flags ENCRYPTED|HAS_OBJECT_ID|CRITICAL)
    chdr = struct.pack("<IHHQQQI", 0x0010, 1, 0x000E, 1, len(stored), len(plain),
                       (2 << 16) | 0)
    blob.extend(chdr + struct.pack("<I", crc32c(chdr)) + stored
                + struct.pack("<I", crc32c(stored)))
    obj_total = 40 + len(stored) + 4

    dpay = struct.pack("<IIQ", 1, 1, 1) + struct.pack("<QIIQQ", 1, 0x0010, 0, obj_off, obj_total)
    dir_off = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "encrypted.bmo").write_bytes(bytes(blob))
    man = {"file": "encrypted.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "alg": "ChaCha20-Poly1305", "key_sha256": hashlib.sha256(KEY).hexdigest(),
           "nonce": NONCE.hex(), "plain_sha256": hashlib.sha256(plain).hexdigest(),
           "obj_offset": obj_off, "sec_offset": sec_off}
    (out_dir / "encrypted.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote encrypted.bmo ({len(blob)} bytes)")

if __name__ == "__main__":
    sys.exit(main())
