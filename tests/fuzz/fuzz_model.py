"""CBOR/model-layer fuzz target (second fuzz target per security process).

Mutates CBOR-encoded envelope bytes and model-object payloads, then feeds
them through parse_envelope + validate_structure + validate_bodies.
Every input must terminate with OK / ModelErr — never a raw traceback
(struct.error, RecursionError, UnicodeDecodeError, KeyError, …).

Run: python tests/fuzz/fuzz_model.py
"""
import random
import struct
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "conformance"))
sys.path.insert(0, str(HERE.parent / "vectors"))

from model_check import ModelErr, parse_envelope, validate_structure  # noqa: E402
from domains_check import validate_bodies  # noqa: E402
from make_hello import cbor_encode  # noqa: E402


# --- CBOR helpers -----------------------------------------------------------

def cbor_uint(n):
    if n < 24:
        return bytes([n])
    if n < 256:
        return bytes([0x18, n])
    return struct.pack(">BH", 0x19, n)

def cbor_text(s):
    b = s.encode("utf-8")
    return bytes([0x60 | (len(b) if len(b) < 24 else 0x18)]) + (b"" if len(b) < 24 else bytes([len(b)])) + b

def cbor_bytes_raw(b):
    return bytes([0x40 | (len(b) if len(b) < 24 else 0x18)]) + (b"" if len(b) < 24 else bytes([len(b)])) + b


# --- Seed generation --------------------------------------------------------

KINDS = ["document", "paragraph", "run", "workbook", "sheet", "slide", "deck",
         "presentation", "image", "table", "row", "cell", "style", "list-def",
         "canvas", "media", "chart", "page"]

BODIES = [
    {},
    {"text": "hello"},
    {"text": "A" * 4000},
    {"align": "left"},
    {"align": "diagonal"},  # invalid — should trigger ModelErr
    {"text": "a" + chr(0x2029) + "b"},  # paragraph separator — should be rejected
    {"name": "Sheet1", "cells": {}},
    {"name": "Sheet1", "cells": {"A1": {"v": 1, "f": "=1+1", "deps": ["B1"]}}},
    {"number": 1},
    {"transition": {"type": "fade", "durMs": 300}},
    {"cols": 2},
    {"gridSpan": 2},
    {"mime": "image/png", "w": 100, "h": 100, "asset": {"$asset": b"\x00" * 32}},
    {"mime": "video/mp4", "asset": {"$asset": b"\xff" * 32}},
    {"v": 3.14},
    {"v": True},
    {"v": "#DIV/0!"},
    {"deeply": {"nested": {"key": ["a", "b", "c"] * 100}}},
]


def _seed_envelope(rng):
    kind = rng.choice(KINDS)
    parent = rng.choice([0, 1, 2, 3])
    body = rng.choice(BODIES)
    return cbor_encode({"kind": kind, "parent": parent, "index": rng.randint(0, 10), "body": body})


def _corrupt(rng, data: bytes) -> bytes:
    b = bytearray(data)
    strategy = rng.choice(["flip", "trunc", "lenlie", "random_byte", "prepend_junk",
                           "duplicate", "swap16", "zero_map_count"])
    if strategy == "flip" and b:
        for _ in range(rng.randint(1, min(8, len(b)))):
            b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
    elif strategy == "trunc" and b:
        b = b[:rng.randrange(len(b) + 1)]
    elif strategy == "lenlie" and len(b) > 4:
        # Overwrite a length byte with a very large value
        i = rng.randrange(len(b) - 1)
        b[i] = rng.choice([0xff, 0x7f, 0x18, 0x19, 0x1a, 0x58, 0x59])
    elif strategy == "random_byte" and b:
        b[rng.randrange(len(b))] = rng.randrange(256)
    elif strategy == "prepend_junk":
        junk = bytes(rng.randrange(256) for _ in range(rng.randint(1, 64)))
        b = junk + bytes(b)
    elif strategy == "duplicate" and b:
        b = bytes(b) * rng.randint(2, 4)
    elif strategy == "swap16" and len(b) > 2:
        i = rng.randrange(len(b) - 1)
        b[i], b[i + 1] = b[i + 1], b[i]
    elif strategy == "zero_map_count" and b:
        # CBORmap opcode is 0xa0-0xbf; zero the low nibble to make it a 0-length map
        for i, byte in enumerate(b):
            if 0xa0 <= byte <= 0xbf:
                b[i] = 0xa0
                break
    return bytes(b)


def _run_one(oid, payload):
    """Run the full model pipeline on one (oid, payload) pair."""
    env = parse_envelope(oid, payload)
    validate_structure([(oid, payload)], {oid}, [])
    validate_bodies([env])


def main(n=2000, seed=20261501):
    rng = random.Random(seed)
    counts, unexpected = {}, []

    for i in range(n):
        oid = rng.randint(1, 1000)
        mode = rng.choice(["valid_corrupt", "random_bytes", "valid"])

        if mode == "valid":
            payload = _seed_envelope(rng)
        elif mode == "random_bytes":
            payload = bytes(rng.randrange(256) for _ in range(rng.randint(0, 200)))
        else:
            payload = _corrupt(rng, _seed_envelope(rng))

        try:
            _run_one(oid, payload)
            counts["OK"] = counts.get("OK", 0) + 1
        except (ModelErr, SystemExit) as e:
            counts[f"CLEAN:{type(e).__name__}"] = counts.get(f"CLEAN:{type(e).__name__}", 0) + 1
        except Exception as e:  # noqa: BLE001 — this IS the point
            counts["UNEXPECTED"] = counts.get("UNEXPECTED", 0) + 1
            if len(unexpected) < 5:
                unexpected.append((i, mode, type(e).__name__, str(e)[:160], payload.hex()[:200]))

    print(f"cases={n} ok_or_clean={n - counts.get('UNEXPECTED', 0)}")
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    print("top outcomes:", top)
    for u in unexpected:
        print("UNEXPECTED:", u)
    if unexpected:
        (HERE / "failures_model.txt").write_text("\n".join(map(str, unexpected)) + "\n")
        print("wrote tests/fuzz/failures_model.txt")
        return 1
    print("MODEL FUZZ CLEAN: no raw exceptions escaped the model parser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
