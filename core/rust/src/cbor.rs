//! Minimal deterministic CBOR (RFC 8949) — RFC-0001 section 2.5.
//! Supports what the container control structures need:
//! uint/int, bytes, text, array, map (sorted keys), bool, null.
//! Writers emit shortest-form + sorted keys. The decoder accepts any
//! well-formed input of these types (plus skip of tags/simple/float
//! for forward tolerance — floats are rejected as values we produce
//! but skipped safely when encountered in unknown keys we preserve
//! at the raw-byte level by the caller).

use crate::error::{BmoError, BmoResult};

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    UInt(u64),
    Int(i64),
    Float(f64),
    Bytes(Vec<u8>),
    Text(String),
    Array(Vec<Value>),
    Map(Vec<(Value, Value)>),
    Bool(bool),
    Null,
}

fn enc_head(major: u8, n: u64, out: &mut Vec<u8>) {
    if n < 24 {
        out.push((major << 5) | n as u8);
    } else if n < 256 {
        out.push((major << 5) | 24);
        out.push(n as u8);
    } else if n < 65536 {
        out.push((major << 5) | 25);
        out.extend_from_slice(&(n as u16).to_be_bytes());
    } else if n < 0x1_0000_0000 {
        out.push((major << 5) | 26);
        out.extend_from_slice(&(n as u32).to_be_bytes());
    } else {
        out.push((major << 5) | 27);
        out.extend_from_slice(&n.to_be_bytes());
    }
}

/// Deterministic encoding of a Value.
pub fn encode(v: &Value) -> Vec<u8> {
    let mut out = Vec::new();
    encode_into(v, &mut out);
    out
}

fn encode_into(v: &Value, out: &mut Vec<u8>) {
    match v {
        Value::UInt(n) => enc_head(0, *n, out),
        Value::Float(x) => {
            // Shortest round-tripping form (writers in v0.x never emit
            // floats; this keeps encode() total for decoded values).
            // NaN is NOT canonical — we still encode it (f32) rather than
            // panic; validators reject NaN in numeric positions.
            let as_f32 = *x as f32;
            if (as_f32 as f64) == *x || x.is_nan() {
                out.push(0xfa);
                out.extend_from_slice(&as_f32.to_be_bytes());
            } else {
                out.push(0xfb);
                out.extend_from_slice(&x.to_be_bytes());
            }
        }
        Value::Int(n) => {
            if *n >= 0 {
                enc_head(0, *n as u64, out);
            } else {
                enc_head(1, (-1 - *n) as u64, out);
            }
        }
        Value::Bytes(b) => {
            enc_head(2, b.len() as u64, out);
            out.extend_from_slice(b);
        }
        Value::Text(s) => {
            enc_head(3, s.len() as u64, out);
            out.extend_from_slice(s.as_bytes());
        }
        Value::Array(items) => {
            enc_head(4, items.len() as u64, out);
            for it in items {
                encode_into(it, out);
            }
        }
        Value::Map(pairs) => {
            // Deterministic: sort by encoded key bytes.
            let mut enc: Vec<(Vec<u8>, Vec<u8>)> = pairs
                .iter()
                .map(|(k, val)| {
                    let mut kb = Vec::new();
                    encode_into(k, &mut kb);
                    let mut vb = Vec::new();
                    encode_into(val, &mut vb);
                    (kb, vb)
                })
                .collect();
            enc.sort_by(|a, b| a.0.cmp(&b.0));
            enc_head(5, enc.len() as u64, out);
            for (kb, vb) in enc {
                out.extend_from_slice(&kb);
                out.extend_from_slice(&vb);
            }
        }
        Value::Bool(true) => out.push(0xf5),
        Value::Bool(false) => out.push(0xf4),
        Value::Null => out.push(0xf6),
    }
}

pub fn uint(n: u64) -> Value {
    Value::UInt(n)
}
pub fn text(s: &str) -> Value {
    Value::Text(s.to_string())
}
pub fn bytes(b: &[u8]) -> Value {
    Value::Bytes(b.to_vec())
}

struct Cursor<'a> {
    b: &'a [u8],
    pos: usize,
}

fn read_n(c: &mut Cursor, n: usize) -> BmoResult<Vec<u8>> {
    if c.pos + n > c.b.len() {
        return Err(BmoError::Corrupt);
    }
    let v = c.b[c.pos..c.pos + n].to_vec();
    c.pos += n;
    Ok(v)
}

