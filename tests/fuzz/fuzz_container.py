"""Structure-aware container fuzzing: mutate goldens, demand clean errors.
Every input must end in OK / BmoErr / ModelErr — never a raw traceback
(struct.error, IndexError, KeyError, ...). Run: python tests/fuzz/fuzz_container.py
"""
import random
import struct
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "conformance"))
from bmo_reader import BmoErr, open_file  # noqa: E402
from model_check import ModelErr  # noqa: E402

GOLDEN = HERE.parent / "golden"
SEEDS = [(GOLDEN / "hello.bmo").read_bytes(), (GOLDEN / "model.bmo").read_bytes()]

def mutate(rng, data: bytes) -> bytes:
    b = bytearray(data)
    s = rng.choice(["flip", "trunc", "lenlie", "magiczero", "crczero",
                    "append", "swap", "chophdr"])
    if s == "flip":
        for _ in range(rng.randint(1, 8)):
            b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
    elif s == "trunc":
        b = b[:rng.randrange(len(b) + 1)]
    elif s == "lenlie":
        # patch a stored_len field of the first chunk (+-big or huge)
        if len(b) > 64 + 24:
            off = 64 + 16
            (v,) = struct.unpack_from("<Q", b, off)
            struct.pack_into("<Q", b, off, (v + rng.choice([1, 7, 10**6, 2**40])) % 2**64)
    elif s == "magiczero":
        for i in (0, len(b) - 64):
            if 0 <= i < len(b):
                b[i:i + 4] = b"\x00\x00\x00\x00"
    elif s == "crczero":
        if len(b) > 40:
            b[32:36] = b"\x00\x00\x00\x00"
            b[-24:-20] = b"\x00\x00\x00\x00"
    elif s == "append":
        b += bytes(rng.randrange(256) for _ in range(rng.randint(1, 300)))
    elif s == "swap":
        if len(b) > 128:
            i, j = rng.randrange(len(b)), rng.randrange(len(b))
            b[i], b[j] = b[j], b[i]
    elif s == "chophdr":
        b = b[:rng.choice([0, 1, 63, 64, 65, 127, 128])]
    return bytes(b)

def main(n=1500, seed=20261007):
    rng = random.Random(seed)
    counts, unexpected = {}, []
    for i in range(n):
        data = mutate(rng, rng.choice(SEEDS))
        try:
            open_file(data)
            counts["OK"] = counts.get("OK", 0) + 1
        except (BmoErr, ModelErr) as e:
            counts[f"CLEAN:{e}"] = counts.get(f"CLEAN:{e}", 0) + 1
        except Exception as e:  # noqa: BLE001 — the whole point is catching these
            counts["UNEXPECTED"] = counts.get("UNEXPECTED", 0) + 1
            if len(unexpected) < 5:
                unexpected.append((i, type(e).__name__, str(e)[:160], data.hex()[:200]))
    print(f"cases={n} ok_or_clean={n - counts.get('UNEXPECTED', 0)}")
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    print("top outcomes:", top)
    for u in unexpected:
        print("UNEXPECTED:", u)
        traceback.print_stack = None
    if unexpected:
        (HERE / "failures.txt").write_text("\n".join(map(str, unexpected)) + "\n")
        print("wrote tests/fuzz/failures.txt")
        return 1
    print("FUZZ CLEAN: no raw exceptions escaped the reader")
    return 0

if __name__ == "__main__":
    sys.exit(main())
