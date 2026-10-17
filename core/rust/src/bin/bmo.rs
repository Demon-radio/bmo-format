//! `bmo` CLI — RFC-0001 section: `bmo info|validate|object`.
//! Zero-deps argument parsing (keep the reference build offline-friendly).

use bmo_core::BmoFile;
use std::fs;
use std::process::ExitCode;

fn usage() -> &'static str {
    "usage:\n  bmo info <file.bmo> [--key <64-hex>]\n  bmo validate <file.bmo> [--key <64-hex>]\n  bmo object <file.bmo> <object_id> [--key <64-hex>]\n  bmo hello <out.bmo>   (write deterministic hello vector)\n\n--key supplies the 32-byte content key (AES-256-GCM/ChaCha20-Poly1305\ncodecs build). Without it encrypted objects fail closed with NEED_KEY.\n`object` writes the decoded payload bytes to stdout (binary-safe)."
}

fn hex_decode(s: &str) -> Result<Vec<u8>, String> {
    if s.len() % 2 != 0 {
        return Err("odd hex length".into());
    }
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(s.len() / 2);
    let nib = |c: u8| -> Result<u8, String> {
        match c {
            b'0'..=b'9' => Ok(c - b'0'),
            b'a'..=b'f' => Ok(c - b'a' + 10),
            b'A'..=b'F' => Ok(c - b'A' + 10),
            _ => Err(format!("bad hex char {:?}", c as char)),
        }
    };
    for pair in b.chunks(2) {
        out.push((nib(pair[0])? << 4) | nib(pair[1])?);
    }
    Ok(out)
}

/// Extract `--key <64-hex>` from argv (if present).
fn parse_key(args: &[String]) -> Result<Option<[u8; 32]>, String> {
    let Some(pos) = args.iter().position(|a| a == "--key") else {
        return Ok(None);
    };
    let hex = args.get(pos + 1).ok_or("--key requires a 64-hex value")?;
    let bytes = hex_decode(hex)?;
    if bytes.len() != 32 {
        return Err(format!("--key must be 32 bytes (64 hex chars), got {}", bytes.len()));
    }
    let mut key = [0u8; 32];
    key.copy_from_slice(&bytes);
    Ok(Some(key))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("{}", usage());
        return ExitCode::from(2);
    }
    let key = match parse_key(&args) {
        Ok(k) => k,
        Err(e) => {
            eprintln!("bad --key: {e}");
            return ExitCode::from(2);
        }
    };
    match args[1].as_str() {
        "info" => {
            let raw = match fs::read(&args[2]) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("read error: {e}");
                    return ExitCode::from(1);
                }
            };
            match BmoFile::open_bytes_with_key(raw, key) {
                Ok(f) => {
                    println!("{}", f.info().report());
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("open failed: {e}");
                    ExitCode::from(1)
                }
            }
        }
        "validate" => {
            let raw = match fs::read(&args[2]) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("read error: {e}");
                    return ExitCode::from(1);
                }
            };
            match BmoFile::open_bytes_with_key(raw, key) {
                Ok(f) => match f.validate() {
                    Ok(_) => {
                        println!("valid");
                        ExitCode::SUCCESS
                    }
                    Err(e) => {
                        eprintln!("invalid: {e}");
                        ExitCode::from(1)
                    }
                },
                Err(e) => {
                    eprintln!("invalid: {e}");
                    ExitCode::from(1)
                }
            }
        }
        "object" => {
            if args.len() < 4 {
                eprintln!("{}", usage());
                return ExitCode::from(2);
            }
            let oid: u64 = match args[3].parse() {
                Ok(v) => v,
                Err(_) => {
                    eprintln!("bad object_id: {}", args[3]);
                    return ExitCode::from(2);
                }
            };
            let raw = match fs::read(&args[2]) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("read error: {e}");
                    return ExitCode::from(1);
                }
            };
            match BmoFile::open_bytes_with_key(raw, key) {
                Ok(f) => match f.read_object(oid) {
                    Ok(payload) => {
                        use std::io::Write;
                        let mut so = std::io::stdout();
                        if so.write_all(&payload).and_then(|_| so.flush()).is_err() {
                            return ExitCode::from(1);
                        }
                        ExitCode::SUCCESS
                    }
                    Err(e) => {
                        eprintln!("object failed: {e}");
                        ExitCode::from(1)
                    }
                },
                Err(e) => {
                    eprintln!("open failed: {e}");
                    ExitCode::from(1)
                }
            }
        }
        "hello" => {
            let mut id = [0u8; 16];
            id[15] = 1;
            let bytes = bmo_core::file::build_hello(id);
            match fs::write(&args[2], &bytes) {
                Ok(()) => {
                    println!("wrote {} ({} bytes)", args[2], bytes.len());
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("write error: {e}");
                    ExitCode::from(1)
                }
            }
        }
        _ => {
            eprintln!("{}", usage());
            ExitCode::from(2)
        }
    }
}
