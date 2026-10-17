"""Codec conformance: ZSTD/DEFLATE + AES/ChaCha/XChaCha chunk paths.
Official vectors + framing, tamper, and fail-closed checks.
Run: python tests/conformance/test_codecs.py
"""
import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vectors"))
sys.path.insert(0, str(HERE.parent.parent / "converters" / "python"))
from bmo_ooxml import container as converter_container  # noqa: E402
from bmo_reader import (BmoErr, aes_gcm_decrypt, crc32c, make_chunk, open_file,
                        parse_chunk_hdr, read_chunk)  # noqa: E402
from make_hello import cbor_encode  # noqa: E402

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

WY_VALID = {
    "key": "59d4eafb4de0cfc7d3db99a8f54b15d7b39f0acc8da69763b019c1699f87674a",
    "iv": "2fcb1b38a99e71b84740ad9b",
    "aad": "",
    "msg": "549b365af913f3b081131ccb6b825588",
    "ct": "f58c16690122d75356907fd96b570fca",
    "tag": "28752c20153092818faba2a334640d6e",
}
WY_BADTAG = {
    "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
    "iv": "505152535455565758595a5b",
    "aad": "",
    "msg": "202122232425262728292a2b2c2d2e2f",
    "ct": "b2061457c0759fc1749f174ee1ccadfa",
    "tag": "9de8fef6d8ab1bf1bf887232eab590dd",
}

