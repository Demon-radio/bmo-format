"""Argon2id (RFC 9106, v19) — pure Python, test-harness ONLY.
NOT constant-time, NOT for production; production MUST use audited
libraries. Exists so password-KDF vectors (SECURITY method
password-argon2id) are verified end-to-end without toolchains.
"""
import hashlib
import struct

M64 = (1 << 64) - 1
M32 = (1 << 32) - 1
SL = 4

def _h64(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=64).digest()

def _h_t(t: int, data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=t).digest()

def h_prime(out_len: int, data: bytes) -> bytes:
    if out_len <= 64:
        return _h_t(out_len, struct.pack("<I", out_len) + data)
    r = (out_len + 31) // 32 - 2
    vs = [_h64(struct.pack("<I", out_len) + data)]
    for _ in range(1, r):
        vs.append(_h64(vs[-1]))
    last = _h_t(out_len - 32 * r, vs[-1])
    return b"".join(v[:32] for v in vs) + last

def _rotr(x: int, n: int) -> int:
    return ((x >> n) | (x << (64 - n))) & M64

def _trunc(x: int) -> int:
    return x & M32

def _gb(st, a, b, c, d):
    st[a] = (st[a] + st[b] + 2 * _trunc(st[a]) * _trunc(st[b])) & M64
    st[d] = _rotr(st[d] ^ st[a], 32)
    st[c] = (st[c] + st[d] + 2 * _trunc(st[c]) * _trunc(st[d])) & M64
    st[b] = _rotr(st[b] ^ st[c], 24)
    st[a] = (st[a] + st[b] + 2 * _trunc(st[a]) * _trunc(st[b])) & M64
    st[d] = _rotr(st[d] ^ st[a], 16)
    st[c] = (st[c] + st[d] + 2 * _trunc(st[c]) * _trunc(st[d])) & M64
    st[b] = _rotr(st[b] ^ st[c], 63)

def _p16(words):
    """Permutation P over 16 words (Figure 18 feeding). Mutates a copy."""
    v = list(words)
    for a, b, c, d in ((0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15),
                       (0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14)):
        _gb(v, a, b, c, d)
    return v

def _bytes_to_words(block: bytes):
    assert len(block) == 1024
    return list(struct.unpack("<128Q", block))

def _words_to_bytes(words) -> bytes:
    return struct.pack("<128Q", *[w & M64 for w in words])

def g_compress(x: bytes, y: bytes) -> bytes:
    """G(X, Y): R = X^Y; P rows; P cols over 8x8 16-byte registers; Z^R."""
    assert len(x) == len(y) == 1024
    r = _bytes_to_words(bytes(a ^ b for a, b in zip(x, y)))
    # registers: 64 x 16-byte (2 words each); rows of 8 registers = 16 words
    q = [0] * 128
    for row in range(8):
        w16 = []
        for reg in range(8):
            base = (row * 8 + reg) * 2
            w16 += [r[base], r[base + 1]]
        o = _p16(w16)
        for reg in range(8):
            base = (row * 8 + reg) * 2
            q[base], q[base + 1] = o[reg * 2], o[reg * 2 + 1]
    z = [0] * 128
    for col in range(8):
        w16 = []
        for row in range(8):
            base = (row * 8 + col) * 2
            w16 += [q[base], q[base + 1]]
        o = _p16(w16)
        for row in range(8):
            base = (row * 8 + col) * 2
            z[base], z[base + 1] = o[row * 2], o[row * 2 + 1]
    return _words_to_bytes([(a ^ b) & M64 for a, b in zip(z, r)])

_ZERO1024 = bytes(1024)

