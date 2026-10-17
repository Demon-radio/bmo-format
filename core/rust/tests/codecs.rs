//! Codec integration tests (feature `codecs`): official AES-GCM + RFC 8439
//! ChaCha20-Poly1305 vectors, DEFLATE/XChaCha framing and fail-closed codes.
//! Mirrors the Python kit's tests/conformance/test_codecs.py one-to-one;
//! run with `--features codecs`.
#![cfg(feature = "codecs")]

use bmo_core::chunk::{
    Chunk, ChunkHeader, COMP_DEFLATE, COMP_ZSTD, ENC_AES_GCM, ENC_CHACHA20_POLY1305,
    ENC_XCHACHA20_POLY1305, T_GENERIC_OBJECT,
};
use bmo_core::codec;
use bmo_core::crc32c::crc32c;
use bmo_core::error::BmoError;
use bmo_core::sha256::sha256;

/// Same key derivation as the Python suite: sha256(b"bmo-test-aes-key").
fn test_key() -> [u8; 32] {
    sha256(b"bmo-test-aes-key")
}
const NONCE: [u8; 12] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap())
        .collect()
}

/// Frame stored bytes exactly like the writer pipeline (header + CRC).
fn frame(ctype: u32, flags: u16, oid: u64, stored: Vec<u8>, decoded_len: u64, codec: u32) -> Vec<u8> {
    let header = ChunkHeader {
        chunk_type: ctype,
        version: 1,
        flags,
        object_id: oid,
        stored_len: stored.len() as u64,
        decoded_len,
        codec,
    };
    Chunk { header, payload: Vec::new(), raw_stored: stored }.to_bytes()
}

/// Repair the payload CRC trailer after mutating stored bytes at `pos`.
fn retrailer(buf: &mut [u8], pos: usize) {
    let stored = buf[40..pos].to_vec();
    let c = crc32c(&stored).to_le_bytes();
    buf[pos..pos + 4].copy_from_slice(&c);
}

#[test]
fn wycheproof_aes_gcm() {
    // C2SP/wycheproof aes_gcm_test.json, 256/96/128 case.
    let key = unhex("59d4eafb4de0cfc7d3db99a8f54b15d7b39f0acc8da69763b019c1699f87674a");
    let key: [u8; 32] = key.try_into().unwrap();
    let mut stored = unhex("2fcb1b38a99e71b84740ad9b"); // iv
    stored.extend(unhex("f58c16690122d75356907fd96b570fca28752c20153092818faba2a334640d6e")); // ct||tag
    let msg = codec::aes_gcm_decrypt(&key, &stored).expect("valid vector must decrypt");
    assert_eq!(msg, unhex("549b365af913f3b081131ccb6b825588"));

    // Bad-tag vector must fail closed.
    let bkey: [u8; 32] = unhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
        .try_into()
        .unwrap();
    let mut bad = unhex("505152535455565758595a5b");
    bad.extend(unhex("b2061457c0759fc1749f174ee1ccadfa9de8fef6d8ab1bf1bf887232eab590dd"));
    assert_eq!(codec::aes_gcm_decrypt(&bkey, &bad), Err(BmoError::AuthFailed));
}

#[test]
fn aes_chunk_roundtrip() {
    let key = test_key();
    let plain = b"aes secret".to_vec();
    let stored = codec::aes_gcm_encrypt(&key, &NONCE, &plain).unwrap();
    let buf = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, stored.clone(), plain.len() as u64, (ENC_AES_GCM as u32) << 16);

    let c = Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&key)).expect("keyed read");
    assert_eq!(c.payload, plain);
    assert_eq!(c.header.encryption_id(), ENC_AES_GCM);
    assert_eq!(c.header.stored_len, stored.len() as u64);
}

