"""BMO Viewer — native desktop app (tkinter, zero dependencies).
Open any .bmo: documents (RTL included), tables, pictures, sheets with
live formulas, slides. No browser, no Office needed.
Run:  python apps/desktop/viewer.py [file.bmo]
"""
import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if getattr(sys, "frozen", False):
    # PyInstaller one-file: kit dirs are bundled as data under _MEIPASS.
    _base = Path(sys._MEIPASS)  # noqa: SLF001
    sys.path.insert(0, str(_base / "kit_conformance"))
    sys.path.insert(0, str(_base / "kit_renderer"))
    sys.path.insert(0, str(_base / "kit_converters"))
    sys.path.insert(0, str(_base / "kit_calc"))
    sys.path.insert(0, str(_base / "kit_vectors"))
else:
    sys.path.insert(0, str(ROOT / "tests" / "conformance"))
    sys.path.insert(0, str(ROOT / "renderer" / "python"))
    sys.path.insert(0, str(ROOT / "converters" / "python"))
    sys.path.insert(0, str(ROOT / "calc" / "python"))
    sys.path.insert(0, str(ROOT / "tests" / "vectors"))
from bmo_reader import BmoErr, open_file  # noqa: E402
from model_check import collect_refs, parse_envelope, validate_structure  # noqa: E402
from domains_check import validate_bodies  # noqa: E402
from bmo_render.load import load as render_load  # noqa: E402
from bmo_calc.evaluate import evaluate_sheet  # noqa: E402
from bmo_ooxml.container import cbor_encode  # noqa: E402
from bmo_ooxml.common import sniff_script  # noqa: E402

BMU_PX = 0.0037795

# ---------------------------------------------------------------- pure view-model (no Tk: unit-tested)

def ordered_children(model, oid):
    out = [e for e in model["children"].get(oid, [])]
    out.sort(key=lambda e: (e["index"] if e["index"] is not None else e["oid"]))
    return out

def flow_items(model, parent_oid):
    """Document order, sections/pages transparently flattened."""
    items = []
    for e in ordered_children(model, parent_oid):
        if e["kind"] in ("section", "page"):
            items.extend(flow_items(model, e["oid"]))
        elif e["kind"] in ("paragraph", "table", "image", "note", "text",
                           "canvas", "group", "shape"):
            items.append(e)
    return items

def table_grid(model, table_oid):
    rows = []
    for r in ordered_children(model, table_oid):
        if r["kind"] != "row":
            continue
        rows.append([c for c in ordered_children(model, r["oid"]) if c["kind"] == "cell"])
    return rows

def sheet_grid(cells, merges):
    """(maxr, maxc, grid, covered, spans) — 1-based (row, col)."""
    def split(addr):
        m = re.match(r"^([A-Za-z]{1,3})([0-9]+)$", addr)
        col = 0
        for ch in m.group(1).upper():
            col = col * 26 + (ord(ch) - 64)
        return int(m.group(2)), col
    grid, maxr, maxc = {}, 0, 0
    for addr, cell in cells.items():
        r, c = split(addr)
        grid[(r, c)] = (addr, cell)
        maxr, maxc = max(maxr, r), max(maxc, c)
    covered, spans = set(), {}
    for mg in merges:
        r1, c1, r2, c2 = mg
        spans[(r1 + 1, c1 + 1)] = (r2 - r1 + 1, c2 - c1 + 1)
        for rr in range(r1 + 1, r2 + 2):
            for cc in range(c1 + 1, c2 + 2):
                if (rr, cc) != (r1 + 1, c1 + 1):
                    covered.add((rr, cc))
    return maxr, maxc, grid, covered, spans

def deck_slides(model):
    out = [e for e in model["envs"] if e["kind"] == "slide"]
    out.sort(key=lambda e: (e["index"] if e["index"] is not None else e["oid"]))
    return out

def run_style(by_id, body):
    st = body.get("style") if isinstance(body, dict) else None
    if isinstance(st, dict) and "$ref" in st:
        t = by_id.get(st["$ref"], {})
        rm = (t.get("body") or {}).get("run", {}) if isinstance(t, dict) else {}
        return rm if isinstance(rm, dict) else {}
    return {}

def list_marker(model, state, body):
    li = body.get("list") if isinstance(body, dict) else None
    if not (isinstance(li, dict) and isinstance(li.get("id"), str)):
        return ""
    n = state.get(li["id"], 0) + 1
    state[li["id"]] = n
    det = next((e for e in model["envs"] if e["kind"] == "list-def"
                and e["body"].get("id") == li["id"]), None)
    style = (det or {}).get("body", {}).get("style", "bullet")
    return "•  " if style == "bullet" else f"{n}. "

# ---------------------------------------------------------------- edit planning (pure: unit-tested)
# Documented UI contract: editing a paragraph keeps the FIRST run's
# formatting; extra runs are removed. Nothing is rewritten in place —
# every save is a new CoW revision (txn+1) validated before touching disk.

def plan_paragraph_edit(para_oid, run_envs, new_text, new_run_oid, encode, sniff):
    """run_envs: [(oid, env_dict)] in sibling order.
    Returns (updated, new, delete) with ENCODED payloads."""
    if not run_envs:
        env = {"kind": "run", "parent": para_oid, "index": 0,
               "body": {"text": new_text, "script": sniff(new_text)}}
        return {}, {new_run_oid: encode(env)}, []
    first_oid = run_envs[0][0]
    first_env = dict(run_envs[0][1])
    body = dict(first_env.get("body", {}))
    body["text"] = new_text
    if new_text:
        body["script"] = sniff(new_text)
    first_env["body"] = body
    return {first_oid: encode(first_env)}, {}, [oid for oid, _ in run_envs[1:]]

def plan_paragraph_delete(para_oid, run_oids):
    return [para_oid] + list(run_oids)

def find_referrers(envs, oids):
    """Objects (other than targets) holding $refs to oids -> blockers."""
    targets = set(oids)
    blockers = set()
    for e in envs:
        if e["oid"] in targets:
            continue
        refs, assets = [], []
        collect_refs(e.get("body"), refs, assets)
        collect_refs(e.get("ext"), refs, assets)
        if targets & set(refs):
            blockers.add(e["oid"])
    return blockers

def plan_sheet_cell(sheet_body, addr, value, formula, extract):
    """Returns a NEW sheet body dict with the cell set. Raises ValueError."""
    import re as _re
    a = (addr or "").replace("$", "")
    if not _re.match(r"^[A-Za-z]{1,3}[0-9]{1,7}$", a):
        raise ValueError(f"bad address {addr!r}")
    if isinstance(value, float) and value != value:
        raise ValueError("NaN is not storable")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    body = {k: (dict(v) if isinstance(v, dict) else v) for k, v in sheet_body.items()}
    cells = dict(body.get("cells", {}))
    key = a.upper()
    if value is None and not formula:
        cells.pop(key, None)  # clearing = sparse omission
    else:
        cell = {"v": value}
        if formula:
            deps, _, _ = extract(formula)
            cell["f"] = formula
            cell["deps"] = deps
        cells[key] = cell
    body["cells"] = cells
    return body

