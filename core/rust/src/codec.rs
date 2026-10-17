//! Executable codec paths (optional `codecs` feature): ZSTD/DEFLATE
//! decompression and AES-256-GCM / ChaCha20-Poly1305 / XChaCha20-Poly1305
//! decryption/encryption. Semantics
//! mirror the Python kit exactly (RFC-0001 section 9 registry, RFC-0007
//! section 1 suites, write order compress-then-encrypt, read order
//! decrypt-then-decompress, fail-closed error codes only).
//!
//! Libraries are standard implementations (RustCrypto AEADs, pure-Rust
//! `ruzstd` decoder and `flate2`); no cipher is invented here.

use crate::error::{BmoError, BmoResult};

/// AES-256-GCM. Stored layout: nonce(12) || ciphertext || tag(16).
/// AAD is empty (matches `AESGCM(key).encrypt(nonce, payload, None)`).
pub fn aes_gcm_decrypt(key: &[u8; 32], stored: &[u8]) -> BmoResult<Vec<u8>> {
    use aes_gcm::aead::{Aead, KeyInit};
    use aes_gcm::{Aes256Gcm, Key, Nonce};

    if stored.len() < 12 + 16 {
        return Err(BmoError::Corrupt);
    }
    let cipher = Aes256Gcm::new(&Key::<Aes256Gcm>::from(*key));
    let nonce: [u8; 12] = stored[..12].try_into().unwrap();
    let nonce = Nonce::<aes_gcm::aes::cipher::consts::U12>::from(nonce);
    cipher.decrypt(&nonce, &stored[12..]).map_err(|_| BmoError::AuthFailed)
}

/// Inverse of [`aes_gcm_decrypt`] (same layout + empty AAD).
pub fn aes_gcm_encrypt(key: &[u8; 32], nonce: &[u8; 12], plain: &[u8]) -> BmoResult<Vec<u8>> {
    use aes_gcm::aead::{Aead, KeyInit};
    use aes_gcm::{Aes256Gcm, Key, Nonce};

    let cipher = Aes256Gcm::new(&Key::<Aes256Gcm>::from(*key));
    let nonce = Nonce::<aes_gcm::aes::cipher::consts::U12>::from(*nonce);
    let mut out = Vec::with_capacity(12 + plain.len() + 16);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&cipher.encrypt(&nonce, plain).map_err(|_| BmoError::Corrupt)?);
    Ok(out)
}

/// ChaCha20-Poly1305 (RFC 8439, enc=2 per RFC-0007 section 1).
/// Stored layout: nonce(12) || ciphertext || tag(16). AAD is empty
/// (matches `ChaCha20Poly1305(key).encrypt(nonce, payload, None)`).
pub fn chacha20poly1305_decrypt(key: &[u8; 32], stored: &[u8]) -> BmoResult<Vec<u8>> {
    use chacha20poly1305::aead::{Aead, KeyInit};
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce};

    if stored.len() < 12 + 16 {
        return Err(BmoError::Corrupt);
    }
    let cipher = ChaCha20Poly1305::new(&Key::from(*key));
    let nonce: [u8; 12] = stored[..12].try_into().unwrap();
    let nonce = Nonce::from(nonce);
    cipher.decrypt(&nonce, &stored[12..]).map_err(|_| BmoError::AuthFailed)
}

/// Inverse of [`chacha20poly1305_decrypt`] (same layout + empty AAD).
pub fn chacha20poly1305_encrypt(key: &[u8; 32], nonce: &[u8; 12], plain: &[u8]) -> BmoResult<Vec<u8>> {
    use chacha20poly1305::aead::{Aead, KeyInit};
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce};

    let cipher = ChaCha20Poly1305::new(&Key::from(*key));
    let nonce = Nonce::from(*nonce);
    let mut out = Vec::with_capacity(12 + plain.len() + 16);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&cipher.encrypt(&nonce, plain).map_err(|_| BmoError::Corrupt)?);
    Ok(out)
}

/// XChaCha20-Poly1305 (RFC-0007 section 1). Stored layout:
/// nonce(24) || ciphertext || tag(16); AAD is empty.
pub fn xchacha20poly1305_decrypt(key: &[u8; 32], stored: &[u8]) -> BmoResult<Vec<u8>> {
    use chacha20poly1305::aead::{Aead, KeyInit};
    use chacha20poly1305::{Key, XChaCha20Poly1305, XNonce};

    if stored.len() < 24 + 16 {
        return Err(BmoError::Corrupt);
    }
    let cipher = XChaCha20Poly1305::new(&Key::from(*key));
    let nonce: [u8; 24] = stored[..24].try_into().unwrap();
    let nonce = XNonce::from(nonce);
    cipher.decrypt(&nonce, &stored[24..]).map_err(|_| BmoError::AuthFailed)
}

/// Inverse of [`xchacha20poly1305_decrypt`] (same layout + empty AAD).
pub fn xchacha20poly1305_encrypt(key: &[u8; 32], nonce: &[u8; 24], plain: &[u8]) -> BmoResult<Vec<u8>> {
    use chacha20poly1305::aead::{Aead, KeyInit};
    use chacha20poly1305::{Key, XChaCha20Poly1305, XNonce};

    let cipher = XChaCha20Poly1305::new(&Key::from(*key));
    let nonce = XNonce::from(*nonce);
    let mut out = Vec::with_capacity(24 + plain.len() + 16);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&cipher.encrypt(&nonce, plain).map_err(|_| BmoError::Corrupt)?);
    Ok(out)
}

/// ZSTD frame decompression with an exact expected output size (the chunk's
/// `decoded_len`). Any library error or size mismatch is CORRUPT — library
/// exceptions never leak (fuzz-closed, same as the Python kit).
pub fn zstd_decompress(data: &[u8], expect: usize) -> BmoResult<Vec<u8>> {
    use std::io::Read;

    let mut src = std::io::Cursor::new(data);
    let dec = ruzstd::decoding::StreamingDecoder::new(&mut src).map_err(|_| BmoError::Corrupt)?;
    let mut out = Vec::with_capacity(expect.min(1 << 20));
    // Bound reads so a lying decoded_len cannot balloon memory: ask for at
    // most expect + 1 bytes, then require an exact match.
    let mut limited = dec.take(expect as u64 + 1);
    limited.read_to_end(&mut out).map_err(|_| BmoError::Corrupt)?;
    if out.len() != expect {
        return Err(BmoError::Corrupt);
    }
    Ok(out)
}

/// Test/vector helper: zstd-compress with the pure-Rust encoder (ruzstd).
/// NOTE: writer bytes need not match libzstd output — BMO never requires
/// byte-identical compressed payloads, only readable frames (RFC-0001 §9).
pub fn zstd_compress(data: &[u8]) -> Vec<u8> {
    ruzstd::encoding::compress_to_vec(data, ruzstd::encoding::CompressionLevel::Fastest)
}

/// RFC 1950 zlib-wrapped DEFLATE with an exact output-size bound.
pub fn deflate_decompress(data: &[u8], expect: usize) -> BmoResult<Vec<u8>> {
    use std::io::Read;

    let decoder = flate2::read::ZlibDecoder::new(data);
    let mut out = Vec::with_capacity(expect.min(1 << 20));
    let mut limited = decoder.take(expect as u64 + 1);
    limited.read_to_end(&mut out).map_err(|_| BmoError::Corrupt)?;
    let decoder = limited.into_inner();
    if out.len() != expect || decoder.total_in() as usize != data.len() {
        return Err(BmoError::Corrupt);
    }
    Ok(out)
}
