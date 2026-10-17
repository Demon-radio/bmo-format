"""Build deterministic tests/golden/password.bmo (RFC-0007 vector).
One AES-256-GCM chunk, SECURITY keyWrap method=password-argon2id:
Argon2id KEK (fixed salt/params) -> RFC 3394 AES-KW wrap of the content key.
Fixed password so the vector is reproducible.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "conformance"))
from make_hello import cbor_encode, crc32c  # noqa: E402
from bmo_reader import aes_kw_wrap, derive_kek, make_chunk  # noqa: E402

PASSWORD = b"bmo-test-password"
SALT = bytes(range(16))
PARAMS = {"salt": SALT, "time": 3, "mem": 65536, "par": 1}
KEY = hashlib.sha256(b"bmo-test-password-key-v1").digest()
NONCE = bytes(range(12))

def main():
    out_dir = HERE.parent / "golden"
    file_id = bytes(15) + b"\x08"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    header = hdr + struct.pack("<I", crc32c(hdr + tail)) + tail

    kek = derive_kek(PASSWORD, PARAMS)
    wrapped = aes_kw_wrap(kek, KEY)
    assert len(wrapped) == 40

    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": ["encryption.aes-256-gcm", "keywrap.password-argon2id"],
        "requiredExtensions": [], "optionalExtensions": [],
        "metadata": {"title": "Password Demo"},
    })
    sec = cbor_encode({
        "encrypted": True, "contentAlg": "AES-256-GCM",
        "keyWrap": {"method": "password-argon2id", "params": PARAMS,
                    "wrappedKey": wrapped},
    })
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "password secret"}})

    blob = bytearray(header)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    sec_off = len(blob)
    blob.extend(make_chunk(0x0007, 1, 0x0008, 0, sec))
    obj_off = len(blob)
    enc_chunk = make_chunk(0x0010, 1, 0x000C, 1, plain,
                           enc=1, key=KEY, nonce=NONCE)
    blob.extend(enc_chunk)

    dpay = struct.pack("<IIQ", 1, 1, 1) + struct.pack("<QIIQQ", 1, 0x0010, 0,
                                                      obj_off, len(enc_chunk))
    dir_off = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")

    (out_dir / "password.bmo").write_bytes(bytes(blob))
    man = {"file": "password.bmo", "size": len(blob), "file_id": file_id.hex(),
           "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
           "alg": "AES-256-GCM", "method": "password-argon2id",
           "password": PASSWORD.decode(), "salt": SALT.hex(),
           "params": {"time": PARAMS["time"], "mem": PARAMS["mem"],
                      "par": PARAMS["par"]},
           "kek_sha256": hashlib.sha256(kek).hexdigest(),
           "wrappedKey": wrapped.hex(), "nonce": NONCE.hex(),
           "key_sha256": hashlib.sha256(KEY).hexdigest(),
           "plain_sha256": hashlib.sha256(plain).hexdigest(),
           "obj_offset": obj_off, "sec_offset": sec_off}
    (out_dir / "password.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"wrote password.bmo ({len(blob)} bytes)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
