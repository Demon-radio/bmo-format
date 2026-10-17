"""RFC-0007 conformance: real ChaCha20-Poly1305 vectors + encrypted golden.
Run: python tests/conformance/test_crypto.py
"""
import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vectors"))
from chacha20poly1305 import AuthFailed, decrypt, encrypt
from make_hello import cbor_encode, crc32c

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

def t_rfc8439_section_282():
    """Official vector: RFC 8439 §2.8.2 (key/nonce/aad/pt/ct/tag)."""
    key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you "
          b"only one tip for the future, sunscreen would be it.")
    exp_ct = bytes.fromhex(
        "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d63d"
        "bea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b3692ddbd"
        "7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3ff4def0"
        "8e4b7a9de576d26586cec64b6116")
    exp_tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")
    out = encrypt(key, nonce, pt, aad)
    assert out[:-16] == exp_ct, "ciphertext must match the RFC byte-for-byte"
    assert out[-16:] == exp_tag, "tag must match the RFC byte-for-byte"
    assert decrypt(key, nonce, out, aad) == pt

def t_roundtrip_lengths():
    k, n = bytes(range(32)), bytes(range(12))
    for ln in (0, 1, 15, 16, 17, 64, 135, 1024):
        m = bytes((i * 31) & 0xFF for i in range(ln))
        assert decrypt(k, n, encrypt(k, n, m)) == m, ln
        assert decrypt(k, n, encrypt(k, n, m, b"aad"), b"aad") == m, ln

def t_tamper_every_position():
    k, n = bytes(range(32)), bytes(range(12))
    ct = bytearray(encrypt(k, n, bytes(32)))
    missed = 0
    for i in range(len(ct)):
        ct[i] ^= 1
        try:
            decrypt(k, n, bytes(ct))
            missed += 1
        except AuthFailed:
            pass
        ct[i] ^= 1
    assert missed == 0, f"{missed} tampered positions verified!"

def t_bindings():
    k, n = bytes(range(32)), bytes(range(12))
    ct = encrypt(k, n, b"data", b"aad1")
    for bad in (lambda: decrypt(bytes(32), n, ct),
                lambda: decrypt(k, b"\xFF" + n[1:], ct),
                lambda: decrypt(k, n, ct, b"aad2")):
        try:
            bad()
        except AuthFailed:
            continue
        raise AssertionError("binding bypassed!")

def t_encrypted_golden():
    from make_encrypted import KEY, NONCE
    raw = (GOLDEN / "encrypted.bmo").read_bytes()
    assert raw[:4] == b"BMO\x1a"
    # SECURITY chunk is plain: find + parse envelope
    off = 64
    sec = doc = None
    enc = None
    while off + 40 <= len(raw) - 64:
        ctype, ver, flags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", raw, off)
        (hcrc,) = struct.unpack_from("<I", raw, off + 36)
        assert crc32c(raw[off:off + 36]) == hcrc
        payload = raw[off + 40:off + 40 + slen]
        (pcrc,) = struct.unpack_from("<I", raw, off + 40 + slen)
        assert crc32c(payload) == pcrc
        if ctype == 0x0007:
            sec = payload
        elif ctype == 0x0001:
            doc = payload
        elif ctype == 0x0010:
            assert flags & 0x0002 and (codec >> 16) == 2 and (codec & 0xFFFF) == 0
            assert slen == 12 + dlen + 16, "nonce(12)+ct+tag(16) framing"
            enc = (payload, dlen)
        off += 40 + slen + 4
    assert sec and doc and enc
    assert KEY in sec, "method:none carries the raw content key (tests only)"
    nonce, stored, want = enc[0][:12], enc[0][12:], enc[1]
    assert nonce == NONCE
    plain = decrypt(KEY, nonce, stored)
    assert len(plain) == want
    assert b"secret hello" in plain
    # wrong key / tampered tag both fail closed
    try:
        decrypt(bytes(32), nonce, stored)
        raise AssertionError("wrong key decrypted!")
    except AuthFailed:
        pass
    bad = bytearray(stored)
    bad[-1] ^= 1
    try:
        decrypt(KEY, nonce, bytes(bad))
        raise AssertionError("tampered tag verified!")
    except AuthFailed:
        pass

