# Architecture Overview

This document describes the architecture of the Chambers Automotive Simulation
Testbed -- a system that validates the Chambers sealed ephemeral computation
model for connected vehicle data sovereignty.

## System Context

The testbed wraps three open-source vehicle simulators in a Chambers
enforcement layer and routes filtered data to mock stakeholder endpoints.

```
+----------------------------------------------------------------+
|                      SIMULATOR LAYER                           |
|                                                                |
|  Phase 1: SUMO ........... traffic telemetry, fleet scale      |
|  Phase 2: CARLA .......... ego-vehicle sensors, drive sessions |
|  Phase 3: ROS 2 / Gazebo . ECU-level, CAN bus, sensor fusion  |
|                                                                |
+----------------------------------------------------------------+
        |               |                |
     TraCI         CARLA API        ROS 2 topics
        |               |                |
        +-------+-------+-------+--------+
                |               |
         chambers-sim      chambers-sim
        (Python adapter)  (Python adapter)
                |               |
                +-------+-------+
                        |
                        v
+----------------------------------------------------------------+
|                CHAMBERS GATEWAY  (Rust)                         |
|                                                                |
|   Session Seal  |  Manifest Evaluator  |  Burn Engine          |
|   Audit Log     |  Software HSM        |  Enforcement Router   |
|                                                                |
+----------------------------------------------------------------+
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
+----------------------------------------------------------------+
|              STAKEHOLDER ENDPOINTS  (mock FastAPI)              |
|                                                                |
|  /oem/telemetry   /insurer/trip   /adas/event   /tier1/diag    |
|  /broker/data (rogue -- should never receive data)             |
|  /foreign/telemetry (non-EU -- jurisdiction blocked)           |
+----------------------------------------------------------------+
```

## Component Diagram

The Chambers Gateway is the central orchestrator. Every data record passes
through the same pipeline regardless of which enforcement point sourced it.

```
+====================================================================+
|                        CHAMBERS GATEWAY                            |
|                                                                    |
|  +------------------+      +---------------------+                 |
|  |   Session Seal   |----->|  Software HSM       |                 |
|  |                  |      |  (SoftwareHsm)      |                 |
|  |  - start_session |      |                     |                 |
|  |  - encrypt       |      |  - generate_key     |                 |
|  |  - end_session   |      |  - encrypt / decrypt|                 |
|  +--------+---------+      |  - destroy_key      |                 |
|           |                +----------+----------+                 |
|           v                           |                            |
|  +------------------+                 |                            |
|  | Manifest         |<----------------+                            |
|  | Evaluator        |                                              |
|  |                  |      +---------------------+                 |
|  |  - evaluate      |----->|  Audit Log          |                 |
|  |  - filter_fields |      |  (AuditLog)         |                 |
|  |  - apply_granulr |      |                     |                 |
|  |  - check_jurisd  |      |  - log_event        |                 |
|  |  - revoke_consent|      |  - verify_chain     |                 |
|  +--------+---------+      |  - export_session   |                 |
|           |                |  - get_summary      |                 |
|           v                +---------------------+                 |
|  +------------------+                 ^                            |
|  |  Gateway Router  |-----------------+                            |
|  |                  |                                              |
|  |  - process_record|      +---------------------+                 |
|  |  - route / block |----->|  Burn Engine         |                |
|  |  - end_session   |      |  (BurnEngine)        |                |
|  +------------------+      |                      |                |
|                            |  Layer 1: Logical    |                |
|                            |  Layer 2: Crypto     |                |
|                            |  Layer 3: Storage    |                |
|                            |  Layer 4: Memory     |                |
|                            |  Layer 5: Semantic   |                |
|                            |  Layer 6: Verify     |                |
|                            +----------------------+                |
+====================================================================+
```

### Component Responsibilities

