"""BMO model structure validator (RFC-0002, envelope v1).
Independent from the Rust core; mirrors core/rust/src/model.rs rule for rule.
"""
import struct

class ModelErr(Exception):
    pass

# --- Minimal CBOR decoder (uint/int/bytes/text/array/map/bool/null) ---
def _need(buf: bytes, pos: int, n: int):
    if pos + n > len(buf):
        raise ModelErr("CORRUPT")  # never let struct.error escape: fuzz-closed

def cbor_decode(buf: bytes, pos: int = 0):
    _need(buf, pos, 1)
    ib = buf[pos]
    pos += 1
    major, ai = ib >> 5, ib & 0x1F
    if major == 7:  # simple/float: ai selects the value, no arg prefix
        if ai == 20:
            return False, pos
        if ai == 21:
            return True, pos
        if ai == 22:
            return None, pos
        if ai == 24:  # one-byte simple value: skip
            _need(buf, pos, 1)
            return None, pos + 1
        if ai == 25:  # f16
            import struct as _st
            _need(buf, pos, 2)
            (v,) = _st.unpack_from(">e", buf, pos)
            return float(v), pos + 2
        if ai == 26:  # f32
            import struct as _st
            _need(buf, pos, 4)
            (v,) = _st.unpack_from(">f", buf, pos)
            return float(v), pos + 4
        if ai == 27:  # f64
            import struct as _st
            _need(buf, pos, 8)
            (v,) = _st.unpack_from(">d", buf, pos)
            return float(v), pos + 8
        raise ModelErr("CORRUPT")
    if ai < 24:
        arg = ai
    elif ai == 24:
        _need(buf, pos, 1)
        arg = buf[pos]; pos += 1
    elif ai == 25:
        _need(buf, pos, 2)
        (arg,) = struct.unpack_from(">H", buf, pos); pos += 2
    elif ai == 26:
        _need(buf, pos, 4)
        (arg,) = struct.unpack_from(">I", buf, pos); pos += 4
    elif ai == 27:
        _need(buf, pos, 8)
        (arg,) = struct.unpack_from(">Q", buf, pos); pos += 8
    else:
        raise ModelErr("CORRUPT")  # indefinite lengths rejected
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major == 2:
        _need(buf, pos, arg)
        return buf[pos:pos + arg], pos + arg
    if major == 3:
        _need(buf, pos, arg)
        try:
            return buf[pos:pos + arg].decode("utf-8"), pos + arg
        except UnicodeDecodeError:
            raise ModelErr("CORRUPT")
    if major == 4:
        if arg > 1_000_000:
            raise ModelErr("LIMIT_EXCEEDED")
        out = []
        for _ in range(arg):
            v, pos = cbor_decode(buf, pos)
            out.append(v)
        return out, pos
    if major == 5:
        if arg > 1_000_000:
            raise ModelErr("LIMIT_EXCEEDED")
        out = {}
        for _ in range(arg):
            k, pos = cbor_decode(buf, pos)
            v, pos = cbor_decode(buf, pos)
            try:
                hash(k)
            except TypeError:
                raise ModelErr("CORRUPT") from None
            if _key(k) in (_key(x) for x in out):
                raise ModelErr("CORRUPT")  # duplicate key
            out[k] = v
        return out, pos
    raise ModelErr("CORRUPT")

def _key(k):
    return (type(k).__name__, k if not isinstance(k, (list, dict)) else repr(k))

def parse_envelope(oid: int, payload: bytes) -> dict:
    v, pos = cbor_decode(payload, 0)
    if pos != len(payload) or not isinstance(v, dict):
        raise ModelErr("CORRUPT")
    if not isinstance(v.get("kind"), str) or not isinstance(v.get("parent"), int):
        raise ModelErr("CORRUPT")
    if v["parent"] < 0:
        raise ModelErr("CORRUPT")
    idx = v.get("index", None)
    if idx is not None and (not isinstance(idx, int) or idx < 0):
        raise ModelErr("CORRUPT")
    if "role" in v and not isinstance(v["role"], str):
        raise ModelErr("CORRUPT")
    if "rev" in v and (not isinstance(v["rev"], int) or v["rev"] < 0):
        raise ModelErr("CORRUPT")
    if "id" in v and v["id"] != oid:
        raise ModelErr("CORRUPT")
    if "ext" in v and not isinstance(v["ext"], dict):
        raise ModelErr("CORRUPT")
    return {"oid": oid, "kind": v["kind"], "parent": v["parent"],
            "index": idx, "role": v.get("role", ""), "rev": v.get("rev", 0),
            "body": v.get("body", None), "ext": v.get("ext", None)}