def t_wycheproof_aes_gcm():
    """Official vectors (C2SP/wycheproof aes_gcm_test.json, 256/96/128)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    v = WY_VALID
    ct_tag = AESGCM(bytes.fromhex(v["key"])).decrypt(
        bytes.fromhex(v["iv"]), bytes.fromhex(v["ct"] + v["tag"]), b"")
    assert ct_tag.hex() == v["msg"]
    # ...and through OUR framing helper (nonce||ct||tag layout)
    stored = bytes.fromhex(v["iv"]) + bytes.fromhex(v["ct"] + v["tag"])
    assert aes_gcm_decrypt(bytes.fromhex(v["key"]), stored).hex() == v["msg"]
    b = WY_BADTAG
    bad = bytes.fromhex(b["iv"]) + bytes.fromhex(b["ct"] + b["tag"])
    try:
        aes_gcm_decrypt(bytes.fromhex(b["key"]), bad)
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
        return
    raise AssertionError("bad-tag vector verified!")

def build_aes_file(key, nonce, enc=1):
    """Build a one-object encrypted file for each registered AEAD suite."""
    alg = {1: "AES-256-GCM", 2: "ChaCha20-Poly1305",
           3: "XChaCha20-Poly1305"}[enc]
    feat = {1: "encryption.aes-256-gcm", 2: "encryption.chacha20-poly1305",
            3: "encryption.xchacha20-poly1305"}[enc]
    file_id = bytes(15) + b"\x0A"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    blob = bytearray(hdr + struct.pack("<I", crc32c(hdr + tail)) + tail)
    doc = cbor_encode({
        "bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
        "docType": "generic", "docId": file_id, "created": 0, "modified": 0,
        "features": [feat], "requiredExtensions": [],
        "optionalExtensions": [], "metadata": {}})
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, doc))
    sec = cbor_encode({"encrypted": True, "contentAlg": alg,
                       "keyWrap": {"method": "none", "wrappedKey": key}})
    blob.extend(make_chunk(0x0007, 1, 0x0008, 0, sec))
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "aes secret"}})
    off = len(blob)
    enc_chunk = make_chunk(0x0010, 1, 0x000C, 1, plain, enc=enc, key=key, nonce=nonce)
    blob.extend(enc_chunk)
    dpay = struct.pack("<IIQ", 1, 1, 1) + struct.pack("<QIIQQ", 1, 0x0010, 0, off, len(enc_chunk))
    doff = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    roff = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, doff, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, roff, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")
    return bytes(blob), plain

KEY = hashlib.sha256(b"bmo-test-aes-key").digest()
NONCE = bytes(range(12))
XNONCE = bytes(range(24))

def t_aes_file_roundtrip():
    raw, plain = build_aes_file(KEY, NONCE)
    f = open_file(raw, KEY)
    assert [o for o, _, _, _ in f["entries"]] == [1]
    h, payload, _ = read_chunk(raw, f["by_id"][1][1], len(raw), KEY)
    assert payload == plain and h["enc"] == 1
    try:
        open_file(raw)
    except BmoErr as e:
        assert str(e) == "NEED_KEY", str(e)
        return
    raise AssertionError("keyless open succeeded!")

def t_aes_wrong_key_and_tamper():
    raw, plain = build_aes_file(KEY, NONCE)
    f = open_file(raw, KEY)
    off = f["by_id"][1][1]
    try:
        read_chunk(raw, off, len(raw), bytes(32))
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
    else:
        raise AssertionError("wrong key decrypted!")
    # flip a tag byte, repair the trailer CRC -> must still fail at the tag
    mut = bytearray(raw)
    h = parse_chunk_hdr(bytes(mut[off:off + 40]))
    tag_at = off + 40 + h["slen"] - 16
    mut[tag_at] ^= 1
    import struct as _s
    _s.pack_into("<I", mut, off + 40 + h["slen"],
                 crc32c(bytes(mut[off + 40:off + 40 + h["slen"]])))
    try:
        read_chunk(bytes(mut), off, len(mut), KEY)
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
        return
    raise AssertionError("tampered tag verified!")

def t_zstd_roundtrip():
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "compress me " * 200}})
    c = make_chunk(0x0010, 1, 0x000C | 0x0001, 7, plain, comp=1)
    h = parse_chunk_hdr(c[:40])
    assert h["comp"] == 1 and h["slen"] < h["dlen"] == len(plain)
    h2, payload, _ = read_chunk(c, 0, len(c))
    assert payload == plain
    # corrupt the stream (valid CRC) -> CORRUPT, never a raw exception
    import struct as _s
    mut = bytearray(c)
    mut[44] ^= 0xFF
    _s.pack_into("<I", mut, 40 + h["slen"], crc32c(bytes(mut[40:40 + h["slen"]])))
    try:
        read_chunk(bytes(mut), 0, len(mut))
    except BmoErr as e:
        assert str(e) == "CORRUPT", str(e)
        return
    raise AssertionError("corrupt zstd stream decoded!")

def t_deflate_roundtrip():
    plain = cbor_encode({"kind": "note", "parent": 0, "index": 0,
                         "body": {"text": "deflate me " * 200}})
    c = make_chunk(0x0010, 1, 0x000C, 8, plain, comp=2)
    h = parse_chunk_hdr(c[:40])
    assert h["comp"] == 2
    _, payload, _ = read_chunk(c, 0, len(c))
    assert payload == plain

    mut = bytearray(c)
    mut[44] ^= 0x80
    struct.pack_into("<I", mut, 40 + h["slen"],
                     crc32c(bytes(mut[40:40 + h["slen"]])))
    try:
        read_chunk(bytes(mut), 0, len(mut))
    except BmoErr as e:
        assert str(e) == "CORRUPT", str(e)
        return
    raise AssertionError("corrupt deflate stream decoded!")

def t_converter_builder_new_codecs():
    plain = b"converter codec writer"
    deflated = converter_container.make_chunk(
        0x0010, 1, 0x000C, 8, plain, comp=2)
    assert read_chunk(deflated, 0, len(deflated))[1] == plain

    key, nonce = bytes(range(32)), bytes(range(24))
    xchacha = converter_container.make_chunk(
        0x0010, 1, 0x000C, 9, plain, comp=2, enc=3, key=key, nonce=nonce)
    assert read_chunk(xchacha, 0, len(xchacha), key)[1] == plain

def t_combined_order():
    plain = b"order matters " * 100
    c = make_chunk(0x0010, 1, 0x000C | 0x0003, 9, plain, comp=1, enc=1,
                   key=KEY, nonce=NONCE)
    h = parse_chunk_hdr(c[:40])
    assert (h["comp"], h["enc"]) == (1, 1)
    _, payload, _ = read_chunk(c, 0, len(c), KEY)
    assert payload == plain


def t_chacha_file_roundtrip():
    """enc=2 (ChaCha20-Poly1305) through the full production path."""
    raw, plain = build_aes_file(KEY, NONCE, enc=2)
    f = open_file(raw, KEY)
    assert [o for o, _, _, _ in f["entries"]] == [1]
    h, payload, _ = read_chunk(raw, f["by_id"][1][1], len(raw), KEY)
    assert payload == plain and h["enc"] == 2
    try:
        open_file(raw)
    except BmoErr as e:
        assert str(e) == "NEED_KEY", str(e)
        return
    raise AssertionError("keyless open succeeded!")


def t_chacha_wrong_key_tamper():
    raw, plain = build_aes_file(KEY, NONCE, enc=2)
    f = open_file(raw, KEY)
    off = f["by_id"][1][1]
    try:
        read_chunk(raw, off, len(raw), bytes(32))
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
    else:
        raise AssertionError("wrong key decrypted!")
    mut = bytearray(raw)
    h = parse_chunk_hdr(bytes(mut[off:off + 40]))
    tag_at = off + 40 + h["slen"] - 16
    mut[tag_at] ^= 1
    struct.pack_into("<I", mut, off + 40 + h["slen"],
                     crc32c(bytes(mut[off + 40:off + 40 + h["slen"]])))
    try:
        read_chunk(bytes(mut), off, len(mut), KEY)
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
        return
    raise AssertionError("tampered tag verified!")

def t_xchacha_file_roundtrip_and_tamper():
    raw, plain = build_aes_file(KEY, XNONCE, enc=3)
    f = open_file(raw, KEY)
    off = f["by_id"][1][1]
    h, payload, _ = read_chunk(raw, off, len(raw), KEY)
    assert h["enc"] == 3 and payload == plain
    try:
        open_file(raw)
    except BmoErr as e:
        assert str(e) == "NEED_KEY", str(e)
    else:
        raise AssertionError("keyless XChaCha open succeeded!")

    try:
        read_chunk(raw, off, len(raw), bytes(32))
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
    else:
        raise AssertionError("wrong key decrypted XChaCha chunk!")
    mut = bytearray(raw)
    h = parse_chunk_hdr(bytes(mut[off:off + 40]))
    tag_at = off + 40 + h["slen"] - 1
    mut[tag_at] ^= 1
    struct.pack_into("<I", mut, off + 40 + h["slen"],
                     crc32c(bytes(mut[off + 40:off + 40 + h["slen"]])))
    try:
        read_chunk(bytes(mut), off, len(mut), KEY)
    except BmoErr as e:
        assert str(e) == "AUTH_FAILED", str(e)
        return
    raise AssertionError("tampered XChaCha tag verified!")


def t_encrypted_golden_chacha():
    """Frozen golden encrypted.bmo now decrypts through the production
    lib (`cryptography`), matching the recorded plaintext hash."""
    import hashlib
    import json
    from make_encrypted import KEY as GKEY
    golden = HERE.parent / "golden"
    raw = (golden / "encrypted.bmo").read_bytes()
    man = json.loads((golden / "encrypted.json").read_text())
    f = open_file(raw, GKEY)
    oid = f["entries"][0][0]
    h, payload, _ = read_chunk(raw, f["by_id"][oid][1], len(raw), GKEY)
    assert h["enc"] == 2
    assert hashlib.sha256(payload).hexdigest() == man["plain_sha256"]


def t_unknown_codec():
    # Unallocated compression/encryption IDs still fail closed.
    for codec, flags in ((9, 0x0001), (4 << 16, 0x0002)):
        hdr = struct.pack("<IHHQQQI", 0x0010, 1, flags, 0, 4, 4, codec)
        blob = hdr + struct.pack("<I", crc32c(hdr)) + b"xxxx" + struct.pack("<I", crc32c(b"xxxx"))
        try:
            parse_chunk_hdr(blob)
        except BmoErr as e:
            assert str(e) == "UPGRADE_REQUIRED", str(e)
        else:
            raise AssertionError(f"future codec parsed: {codec}")

if __name__ == "__main__":
    print("codec conformance (ZSTD/DEFLATE + AES-GCM/ChaCha/XChaCha):")
    for name, fn in [("wycheproof_aes_gcm", t_wycheproof_aes_gcm),
                     ("aes_file_roundtrip", t_aes_file_roundtrip),
                     ("aes_wrong_key_tamper", t_aes_wrong_key_and_tamper),
                     ("zstd_roundtrip", t_zstd_roundtrip),
                     ("deflate_roundtrip", t_deflate_roundtrip),
                     ("converter_builder_new_codecs", t_converter_builder_new_codecs),
                     ("combined_order", t_combined_order),
                     ("chacha_file_roundtrip", t_chacha_file_roundtrip),
                     ("chacha_wrong_key_tamper", t_chacha_wrong_key_tamper),
                     ("xchacha_roundtrip_tamper", t_xchacha_file_roundtrip_and_tamper),
                     ("encrypted_golden_chacha", t_encrypted_golden_chacha),
                     ("unknown_codec", t_unknown_codec)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
