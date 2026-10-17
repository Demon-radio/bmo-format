"""ChaCha20-Poly1305 AEAD (RFC 8439, §2.8) — pure Python, no dependencies.
Used for the RFC-0007 encrypted test vector. Production SDKs MUST use
audited libraries; this exists so the FORMAT path (framing, nonces, tags,
SECURITY envelope) is verified byte-for-byte without extra toolchains.
"""
import hmac
import struct

_MASK = 0xFFFFFFFF

def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (32 - n))) & _MASK

def _qr(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & _MASK; x[d] ^= x[a]; x[d] = _rotl(x[d], 16)
    x[c] = (x[c] + x[d]) & _MASK; x[b] ^= x[c]; x[b] = _rotl(x[b], 12)
    x[a] = (x[a] + x[b]) & _MASK; x[d] ^= x[a]; x[d] = _rotl(x[d], 8)
    x[c] = (x[c] + x[d]) & _MASK; x[b] ^= x[c]; x[b] = _rotl(x[b], 7)

def chacha_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    assert len(key) == 32 and len(nonce) == 12
    state = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
    state += list(struct.unpack("<8I", key))
    state += [counter & _MASK]
    state += list(struct.unpack("<3I", nonce))
    work = state[:]
    for _ in range(10):
        _qr(work, 0, 4, 8, 12); _qr(work, 1, 5, 9, 13)
        _qr(work, 2, 6, 10, 14); _qr(work, 3, 7, 11, 15)
        _qr(work, 0, 5, 10, 15); _qr(work, 1, 6, 11, 12)
        _qr(work, 2, 7, 8, 13); _qr(work, 3, 4, 9, 14)
    return struct.pack("<16I", *[((w + s) & _MASK) for w, s in zip(work, state)])

def _pad16(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 16)

def _poly1305(key32: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key32[:16], "little") & 0xFFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key32[16:], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        n = int.from_bytes(block + b"\x01" + b"\x00" * (15 - len(block)), "little")
        acc = ((acc + n) * r) % p
    return ((acc + s) % (1 << 128)).to_bytes(16, "little")

def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Returns ciphertext || tag(16)."""
    otk = chacha_block(key, 0, nonce)[:32]
    ct = bytearray()
    for i in range(0, len(plaintext), 64):
        ks = chacha_block(key, 1 + i // 64, nonce)
        blk = plaintext[i:i + 64]
        ct += bytes(a ^ b for a, b in zip(blk, ks))
    ct = bytes(ct)
    mac = _pad16(aad) + _pad16(ct) + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ct))
    return ct + _poly1305(otk, mac)

class AuthFailed(Exception):
    pass

def decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
    if len(ct_and_tag) < 16:
        raise AuthFailed("too short")
    ct, tag = ct_and_tag[:-16], ct_and_tag[-16:]
    otk = chacha_block(key, 0, nonce)[:32]
    mac = _pad16(aad) + _pad16(ct) + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ct))
    if not hmac.compare_digest(_poly1305(otk, mac), tag):
        raise AuthFailed("tag mismatch — MUST NOT return partial plaintext")
    pt = bytearray()
    for i in range(0, len(ct), 64):
        ks = chacha_block(key, 1 + i // 64, nonce)
        blk = ct[i:i + 64]
        pt += bytes(a ^ b for a, b in zip(blk, ks))
    return bytes(pt)
