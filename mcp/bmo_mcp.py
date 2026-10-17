"""BMO MCP server — AI-native document access over the Python kit.
Transport: line-delimited JSON-RPC 2.0 on stdio (MCP 2024-11-05 shape).
Zero dependencies. Reads/writes real .bmo files (append-only commits).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "converters" / "python"))
sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))
from bmo_ooxml.container import cbor_encode, document_meta, genesis  # noqa: E402
from bmo_ooxml.xlsx import extract_refs  # noqa: E402 (formula deps contract)
from bmo_reader import BmoErr, append_commit, open_file, read_chunk  # noqa: E402
from model_check import cbor_decode, parse_envelope  # noqa: E402

PROTOCOL = "2024-11-05"
SERVER = {"name": "bmo-mcp", "version": "0.1.0-alpha"}

def _envelopes(raw):
    f = open_file(raw)
    out = []
    for oid, ct, fo, tl in f["entries"]:
        if ct == 0x0010:
            out.append(parse_envelope(oid, read_chunk(raw, fo, len(raw))[1]))
    return f, out

def _next_oid(f):
    return max([e[0] for e in f["entries"]] or [0]) + 1

def _sibling_index(envs, parent):
    sib = [e["index"] for e in envs if e["parent"] == parent and e["index"] is not None]
    return (max(sib) + 1) if sib else len([e for e in envs if e["parent"] == parent])

def t_info(a):
    raw = Path(a["path"]).read_bytes()
    f = open_file(raw)
    kinds = {}
    for oid, ct, _, _ in f["entries"]:
        kinds[str(ct)] = kinds.get(str(ct), 0) + 1
    return {"format": f"{f['header']['major']}.{f['header']['minor']}",
            "objects": len(f["entries"]), "txn": f["footer"]["txn"],
            "encrypted": bool(f["header"]["flags"] & 1),
            "chunk_kinds": kinds, "size": len(raw)}

def t_validate(a):
    try:
        open_file(Path(a["path"]).read_bytes())
        return {"valid": True}
    except BmoErr as e:
        return {"valid": False, "error": str(e)}

def t_create_document(a):
    p = Path(a["path"])
    if p.exists():
        raise ValueError("exists (refusing to overwrite)")
    file_id = bytes(15) + b"\x20"
    title = a.get("title", "Untitled")
    objs, oid = [], 1
    objs.append((oid, {"kind": "document", "parent": 0, "index": 0,
                       "body": {"title": title}}))
    root = oid
    oid += 1
    for i, para in enumerate(a.get("text", "").split("\n\n")):
        if not para.strip():
            continue
        objs.append((oid, {"kind": "paragraph", "parent": root, "index": i,
                           "body": {}}))
        poid = oid
        oid += 1
        objs.append((oid, {"kind": "run", "parent": poid, "index": 0,
                           "body": {"text": para}}))
        oid += 1
    p.write_bytes(genesis(file_id, document_meta(
        "document", file_id, title, ["model.envelope-v1", "text.v1"]), objs))
    return {"path": str(p), "objects": len(objs)}

def t_get_text(a):
    raw = Path(a["path"]).read_bytes()
    _, envs = _envelopes(raw)
    out = []
    for e in envs:
        if e["kind"] in ("run", "note") and isinstance(e["body"].get("text"), str):
            out.append({"oid": e["oid"], "kind": e["kind"], "text": e["body"]["text"]})
    return {"texts": out}

def t_append_note(a):
    p = Path(a["path"])
    raw = p.read_bytes()
    f, envs = _envelopes(raw)
    parent = a.get("parent", 1)
    oid = _next_oid(f)
    env = {"kind": "note", "parent": parent, "index": _sibling_index(envs, parent),
           "body": {"text": a["text"]}}
    if "lang" in a:
        env["body"]["lang"] = a["lang"]
    p.write_bytes(append_commit(raw, {}, {oid: cbor_encode(env)}))
    return {"oid": oid, "txn": f["footer"]["txn"] + 1}

def _find_sheet(raw, name):
    f, envs = _envelopes(raw)
    wb = next(e for e in envs if e["kind"] == "workbook")
    sh = next((e for e in envs if e["kind"] == "sheet" and e["body"].get("name") == name), None)
    if sh is None:
        raise ValueError(f"no sheet {name!r}")
    payload = read_chunk(raw, f["by_id"][sh["oid"]][1], len(raw))[1]
    body, n = cbor_decode(payload, 0)
    assert n == len(payload)
    return f, envs, wb, sh, body["body"]

def t_sheet_create(a):
    p = Path(a["path"])
    if p.exists():
        raise ValueError("exists (refusing to overwrite)")
    file_id = bytes(15) + b"\x21"
    title = a.get("title", "Workbook")
    objs = [(1, {"kind": "workbook", "parent": 0, "index": 0, "body": {}}),
            (2, {"kind": "sheet", "parent": 1, "index": 0,
                 "body": {"name": a.get("name", "Sheet1"), "cells": {}}})]
    p.write_bytes(genesis(file_id, document_meta(
        "spreadsheet", file_id, title, ["model.envelope-v1", "sheet.v1"]), objs))
    return {"path": str(p)}

def t_sheet_set_cell(a):
    import re
    p = Path(a["path"])
    raw = p.read_bytes()
    f, envs, wb, sh, body = _find_sheet(raw, a["sheet"])
    addr = a["addr"].replace("$", "")
    if not re.match(r"^[A-Za-z]{1,3}[0-9]{1,7}$", addr):
        raise ValueError("bad address")
    v = a["value"]
    if isinstance(v, float) and (v != v):
        raise ValueError("NaN not canonical")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    cell = {"v": v}
    if a.get("formula"):
        deps, _, _ = extract_refs(a["formula"])
        cell["f"] = a["formula"]
        cell["deps"] = deps
    body["cells"][addr] = cell
    new_payload = cbor_encode({"kind": "sheet", "parent": sh["parent"],
                               "index": sh["index"], "body": body})
    p.write_bytes(append_commit(raw, {sh["oid"]: new_payload}))
    return {"addr": addr, "txn": f["footer"]["txn"] + 1,
            "deps": cell.get("deps", [])}

def t_sheet_get_cell(a):
    raw = Path(a["path"]).read_bytes()
    _, _, _, _, body = _find_sheet(raw, a["sheet"])
    cell = body["cells"].get(a["addr"].replace("$", ""))
    return {"cell": cell}

def t_list_objects(a):
    raw = Path(a["path"]).read_bytes()
    _, envs = _envelopes(raw)
    return {"objects": [{"oid": e["oid"], "kind": e["kind"], "parent": e["parent"]} for e in envs]}

TOOLS = {
    "bmo_info": (t_info, {"path": "file to inspect"}),
    "bmo_validate": (t_validate, {"path": "file to validate"}),
    "bmo_create_document": (t_create_document, {"path": "new .bmo", "title": "doc title", "text": "paragraphs separated by blank lines"}),
    "bmo_get_text": (t_get_text, {"path": "file"}),
    "bmo_append_note": (t_append_note, {"path": "file", "text": "note", "parent": "default 1"}),
    "bmo_sheet_create": (t_sheet_create, {"path": "new .bmo", "title": "wb title", "name": "first sheet"}),
    "bmo_sheet_set_cell": (t_sheet_set_cell, {"path": "file", "sheet": "name", "addr": "A1", "value": "number|string|bool|null", "formula": "optional"}),
    "bmo_sheet_get_cell": (t_sheet_get_cell, {"path": "file", "sheet": "name", "addr": "A1"}),
    "bmo_list_objects": (t_list_objects, {"path": "file"}),
}

def _tool_spec(name):
    _, desc = TOOLS[name]
    return {"name": name, "description": f"BMO {name} {desc}",
            "inputSchema": {"type": "object"}}

def handle(req):
    mid, method = req.get("id"), req.get("method")
    params = req.get("params") or {}
    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    def err(code, msg, data=None):
        e = {"code": code, "message": msg}
        if data is not None:
            e["data"] = data
        return {"jsonrpc": "2.0", "id": mid, "error": e}
    try:
        if method == "initialize":
            return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                       "serverInfo": SERVER})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": [_tool_spec(n) for n in TOOLS]})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in TOOLS:
                return err(-32602, f"unknown tool {name!r}")
            try:
                result = TOOLS[name][0](args)
            except (ValueError, BmoErr, KeyError, StopIteration, OSError) as e:
                return err(-32602, f"{name}: {e}")
            return ok({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})
        return err(-32601, f"unknown method {method!r}")
    except Exception as e:  # never drop the connection on bugs
        return err(-32603, f"internal: {type(e).__name__}: {e}")

def main():
    inp = sys.stdin.buffer
    out = sys.stdout
    while True:
        line = inp.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": f"parse: {e}"}}) + "\n")
            out.flush()
            continue
        resp = handle(req)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()

if __name__ == "__main__":
    main()
