# Security & Cryptographic Scope Audit

Date: 2026-10-15 (updated 2026-10-16: Rust codec execution + ChaCha20-Poly1305
readers wired + Argon2id/AES-KW password wrap wired in the Python kit). Method:
inventory every security-relevant mechanism from RFC-0001 §9 / RFC-0007,
state exactly where it is implemented, and cite the test that exercises it.
Anything not run here is marked NOT VERIFIED — no mechanism below is
claimed beyond the evidence shown.

Implementations: **Rust core** (`core/rust`, reference for
container/model/domains; codec execution behind the `codecs` feature),
**Python kit** (`tests/conformance/bmo_reader.py` + `converters/`, the
shipping reader/writer), **pure-Python harnesses**
(`tests/conformance/{ed25519,chacha20poly1305,argon2}.py`, test-only).

## Crypto inventory

| Mechanism | Spec | Python kit | Rust core | Evidence | Unsupported-side behavior |
|---|---|---|---|---|---|
| CRC32C integrity | RFC-0001 §10 | read+write | read+write | Castagnoli vectors (both) | mismatches → `*_CRC_MISMATCH`, reject |
| SHA-256 | RFC-0001 §10 | read+write | read+write | NIST vectors: Rust `sha256.rs` tests, Python hashlib canonical-hash pin (`test_crypto` canonical_hash) | — |
| ZSTD (comp=1) | RFC-0001 §9 | read+write (lib `zstandard`) | read (decompress, `ruzstd` 0.9.1, `codecs`) | Python: roundtrip + corrupt→`CORRUPT` + output cap (`test_codecs`); Rust: `tests/codecs.rs` roundtrip/corrupt/lying-dlen→`CORRUPT`; cross-impl: Python-written zstd file validates in Rust CLI (`tests/interop`) | no-codecs Rust build: `UPGRADE_REQUIRED` |
| AES-256-GCM (enc=1) | RFC-0007 §1 | read+write (lib `cryptography`) | read+write (RustCrypto `aes-gcm` 0.11.1, `codecs`) | Wycheproof C2SP vector (256/96/128) in BOTH (`test_codecs` / `tests/codecs.rs`), golden frame roundtrip, wrong-key & tamper→`AUTH_FAILED` no partial output (both), cross-impl: Python-written AES file opens in Rust CLI with `--key`; keyless→`NEED_KEY`, wrong key→`AUTH_FAILED` (identical codes, `tests/interop`) | JS / no-codecs Rust build: `UPGRADE_REQUIRED` |
| ChaCha20-Poly1305 (enc=2) | RFC-0007 §1 | read+write (lib `cryptography`) | read+write (RustCrypto `chacha20poly1305` 0.11.0, `codecs`) | RFC 8439 §2.8.2 official vector in BOTH (`test_codecs` / `tests/codecs.rs`), `encrypted.bmo` golden decrypts bit-exact in BOTH, wrong-key & tamper→`AUTH_FAILED` (both), byte parity: Rust CLI `object --key` == Python == manifest `plain_sha256` (`tests/interop`); Python-computed ciphertext pinned as Rust known-answer | JS / no-codecs Rust build: `UPGRADE_REQUIRED`; keyless: `NEED_KEY` |
| XChaCha20-Poly1305 (enc=3) | RFC-0007 §1 | none | none | none | `UPGRADE_REQUIRED` (registry test) |
| DEFLATE / others (comp>1) | RFC-0001 §9 | none | none | none | `UPGRADE_REQUIRED` (unknown-codec test) |
| Argon2id KDF | RFC-0007 §2.1 | read (lib `cryptography`) | none | official phc GENKAT trace (source sha256 `ba05643e…8d28d`) parsed to a 96-block digest manifest, asserted block-for-block + h0 + tag (`test_crypto` `argon2id_blocks`); harness cross-checked against the reference C implementation over an m/p/t matrix + `cryptography` pins (`test_password` `kdf_cross_impl`) | Rust: no KDF — `password-argon2id` files fail closed per-chunk (`NEED_KEY`) |
| Ed25519 signatures | RFC-0007 §3 | harness verify only | none (structure walk) | RFC 8032 vectors + `signed.bmo` canonical-hash + verify (`test_crypto` signed_golden) | no runtime verify API in either implementation yet |
| AES-KW password wrap | RFC-0007 §2 | read (lib `cryptography`) | none | RFC 3394 §4.1 + §4.6 official vectors through the helpers; `password.bmo` golden pinned end-to-end (KEK sha256, unwrapped key sha256, `plain_sha256`; `test_password` `password_golden`); bounds → `STRUCTURE_INVALID` before any KDF work, wrong password → `AUTH_FAILED`, keyless → `NEED_KEY`, unknown method → `UPGRADE_REQUIRED` | Rust: no SECURITY handling — wrapped files fail closed per-chunk (`NEED_KEY`); write path exists only as the fixed golden builder |
| ECDSA / RSA-PSS | RFC-0007 | none | none | none | spec-name only, MUST NOT be claimed |

