"""Independent minimal BMO container reader (RFC-0001).
Separate code path from tests/vectors/make_hello.py: parses header,
footer chain, directory, chunks; verifies CRCs; supports random access
reads and append-only commits. Used by test_container.py.
"""
import struct

POLY = 0x82F63B78
_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ POLY if _c & 1 else _c >> 1
    _T.append(_c & 0xFFFFFFFF)

def crc32c(data: bytes) -> int:
    c = 0xFFFFFFFF
    for b in data:
        c = _T[(c ^ b) & 0xFF] ^ (c >> 8)
    return (c ^ 0xFFFFFFFF) & 0xFFFFFFFF

HEADER_LEN = 64
CHUNK_HDR = 40
FOOTER_LEN = 64

class BmoErr(Exception):
    pass

def _have_zstd() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return False

def _have_aes() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except ImportError:
        return False

def _have_xchacha() -> bool:
    try:
        from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt  # noqa: F401
        return True
    except ImportError:
        return False

def zstd_decompress(data: bytes, expect: int) -> bytes:
    import zstandard
    try:
        out = zstandard.ZstdDecompressor().decompress(data, max_output_size=expect + 1)
    except Exception:
        raise BmoErr("CORRUPT")  # never leak library exceptions (fuzz-closed)
    if len(out) != expect:
        raise BmoErr("CORRUPT")
    return out

def aes_gcm_decrypt(key: bytes, stored: bytes) -> bytes:
    """Stored layout nonce(12)||ciphertext||tag(16). Fail closed."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(key) != 32 or len(stored) < 12 + 16:
        raise BmoErr("CORRUPT")
    try:
        return AESGCM(key).decrypt(stored[:12], stored[12:], None)
    except InvalidTag:
        raise BmoErr("AUTH_FAILED")

def chacha20poly1305_decrypt(key: bytes, stored: bytes) -> bytes:
    """enc=2 (RFC 8439). Same layout nonce(12)||ciphertext||tag(16).
    Uses the audited `cryptography` lib — never the test harness."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    if len(key) != 32 or len(stored) < 12 + 16:
        raise BmoErr("CORRUPT")
    try:
        return ChaCha20Poly1305(key).decrypt(stored[:12], stored[12:], None)
    except InvalidTag:
        raise BmoErr("AUTH_FAILED")

def xchacha20poly1305_decrypt(key: bytes, stored: bytes) -> bytes:
    """enc=3 (XChaCha20-Poly1305). nonce(24)||ciphertext||tag(16)."""
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
    from nacl.exceptions import CryptoError
    if len(key) != 32 or len(stored) < 24 + 16:
        raise BmoErr("CORRUPT")
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(
            stored[24:], None, stored[:24], key)
    except CryptoError:
        raise BmoErr("AUTH_FAILED")

def deflate_decompress(data: bytes, expect: int) -> bytes:
    """RFC 1950 zlib stream with a strict output bound and exact-size check."""
    import zlib
    try:
        dec = zlib.decompressobj()
        out = dec.decompress(data, expect + 1)
    except zlib.error:
        raise BmoErr("CORRUPT")
    if len(out) != expect or not dec.eof or dec.unconsumed_tail or dec.unused_data:
        raise BmoErr("CORRUPT")
    return out

def verify_signature(alg: str, public_key_bytes: bytes, canonical_hash: bytes,
                    signature: bytes) -> None:
    """Verify RFC-0007 signatures against caller-supplied trusted keys."""
    if len(canonical_hash) != 32:
        raise BmoErr("STRUCTURE_INVALID")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import (
        ed25519, ec, padding, rsa)
    try:
        if alg == "Ed25519":
            key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            key.verify(signature, canonical_hash)
        elif alg == "ECDSA-P256-SHA256":
            key = serialization.load_der_public_key(public_key_bytes)
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                    key.curve, ec.SECP256R1):
                raise BmoErr("STRUCTURE_INVALID")
            key.verify(signature, canonical_hash, ec.ECDSA(hashes.SHA256()))
        elif alg == "RSA-PSS-SHA256":
            key = serialization.load_der_public_key(public_key_bytes)
            if not isinstance(key, rsa.RSAPublicKey):
                raise BmoErr("STRUCTURE_INVALID")
            key.verify(signature, canonical_hash, padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                hashes.SHA256())
        else:
            raise BmoErr("UPGRADE_REQUIRED")
    except InvalidSignature:
        raise BmoErr("AUTH_FAILED")
    except (TypeError, ValueError):
        raise BmoErr("STRUCTURE_INVALID")

