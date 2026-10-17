//! Domain body validators — RFC-0003..0006. Mirrors
//! tests/conformance/domains_check.py rule for rule.
//! `$ref`/`$asset` existence stays R6 (model.rs); this layer checks shapes,
//! vocabularies, addresses and container membership.

use std::collections::{HashMap, HashSet};

use crate::cbor::{self, Value};
use crate::directory::{DirEntry, TOMBSTONE};
use crate::error::{BmoError, BmoResult};
use crate::model::{parse_envelope, Envelope};
use crate::chunk;

fn bad() -> BmoError {
    BmoError::BadStructure
}

fn as_map(v: &Value) -> Option<&Vec<(Value, Value)>> {
    match v {
        Value::Map(p) => Some(p),
        _ => None,
    }
}

fn get<'a>(pairs: &'a [(Value, Value)], key: &str) -> Option<&'a Value> {
    cbor::map_get(pairs, key)
}

fn req_map<'a>(body: &'a Value) -> BmoResult<&'a Vec<(Value, Value)>> {
    as_map(body).ok_or_else(bad)
}

fn opt_text(pairs: &[(Value, Value)], key: &str) -> BmoResult<()> {
    match get(pairs, key) {
        None => Ok(()),
        Some(Value::Text(_)) => Ok(()),
        _ => Err(bad()),
    }
}

fn opt_uint(pairs: &[(Value, Value)], key: &str) -> BmoResult<()> {
    match get(pairs, key) {
        None => Ok(()),
        Some(Value::UInt(_)) => Ok(()),
        _ => Err(bad()),
    }
}

fn is_uint(v: &Value) -> bool {
    matches!(v, Value::UInt(_))
}

fn as_u64(v: &Value) -> Option<u64> {
    match v {
        Value::UInt(n) => Some(*n),
        _ => None,
    }
}

fn ref_shape(v: &Value) -> BmoResult<()> {
    if as_map(v).is_some() {
        Ok(())
    } else {
        Err(bad())
    }
}

fn check_color(v: &Value) -> BmoResult<()> {
    if let Value::Text(s) = v {
        if s == "none" {
            return Ok(());
        }
        return Err(bad());
    }
    let pairs = req_map(v)?;
    match get(pairs, "srgb") {
        Some(Value::Bytes(b)) if b.len() == 3 => {}
        _ => return Err(bad()),
    }
    if let Some(a) = get(pairs, "a") {
        match a {
            Value::UInt(n) if *n <= 255 => {}
            _ => return Err(bad()),
        }
    }
    Ok(())
}

fn check_path(d: &Value) -> BmoResult<()> {
    let ops = match d {
        Value::Array(items) => items,
        _ => return Err(bad()),
    };
    for op in ops {
        let parts = match op {
            Value::Array(p) => p,
            _ => return Err(bad()),
        };
        if parts.is_empty() {
            return Err(bad());
        }
        let (name, arity) = match &parts[0] {
            Value::Text(s) if s == "M" => ("M", 3),
            Value::Text(s) if s == "L" => ("L", 3),
            Value::Text(s) if s == "C" => ("C", 7),
            Value::Text(s) if s == "Z" => ("Z", 1),
            _ => return Err(bad()),
        };
        let _ = name;
        if parts.len() != arity || parts[1..].iter().any(|x| !is_uint(x)) {
            return Err(bad());
        }
    }
    Ok(())
}

fn check_shape(body: &Value) -> BmoResult<()> {
    let pairs = req_map(body)?;
    let kind = match get(pairs, "shape") {
        Some(Value::Text(s)) => s.as_str(),
        _ => return Err(bad()),
    };
    let geom = match get(pairs, "geom") {
        Some(g) => req_map(g)?,
        None => return Err(bad()),
    };
    let need: &[&str] = match kind {
        "rect" => &["x", "y", "w", "h"],
        "ellipse" => &["cx", "cy", "rx", "ry"],
        "line" => &["x1", "y1", "x2", "y2"],
        "path" => &["d"],
        _ => return Err(bad()),
    };
    for k in need {
        if get(geom, k).is_none() {
            return Err(bad());
        }
    }
    if kind == "path" {
        check_path(get(geom, "d").unwrap())?;
    } else {
        for k in need {
            if !is_uint(get(geom, k).unwrap()) {
                return Err(bad());
            }
        }
    }
    if let Some(fill) = get(pairs, "fill") {
        check_color(fill)?;
    }
    if let Some(ln) = get(pairs, "line") {
        let lp = req_map(ln)?;
        match get(lp, "w") {
            Some(Value::UInt(_)) => {}
            _ => return Err(bad()),
        }
        check_color(get(lp, "color").unwrap_or(&Value::Text("none".to_string())))?;
    }
    Ok(())
}

