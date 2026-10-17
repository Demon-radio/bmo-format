"""Load .bmo bytes into plain render structures (envelopes + assets)."""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent / "tests" / "conformance"))
from bmo_reader import open_file, parse_chunk_hdr, parse_footer, read_chunk  # noqa: E402
from model_check import cbor_decode, parse_envelope  # noqa: E402

def load(raw: bytes) -> dict:
    f = open_file(raw)
    envs = []
    for oid, ct, fo, tl in f["entries"]:
        if ct == 0x0010:
            envs.append(parse_envelope(oid, read_chunk(raw, fo, len(raw))[1]))
    by_id = {e["oid"]: e for e in envs}
    children = {}
    for e in envs:
        children.setdefault(e["parent"], []).append(e)
    for v in children.values():
        v.sort(key=lambda e: e["index"] if e["index"] is not None else e["oid"])
    return {"file": f, "envs": envs, "by_id": by_id, "children": children,
            "assets": _assets(raw)}

def _assets(raw: bytes) -> dict:
    """sha -> bytes via ASSET_TABLE + ASSET_DATA scan."""
    tables, data = [], {}
    off, end = 64, len(raw)
    while off + 40 <= end:
        if off + 64 <= end:
            try:
                parse_footer(raw[off:off + 64])
                off += 64
                continue
            except Exception:
                pass
        try:
            h = parse_chunk_hdr(raw[off:off + 40])
        except Exception:
            break
        total = 40 + h["slen"] + 4
        if off + total > end:
            break
        payload = raw[off + 40:off + 40 + h["slen"]]
        if h["type"] == 0x0004:
            tables.append(payload)
        elif h["type"] == 0x0005:
            data[h["oid"]] = payload
        off += total
    out = {}
    for t in tables:
        try:
            v, n = cbor_decode(t, 0)
        except Exception:
            continue
        if n != len(t) or not isinstance(v, dict):
            continue
        for sha, meta in v.items():
            if not isinstance(sha, bytes) or len(sha) != 32:
                continue
            chunks = meta.get("chunks", []) if isinstance(meta, dict) else []
            blob = b"".join(data.get(c, b"") for c in chunks if isinstance(c, int))
            if blob:
                out[sha] = blob
    return out
