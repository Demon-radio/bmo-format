"""Minimal BMO genesis builder for converters (independent code path).
Zero dependencies. Deterministic: caller fixes ids, timestamps are zero.
"""
import struct

_POLY = 0x82F63B78
_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ _POLY if _c & 1 else _c >> 1
    _T.append(_c & 0xFFFFFFFF)

def crc32c(data: bytes) -> int:
    c = 0xFFFFFFFF
    for b in data:
        c = _T[(c ^ b) & 0xFF] ^ (c >> 8)
    return (c ^ 0xFFFFFFFF) & 0xFFFFFFFF

def _eu(n: int, major: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 256:
        return bytes([(major << 5) | 24, n])
    if n < 65536:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    if n < 2**32:
        return bytes([(major << 5) | 26]) + struct.pack(">I", n)
    return bytes([(major << 5) | 27]) + struct.pack(">Q", n)

def cbor_encode(v) -> bytes:
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if v is None:
        return b"\xf6"
    if isinstance(v, int):
        return _eu(v, 0) if v >= 0 else _eu(-1 - v, 1)
    if isinstance(v, float):
        if v == struct.unpack(">f", struct.pack(">f", v))[0]:
            return b"\xfa" + struct.pack(">f", v)
        return b"\xfb" + struct.pack(">d", v)
    if isinstance(v, bytes):
        return _eu(len(v), 2) + v
    if isinstance(v, str):
        b = v.encode("utf-8")
        return _eu(len(b), 3) + b
    if isinstance(v, list):
        return _eu(len(v), 4) + b"".join(cbor_encode(x) for x in v)
    if isinstance(v, dict):
        items = sorted(((cbor_encode(k), cbor_encode(x)) for k, x in v.items()))
        return _eu(len(items), 5) + b"".join(k + x for k, x in items)
    raise TypeError(type(v))

def make_chunk(ctype: int, ver: int, flags: int, oid: int, payload: bytes,
               comp: int = 0, enc: int = 0, key: bytes = None,
               nonce: bytes = None) -> bytes:
    """Same write pipeline as the reference reader's builder (see
    tests/conformance/bmo_reader.make_chunk): compress-then-encrypt.
    comp: 0 NONE, 1 ZSTD, 2 DEFLATE. enc: 0 NONE, 1 AES-256-GCM,
    2 ChaCha20-Poly1305, 3 XChaCha20-Poly1305."""
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
    hdr = struct.pack("<IHHQQQI", ctype, ver, flags, oid, len(payload),
                      decoded_len, (enc << 16) | comp)
    return hdr + struct.pack("<I", crc32c(hdr)) + payload + struct.pack("<I", crc32c(payload))

def genesis(file_id: bytes, doc: dict, objects: list, extra=()) -> bytes:
    """objects: [(oid, envelope_dict)]; extra: [(ctype, flags, oid, payload)]."""
    assert isinstance(file_id, bytes) and len(file_id) == 16
    assert doc.get("docId") == file_id, "docId must equal header file_id"
    hdr = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    tail = struct.pack("<q", 0) + bytes(20)
    blob = bytearray(hdr + struct.pack("<I", crc32c(hdr + tail)) + tail)
    blob.extend(make_chunk(0x0001, 1, 0x0008, 0, cbor_encode(doc)))
    for oid, env in objects:
        blob.extend(make_chunk(0x0010, 1, 0x000C, oid, cbor_encode(env)))
    for ctype, flags, oid, payload in extra:
        blob.extend(make_chunk(ctype, 1, flags, oid, payload))
    entries = [(o, 0x0010) for o, _ in objects]
    for ctype, flags, oid, payload in extra:
        if oid != 0:
            entries.append((oid, ctype))
    return _finish(blob, entries)

def _finish(blob, entries):
    # Walk chunks to learn offsets (robust: no caller-supplied positions).
    off = 64
    found = {}
    while off + 40 <= len(blob):
        ctype, ver, flags, oid, slen, dlen, codec = struct.unpack_from("<IHHQQQI", blob, off)
        total = 40 + slen + 4
        if oid != 0:
            found[oid] = (ctype, off, total)
        off += total
    dpay = struct.pack("<IIQ", 1, len(entries), 1)
    for oid, ct in sorted(entries):
        c2, fo, tl = found[oid]
        assert c2 == ct, (oid, c2, ct)
        dpay += struct.pack("<QIIQQ", oid, ct, 0, fo, tl)
    dir_off = len(blob)
    dc = make_chunk(0x0002, 1, 0x0008, 0, dpay)
    blob.extend(dc)
    root_off = len(blob)
    rc = make_chunk(0x0003, 1, 0x0008, 0, struct.pack("<IIQQ", 1, 0, dir_off, len(dc)))
    blob.extend(rc)
    ftr = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off, len(rc), 1, 0)
    blob.extend(ftr + struct.pack("<I", crc32c(ftr)) + bytes(16) + b"BMOF")
    return bytes(blob)

def document_meta(doc_type: str, file_id: bytes, title: str, features: list) -> dict:
    return {"bmoMajor": 0, "bmoMinor": 1, "minMajor": 0, "minMinor": 1,
            "docType": doc_type, "docId": file_id, "created": 0, "modified": 0,
            "features": features, "requiredExtensions": [], "optionalExtensions": [],
            "metadata": {"title": title}}

def assets_extra(assets: dict, start_oid: int = 100):
    """assets {sha: (mime, bytes)} -> (extra_chunks, table_oid_list).
    extra_chunks fits genesis(extra=...); data oids allocated from start_oid."""
    table = {}
    extra = []
    oid = start_oid
    for sha in sorted(assets):
        mime, blob = assets[sha]
        table[sha] = {"size": len(blob), "mime": mime, "chunks": [oid]}
        extra.append((0x0005, 0x0004, oid, blob))  # ASSET_DATA, ancillary + oid
        oid += 1
    table_payload = cbor_encode(table)
    return ([(0x0004, 0x0008, 0, table_payload)] + extra) if table else []