fn read_arg(c: &mut Cursor, ai: u8) -> BmoResult<u64> {
    match ai {
        0..=23 => Ok(ai as u64),
        24 => Ok(read_n(c, 1)?[0] as u64),
        25 => Ok(u16::from_be_bytes(read_n(c, 2)?[..].try_into().unwrap()) as u64),
        26 => Ok(u32::from_be_bytes(read_n(c, 4)?[..].try_into().unwrap()) as u64),
        27 => Ok(u64::from_be_bytes(read_n(c, 8)?[..].try_into().unwrap())),
        _ => Err(BmoError::Corrupt), // indefinite lengths rejected at container level
    }
}

/// IEEE 754 binary16 → f64 (for decoding only; never emitted).
fn f16_to_f64(bits: u16) -> f64 {
    let sign = ((bits >> 15) & 1) as u64;
    let exp = ((bits >> 10) & 0x1F) as i32;
    let mant = (bits & 0x3FF) as u64;
    if exp == 0 {
        let v = (mant as f64) * 2f64.powi(-24);
        return if sign == 1 { -v } else { v };
    }
    if exp == 31 {
        return if mant == 0 {
            if sign == 1 { f64::NEG_INFINITY } else { f64::INFINITY }
        } else {
            f64::NAN
        };
    }
    f64::from_bits((sign << 63) | (((exp - 15 + 1023) as u64) << 52) | (mant << 42))
}

/// Decode one generic value. Unknown simple/float/tag payloads are
/// rejected (callers that need byte-preservation keep raw payloads).
pub fn decode(data: &[u8]) -> BmoResult<(Value, usize)> {
    let mut c = Cursor { b: data, pos: 0 };
    let v = decode_one(&mut c)?;
    Ok((v, c.pos))
}

fn decode_one(c: &mut Cursor) -> BmoResult<Value> {
    let ib = read_n(c, 1)?[0];
    let major = ib >> 5;
    let ai = ib & 0x1f;
    match major {
        0 => Ok(Value::UInt(read_arg(c, ai)?)),
        1 => {
            let n = read_arg(c, ai)?;
            if n > i64::MAX as u64 {
                return Err(BmoError::Corrupt);
            }
            Ok(Value::Int(-1 - n as i64))
        }
        2 => {
            let n = read_arg(c, ai)? as usize;
            Ok(Value::Bytes(read_n(c, n)?))
        }
        3 => {
            let n = read_arg(c, ai)? as usize;
            let b = read_n(c, n)?;
            let s = String::from_utf8(b).map_err(|_| BmoError::Corrupt)?;
            Ok(Value::Text(s))
        }
        4 => {
            let n = read_arg(c, ai)? as usize;
            if n > 1_000_000 {
                return Err(BmoError::LimitExceeded);
            }
            let mut items = Vec::with_capacity(n.min(1024));
            for _ in 0..n {
                items.push(decode_one(c)?);
            }
            Ok(Value::Array(items))
        }
        5 => {
            let n = read_arg(c, ai)? as usize;
            if n > 1_000_000 {
                return Err(BmoError::LimitExceeded);
            }
            let mut pairs = Vec::with_capacity(n.min(1024));
            for _ in 0..n {
                let k = decode_one(c)?;
                let v = decode_one(c)?;
                pairs.push((k, v));
            }
            Ok(Value::Map(pairs))
        }
        7 => match ai {
            20 => Ok(Value::Bool(false)),
            21 => Ok(Value::Bool(true)),
            22 => Ok(Value::Null),
            25 => {
                let bits = u16::from_be_bytes(read_n(c, 2)?[..].try_into().unwrap());
                Ok(Value::Float(f16_to_f64(bits)))
            }
            26 => {
                let bits = u32::from_be_bytes(read_n(c, 4)?[..].try_into().unwrap());
                Ok(Value::Float(f32::from_be_bytes(bits.to_be_bytes()) as f64))
            }
            27 => {
                let bits = u64::from_be_bytes(read_n(c, 8)?[..].try_into().unwrap());
                Ok(Value::Float(f64::from_be_bytes(bits.to_be_bytes())))
            }
            _ => Err(BmoError::Corrupt),
        },
        _ => Err(BmoError::Corrupt),
    }
}

/// Look up a text key inside a decoded map.
pub fn map_get<'a>(map: &'a [(Value, Value)], key: &str) -> Option<&'a Value> {
    map.iter().find_map(|(k, v)| match k {
        Value::Text(s) if s == key => Some(v),
        _ => None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn deterministic_key_order() {
        let m = Value::Map(vec![(text("b"), uint(2)), (text("a"), uint(1))]);
        let e = encode(&m);
        // "a":1 then "b":2  => a5 6161 01 6162 02
        assert_eq!(e, vec![0xa2, 0x61, b'a', 0x01, 0x61, b'b', 0x02]);
        let (v, n) = decode(&e).unwrap();
        assert_eq!(n, e.len());
        assert_eq!(v, Value::Map(vec![(text("a"), uint(1)), (text("b"), uint(2))]));
    }
}
