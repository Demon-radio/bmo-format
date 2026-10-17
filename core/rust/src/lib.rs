//! `bmo-core` — reference implementation of BMO RFC-0001.
//! Zero required dependencies (codec execution — ZSTD + AES-GCM — is the
//! optional `codecs` feature). The spec text (`spec/RFC-0001.md`) is
//! authoritative; this crate exists to prove the spec is implementable
//! byte-for-byte.
//!
//! ## C API surface (stable, minimal)
//! The following functions are the intended `libbmo` boundary; other
//! languages bind to these, never to file bytes directly:
//! `bmo_document_open / bmo_document_validate / bmo_object_get /
//!  bmo_append_commit / bmo_build_hello` (see `file.rs`).
//! A `cbindgen`-ready header can be generated from these signatures.

pub mod cbor;
pub mod chunk;
#[cfg(feature = "codecs")]
pub mod codec;
pub mod crc32c;
pub mod directory;
pub mod domains;
pub mod error;
pub mod file;
pub mod footer;
pub mod header;
pub mod model;
pub mod root;
pub mod sha256;
#[cfg(feature = "signatures")]
pub mod signature;

pub use error::{BmoError, BmoResult};
pub use file::{append_commit, BmoFile, FileBuilder, FileInfo};
