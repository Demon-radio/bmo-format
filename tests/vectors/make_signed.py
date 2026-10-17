"""Build deterministic tests/golden/signed.bmo (RFC-0007 vector).
Ed25519 signature over the RFC-0001 §10 canonical hash. Fixed test key.
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
from ed25519 import secret_to_public, sign

SEED = hashlib.sha256(b"bmo-test-ed25519-v1").digest()
PUB = secret_to_public(SEED)

def main():
    out_dir = HERE.parent / "golden"
    file_id = bytes(15) + b"\x09"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": ["model.envelope-v1", "signed.ed25519"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Signed Demo"},
    })
    note = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                        "body": {"text": "sign me"}})
    # canonical bytes (RFC-0001 §10) over txn 1
    objs = {"1": hashlib.sha256(note).digest(),
            "doc": hashlib.sha256(doc).digest()}
    canon = cbor_encode({"fileId": file_id, "magic": "BMO1",
                         "objects": objs, "txn": 1})
    chash = hashlib.sha256(canon).digest()
    sig = sign(SEED, chash)

    blob = bytearray(header)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    blob.extend(make_chunk(0x0010, 1, 0x000C, 1, note))

    dir_off = len(blob)
    # directory must point at the note chunk: exact offsets
    note_off = 64 + 40 + len(doc) + 4
    dpay = struct.pack("<IIQ", 1, 1, 1) + struct.pack(
        "<QIIQQ", 1, 0x0010, 0, note_off, 40 + len(note) + 4)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)

    sig_payload = cbor_encode({
        "alg": "Ed25519", "canonicalHash": chash, "coveredRootOffset": root_off,
        "coveredTxn": 1, "signature": sig, "signer": "bmo-test", "timestamp": 0,
    })
    blob.extend(make_chunk(0x0008, 1, 0x0000, 0, sig_payload))

    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "signed.bmo").write_bytes(bytes(blob))
    man = {"file": "signed.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "pubkey": PUB.hex(), "signature": sig.hex(),
           "canonicalHash": chash.hex()}
    (out_dir / "signed.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote signed.bmo ({len(blob)} bytes)")

if __name__ == "__main__":
    sys.exit(main())
