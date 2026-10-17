//! BMO error codes (RFC-0001 sections 12-14, 17).
//! Readers MUST surface these distinctly — never a generic "parse failed".

use std::fmt;

/// Machine-readable failure reason. Display strings are stable and
/// matched by the conformance suite.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BmoError {
    TooSmall,
    BadMagic,
    BadHeaderLength,
    UnsupportedVersion,
    HeaderCrc,
    ChunkHeaderCrc,
    PayloadCrc,
    FooterCrc,
    NoValidFooter,
    Truncated,
    DuplicateObjectId,
    DirectoryUnsorted,
    UnknownCritical,
    UnknownExtension,
    UpgradeRequired,
    IncompatibleMajor,
    LimitExceeded,
    AuthFailed,
    NeedKey, // Encrypted chunk, no content key supplied (fail closed).
    // NOTE: codec decrypt/decompress paths are implemented in the Python
    // reference kit and verified there; the Rust core returns
    // UPGRADE_REQUIRED for coded chunks until a toolchain builds it.
    DocIdMismatch,
    TxnMismatch,
    RootMismatch,
    Corrupt,
    BadStructure,
    DanglingRef,
}

impl fmt::Display for BmoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            BmoError::TooSmall => "TOO_SMALL",
            BmoError::BadMagic => "BAD_MAGIC",
            BmoError::BadHeaderLength => "BAD_HEADER_LENGTH",
            BmoError::UnsupportedVersion => "UNSUPPORTED_VERSION",
            BmoError::HeaderCrc => "HEADER_CRC_MISMATCH",
            BmoError::ChunkHeaderCrc => "CHUNK_HEADER_CRC_MISMATCH",
            BmoError::PayloadCrc => "PAYLOAD_CRC_MISMATCH",
            BmoError::FooterCrc => "FOOTER_CRC_MISMATCH",
            BmoError::NoValidFooter => "NO_VALID_FOOTER",
            BmoError::Truncated => "TRUNCATED",
            BmoError::DuplicateObjectId => "DUPLICATE_OBJECT_ID",
            BmoError::DirectoryUnsorted => "DIRECTORY_UNSORTED",
            BmoError::UnknownCritical => "UNKNOWN_CRITICAL",
            BmoError::UnknownExtension => "UNKNOWN_EXTENSION",
            BmoError::UpgradeRequired => "UPGRADE_REQUIRED",
            BmoError::IncompatibleMajor => "INCOMPATIBLE_MAJOR",
            BmoError::LimitExceeded => "LIMIT_EXCEEDED",
            BmoError::AuthFailed => "AUTH_FAILED",
            BmoError::NeedKey => "NEED_KEY",
            BmoError::DocIdMismatch => "DOC_ID_MISMATCH",
            BmoError::TxnMismatch => "TXN_MISMATCH",
            BmoError::RootMismatch => "ROOT_MISMATCH",
            BmoError::Corrupt => "CORRUPT",
            BmoError::BadStructure => "STRUCTURE_INVALID",
            BmoError::DanglingRef => "DANGLING_REF",
        };
        write!(f, "{s}")
    }
}

impl std::error::Error for BmoError {}

/// C ABI style result code (see lib.rs docs for the C API surface).
pub type BmoResult<T> = Result<T, BmoError>;