def canonical_hash_hello():
    """RFC-0001 section 10 canonical bytes for hello.bmo."""
    raw = (GOLDEN / "hello.bmo").read_bytes()
    file_id = raw[16:32]
    objs = {}
    off = 64
    while off + 40 <= len(raw) - 64:
        ctype, ver, flags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", raw, off)
        if ctype in (0x0001, 0x0010):
            payload = raw[off + 40:off + 40 + slen]
            if oid == 0:
                objs["doc"] = hashlib.sha256(payload).digest()
            else:
                objs[str(oid)] = hashlib.sha256(payload).digest()
        import struct as _s
        total = 40 + slen + 4
        off += total
        if off + 40 > len(raw):
            break
        if raw[off:off + 4] == b"BMOF":
            break
    canon = cbor_encode({"fileId": file_id, "magic": "BMO1",
                         "objects": objs, "txn": 1})
    return hashlib.sha256(canon).hexdigest()

# Pinned by first verified run (deterministic inputs: fixed file_id, txn 1).
EXPECTED_CANONICAL_HELLO = "ea6db170f6709d4583d1d4255dd84e46d9133aadc05edaeeba62cf398e22ab5f"

def t_argon2id_kats():
    """Official vector: phc-winner-argon2 kats, argon2id m=32/t=3/p=4."""
    from argon2 import argon2id
    tag = argon2id(bytes([1]) * 32, bytes([2]) * 16, secret=bytes([3]) * 8,
                   ad=bytes([4]) * 12, m_kib=32, t_cost=3, lanes=4, tag_len=32)
    assert tag.hex() == "0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659"

def t_argon2id_blocks():
    """phc kats GENKAT trace: H0 + all 96 memory blocks + tag, digest-pinned."""
    import json as _json
    from argon2 import argon2id
    ref = _json.loads((HERE.parent / "vectors" / "reference" /
                       "argon2id_kat_v19_m32_t3_p4.json").read_text())
    p = ref["params"]
    debug = {}
    tag = argon2id(bytes.fromhex(p["pwd"]), bytes.fromhex(p["salt"]),
                   secret=bytes.fromhex(p["secret"]), ad=bytes.fromhex(p["ad"]),
                   m_kib=p["m_kib"], t_cost=p["t_cost"], lanes=p["lanes"],
                   tag_len=p["tag_len"], version=p["version"], debug=debug)
    assert debug["h0"].hex() == ref["pre_hashing_digest"]
    assert len(debug["passes"]) == len(ref["passes"]) == 3
    for pi, (got, want) in enumerate(zip(debug["passes"], ref["passes"])):
        assert len(got) == 32, (pi, len(got))
        for bi, (blk, wh) in enumerate(zip(got, want)):
            assert hashlib.sha256(blk).hexdigest() == wh, f"pass {pi} block {bi}"
    assert tag.hex() == ref["tag"]

def t_ed25519_rfc8032():
    """Official vectors: RFC 8032 §7.1 TEST 1..3 (pubkey+sign byte-exact)."""
    from ed25519 import secret_to_public, sign, verify
    vecs = [
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
         "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
         "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
         "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
         "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
         "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ]
    for sk, pk, msg, sig in vecs:
        skb, msgb = bytes.fromhex(sk), bytes.fromhex(msg)
        assert secret_to_public(skb).hex() == pk
        assert sign(skb, msgb).hex() == sig
        assert verify(bytes.fromhex(pk), msgb, bytes.fromhex(sig))
    bad = bytearray.fromhex(vecs[0][3])
    bad[10] ^= 1
    assert not verify(bytes.fromhex(vecs[0][1]), b"", bytes(bad))
    assert not verify(bytes.fromhex(vecs[0][1]), b"x", bytes.fromhex(vecs[0][3]))

def t_ecdsa_and_rsa_pss_verification():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from bmo_reader import BmoErr, verify_signature

    canonical_hash = hashlib.sha256(b"signature interoperability input").digest()

    ec_private = ec.generate_private_key(ec.SECP256R1())
    ec_public = ec_private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ec_signature = ec_private.sign(canonical_hash, ec.ECDSA(hashes.SHA256()))
    verify_signature("ECDSA-P256-SHA256", ec_public, canonical_hash, ec_signature)

    rsa_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_public = rsa_private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    rsa_signature = rsa_private.sign(
        canonical_hash, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=32), hashes.SHA256())
    verify_signature("RSA-PSS-SHA256", rsa_public, canonical_hash, rsa_signature)

    for alg, public, signature in (
        ("ECDSA-P256-SHA256", ec_public, ec_signature),
        ("RSA-PSS-SHA256", rsa_public, rsa_signature),
    ):
        altered = bytearray(canonical_hash)
        altered[0] ^= 1
        try:
            verify_signature(alg, public, bytes(altered), signature)
        except BmoErr as exc:
            assert str(exc) == "AUTH_FAILED", str(exc)
        else:
            raise AssertionError(f"{alg} accepted a modified canonical hash")

    try:
        verify_signature("ECDSA-P256-SHA256", ec_public, canonical_hash,
                         ec_signature[:-1])
    except BmoErr as exc:
        assert str(exc) in ("STRUCTURE_INVALID", "AUTH_FAILED")
    else:
        raise AssertionError("truncated ECDSA signature verified")

