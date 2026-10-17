"""Live MCP test: spawns mcp/bmo_mcp.py, drives a full document +
spreadsheet scenario over stdio JSON-RPC. Run: python tests/test_mcp.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "mcp" / "bmo_mcp.py"
passed = failed = 0

def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS {name}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        failed += 1

class Client:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(SERVER)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.seq = 0

    def req(self, method, params=None):
        self.seq += 1
        self.p.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": self.seq, "method": method,
             **({"params": params} if params is not None else {})}) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, "server closed stdout"
        resp = json.loads(line)
        assert resp.get("id") == self.seq, resp
        return resp

    def call(self, tool, args):
        r = self.req("tools/call", {"name": tool, "arguments": args})
        assert "error" not in r, r["error"]
        return json.loads(r["result"]["content"][0]["text"])

    def close(self):
        try:
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        self.p.wait(timeout=10)

TMP = tempfile.mkdtemp(prefix="bmo-mcp-")
cli = Client()

def t_handshake():
    r = cli.req("initialize", {"protocolVersion": "2024-11-05",
                               "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert r["result"]["protocolVersion"] == "2024-11-05", r
    assert r["result"]["serverInfo"]["name"] == "bmo-mcp"
    tools = cli.req("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(names) == 9 and "bmo_sheet_set_cell" in names, names

def t_document_flow():
    p = str(Path(TMP) / "memo.bmo")
    r = cli.call("bmo_create_document", {"path": p, "title": "Memo",
                                         "text": "First para.\n\nSecond para."})
    assert r["objects"] == 5, r  # doc + 2 paras + 2 runs
    assert cli.call("bmo_validate", {"path": p}) == {"valid": True}
    t = cli.call("bmo_get_text", {"path": p})
    assert [x["text"] for x in t["texts"]] == ["First para.", "Second para."], t
    n = cli.call("bmo_append_note", {"path": p, "text": "reviewed"})
    assert n["txn"] == 2, n
    assert cli.call("bmo_validate", {"path": p}) == {"valid": True}
    objs = cli.call("bmo_list_objects", {"path": p})
    assert len(objs["objects"]) == 6, objs
    info = cli.call("bmo_info", {"path": p})
    assert info["objects"] == 6 and info["txn"] == 2, info

def t_sheet_flow():
    p = str(Path(TMP) / "budget.bmo")
    cli.call("bmo_sheet_create", {"path": p, "title": "Budget", "name": "Q1"})
    r = cli.call("bmo_sheet_set_cell", {"path": p, "sheet": "Q1", "addr": "A1", "value": 42})
    assert r["txn"] == 2 and r["deps"] == [], r
    r = cli.call("bmo_sheet_set_cell", {"path": p, "sheet": "Q1", "addr": "B2",
                                        "value": None, "formula": "A1*2"})
    assert r["deps"] == ["A1"] and r["txn"] == 3, r
    c = cli.call("bmo_sheet_get_cell", {"path": p, "sheet": "Q1", "addr": "B2"})
    assert c["cell"]["f"] == "A1*2" and c["cell"]["deps"] == ["A1"], c
    assert cli.call("bmo_validate", {"path": p}) == {"valid": True}
    assert cli.call("bmo_sheet_get_cell", {"path": p, "sheet": "Q1", "addr": "Z9"}) == {"cell": None}

def t_errors():
    r = cli.req("tools/call", {"name": "nope", "arguments": {}})
    assert r["error"]["code"] == -32602, r
    r = cli.req("tools/call", {"name": "bmo_validate", "arguments": {"path": str(Path(TMP) / "missing.bmo")}})
    assert r["error"]["code"] == -32602, r
    r = cli.req("tools/call", {"name": "bmo_sheet_set_cell",
                               "arguments": {"path": str(Path(TMP) / "budget.bmo"),
                                             "sheet": "Nope", "addr": "A1", "value": 1}})
    assert "no sheet" in r["error"]["message"], r

if __name__ == "__main__":
    print("mcp live test:")
    try:
        for name, fn in [("handshake", t_handshake), ("document_flow", t_document_flow),
                         ("sheet_flow", t_sheet_flow), ("errors", t_errors)]:
            check(name, fn)
    finally:
        cli.close()
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