#[test]
fn aes_need_key_and_wrong_key_and_tamper() {
    let key = test_key();
    let plain = b"aes secret".to_vec();
    let stored = codec::aes_gcm_encrypt(&key, &NONCE, &plain).unwrap();
    let buf = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, stored.clone(), plain.len() as u64, (ENC_AES_GCM as u32) << 16);

    // Keyless: fail closed with NEED_KEY (same as Python open_file).
    assert_eq!(Chunk::read_at(&buf, 0, buf.len() as u64).unwrap_err(), BmoError::NeedKey);

    // Wrong key: AUTH_FAILED.
    let other = sha256(b"not-the-key");
    assert_eq!(
        Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&other)).unwrap_err(),
        BmoError::AuthFailed
    );

    // Tamper a tag byte, repair the trailer CRC -> still AUTH_FAILED.
    // (CRC is checked over stored bytes before any decode — same as Python.)
    let mut mut_buf = buf.clone();
    let tag_at = 40 + stored.len() - 16;
    mut_buf[tag_at] ^= 1;
    let end = 40 + stored.len();
    retrailer(&mut mut_buf, end);
    assert_eq!(
        Chunk::read_at_with_key(&mut_buf, 0, mut_buf.len() as u64, Some(&key)).unwrap_err(),
        BmoError::AuthFailed
    );

    // Too-short stored payload: CORRUPT, never a library panic.
    let short = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, vec![0u8; 12 + 15], 0, (ENC_AES_GCM as u32) << 16);
    assert_eq!(
        Chunk::read_at_with_key(&short, 0, short.len() as u64, Some(&key)).unwrap_err(),
        BmoError::Corrupt
    );
}

#[test]
fn zstd_roundtrip_and_corruption() {
    let plain = b"compress me ".repeat(200);
    let stored = codec::zstd_compress(&plain);
    let codec_id = COMP_ZSTD as u32;
    let buf = frame(0x0010, 0x000C | 0x0001, 7, stored.clone(), plain.len() as u64, codec_id);

    let c = Chunk::read_at(&buf, 0, buf.len() as u64).expect("zstd read");
    assert_eq!(c.payload, plain);
    assert!(c.header.stored_len < c.header.decoded_len, "should actually compress");

    // Corrupt the stream, keep CRC valid -> CORRUPT (never a raw exception).
    let mut mut_buf = buf.clone();
    mut_buf[44] ^= 0xFF;
    let end = 40 + stored.len();
    retrailer(&mut mut_buf, end);
    assert_eq!(Chunk::read_at(&mut_buf, 0, mut_buf.len() as u64).unwrap_err(), BmoError::Corrupt);

    // Lying decoded_len -> CORRUPT (exact-size contract).
    let lying = frame(0x0010, 0x000C | 0x0001, 7, stored.clone(), plain.len() as u64 + 1, codec_id);
    assert_eq!(Chunk::read_at(&lying, 0, lying.len() as u64).unwrap_err(), BmoError::Corrupt);
}

#[test]
fn combined_order_compress_then_encrypt() {
    let key = test_key();
    let plain = b"order matters ".repeat(100);
    let stored = codec::aes_gcm_encrypt(&key, &NONCE, &codec::zstd_compress(&plain)).unwrap();
    let codec_id = ((ENC_AES_GCM as u32) << 16) | COMP_ZSTD as u32;
    let buf = frame(0x0010, 0x000C | 0x0003, 9, stored, plain.len() as u64, codec_id);

    let c = Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&key)).expect("combined read");
    assert_eq!(c.payload, plain);
}

