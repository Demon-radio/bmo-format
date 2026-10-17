//! RFC-0003..0006 domain conformance. Mirrors
//! tests/conformance/test_domains.py. Run: `cargo test`.

use bmo_core::cbor::Value;
use bmo_core::chunk::T_GENERIC_OBJECT;
use bmo_core::directory::DirEntry;
use bmo_core::{BmoError, BmoFile};
use std::collections::HashMap;
use std::path::PathBuf;

fn golden(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("../../tests/golden/{name}"))
}

fn open_validated(name: &str) -> BmoFile {
    let raw = std::fs::read(golden(name)).unwrap_or_else(|_| panic!("run the vector makers first: {name}"));
    let f = BmoFile::open_bytes(raw).unwrap_or_else(|e| panic!("{name} must open: {e}"));
    f.validate().unwrap_or_else(|e| panic!("{name} must validate: {e}"));
    f
}

#[test]
fn legacy_goldens_still_validate() {
    open_validated("hello.bmo");
    open_validated("model.bmo");
}

#[test]
fn table_golden() {
    let f = open_validated("table.bmo");
    assert_eq!(f.dir.entries.len(), 16);
    let r13 = f.read_object(13).unwrap();
    let (v, _) = bmo_core::cbor::decode(&r13).unwrap();
    assert!(format!("{v:?}").contains("R2C1"));
}

#[test]
fn text_golden() {
    let f = open_validated("text.bmo");
    assert_eq!(f.dir.entries.len(), 10);
    let run8 = f.read_object(8).unwrap();
    let (v, _) = bmo_core::cbor::decode(&run8).unwrap();
    assert!(format!("{v:?}").contains("Arab"));
}

#[test]
fn graphics_golden_asset_chain() {
    let f = open_validated("graphics.bmo");
    assert!(f.dir.entries.iter().any(|e| e.chunk_type == 0x0005));
    let img = f.read_object(4).unwrap();
    let (v, _) = bmo_core::cbor::decode(&img).unwrap();
    assert!(format!("{v:?}").contains("image/png"));
}

#[test]
fn sheet_golden_floats_and_deps() {
    let f = open_validated("sheet.bmo");
    let s3 = f.read_object(3).unwrap();
    let (v, _) = bmo_core::cbor::decode(&s3).unwrap();
    let s = format!("{v:?}");
    assert!(s.contains("A1*2") && s.contains("merges"));
}

#[test]
fn deck_golden_composes() {
    let f = open_validated("deck.bmo");
    assert_eq!(f.dir.entries.len(), 8);
    let s4 = f.read_object(4).unwrap();
    let (v, _) = bmo_core::cbor::decode(&s4).unwrap();
    assert!(format!("{v:?}").contains("fade"));
}

#[test]
fn accepts_slide_media_and_rejects_bad_mime_or_parent() {
    let asset = Value::Map(vec![(
        txt("$asset"),
        Value::Bytes(vec![0; 32]),
    )]);
    let media_body = |mime: &str| {
        Value::Map(vec![
            (txt("asset"), asset.clone()),
            (txt("mime"), txt(mime)),
        ])
    };
    let valid = bodies(vec![
        (1, enc_env("deck", 0, Value::Map(vec![]))),
        (2, enc_env("slide", 1, Value::Map(vec![]))),
        (3, enc_env("media", 2, media_body("audio/mpeg"))),
    ]);
    assert!(valid.is_ok());

    let bad_mime = bodies(vec![
        (1, enc_env("deck", 0, Value::Map(vec![]))),
        (2, enc_env("slide", 1, Value::Map(vec![]))),
        (3, enc_env("media", 2, media_body("image/png"))),
    ]);
    assert_eq!(bad_mime.unwrap_err(), BmoError::BadStructure);

    let bad_parent = bodies(vec![
        (1, enc_env("deck", 0, Value::Map(vec![]))),
        (2, enc_env("media", 1, media_body("video/mp4"))),
    ]);
    assert_eq!(bad_parent.unwrap_err(), BmoError::BadStructure);
}

// --- synthetic negatives through validate_bodies ---

fn bodies(objs: Vec<(u64, Vec<u8>)>) -> Result<(), BmoError> {
    let store: HashMap<u64, Vec<u8>> = objs.into_iter().collect();
    let entries: Vec<DirEntry> = store
        .keys()
        .map(|oid| DirEntry { object_id: *oid, chunk_type: T_GENERIC_OBJECT, file_offset: 64, total_len: 44 })
        .collect();
    bmo_core::domains::validate_bodies(&entries, &|id| store.get(&id).cloned().ok_or(BmoError::Corrupt))
}

fn enc_env(kind: &str, parent: u64, body: Value) -> Vec<u8> {
    bmo_core::cbor::encode(&Value::Map(vec![
        (bmo_core::cbor::text("body"), body),
        (bmo_core::cbor::text("kind"), bmo_core::cbor::text(kind)),
        (bmo_core::cbor::text("parent"), Value::UInt(parent)),
    ]))
}

fn txt(s: &str) -> Value {
    bmo_core::cbor::text(s)
}

