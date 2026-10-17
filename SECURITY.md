# BMO Security Model (working notes, not a certification)

## Reporting a vulnerability

Please do not report vulnerabilities in public issues. If the repository's
**Security** tab offers **Report a vulnerability**, use it to send a private
advisory to the maintainers. If that option is unavailable, contact the
repository owner through GitHub and request a private channel. Include the
affected version or commit, impact, and a minimal reproduction when safe.

This project is alpha software. No response-time or remediation SLA is
promised. Do not include real user documents, credentials, or secret keys in
reports. Coordinated disclosure is requested; please allow maintainers time
to investigate before public disclosure.

## Threat posture: every document is untrusted input

- **Bounds first.** All lengths/offsets checked before allocation
  (§14 limits: 2^48 file, 2^32 chunk, 255 secondary indexes).
  Decompression capped (`max_output_size = decoded+1`); ratio cap 64:1
  recommended for archive profiles.
- **No code execution on open.** No macros/scripts/fonts-executed;
  images treated as opaque bytes; fonts are system fonts (no embedding
  in v0.10, so no font-parser attack surface in our code).
- **Fuzz-closed parsers.** `tests/fuzz/` includes independent container
  and CBOR/model targets (1,500 and 2,000 deterministic cases per run);
  malformed input must produce a clean format error, never a raw library
  exception. Zstandard errors are mapped to `CORRUPT`.
- **Crypto discipline.** Standard suites only (AES-GCM/ChaCha20-Poly1305
  and XChaCha20-Poly1305 via audited libs in implementation paths;
  pure-Python copies exist ONLY
  in `tests/` for vectors). Nonces: CSPRNG, never reused per key.
  Order fixed: compress-then-encrypt. Tag failure → AUTH_FAILED, no
  partial plaintext. Password KDF MUST be Argon2id with enforced
  parameter bounds (RFC-0007 §2.1); `method:none` is test-only and
  readers SHOULD warn.
- **Signatures prove integrity+origin, not trust.** Key distribution is
  BYO-PKI. Timestamps informational (no TSA).
- **Traffic-shape metadata is NOT hidden** (sizes/counts/txn cadence
  visible without the key) — documented in RFC-0007, not a bug.
- **MCP/tools:** bounded ops, read/write separation, no path access
  outside explicit file args, no silent overwrites (commits validate
  before writing). Local servers bind 127.0.0.1 only.

## Known gaps (do not claim otherwise)

- Full mechanism-by-mechanism status: `docs/SECURITY_AUDIT.md`.
- `SECURITY.md` currently directs reporters to private GitHub reporting if
  enabled, or to contact the repository owner privately. The authenticated
  repository API did not expose the private-reporting setting; verify that
  feature in repository settings before relying on it.
- Rust AES-KW/Argon2id remains deferred; password-wrapped Rust reads fail
  closed with `NEED_KEY`. ECDSA/RSA-PSS verification is available behind
  the optional signature feature and uses caller-provisioned keys.
- Python dependencies are pinned in requirements files; CI audits the
  Python lock and Rust lock and uploads a CycloneDX Python SBOM. A clean
  audit run is required before treating the dependency gate as verified.
- The current local dependency scan found no known vulnerabilities after
  upgrading `cryptography`, `PyNaCl`, `anyio`, `pypdf`, and the local `pip`.
  Signed releases remain pending.
- Rust codecs and ECDSA/RSA-PSS verification are optional features; the
  default build remains dependency-free and does not execute coded chunks.
- Regulatory compliance (GDPR/HIPAA/SOC2): NOT claimed, no evidence.
