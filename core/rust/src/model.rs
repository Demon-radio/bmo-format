//! Object model framework — RFC-0002 (envelope v1).
//! Structural validation R1–R6 over GENERIC_OBJECT payloads.
//! Bodies of reserved kinds stay opaque; only the envelope and
//! `$ref`/`$asset` links are interpreted here.

use std::collections::{HashMap, HashSet};

use crate::cbor::{self, Value};
use crate::chunk;
use crate::directory::{DirEntry, TOMBSTONE};
use crate::error::{BmoError, BmoResult};

/// Secondary index_type for KIND_INDEX (RFC-0002 section 7).
pub const KIND_INDEX_TYPE: u32 = 1;

#[derive(Debug, Clone)]
pub struct StructureReport {
    pub root: u64,
    pub kinds: HashMap<String, Vec<u64>>,
}

/// Parsed envelope v1 (re-exported for the domain layer).
#[derive(Debug, Clone)]
pub struct Envelope {
    pub oid: u64,
    pub kind: String,
    pub parent: u64,
    pub index: Option<u64>,
    pub body: Value,
    pub ext: Value,
}

pub fn parse_envelope(oid: u64, payload: &[u8]) -> BmoResult<Envelope> {
    let (v, n) = cbor::decode(payload)?;
    if n != payload.len() {
        return Err(BmoError::Corrupt);
    }
    let pairs = match &v {
        Value::Map(p) => p,
        _ => return Err(BmoError::Corrupt),
    };
    let kind = match cbor::map_get(pairs, "kind") {
        Some(Value::Text(s)) => s.clone(),
        _ => return Err(BmoError::Corrupt),
    };
    let parent = match cbor::map_get(pairs, "parent") {
        Some(Value::UInt(x)) => *x,
        _ => return Err(BmoError::Corrupt),
    };
    let index = match cbor::map_get(pairs, "index") {
        None => None,
        Some(Value::UInt(x)) => Some(*x),
        _ => return Err(BmoError::Corrupt),
    };
    match cbor::map_get(pairs, "role") {
        None | Some(Value::Text(_)) => {}
        _ => return Err(BmoError::Corrupt),
    }
    match cbor::map_get(pairs, "rev") {
        None | Some(Value::UInt(_)) => {}
        _ => return Err(BmoError::Corrupt),
    }
    match cbor::map_get(pairs, "id") {
        None => {}
        Some(Value::UInt(x)) if *x == oid => {}
        _ => return Err(BmoError::Corrupt),
    }
    let body = cbor::map_get(pairs, "body").cloned().unwrap_or(Value::Null);
    let ext = cbor::map_get(pairs, "ext").cloned().unwrap_or(Value::Null);
    if !matches!(ext, Value::Null | Value::Map(_)) {
        return Err(BmoError::Corrupt);
    }
    Ok(Envelope { oid, kind, parent, index, body, ext })
}