# --- RFC-0007 section 2: password-argon2id keyWrap (Argon2id -> AES-KW) ---

_ARGON2_BOUNDS = (("time", 1, 8), ("mem", 8192, 1048576), ("par", 1, 8))

def _have_argon2() -> bool:
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id  # noqa: F401
        return True
    except ImportError:
        return False

def derive_kek(password: bytes, params: dict) -> bytes:
    """Argon2id (RFC 9106) -> 32-byte KEK. Params outside the RFC-0007
    profile (salt bstr(16), time 1..8, mem 8192..1048576 KiB, par 1..8)
    are rejected with STRUCTURE_INVALID — never clamped."""
    salt = params.get("salt") if isinstance(params, dict) else None
    if not isinstance(salt, bytes) or len(salt) != 16:
        raise BmoErr("STRUCTURE_INVALID")
    for name, lo, hi in _ARGON2_BOUNDS:
        v = params.get(name)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise BmoErr("STRUCTURE_INVALID")
    if not _have_argon2():
        raise BmoErr("UPGRADE_REQUIRED")
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    return Argon2id(salt=salt, length=32, iterations=params["time"],
                    lanes=params["par"], memory_cost=params["mem"]).derive(password)

def aes_kw_wrap(kek: bytes, key: bytes) -> bytes:
    """RFC 3394 AES key wrap (cryptography). Content key MUST be 32 bytes."""
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
    if len(kek) != 32 or len(key) != 32:
        raise BmoErr("STRUCTURE_INVALID")
    return aes_key_wrap(kek, key)

def aes_kw_unwrap(kek: bytes, wrapped: bytes) -> bytes:
    """RFC 3394 AES key unwrap. Wrong password / tampered blob fails the
    A6 integrity check -> AUTH_FAILED; malformed envelope -> STRUCTURE_INVALID."""
    from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap
    if len(kek) != 32 or not isinstance(wrapped, bytes) or len(wrapped) != 40:
        raise BmoErr("STRUCTURE_INVALID")
    try:
        key = aes_key_unwrap(kek, wrapped)
    except InvalidUnwrap:
        raise BmoErr("AUTH_FAILED")
    if len(key) != 32:
        raise BmoErr("STRUCTURE_INVALID")
    return key

def parse_header(raw: bytes) -> dict:
    if len(raw) < 128:
        raise BmoErr("TOO_SMALL")
    if raw[0:4] != b"BMO\x1a":
        raise BmoErr("BAD_MAGIC")
    major, minor, hlen, hver, flags = struct.unpack_from("<HHHHI", raw, 4)
    if hlen != 64 or hver != 1:
        raise BmoErr("BAD_HEADER_LENGTH")
    if major != 0:
        raise BmoErr("INCOMPATIBLE_MAJOR")
    if minor != 1:
        raise BmoErr("UPGRADE_REQUIRED")
    file_id = raw[16:32]
    (hcrc,) = struct.unpack_from("<I", raw, 32)
    if crc32c(raw[0:32] + raw[36:64]) != hcrc:
        raise BmoErr("HEADER_CRC_MISMATCH")
    return {"major": major, "minor": minor, "flags": flags, "file_id": file_id}