#[test]
fn deflate_roundtrip_and_corruption() {
    let plain = b"deflate stream ".repeat(100);
    let stored = flate2::write::ZlibEncoder::new(
        Vec::new(),
        flate2::Compression::default(),
    );
    use std::io::Write;
    let mut encoder = stored;
    encoder.write_all(&plain).unwrap();
    let stored = encoder.finish().unwrap();
    let buf = frame(
        T_GENERIC_OBJECT,
        0x000C | 0x0001,
        10,
        stored.clone(),
        plain.len() as u64,
        COMP_DEFLATE as u32,
    );
    let c = Chunk::read_at(&buf, 0, buf.len() as u64).expect("deflate read");
    assert_eq!(c.payload, plain);

    let mut corrupt = stored;
    let corrupt_at = corrupt.len() / 2;
    corrupt[corrupt_at] ^= 0x80;
    let bad = frame(
        T_GENERIC_OBJECT,
        0x000C | 0x0001,
        10,
        corrupt,
        plain.len() as u64,
        COMP_DEFLATE as u32,
    );
    assert_eq!(
        Chunk::read_at(&bad, 0, bad.len() as u64).unwrap_err(),
        BmoError::Corrupt
    );
}

#[test]
fn xchacha_chunk_roundtrip_and_authentication() {
    let key = test_key();
    let nonce: [u8; 24] = std::array::from_fn(|i| i as u8);
    let plain = b"xchacha secret".to_vec();
    let stored = codec::xchacha20poly1305_encrypt(&key, &nonce, &plain).unwrap();
    assert_eq!(stored.len(), 24 + plain.len() + 16);
    let buf = frame(
        T_GENERIC_OBJECT,
        0x000C | 0x0002,
        11,
        stored.clone(),
        plain.len() as u64,
        (ENC_XCHACHA20_POLY1305 as u32) << 16,
    );
    assert_eq!(
        Chunk::read_at(&buf, 0, buf.len() as u64).unwrap_err(),
        BmoError::NeedKey
    );
    assert_eq!(
        Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&key))
            .unwrap()
            .payload,
        plain
    );
    let mut tampered = buf.clone();
    let tag_at = 40 + stored.len() - 1;
    tampered[tag_at] ^= 1;
    retrailer(&mut tampered, 40 + stored.len());
    assert_eq!(
        Chunk::read_at_with_key(&tampered, 0, tampered.len() as u64, Some(&key))
            .unwrap_err(),
        BmoError::AuthFailed
    );
}

#[test]
fn rfc8439_chacha20poly1305_vector() {
    // RFC 8439 section 2.8.2 (AEAD_CHACHA20_POLY1305). Non-empty AAD, so the
    // official vector is verified through the audited primitive directly;
    // BMO's chunk path (empty AAD) is covered by roundtrip + interop parity.
    use chacha20poly1305::aead::{Aead, KeyInit, Payload};
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce};

    let key: [u8; 32] = unhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        .try_into()
        .unwrap();
    let nonce_bytes: [u8; 12] = unhex("070000004041424344454647").try_into().unwrap();
    let nonce = Nonce::from(nonce_bytes);
    let aad = unhex("50515253c0c1c2c3c4c5c6c7");
    let mut ct = unhex(
        "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d63dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b3692ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3ff4def08e4b7a9de576d26586cec64b6116",
    );
    ct.extend(unhex("1ae10b594f09e26a7e902ecbd0600691")); // tag

    let cipher = ChaCha20Poly1305::new(&Key::from(key));
    let msg = cipher
        .decrypt(&nonce, Payload { msg: &ct, aad: &aad })
        .expect("valid RFC 8439 vector must decrypt");
    assert_eq!(
        String::from_utf8(msg).unwrap(),
        "Ladies and Gentlemen of the class of '99: If I could offer you \
         only one tip for the future, sunscreen would be it."
    );

    // Flip one tag byte: authentication must fail.
    let last = ct.len() - 1;
    ct[last] ^= 1;
    assert!(cipher.decrypt(&nonce, Payload { msg: &ct, aad: &aad }).is_err());
}

