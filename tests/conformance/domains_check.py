"""Domain body validators — RFC-0003..0006. Mirrors core/rust/src/domains.rs.
validate_bodies(envs): envs = envelope dicts from model_check.parse_envelope.
All failures raise ModelErr("STRUCTURE_INVALID") ($ref/$asset stay R6).
"""
import re

from model_check import ModelErr

ALIGNS = {"start", "end", "left", "right", "center", "justify"}
DIRS = {"auto", "ltr", "rtl"}
SHAPES = {"rect", "ellipse", "line", "path"}
CALC_MODES = {"auto", "manual"}
ADDR_RE = re.compile(r"^\$?[A-Za-z]{1,3}\$?[0-9]{1,7}$")

def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)

def _req_map(body, what):
    if not isinstance(body, dict):
        raise ModelErr("STRUCTURE_INVALID")
    return body

def _opt_text(body, key):
    if key in body and not isinstance(body[key], str):
        raise ModelErr("STRUCTURE_INVALID")

def _opt_uint(body, key):
    if key in body and (not _is_int(body[key]) or body[key] < 0):
        raise ModelErr("STRUCTURE_INVALID")

def _ref_shape(v):
    if not isinstance(v, dict):
        raise ModelErr("STRUCTURE_INVALID")

def _color(v):
    if v == "none":
        return
    if not isinstance(v, dict):
        raise ModelErr("STRUCTURE_INVALID")
    rgb = v.get("srgb")
    if not isinstance(rgb, bytes) or len(rgb) != 3:
        raise ModelErr("STRUCTURE_INVALID")
    if "a" in v and (not _is_int(v["a"]) or not 0 <= v["a"] <= 255):
        raise ModelErr("STRUCTURE_INVALID")

def _check_path(d):
    if not isinstance(d, list):
        raise ModelErr("STRUCTURE_INVALID")
    for op in d:
        if not isinstance(op, list) or not op or op[0] not in ("M", "L", "C", "Z"):
            raise ModelErr("STRUCTURE_INVALID")
        need = {"M": 3, "L": 3, "C": 7, "Z": 1}[op[0]]
        if len(op) != need or any(not _is_int(x) for x in op[1:]):
            raise ModelErr("STRUCTURE_INVALID")

def _check_shape(body):
    b = _req_map(body, "shape")
    if b.get("shape") not in SHAPES:
        raise ModelErr("STRUCTURE_INVALID")
    g = b.get("geom")
    if not isinstance(g, dict):
        raise ModelErr("STRUCTURE_INVALID")
    need = {"rect": ("x", "y", "w", "h"), "ellipse": ("cx", "cy", "rx", "ry"),
            "line": ("x1", "y1", "x2", "y2"), "path": ("d",)}[b["shape"]]
    for k in need:
        if k not in g:
            raise ModelErr("STRUCTURE_INVALID")
    if b["shape"] == "path":
        _check_path(g["d"])
    else:
        for k in need:
            if not _is_int(g[k]):
                raise ModelErr("STRUCTURE_INVALID")
    if "fill" in b:
        _color(b["fill"])
    if "line" in b:
        ln = b["line"]
        if not isinstance(ln, dict) or not _is_int(ln.get("w", -1)) or ln.get("w", -1) < 0:
            raise ModelErr("STRUCTURE_INVALID")
        _color(ln.get("color", "none"))

def _addr_parts(addr):
    a = addr.replace("$", "")
    m = re.match(r"^([A-Za-z]{1,3})([0-9]{1,7})$", a)
    if not m:
        return None
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))

def _check_cell(cell):
    if not isinstance(cell, dict):
        raise ModelErr("STRUCTURE_INVALID")
    if "v" in cell and "err" in cell:
        raise ModelErr("STRUCTURE_INVALID")
    if "v" in cell:
        v = cell["v"]
        # bool is a first-class cell value (CBOR keeps it distinct from int).
        if not isinstance(v, (int, float, str, bool, type(None))):
            raise ModelErr("STRUCTURE_INVALID")
        if isinstance(v, float) and v != v:  # NaN not canonical
            raise ModelErr("STRUCTURE_INVALID")
    elif "err" in cell:
        if not isinstance(cell["err"], str):
            raise ModelErr("STRUCTURE_INVALID")
    else:
        raise ModelErr("STRUCTURE_INVALID")  # every cell carries v or err
    if "f" in cell:
        if not isinstance(cell["f"], str) or not cell["f"]:
            raise ModelErr("STRUCTURE_INVALID")
        deps = cell.get("deps")
        if not isinstance(deps, list) or not all(isinstance(d, str) and _addr_parts(d) for d in deps):
            raise ModelErr("STRUCTURE_INVALID")
    if "s" in cell:
        _ref_shape(cell["s"])