def parse_footer(buf: bytes) -> dict:
    if len(buf) != 64 or buf[0:4] != b"BMOF" or buf[60:64] != b"BMOF":
        raise BmoErr("NO_VALID_FOOTER")
    ver, flags, roff, rlen, txn, prev = struct.unpack_from("<HHQQQQ", buf, 4)
    (fcrc,) = struct.unpack_from("<I", buf, 40)
    if ver != 1 or txn == 0:
        raise BmoErr("CORRUPT")
    if crc32c(buf[0:40]) != fcrc:
        raise BmoErr("FOOTER_CRC_MISMATCH")
    return {"roff": roff, "rlen": rlen, "txn": txn, "prev": prev}

def find_footer(raw: bytes):
    end = len(raw)
    try:
        return end - 64, parse_footer(raw[end - 64:end])
    except BmoErr:
        pass
    lo = max(0, end - 16 * 1024 * 1024 - 64)
    off = end - 65
    while off >= lo:
        if raw[off:off + 4] == b"BMOF" and raw[off + 60:off + 64] == b"BMOF":
            try:
                return off, parse_footer(raw[off:off + 64])
            except BmoErr:
                pass
        off -= 1
    raise BmoErr("NO_VALID_FOOTER")

def parse_chunk_hdr(buf: bytes) -> dict:
    if len(buf) < 40:
        raise BmoErr("TRUNCATED")
    ctype, ver, flags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", buf, 0)
    (hcrc,) = struct.unpack_from("<I", buf, 36)
    if crc32c(buf[0:36]) != hcrc:
        raise BmoErr("CHUNK_HEADER_CRC_MISMATCH")
    if ctype == 0:
        raise BmoErr("CORRUPT")
    if slen > 2**32 - 1 or dlen > 2**32 - 1:
        raise BmoErr("LIMIT_EXCEEDED")
    comp, enc = codec & 0xFFFF, (codec >> 16) & 0xFFFF
    if bool(flags & 1) != bool(comp) or bool(flags & 2) != bool(enc):
        raise BmoErr("CORRUPT")
    # Codec registry (RFC-0001 section 9): 0=NONE always; nonzero codecs
    # require their audited implementation, else
    # clean UPGRADE_REQUIRED.
    if comp not in (0, 1, 2) or enc not in (0, 1, 2, 3):
        raise BmoErr("UPGRADE_REQUIRED")
    if comp == 1 and not _have_zstd():
        raise BmoErr("UPGRADE_REQUIRED")
    if enc in (1, 2) and not _have_aes():  # cryptography provides both suites
        raise BmoErr("UPGRADE_REQUIRED")
    if enc == 3 and not _have_xchacha():
        raise BmoErr("UPGRADE_REQUIRED")
    if flags & 0xFFF0 and flags & 8:
        raise BmoErr("UNKNOWN_CRITICAL")
    return {"type": ctype, "flags": flags, "oid": oid, "slen": slen,
            "dlen": dlen, "comp": comp, "enc": enc}

def read_chunk(raw: bytes, off: int, end: int, content_key: bytes = None):
    h = parse_chunk_hdr(raw[off:off + 40])
    total = 40 + h["slen"] + 4
    if off + total > end:
        raise BmoErr("TRUNCATED")
    stored = raw[off + 40:off + 40 + h["slen"]]
    (pcrc,) = struct.unpack_from("<I", raw, off + 40 + h["slen"])
    if crc32c(stored) != pcrc:
        raise BmoErr("PAYLOAD_CRC_MISMATCH")
    payload = stored
    if h["enc"] in (1, 2, 3):  # decrypt first (write order was compress-then-encrypt)
        if content_key is None:
            raise BmoErr("NEED_KEY")
        dec = {1: aes_gcm_decrypt, 2: chacha20poly1305_decrypt,
               3: xchacha20poly1305_decrypt}[h["enc"]]
        payload = dec(content_key, payload)
    if h["comp"] == 1:
        payload = zstd_decompress(payload, h["dlen"])
    elif h["comp"] == 2:
        payload = deflate_decompress(payload, h["dlen"])
    elif len(payload) != h["dlen"]:
        # plain chunks: stored == decoded; encrypted-only: decrypted == decoded
        raise BmoErr("CORRUPT")
    return h, payload, total

