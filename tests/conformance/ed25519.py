"""Ed25519 (RFC 8032 §5.1/§6) — pure Python, test-harness ONLY.
Faithful transcription of the RFC's Python illustration (extended
coordinates, double-and-add). NOT constant-time, NOT for production;
production SDKs MUST use audited libraries. Exists so SIGNATURE chunks,
canonical hashes and verify paths are tested end-to-end without toolchains.
"""
import hashlib

def _sha512(s: bytes) -> bytes:
    return hashlib.sha512(s).digest()

P = 2**255 - 19

def _inv(x: int) -> int:
    return pow(x, P - 2, P)

D = -121665 * _inv(121666) % P
Q = 2**252 + 27742317777372353535851937790883648493

def _sha512_modq(s: bytes) -> int:
    return int.from_bytes(_sha512(s), "little") % Q

def point_add(XYZT1, XYZT2):
    X1, Y1, Z1, T1 = XYZT1
    X2, Y2, Z2, T2 = XYZT2
    A = (Y1 - X1) * (Y2 - X2) % P
    B = (Y1 + X1) * (Y2 + X2) % P
    C = 2 * T1 * T2 * D % P
    Dd = 2 * Z1 * Z2 % P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)

def point_mul(s: int, XYZT):
    R = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            R = point_add(R, XYZT)
        XYZT = point_add(XYZT, XYZT)
        s >>= 1
    return R

def point_equal(XYZT1, XYZT2) -> bool:
    X1, Y1, Z1, _ = XYZT1
    X2, Y2, Z2, _ = XYZT2
    return (X1 * Z2 - X2 * Z1) % P == 0 and (Y1 * Z2 - Y2 * Z1) % P == 0

_SQRT_M1 = pow(2, (P - 1) // 4, P)

def _recover_x(y: int, sign: int):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    return P - x if (x & 1) != sign else x

_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)

def point_compress(XYZT) -> bytes:
    X, Y, Z, _ = XYZT
    inv = _inv(Z)
    x, y = X * inv % P, Y * inv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")

def point_decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)

def secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("bad private key length")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]

def secret_to_public(secret: bytes) -> bytes:
    a, _ = secret_expand(secret)
    return point_compress(point_mul(a, G))

def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = secret_expand(secret)
    A = point_compress(point_mul(a, G))
    r = _sha512_modq(prefix + msg)
    Rs = point_compress(point_mul(r, G))
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % Q
    return Rs + int.to_bytes(s, 32, "little")

def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = point_decompress(public)
    if not A:
        return False
    R = point_decompress(signature[:32])
    if not R:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = _sha512_modq(signature[:32] + public + msg)
    return point_equal(point_mul(s, G), point_add(R, point_mul(h, A)))