def plan_slide_body(slide_body, notes, tr_type, dur_ms):
    body = dict(slide_body)
    if notes is not None:
        if notes:
            body["notes"] = notes
        else:
            body.pop("notes", None)
    if tr_type is not None:
        if tr_type == "none":
            body.pop("transition", None)
        else:
            tr = {"type": tr_type}
            if isinstance(dur_ms, int) and dur_ms > 0:
                tr["durMs"] = dur_ms
            body["transition"] = tr
    return body

def env_for_write(p):
    """Parsed envelope -> writable envelope dict (defaults omitted so
    re-encoded bytes stay canonical). Shared by all edit flows."""
    d = {"kind": p["kind"], "parent": p["parent"], "index": p["index"],
         "body": p["body"]}
    if p.get("role"):
        d["role"] = p["role"]
    if p.get("rev"):
        d["rev"] = p["rev"]
    if p.get("ext") is not None:
        d["ext"] = p["ext"]
    return d

def clone_subtree(envs, root_oid, start_oid):
    """Deep-copy root + all descendants to fresh oids.
    Parent links INSIDE the set are remapped; $refs are kept verbatim
    (shared styles/layouts stay shared). Indexes preserved (caller sets
    the root's own index for its new slot).
    Returns ({new_oid: env_dict}, {old_oid: new_oid})."""
    by_id = {e["oid"]: e for e in envs}
    if root_oid not in by_id:
        raise ValueError(f"no object #{root_oid}")
    Desc = [root_oid]
    seen, queue = {root_oid}, [root_oid]
    while queue:
        cur = queue.pop(0)
        for e in envs:
            if e["parent"] == cur and e["oid"] not in seen:
                seen.add(e["oid"])
                queue.append(e["oid"])
                Desc.append(e["oid"])
    mapping, new_envs, nxt = {}, {}, start_oid
    for old in Desc:
        mapping[old] = nxt
        nxt += 1
    for old in Desc:
        src = by_id[old]
        env = env_for_write(src)
        if src["parent"] in mapping:
            env["parent"] = mapping[src["parent"]]
        new_envs[mapping[old]] = env
    return new_envs, mapping

def plan_move_sibling(envs, oid, delta, encode, kind=None):
    """Swap oid with a sibling (same parent) by delta (-1 up, +1 down).
    With kind set, moves among same-kind siblings only (e.g. paragraphs
    leapfrog over styles). Returns {oid: payload} for both movers.
    Raises ValueError at the ends."""
    me = next((e for e in envs if e["oid"] == oid), None)
    if me is None:
        raise ValueError(f"no object #{oid}")
    sibs = sorted([e for e in envs if e["parent"] == me["parent"] and e["index"] is not None
                   and (kind is None or e["kind"] == kind)],
                  key=lambda e: e["index"])
    i = next(i for i, e in enumerate(sibs) if e["oid"] == oid)
    j = i + delta
    if j < 0 or j >= len(sibs):
        raise ValueError("already at the edge")
    out = {}
    ea, eb = sibs[i], sibs[j]
    na, nb = env_for_write(ea), env_for_write(eb)
    na["index"], nb["index"] = eb["index"], ea["index"]
    out[ea["oid"]] = encode(na)
    out[eb["oid"]] = encode(nb)
    return out

def count_refs(envs, oid):
    """How many objects $ref this oid (for shared-style cloning)."""
    n = 0
    for e in envs:
        refs, assets = [], []
        collect_refs(e.get("body"), refs, assets)
        collect_refs(e.get("ext"), refs, assets)
        n += sum(1 for r in refs if r == oid)
    return n

def descendants(envs, root_oid):
    """All descendant oids including root (BFS over parent links)."""
    seen, queue, out = {root_oid}, [root_oid], []
    while queue:
        cur = queue.pop(0)
        out.append(cur)
        for e in envs:
            if e["parent"] == cur and e["oid"] not in seen:
                seen.add(e["oid"])
                queue.append(e["oid"])
    return out

def plan_compact_siblings(envs, parent_oid, removed, encode):
    """After deleting `removed`, re-densify surviving explicit siblings (R4).
    Returns {oid: payload} for changed siblings only (often empty)."""
    gone = set(removed)
    sibs = sorted([e for e in envs if e["parent"] == parent_oid and e["oid"] not in gone
                   and e["index"] is not None], key=lambda e: e["index"])
    out = {}
    for i, s in enumerate(sibs):
        if s["index"] != i:
            env = env_for_write(s)
            env["index"] = i
            out[s["oid"]] = encode(env)
    return out

def plan_run_style(run_env, style_env_or_nil, refcount, bold, italic,
                   style_parent, style_index, new_style_oid, encode):
    """Apply bold/italic to a run's style.
    - No style yet -> create one (NEW).
    - Shared style (refcount>1) -> clone (NEW), original untouched.
    - Exclusive style -> update in place (UPDATED).
    Returns (run_payload, updated, new, style_ref_oid)."""
    rm = {}
    if style_env_or_nil is not None:
        rm = dict((style_env_or_nil.get("body") or {}).get("run", {}) or {})
    rm["b"] = 1 if bold else 0
    rm["i"] = 1 if italic else 0
    if not rm["b"]:
        rm.pop("b", None)
    if not rm["i"]:
        rm.pop("i", None)
    run = dict(run_env)
    body = dict(run.get("body", {}))
    if style_env_or_nil is None or refcount > 1:
        base = {"kind": "style", "parent": style_parent, "index": style_index,
                "body": {"name": "custom", "run": rm}}
        if style_env_or_nil is not None:
            base["body"] = dict(style_env_or_nil.get("body", {}))
            base["body"]["run"] = rm
        body["style"] = {"$ref": new_style_oid}
        return encode({**run, "body": body}), {}, {new_style_oid: encode(base)}, new_style_oid
    upd = dict(style_env_or_nil)
    upd_body = dict(upd.get("body", {}))
    upd_body["run"] = rm
    upd["body"] = upd_body
    body["style"] = {"$ref": style_env_or_nil["oid"]}
    return encode({**run, "body": body}), {style_env_or_nil["oid"]: encode(upd)}, {}, style_env_or_nil["oid"]