## Fail-closed discipline (verified)

* Fuzzing: 1500/1500 mutated inputs (case count printed by
  `tests/fuzz/fuzz_container.py`), zero raw exceptions escaped; outcomes
  are clean typed errors or OK.
* Authentication failure never yields partial plaintext: tag checked
  before decrypt-return (`test_codecs` wrong-key/tamper; RFC 8439 harness
  `AuthFailed` contract).
* Cross-implementation parity on `encrypted.bmo`: keyless it fails closed
  in both (Python/codecs builds `NEED_KEY`; a codecs-less Rust build
  `UPGRADE_REQUIRED`) — never a silent open; with the key both decrypt
  byte-identically (`tests/interop` `encrypted_keyless_fail_closed` +
  `encrypted_golden_parity`).
* Password wrap fails closed before any risky work: out-of-profile KDF
  params → `STRUCTURE_INVALID` (never clamped); wrong password → `AUTH_FAILED`
  with no partial state; missing password / `kms` / `cert` → `NEED_KEY`;
  unknown method → `UPGRADE_REQUIRED`; duplicate SECURITY chunks or
  garbage CBOR → `CORRUPT`; an encrypted SECURITY chunk → `STRUCTURE_INVALID`
  (`test_password.py`, 10/10).
* No execution on open; images opaque; no font parsing. Decompression
  capped at `decoded+1`.

## Boundary: where crypto code lives

* Production paths use **audited libraries**: `cryptography` (AES-GCM /
  ChaCha20-Poly1305 / Argon2id / RFC 3394 AES-KW) and `zstandard` in
  Python; RustCrypto `aes-gcm
  0.11.1` + `chacha20poly1305 0.11.0` + `ruzstd 0.9.1` in
  the Rust core behind the `codecs` feature — verified: no module outside
  `tests/` imports the pure-Python `ed25519`/`chacha20poly1305`/`argon2`
  files (grep evidence, 2026-10-15).
* Rust `aes-gcm`/`chacha20poly1305` are compiled with
  `default-features = false` (`alloc`-only): no `getrandom` in the graph —
  the core never generates nonces or keys; callers/kit supplies them.
  `chacha20poly1305 0.11.0` is pinned over the older 0.10.1 line because
  that line drags `generic-array` (a host build script) into the graph,
  which breaks MSVC-host builds on machines without a linker.
* **Nonce generation is not implemented anywhere in the repo** (verified
  by grep): every encrypt path (`container.py`, `make_chunk`, Rust
  `aes_gcm_encrypt`/`chacha20poly1305_encrypt`) requires the caller to
  pass an explicit 12-byte nonce; all current callers are fixed test
  vectors. A future encrypted-writer must generate a fresh random nonce
  per payload (RFC-0007) — recorded as a gap, not a claim.
* The pure-Python harnesses exist to pin *spec* behavior against official
  vectors; they are not hardened and MUST NOT be used to process real
  secrets. Any import from outside `tests/` is a bug. The Argon2id
  harness had a data-independent-addressing bug (address word indexed
  from stream start instead of the segment-local index); it was found,
  fixed, and re-verified block-for-block against the official phc GENKAT
  trace plus a cross-check against the reference C implementation over
  an m/p/t matrix (2026-10-16).
* `pymupdf` (AGPL) is import-guarded, optional, and used only to build
  test fixtures — it is not a runtime dependency of shipping code paths.

## Not verified here (do not claim)

* No independent third-party audit; no CVE process (issue #11).
* No dependency pinning/SBOM yet (issue #10); no signed releases.
* LibreOffice-based rendering checks unavailable on the verification
  machine (see docs/BUILD.md).
* Rust core security scope so far = structure + CRC + bounds + AES-GCM /
  ChaCha20-Poly1305 decrypt/encrypt + ZSTD decompress (audited crates,
  `codecs` feature). It implements no signature verification and no key
  derivation; statements like "Rust verifies signatures" are false until
  implemented.
* XChaCha20-Poly1305 (enc=3) and DEFLATE (comp=2) are registry-reserved
  only: both readers `UPGRADE_REQUIRED` with the same code
  (`unknown_codec` tests in both implementations).
* Regulatory compliance (GDPR/HIPAA/SOC 2): not claimed, no evidence.
