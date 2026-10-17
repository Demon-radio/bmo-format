//! Conformance: golden hello.bmo opens byte-identical to the Python vector.
//! Run: `cargo test` from core/rust.

use bmo_core::BmoFile;
use std::path::PathBuf;

fn golden() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/golden/hello.bmo")
}

#[test]
fn hello_opens_and_reports() {
    let raw = std::fs::read(golden()).expect("golden hello.bmo must exist (run tests/vectors/make_hello.py)");
    assert_eq!(raw.len(), 829, "golden size pinned by RFC-0001 section 16");
    let f = BmoFile::open_bytes(raw).expect("hello.bmo must open");
    let info = f.info();
    assert_eq!((info.major, info.minor), (0, 1));
    assert_eq!(info.objects, 3);
    assert_eq!(info.pages, 1);
    assert_eq!(info.assets, 0);
    assert!(!info.encrypted && !info.signed);
    assert!(f.validate().is_ok());
    let report = info.report();
    assert!(report.contains("BMO Format: 0.1"));
    assert!(report.contains("Objects: 3"));
}

#[test]
fn hello_matches_rust_builder() {
    let raw = std::fs::read(golden()).unwrap();
    let mut id = [0u8; 16];
    id[15] = 1;
    let rebuilt = bmo_core::file::build_hello(id);
    assert_eq!(rebuilt, raw, "Rust builder must emit the golden bytes exactly");
}

#[test]
fn rejects_tampered_payload() {
    let mut raw = std::fs::read(golden()).unwrap();
    // Flip a byte inside OBJECT#3's text payload (offset 449+40 = 489).
    raw[489] ^= 0xFF;
    let err = BmoFile::open_bytes(raw).unwrap_err();
    assert!(
        matches!(err, bmo_core::BmoError::PayloadCrc | bmo_core::BmoError::Corrupt),
        "tamper must fail closed, got {err}"
    );
}

#[test]
fn rejects_bad_magic() {
    let mut raw = std::fs::read(golden()).unwrap();
    raw[0] = 0x58;
    assert_eq!(BmoFile::open_bytes(raw).unwrap_err(), bmo_core::BmoError::BadMagic);
}