def parse_dir(payload: bytes):
    ver, cnt, txn = struct.unpack_from("<IIQ", payload, 0)
    if ver != 1:
        raise BmoErr("UPGRADE_REQUIRED")
    if len(payload) != 16 + cnt * 32:
        raise BmoErr("CORRUPT")
    entries = []
    prev = None
    for i in range(cnt):
        oid, ct, res, fo, tl = struct.unpack_from("<QIIQQ", payload, 16 + 32 * i)
        if res != 0 or oid == 0 or ct == 0:
            raise BmoErr("CORRUPT")
        if prev is not None and oid <= prev:
            raise BmoErr("DUPLICATE_OBJECT_ID" if oid == prev else "DIRECTORY_UNSORTED")
        prev = oid
        entries.append((oid, ct, fo, tl))
    return txn, entries

KNOWN_TYPES = {0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0006, 0x0007, 0x0008, 0x0010}

def scan_regions(raw: bytes):
    """Walk every chunk region (skipping footers), verifying CRCs.
    Returns DOCUMENT payloads. Mirrors BmoFile::load_document + validate."""
    docs = []
    off, end = 64, len(raw)
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64])
                off += 64
                continue
            except BmoErr:
                pass
        try:
            h = parse_chunk_hdr(raw[off:off + 40])
        except BmoErr as e:
            if str(e) in ("UPGRADE_REQUIRED", "UNKNOWN_CRITICAL"):
                raise
            break  # framing garbage = torn tail, stop scan
        total = 40 + h["slen"] + 4
        if off + total > end:
            break
        payload = raw[off + 40:off + 40 + h["slen"]]
        (pcrc,) = struct.unpack_from("<I", raw, off + 40 + h["slen"])
        if crc32c(payload) != pcrc:
            raise BmoErr("PAYLOAD_CRC_MISMATCH")
        if h["type"] not in KNOWN_TYPES and h["flags"] & 8:
            raise BmoErr("UNKNOWN_CRITICAL")
        if h["type"] == 0x0001:
            docs.append(payload)
        off += total
    return docs

def _decode_security(payload: bytes) -> dict:
    from model_check import ModelErr, cbor_decode
    try:
        v, pos = cbor_decode(payload, 0)
    except ModelErr:
        raise BmoErr("CORRUPT")
    if pos != len(payload) or not isinstance(v, dict):
        raise BmoErr("CORRUPT")
    return v

def find_security(raw: bytes):
    """Decode the SECURITY (0x0007) envelope, or None if absent. The chunk
    MUST be unencrypted and unique (RFC-0007 section 2)."""
    found = None
    off, end = 64, len(raw)
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64])
                off += 64
                continue
            except BmoErr:
                pass
        try:
            h = parse_chunk_hdr(raw[off:off + 40])
        except BmoErr as e:
            if str(e) in ("UPGRADE_REQUIRED", "UNKNOWN_CRITICAL"):
                raise
            break  # framing garbage = torn tail, stop scan
        total = 40 + h["slen"] + 4
        if off + total > end:
            break
        payload = raw[off + 40:off + 40 + h["slen"]]
        if h["type"] == 0x0007:
            if h["enc"] != 0:
                raise BmoErr("STRUCTURE_INVALID")  # MUST be unencrypted
            if found is not None:
                raise BmoErr("CORRUPT")  # at most one SECURITY chunk
            (pcrc,) = struct.unpack_from("<I", raw, off + 40 + h["slen"])
            if crc32c(payload) != pcrc:
                raise BmoErr("PAYLOAD_CRC_MISMATCH")
            if h["comp"] == 1:
                payload = zstd_decompress(payload, h["dlen"])
            elif h["comp"] == 2:
                payload = deflate_decompress(payload, h["dlen"])
            found = payload
        off += total
    return _decode_security(found) if found is not None else None

