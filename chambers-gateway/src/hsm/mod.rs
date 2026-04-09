use crate::core::{ChambersError, Result};
use chrono::{DateTime, Utc};
use ring::aead::{self, Aad, BoundKey, Nonce, NonceSequence, OpeningKey, SealingKey, UnboundKey};
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// Opaque handle referencing a key stored inside the HSM.
pub type KeyHandle = u64;

/// Encrypted output: nonce || ciphertext (which includes the 16-byte GCM tag).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CipherText {
    pub nonce: Vec<u8>,
    pub data: Vec<u8>,
}

/// Receipt returned when a key is destroyed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DestructionReceipt {
    pub key_handle: KeyHandle,
    pub destroyed_at: DateTime<Utc>,
    pub zeroed: bool,
}

/// A single-use nonce sequence that yields exactly one nonce then errors.
struct SingleNonce(Option<[u8; 12]>);

impl NonceSequence for SingleNonce {
    fn advance(&mut self) -> std::result::Result<Nonce, ring::error::Unspecified> {
        self.0
            .take()
            .map(|n| Nonce::assume_unique_for_key(n))
            .ok_or(ring::error::Unspecified)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum KeyState {
    Active(Vec<u8>),
    Destroyed,
}

struct HsmInner {
    keys: HashMap<KeyHandle, KeyState>,
    next_handle: KeyHandle,
    rng: SystemRandom,
}

/// Software HSM simulating a PKCS#11 hardware security module.
///
/// Keys never leave the HSM boundary — all cryptographic operations happen
/// inside this struct. Thread-safe via `Arc<Mutex<...>>`.
#[derive(Clone)]
pub struct SoftwareHsm {
    inner: Arc<Mutex<HsmInner>>,
}

impl SoftwareHsm {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(HsmInner {
                keys: HashMap::new(),
                next_handle: 1,
                rng: SystemRandom::new(),
            })),
        }
    }

    /// Generate a new AES-256 key. Returns the handle; the raw key material
    /// never leaves the HSM.
    pub fn generate_key(&self) -> Result<(KeyHandle, ())> {
        let mut inner = self.inner.lock().map_err(|e| ChambersError::Hsm(e.to_string()))?;
        let mut key_bytes = vec![0u8; 32]; // AES-256
        inner
            .rng
            .fill(&mut key_bytes)
            .map_err(|_| ChambersError::Hsm("RNG failure".into()))?;
        let handle = inner.next_handle;
        inner.next_handle += 1;
        inner.keys.insert(handle, KeyState::Active(key_bytes));
        Ok((handle, ()))
    }

    /// Encrypt plaintext using the key referenced by `handle`.
    /// Returns nonce + ciphertext (with GCM tag appended).
    pub fn encrypt(&self, handle: KeyHandle, plaintext: &[u8]) -> Result<CipherText> {
        let inner = self.inner.lock().map_err(|e| ChambersError::Hsm(e.to_string()))?;
        let key_bytes = match inner.keys.get(&handle) {
            Some(KeyState::Active(bytes)) => bytes.clone(),
            Some(KeyState::Destroyed) => return Err(ChambersError::KeyDestroyed(handle)),
            None => return Err(ChambersError::KeyNotFound(handle)),
        };

        // Generate random 12-byte nonce
        let mut nonce_bytes = [0u8; 12];
        inner
            .rng
            .fill(&mut nonce_bytes)
            .map_err(|_| ChambersError::Hsm("RNG failure generating nonce".into()))?;

        // We must drop the lock before performing crypto (ring doesn't need it)
        drop(inner);

        let unbound = UnboundKey::new(&aead::AES_256_GCM, &key_bytes)
            .map_err(|_| ChambersError::Encryption("Failed to create AES key".into()))?;

        let nonce_seq = SingleNonce(Some(nonce_bytes));
        let mut sealing_key = SealingKey::new(unbound, nonce_seq);

        let mut in_out = plaintext.to_vec();
        sealing_key
            .seal_in_place_append_tag(Aad::empty(), &mut in_out)
            .map_err(|_| ChambersError::Encryption("seal_in_place failed".into()))?;

        Ok(CipherText {
            nonce: nonce_bytes.to_vec(),
            data: in_out,
        })
    }

    /// Decrypt ciphertext using the key referenced by `handle`.
    pub fn decrypt(&self, handle: KeyHandle, ct: &CipherText) -> Result<Vec<u8>> {
        let inner = self.inner.lock().map_err(|e| ChambersError::Hsm(e.to_string()))?;
        let key_bytes = match inner.keys.get(&handle) {
            Some(KeyState::Active(bytes)) => bytes.clone(),
            Some(KeyState::Destroyed) => return Err(ChambersError::KeyDestroyed(handle)),
            None => return Err(ChambersError::KeyNotFound(handle)),
        };
        drop(inner);

        let mut nonce_bytes = [0u8; 12];
        if ct.nonce.len() != 12 {
            return Err(ChambersError::Decryption("Invalid nonce length".into()));
        }
        nonce_bytes.copy_from_slice(&ct.nonce);

        let unbound = UnboundKey::new(&aead::AES_256_GCM, &key_bytes)
            .map_err(|_| ChambersError::Decryption("Failed to create AES key".into()))?;

        let nonce_seq = SingleNonce(Some(nonce_bytes));
        let mut opening_key = OpeningKey::new(unbound, nonce_seq);

        let mut in_out = ct.data.clone();
        let plaintext = opening_key
            .open_in_place(Aad::empty(), &mut in_out)
            .map_err(|_| ChambersError::Decryption("open_in_place failed".into()))?;

        Ok(plaintext.to_vec())
    }

    /// Destroy the key: zero out all key material and remove from the store.
    /// After this call, encrypt/decrypt with this handle will return Err.
    pub fn destroy_key(&self, handle: KeyHandle) -> Result<DestructionReceipt> {
        let mut inner = self.inner.lock().map_err(|e| ChambersError::Hsm(e.to_string()))?;
        match inner.keys.get_mut(&handle) {
            Some(state @ KeyState::Active(_)) => {
                // Zero out key material
                if let KeyState::Active(ref mut bytes) = state {
                    for b in bytes.iter_mut() {
                        *b = 0;
                    }
                }
                // Mark as destroyed
                *state = KeyState::Destroyed;
            }
            Some(KeyState::Destroyed) => {
                return Err(ChambersError::KeyDestroyed(handle));
            }
            None => {
                return Err(ChambersError::KeyNotFound(handle));
            }
        }

        Ok(DestructionReceipt {
            key_handle: handle,
            destroyed_at: Utc::now(),
            zeroed: true,
        })
    }

    /// Check whether a key handle is currently active.
    pub fn is_key_active(&self, handle: KeyHandle) -> Result<bool> {
        let inner = self.inner.lock().map_err(|e| ChambersError::Hsm(e.to_string()))?;
        match inner.keys.get(&handle) {
            Some(KeyState::Active(_)) => Ok(true),
            Some(KeyState::Destroyed) => Ok(false),
            None => Ok(false),
        }
    }
}