#[test]
fn rejects_bad_align() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("paragraph", 1, Value::Map(vec![(txt("align"), txt("diagonal"))]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_u2029_in_run() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("paragraph", 1, Value::Map(vec![]))),
        (3, enc_env("run", 2, Value::Map(vec![(txt("text"), txt("a\u{2029}b"))]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_bad_addr_and_formula_without_deps() {
    let wb = enc_env("workbook", 0, Value::Map(vec![]));
    let bad_sheet = enc_env(
        "sheet",
        1,
        Value::Map(vec![
            (txt("name"), txt("S")),
            (txt("cells"), Value::Map(vec![(txt("ZZZ99999999"), Value::Map(vec![(txt("v"), Value::UInt(1))]))])),
        ]),
    );
    let r = bodies(vec![(1, wb.clone()), (2, bad_sheet)]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);

    let no_deps = enc_env(
        "sheet",
        1,
        Value::Map(vec![
            (txt("name"), txt("S")),
            (
                txt("cells"),
                Value::Map(vec![(
                    txt("A1"),
                    Value::Map(vec![
                        (txt("v"), Value::UInt(1)),
                        (txt("f"), txt("1+1")),
                    ]),
                )]),
            ),
        ]),
    );
    let r2 = bodies(vec![(1, wb), (2, no_deps)]);
    assert_eq!(r2.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_cell_under_paragraph() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("paragraph", 1, Value::Map(vec![]))),
        (3, enc_env("cell", 2, Value::Map(vec![]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_row_outside_table() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("page", 1, Value::Map(vec![(txt("number"), Value::UInt(1))]))),
        (3, enc_env("row", 2, Value::Map(vec![]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn accepts_nested_table_in_cell() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("table", 1, Value::Map(vec![]))),
        (3, enc_env("row", 2, Value::Map(vec![]))),
        (4, enc_env("cell", 3, Value::Map(vec![]))),
        (5, enc_env("table", 4, Value::Map(vec![]))),
        (6, enc_env("row", 5, Value::Map(vec![]))),
        (7, enc_env("cell", 6, Value::Map(vec![]))),
        (8, enc_env("paragraph", 7, Value::Map(vec![]))),
        (9, enc_env("run", 8, Value::Map(vec![(txt("text"), txt("nested"))]))),
    ]);
    assert!(r.is_ok());
}

#[test]
fn validates_list_definition_level_formatting() {
    let valid = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env(
            "list-def",
            1,
            Value::Map(vec![
                (txt("id"), txt("L1")),
                (txt("style"), txt("decimal")),
                (txt("start"), Value::UInt(5)),
                (txt("text"), txt("%1.")),
                (txt("levels"), Value::Array(vec![Value::Map(vec![
                    (txt("level"), Value::UInt(1)),
                    (txt("style"), txt("lowerLetter")),
                    (txt("start"), Value::UInt(3)),
                    (txt("text"), txt("%2)")),
                ])])),
            ]),
        )),
    ]);
    assert!(valid.is_ok());

    for levels in [
        vec![
            Value::Map(vec![(txt("level"), Value::UInt(1))]),
            Value::Map(vec![(txt("level"), Value::UInt(1))]),
        ],
        vec![Value::Map(vec![(txt("level"), Value::UInt(0))])],
        vec![Value::Map(vec![(txt("level"), Value::UInt(9))])],
        vec![Value::Map(vec![
            (txt("level"), Value::UInt(1)),
            (txt("start"), Value::Int(-1)),
        ])],
    ] {
        let invalid = bodies(vec![
            (1, enc_env("document", 0, Value::Map(vec![]))),
            (2, enc_env("list-def", 1, Value::Map(vec![
                (txt("id"), txt("L1")),
                (txt("levels"), Value::Array(levels)),
            ]))),
        ]);
        assert_eq!(invalid.unwrap_err(), BmoError::BadStructure);
    }
}

#[test]
fn rejects_table_under_paragraph() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("paragraph", 1, Value::Map(vec![]))),
        (3, enc_env("table", 2, Value::Map(vec![]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);
}

#[test]
fn rejects_run_under_page_and_dup_sheet() {
    let r = bodies(vec![
        (1, enc_env("document", 0, Value::Map(vec![]))),
        (2, enc_env("page", 1, Value::Map(vec![(txt("number"), Value::UInt(1))]))),
        (3, enc_env("run", 2, Value::Map(vec![(txt("text"), txt("x"))]))),
    ]);
    assert_eq!(r.unwrap_err(), BmoError::BadStructure);

    let r2 = bodies(vec![
        (1, enc_env("workbook", 0, Value::Map(vec![]))),
        (2, enc_env("sheet", 1, Value::Map(vec![
            (txt("name"), txt("S")),
            (txt("cells"), Value::Map(vec![])),
        ]))),
        (3, enc_env("sheet", 1, Value::Map(vec![
            (txt("name"), txt("S")),
            (txt("cells"), Value::Map(vec![])),
        ]))),
    ]);
    assert_eq!(r2.unwrap_err(), BmoError::BadStructure);
}
