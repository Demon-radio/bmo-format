"""Shared OOXML helpers: OPC zip I/O, XML namespaces, canonical model
comparison for fidelity measurement. Zero dependencies.
"""
import json
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

def qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"

def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag

def opc_write(files: dict) -> bytes:
    """Deterministic OPC zip (fixed timestamps => byte-stable output)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    return buf.getvalue()

def opc_read(data: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(data)) as z:
        if z.testzip() is not None:
            raise ValueError("CORRUPT_ZIP")
        return {n: z.read(n) for n in z.namelist()}

CT_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>%s</Types>"""

def ct_item(part: str, ctype: str) -> str:
    return f'<Override PartName="{part}" ContentType="{ctype}"/>'

RELS_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>"""

def rel_item(rid: str, rtype: str, target: str, mode: str = "") -> str:
    m = f' TargetMode="{mode}"' if mode else ""
    return f'<Relationship Id="{rid}" Type="{rtype}" Target="{target}"{m}/>'

def xml_bytes(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")

# --- fidelity -----------------------------------------------------------

def sniff_script(text: str) -> str:
    """Dominant script for run metadata (importers only, deterministic)."""
    arab = hebr = cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF \
                or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
            arab += 1
        elif 0x0590 <= o <= 0x05FF:
            hebr += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF:
            cjk += 1
    if arab >= hebr and arab >= cjk and arab > 0:
        return "Arab"
    if hebr >= cjk and hebr > 0:
        return "Hebr"
    if cjk > 0:
        return "Hani"
    return "Latn"

MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "gif": "image/gif", "bmp": "image/bmp", "tif": "image/tiff",
               "tiff": "image/tiff", "emf": "image/emf", "wmf": "image/wmf",
               "svg": "image/svg+xml", "webp": "image/webp"}

def mime_for(name: str) -> str:
    return MIME_BY_EXT.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")

def emu_to_bmu(emu: int) -> int:
    """OOXML EMU -> BMO bmu (thousandths of mm): 914400 EMU = 25.4 mm."""
    return max(1, int(round(emu * 25400 / 914400)))

def bmu_to_emu(bmu: int) -> int:
    return max(1, int(round(bmu * 914400 / 25400)))

def png1x1(rgb=(200, 30, 30)) -> bytes:
    """Minimal VALID 1x1 RGB PNG (zlib from stdlib) for image fixtures."""
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb))) + chunk(b"IEND", b""))

def normalize(envs: list) -> list:
    """[(kind, parent_kind_or_ROOT, body_json)] with $refs resolved to
    target kind+name so renumbered-but-equal files compare equal."""
    by_id = {e["oid"]: e for e in envs}
    def refname(v, stack=()):
        if isinstance(v, dict) and set(v) == {"$ref"}:
            oid = v["$ref"]
            if oid in stack:
                return {"$kind": None, "$cycle": True}  # cyclic style refs: mark, don't hang
            t = by_id.get(oid, {})
            return {"$kind": t.get("kind"), "$body": refname(t.get("body"), stack + (oid,))}
        if isinstance(v, bytes):
            return {"$bytes": v.hex()}  # sha digests compare by hex
        if isinstance(v, dict):
            return {k: refname(x, stack) for k, x in v.items()}
        if isinstance(v, list):
            return [refname(x, stack) for x in v]
        return v
    out = []
    for e in envs:
        pk = by_id.get(e["parent"], {}).get("kind", "ROOT") if e["parent"] else "ROOT"
        out.append((e["kind"], pk, json.dumps(refname(e["body"]), sort_keys=True, ensure_ascii=False)))
    return sorted(out)

def fidelity(a: list, b: list) -> dict:
    """Category-wise preserved/total + overall. a=reference, b=candidate."""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    cats = {}
    for item in ca:
        cats.setdefault(item[0], [0, 0])
        hit = min(ca[item], cb.get(item, 0))
        cats[item[0]][0] += hit
        cats[item[0]][1] += ca[item]
    total_hit = sum(v[0] for v in cats.values())
    total_ref = sum(v[1] for v in cats.values())
    return {"by_kind": {k: {"kept": h, "of": t, "pct": round(100 * h / t, 1) if t else 100.0}
                        for k, (h, t) in sorted(cats.items())},
            "overall_pct": round(100 * total_hit / total_ref, 1) if total_ref else 100.0,
            "kept": total_hit, "of": total_ref,
            "dropped": sorted((ca - cb).elements()),
            "added": sorted((cb - ca).elements())}
