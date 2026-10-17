# BMO — Binary Modular Object (0.x-alpha)

Portable, editable, structured binary container for documents and data.
**The spec text is authoritative; code proves it implementable.**

## Normal user? Start here (no installs, no commands to memorize)

You need only Python 3.11+.

**Easiest — double-click.** One-time setup (no admin rights needed):

```powershell
powershell -ExecutionPolicy Bypass -File apps\web\install-association.ps1
```

After that, double-clicking any `.bmo` file opens it in **BMO Editor**, a
normal desktop window — no browser, no Office:

- Documents (Arabic renders right-to-left), tables, pictures
- Spreadsheets as tabs with **live computed formulas**
- Slides with Next/Prev, notes and transitions
- File > Convert To Word/Excel/PowerPoint (correct filenames suggested)
- **Editing**: click a paragraph to change its text (with bold/italic),
  move it up/down, add/delete paragraphs, double-click spreadsheet cells
  (formulas too), add/rename/delete sheets, add/duplicate/delete slides,
  edit notes and transitions — every save appends a new verified revision
  inside the same file (your old versions stay in its history; invalid
  edits are refused, never written)

To undo the association: `uninstall-association.ps1` next to it. If icons
look stale, restart Explorer once.

**Alternative — in your browser.** `python apps/web/server.py` opens
BMO Studio (Inspect / Convert / View / Calculate tabs, files stay local).
Stop with `Ctrl+C`.

### “BAD_MAGIC” / “Not a valid .bmo file”? Read this

Renaming a file to `.bmo` never converts it — a Word file renamed to
`.bmo` (or a `.txt` saved as `.bmo`) is still the old bytes inside, and
BMO correctly refuses it. Convert the **original** instead: Convert tab
(`report.docx` → `report.bmo`), or:

```powershell
python cli/bmo.py convert report.docx report.bmo
python cli/bmo.py validate report.bmo   # -> valid (...)
```

Prefer commands? The same engine is scriptable — see “Verify” and `cli/bmo.py`
(`info`, `validate`, `inspect`, `convert`, `render`, `calc`).

Measured (not claimed): converters 100/100/90 round-trip (+RTL/complex
100%), crypto/KDF/signatures against official RFC vectors, indexed reads
11,425× faster than scans and flat at 10× file size, 1500/1500 fuzz inputs
fail with codes — see `tests/converters/compat_matrix.json`,
`benchmarks/results.json`, `tests/fuzz/`.

## Layout

```text
spec/          RFC-0001 container … RFC-0010 rendering (read in order)
core/rust/     reference core (zero deps) + bmo CLI + cargo conformance tests
sdk/js/        JavaScript SDK (zero deps, node --test; npm: bmo-format-sdk)
converters/python/bmo_ooxml/  DOCX/XLSX/PPTX/CSV/MD <-> BMO (zero deps, measured)
calc/python/bmo_calc/      formula engine (Excel-compatible semantics)
renderer/python/bmo_render/   SVG/HTML reference renderer (see RFC-0010)
apps/web/      BMO Studio: local web UI for normal users (just run it)
mcp/           BMO MCP server (stdio JSON-RPC, 9 tools)
cli/           bmo.py: info/validate/inspect/convert/render/calc
benchmarks/    random-access proof (indexed reads flat at 10x size)
tests/vectors/ deterministic builders (make_*.py) — golden files stay byte-identical
tests/conformance/ executable suites mirroring the cargo tests
tests/converters/ round-trip fidelity + compat_matrix.json (measured, not claimed)
tests/fuzz/    mutated-input harness: readers must fail with codes, never tracebacks
tests/golden/  pinned vectors (see RFC-0009 §3 for hashes)
```

## Verify (no toolchain needed)

```bash
python tests/run_all.py          # all portable suites, exit 0 = green
node --test sdk/js/test.js       # JS SDK suite
```

Install the JavaScript container reader with `npm install bmo-format-sdk`.
Its API and limitations are documented in [sdk/js/README.md](./sdk/js/README.md).

With a Rust toolchain:

```bash
cd core/rust && cargo test && cargo test --features "codecs signatures"
# Optional implementations: ZSTD/DEFLATE, AES-GCM/ChaCha/XChaCha,
# ECDSA-P256 and RSA-PSS signature verification
cargo run --release -- info ../../tests/golden/hello.bmo
# no MSVC link.exe? use the zig/GNU path (both feature sets):
#   powershell -File core/rust/run_tests.ps1
```

Cross-implementation checks (needs the Rust CLI built; skips otherwise):

```bash
python tests/interop/test_rust_interop.py     # 8 checks: byte-identical hello,
                                              # fail-closed + decrypt parity
python tests/test_exe.py                      # frozen dist\BMOViewer.exe verification
```

## Rules of the repo

1. Spec first: code that disagrees with `spec/` is a bug in the code.
2. Two implementations: new features land with vectors + negatives that
   both the Python harness and the Rust core assert.
3. Goldens are frozen: same bytes, same hashes, every rebuild.
4. No executable content in files, no cipher inventing (standard suites only).

## License

Code is licensed under Apache-2.0; the specification in `spec/` is
licensed under CC BY 4.0. See [LICENSE](./LICENSE) and
[spec/LICENSE](./spec/LICENSE). These are the project's intended licenses;
they have not received independent legal review.