def t_signed_golden():
    """signed.bmo: stored canonicalHash recomputes + Ed25519 verifies."""
    import hashlib as _hl
    from ed25519 import verify as ed_verify
    raw = (GOLDEN / "signed.bmo").read_bytes()
    file_id = raw[16:32]
    objs, sigs = {}, []
    off = 64
    while off + 40 <= len(raw) - 64:
        ctype, ver, flags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", raw, off)
        payload = raw[off + 40:off + 40 + slen]
        if ctype in (0x0001, 0x0010):
            objs["doc" if oid == 0 else str(oid)] = _hl.sha256(payload).digest()
        elif ctype == 0x0008:
            sigs.append(payload)
        off += 40 + slen + 4
        if raw[off:off + 4] == b"BMOF":
            break
    assert len(sigs) == 1
    import sys as _s
    _s.path.insert(0, str(HERE))
    from model_check import cbor_decode
    dec, n = cbor_decode(sigs[0], 0)
    assert n == len(sigs[0]) and dec["alg"] == "Ed25519"
    canon = cbor_encode({"fileId": file_id, "magic": "BMO1",
                         "objects": objs, "txn": dec["coveredTxn"]})
    assert _hl.sha256(canon).digest() == dec["canonicalHash"]
    import json as _json
    pub = bytes.fromhex(_json.loads((GOLDEN / "signed.json").read_text())["pubkey"])
    assert ed_verify(pub, dec["canonicalHash"], dec["signature"])
    from bmo_reader import verify_signature
    verify_signature("Ed25519", pub, dec["canonicalHash"], dec["signature"])
    bad = bytearray(dec["signature"])
    bad[0] ^= 1
    assert not ed_verify(pub, dec["canonicalHash"], bytes(bad))
    from bmo_reader import BmoErr
    try:
        verify_signature("Ed25519", pub, dec["canonicalHash"], bytes(bad))
    except BmoErr as exc:
        assert str(exc) == "AUTH_FAILED", str(exc)
    else:
        raise AssertionError("tampered Ed25519 signature verified")

def t_canonical_hash_stable():
    h = canonical_hash_hello()
    if EXPECTED_CANONICAL_HELLO == "FILL":
        print(f"\n    canonical(hello) = {h}")
        return
    assert h == EXPECTED_CANONICAL_HELLO, f"{h} != pinned"

if __name__ == "__main__":
    print("crypto conformance:")
    for name, fn in [("rfc8439_282", t_rfc8439_section_282),
                     ("argon2id_kats", t_argon2id_kats),
                     ("argon2id_blocks", t_argon2id_blocks),
                     ("ed25519_rfc8032", t_ed25519_rfc8032),
                     ("ecdsa_and_rsa_pss", t_ecdsa_and_rsa_pss_verification),
                     ("signed_golden", t_signed_golden),
                     ("roundtrip", t_roundtrip_lengths),
                     ("tamper_all_positions", t_tamper_every_position),
                     ("bindings", t_bindings),
                     ("encrypted_golden", t_encrypted_golden),
                     ("canonical_hash", t_canonical_hash_stable)]:
        check(name, fn)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
