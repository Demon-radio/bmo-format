//! Optional RFC-0007 ECDSA and RSA-PSS signature verification.
//! Trust/key discovery is deliberately left to the caller.

use crate::error::{BmoError, BmoResult};

/// Verify an RFC-0007 ECDSA-P256-SHA256 signature over the 32-byte
/// canonical hash. The public key is SubjectPublicKeyInfo DER and the
/// ECDSA signature uses ASN.1 DER encoding.
pub fn verify_ecdsa_p256_sha256(
    public_key_spki_der: &[u8],
    canonical_hash: &[u8; 32],
    signature_der: &[u8],
) -> BmoResult<()> {
    use p256::ecdsa::{signature::Verifier, Signature, VerifyingKey};
    use p256::pkcs8::DecodePublicKey;

    let key = VerifyingKey::from_public_key_der(public_key_spki_der)
        .map_err(|_| BmoError::BadStructure)?;
    let signature = Signature::from_der(signature_der).map_err(|_| BmoError::BadStructure)?;
    key.verify(canonical_hash, &signature)
        .map_err(|_| BmoError::AuthFailed)
}

/// Verify RFC-0007 RSA-PSS-SHA256 over the 32-byte canonical hash.
/// The public key is SubjectPublicKeyInfo DER; MGF1 uses SHA-256 and the
/// salt length is exactly 32 bytes (the SHA-256 digest size).
pub fn verify_rsa_pss_sha256(
    public_key_spki_der: &[u8],
    canonical_hash: &[u8; 32],
    signature_bytes: &[u8],
) -> BmoResult<()> {
    use rsa::pkcs8::DecodePublicKey;
    use rsa::pss::{Signature, VerifyingKey};
    use rsa::signature::Verifier;
    use rsa::RsaPublicKey;
    use sha2::Sha256;

    let key = RsaPublicKey::from_public_key_der(public_key_spki_der)
        .map_err(|_| BmoError::BadStructure)?;
    let verifier = VerifyingKey::<Sha256>::new_with_salt_len(key, 32);
    let signature = Signature::try_from(signature_bytes).map_err(|_| BmoError::BadStructure)?;
    verifier
        .verify(canonical_hash, &signature)
        .map_err(|_| BmoError::AuthFailed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn verifies_ecdsa_and_rejects_tampering() {
        use p256::ecdsa::signature::Signer;
        use p256::ecdsa::{SigningKey, VerifyingKey};
        use p256::pkcs8::EncodePublicKey;

        let signing_key = SigningKey::from_slice(&[7; 32]).unwrap();
        let verifying_key = VerifyingKey::from(&signing_key);
        let public_key = verifying_key.to_public_key_der().unwrap();
        let hash = [0x42; 32];
        let signature: p256::ecdsa::Signature = signing_key.sign(&hash);
        let signature_der = signature.to_der();

        verify_ecdsa_p256_sha256(public_key.as_bytes(), &hash, signature_der.as_bytes()).unwrap();
        let mut changed = hash;
        changed[0] ^= 1;
        assert_eq!(
            verify_ecdsa_p256_sha256(public_key.as_bytes(), &changed, signature_der.as_bytes()),
            Err(BmoError::AuthFailed)
        );
    }

    #[test]
    fn verifies_rsa_pss_and_rejects_tampering() {
        use rand::rngs::OsRng;
        use rsa::pkcs8::EncodePublicKey;
        use rsa::pss::SigningKey;
        use rsa::signature::{RandomizedSigner, SignatureEncoding};
        use rsa::{RsaPrivateKey, RsaPublicKey};
        use sha2::Sha256;

        let private_key = RsaPrivateKey::new(&mut OsRng, 2048).unwrap();
        let public_key = RsaPublicKey::from(&private_key).to_public_key_der().unwrap();
        let hash = [0x24; 32];
        let signer = SigningKey::<Sha256>::new_with_salt_len(private_key, 32);
        let signature = signer.sign_with_rng(&mut OsRng, &hash);
        let signature = signature.to_vec();

        verify_rsa_pss_sha256(public_key.as_bytes(), &hash, &signature).unwrap();
        let mut changed = hash;
        changed[0] ^= 1;
        assert_eq!(
            verify_rsa_pss_sha256(public_key.as_bytes(), &changed, &signature),
            Err(BmoError::AuthFailed)
        );
    }
}
