"""Random-access proof: indexed reads stay flat while files grow 10x.
Builds N-note files, times full open vs 200 indexed reads vs 200 linear
scans. Run: python benchmarks/run.py (writes benchmarks/results.json).
"""
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "converters" / "python"))
sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))
from bmo_ooxml.container import cbor_encode, document_meta, genesis, make_chunk  # noqa: E402
from bmo_reader import open_file, parse_chunk_hdr, parse_footer  # noqa: E402

def build(n, fid):
    objs = [(1, {"kind": "document", "parent": 0, "index": 0, "body": {}})]
    for i in range(2, n + 1):
        objs.append((i, {"kind": "note", "parent": 1, "index": i - 2,
                         "body": {"text": f"note {i}"}}))
    return genesis(fid, document_meta("generic", fid, f"bench {n}",
                                      ["model.envelope-v1"]), objs)

def timeit(fn, reps=3):
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best

def bench(n):
    raw = build(n, bytes(15) + b"\x40")
    f = open_file(raw)
    by_id = dict(f["by_id"])
    rng = random.Random(1234)
    targets = [rng.randint(1, n) for _ in range(200)]

    def indexed():
        for oid in targets:
            ct, fo = by_id[oid]
            h = parse_chunk_hdr(raw[fo:fo + 40])
            _ = raw[fo + 40:fo + 40 + h["slen"]]

    def linear():
        for oid in targets:
            off, end, got = 64, len(raw), None
            while off + 40 <= end:
                if off + 64 <= end:
                    try:
                        parse_footer(raw[off:off + 64])
                        off += 64
                        continue
                    except Exception:
                        pass
                h = parse_chunk_hdr(raw[off:off + 40])
                if h["oid"] == oid:
                    got = off
                    break
                off += 40 + h["slen"] + 4
            assert got is not None

    t_open = timeit(lambda: open_file(raw))
    t_idx = timeit(indexed) / 200 * 1e6
    t_lin = timeit(linear, reps=1) / 200 * 1e3
    return {"n": n, "bytes": len(raw), "open_ms": round(t_open * 1e3, 1),
            "indexed_us_per_read": round(t_idx, 1),
            "linear_ms_per_read": round(t_lin, 2),
            "speedup": round(t_lin * 1000 / t_idx, 0)}

if __name__ == "__main__":
    small, big = bench(2000), bench(20000)
    scale = big["indexed_us_per_read"] / small["indexed_us_per_read"]
    res = {"small": small, "big": big,
           "indexed_scaling_10x_size": round(scale, 2)}
    (HERE / "results.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    assert big["speedup"] > 10, "indexed must beat linear by >10x"
    assert scale < 3.0, f"indexed reads must stay ~flat (got {scale}x for 10x size)"
    print("RANDOM-ACCESS PROVEN: indexed reads flat, linear scans grow")