def open_file(raw: bytes, content_key: bytes = None) -> dict:
    """content_key unlocks encrypted chunks (AES-GCM / ChaCha20-Poly1305 /
    XChaCha20-Poly1305,
    from the SECURITY chunk). Without it, encrypted entries fail with
    NEED_KEY (fail closed)."""
    header = parse_header(raw)
    foff, footer = find_footer(raw)
    end = len(raw)
    rh, rpay, _ = read_chunk(raw, footer["roff"], end)
    if rh["type"] != 0x0003:
        raise BmoErr("ROOT_MISMATCH")
    rver, m, doff, dlen = struct.unpack_from("<IIQQ", rpay, 0)
    if rver != 1 or m > 255 or len(rpay) != 24 + m * 24:
        raise BmoErr("CORRUPT")
    secondary = []
    for i in range(m):
        itype, res, ioff, ilen = struct.unpack_from("<IIQQ", rpay, 24 + 24 * i)
        if res != 0:
            raise BmoErr("CORRUPT")
        secondary.append((itype, ioff, ilen))  # unknown index types ignored, never fatal
    dh, dpay, _ = read_chunk(raw, doff, end)
    if dh["type"] != 0x0002:
        raise BmoErr("CORRUPT")
    txn, entries = parse_dir(dpay)
    if txn != footer["txn"]:
        raise BmoErr("TXN_MISMATCH")
    by_id = {}
    for oid, ct, fo, tl in entries:
        ch, _, total = read_chunk(raw, fo, end, content_key)
        if ch["oid"] != oid or ch["type"] != ct or total != tl:
            raise BmoErr("CORRUPT")
        by_id[oid] = (ct, fo)
    docs = scan_regions(raw)
    if not docs or any(header["file_id"] not in d for d in docs):
        raise BmoErr("DOC_ID_MISMATCH")
    return {"header": header, "footer": footer, "foff": foff,
            "entries": entries, "by_id": by_id, "secondary": secondary}

def unwrap_content_key(envelope: dict, password: bytes = None) -> bytes:
    """Resolve the 32-byte content key from a SECURITY envelope (RFC-0007
    section 2). The test-only `none` method carries the key raw; `kms`/
    `cert` and a missing password fail closed with NEED_KEY; unknown
    future methods upgrade."""
    kw = envelope.get("keyWrap")
    if not isinstance(kw, dict):
        raise BmoErr("STRUCTURE_INVALID")
    method = kw.get("method")
    if not isinstance(method, str):
        raise BmoErr("STRUCTURE_INVALID")
    if method == "password-argon2id":
        if password is None:
            raise BmoErr("NEED_KEY")
        if not isinstance(password, bytes):
            raise BmoErr("STRUCTURE_INVALID")
        return aes_kw_unwrap(derive_kek(password, kw.get("params")),
                             kw.get("wrappedKey"))
    if method == "none":  # TEST VECTORS ONLY (RFC-0007 section 2)
        wrapped = kw.get("wrappedKey")
        if not isinstance(wrapped, bytes) or len(wrapped) != 32:
            raise BmoErr("STRUCTURE_INVALID")
        return wrapped
    if method in ("kms", "cert"):
        raise BmoErr("NEED_KEY")  # offline reader: never has the key
    raise BmoErr("UPGRADE_REQUIRED")

def open_file_password(raw: bytes, password: bytes = None) -> dict:
    """Open with the SECURITY keyWrap flow: Argon2id KEK -> AES-KW unwrap
    -> open_file. Files without SECURITY open normally (encrypted chunks
    then fail closed with NEED_KEY)."""
    envelope = find_security(raw)
    if envelope is None:
        return open_file(raw)
    if not isinstance(envelope.get("encrypted"), bool):
        raise BmoErr("STRUCTURE_INVALID")
    return open_file(raw, unwrap_content_key(envelope, password))

