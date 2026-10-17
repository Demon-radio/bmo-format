"""BMO Studio — local web UI for normal users. Zero dependencies.
Run:  python apps/web/server.py        (opens http://127.0.0.1:8077 in your browser)
Tabs: Inspect (.bmo info+objects) | Convert (Office/CSV/MD <-> BMO) |
      View (render documents/sheets/slides) | Calculate (spreadsheet results).
Local only (127.0.0.1), files never leave your machine. Upload cap: 64 MB.
"""
import json
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "converters" / "python"))
sys.path.insert(0, str(ROOT / "tests" / "conformance"))
sys.path.insert(0, str(ROOT / "renderer" / "python"))
sys.path.insert(0, str(ROOT / "calc" / "python"))
from bmo_ooxml import container, csvmd, docx, pptx, xlsx  # noqa: E402
from bmo_reader import BmoErr, open_file, read_chunk  # noqa: E402
from model_check import parse_envelope  # noqa: E402
from bmo_render.load import load as render_load  # noqa: E402
from bmo_render import svg as render_svg, sheet_html  # noqa: E402
from bmo_calc.evaluate import evaluate_sheet  # noqa: E402

MAX_UPLOAD = 64 * 1024 * 1024
PORT = 8077

PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BMO Studio</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1c1c1c}
h1{font-size:26px} .tabs{display:flex;gap:8px;margin:16px 0}
.tab{padding:8px 16px;border:1px solid #999;border-radius:8px 8px 0 0;cursor:pointer;background:#f0f0f0}
.tab.on{background:#fff;border-bottom:1px solid #fff;font-weight:bold}
.panel{border:1px solid #999;border-radius:0 8px 8px 8px;padding:16px;display:none}
.panel.on{display:block}
.row{margin:10px 0} label{display:inline-block;min-width:130px}
button{padding:8px 18px;font-size:15px;cursor:pointer}
pre{background:#f6f6f6;padding:12px;border-radius:8px;overflow:auto;max-height:320px}
#preview{border:1px solid #ccc;border-radius:8px;min-height:200px;margin-top:12px}
iframe{width:100%;height:480px;border:0}
.note{color:#555;font-size:13px} .err{color:#a00}
table.grid{border-collapse:collapse} table.grid td,table.grid th{border:1px solid #999;padding:4px 10px}
</style></head><body>
<h1>BMO Studio <span class="note">— local, offline, your files stay here</span></h1>
<div class="tabs">
<div class="tab on" data-p="p-inspect">Inspect</div>
<div class="tab" data-p="p-convert">Convert</div>
<div class="tab" data-p="p-view">View</div>
<div class="tab" data-p="p-calc">Calculate</div>
</div>

<div class="panel on" id="p-inspect">
<div class="row"><label>BMO file:</label><input type="file" id="f-inspect" accept=".bmo"></div>
<div class="row"><button onclick="inspect()">Inspect</button></div>
<pre id="o-inspect">Pick a .bmo file, then Inspect.</pre>
</div>

<div class="panel" id="p-convert">
<div class="row"><label>Input file:</label><input type="file" id="f-convert"></div>
<div class="row"><label>From:</label><select id="c-from">
<option value="auto">auto-detect</option><option>docx</option><option>xlsx</option>
<option>pptx</option><option>csv</option><option>md</option><option>bmo</option></select>
<label>To:</label><select id="c-to">
<option>bmo</option><option>docx</option><option>xlsx</option><option>pptx</option></select></div>
<div class="row"><button onclick="convert()">Convert &amp; download</button></div>
<pre id="o-convert">e.g. report.docx -&gt; report.bmo, or back again.</pre>
</div>

<div class="panel" id="p-view">
<div class="row"><label>BMO file:</label><input type="file" id="f-view" accept=".bmo">
<button onclick="view()">Render</button></div>
<div id="preview"><p class="note" style="padding:12px">Rendered output appears here.</p></div>
</div>

<div class="panel" id="p-calc">
<div class="row"><label>Workbook .bmo:</label><input type="file" id="f-calc" accept=".bmo"></div>
<div class="row"><label>Sheet:</label><input id="c-sheet" placeholder="(all sheets)" size="20">
<button onclick="calc()">Calculate</button></div>
<pre id="o-calc">Runs every formula and shows the computed grid.</pre>
</div>

<script>
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
 t.classList.add('on'); document.getElementById(t.dataset.p).classList.add('on');});
async function post(path, fd){
 const r = await fetch(path,{method:'POST',body:fd});
 if(!r.ok){ throw new Error(await r.text()); }
 return r;
}
async function inspect(){
 const f=document.getElementById('f-inspect').files[0]; if(!f) return alert('pick a file');
 const fd=new FormData(); fd.append('file',f);
 try{ document.getElementById('o-inspect').textContent=await (await post('/api/inspect',fd)).text(); }
 catch(e){ document.getElementById('o-inspect').textContent='ERROR: '+e.message; }
}
async function convert(){
 const f=document.getElementById('f-convert').files[0]; if(!f) return alert('pick a file');
 const fd=new FormData(); fd.append('file',f);
 fd.append('from',document.getElementById('c-from').value);
 fd.append('to',document.getElementById('c-to').value);
 try{
  const r=await post('/api/convert',fd);
  const blob=await r.blob();
  const cd=r.headers.get('Content-Disposition')||''; const m=cd.match(/filename="([^"]+)"/);
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=(m&&m[1])||'output'; a.click();
  document.getElementById('o-convert').textContent='Downloaded '+(m&&m[1]);
 }catch(e){ document.getElementById('o-convert').textContent='ERROR: '+e.message; }
}
async function view(){
 const f=document.getElementById('f-view').files[0]; if(!f) return alert('pick a file');
 const fd=new FormData(); fd.append('file',f);
 try{
  const j=await (await post('/api/render',fd)).json();
  const pv=document.getElementById('preview'); pv.innerHTML='';
  j.files.forEach((d,i)=>{
   const h=document.createElement('div'); h.innerHTML='<b>'+d.name+'</b> ';
   const dl=document.createElement('a'); dl.textContent='[download]'; dl.href='#';
   dl.onclick=()=>{ const b=new Blob([d.text],{type:d.mime}); const a=document.createElement('a');
     a.href=URL.createObjectURL(b); a.download=d.name; a.click(); return false; };
   h.appendChild(dl); pv.appendChild(h);
   const fr=document.createElement('iframe'); fr.srcdoc=d.text; pv.appendChild(fr);
  });
 }catch(e){ document.getElementById('preview').innerHTML='<p class=err>ERROR: '+e.message+'</p>'; }
}
async function calc(){
 const f=document.getElementById('f-calc').files[0]; if(!f) return alert('pick a file');
 const fd=new FormData(); fd.append('file',f);
 fd.append('sheet',document.getElementById('c-sheet').value);
 try{ document.getElementById('o-calc').textContent=await (await post('/api/calc',fd)).text(); }
 catch(e){ document.getElementById('o-calc').textContent='ERROR: '+e.message; }
}
</script></body></html>"""


def parse_multipart(body: bytes, content_type: str):
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        raise ValueError("not multipart")
    sep = b"--" + m.group(1).strip('"').encode("latin-1")
    out = {}
    for chunk in body.split(sep)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, data = chunk.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        hs = head.decode("latin-1")
        name = re.search(r'name="([^"]+)"', hs)
        fn = re.search(r'filename="([^"]*)"', hs)
        if name:
            out[name.group(1)] = ((fn.group(1) if fn else None), data)
    return out


def model_envs(raw):
    f = open_file(raw)
    envs = []
    for oid, ct, fo, tl in f["entries"]:
        if ct == 0x0010:
            e = parse_envelope(oid, read_chunk(raw, fo, len(raw))[1])
            envs.append({"oid": oid, "kind": e["kind"], "parent": e["parent"],
                         "index": e["index"], "body": e["body"]})
    return f, envs


def to_bmo_bytes(fmt, data: bytes, title: str, fid: int):
    file_id = bytes(15) + bytes([fid])
    if fmt == "docx":
        t, envs, _, assets = docx.import_docx(data)
        meta = ("document", ["model.envelope-v1", "text.v1"])
    elif fmt == "xlsx":
        t, envs, _, assets = xlsx.import_xlsx(data)
        meta = ("spreadsheet", ["model.envelope-v1", "sheet.v1"])

    elif fmt == "pptx":
        t, envs, _, assets = pptx.import_pptx(data)
        meta = ("presentation", ["model.envelope-v1", "text.v1", "graphics.v1", "deck.v1"])
    elif fmt == "csv":
        t, envs, _ = csvmd.import_csv(data.decode("utf-8-sig"), title)
        assets, meta = {}, ("spreadsheet", ["model.envelope-v1", "sheet.v1"])
    elif fmt == "md":
        t, envs, _ = csvmd.import_markdown(data.decode("utf-8-sig"), title)
        assets, meta = {}, ("document", ["model.envelope-v1", "text.v1"])
    else:
        raise ValueError(f"cannot import .{fmt}")
    objs = [(e["oid"], {k: e[k] for k in ("kind", "parent", "index", "body")}) for e in envs]
    raw = container.genesis(file_id, container.document_meta(
        meta[0], file_id, t, meta[1]), objs, container.assets_extra(assets))
    open_file(raw)
    return raw, t


class Handler(BaseHTTPRequestHandler):
    server_version = "BMOStudio/0.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json", filename=None,
              extra_notes=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for note in list(extra_notes)[:5]:
            self.send_header("X-BMO-Note", str(note)[:200])
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(),
                   "application/json")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file too large (64 MB cap)")
        return self.rfile.read(n)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html"):
            data = PAGE.encode()
            self._send(200, data, "text/html; charset=utf-8")
        elif urlparse(self.path).path == "/api/ping":
            self._json(200, {"ok": True, "app": "bmo-studio"})
        elif urlparse(self.path).path == "/open":
            self.page_open()
        elif urlparse(self.path).path == "/raw":
            self.route_raw()
        else:
            self._json(404, {"error": "unknown route"})

    def page_open(self):
        """Render a local file path directly (used by double-click opener).
        Server runs as you, so it can read your disk. Local only."""
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        p = (q.get("path") or [""])[0]
        try:
            raw = Path(p).read_bytes()
        except OSError:
            return self._send(200, b"<h1>BMO Studio</h1><p>Cannot read file.</p>",
                              "text/html; charset=utf-8")
        try:
            model = render_load(raw)
        except Exception as e:
            return self._send(200, ("<h1>BMO Studio</h1>"
                                    f"<p>Not a valid .bmo file ({type(e).__name__}). "
                                    "Tip: renaming never converts - use Convert on the original.</p>").encode(),
                                "text/html; charset=utf-8")
        name = Path(p).name
        kinds = {e["kind"] for e in model["envs"]}
        if "sheet" in kinds or "workbook" in kinds:
            body = sheet_html.render_workbook(model, name)
            page = f"<h1>{name}</h1>{body}"
        elif "slide" in kinds or "deck" in kinds:
            parts = "".join(f"<h2>Slide {i}</h2>{s}" for i, s in
                            enumerate(render_svg.render_deck(model, name), 1))
            page = f"<h1>{name}</h1>{parts}"
        elif "canvas" in kinds:
            page = f"<h1>{name}</h1>{render_svg.render_canvas_page(model, name)}"
        else:
            page = f"<h1>{name}</h1>{render_svg.render_document(model, name)}"
        shell = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                 f"<title>{name} - BMO Studio</title></head><body>" + page + "</body></html>")
        self._send(200, shell.encode("utf-8"), "text/html; charset=utf-8")

    def route_raw(self):
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        p = (q.get("path") or [""])[0]
        try:
            raw = Path(p).read_bytes()
            m = render_load(raw)
            self._json(200, {"file": Path(p).name, "size": len(raw),
                             "objects": len(m["envs"])})
        except Exception as e:
            self._json(400, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/inspect":
                self.api_inspect()
            elif path == "/api/convert":
                self.api_convert()
            elif path == "/api/render":
                self.api_render()
            elif path == "/api/calc":
                self.api_calc()
            else:
                self._json(404, {"error": "unknown route"})
        except (ValueError, BmoErr, OSError) as e:
            self._json(400, {"error": f"{type(e).__name__}: {e}"})
        except Exception as e:  # never leak tracebacks to the browser
            self._json(500, {"error": f"internal: {type(e).__name__}"})

    def _upload(self, field="file"):
        parts = parse_multipart(self._body(), self.headers.get("Content-Type"))
        if field not in parts:
            raise ValueError("no file uploaded")
        fname, data = parts[field]
        if not data:
            raise ValueError("empty file")
        return fname or "upload", data, parts

    def api_inspect(self):
        fname, data, _ = self._upload()
        f, envs = model_envs(data)
        kinds = {}
        for e in envs:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        self._json(200, {"file": fname, "size": len(data),
                         "format": f"{f['header']['major']}.{f['header']['minor']}",
                         "objects": len(envs), "txn": f["footer"]["txn"],
                         "kinds": kinds,
                         "objects_detail": [{"oid": e["oid"], "kind": e["kind"],
                                             "parent": e["parent"]} for e in envs]})

    def api_convert(self):
        fname, data, parts = self._upload()
        fmt_in = self._field(parts, "from", auto=True, fname=fname)
        fmt_out = self._field(parts, "to", must=True)
        stem = Path(fname).stem or "output"
        if fmt_in == "bmo" and fmt_out == "bmo":
            raise ValueError("bmo->bmo is a copy; nothing to do")
        if fmt_in == "bmo":
            f, envs = model_envs(data)
            approx = []
            assets = self._asset_map(data, envs)
            if fmt_out == "docx":
                out = docx.export_docx(envs, stem, approx, assets)
            elif fmt_out == "xlsx":
                out = xlsx.export_xlsx(envs, stem, approx)
            elif fmt_out == "pptx":
                out = pptx.export_pptx(envs, stem, approx, assets)
            else:
                raise ValueError(f"bmo->{fmt_out} not supported yet")
            self._send(200, out, "application/octet-stream", f"{stem}.{fmt_out}",
                       extra_notes=approx)
            return
        if fmt_out != "bmo":
            raise ValueError("only OOXML->bmo is supported here (use a .bmo input for export)")
        raw, title = to_bmo_bytes(fmt_in, data, stem, 0x50)
        self._send(200, raw, "application/octet-stream", f"{stem}.bmo")

    def _asset_map(self, raw, envs):
        try:
            from bmo_render.load import _assets as _a
            blobs = _a(raw)
            return {sha: (self._mime_for(envs, sha), blob[sha]) for sha in blobs}
        except Exception:
            return {}

    @staticmethod
    def _mime_for(envs, sha):
        for e in envs:
            a = (e["body"] or {}).get("asset") if isinstance(e.get("body"), dict) else None
            if isinstance(a, dict) and a.get("$asset") == sha:
                return e["body"].get("mime", "application/octet-stream")
        return "application/octet-stream"

    @staticmethod
    def _field(parts, key, auto=False, must=False, fname=""):
        v = parts.get(key, (None, b""))[1].decode("utf-8", "ignore").strip().lower()
        if v in ("", "auto") and auto:
            v = Path(fname).suffix[1:].lower()
        if must and not v:
            raise ValueError(f"missing field {key}")
        if v not in ("docx", "xlsx", "pptx", "csv", "md", "bmo"):
            raise ValueError(f"bad format {v!r}")
        return v

    def api_render(self):
        fname, data, _ = self._upload()
        model = render_load(data)
        kinds = {e["kind"] for e in model["envs"]}
        files = []
        if "sheet" in kinds or "workbook" in kinds:
            files.append({"name": Path(fname).stem + ".html", "mime": "text/html",
                          "text": sheet_html.render_workbook(model, Path(fname).stem)})
        elif "slide" in kinds or "deck" in kinds:
            for i, s in enumerate(render_svg.render_deck(model, Path(fname).stem), 1):
                files.append({"name": f"{Path(fname).stem}-{i}.svg",
                              "mime": "image/svg+xml", "text": s})
        elif "canvas" in kinds:
            files.append({"name": Path(fname).stem + ".svg", "mime": "image/svg+xml",
                          "text": render_svg.render_canvas_page(model, Path(fname).stem)})
        else:
            files.append({"name": Path(fname).stem + ".svg", "mime": "image/svg+xml",
                          "text": render_svg.render_document(model, Path(fname).stem)})
        self._json(200, {"files": files})

    def api_calc(self):
        fname, data, parts = self._upload()
        want = parts.get("sheet", (None, b""))[1].decode("utf-8", "ignore").strip()
        _, envs = model_envs(data)
        sheets = {}
        for e in envs:
            if e["kind"] == "sheet":
                sheets[e["body"].get("name", "?")] = e["body"].get("cells", {})
        if want and want not in sheets:
            raise ValueError(f"no sheet {want!r} (have: {sorted(sheets)})")
        out = {}
        for name, cells in sheets.items():
            if want and name != want:
                continue
            out[name] = evaluate_sheet(cells, sheets, name)
        self._json(200, {"sheets": out})


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="BMO Studio — local web UI")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"BMO Studio at {url}  (Ctrl+C to stop; local only)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