def collect_refs(v, refs: list, assets: list):
    if isinstance(v, dict):
        for k, val in v.items():
            if k == "$ref":
                if not isinstance(val, int) or val < 0:
                    raise ModelErr("CORRUPT")
                refs.append(val)
                continue
            if k == "$asset":
                if not isinstance(val, bytes) or len(val) != 32:
                    raise ModelErr("CORRUPT")
                assets.append(val)
                continue
            collect_refs(k, refs, assets)
            collect_refs(val, refs, assets)
    elif isinstance(v, list):
        for it in v:
            collect_refs(it, refs, assets)

def table_shas(payload: bytes) -> set:
    v, pos = cbor_decode(payload, 0)
    if pos != len(payload) or not isinstance(v, dict):
        raise ModelErr("CORRUPT")
    out = set()
    for k in v:
        if not isinstance(k, bytes) or len(k) != 32:
            raise ModelErr("CORRUPT")
        out.add(k)
    return out

def validate_structure(objs: list, all_ids: set, tables: list) -> dict:
    """objs: [(oid, payload_bytes)] of GENERIC_OBJECT entries.
    Enforces R1..R6. Returns {'root': oid, 'kinds': {kind: [oids]}}."""
    envs = [parse_envelope(o, p) for o, p in objs]
    roots = [e for e in envs if e["parent"] == 0]
    if len(roots) != 1:  # R1
        raise ModelErr("STRUCTURE_INVALID")
    by_id = {e["oid"]: e for e in envs}
    for e in envs:  # R2+R3: every ancestor chain reaches root 0
        cur, steps = e["parent"], 0
        while cur != 0:
            steps += 1
            if steps > len(envs):
                raise ModelErr("STRUCTURE_INVALID")
            nxt = by_id.get(cur)
            if nxt is None:
                raise ModelErr("STRUCTURE_INVALID")
            cur = nxt["parent"]
    children = {}  # R4: dense sibling indexes
    for e in envs:
        children.setdefault(e["parent"], []).append(e)
    for kids in children.values():
        exp = [k for k in kids if k["index"] is not None]
        if exp:
            if len(exp) != len(kids):
                raise ModelErr("STRUCTURE_INVALID")
            idx = sorted(k["index"] for k in kids)
            if any(v > 0xFFFFFFFF for v in idx):  # u32-capped per RFC-0001 §14
                raise ModelErr("STRUCTURE_INVALID")
            if idx != list(range(len(kids))):
                raise ModelErr("STRUCTURE_INVALID")
    shas = set()  # R6: reference integrity
    for t in tables:
        shas |= table_shas(t)
    for e in envs:
        refs, assets = [], []
        collect_refs(e["body"], refs, assets)
        collect_refs(e["ext"], refs, assets)
        for r in refs:
            if r not in all_ids:
                raise ModelErr("DANGLING_REF")
        for a in assets:
            if a not in shas:
                raise ModelErr("DANGLING_REF")
    kinds = {}
    for e in envs:
        kinds.setdefault(e["kind"], []).append(e["oid"])
    for v in kinds.values():
        v.sort()
    return {"root": roots[0]["oid"], "kinds": kinds}

# --- KIND_INDEX (secondary index_type 1) ---
def build_kind_index(kinds: dict) -> bytes:
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "vectors"))
    from make_hello import cbor_encode  # noqa
    items = sorted(kinds.items())
    out = struct.pack("<II", 1, len(items))
    for kind, oids in items:
        kb = kind.encode()
        out += struct.pack("<H", len(kb)) + kb
        out += struct.pack("<I", len(oids))
        for o in sorted(oids):
            out += struct.pack("<Q", o)
    return out

def parse_kind_index(payload: bytes) -> dict:
    (ver, n) = struct.unpack_from("<II", payload, 0)
    if ver != 1:
        raise ModelErr("UPGRADE_REQUIRED")
    off, out = 8, {}
    for _ in range(n):
        (kl,) = struct.unpack_from("<H", payload, off); off += 2
        kind = payload[off:off + kl].decode("utf-8"); off += kl
        (cnt,) = struct.unpack_from("<I", payload, off); off += 4
        oids = list(struct.unpack_from(f"<{cnt}Q", payload, off)); off += 8 * cnt
        if oids != sorted(oids):
            raise ModelErr("CORRUPT")
        out[kind] = oids
    if off != len(payload):
        raise ModelErr("CORRUPT")
    return out