#[test]
fn chacha_chunk_roundtrip() {
    let key = test_key();
    let plain = b"chacha secret".to_vec();
    let stored = codec::chacha20poly1305_encrypt(&key, &NONCE, &plain).unwrap();
    let codec_id = (ENC_CHACHA20_POLY1305 as u32) << 16;
    let buf = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, stored.clone(), plain.len() as u64, codec_id);

    let c = Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&key)).expect("keyed read");
    assert_eq!(c.payload, plain);
    assert_eq!(c.header.encryption_id(), ENC_CHACHA20_POLY1305);
    assert_eq!(c.header.stored_len, stored.len() as u64);

    // Cross-implementation known answer: the same key/nonce/plaintext run
    // through Python `cryptography` (ChaCha20Poly1305.encrypt, None AAD)
    // must produce identical bytes. This is the byte-parity anchor between
    // the two implementations (interop test repeats it end-to-end).
    assert_eq!(
        stored,
        unhex("000102030405060708090a0b65bd5e9208264e6771e6c71255e24c1b9fa5e6246fb49f55f88dd20601")
    );
}

#[test]
fn chacha_need_key_wrong_key_and_tamper() {
    let key = test_key();
    let plain = b"chacha secret".to_vec();
    let stored = codec::chacha20poly1305_encrypt(&key, &NONCE, &plain).unwrap();
    let codec_id = (ENC_CHACHA20_POLY1305 as u32) << 16;
    let buf = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, stored.clone(), plain.len() as u64, codec_id);

    // Keyless: fail closed with NEED_KEY (same as Python open_file).
    assert_eq!(Chunk::read_at(&buf, 0, buf.len() as u64).unwrap_err(), BmoError::NeedKey);

    // Wrong key: AUTH_FAILED.
    let other = sha256(b"not-the-key");
    assert_eq!(
        Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&other)).unwrap_err(),
        BmoError::AuthFailed
    );

    // Tamper a tag byte, repair the trailer CRC -> still AUTH_FAILED.
    let mut mut_buf = buf.clone();
    let tag_at = 40 + stored.len() - 16;
    mut_buf[tag_at] ^= 1;
    let end = 40 + stored.len();
    retrailer(&mut mut_buf, end);
    assert_eq!(
        Chunk::read_at_with_key(&mut_buf, 0, mut_buf.len() as u64, Some(&key)).unwrap_err(),
        BmoError::AuthFailed
    );

    // Too-short stored payload: CORRUPT, never a library panic.
    let short = frame(T_GENERIC_OBJECT, 0x000C | 0x0002, 1, vec![0u8; 12 + 15], 0, codec_id);
    assert_eq!(
        Chunk::read_at_with_key(&short, 0, short.len() as u64, Some(&key)).unwrap_err(),
        BmoError::Corrupt
    );
}

#[test]
fn chacha_combined_order_compress_then_encrypt() {
    let key = test_key();
    let plain = b"order matters ".repeat(100);
    let stored = codec::chacha20poly1305_encrypt(&key, &NONCE, &codec::zstd_compress(&plain)).unwrap();
    let codec_id = ((ENC_CHACHA20_POLY1305 as u32) << 16) | COMP_ZSTD as u32;
    let buf = frame(0x0010, 0x000C | 0x0003, 9, stored, plain.len() as u64, codec_id);

    let c = Chunk::read_at_with_key(&buf, 0, buf.len() as u64, Some(&key)).expect("combined read");
    assert_eq!(c.payload, plain);
}

#[test]
fn unknown_codec_upgrades_cleanly() {
    let hdr = ChunkHeader {
        chunk_type: 0x0010,
        version: 1,
        flags: 0x0001,
        object_id: 0,
        stored_len: 4,
        decoded_len: 4,
        codec: 9, // not in the registry
    };
    assert_eq!(
        ChunkHeader::parse(&hdr.serialize()).unwrap_err(),
        BmoError::UpgradeRequired
    );

    // enc=4 is reserved and stays fail-closed even with executable codecs.
    let unknown_encryption = ChunkHeader {
        chunk_type: 0x0010,
        version: 1,
        flags: 0x0002,
        object_id: 0,
        stored_len: 4,
        decoded_len: 4,
        codec: (4u32) << 16,
    };
    assert_eq!(
        ChunkHeader::parse(&unknown_encryption.serialize()).unwrap_err(),
        BmoError::UpgradeRequired
    );
}