| Component | Crate Module | Responsibility |
|-----------|-------------|----------------|
| Session Seal | `gateway::start_session` | Generate ephemeral AES-256-GCM key per drive session via Software HSM |
| Manifest Evaluator | `manifest::ManifestEvaluator` | Per-record filtering: match data type to stakeholder categories, filter fields, apply granularity, check jurisdiction |
| Burn Engine | `burn::BurnEngine` | 6-layer cryptographic erasure on session end producing a `BurnReceipt` |
| Audit Log | `audit::AuditLog` | Append-only SQLite with HMAC-SHA256 chain -- immutable per-session record |
| Software HSM | `hsm::SoftwareHsm` | Simulates PKCS#11 HSM: key generation, AES-256-GCM encrypt/decrypt, key destruction |
| Gateway | `gateway::Gateway` | Orchestrates all components at the enforcement point |

## Data Flow

The following diagram shows the path every data record takes from sensor
generation to final disposition (transmit or burn).

```
  Sensor / ECU / Simulator
         |
         v
  +------------------+
  | Data Record      |    source, data_type, fields, timestamp
  +------------------+
         |
         v
  +------------------+
  | Session Seal     |    encrypt(record) under session AES-256-GCM key
  | (HSM encrypt)    |    store ciphertext in SessionDataStore
  +------------------+
         |
         v
  +------------------+
  | Audit: Generated |    log DataGenerated event with type, source, size
  +------------------+
         |
         v
  +------------------+
  | Manifest         |    for each stakeholder in manifest:
  | Evaluation       |      1. does stakeholder declare this data_type?
  |                  |      2. is stakeholder consent revoked?
  |                  |      3. filter fields (allow-list / deny-list)
  |                  |      4. apply granularity (raw/anon/agg/score)
  |                  |      5. check jurisdiction (EU/EEA/UK)
  +------------------+
         |
    +----+----+
    |         |
    v         v
 TRANSMIT   BLOCK
    |         |
    v         v
 Audit:    Audit:
 Transmitted  Blocked
    |         |
    v         v
 Stakeholder  (record stays encrypted,
 endpoint     awaiting session burn)
```

On session end (park / ignition off):

```
  Park event
       |
       v
  +-------------------+
  | Burn Engine       |
  | 6-layer protocol  |
  +-------------------+
       |
       v
  +-------------------+
  | BurnReceipt       |    per-layer success, timing, HSM attestation
  +-------------------+
       |
       v
  +-------------------+
  | Audit: BurnCompleted |  receipt appended to immutable log
  | Audit: SessionEnd    |
  +-------------------+
```

## The 5 Enforcement Points

The Chambers architecture defines five enforcement points (EPs), one per
external communication channel on a connected vehicle. All five share a single
manifest, a single HSM, and a single audit log.

```
                    +----------------------------+
                    |     CHAMBERS GATEWAY        |
                    |                             |
  Cellular  ------->  EP1: Cellular Gateway      |
  (TCU/4G/5G)      |    highest volume channel   |
                    |    telemetry, OTA, cloud    |
                    |                             |
  Bluetooth ------->  EP2: Bluetooth / IVI       |
  (phone pair)      |    pairing session scope    |
                    |    contacts, calls, media   |
                    |                             |
  OBD-II   -------->  EP3: OBD-II Diagnostic     |
  (port)            |    authenticated access     |
                    |    encrypted responses      |
                    |                             |
  V2X      -------->  EP4: V2X Stack             |
  (DSRC/C-V2X)     |    pseudonym rotation       |
                    |    CAM/DENM broadcast       |
                    |                             |
  Wi-Fi    -------->  EP5: Wi-Fi Hotspot         |
  (in-car AP)       |    passthrough policy       |
                    |    vehicle outbound = EP1   |
                    +----------------------------+
```

| EP | Channel | Primary Data Types | Session Scope | Phase |
|----|---------|-------------------|---------------|-------|
| EP1 | Cellular | Telemetry, sensor health, ADAS events, diagnostics | Drive session (ignition to park) | 1 |
| EP2 | Bluetooth | Contacts, call history, SMS, media metadata | Pairing session (connect to disconnect) | 3 |
| EP3 | OBD-II | DTCs, PIDs, freeze frames | Diagnostic session (tool connect to disconnect) | 3 |
| EP4 | V2X | CAM broadcasts, DENM, pseudonym linkage | Pseudonym period (5-min rotation) | 2 |
| EP5 | Wi-Fi | Passenger traffic (passthrough), vehicle outbound | Hotspot session | 3 |

## Concept Mapping

