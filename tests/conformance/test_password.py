"""RFC-0007 password wrap conformance: Argon2id KEK -> RFC 3394 AES-KW ->
content key, golden vector, profile bounds and fail-closed discipline.
Run: python tests/conformance/test_password.py
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vectors"))
from bmo_reader import (BmoErr, aes_kw_unwrap, aes_kw_wrap, derive_kek,
                        find_security, make_chunk, open_file,
                        open_file_password, read_chunk,
                        unwrap_content_key)  # noqa: E402
from make_hello import cbor_encode, crc32c  # noqa: E402

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

VALID_PARAMS = {"salt": bytes(range(16)), "time": 3, "mem": 8192, "par": 1}

def _expect(err, fn, *args):
    try:
        fn(*args)
    except BmoErr as e:
        assert str(e) == err, f"want {err} got {e}"
        return
    raise AssertionError(f"{fn.__name__}: expected {err}")

def _envelope(key_wrap, encrypted=True):
    return cbor_encode({"encrypted": encrypted, "contentAlg": "AES-256-GCM",
                        "keyWrap": key_wrap})

def _mini_file(sec_payload, extra=None, enc_sec=False):
    """Header + SECURITY chunk(s) only: enough for the walk (no root)."""
    file_id = bytes(15) + b"\x09"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    blob = bytearray(hdr + struct.pack("<I", crc32c(hdr + tail)) + tail)
    if enc_sec:
        blob.extend(make_chunk(0x0007, 1, 0x000A, 0, sec_payload, enc=1,
                               key=bytes(32), nonce=bytes(12)))
    else:
        blob.extend(make_chunk(0x0007, 1, 0x0008, 0, sec_payload))
    if extra is not None:
        blob.extend(make_chunk(0x0007, 1, 0x0008, 0, extra))
    return bytes(blob)

def t_rfc3394_vectors():
    """Official vectors: RFC 3394 sections 4.1 (primitive) and 4.6
    (256-bit KEK + 256-bit key = exactly our profile shape -> 40 B)."""
    from cryptography.hazmat.primitives.keywrap import (aes_key_unwrap as prim_unwrap,
                                                        aes_key_wrap as prim_wrap)
    kek1 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    key1 = bytes.fromhex("00112233445566778899aabbccddeeff")
    w1 = bytes.fromhex("1fa68b0a8112b447aef34bd8fb5a7b829d3e862371d2cfe5")
    assert prim_wrap(kek1, key1) == w1
    assert prim_unwrap(kek1, w1) == key1
    kek6 = bytes.fromhex("000102030405060708090a0b0c0d0e0f"
                         "101112131415161718191a1b1c1d1e1f")
    key6 = bytes.fromhex("00112233445566778899aabbccddeeff"
                         "000102030405060708090a0b0c0d0e0f")
    w6 = bytes.fromhex("28c9f404c4b810f4cbccb35cfb87f8263f5786e2d80ed326"
                       "cbc7f0e71a99f43bfb988b9b7a02dd21")
    assert aes_kw_wrap(kek6, key6) == w6
    assert aes_kw_unwrap(kek6, w6) == key6
    bad = bytearray(w6)
    bad[7] ^= 1
    _expect("AUTH_FAILED", aes_kw_unwrap, kek6, bytes(bad))
    _expect("AUTH_FAILED", aes_kw_unwrap, bytes(32), w6)

def t_kdf_cross_impl():
    """Argon2id construction cross-checked three ways: pure-Python harness
    (phc GENKAT-verified) == cryptography/OpenSSL == pinned. Profile-scale
    pins run cryptography-only (the harness is minutes-slow at 8 MiB+)."""
    from argon2 import argon2id as harness
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    salt = bytes(range(16))
    small = harness(b"bmo-kdf-cross", salt, m_kib=32, t_cost=3, lanes=1, tag_len=32)
    openssl = Argon2id(salt=salt, length=32, iterations=3, lanes=1,
                       memory_cost=32).derive(b"bmo-kdf-cross")
    assert small.hex() == ("703934376580f12a2881b4d6bfb127d8e21a11be7f88d49c"
                           "326d6d3174b96c26")
    assert openssl == small
    p1 = derive_kek(b"bmo-kdf-cross", {"salt": salt, "time": 3, "mem": 8192, "par": 1})
    assert p1.hex() == ("ed3bae60371812ac72c1f4f0fdc0c502dbdf1c1c7b1d32be6407a4b"
                        "7b2521e19")
    p2 = derive_kek(b"bmo-kdf-cross", {"salt": salt, "time": 2, "mem": 16384, "par": 4})
    assert p2.hex() == ("a643d62b1374f7562a6a1b03cb03ebe3f9fbc0a673144821b530a10b"
                        "588f1e54")

def t_kw_helpers_shape():
    kek = derive_kek(b"bmo-kw-shape", VALID_PARAMS)
    w = aes_kw_wrap(kek, bytes(range(32)))
    assert len(w) == 40 and aes_kw_unwrap(kek, w) == bytes(range(32))
    _expect("STRUCTURE_INVALID", aes_kw_wrap, bytes(31), bytes(32))
    _expect("STRUCTURE_INVALID", aes_kw_wrap, bytes(32), bytes(31))
    _expect("STRUCTURE_INVALID", aes_kw_unwrap, kek, bytes(39))
    _expect("STRUCTURE_INVALID", aes_kw_unwrap, kek, bytes(48))
    _expect("AUTH_FAILED", aes_kw_unwrap, kek, bytes(40))

def t_password_golden():
    """password.bmo: envelope -> KEK (pinned) -> AES-KW unwrap -> open."""
    raw = (GOLDEN / "password.bmo").read_bytes()
    man = json.loads((GOLDEN / "password.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == man["sha256_file"]
    sec = find_security(raw)
    kw = sec["keyWrap"]
    assert sec["encrypted"] is True and kw["method"] == "password-argon2id"
    assert kw["params"]["salt"].hex() == man["salt"]
    assert (kw["params"]["time"], kw["params"]["mem"], kw["params"]["par"]) == (
        man["params"]["time"], man["params"]["mem"], man["params"]["par"])
    assert len(kw["wrappedKey"]) == 40
    assert kw["wrappedKey"].hex() == man["wrappedKey"]
    kek = derive_kek(man["password"].encode(), kw["params"])
    assert hashlib.sha256(kek).hexdigest() == man["kek_sha256"]
    key = aes_kw_unwrap(kek, kw["wrappedKey"])
    assert hashlib.sha256(key).hexdigest() == man["key_sha256"]
    f = open_file_password(raw, man["password"].encode())
    assert [o for o, _, _, _ in f["entries"]] == [1]
    h, payload, _ = read_chunk(raw, f["by_id"][1][1], len(raw), key)
    assert h["enc"] == 1
    assert hashlib.sha256(payload).hexdigest() == man["plain_sha256"]
    assert b"password secret" in payload

def t_password_wrong_password():
    raw = (GOLDEN / "password.bmo").read_bytes()
    _expect("AUTH_FAILED", open_file_password, raw, b"wrong-password")

def t_password_keyless():
    raw = (GOLDEN / "password.bmo").read_bytes()
    _expect("NEED_KEY", open_file_password, raw)

def t_password_bounds():
    """Every param outside RFC-0007 2.1 bounds -> STRUCTURE_INVALID (never
    clamped), raised before any KDF work happens."""
    salt = VALID_PARAMS["salt"]
    bad_params = [
        {"salt": bytes(15), "time": 3, "mem": 8192, "par": 1},
        {"salt": bytes(17), "time": 3, "mem": 8192, "par": 1},
        {"salt": "00112233", "time": 3, "mem": 8192, "par": 1},
        {"salt": salt, "time": 0, "mem": 8192, "par": 1},
        {"salt": salt, "time": 9, "mem": 8192, "par": 1},
        {"salt": salt, "time": "3", "mem": 8192, "par": 1},
        {"salt": salt, "time": True, "mem": 8192, "par": 1},
        {"salt": salt, "time": 3, "mem": 8191, "par": 1},
        {"salt": salt, "time": 3, "mem": 1048577, "par": 1},
        {"salt": salt, "time": 3, "mem": 8192, "par": 0},
        {"salt": salt, "time": 3, "mem": 8192, "par": 9},
    ]
    for p in bad_params:
        kw = {"method": "password-argon2id", "params": p, "wrappedKey": bytes(40)}
        _expect("STRUCTURE_INVALID", open_file_password, _mini_file(_envelope(kw)), b"pw")
    for kw in (
        {"method": "password-argon2id", "wrappedKey": bytes(40)},
        {"method": "password-argon2id", "params": "x", "wrappedKey": bytes(40)},
        {"method": "password-argon2id", "params": VALID_PARAMS},
        {"method": "password-argon2id", "params": VALID_PARAMS, "wrappedKey": bytes(39)},
    ):
        _expect("STRUCTURE_INVALID", open_file_password, _mini_file(_envelope(kw)), b"pw")

def t_password_methods():
    """none (test-only) carries the raw key; kms/cert fail closed; unknown
    future methods upgrade; malformed envelopes are structural errors."""
    ok = {"method": "none", "wrappedKey": bytes(range(32))}
    assert unwrap_content_key({"keyWrap": ok}) == bytes(range(32))
    _expect("STRUCTURE_INVALID", unwrap_content_key,
            {"keyWrap": {"method": "none", "wrappedKey": bytes(31)}})
    _expect("NEED_KEY", unwrap_content_key,
            {"keyWrap": {"method": "kms", "kmsUri": "kms://example/key"}})
    _expect("NEED_KEY", unwrap_content_key,
            {"keyWrap": {"method": "cert", "wrappedKey": bytes(40)}}, b"pw")
    _expect("UPGRADE_REQUIRED", unwrap_content_key, {"keyWrap": {"method": "pq-2026"}})
    _expect("STRUCTURE_INVALID", unwrap_content_key, {"keyWrap": {"method": 5}})
    _expect("STRUCTURE_INVALID", unwrap_content_key, {"keyWrap": {}})
    _expect("STRUCTURE_INVALID", unwrap_content_key, {})

def t_password_security_rules():
    """SECURITY chunk invariants: unencrypted, unique, strict CBOR."""
    env = _envelope({"method": "none", "wrappedKey": bytes(32)})
    _expect("STRUCTURE_INVALID", open_file_password, _mini_file(env, enc_sec=True))
    _expect("CORRUPT", open_file_password, _mini_file(env, extra=env))
    _expect("CORRUPT", open_file_password, _mini_file(b"\x9f"))
    _expect("CORRUPT", open_file_password, _mini_file(cbor_encode([1, 2])))
    _expect("CORRUPT", open_file_password, _mini_file(env + b"\x00"))
    bad_flag = cbor_encode({"encrypted": 1, "contentAlg": "AES-256-GCM",
                            "keyWrap": {"method": "none", "wrappedKey": bytes(32)}})
    _expect("STRUCTURE_INVALID", open_file_password, _mini_file(bad_flag))

def t_password_plain_file():
    """No SECURITY chunk: the password API just opens the file."""
    raw = (GOLDEN / "hello.bmo").read_bytes()
    assert open_file_password(raw) == open_file(raw)

if __name__ == "__main__":
    print("password wrap conformance (Argon2id -> AES-KW):")
    for name, fn in [("rfc3394_vectors", t_rfc3394_vectors),
                     ("kdf_cross_impl", t_kdf_cross_impl),
                     ("kw_helpers_shape", t_kw_helpers_shape),
                     ("password_golden", t_password_golden),
                     ("password_wrong_password", t_password_wrong_password),
                     ("password_keyless", t_password_keyless),
                     ("password_bounds", t_password_bounds),
                     ("password_methods", t_password_methods),
                     ("password_security_rules", t_password_security_rules),
                     ("password_plain_file", t_password_plain_file)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