def _check_sheet(body):
    b = _req_map(body, "sheet")
    name = b.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 31:
        raise ModelErr("STRUCTURE_INVALID")
    cells = b.get("cells")
    if not isinstance(cells, dict):
        raise ModelErr("STRUCTURE_INVALID")
    for addr, cell in cells.items():
        if not isinstance(addr, str) or not ADDR_RE.match(addr):
            raise ModelErr("STRUCTURE_INVALID")
        parts = _addr_parts(addr)
        if not parts or not (1 <= parts[0] <= 16384 and 1 <= parts[1] <= 1048576):
            raise ModelErr("STRUCTURE_INVALID")
        _check_cell(cell)
    for mg in b.get("merges", []):
        if (not isinstance(mg, list) or len(mg) != 4
                or any(not _is_int(x) or x < 0 for x in mg)
                or mg[2] < mg[0] or mg[3] < mg[1]):
            raise ModelErr("STRUCTURE_INVALID")

def validate_bodies(envs: list):
    by_id = {e["oid"]: e for e in envs}
    children = {}
    for e in envs:
        children.setdefault(e["parent"], []).append(e)
    RESOURCES = ("style", "numfmt", "list-def")  # may live under any container

    def kids(oid):
        return children.get(oid, [])

    def require_kids(oid, allowed):
        ok = set(allowed) | set(RESOURCES)
        for c in kids(oid):
            if c["kind"] not in ok:
                raise ModelErr("STRUCTURE_INVALID")
    # file-scope registries
    list_ids = set()
    for e in envs:
        if e["kind"] == "list-def":
            b = _req_map(e["body"], "list-def")
            lid = b.get("id")
            if not isinstance(lid, str) or not lid or lid in list_ids:
                raise ModelErr("STRUCTURE_INVALID")
            list_ids.add(lid)
            _opt_text(b, "style")
            _opt_text(b, "text")
            _opt_uint(b, "start")
            if "levels" in b:
                levels = b["levels"]
                if not isinstance(levels, list):
                    raise ModelErr("STRUCTURE_INVALID")
                seen_levels = set()
                for level in levels:
                    level = _req_map(level, "list level")
                    number = level.get("level")
                    if (not _is_int(number) or not 1 <= number <= 8
                            or number in seen_levels):
                        raise ModelErr("STRUCTURE_INVALID")
                    seen_levels.add(number)
                    _opt_text(level, "style")
                    _opt_text(level, "text")
                    _opt_uint(level, "start")
    sheets_by_parent = {}
    for e in envs:
        if e["kind"] == "sheet":
            _check_sheet(e["body"])
            sheets_by_parent.setdefault(e["parent"], []).append(e["body"]["name"])
    for names in sheets_by_parent.values():
        if len(set(names)) != len(names):
            raise ModelErr("STRUCTURE_INVALID")
    # per-kind bodies + membership
    for e in envs:
        k, b = e["kind"], e["body"]
        if k == "document":
            _req_map(b, k)
            _opt_text(b, "title")
        elif k == "section":
            _req_map(b, k)
        elif k == "page":
            bb = _req_map(b, k)
            if not _is_int(bb.get("number", -1)) or bb.get("number", -1) < 0:
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("paragraph", "shape", "image", "note", "section", "annotation",
                                    "text", "table"))  # "text" = RFC-0003 §1 legacy alias
        elif k in ("note", "text"):
            bb = _req_map(b, k)
            if not isinstance(bb.get("text"), str):
                raise ModelErr("STRUCTURE_INVALID")
            _opt_text(bb, "lang")
        elif k == "annotation":
            bb = _req_map(b, k)
            if not isinstance(bb.get("text"), str):
                raise ModelErr("STRUCTURE_INVALID")
            _ref_shape(bb.get("target"))
        elif k == "paragraph":
            bb = _req_map(b, k)
            if "align" in bb and bb["align"] not in ALIGNS:
                raise ModelErr("STRUCTURE_INVALID")
            if "dir" in bb and bb["dir"] not in DIRS:
                raise ModelErr("STRUCTURE_INVALID")
            if "list" in bb:
                li = bb["list"]
                if (not isinstance(li, dict) or not isinstance(li.get("id"), str)
                        or li["id"] not in list_ids
                        or not _is_int(li.get("level", -1)) or not 0 <= li.get("level", -1) <= 8):
                    raise ModelErr("STRUCTURE_INVALID")
            if "style" in bb:
                _ref_shape(bb["style"])
            require_kids(e["oid"], ("run",))
        elif k == "run":
            bb = _req_map(b, k)
            if not isinstance(bb.get("text"), str):
                raise ModelErr("STRUCTURE_INVALID")
            if "\u2029" in bb["text"]:
                raise ModelErr("STRUCTURE_INVALID")
            _opt_text(bb, "lang")
            if "script" in bb and (not isinstance(bb["script"], str) or len(bb["script"]) != 4):
                raise ModelErr("STRUCTURE_INVALID")
            if "style" in bb:
                _ref_shape(bb["style"])
            pk = by_id.get(e["parent"], {}).get("kind")
            if pk != "paragraph":
                raise ModelErr("STRUCTURE_INVALID")
        elif k == "list-def":
            pass  # checked in registry pass
        elif k == "style":
            bb = _req_map(b, k)
            _opt_text(bb, "name")
            if "basedOn" in bb:
                _ref_shape(bb["basedOn"])
        elif k == "table":
            bb = _req_map(b, k)
            _opt_uint(bb, "cols")
            if "style" in bb:
                _ref_shape(bb["style"])
            if by_id.get(e["parent"], {}).get("kind") not in {
                    "document", "section", "page", "slide", "cell"}:
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("row",))
        elif k == "row":
            bb = _req_map(b, k)
            _opt_uint(bb, "h")
            if by_id.get(e["parent"], {}).get("kind") != "table":
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("cell",))
        elif k == "cell":
            bb = _req_map(b, k)
            if "gridSpan" in bb and (not _is_int(bb["gridSpan"]) or bb["gridSpan"] < 1):
                raise ModelErr("STRUCTURE_INVALID")
            if by_id.get(e["parent"], {}).get("kind") != "row":
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("paragraph", "table"))
        elif k == "canvas":
            bb = _req_map(b, k)
            if not _is_int(bb.get("w", 0)) or bb["w"] <= 0 or not _is_int(bb.get("h", 0)) or bb["h"] <= 0:
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("shape", "image", "group"))
        elif k == "shape":
            _check_shape(b)
        elif k == "group":
            _req_map(b, k)
            require_kids(e["oid"], ("shape", "image", "group"))
        elif k == "image":
            bb = _req_map(b, k)
            _ref_shape(bb.get("asset"))
            if not isinstance(bb.get("mime"), str) or not bb["mime"]:
                raise ModelErr("STRUCTURE_INVALID")
            if not _is_int(bb.get("w", 0)) or bb["w"] <= 0 or not _is_int(bb.get("h", 0)) or bb["h"] <= 0:
                raise ModelErr("STRUCTURE_INVALID")
        elif k == "media":
            bb = _req_map(b, k)
            _ref_shape(bb.get("asset"))
            if not isinstance(bb.get("mime"), str) or not bb["mime"].startswith(("audio/", "video/")):
                raise ModelErr("STRUCTURE_INVALID")
            if by_id.get(e["parent"], {}).get("kind") != "slide":
                raise ModelErr("STRUCTURE_INVALID")
        elif k == "workbook":
            bb = _req_map(b, k) if isinstance(b, dict) else {}
            if "calc" in bb:
                if not isinstance(bb["calc"], dict) or bb["calc"].get("mode", "auto") not in CALC_MODES:
                    raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("sheet",))
        elif k == "sheet":
            if by_id.get(e["parent"], {}).get("kind") != "workbook":
                raise ModelErr("STRUCTURE_INVALID")
            if children.get(e["oid"]):
                raise ModelErr("STRUCTURE_INVALID")
        elif k == "numfmt":
            bb = _req_map(b, k)
            if not isinstance(bb.get("code"), str):
                raise ModelErr("STRUCTURE_INVALID")
        elif k == "deck":
            if isinstance(b, dict):
                pass
            elif b is not None:
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("slide", "master", "layout"))
        elif k == "slide":
            bb = _req_map(b, k) if isinstance(b, dict) else {}
            if "layout" in bb:
                _ref_shape(bb["layout"])
            if "transition" in bb:
                tr = bb["transition"]
                if not isinstance(tr, dict) or not isinstance(tr.get("type"), str):
                    raise ModelErr("STRUCTURE_INVALID")
                _opt_uint(tr, "durMs")
            _opt_text(bb, "notes")
            if by_id.get(e["parent"], {}).get("kind") != "deck":
                raise ModelErr("STRUCTURE_INVALID")
            require_kids(e["oid"], ("paragraph", "shape", "image", "media", "note", "annotation", "table"))
        elif k in ("master", "layout"):
            if isinstance(b, dict):
                pass
            elif b is not None:
                raise ModelErr("STRUCTURE_INVALID")
        # reserved/future/vendor kinds: opaque, preserved (no checks)