def _di_addresses(r, lane, sl, m_blocks, passes, y, start_index):
    """Yield (J1, J2) pairs for computed blocks of one segment, index-aligned.
    Matches ref.c: pseudo_rand = address_block.v[index % 128] where index is
    segment-local (pass 0 / slice 0 starts at 2), and a new address block is
    generated with counter = index // 128 + 1 when crossing a 128 boundary."""
    i = start_index
    while True:
        zin = (struct.pack("<6Q", r, lane, sl, m_blocks, passes, y))
        inner = g_compress(_ZERO1024, zin + struct.pack("<Q", i // 128 + 1) + bytes(968))
        block = g_compress(_ZERO1024, inner)
        words = struct.unpack("<128Q", block)
        for w in words[i % 128:]:
            yield w & M32, (w >> 32) & M32
            i += 1

def argon2id(pwd: bytes, salt: bytes, secret: bytes = b"", ad: bytes = b"",
             m_kib: int = 32, t_cost: int = 3, lanes: int = 4,
             tag_len: int = 32, version: int = 19, debug=None) -> bytes:
    y = 2
    h0 = _h64(struct.pack("<6I", lanes, tag_len, m_kib, t_cost, version, y)
              + struct.pack("<I", len(pwd)) + pwd
              + struct.pack("<I", len(salt)) + salt
              + struct.pack("<I", len(secret)) + secret
              + struct.pack("<I", len(ad)) + ad)
    if debug is not None:
        debug["h0"] = h0
    m_blocks = 4 * lanes * (m_kib // (4 * lanes))
    q = m_blocks // lanes
    seglen = q // SL
    B = [[None] * q for _ in range(lanes)]
    for i in range(lanes):
        B[i][0] = h_prime(1024, h0 + struct.pack("<2I", 0, i))
        B[i][1] = h_prime(1024, h0 + struct.pack("<2I", 1, i))

    def W_for(r, s, i, j, l):
        """Reference window (RFC 9106 §3.4.2): last-3 finished segments of
        lane l plus current-segment cols below j-1 (same lane only),
        ALWAYS excluding the previous block B[i][j-1]."""
        order = []
        back, rr, ss = 0, r, s
        while back < 3:
            ss -= 1
            if ss < 0:
                rr -= 1
                ss = SL - 1
            if rr < 0:
                break
            order.append((rr, ss))
            back += 1
        W = []
        for rr2, ss2 in reversed(order):
            W += [(l, c) for c in range(ss2 * seglen, (ss2 + 1) * seglen)]
        if l == i:
            W += [(i, c) for c in range(s * seglen, j - 1)]
        elif j == s * seglen:
            W = W[:-1]  # first block of segment: drop last
        prev_col = j - 1 if j > 0 else q - 1
        return [x for x in W if x != (i, prev_col)]

    def pick(W, j1):
        size = len(W)
        assert size > 0, "empty reference window"
        x = (j1 * j1) >> 32
        yv = (size * x) >> 32
        return W[size - 1 - yv]

    # NOTE: ref_index mixes lanes; DI streams are per (r,s,lane): precompute.
    for r in range(t_cost):
        for s in range(SL):
            di = {}
            if r == 0 and s in (0, 1):
                for l in range(lanes):
                    start = 2 if s == 0 else 0
                    di[l] = _di_addresses(r, l, s, m_blocks, t_cost, y, start)
            seg_start = s * seglen
            for i in range(lanes):
                for j in range(seg_start, seg_start + seglen):
                    if r == 0 and j < 2:
                        continue
                    prev = B[i][j - 1] if j > 0 else B[i][q - 1]
                    if r == 0 and s in (0, 1):
                        j1, j2 = next(di[i])
                        l = i if (r == 0 and s == 0) else j2 % lanes
                    else:
                        j1 = int.from_bytes(prev[0:4], "little")
                        j2 = int.from_bytes(prev[4:8], "little")
                        l = j2 % lanes
                    l, z = pick(W_for(r, s, i, j, l), j1)
                    new = g_compress(prev, B[l][z])
                    if r == 0:
                        B[i][j] = new
                    else:
                        B[i][j] = bytes(a ^ b for a, b in zip(new, B[i][j]))
        if debug is not None:
            debug.setdefault("passes", []).append(
                [B[i][j] for i in range(lanes) for j in range(q)])
    C = B[0][q - 1]
    for i in range(1, lanes):
        C = bytes(a ^ b for a, b in zip(C, B[i][q - 1]))
    return h_prime(tag_len, C)