This table maps Chambers architectural concepts to their vehicle-domain
equivalents and the regulations they address (from paper Section 9.3).

| Chambers Concept | Vehicle Concept | Regulation | Implementation |
|-----------------|----------------|------------|----------------|
| Sealed World | Drive session (ignition to park) | GDPR Art. 5(1)(e) storage limitation | `SessionSeal` + ephemeral AES-256-GCM key |
| Sealed World | Bluetooth pairing session | GDPR Art. 5(1)(c) data minimisation | EP2 pairing session key |
| Sealed World | V2X pseudonym period | C-ITS pseudonym framework | EP4 pseudonym rotation + burn |
| Preservation Manifest | Typed schema per stakeholder | GDPR Art. 30 processing record | `PreservationManifest` JSON Schema |
| Manifest Category | Data type + granularity + jurisdiction | GDPR Art. 6 legal basis | `CategoryDeclaration` |
| Burn Engine | Key destruction on session end | GDPR Art. 17 right to erasure | 6-layer `BurnEngine` |
| Burn Receipt | Proof of cryptographic erasure | GDPR Art. 5(2) accountability | `BurnReceipt` in audit log |
| Audit Log | Per-session immutable record | GDPR Art. 30, R155 Annex 5 | HMAC-chained SQLite |
| Session Seal Key | Ephemeral AES-256-GCM via HSM | ePrivacy, GDPR Art. 32 | `SoftwareHsm::generate_key` |
| Gateway | Telematics control unit enforcement | R155 CSMS, CRA Annex I | `Gateway` orchestrator |
| Consent Revocation | Mid-session manifest update | GDPR Art. 7(3) | `ManifestEvaluator::revoke_consent` |
| Fallback Mode | HSM unavailable, block all | GDPR Art. 25 data protection by default | `Gateway::enter_fallback_mode` |
| Mandatory Retention | eCall, EDR, R155 security log | EU 2015/758, General Safety Reg. | `MandatoryRetention` in manifest |
| Sealed Event | Bounded ADAS capture window | AI Act Art. 10 data governance | 5s-before / 2s-after trigger |

## Session Lifecycle

A complete session lifecycle from ignition to final audit:

```
  IGNITION ON
       |
       v
  [1] Session Start
       |  - HSM generates ephemeral AES-256-GCM key
       |  - Gateway creates SessionState
       |  - ManifestEvaluator loaded with preservation manifest
       |  - BurnEngine registers session-to-key mapping
       |  - Audit: SessionStart, ManifestLoaded
       |
       v
  [2] Drive (active session)
       |  - Sensor data flows continuously
       |  - Each record: encrypt -> evaluate -> route/block -> audit
       |  - Consent may be revoked mid-session (Audit: ConsentRevoked)
       |  - HSM may fail -> fallback mode (Audit: HsmFallback)
       |  - Sealed ADAS events captured on safety triggers
       |
       v
  [3] Park (session end trigger)
       |
       v
  [4] Burn (6-layer protocol)
       |  Layer 1: Logical deletion -- mark all entries as deleted
       |  Layer 2: Cryptographic -- destroy session key via HSM
       |  Layer 3: Storage -- overwrite ciphertext with random bytes
       |  Layer 4: Memory -- zero all in-memory buffers
       |  Layer 5: Semantic -- destroy session-to-key and
       |           session-to-data mappings
       |  Layer 6: Verification -- confirm all layers succeeded,
       |           key destroyed, data removed, mappings gone
       |
       v
  [5] BurnReceipt
       |  - Per-layer success/failure + duration
       |  - HSM DestructionReceipt (key handle, timestamp, zeroed)
       |  - Overall success boolean
       |
       v
  [6] Audit Finalization
       |  - Audit: BurnStarted, BurnCompleted, SessionEnd
       |  - HMAC chain links all entries for this session
       |  - Audit log survives session end (stored outside
       |    session-encrypted boundary)
       |
       v
  SESSION COMPLETE
       |
       |  Post-session queries available:
       |  - chambers audit verify <session_id>
       |  - chambers audit show <session_id>
       |  - chambers audit export <session_id>
       |  - chambers audit driver-summary <session_id>
```
