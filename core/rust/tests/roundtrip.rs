//! Conformance: append-only commits + unknown ancillary preservation.
//! Proves RFC-0001 sections 11 (CoW) and 13 (forward compat).

use bmo_core::chunk::Chunk;
use bmo_core::{append_commit, BmoFile};

fn hello() -> Vec<u8> {
    let mut id = [0u8; 16];
    id[15] = 1;
    bmo_core::file::build_hello(id)
}

fn updated_text_3() -> Vec<u8> {
    bmo_core::cbor::encode(&bmo_core::cbor::Value::Map(vec![
        (
            bmo_core::cbor::text("body"),
            bmo_core::cbor::Value::Map(vec![
                (bmo_core::cbor::text("lang"), bmo_core::cbor::text("en")),
                (bmo_core::cbor::text("text"), bmo_core::cbor::text("Hello BMO v2")),
            ]),
        ),
        (bmo_core::cbor::text("kind"), bmo_core::cbor::text("text")),
        (bmo_core::cbor::text("parent"), bmo_core::cbor::Value::UInt(2)),
    ]))
}

/// Copy-on-write: an update appends bytes, links history, stays readable.
#[test]
fn cow_append_grows_and_links_history() {
    let genesis = hello();
    let v2 = append_commit(&genesis, vec![(3, updated_text_3())], vec![], vec![])
        .expect("append must succeed");
    assert!(v2.len() > genesis.len(), "append must grow, never rewrite");
    // Untouched prefix intact (everything before the old footer).
    assert_eq!(&v2[..genesis.len() - 64], &genesis[..genesis.len() - 64]);
    // Old footer still present mid-file (rollback anchor).
    assert!(v2.windows(4).any(|w| w == b"BMOF"));

    let f2 = BmoFile::open_bytes(v2).expect("v2 must open");
    assert_eq!(f2.footer.txn_id, 2);
    assert_eq!(f2.history.len(), 2, "history chain must link footers");
    assert!(f2.validate().is_ok());
    let (v, _) = bmo_core::cbor::decode(&f2.read_object(3).unwrap()).unwrap();
    assert!(format!("{v:?}").contains("Hello BMO v2"), "CoW update must be visible");
    // Untouched objects still resolve to the ORIGINAL offsets.
    let f1 = BmoFile::open_bytes(genesis).unwrap();
    assert_eq!(
        f2.dir.find(1).unwrap().file_offset,
        f1.dir.find(1).unwrap().file_offset,
        "unmodified objects must keep their offsets"
    );
}

/// Forward compat: unknown ancillary chunks survive a full rebuild byte-exact.
#[test]
fn unknown_ancillary_preserved() {
    let genesis = hello();
    let alien = Chunk::build(0x8001, 1, 0x0000, 0, b"future-feature".to_vec());
    let alien_raw = alien.to_bytes();

    let base = BmoFile::open_bytes(genesis).unwrap();
    let mut b = bmo_core::FileBuilder::new(base.header.file_id);
    b.document(base.document.clone());
    for oid in [1u64, 2, 3] {
        b.object(oid, base.read_object(oid).unwrap());
    }
    b.preserve_raw(alien_raw.clone());
    let rebuilt = b.finish(vec![]);

    let fa = BmoFile::open_bytes(rebuilt.clone()).expect("file with alien must open");
    assert!(fa.validate().is_ok());
    assert!(
        rebuilt.windows(alien_raw.len()).any(|w| w == alien_raw.as_slice()),
        "unknown ancillary chunk must be preserved byte-exact"
    );
}

/// Unknown CRITICAL chunks fail closed, never skipped.
#[test]
fn unknown_critical_refused() {
    let genesis = hello();
    // Splice a critical unknown chunk (0x0FFF, CRITICAL) before the footer.
    let evil = Chunk::build(0x0FFF, 1, 0x0008, 0, b"nope".to_vec()).to_bytes();
    let split = genesis.len() - 64;
    let mut tampered = genesis[..split].to_vec();
    tampered.extend_from_slice(&evil);
    tampered.extend_from_slice(&genesis[split..]); // keep tip footer valid
    // The tip footer still resolves (insertion is after the last chunk),
    // but the DOCUMENT scan meets the alien critical chunk first.
    let err = BmoFile::open_bytes(tampered).unwrap_err();
    assert_eq!(err, bmo_core::BmoError::UnknownCritical);
}
