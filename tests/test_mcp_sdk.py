"""MCP SDK interop: the REAL modelcontextprotocol client (v2.x) talks to OUR
hand-rolled server over stdio. Proves any compliant AI client can use BMO.
Run: python tests/test_mcp_sdk.py  (needs `mcp` package + network-free)
"""
import anyio
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "mcp" / "bmo_mcp.py"
passed = failed = 0

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        print(f"  PASS {name}")
        passed += 1
    else:
        print(f"  FAIL {name}: {detail}")
        failed += 1

async def main():
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp import ClientSession
    tmp = Path(tempfile.mkdtemp(prefix="bmo-sdk-"))
    doc = tmp / "sdk.bmo"
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            check("handshake", init.protocol_version == "2024-11-05",
                  getattr(init, "protocol_version", "?"))
            tools = await s.list_tools()
            names = {t.name for t in tools.tools}
            check("tools-listed", len(names) == 9 and "bmo_sheet_set_cell" in names, sorted(names))
            r = await s.call_tool("bmo_create_document",
                                  {"path": str(doc), "title": "SDK",
                                   "text": "Hello via SDK."})
            check("create", '"objects": 3' in r.content[0].text, r.content[0].text[:120])
            r = await s.call_tool("bmo_validate", {"path": str(doc)})
            check("validate", '"valid":true' in r.content[0].text.replace(" ", ""))
            r = await s.call_tool("bmo_sheet_create",
                                  {"path": str(tmp / "s.bmo"), "name": "Q"})
            r = await s.call_tool("bmo_sheet_set_cell",
                                  {"path": str(tmp / "s.bmo"), "sheet": "Q",
                                   "addr": "A1", "value": 7})
            check("sheet-set", '"txn": 2' in r.content[0].text, r.content[0].text[:120])
            try:
                await s.call_tool("nope", {})
                check("unknown-tool-errors", False, "no error raised")
            except Exception as e:
                check("unknown-tool-errors", True)

if __name__ == "__main__":
    print("mcp SDK interop (real client, our server):")
    try:
        anyio.run(main)
    except Exception as e:
        print(f"  FAIL harness: {type(e).__name__}: {e}")
        failed += 1
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