/// A1 address → (col, row), 1-based. Manual parse (no regex crate):
/// [$]letters(1-3)[ $]digits(1-7), col 1..16384, row 1..1048576.
fn addr_parts(addr: &str) -> Option<(u32, u32)> {
    let a: String = addr.chars().filter(|c| *c != '$').collect();
    let split = a.find(|c: char| c.is_ascii_digit())?;
    let (letters, digits) = a.split_at(split);
    if !(1..=3).contains(&letters.len()) || !(1..=7).contains(&digits.len()) {
        return None;
    }
    if !letters.chars().all(|c| c.is_ascii_alphabetic()) || !digits.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let mut col: u32 = 0;
    for ch in letters.chars() {
        col = col * 26 + (ch.to_ascii_uppercase() as u32 - 'A' as u32 + 1);
    }
    let row: u32 = digits.parse().ok()?;
    if (1..=16384).contains(&col) && (1..=1048576).contains(&row) {
        Some((col, row))
    } else {
        None
    }
}

fn check_cell(cell: &Value) -> BmoResult<()> {
    let pairs = req_map(cell)?;
    let has_v = get(pairs, "v").is_some();
    let has_err = get(pairs, "err").is_some();
    if has_v == has_err {
        return Err(bad());
    }
    if let Some(v) = get(pairs, "v") {
        match v {
            Value::UInt(_) | Value::Int(_) | Value::Text(_) | Value::Bool(_) | Value::Null => {}
            Value::Float(x) if !x.is_nan() => {}
            _ => return Err(bad()),
        }
    }
    if let Some(e) = get(pairs, "err") {
        if !matches!(e, Value::Text(_)) {
            return Err(bad());
        }
    }
    if let Some(f) = get(pairs, "f") {
        match f {
            Value::Text(s) if !s.is_empty() => {}
            _ => return Err(bad()),
        }
        match get(pairs, "deps") {
            Some(Value::Array(deps)) => {
                for d in deps {
                    match d {
                        Value::Text(s) if addr_parts(s).is_some() => {}
                        _ => return Err(bad()),
                    }
                }
            }
            _ => return Err(bad()),
        }
    }
    if let Some(s) = get(pairs, "s") {
        ref_shape(s)?;
    }
    Ok(())
}

fn check_sheet(body: &Value) -> BmoResult<()> {
    let pairs = req_map(body)?;
    match get(pairs, "name") {
        // chars, not bytes: matches the spec and the Python mirror.
        Some(Value::Text(n)) if (1..=31).contains(&n.chars().count()) => {}
        _ => return Err(bad()),
    }
    let cells = match get(pairs, "cells") {
        Some(Value::Map(m)) => m,
        _ => return Err(bad()),
    };
    for (addr, cell) in cells {
        match addr {
            Value::Text(s) if addr_parts(s).is_some() => {}
            _ => return Err(bad()),
        }
        check_cell(cell)?;
    }
    if let Some(Value::Array(merges)) = get(pairs, "merges") {
        for mg in merges {
            match mg {
                Value::Array(q) if q.len() == 4 && q.iter().all(|x| is_uint(x)) => {
                    let v: Vec<u64> = q.iter().map(|x| as_u64(x).unwrap()).collect();
                    if v[2] < v[0] || v[3] < v[1] {
                        return Err(bad());
                    }
                }
                _ => return Err(bad()),
            }
        }
    } else if get(pairs, "merges").is_some() {
        return Err(bad());
    }
    Ok(())
}