impl Default for SoftwareHsm {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encrypt_decrypt_roundtrip() {
        let hsm = SoftwareHsm::new();
        let (handle, _) = hsm.generate_key().unwrap();
        let plaintext = b"hello automotive world";
        let ct = hsm.encrypt(handle, plaintext).unwrap();
        let recovered = hsm.decrypt(handle, &ct).unwrap();
        assert_eq!(recovered, plaintext);
    }

    #[test]
    fn destroy_prevents_decrypt() {
        let hsm = SoftwareHsm::new();
        let (handle, _) = hsm.generate_key().unwrap();
        let ct = hsm.encrypt(handle, b"secret data").unwrap();
        hsm.destroy_key(handle).unwrap();
        assert!(hsm.decrypt(handle, &ct).is_err());
    }

    #[test]
    fn destroy_prevents_encrypt() {
        let hsm = SoftwareHsm::new();
        let (handle, _) = hsm.generate_key().unwrap();
        hsm.destroy_key(handle).unwrap();
        assert!(hsm.encrypt(handle, b"data").is_err());
    }

    #[test]
    fn double_destroy_errors() {
        let hsm = SoftwareHsm::new();
        let (handle, _) = hsm.generate_key().unwrap();
        hsm.destroy_key(handle).unwrap();
        assert!(hsm.destroy_key(handle).is_err());
    }
}
