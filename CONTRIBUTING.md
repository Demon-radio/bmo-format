# Contributing to BMO (working draft)

## Ground rules

1. **Spec first.** Behavior changes start as RFC text in `spec/`. Code
   that disagrees with `spec/` is a bug in the code.
2. **Two implementations.** New features land with vectors + negatives
   asserted by at least the Python harness AND one more implementation
   (Rust/JS). This rule has caught real bugs three times; keep it.
3. **Goldens are frozen.** Same bytes, same hashes, every rebuild.
   Builders are deterministic (fixed IDs, zero timestamps).
4. **Measure, don't claim.** Fidelity/compat numbers live in
   `compat_matrix.json` + `benchmarks/results.json`, never in prose.
5. **No silent loss.** Unknown chunks/kinds/keys preserved; skipped
   input constructs counted in stats; approximations listed on export.
6. **Tests are the contract.** `python tests/run_all.py` green +
   `node --test sdk/js/test.js` green before any merge. Rust when tooled.

## Workflow

- Small, reversible changes; run the affected suite(s) + full `run_all`
  before declaring done.
- New kinds/chunks: RFC entry + validator (both mirrors) + golden or
  negative vectors + `IMPLEMENTATION_STATUS.md` row update.
- Crypto: audited libraries in production paths; pure-Python copies
  ONLY under `tests/` and proven against official vectors.
- Never commit secrets, keys, or real user documents. Sample binaries
  in `tests/converters/samples/` are public test fixtures only.
- No releases/publishing without explicit authorization.

## Definition of done (plan §16)

Implementation exists · behavior documented · tests pass · errors and
unsupported cases explicit · security considered · agrees with spec ·
verification commands + results recorded.
