"""Web UI conformance: live server, real uploads through every endpoint.
Run: python tests/test_web.py
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "apps" / "web" / "server.py"
GOLDEN = HERE / "golden"
PORT = 18077
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

def encode_multipart(fields, files):
    bound = "BMOBOUNDARY42"
    body = b""
    for k, v in fields.items():
        body += (f"--{bound}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for k, (fname, data) in files.items():
        body += (f"--{bound}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    return body + f"--{bound}--\r\n".encode(), bound

def post(path, fields=None, files=None):
    body, bound = encode_multipart(fields or {}, files or {})
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()

def t_ping():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=10) as r:
        assert json.load(r)["ok"] is True

def t_inspect():
    s, _, b = post("/api/inspect", files={"file": ("hello.bmo", (GOLDEN / "hello.bmo").read_bytes())})
    assert s == 200, b[:200]
    j = json.loads(b)
    assert j["objects"] == 3 and j["kinds"]["document"] == 1, j
    assert any(o["kind"] == "text" for o in j["objects_detail"])

def t_convert_csv_bmo():
    s, h, b = post("/api/convert", {"from": "csv", "to": "bmo"},
                   {"file": ("s.csv", b"a,b\n1,x\n")})
    assert s == 200, b[:200]
    assert h.get_content_disposition() == "attachment", h
    assert b[:4] == b"BMO\x1a"
    sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))
    from bmo_reader import open_file
    assert len(open_file(b)["entries"]) == 2  # workbook + sheet

def t_convert_bmo_xlsx():
    s, h, b = post("/api/convert", {"from": "bmo", "to": "xlsx"},
                   {"file": ("sheet.bmo", (GOLDEN / "sheet.bmo").read_bytes())})
    assert s == 200, b[:300]
    assert b[:2] == b"PK"

def t_render():
    s, _, b = post("/api/render", files={"file": ("text.bmo", (GOLDEN / "text.bmo").read_bytes())})
    assert s == 200, b[:200]
    j = json.loads(b)
    assert j["files"] and "<svg" in j["files"][0]["text"]

def t_calc():
    s, _, b = post("/api/calc", {"sheet": "Summary"},
                   {"file": ("sheet.bmo", (GOLDEN / "sheet.bmo").read_bytes())})
    assert s == 200, b[:200]
    assert json.loads(b)["sheets"]["Summary"]["D4"] == 84

def t_open_route():
    import urllib.parse
    p = GOLDEN / "text.bmo"
    url = f"http://127.0.0.1:{PORT}/open?path=" + urllib.parse.quote(str(p))
    with urllib.request.urlopen(url, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    assert r.status == 200 and "مرحبا" in body, body[:200]
    # renamed text is rejected with a friendly page, not a traceback
    url2 = f"http://127.0.0.1:{PORT}/open?path=" + urllib.parse.quote(str(GOLDEN / "hello.json"))
    with urllib.request.urlopen(url2, timeout=30) as r2:
        assert "Not a valid" in r2.read().decode("utf-8", "replace")

def t_bad_upload():
    s, _, b = post("/api/inspect", files={"file": ("x.bin", b"\x00" * 200)})
    assert s == 400, (s, b[:200])
    assert "error" in json.loads(b)

if __name__ == "__main__":
    print("web ui conformance (live server):")
    srv = subprocess.Popen([sys.executable, str(SERVER), "--port", str(PORT), "--no-browser"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=2).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("FAIL: server did not start")
            sys.exit(1)
        for name, fn in [("ping", t_ping), ("inspect", t_inspect),
                         ("open_route", t_open_route),
                         ("convert_csv_bmo", t_convert_csv_bmo),
                         ("convert_bmo_xlsx", t_convert_bmo_xlsx),
                         ("render", t_render), ("calc", t_calc),
                         ("bad_upload", t_bad_upload)]:
            check(name, fn)
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