def load_document(path):
    """(model, report) or raises FriendlyError."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise FriendlyError("Cannot read that file.")
    try:
        model = render_load(raw)
    except BmoErr as e:
        code = str(e)
        if code == "UPGRADE_REQUIRED":
            raise FriendlyError(
                "This file needs a newer/other BMO build (unsupported "
                "codec or format feature: UPGRADE_REQUIRED).")
        if code == "NEED_KEY":
            raise FriendlyError(
                "This file is encrypted; a content key is required "
                "(NEED_KEY).\n"
                "Key entry is not available in this build yet.")
        raise FriendlyError(
            f"Not a valid .bmo file ({type(e).__name__}).\n"
            "Renaming never converts — convert the original Word/Excel file instead.")
    except Exception as e:
        raise FriendlyError(
            f"Not a valid .bmo file ({type(e).__name__}).\n"
            "Renaming never converts — convert the original Word/Excel file instead.")
    return model

class FriendlyError(Exception):
    pass

# ---------------------------------------------------------------- Tk UI (imported lazily so tests stay headless)

def run_app(path=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    import tkinter.font as tkfont

    root = tk.Tk()
    root.title("BMO Editor")
    root.geometry("950x700")
    state = {"model": None, "path": None, "raw": None, "slide": 0, "images": [],
             "sel_para": None, "mode": None}

    menubar = tk.Menu(root)
    filemenu = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="File", menu=filemenu)
    status = tk.StringVar(value="Open a .bmo file to begin.")
    body = tk.Frame(root)
    body.pack(fill="both", expand=True)

    def clear():
        for w in body.winfo_children():
            w.destroy()

    def set_title():
        p = state["path"]
        root.title(f"{Path(p).name} — BMO Editor" if p else "BMO Editor")

    def full_check(raw):
        """Container + structure + domains. Raises FriendlyError / BmoErr."""
        from bmo_reader import append_commit as _ac, read_chunk as _rc  # noqa
        f = open_file(raw)
        got, ids, tables = [], set(), []
        for oid, ct, fo, tl in f["entries"]:
            ids.add(oid)
            if ct == 0x0010:
                got.append((oid, _rc(raw, fo, len(raw))[1]))
        rep = validate_structure(got, ids, tables)
        envs = [parse_envelope(o, x) for o, x in got]
        validate_bodies(envs)
        return f, rep, envs

    def commit(updated=None, new=None, delete=()):
        """Append-only save: build txn+1 in memory, fully validate, then write.
        Nothing touches disk unless the new revision is proven valid."""
        from bmo_reader import append_commit as _ac
        new_bytes = _ac(state["raw"], updated or {}, dict(new or {}), delete)
        try:
            full_check(new_bytes)
        except (BmoErr, Exception) as e:
            messagebox.showerror("BMO Editor",
                                 f"Save refused — new revision invalid:\n{type(e).__name__}: {e}")
            return False
        Path(state["path"]).write_bytes(new_bytes)
        show(state["path"])
        return True

    def next_oid():
        return max([e["oid"] for e in state["model"]["envs"]] or [0]) + 1

    def show(path):
        try:
            model = load_document(path)
        except FriendlyError as e:
            messagebox.showerror("BMO Editor", str(e))
            return
        keep_slide = state.get("slide", 0) if state.get("path") == str(path) else 0
        state["model"], state["path"] = model, str(path)
        state["raw"] = Path(path).read_bytes()
        state["slide"] = keep_slide
        state["sel_para"] = None
        set_title()
        kinds = {e["kind"] for e in model["envs"]}
        f = model["file"]
        status.set(f"{len(model['envs'])} objects · txn {f['footer']['txn']} · {Path(path).stat().st_size} bytes")
        if "sheet" in kinds or "workbook" in kinds:
            show_sheets()
        elif "slide" in kinds or "deck" in kinds:
            show_slide()
        else:
            show_document()

    # -- document view --
    def show_document():
        clear()
        state["mode"] = "document"
        model = state["model"]
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x")
        sel_lbl = ttk.Label(toolbar, text="Click a paragraph to select it.")
        sel_lbl.pack(side="left", padx=6)
        btn_edit = ttk.Button(toolbar, text="Edit Text…", state="disabled")
        btn_del = ttk.Button(toolbar, text="Delete", state="disabled")
        btn_add = ttk.Button(toolbar, text="Add Paragraph…")
        btn_up = ttk.Button(toolbar, text="Move Up", state="disabled")
        btn_down = ttk.Button(toolbar, text="Move Down", state="disabled")
        btn_add.pack(side="right", padx=2)
        btn_down.pack(side="right", padx=2)
        btn_up.pack(side="right", padx=2)
        btn_del.pack(side="right", padx=2)
        btn_edit.pack(side="right", padx=2)
        txt = tk.Text(body, wrap="word", font=("Segoe UI", 11), padx=18, pady=14)
        sb = ttk.Scrollbar(body, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_config("sel", background="#cfe3ff")
        tagid = [0]

        def tag(**kw):
            tagid[0] += 1
            name = f"t{tagid[0]}"
            txt.tag_config(name, **kw)
            return name

        def para_tags(p):
            b = p["body"]
            kw = {}
            rm = run_style(model["by_id"], b)
            if rm.get("b"):
                kw["font"] = tkfont.Font(txt, txt.cget("font"), weight="bold")
            a = b.get("align", "start")
            kw["justify"] = {"center": "center"}.get(a, "right" if a in ("right", "end") or b.get("dir") == "rtl" else "left")
            return kw

        markers = {}
        roots = [e for e in model["envs"] if e["parent"] == 0]
        items = flow_items(model, roots[0]["oid"]) if roots else []
        state["doc_marks"] = []
        for e in items:
            b = e["body"]
            if e["kind"] in ("paragraph", "note", "text"):
                txt.mark_set(f"pp{e['oid']}", "end-1c")
                txt.mark_gravity(f"pp{e['oid']}", "left")
                state["doc_marks"].append(e["oid"])
            if e["kind"] == "paragraph":
                pre = list_marker(model, markers, b)
                kw = para_tags(e)
                t = tag(**kw) if kw else None
                runs = [c for c in ordered_children(model, e["oid"]) if c["kind"] == "run"]
                if pre:
                    txt.insert("end", pre, (t,) if t else ())
                for r in runs:
                    rb, rm = r["body"], run_style(model["by_id"], r["body"])
                    fnt = tkfont.Font(txt, txt.cget("font"))
                    if rm.get("b"):
                        fnt.configure(weight="bold")
                    if rm.get("i"):
                        fnt.configure(slant="italic")
                    if isinstance(rm.get("size"), int):
                        fnt.configure(size=rm["size"])
                    col = f'#{rm["color"]}' if isinstance(rm.get("color"), str) and len(rm["color"]) == 6 else None
                    rt = tag(font=fnt, **({"foreground": col} if col else {}),
                             **(kw if kw and "justify" in kw else {}))
                    if kw.get("justify"):
                        rt = tag(font=fnt, justify=kw["justify"],
                                 **({"foreground": col} if col else {}))
                    txt.insert("end", rb.get("text", ""), rt)
                txt.insert("end", "\n")
            elif e["kind"] in ("note", "text"):
                txt.insert("end", (b.get("text", "") or "") + "\n")
            elif e["kind"] == "table":
                frame = ttk.Frame(txt, borderwidth=1, relief="solid")
                for ri, cells in enumerate(table_grid(model, e["oid"])):
                    for ci, c in enumerate(cells):
                        text = "\n".join(
                            "".join(rr["body"].get("text", "")
                                     for rr in ordered_children(model, pp["oid"])
                                     if rr["kind"] == "run")
                            for pp in ordered_children(model, c["oid"])
                            if pp["kind"] == "paragraph") or " "
                        ttk.Label(frame, text=text, borderwidth=1, relief="solid",
                                  padding=4, wraplength=220).grid(row=ri, column=ci, sticky="nsew")
                txt.window_create("end", window=frame)
                txt.insert("end", "\n")
            elif e["kind"] == "image":
                show_image_block(txt, e)
            elif e["kind"] in ("canvas", "group", "shape"):
                pass  # drawing objects render on slides/canvases; skipped inline
        txt.configure(state="disabled")

        def runs_of(oid):
            out = []
            for r in ordered_children(model, oid):
                if r["kind"] == "run":
                    out.append((r["oid"], env_for_write(r)))
            return out

        def select_para(oid):
            state["sel_para"] = oid
            txt.tag_remove("sel", "1.0", "end")
            for b in (btn_edit, btn_del, btn_up, btn_down):
                b.config(state="disabled")
            if oid is None:
                sel_lbl.config(text="Click a paragraph to select it.")
                return
            i = state["doc_marks"].index(oid)
            start = f"pp{oid}"
            end = f"pp{state['doc_marks'][i + 1]}" if i + 1 < len(state["doc_marks"]) else "end-1c"
            txt.tag_add("sel", start, end)
            sel_lbl.config(text=f"Paragraph #{oid} selected")
            for _b in (btn_edit, btn_del, btn_up, btn_down):
                _b.config(state="normal")

        def on_click(ev):
            try:
                idx = txt.index(f"@{ev.x},{ev.y}")
            except Exception:
                return
            best = None
            for oid in state["doc_marks"]:
                try:
                    if txt.compare(f"pp{oid}", "<=", idx):
                        best = oid
                except Exception:
                    break
            select_para(best)

        txt.bind("<ButtonRelease-1>", on_click)

        def ask_paragraph(title, initial, bold, italic, note):
            """Returns (text, bold, italic) or None."""
            win = tk.Toplevel(root)
            win.title(title)
            win.geometry("560x340")
            win.transient(root)
            ttk.Label(win, text=note, wraplength=520).pack(pady=4)
            box = tk.Text(win, wrap="word", font=("Segoe UI", 11))
            box.pack(fill="both", expand=True, padx=8, pady=4)
            box.insert("1.0", initial)
            bv, iv = tk.BooleanVar(value=bold), tk.BooleanVar(value=italic)
            fmt = ttk.Frame(win)
            fmt.pack(fill="x", padx=8)
            ttk.Checkbutton(fmt, text="Bold", variable=bv).pack(side="left")
            ttk.Checkbutton(fmt, text="Italic", variable=iv).pack(side="left", padx=12)
            ttk.Label(fmt, text="(applies to this paragraph's first run; shared styles are cloned, never changed for others)"
                      ).pack(side="left", padx=8)
            result = {"v": None}
            bar = ttk.Frame(win)
            bar.pack(fill="x", pady=4)

            def ok():
                result["v"] = (box.get("1.0", "end-1c"), bv.get(), iv.get())
                win.destroy()
            ttk.Button(bar, text="Save", command=ok).pack(side="right", padx=8)
            ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right")
            win.grab_set()
            root.wait_window(win)
            return result["v"]

        def ask_text(title, initial, note):
            r = ask_paragraph(title, initial, False, False, note)
            return None if r is None else r[0]

        def edit_selected():
            oid = state["sel_para"]
            if oid is None:
                return
            para = model["by_id"][oid]
            if para["kind"] != "paragraph":
                messagebox.showinfo("BMO Editor", "Only paragraphs are text-edited here.")
                return
            runs = runs_of(oid)
            current = "".join(model["by_id"][r]["body"].get("text", "") for r, _ in runs)
            first_rm = run_style(model["by_id"], model["by_id"][runs[0][0]]["body"]) if runs else {}
            res = ask_paragraph(f"Edit paragraph #{oid}", current,
                                bool(first_rm.get("b")), bool(first_rm.get("i")),
                                "Text + first-run formatting. Extra runs are merged.")
            if res is None:
                return
            new_text, bb, ii = res
            # oid budget: at most one new run (max+1) and one new style (max+2);
            # gaps are fine — ids are never reused, only never duplicated.
            budget = next_oid()
            if runs:
                first_oid = runs[0][0]
                base = dict(runs[0][1])
                bbody = dict(base.get("body", {}))
                bbody["text"] = new_text
                if new_text:
                    bbody["script"] = sniff_script(new_text)
                base["body"] = bbody
                first_env, first_is_new = base, False
                updated, new, delete = {}, {}, [o for o, _ in runs[1:]]
                style_oid = budget
            else:
                if not new_text:
                    return
                first_oid = budget
                first_env = {"kind": "run", "parent": oid, "index": 0,
                             "body": {"text": new_text, "script": sniff_script(new_text)}}
                first_is_new = True
                updated, new, delete = {}, {}, []
                style_oid = budget + 1
            # 2) style plan on the (possibly new) first run
            orig = model["by_id"].get(first_oid, {})
            st = (orig.get("body") or {}).get("style") if isinstance(orig.get("body"), dict) else None
            style_env = model["by_id"].get(st["$ref"]) if isinstance(st, dict) and "$ref" in st else None
            rc = count_refs(model["envs"], style_env["oid"]) if style_env else 0
            roots3 = [e for e in model["envs"] if e["parent"] == 0]
            sroot = roots3[0]["oid"] if roots3 else oid
            sibs = [e["index"] or 0 for e in ordered_children(model, sroot)]
            sidx = (max(sibs) + 1) if sibs else 0
            run_pay, s_upd, s_new, _sref = plan_run_style(
                first_env, style_env, rc, bb, ii, sroot, sidx, style_oid, cbor_encode)
            if first_is_new:
                new[first_oid] = run_pay
            else:
                updated[first_oid] = run_pay
            updated.update(s_upd)
            new.update(s_new)
            if commit(updated, new, delete):
                status.set(f"saved paragraph #{oid} · txn {state['model']['file']['footer']['txn']}")

        def add_paragraph():
            from tkinter import simpledialog
            model2 = state["model"]
            sel = state["sel_para"]
            if sel is not None and model2["by_id"].get(sel, {}).get("kind") == "paragraph":
                ref = model2["by_id"][sel]
                parent, index = ref["parent"], (ref["index"] or 0) + 1
            else:
                roots2 = [e for e in model2["envs"] if e["parent"] == 0]
                if not roots2:
                    return
                parent = roots2[0]["oid"]
                sibs = [e["index"] or 0 for e in ordered_children(model2, parent)]
                index = (max(sibs) + 1) if sibs else 0
            text = ask_text("New paragraph", "", "Empty text creates an empty paragraph.")
            if text is None:
                return
            p_oid, oid = next_oid(), next_oid() + 1
            objs = {p_oid: cbor_encode({"kind": "paragraph", "parent": parent,
                                        "index": index, "body": {}})}
            if text:
                objs[oid] = cbor_encode({"kind": "run", "parent": p_oid, "index": 0,
                                         "body": {"text": text, "script": sniff_script(text)}})
            # inserting mid-list shifts later siblings (R4: indexes stay dense)
            bumped = {}
            for sib in ordered_children(model2, parent):
                if (sib["index"] or 0) >= index:
                    env = env_for_write(sib)
                    env["index"] = (sib["index"] or 0) + 1
                    bumped[sib["oid"]] = cbor_encode(env)
            if commit(bumped, objs):
                status.set(f"added paragraph #{p_oid} · txn {state['model']['file']['footer']['txn']}")

        def delete_selected():
            oid = state["sel_para"]
            if oid is None:
                return
            para = model["by_id"].get(oid)
            if not para or para["kind"] != "paragraph":
                messagebox.showinfo("BMO Editor", "Select a paragraph first.")
                return
            runs = [r for r in ordered_children(model, oid) if r["kind"] == "run"]
            doomed = [oid] + [r["oid"] for r in runs]
            blockers = find_referrers(model["envs"], doomed)
            if blockers:
                messagebox.showerror("BMO Editor",
                                     "Cannot delete: referenced by object(s) "
                                     + ", ".join(f"#{b}" for b in sorted(blockers)))
                return
            if not messagebox.askyesno("BMO Editor", f"Delete paragraph #{oid} and its runs?"):
                return
            lite = [{"oid": e["oid"], "kind": e["kind"], "parent": e["parent"],
                     "index": e["index"], "body": e["body"]} for e in model["envs"]]
            bumped = plan_compact_siblings(lite, para["parent"], doomed, cbor_encode)
            if commit(bumped, {}, doomed):
                status.set(f"deleted #{oid} · txn {state['model']['file']['footer']['txn']}")

        def move_selected(delta):
            oid = state["sel_para"]
            if oid is None:
                return
            if model["by_id"].get(oid, {}).get("kind") != "paragraph":
                return
            try:
                upd = plan_move_sibling(model["envs"], oid, delta, cbor_encode,
                                        kind="paragraph")
            except ValueError:
                status.set("already at the top/bottom")
                return
            if commit(upd):
                # commit() already rebuilt the view; restore the highlight
                if oid in (state.get("doc_marks") or []):
                    select_para(oid)
                status.set(f"moved #{oid} · txn {state['model']['file']['footer']['txn']}")

        btn_edit.config(command=edit_selected)
        btn_del.config(command=delete_selected)
        btn_add.config(command=add_paragraph)
        btn_up.config(command=lambda: move_selected(-1))
        btn_down.config(command=lambda: move_selected(+1))
        if state["sel_para"] in (state.get("doc_marks") or []):
            select_para(state["sel_para"])

    def show_image_block(txt, e):
        b = e["body"]
        sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
        blob = state["model"]["assets"].get(sha) if isinstance(sha, bytes) else None
        if blob:
            try:
                img = tk.PhotoImage(data=base64.b64encode(blob).decode("ascii"))
                state["images"].append(img)  # keep a reference!
                txt.window_create("end", window=ttk.Label(txt, image=img))
                txt.insert("end", "\n")
                return
            except Exception:
                pass
        txt.insert("end", f"[image {b.get('mime', '?')}]" + "\n")

    # -- sheets --
    def show_sheets():
        clear()
        state["mode"] = "sheets"
        model = state["model"]
        bar = ttk.Frame(body)
        bar.pack(fill="x")
        ttk.Label(bar, text="Double-click a row or press Set Cell… (empty value clears the cell).").pack(side="left", padx=6)
        btn_cell = ttk.Button(bar, text="Set Cell…")
        btn_cell.pack(side="right", padx=4)
        btn_sadd = ttk.Button(bar, text="Add Sheet…")
        btn_sadd.pack(side="right", padx=4)
        btn_sren = ttk.Button(bar, text="Rename…")
        btn_sren.pack(side="right", padx=4)
        btn_sdel = ttk.Button(bar, text="Delete Sheet")
        btn_sdel.pack(side="right", padx=4)
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)
        sheet_oid_by_tab = {}
        formbar = ttk.Label(body, text="", anchor="w", padding=4)
        formbar.pack(fill="x")
        for e in model["envs"]:
            if e["kind"] != "sheet":
                continue
            name = e["body"].get("name", "?")
            sheet_oid_by_tab[name] = e["oid"]
            cells = e["body"].get("cells", {})
            maxr, maxc, grid, covered, spans = sheet_grid(
                cells, e["body"].get("merges", []))
            frame = ttk.Frame(nb)
            nb.add(frame, text=name)
            cols = [f"C{c}" for c in range(1, maxc + 1)] or ["C1"]
            tv = ttk.Treeview(frame, columns=cols, show="headings", height=22)
            for ci, cn in enumerate(cols, 1):
                cname = ""
                n = ci
                while n:
                    n, r = divmod(n - 1, 26)
                    cname = chr(65 + r) + cname
                tv.heading(cn, text=cname)
                tv.column(cn, width=110)
            letters = {}
            for ci in range(1, maxc + 1):
                cname = ""
                n = ci
                while n:
                    n, r = divmod(n - 1, 26)
                    cname = chr(65 + r) + cname
                letters[ci] = cname
            try:
                computed = evaluate_sheet(cells, {x["body"].get("name", "?"): x["body"].get("cells", {})
                                                for x in model["envs"] if x["kind"] == "sheet"}, name)
            except Exception:
                computed = {}
            for r in range(1, (maxr or 1) + 1):
                vals = []
                for c in range(1, (maxc or 1) + 1):
                    if (r, c) in covered:
                        vals.append("")
                        continue
                    addr = f"{letters[c]}{r}"
                    v = computed.get(addr, (grid.get((r, c)) or (None, {}))[1].get("v"))
                    vals.append("" if v is None else ("TRUE" if v is True else ("FALSE" if v is False else str(v))))
                tv.insert("", "end", text=str(r), values=vals)
            vsb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=vsb.set)
            tv.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            def onsel(_ev=None, _tv=tv, _cells=cells, _name=name):
                sel = _tv.selection()
                if not sel:
                    return
                row = _tv.index(sel[0]) + 1
                col = (_tv.identify_column(_tv.winfo_pointerx() - _tv.winfo_rootx()) or "#1").lstrip("#")
                try:
                    ci = int(col)
                    addr = f"{letters[ci]}{row}"
                    cell = _cells.get(addr, {})
                    formbar.config(text=f"{addr}: f={cell.get('f', '')} v={cell.get('v', '')}")
                except Exception:
                    pass
            tv.bind("<<TreeviewSelect>>", onsel)

            def on_double(ev, _tv=tv, _letters=letters):
                row = _tv.identify_row(ev.y)
                col = _tv.identify_column(ev.x)
                if not (row and col and col.startswith("#")):
                    return
                try:
                    ci = int(col[1:])
                    ri = _tv.index(row) + 1
                    addr = f"{_letters[ci]}{ri}"
                    oid, env = current_sheet()
                    cur = (env["body"].get("cells", {}) if env else {}).get(addr, {})
                    set_cell_dialog(addr=addr,
                                    value="" if cur.get("v") is None else str(cur.get("v", "")),
                                    formula=cur.get("f", ""))
                except Exception:
                    pass

            tv.bind("<Double-1>", on_double)

        def current_sheet():
            try:
                name = nb.tab(nb.select(), "text")
            except Exception:
                return None, None
            oid = sheet_oid_by_tab.get(name)
            env = model["by_id"].get(oid) if oid else None
            return (oid, env) if env else (None, None)

        def set_cell_dialog(_tv=None, addr=None, value="", formula=""):
            from bmo_ooxml.csvmd import _sniff
            from bmo_ooxml.xlsx import extract_refs
            oid, env = current_sheet()
            if env is None:
                messagebox.showinfo("BMO Editor", "No sheet open.")
                return
            win = tk.Toplevel(root)
            win.title("Set cell")
            win.geometry("380x220")
            win.transient(root)
            ttk.Label(win, text="Cell (e.g. B2):").pack(anchor="w", padx=8)
            e_addr = ttk.Entry(win)
            e_addr.pack(fill="x", padx=8)
            e_addr.insert(0, addr or "")
            ttk.Label(win, text="Value (empty clears the cell):").pack(anchor="w", padx=8)
            e_val = ttk.Entry(win)
            e_val.pack(fill="x", padx=8)
            e_val.insert(0, value)
            ttk.Label(win, text="Formula (optional, e.g. A1*2):").pack(anchor="w", padx=8)
            e_f = ttk.Entry(win)
            e_f.pack(fill="x", padx=8)
            e_f.insert(0, formula)
            result = {"ok": False}

            def ok():
                a = e_addr.get().strip()
                raw_v = e_val.get()
                f = e_f.get().strip()
                try:
                    v, _blank = _sniff(raw_v)
                    if not raw_v.strip() and not f:
                        v = None  # clear
                    new_body = plan_sheet_cell(env["body"], a, v, f or None, extract_refs)
                except ValueError as ex:
                    messagebox.showerror("BMO Editor", str(ex))
                    return
                new_env = env_for_write(env)
                new_env["body"] = new_body
                if commit({oid: cbor_encode(new_env)}):
                    status.set(f"set {a.upper()} · txn {state['model']['file']['footer']['txn']}")
                    try:
                        nb.select(nb.tabs()[list(sheet_oid_by_tab).index(
                            next(n for n, o in sheet_oid_by_tab.items() if o == oid))])
                    except Exception:
                        pass
                result["ok"] = True
                win.destroy()
            ttk.Button(win, text="Save", command=ok).pack(side="right", padx=8, pady=8)
            ttk.Button(win, text="Cancel", command=win.destroy).pack(side="right", pady=8)
            win.grab_set()

        btn_cell.config(command=lambda: set_cell_dialog())

        def current_sheet_oid():
            oid, env = current_sheet()
            return oid

        def wb_of():
            wb = next((e for e in model["envs"] if e["kind"] == "workbook"), None)
            return wb

        def add_sheet():
            from tkinter import simpledialog
            wb = wb_of()
            if wb is None:
                return
            name = simpledialog.askstring("New sheet", "Sheet name (1-31 chars, unique):", parent=root)
            if name is None:
                return
            name = name.strip()
            taken = {e["body"].get("name") for e in model["envs"] if e["kind"] == "sheet"}
            if not (1 <= len(name) <= 31) or name in taken:
                messagebox.showerror("BMO Editor", "Bad or duplicate sheet name.")
                return
            sibs = [e["index"] or 0 for e in ordered_children(model, wb["oid"])]
            idx = (max(sibs) + 1) if sibs else 0
            new = {next_oid(): cbor_encode({"kind": "sheet", "parent": wb["oid"],
                                            "index": idx, "body": {"name": name, "cells": {}}})}
            if commit({}, new):
                state["sheet_tab"] = name

        def rename_sheet():
            from tkinter import simpledialog
            oid, env = current_sheet()
            if env is None:
                messagebox.showinfo("BMO Editor", "No sheet open.")
                return
            name = simpledialog.askstring("Rename sheet", "New name (1-31 chars, unique):",
                                          initialvalue=env["body"].get("name", ""), parent=root)
            if name is None:
                return
            name = name.strip()
            taken = {e["body"].get("name") for e in model["envs"]
                     if e["kind"] == "sheet" and e["oid"] != oid}
            if not (1 <= len(name) <= 31) or name in taken:
                messagebox.showerror("BMO Editor", "Bad or duplicate sheet name.")
                return
            body = dict(env["body"])
            body["name"] = name
            new_env = env_for_write(env)
            new_env["body"] = body
            if commit({oid: cbor_encode(new_env)}):
                state["sheet_tab"] = name

        def delete_sheet():
            oid, env = current_sheet()
            if env is None:
                messagebox.showinfo("BMO Editor", "No sheet open.")
                return
            sheets = [e for e in model["envs"] if e["kind"] == "sheet"]
            if len(sheets) <= 1:
                messagebox.showerror("BMO Editor", "A workbook keeps at least one sheet.")
                return
            if not messagebox.askyesno("BMO Editor",
                                       f"Delete sheet {env['body'].get('name')!r}? "
                                       "Formulas in other sheets may reference it."):
                return
            wb = next((e for e in model["envs"] if e["kind"] == "workbook"), None)
            bumped = plan_compact_siblings(model["envs"], wb["oid"], [oid], cbor_encode) if wb else {}
            if commit(bumped, {}, [oid]):
                state.pop("sheet_tab", None)

        btn_sadd.config(command=add_sheet)
        btn_sren.config(command=rename_sheet)
        btn_sdel.config(command=delete_sheet)
        if state.get("sheet_tab"):
            for tab in nb.tabs():
                if nb.tab(tab, "text") == state["sheet_tab"]:
                    nb.select(tab)
                    break

    # -- slides --
    def show_slide():
        clear()
        state["mode"] = "slides"
        model = state["model"]
        slides = deck_slides(model)
        if not slides:
            return
        state["slide"] = max(0, min(state["slide"], len(slides) - 1))
        s = slides[state["slide"]]
        top = ttk.Frame(body)
        top.pack(fill="x")
        ttk.Button(top, text="◀ Prev", command=lambda: step(-1)).pack(side="left")
        ttk.Label(top, text=f"Slide {state['slide'] + 1} / {len(slides)}").pack(side="left", padx=12)
        ttk.Button(top, text="Next ▶", command=lambda: step(1)).pack(side="left")
        tr = s["body"].get("transition") if isinstance(s["body"], dict) else None
        if isinstance(tr, dict):
            ttk.Label(top, text=f"transition: {tr.get('type')}").pack(side="left", padx=12)
        ttk.Button(top, text="Notes…", command=lambda: edit_notes(s)).pack(side="right", padx=2)
        ttk.Button(top, text="Transition…", command=lambda: edit_transition(s)).pack(side="right", padx=2)
        ttk.Button(top, text="Add Slide", command=lambda: add_slide()).pack(side="right", padx=8)
        ttk.Button(top, text="Duplicate", command=lambda: dup_slide(s)).pack(side="right", padx=2)
        ttk.Button(top, text="Delete Slide", command=lambda: del_slide(s)).pack(side="right", padx=2)
        cv = tk.Canvas(body, width=900, height=500, bg="white")
        cv.pack(fill="both", expand=True, padx=12, pady=8)
        y = 30
        for c in ordered_children(model, s["oid"]):
            b = c["body"]
            if c["kind"] == "paragraph":
                runs = [r for r in ordered_children(model, c["oid"]) if r["kind"] == "run"]
                fnt = tkfont.Font(family="Segoe UI", size=20)
                for r in runs:
                    rm = run_style(model["by_id"], r["body"])
                    f2 = tkfont.Font(family="Segoe UI", size=20)
                    if rm.get("b"):
                        f2.configure(weight="bold")
                    if rm.get("i"):
                        f2.configure(slant="italic")
                    cv.create_text(60, y, anchor="nw", text=r["body"].get("text", ""),
                                   font=f2, justify="left", width=800)
                    y += 30
                y += 8
            elif c["kind"] in ("shape", "image", "group"):
                draw_shape(cv, model, c, state)
        notes = s["body"].get("notes") if isinstance(s["body"], dict) else None
        if isinstance(notes, str) and notes:
            ttk.Label(body, text="Notes: " + notes, wraplength=880, justify="left").pack(pady=4)

        def edit_notes(slide):
            from tkinter import simpledialog
            b = slide["body"] if isinstance(slide["body"], dict) else {}
            new_notes = simpledialog.askstring(
                "Speaker notes", f"Notes for slide #{slide['oid']} (empty clears):",
                initialvalue=b.get("notes", ""), parent=root)
            if new_notes is None:
                return
            env = env_for_write(slide)
            env["body"] = plan_slide_body(b, new_notes, None, None)
            if commit({slide["oid"]: cbor_encode(env)}):
                status.set(f"notes saved · txn {state['model']['file']['footer']['txn']}")

        def edit_transition(slide):
            from tkinter import simpledialog
            b = slide["body"] if isinstance(slide["body"], dict) else {}
            cur = (b.get("transition") or {}).get("type", "none") if isinstance(b.get("transition"), dict) else "none"
            t = simpledialog.askstring("Transition",
                                       "Type (none/fade/wipe/slide/push/zoom/…; free text, preserved as-is):",
                                       initialvalue=cur, parent=root)
            if t is None:
                return
            t = t.strip() or "none"
            dur = None
            if t != "none":
                d = simpledialog.askstring("Transition", "Duration ms (blank = default):",
                                           initialvalue=str(((b.get("transition") or {}).get("durMs", "")) if isinstance(b.get("transition"), dict) else ""),
                                           parent=root)
                if d is None:
                    return
                d = d.strip()
                dur = int(d) if d.isdigit() else None
            env = env_for_write(slide)
            env["body"] = plan_slide_body(b, None, t, dur)
            if commit({slide["oid"]: cbor_encode(env)}):
                status.set(f"transition saved · txn {state['model']['file']['footer']['txn']}")

        def deck_of():
            return next((e for e in model["envs"] if e["kind"] == "deck"), None)

        def layout_of():
            for e in ordered_children(model, deck_of()["oid"]) if deck_of() else []:
                if e["kind"] == "layout":
                    return e["oid"]
            return None

        def add_slide():
            deck = deck_of()
            if deck is None:
                return
            sibs = [e["index"] or 0 for e in ordered_children(model, deck["oid"])]
            idx = (max(sibs) + 1) if sibs else 0
            body = {}
            lo = layout_of()
            if lo is not None:
                body["layout"] = {"$ref": lo}
            new = {next_oid(): cbor_encode({"kind": "slide", "parent": deck["oid"],
                                            "index": idx, "body": body})}
            if commit({}, new):
                state["slide"] = len(deck_slides(state["model"])) - 1
                status.set(f"slide added · txn {state['model']['file']['footer']['txn']}")

        def dup_slide(slide):
            deck = deck_of()
            fresh = next_oid()
            clones, mapping = clone_subtree(model["envs"], slide["oid"], fresh)
            sibs = [e["index"] or 0 for e in ordered_children(model, deck["oid"])] if deck else [0]
            clones[mapping[slide["oid"]]]["index"] = max(sibs) + 1
            new = {o: cbor_encode(e) for o, e in clones.items()}
            if commit({}, new):
                state["slide"] = len(deck_slides(state["model"])) - 1
                status.set(f"slide duplicated · txn {state['model']['file']['footer']['txn']}")

        def del_slide(slide):
            slides = deck_slides(model)
            if len(slides) <= 1:
                messagebox.showerror("BMO Editor", "A deck keeps at least one slide.")
                return
            doomed = sorted(descendants(model["envs"], slide["oid"]))
            blockers = find_referrers(model["envs"], doomed) - set(doomed)
            if blockers:
                messagebox.showerror("BMO Editor",
                                     "Cannot delete: referenced by object(s) "
                                     + ", ".join(f"#{b}" for b in sorted(blockers)))
                return
            if not messagebox.askyesno("BMO Editor",
                                       f"Delete slide #{slide['oid']} and its {len(doomed) - 1} object(s)?"):
                return
            deck = next((e for e in model["envs"] if e["kind"] == "deck"), None)
            bumped = plan_compact_siblings(model["envs"], deck["oid"], doomed, cbor_encode) if deck else {}
            if commit(bumped, {}, doomed):
                state["slide"] = 0
                status.set(f"slide deleted · txn {state['model']['file']['footer']['txn']}")

        def step(d):
            state["slide"] = (state["slide"] + d) % len(slides)
            show_slide()

    def draw_shape(cv, model, e, state):
        if e["kind"] == "group":
            for c in ordered_children(model, e["oid"]):
                draw_shape(cv, model, c, state)
            return
        if e["kind"] == "image":
            b = e["body"]
            sha = (b.get("asset") or {}).get("$asset") if isinstance(b.get("asset"), dict) else None
            blob = model["assets"].get(sha) if isinstance(sha, bytes) else None
            if blob:
                try:
                    img = tk.PhotoImage(data=base64.b64encode(blob).decode("ascii"))
                    wpx = max(1, int(b.get("w", 1000) * BMU_PX))
                    sub = max(1, round(max(img.width() / 380, 1)))
                    img = img.subsample(sub, sub)
                    state["images"].append(img)
                    cv.create_image(60, 300, anchor="nw", image=img)
                    return
                except Exception:
                    pass
            cv.create_text(60, 300, anchor="nw", text=f"[image {b.get('mime', '?')}]")
            return
        b, g = e["body"], e["body"].get("geom", {})
        fill = _hex(b.get("fill", "#000000")) if e["kind"] != "line" else ""
        ln = b.get("line") if isinstance(b.get("line"), dict) else {}
        lc = _hex(ln.get("color")) if isinstance(ln.get("color"), dict) else ""
        lw = max(1, int(ln.get("w", 0) * BMU_PX)) if isinstance(ln.get("w"), int) else 1
        k = b.get("shape")
        S = lambda v: v * BMU_PX + 40
        if k == "rect":
            cv.create_rectangle(S(g["x"]), S(g["y"]), S(g["x"] + g["w"]), S(g["y"] + g["h"]),
                                fill=fill or "white", outline=lc or "black", width=lw)
        elif k == "ellipse":
            cv.create_oval(S(g["cx"] - g["rx"]), S(g["cy"] - g["ry"]),
                           S(g["cx"] + g["rx"]), S(g["cy"] + g["ry"]),
                           fill=fill or "white", outline=lc or "black", width=lw)
        elif k == "line":
            cv.create_line(S(g["x1"]), S(g["y1"]), S(g["x2"]), S(g["y2"]),
                           fill=lc or "black", width=lw)
        elif k == "path":
            pts = []
            for op in g.get("d", []):
                if op[0] in ("M", "L"):
                    pts += [S(op[1]), S(op[2])]
                elif op[0] == "C":
                    pts += [S(op[5]), S(op[6])]
            if pts:
                cv.create_line(*pts, fill=lc or "black", width=lw)

    def _hex(c):
        if c == "none" or c is None:
            return ""
        if isinstance(c, dict) and isinstance(c.get("srgb"), bytes) and len(c["srgb"]) == 3:
            return "#" + c["srgb"].hex()
        return "#000000"

    def do_open():
        from tkinter import filedialog
        p = filedialog.askopenfilename(title="Open BMO file",
                                       filetypes=[("BMO documents", "*.bmo"), ("All files", "*.*")])
        if p:
            show(p)

    def do_convert(kind):
        if not state["model"]:
            return
        from tkinter import filedialog
        ext = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx"}[kind]
        out = filedialog.asksaveasfilename(
            title=f"Convert to {kind.upper()}",
            defaultextension=ext,
            initialfile=Path(state["path"]).stem + ext,
            filetypes=[(kind.upper(), "*" + ext)])
        if not out:
            return
        try:
            sys.path.insert(0, str(ROOT / "converters" / "python"))
            from bmo_ooxml import docx, pptx, xlsx
            envs = [{"oid": e["oid"], "kind": e["kind"], "parent": e["parent"],
                     "index": e["index"], "body": e["body"]} for e in state["model"]["envs"]]
            approx = []
            blobs = state["model"]["assets"]
            if kind == "docx":
                data = docx.export_docx(envs, Path(state["path"]).stem, approx,
                                        {s: ("application/octet-stream", b) for s, b in blobs.items()})
            elif kind == "xlsx":
                data = xlsx.export_xlsx(envs, Path(state["path"]).stem, approx)
            else:
                data = pptx.export_pptx(envs, Path(state["path"]).stem, approx,
                                        {s: ("application/octet-stream", b) for s, b in blobs.items()})
            Path(out).write_bytes(data)
            msg = "Saved."
            if approx:
                msg += "\nNotes:\n- " + "\n- ".join(approx[:5])
            messagebox.showinfo("BMO Viewer", msg)
        except Exception as e:
            messagebox.showerror("BMO Viewer", f"Convert failed: {type(e).__name__}: {e}")

    convmenu = tk.Menu(filemenu, tearoff=0)
    filemenu.add_command(label="Open…", command=do_open)
    filemenu.add_cascade(label="Convert To", menu=convmenu)
    convmenu.add_command(label="Word (.docx)", command=lambda: do_convert("docx"))
    convmenu.add_command(label="Excel (.xlsx)", command=lambda: do_convert("xlsx"))
    convmenu.add_command(label="PowerPoint (.pptx)", command=lambda: do_convert("pptx"))
    filemenu.add_separator()
    filemenu.add_command(label="Exit", command=root.destroy)
    root.config(menu=menubar)
    statusbar = ttk.Label(root, textvariable=status, anchor="w", padding=3)
    statusbar.pack(fill="x", side="bottom")

    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".bmo"):
        root.after(50, lambda: show(sys.argv[1]))
    root.mainloop()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("usage: viewer.py [file.bmo]  |  viewer.py --smoke-test file.bmo"
              "  |  viewer.py --check file.bmo")
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        # Headless open+validate: no window, typed exit codes (automation).
        try:
            model = load_document(sys.argv[2])
        except FriendlyError as e:
            print(f"CHECK-FAIL {e}")
            raise SystemExit(1)
        print(f"CHECK-OK objects={len(model['envs'])}")
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--smoke-test":
        # Frozen-binary self test: open + validate + prove Tk works. No window.
        import tkinter as tk
        model = load_document(sys.argv[2])
        r = tk.Tk()
        r.withdraw()
        t = tk.Text(r)
        t.insert("1.0", "smoke")
        r.update()
        r.destroy()
        print(f"SMOKE-OK objects={len(model['envs'])}")
        return
    run_app()

if __name__ == "__main__":
    main()