/// Validate every GENERIC_OBJECT body by kind. Reserved/future/vendor
/// kinds are opaque (no checks) — preservation is the container's job.
pub fn validate_bodies(entries: &[DirEntry], load: &dyn Fn(u64) -> BmoResult<Vec<u8>>) -> BmoResult<()> {
    let mut envs: Vec<Envelope> = Vec::new();
    for e in entries {
        if e.file_offset == TOMBSTONE || e.chunk_type != chunk::T_GENERIC_OBJECT {
            continue;
        }
        envs.push(parse_envelope(e.object_id, &load(e.object_id)?)?);
    }
    let by_id: HashMap<u64, &Envelope> = envs.iter().map(|e| (e.oid, e)).collect();
    let mut children: HashMap<u64, Vec<&Envelope>> = HashMap::new();
    for e in &envs {
        children.entry(e.parent).or_default().push(e);
    }
    let kids_of = |oid: u64| -> Vec<&Envelope> { children.get(&oid).cloned().unwrap_or_default() };

    // File-scope registries.
    let mut list_ids: HashSet<String> = HashSet::new();
    for e in &envs {
        if e.kind == "list-def" {
            let pairs = req_map(&e.body)?;
            match get(pairs, "id") {
                Some(Value::Text(id)) if !id.is_empty() => {
                    if !list_ids.insert(id.clone()) {
                        return Err(bad());
                    }
                }
                _ => return Err(bad()),
            }
        }
    }
    let mut sheet_names: HashMap<u64, Vec<String>> = HashMap::new();
    for e in &envs {
        if e.kind == "sheet" {
            check_sheet(&e.body)?;
            let name = match e.body {
                Value::Map(ref p) => match get(p, "name") {
                    Some(Value::Text(n)) => n.clone(),
                    _ => return Err(bad()),
                },
                _ => return Err(bad()),
            };
            sheet_names.entry(e.parent).or_default().push(name);
        }
    }
    for names in sheet_names.values() {
        let uniq: HashSet<&String> = names.iter().collect();
        if uniq.len() != names.len() {
            return Err(bad());
        }
    }

    for e in &envs {
        let k = e.kind.as_str();
        // Membership helper: allowed kinds + resource kinds.
        let kids = kids_of(e.oid);
        let require_kids = |allowed: &[&str]| -> BmoResult<()> {
            for c in &kids {
                if !allowed.contains(&c.kind.as_str())
                    && !matches!(c.kind.as_str(), "style" | "numfmt" | "list-def")
                {
                    return Err(bad());
                }
            }
            Ok(())
        };
        match k {
            "document" => {
                let p = req_map(&e.body)?;
                opt_text(p, "title")?;
            }
            "section" => {
                req_map(&e.body)?;
            }
            "page" => {
                let p = req_map(&e.body)?;
                match get(p, "number") {
                    Some(Value::UInt(_)) => {}
                    _ => return Err(bad()),
                }
                require_kids(&["paragraph", "shape", "image", "note", "section", "annotation", "text", "table"])?;
            }
            "note" | "text" => {
                let p = req_map(&e.body)?;
                if !matches!(get(p, "text"), Some(Value::Text(_))) {
                    return Err(bad());
                }
                opt_text(p, "lang")?;
            }
            "annotation" => {
                let p = req_map(&e.body)?;
                if !matches!(get(p, "text"), Some(Value::Text(_))) {
                    return Err(bad());
                }
                match get(p, "target") {
                    Some(t) => ref_shape(t)?,
                    None => return Err(bad()),
                }
            }
            "paragraph" => {
                let p = req_map(&e.body)?;
                if let Some(a) = get(p, "align") {
                    match a {
                        Value::Text(s)
                            if matches!(
                                s.as_str(),
                                "start" | "end" | "left" | "right" | "center" | "justify"
                            ) => {}
                        _ => return Err(bad()),
                    }
                }
                if let Some(d) = get(p, "dir") {
                    match d {
                        Value::Text(s) if matches!(s.as_str(), "auto" | "ltr" | "rtl") => {}
                        _ => return Err(bad()),
                    }
                }
                if let Some(li) = get(p, "list") {
                    let lp = req_map(li)?;
                    match get(lp, "id") {
                        Some(Value::Text(id)) if list_ids.contains(id) => {}
                        _ => return Err(bad()),
                    }
                    match get(lp, "level") {
                        Some(Value::UInt(n)) if *n <= 8 => {}
                        _ => return Err(bad()),
                    }
                }
                if let Some(s) = get(p, "style") {
                    ref_shape(s)?;
                }
                require_kids(&["run"])?;
            }
            "run" => {
                let p = req_map(&e.body)?;
                match get(p, "text") {
                    Some(Value::Text(t)) if !t.contains('\u{2029}') => {}
                    _ => return Err(bad()),
                }
                opt_text(p, "lang")?;
                if let Some(s) = get(p, "script") {
                    match s {
                        Value::Text(t) if t.chars().count() == 4 => {}
                        _ => return Err(bad()),
                    }
                }
                if let Some(s) = get(p, "style") {
                    ref_shape(s)?;
                }
                match by_id.get(&e.parent) {
                    Some(par) if par.kind == "paragraph" => {}
                    _ => return Err(bad()),
                }
            }
            "list-def" => {} // registry pass above
            "style" => {
                let p = req_map(&e.body)?;
                opt_text(p, "name")?;
                if let Some(b) = get(p, "basedOn") {
                    ref_shape(b)?;
                }
            }
            "table" => {
                let p = req_map(&e.body)?;
                opt_uint(p, "cols")?;
                if let Some(s) = get(p, "style") {
                    ref_shape(s)?;
                }
                match by_id.get(&e.parent) {
                    Some(par) if matches!(par.kind.as_str(), "document" | "section" | "page" | "slide" | "cell") => {}
                    _ => return Err(bad()),
                }
                require_kids(&["row"])?;
            }
            "row" => {
                let p = req_map(&e.body)?;
                opt_uint(p, "h")?;
                match by_id.get(&e.parent) {
                    Some(par) if par.kind == "table" => {}
                    _ => return Err(bad()),
                }
                require_kids(&["cell"])?;
            }
            "cell" => {
                let p = req_map(&e.body)?;
                if let Some(g) = get(p, "gridSpan") {
                    match g {
                        Value::UInt(n) if *n >= 1 => {}
                        _ => return Err(bad()),
                    }
                }
                match by_id.get(&e.parent) {
                    Some(par) if par.kind == "row" => {}
                    _ => return Err(bad()),
                }
                require_kids(&["paragraph", "table"])?;
            }
            "canvas" => {
                let p = req_map(&e.body)?;
                match (get(p, "w"), get(p, "h")) {
                    (Some(Value::UInt(w)), Some(Value::UInt(h))) if *w > 0 && *h > 0 => {}
                    _ => return Err(bad()),
                }
                require_kids(&["shape", "image", "group"])?;
            }
            "shape" => check_shape(&e.body)?,
            "group" => {
                req_map(&e.body)?;
                require_kids(&["shape", "image", "group"])?;
            }
            "image" => {
                let p = req_map(&e.body)?;
                match get(p, "asset") {
                    Some(a) => ref_shape(a)?,
                    None => return Err(bad()),
                }
                match get(p, "mime") {
                    Some(Value::Text(m)) if !m.is_empty() => {}
                    _ => return Err(bad()),
                }
                match (get(p, "w"), get(p, "h")) {
                    (Some(Value::UInt(w)), Some(Value::UInt(h))) if *w > 0 && *h > 0 => {}
                    _ => return Err(bad()),
                }
            }
            "media" => {
                let p = req_map(&e.body)?;
                match get(p, "asset") {
                    Some(a) => ref_shape(a)?,
                    None => return Err(bad()),
                }
                match get(p, "mime") {
                    Some(Value::Text(m)) if m.starts_with("audio/") || m.starts_with("video/") => {}
                    _ => return Err(bad()),
                }
                match by_id.get(&e.parent) {
                    Some(parent) if parent.kind == "slide" => {}
                    _ => return Err(bad()),
                }
            }
            "workbook" => {
                match &e.body {
                    Value::Map(p) => {
                        if let Some(calc) = get(p, "calc") {
                            let cp = req_map(calc)?;
                            match get(cp, "mode").unwrap_or(&Value::Text("auto".to_string())) {
                                Value::Text(m) if m == "auto" || m == "manual" => {}
                                _ => return Err(bad()),
                            }
                        }
                    }
                    Value::Null => {} // absent body: defaults, kids still enforced
                    _ => return Err(bad()),
                }
                require_kids(&["sheet"])?;
            }
            "sheet" => {
                match by_id.get(&e.parent) {
                    Some(par) if par.kind == "workbook" => {}
                    _ => return Err(bad()),
                }
                if !kids.is_empty() {
                    return Err(bad());
                }
            }
            "numfmt" => {
                let p = req_map(&e.body)?;
                if !matches!(get(p, "code"), Some(Value::Text(_))) {
                    return Err(bad());
                }
            }
            "deck" => {
                match &e.body {
                    Value::Map(_) | Value::Null => {}
                    _ => return Err(bad()),
                }
                require_kids(&["slide", "master", "layout"])?;
            }
            "slide" => {
                if let Value::Map(p) = &e.body {
                    if let Some(l) = get(p, "layout") {
                        ref_shape(l)?;
                    }
                    if let Some(tr) = get(p, "transition") {
                        let tp = req_map(tr)?;
                        if !matches!(get(tp, "type"), Some(Value::Text(_))) {
                            return Err(bad());
                        }
                        opt_uint(tp, "durMs")?;
                    }
                    opt_text(p, "notes")?;
                } else if !matches!(&e.body, Value::Null) {
                    return Err(bad());
                }
                match by_id.get(&e.parent) {
                    Some(par) if par.kind == "deck" => {}
                    _ => return Err(bad()),
                }
                require_kids(&["paragraph", "shape", "image", "media", "note", "annotation", "table"])?;
            }
            "master" | "layout" => {
                match &e.body {
                    Value::Map(_) | Value::Null => {}
                    _ => return Err(bad()),
                }
            }
            _ => {} // reserved/future/vendor kinds: opaque
        }
    }
    Ok(())
}
