//! RFC-0002 conformance: envelope v1, R1–R6, KIND_INDEX.
//! Mirrors tests/conformance/test_model.py. Run: `cargo test`.

use bmo_core::chunk::T_GENERIC_OBJECT;
use bmo_core::directory::DirEntry;
use bmo_core::model::{build_kind_index, parse_kind_index, validate_structure};
use bmo_core::{BmoError, BmoFile};
use std::collections::HashMap;
use std::path::PathBuf;

fn golden(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("../../tests/golden/{name}"))
}

fn env(kind: &str, parent: u64, index: Option<u64>, body: bmo_core::cbor::Value) -> Vec<u8> {
    let mut pairs = vec![
        (bmo_core::cbor::text("body"), body),
        (bmo_core::cbor::text("kind"), bmo_core::cbor::text(kind)),
        (bmo_core::cbor::text("parent"), bmo_core::cbor::Value::UInt(parent)),
    ];
    if let Some(i) = index {
        pairs.push((bmo_core::cbor::text("index"), bmo_core::cbor::Value::UInt(i)));
    }
    bmo_core::cbor::encode(&bmo_core::cbor::Value::Map(pairs))
}

/// Validate synthetic (oid → payload) maps directly through the model layer.
fn check_map(objs: Vec<(u64, Vec<u8>)>) -> Result<bmo_core::model::StructureReport, BmoError> {
    let store: HashMap<u64, Vec<u8>> = objs.into_iter().collect();
    let entries: Vec<DirEntry> = store
        .keys()
        .map(|oid| DirEntry { object_id: *oid, chunk_type: T_GENERIC_OBJECT, file_offset: 64, total_len: 44 })
        .collect();
    validate_structure(&entries, &|id| store.get(&id).cloned().ok_or(BmoError::Corrupt), &[])
}

#[test]
fn model_golden_opens_and_indexes() {
    let raw = std::fs::read(golden("model.bmo")).expect("run tests/vectors/make_model.py");
    assert_eq!(raw.len(), 1250);
    let f = BmoFile::open_bytes(raw).expect("model.bmo must open");
    assert!(f.validate().is_ok());
    assert_eq!(f.dir.entries.len(), 4);
    // KIND_INDEX must equal the directory-derived kind map.
    assert_eq!(f.root.secondary.len(), 1);
    let sec = &f.root.secondary[0];
    assert_eq!(sec.index_type, bmo_core::model::KIND_INDEX_TYPE);
    let idx_payload = bmo_core::chunk::Chunk::read_at(&f.raw, sec.index_offset, f.raw.len() as u64)
        .unwrap()
        .payload;
    let groups = parse_kind_index(&idx_payload).unwrap();
    assert_eq!(groups.get("document"), Some(&vec![1u64]));
    assert_eq!(groups.get("page"), Some(&vec![2u64]));
    assert_eq!(groups.get("note"), Some(&vec![3u64]));
    assert_eq!(groups.get("annotation"), Some(&vec![4u64]));
    // $ref target resolves: annotation → note 3.
    assert!(f.read_object(3).is_ok());
}

#[test]
fn hello_passes_envelope_defaults() {
    let raw = std::fs::read(golden("hello.bmo")).unwrap();
    let f = BmoFile::open_bytes(raw).unwrap();
    assert!(f.validate().is_ok(), "v0.1 files stay valid under envelope defaults");
}

#[test]
fn builder_model_roundtrip() {
    let mut id = [0u8; 16];
    id[15] = 9;
    let doc = bmo_core::cbor::encode(&bmo_core::cbor::Value::Map(vec![
        (bmo_core::cbor::text("bmoMajor"), bmo_core::cbor::Value::UInt(0)),
        (bmo_core::cbor::text("bmoMinor"), bmo_core::cbor::Value::UInt(1)),
        (bmo_core::cbor::text("created"), bmo_core::cbor::Value::UInt(0)),
        (bmo_core::cbor::text("docId"), bmo_core::cbor::bytes(&id)),
        (bmo_core::cbor::text("docType"), bmo_core::cbor::text("generic")),
        (bmo_core::cbor::text("features"), bmo_core::cbor::Value::Array(vec![])),
        (bmo_core::cbor::text("metadata"), bmo_core::cbor::Value::Map(vec![])),
        (bmo_core::cbor::text("minMajor"), bmo_core::cbor::Value::UInt(0)),
        (bmo_core::cbor::text("minMinor"), bmo_core::cbor::Value::UInt(1)),
        (bmo_core::cbor::text("modified"), bmo_core::cbor::Value::UInt(0)),
        (bmo_core::cbor::text("optionalExtensions"), bmo_core::cbor::Value::Array(vec![])),
        (bmo_core::cbor::text("requiredExtensions"), bmo_core::cbor::Value::Array(vec![])),
    ]));
    let mut b = bmo_core::FileBuilder::new(id);
    b.document(doc);
    b.object(1, env("document", 0, Some(0), bmo_core::cbor::Value::Map(vec![])));
    b.object(2, env("note", 1, Some(0), bmo_core::cbor::Value::Map(vec![(
        bmo_core::cbor::text("text"),
        bmo_core::cbor::text("hi"),
    )])));
    b.secondary(
        bmo_core::model::KIND_INDEX_TYPE,
        build_kind_index(&[("document", &[1u64]), ("note", &[2u64])]),
    );
    let bytes = b.finish(vec![]);
    let f = BmoFile::open_bytes(bytes).expect("built model file must open");
    assert!(f.validate().is_ok(), "validate failed: {:?}", f.validate().err());
    assert_eq!(f.root.secondary.len(), 1);
}

#[test]
fn rejects_two_roots() {
    let r = check_map(vec![
        (1, env("note", 0, None, bmo_core::cbor::Value::Null)),
        (2, env("note", 0, None, bmo_core::cbor::Value::Null)),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_cycle() {
    let r = check_map(vec![
        (1, env("note", 0, None, bmo_core::cbor::Value::Null)),
        (2, env("note", 3, None, bmo_core::cbor::Value::Null)),
        (3, env("note", 2, None, bmo_core::cbor::Value::Null)),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_dangling_ref() {
    let body = bmo_core::cbor::Value::Map(vec![(
        bmo_core::cbor::text("target"),
        bmo_core::cbor::Value::Map(vec![(
            bmo_core::cbor::text("$ref"),
            bmo_core::cbor::Value::UInt(77),
        )]),
    )]);
    let r = check_map(vec![(1, env("annotation", 0, None, body))]);
    assert_eq!(r.unwrap_err(), BmoError::DanglingRef);
}

#[test]
fn rejects_mixed_and_sparse_index() {
    let mixed = check_map(vec![
        (1, env("document", 0, None, bmo_core::cbor::Value::Null)),
        (2, env("note", 1, Some(0), bmo_core::cbor::Value::Null)),
        (3, env("note", 1, None, bmo_core::cbor::Value::Null)),
    ]);
    assert_eq!(mixed.unwrap_err(), BmoError::BadStructure);
    let sparse = check_map(vec![
        (1, env("document", 0, None, bmo_core::cbor::Value::Null)),
        (2, env("note", 1, Some(0), bmo_core::cbor::Value::Null)),
        (3, env("note", 1, Some(5), bmo_core::cbor::Value::Null)),
    ]);
    assert_eq!(sparse.unwrap_err(), BmoError::BadStructure);
}
