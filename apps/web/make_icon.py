"""Build apps/web/bmo.ico: hand-drawn 16x16 'B' glyph, BMO blue, 32x32 PNG-in-ICO.
Zero dependencies. Run once: python apps/web/make_icon.py
"""
import struct
import zlib
from pathlib import Path

# 16x16 letter B (1 = white pixel)
B = [
    "................",
    ".XXXXX..........",
    ".XXXXXX.........",
    ".XX...XX........",
    ".XX...XXX.......",
    ".XX....XX.......",
    ".XX....XX.......",
    ".XXXXXXX........",
    ".XXXXXXX........",
    ".XX....XXX......",
    ".XX.....XX......",
    ".XX.....XX......",
    ".XX....XXX......",
    ".XXXXXXX........",
    ".XXXXXX.........",
    "................",
]
BG = (24, 90, 178)  # BMO blue
FG = (255, 255, 255)

def png32() -> bytes:
    # scale the 16x16 glyph 2x -> 32x32
    px = []
    for row in B:
        big = []
        for c in row:
            big += [FG if c == "X" else BG] * 2
        px += [big, list(big)]
    data = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in px)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(data))) + chunk(b"IEND", b""))

def main():
    png = png32()
    # validate: parse back IHDR
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (32, 32)
    ico = (struct.pack("<HHH", 0, 1, 1)
           + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(png), 22)
           + png)
    out = Path(__file__).resolve().parent / "bmo.ico"
    out.write_bytes(ico)
    print(f"wrote {out} ({len(ico)} bytes)")

if __name__ == "__main__":
    main()
