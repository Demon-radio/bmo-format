"""Generate deterministic hello.bmo per RFC-0001 section 16.
No randomness, no compression/encryption. Fixed file_id, timestamps.
Also emits tests/golden/hello.json manifest.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

# --- CRC32C (Castagnoli, reflected poly 0x82F63B78) ---
_POLY = 0x82F63B78
_TABLE = None

def _make_table():
    t = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ _POLY if crc & 1 else crc >> 1
        t.append(crc & 0xFFFFFFFF)
    return t

_TABLE = _make_table()

def crc32c(data: bytes, crc: int = 0xFFFFFFFF) -> int:
    for b in data:
        crc = _TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF

assert crc32c(b"123456789") == 0xE3069283, hex(crc32c(b"123456789"))
assert crc32c(b"") == 0x00000000

# --- Minimal deterministic CBOR encoder ---
def _enc_uint(n: int, major: int) -> bytes:
    assert n >= 0
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
        if v >= 0:
            return _enc_uint(v, 0)
        return _enc_uint(-1 - v, 1)
    if isinstance(v, float):
        import struct as _st
        if v == float(_st.unpack(">f", _st.pack(">f", v))[0]):
            return b"\xfa" + _st.pack(">f", v)
        return b"\xfb" + _st.pack(">d", v)
    if isinstance(v, bytes):
        return _enc_uint(len(v), 2) + v
    if isinstance(v, str):
        b = v.encode("utf-8")
        return _enc_uint(len(b), 3) + b
    if isinstance(v, list):
        return _enc_uint(len(v), 4) + b"".join(cbor_encode(x) for x in v)
    if isinstance(v, dict):
        items = [(cbor_encode(k), cbor_encode(val)) for k, val in v.items()]
        items.sort(key=lambda kv: kv[0])
        return _enc_uint(len(items), 5) + b"".join(k + val for k, val in items)
    raise TypeError(f"unsupported CBOR type: {type(v)}")

# --- Chunk helpers ---
FLAG_COMPRESSED = 0x0001
FLAG_ENCRYPTED = 0x0002
FLAG_HAS_OBJECT_ID = 0x0004
FLAG_CRITICAL = 0x0008

T_DOCUMENT = 0x0001
T_DIRECTORY = 0x0002
T_ROOT = 0x0003
T_GENERIC = 0x0010

def make_chunk(chunk_type, version, flags, object_id, payload: bytes) -> bytes:
    stored_len = len(payload)
    decoded_len = len(payload)
    codec = 0
    hdr_wo_crc = struct.pack("<IHHQQQI", chunk_type, version, flags,
                             object_id, stored_len, decoded_len, codec)
    assert len(hdr_wo_crc) == 36
    hcrc = crc32c(hdr_wo_crc)
    header = hdr_wo_crc + struct.pack("<I", hcrc)
    trailer = struct.pack("<I", crc32c(payload))
    return header + payload + trailer

def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "golden"
    out_dir.mkdir(parents=True, exist_ok=True)

    file_id = bytes(15) + b"\x01"
    created_ms = 0

    # File header (64 bytes)
    hdr_fixed = struct.pack("<4sHHHHI", b"BMO\x1a", 0, 1, 64, 1, 0) + file_id
    assert len(hdr_fixed) == 32
    tail = struct.pack("<q", created_ms) + bytes(20)
    header_crc = crc32c(hdr_fixed + tail)
    file_header = hdr_fixed + struct.pack("<I", header_crc) + tail
    assert len(file_header) == 64

    # DOCUMENT payload
    doc = {
        "bmoMajor": 0, "bmoMinor": 1,
        "minMajor": 0, "minMinor": 1,
        "docType": "generic",
        "docId": file_id,
        "created": 0, "modified": 0,
        "features": [],
        "requiredExtensions": [],
        "optionalExtensions": [],
        "metadata": {"title": "Hello BMO"},
    }
    doc_payload = cbor_encode(doc)

    # Generic objects
    obj1 = {"kind": "document", "parent": 0, "body": {"title": "Hello BMO"}}
    obj2 = {"kind": "page", "parent": 1, "body": {"number": 1}}
    obj3 = {"kind": "text", "parent": 2, "body": {"lang": "en", "text": "Hello BMO"}}
    p1, p2, p3 = cbor_encode(obj1), cbor_encode(obj2), cbor_encode(obj3)

    chunks_meta = []  # (type, object_id, offset, total_len, payload_sha)
    blob = bytearray(file_header)
    manifest_chunks = []

    def append(chunk_type, version, flags, oid, payload, name):
        off = len(blob)
        data = make_chunk(chunk_type, version, flags, oid, payload)
        blob.extend(data)
        chunks_meta.append((chunk_type, oid, off, len(data),
                            hashlib.sha256(payload).hexdigest()))
        manifest_chunks.append({"name": name, "type": hex(chunk_type),
                                "object_id": oid, "offset": off,
                                "total_len": len(data),
                                "stored_len": len(payload),
                                "sha256": hashlib.sha256(payload).hexdigest()})

    append(T_DOCUMENT, 1, FLAG_CRITICAL, 0, doc_payload, "DOCUMENT")
    append(T_GENERIC, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, 1, p1, "OBJECT#1")
    append(T_GENERIC, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, 2, p2, "OBJECT#2")
    append(T_GENERIC, 1, FLAG_HAS_OBJECT_ID | FLAG_CRITICAL, 3, p3, "OBJECT#3")

    # OBJECT_DIRECTORY (binary): version, count, txn, entries sorted by object_id
    # entries for object chunks only (DOC has oid 0 -> omitted)
    dir_entries = [(oid, ctype, off, tlen) for ctype, oid, off, tlen, _
                   in chunks_meta if oid != 0]
    dir_entries.sort(key=lambda e: e[0])
    dir_payload = struct.pack("<IIQ", 1, len(dir_entries), 1)
    for oid, ctype, off, tlen in dir_entries:
        dir_payload += struct.pack("<QIIQQ", oid, ctype, 0, off, tlen)
    dir_off = len(blob)
    dir_data = make_chunk(T_DIRECTORY, 1, FLAG_CRITICAL, 0, dir_payload)
    blob.extend(dir_data)
    dir_total = len(dir_data)

    # ROOT_INDEX: version, M=0, dir_offset, dir_len
    root_payload = struct.pack("<IIQQ", 1, 0, dir_off, dir_total)
    root_off = len(blob)
    root_data = make_chunk(T_ROOT, 1, FLAG_CRITICAL, 0, root_payload)
    blob.extend(root_data)
    root_total = len(root_data)

    # FOOTER (64 bytes)
    footer_wo_crc = struct.pack("<4sHHQQQQ", b"BMOF", 1, 0, root_off,
                                root_total, 1, 0)
    assert len(footer_wo_crc) == 40
    fcrc = crc32c(footer_wo_crc)
    footer = footer_wo_crc + struct.pack("<I", fcrc) + bytes(16) + b"BMOF"
    assert len(footer) == 64
    blob.extend(footer)

    bmo_path = out_dir / "hello.bmo"
    bmo_path.write_bytes(bytes(blob))

    manifest = {
        "file": "hello.bmo",
        "size": len(blob),
        "format": "0.1",
        "objects": 3,
        "file_id": file_id.hex(),
        "header_crc": f"{header_crc:#010x}",
        "footer_crc": f"{fcrc:#010x}",
        "footer_offset": len(blob) - 64,
        "root_offset": root_off,
        "dir_offset": dir_off,
        "chunks": manifest_chunks,
        "sha256_file": hashlib.sha256(bytes(blob)).hexdigest(),
    }
    (out_dir / "hello.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                        encoding="utf-8")
    print(f"wrote {bmo_path} ({len(blob)} bytes)")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    sys.exit(main())