fn collect_refs(v: &Value, refs: &mut Vec<u64>, assets: &mut Vec<[u8; 32]>) -> BmoResult<()> {
    match v {
        Value::Map(pairs) => {
            for (k, val) in pairs {
                if let Value::Text(s) = k {
                    if s == "$ref" {
                        match val {
                            Value::UInt(id) => {
                                refs.push(*id);
                                continue;
                            }
                            _ => return Err(BmoError::Corrupt),
                        }
                    }
                    if s == "$asset" {
                        match val {
                            Value::Bytes(b) if b.len() == 32 => {
                                let mut a = [0u8; 32];
                                a.copy_from_slice(b);
                                assets.push(a);
                                continue;
                            }
                            _ => return Err(BmoError::Corrupt),
                        }
                    }
                }
                collect_refs(k, refs, assets)?;
                collect_refs(val, refs, assets)?;
            }
            Ok(())
        }
        Value::Array(items) => {
            for it in items {
                collect_refs(it, refs, assets)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn table_shas(payload: &[u8]) -> BmoResult<HashSet<[u8; 32]>> {
    let (v, n) = cbor::decode(payload)?;
    if n != payload.len() {
        return Err(BmoError::Corrupt);
    }
    match v {
        Value::Map(pairs) => {
            let mut out = HashSet::new();
            for (k, _) in pairs {
                match k {
                    Value::Bytes(b) if b.len() == 32 => {
                        let mut a = [0u8; 32];
                        a.copy_from_slice(&b);
                        out.insert(a);
                    }
                    _ => return Err(BmoError::Corrupt),
                }
            }
            Ok(out)
        }
        _ => Err(BmoError::Corrupt),
    }
}

/// Validate R1–R6 over the directory snapshot.
/// `load` resolves live object payloads by ID; `asset_tables` holds
/// decoded ASSET_TABLE payloads collected during the chunk walk.
pub fn validate_structure(
    entries: &[DirEntry],
    load: &dyn Fn(u64) -> BmoResult<Vec<u8>>,
    asset_tables: &[Vec<u8>],
) -> BmoResult<StructureReport> {
    let mut objs: Vec<Envelope> = Vec::new();
    let mut all_ids: HashSet<u64> = HashSet::new();
    for e in entries {
        if e.file_offset == TOMBSTONE {
            continue;
        }
        all_ids.insert(e.object_id);
        if e.chunk_type != chunk::T_GENERIC_OBJECT {
            continue;
        }
        objs.push(parse_envelope(e.object_id, &load(e.object_id)?)?);
    }
    // R1: single root.
    let roots: Vec<&Envelope> = objs.iter().filter(|o| o.parent == 0).collect();
    if roots.len() != 1 {
        return Err(BmoError::BadStructure);
    }
    // R2+R3: every ancestor chain reaches 0.
    let by_id: HashMap<u64, &Envelope> = objs.iter().map(|o| (o.oid, o)).collect();
    for o in &objs {
        let mut cur = o.parent;
        let mut steps = 0usize;
        while cur != 0 {
            steps += 1;
            if steps > objs.len() {
                return Err(BmoError::BadStructure);
            }
            match by_id.get(&cur) {
                Some(p) => cur = p.parent,
                None => return Err(BmoError::BadStructure),
            }
        }
    }
    // R4: sibling order all-explicit+dense or all-implicit.
    let mut children: HashMap<u64, Vec<&Envelope>> = HashMap::new();
    for o in &objs {
        children.entry(o.parent).or_default().push(o);
    }
    for kids in children.values() {
        let explicit = kids.iter().filter(|k| k.index.is_some()).count();
        if explicit > 0 {
            if explicit != kids.len() {
                return Err(BmoError::BadStructure);
            }
            let mut idx: Vec<u64> = kids.iter().map(|k| k.index.unwrap()).collect();
            // Platform-independent bound: sibling positions fit u32
            // (object counts are u32-capped by RFC-0001 section 14).
            if idx.iter().any(|v| *v > 0xFFFF_FFFF) {
                return Err(BmoError::BadStructure);
            }
            idx.sort_unstable();
            for (i, v) in idx.iter().enumerate() {
                if *v as usize != i {
                    return Err(BmoError::BadStructure);
                }
            }
        }
    }
    // R6: reference integrity.
    let mut shas: HashSet<[u8; 32]> = HashSet::new();
    for t in asset_tables {
        shas.extend(table_shas(t)?);
    }
    for o in &objs {
        let mut refs = Vec::new();
        let mut assets = Vec::new();
        collect_refs(&o.body, &mut refs, &mut assets)?;
        collect_refs(&o.ext, &mut refs, &mut assets)?;
        for r in refs {
            if !all_ids.contains(&r) {
                return Err(BmoError::DanglingRef);
            }
        }
        for a in assets {
            if !shas.contains(&a) {
                return Err(BmoError::DanglingRef);
            }
        }
    }
    let mut kinds: HashMap<String, Vec<u64>> = HashMap::new();
    for o in &objs {
        kinds.entry(o.kind.clone()).or_default().push(o.oid);
    }
    for v in kinds.values_mut() {
        v.sort_unstable();
    }
    Ok(StructureReport { root: roots[0].oid, kinds })
}

/// Build a KIND_INDEX payload from kind → ids groups.
pub fn build_kind_index(groups: &[(&str, &[u64])]) -> Vec<u8> {
    let mut order: Vec<(&str, Vec<u64>)> = groups
        .iter()
        .map(|(k, ids)| (*k, {
            let mut v = ids.to_vec();
            v.sort_unstable();
            v
        }))
        .collect();
    order.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
    let mut out = Vec::new();
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(order.len() as u32).to_le_bytes());
    for (kind, ids) in &order {
        out.extend_from_slice(&(kind.len() as u16).to_le_bytes());
        out.extend_from_slice(kind.as_bytes());
        out.extend_from_slice(&(ids.len() as u32).to_le_bytes());
        for id in ids {
            out.extend_from_slice(&id.to_le_bytes());
        }
    }
    out
}

/// Parse a KIND_INDEX payload back to kind → ids.
pub fn parse_kind_index(payload: &[u8]) -> BmoResult<HashMap<String, Vec<u64>>> {
    if payload.len() < 8 {
        return Err(BmoError::Corrupt);
    }
    let ver = u32::from_le_bytes(payload[0..4].try_into().unwrap());
    let n = u32::from_le_bytes(payload[4..8].try_into().unwrap()) as usize;
    if ver != 1 {
        return Err(BmoError::UpgradeRequired);
    }
    let mut off = 8usize;
    let mut out = HashMap::new();
    for _ in 0..n {
        if off + 2 > payload.len() {
            return Err(BmoError::Corrupt);
        }
        let kl = u16::from_le_bytes(payload[off..off + 2].try_into().unwrap()) as usize;
        off += 2;
        if off + kl + 4 > payload.len() {
            return Err(BmoError::Corrupt);
        }
        let kind = std::str::from_utf8(&payload[off..off + kl]).map_err(|_| BmoError::Corrupt)?.to_string();
        off += kl;
        let cnt = u32::from_le_bytes(payload[off..off + 4].try_into().unwrap()) as usize;
        off += 4;
        if off + 8 * cnt > payload.len() {
            return Err(BmoError::Corrupt);
        }
        let mut ids = Vec::with_capacity(cnt);
        for _ in 0..cnt {
            ids.push(u64::from_le_bytes(payload[off..off + 8].try_into().unwrap()));
            off += 8;
        }
        let mut sorted = ids.clone();
        sorted.sort_unstable();
        if ids != sorted {
            return Err(BmoError::Corrupt);
        }
        out.insert(kind, ids);
    }
    if off != payload.len() {
        return Err(BmoError::Corrupt);
    }
    Ok(out)
}