def make_chunk(ctype, ver, flags, oid, payload: bytes, comp: int = 0,
               enc: int = 0, key: bytes = None, nonce: bytes = None) -> bytes:
    """Write pipeline: serialize -> compress -> encrypt -> frame.
    comp: 0 NONE, 1 ZSTD, 2 DEFLATE. enc: 0 NONE, 1 AES-256-GCM,
    2 ChaCha20-Poly1305, 3 XChaCha20-Poly1305. decoded_len records the
    ORIGINAL plaintext size for pre-allocation."""
    if comp not in (0, 1, 2) or enc not in (0, 1, 2, 3):
        raise ValueError("unsupported compression or encryption ID")
    decoded_len = len(payload)
    if comp == 1:
        import zstandard
        payload = zstandard.ZstdCompressor(level=3).compress(payload)
        flags |= 0x0001
    elif comp == 2:
        import zlib
        payload = zlib.compress(payload)
        flags |= 0x0001
    if enc in (1, 2, 3):
        nonce_len = 24 if enc == 3 else 12
        if key is None or len(key) != 32 or nonce is None or len(nonce) != nonce_len:
            raise ValueError("AEAD requires a 32-byte key and suite-sized nonce")
        if enc == 1:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            payload = nonce + AESGCM(key).encrypt(nonce, payload, None)
        elif enc == 2:
            from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
            payload = nonce + ChaCha20Poly1305(key).encrypt(nonce, payload, None)
        else:
            from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt
            payload = nonce + crypto_aead_xchacha20poly1305_ietf_encrypt(
                payload, None, nonce, key)
        flags |= 0x0002
    codec = (enc << 16) | comp
    hdr = struct.pack("<IHHQQQI", ctype, ver, flags, oid, len(payload),
                      decoded_len, codec)
    return hdr + struct.pack("<I", crc32c(hdr)) + payload + struct.pack("<I", crc32c(payload))

def append_commit(raw: bytes, updated: dict, new_objs: dict = None,
                  delete=()) -> bytes:
    """updated: {object_id: new_payload}. new_objs: {object_id: payload}
    for brand-new objects. delete: ids dropped from the new snapshot
    (their bytes stay in history; only the index moves on). Returns txn+1."""
    f = open_file(raw)
    out = bytearray(raw)
    live = {oid: (ct, fo) for oid, ct, fo, _ in
            [(o, c, x, t) for o, c, x, t in f["entries"]]}
    for oid, payload in updated.items():
        c = make_chunk(0x0010, 1, 0x000C, oid, payload)
        live[oid] = (0x0010, len(out))
        out.extend(c)
    for oid, payload in (new_objs or {}).items():
        if oid in live:
            raise BmoErr("DUPLICATE_OBJECT_ID")
        c = make_chunk(0x0010, 1, 0x000C, oid, payload)
        live[oid] = (0x0010, len(out))
        out.extend(c)
    for oid in delete:
        live.pop(oid, None)  # history bytes stay; snapshot drops the id
    # rebuild directory snapshot
    items = sorted(live.items())
    dpay = struct.pack("<IIQ", 1, len(items), f["footer"]["txn"] + 1)
    for oid, (ct, fo) in items:
        # total len must be recomputed for moved/added chunks
        if oid in updated:
            tl = 40 + len(updated[oid]) + 4
        elif new_objs and oid in new_objs:
            tl = 40 + len(new_objs[oid]) + 4
        else:
            tl0 = next(e[3] for e in f["entries"] if e[0] == oid)
            tl = tl0
        dpay += struct.pack("<QIIQQ", oid, ct, 0, fo, tl)
    doff = len(out)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    out.extend(dc)
    rpay = struct.pack("<IIQQ", 1, 0, doff, len(dc))
    roff = len(out)
    rc = make_chunk(0x0003, 1, 0x0008, 0, rpay)
    out.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, roff, len(rc),
                      f["footer"]["txn"] + 1, f["foff"])
    out.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")
    open_file(bytes(out))  # fail fast
    return bytes(out)
