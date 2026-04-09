# PRD: Chambers Automotive Simulation Testbed

**Product**: Chambers Vehicle Simulation Testbed
**Author**: Arko Ganguli
**Date**: 2026-04-08
**Status**: Draft
**Reference**: Chambers for Automotive — Position Paper (Revised, April 2026)

---

## 1. Problem Statement

The Chambers position paper proposes a sealed ephemeral computation model for connected vehicle data sovereignty. It maps 28 regulatory obligations across 6 EU legal instruments to 5 architectural components (Session Seal, Manifest, Burn Engine, Audit Log, Gateway). However:

- **No production implementation exists** (Limitation #1 in the paper)
- The concept mapping has **not been validated on production telematics hardware**
- The architecture describes **5 enforcement points** across Cellular, Bluetooth, Wi-Fi, OBD-II, and V2X channels, but none have been tested under realistic data loads
- The **16-threat model** across 5 channels has no empirical validation

A simulation testbed is needed to move Chambers from position paper to demonstrable prototype before the regulatory window closes (CRA vulnerability reporting: Sept 2026, AI Act Annex III: Aug 2026).

## 2. Objective

Build a multi-phase vehicle simulation testbed that validates the Chambers architecture under realistic conditions, producing:

1. **Proof of enforcement** — demonstrate that declared data passes the gateway while undeclared data is destroyed
2. **Threat model validation** — simulate the 16 channel-specific threats and show Chambers mitigations working
3. **Performance benchmarks** — measure latency overhead of session encryption, manifest evaluation, and burn operations on telemetry streams
4. **Compliance artefact generation** — produce sample audit logs, manifest records, and burn confirmations that map to the 28 regulatory obligations
5. **Stakeholder data flow demonstration** — show OEM, insurer, ADAS supplier, and Tier-1 supplier each receiving only their declared data categories

## 3. Non-Goals

- Production-grade HSM integration (simulated via software HSM)
- ASIL-rated safety certification
- Full AUTOSAR Classic integration
- Production deployment to real vehicle hardware (NXP S32G etc.)
- Fleet/commercial vehicle use cases (paper scopes to private passenger)

## 4. Architecture Overview

The testbed wraps three open-source vehicle simulators in a Chambers enforcement layer, validating each paper concept progressively:

```
+-------------------------------------------------------------+
|                    SIMULATOR LAYER                           |
|  Phase 1: SUMO        (traffic telemetry, fleet scale)      |
|  Phase 2: CARLA        (ego-vehicle sensors, drive sessions) |
|  Phase 3: ROS 2/Gazebo (ECU-level, CAN bus, sensor fusion)  |
+-------------------------------------------------------------+
                           |
                    TraCI / CARLA API / ROS 2 topics
                           |
+-------------------------------------------------------------+
|                 CHAMBERS GATEWAY (Rust/Python)               |
|                                                              |
|  +-------------+  +-----------+  +-------------+            |
|  | Session Seal|  |  Manifest |  | Burn Engine  |            |
|  | (ephemeral  |  |  (typed   |  | (key destroy |            |
|  |  key per    |  |  schema,  |  |  on session  |            |
|  |  session)   |  |  per-     |  |  end, 6-layer|            |
|  |             |  |  stakehdr)|  |  erasure)    |            |
|  +-------------+  +-----------+  +-------------+            |
|                                                              |
|  +-------------+  +--------------------+                     |
|  | Audit Log   |  | Enforcement Points |                     |
|  | (per-session|  | EP1: Cellular GW   |                     |
|  |  immutable  |  | EP2: Bluetooth/IVI |                     |
|  |  record)    |  | EP3: OBD-II Diag   |                     |
|  |             |  | EP4: V2X Stack     |                     |
|  +-------------+  | EP5: Wi-Fi GW      |                     |
|                    +--------------------+                     |
+-------------------------------------------------------------+
                           |
+-------------------------------------------------------------+
|               STAKEHOLDER ENDPOINTS (mock)                   |
|  OEM Cloud | Insurer API | ADAS Supplier | Tier-1 Supplier  |
+-------------------------------------------------------------+
```

## 5. Phased Delivery

### Phase 1: SUMO + Telemetry-Level Chambers (MVP)

**Simulator**: SUMO (Simulation of Urban Mobility)
**Goal**: Validate preservation manifest and burn semantics on vehicle telemetry streams at fleet scale.

**What gets built**:
- SUMO traffic scenario generating position, speed, acceleration, and routing data for 100+ vehicles
- Chambers gateway process consuming SUMO TraCI data per vehicle
- Preservation manifest schema (JSON/protobuf) implementing the paper's typed grammar
- Per-vehicle sealed drive sessions with ephemeral encryption keys (software HSM via SoftHSM2)
- Burn engine destroying session keys on simulated "park" event
- Stakeholder data routing: OEM gets anonymised aggregates, insurer gets severity scores without GPS, ADAS gets sealed events only
- Audit log recording per-session: what was generated, declared, transmitted, burned
- Data residue comparison: quantify data leaked without Chambers vs. with Chambers across fleet

**Validates paper concepts**:
- Section 9.1: Sealed drive sessions, typed preservation manifest, burn engine
- Section 9.5: Stakeholder data flows (OEM, insurer, ADAS, Tier-1)
- Section 10.1: Cellular channel threats (bulk exfiltration, OEM hoarding, third-party selling)
- Section 7: Regulatory compliance framework (manifest as Art. 30 record, burn as Art. 17 proof)

**Key metrics**:
- Data reduction ratio (bytes generated vs. bytes surviving session end)
- Manifest evaluation latency per telemetry message
- Burn completion time (key destruction + verification)
- Audit log completeness (100% of data flow decisions recorded)

### Phase 2: CARLA + Sensor-Level Chambers

**Simulator**: CARLA (0.9.x+, Unreal Engine)
**Goal**: Prove sealed drive sessions with realistic sensor data (camera, LiDAR, GPS, IMU). Demonstrate sealed ADAS events.

**What gets built**:
- CARLA ego-vehicle scenario with urban driving, sensor suite (RGB camera, LiDAR, GNSS, IMU)
- Session seal wrapping all sensor streams under one ephemeral key per drive
- Sealed ADAS event trigger: detect near-collision via CARLA collision sensor, capture bounded temporal window (5s before, 2s after), retain as declared exception
- EP1 (Cellular Gateway) enforcement: intercept all data egress to mock OEM cloud, apply manifest filter
- EP4 (V2X) enforcement: CARLA-SUMO co-simulation, simulate CAM broadcast, enforce pseudonym session boundaries
- Consent revocation mid-session: demonstrate manifest update and immediate data flow cutoff
- HSM fallback mode: simulate HSM unavailability, show conservative default (no telemetry transmitted)

**Validates paper concepts**:
- Section 6: Safety-critical exceptions (eCall, EDR outside Chambers boundary)
- Section 6.2: HSM failure and fallback
- Section 6.3: Consent revocation mid-session
- Section 10.5: V2X pseudonym rotation as session boundary
- Section 5.5: AI Act Art. 10 (sealed ADAS events as training data governance)

**Key metrics**:
- Sensor-to-gateway latency (camera frame encrypted in <X ms)
- Sealed event capture completeness (all collision events captured within window)
- V2X pseudonym session burn time
- Fallback mode activation/recovery time

### Phase 3: ROS 2 + Gazebo ECU-Level Integration

**Simulator**: ROS 2 Humble + Gazebo Fortress
**Goal**: Simulate intra-vehicle ECU communication, CAN bus messages, and enforcement at the middleware layer. Target Route 1 (Tier-1 middleware) and Route 2 (open-source SDV contribution).

**What gets built**:
- Gazebo vehicle model with simulated ECUs (powertrain, body, ADAS, infotainment, telematics)
- ROS 2 nodes representing CAN bus message flow between ECUs
- Chambers gateway as a ROS 2 node intercepting messages at the middleware (SOME/IP/DDS equivalent) layer
- EP2 (Bluetooth/IVI): simulate phone pairing session, contact sync, pairing key burn on disconnect
- EP3 (OBD-II): simulate diagnostic tool connection, authenticated access, encrypted responses
- EP5 (Wi-Fi): simulate hotspot passthrough policy, passenger traffic isolation
- AUTOSAR Crypto Stack interface layer (simulated) for session key management
- Full 5-enforcement-point integration test across all channels simultaneously

**Validates paper concepts**:
- Section 3.1: Full vehicle software architecture (Layers 0-5)
- Section 10.2: Bluetooth pairing as sealed world
- Section 10.3: Wi-Fi hotspot passthrough
- Section 10.4: OBD-II encrypted diagnostic responses
- Section 10.6: Architectural summary — all 5 EPs sharing one manifest, one HSM, one audit log

**Key metrics**:
- CAN bus message interception latency
- Multi-channel simultaneous enforcement overhead
- Bluetooth pairing session lifecycle (pair → sync → disconnect → burn)
- OBD-II diagnostic response encryption/decryption time

## 6. Technical Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Chambers Core | Rust (extending github.com/therealguikorinaga/chamber) | Paper's reference implementation; encrypted memory pool, 6-layer burn engine, 44 tests |
| Gateway Adapter | Python 3.11+ | SUMO TraCI, CARLA PythonAPI, and ROS 2 rclpy all have Python bindings |
| Manifest Schema | JSON Schema / Protobuf | Typed, grammar-constrained, machine-readable; maps to paper's "typed preservation manifest" |
| Software HSM | SoftHSM2 (PKCS#11) | OASIS standard; drop-in replacement for hardware HSM in simulation |
| Audit Log | Append-only SQLite + HMAC chain | Immutable per-session record; verifiable integrity |
| Mock Stakeholders | FastAPI endpoints | OEM cloud, insurer, ADAS supplier, Tier-1 — each receives only declared categories |
| SUMO | SUMO 1.20+ via TraCI | Lightweight traffic sim, fleet-scale telemetry |
| CARLA | CARLA 0.9.15+ (Unreal Engine 4) | Realistic sensors (LiDAR, camera, GPS), V2X via CARLA-SUMO bridge |
| ROS 2 | ROS 2 Humble + Gazebo Fortress | ECU simulation, CAN bus, middleware layer |
| CI/Testing | pytest + cargo test | Python integration tests + Rust unit tests from chamber repo |

## 7. Preservation Manifest Schema (Draft)

```json
{
  "manifest_version": "1.0",
  "vehicle_id": "<pseudonymised>",
  "session_id": "<uuid>",
  "session_start": "<ISO8601>",
  "stakeholders": [
    {
      "id": "oem_volkswagen",
      "role": "oem",
      "legal_basis": "legitimate_interest",
      "categories": [
        {
          "type": "sensor_health",
          "granularity": "anonymised_aggregate",
          "retention": "P90D",
          "purpose": "predictive_maintenance",
          "jurisdiction": ["EU"]
        }
      ]
    },
    {
      "id": "insurer_allianz",
      "role": "insurer",
      "legal_basis": "explicit_consent",
      "consent_ref": "<consent_record_id>",
      "categories": [
        {
          "type": "driving_behaviour",
          "fields": ["acceleration", "braking", "cornering_severity"],
          "excluded_fields": ["gps_position", "timestamps"],
          "granularity": "per_trip_score",
          "retention": "policy_term",
          "purpose": "risk_model_input"
        }
      ]
    },
    {
      "id": "adas_supplier_mobileye",
      "role": "adas_supplier",
      "legal_basis": "legitimate_interest",
      "categories": [
        {
          "type": "sealed_event",
          "trigger": "safety_critical",
          "window": {"before": "PT5S", "after": "PT2S"},
          "granularity": "anonymised",
          "retention": "P12M",
          "purpose": "model_retraining"
        }
      ]
    }
  ],
  "mandatory_retention": [
    {"type": "ecall", "regulation": "EU_2015_758", "treatment": "exempt_outside_boundary"},
    {"type": "edr", "regulation": "EU_General_Safety", "treatment": "exempt_write_once_partition"},
    {"type": "security_event_log", "regulation": "R155", "treatment": "declared_mandatory"}
  ]
}
```

## 8. Threat Simulation Matrix

Each of the paper's 16 threats will be simulated as a test scenario:

| # | Channel | Threat | Simulation Method | Phase |
|---|---------|--------|-------------------|-------|
| T1 | Cellular | Bulk telemetry exfiltration | SUMO: attempt raw data egress, verify gateway blocks undeclared fields | 1 |
| T2 | Cellular | OEM cloud data hoarding | Mock OEM endpoint attempts to request data beyond manifest declaration | 1 |
| T3 | Cellular | Third-party data selling | Route data to mock data broker endpoint, verify gateway rejects | 1 |
| T4 | Cellular | Foreign state backend access | Mock endpoint with non-EU jurisdiction, verify manifest jurisdiction constraint blocks transfer | 1 |
| T5 | Bluetooth | Contact/SMS sync persists | CARLA+ROS2: simulate phone disconnect, verify synced data is irrecoverable | 3 |
| T6 | Bluetooth | Call history retained on resale | Simulate vehicle ownership change, verify previous pairing sessions are burned | 3 |
| T7 | Bluetooth | Media metadata leakage | Verify A2DP metadata does not persist beyond pairing session | 3 |
| T8 | Wi-Fi | Passenger traffic inspection | Simulate hotspot connection, verify manifest enforces passthrough-only | 3 |
| T9 | Wi-Fi | Outbound data over Wi-Fi | Verify same manifest check applies to Wi-Fi egress as cellular | 3 |
| T10 | Wi-Fi | Rogue AP data injection | Simulate rogue AP, verify gateway policy enforcement | 3 |
| T11 | OBD-II | Casual diagnostic extraction | Simulate unauthenticated OBD reader, verify encrypted responses | 3 |
| T12 | OBD-II | Insurance dongle bypass | Simulate insurance black box plugged into OBD-II, verify manifest controls apply | 3 |
| T13 | OBD-II | Stolen vehicle data dump | Verify data encrypted at rest, key destroyed on session end | 3 |
| T14 | V2X | Position tracking via CAMs | CARLA-SUMO: collect CAM broadcasts, attempt trajectory reconstruction across pseudonym rotation | 2 |
| T15 | V2X | Trajectory re-identification | Verify cross-session linkage data is destroyed on pseudonym rotation | 2 |
| T16 | V2X | Inbound V2X data hoarding | Verify inbound V2X messages are ephemeral (used for awareness, not stored) | 2 |

## 9. Success Criteria

### Phase 1 (MVP)
- [ ] 100-vehicle SUMO scenario running with per-vehicle Chambers sessions
- [ ] Preservation manifest correctly routes data to 4 stakeholder endpoints
- [ ] Burn engine destroys session keys within 1 second of "park" event
- [ ] Audit log captures 100% of data flow decisions with HMAC integrity chain
- [ ] Data residue comparison shows >90% reduction in persisted data vs. no-Chambers baseline
- [ ] Threats T1-T4 simulated and mitigated

### Phase 2
- [ ] CARLA ego-vehicle drive session sealed with ephemeral key
- [ ] Sealed ADAS event captures bounded temporal window around collision
- [ ] V2X pseudonym rotation burns cross-session linkage data (T14, T15, T16)
- [ ] HSM fallback mode: zero telemetry transmitted when HSM unavailable
- [ ] Consent revocation mid-session halts data flow to revoked stakeholder within 1 message
- [ ] Sensor-to-gateway encryption latency < 10ms per frame

### Phase 3
- [ ] Full 5-enforcement-point simultaneous operation
- [ ] Bluetooth pairing session lifecycle: pair, sync, disconnect, burn — zero residual data
- [ ] OBD-II authenticated diagnostic access; unauthenticated requests receive encrypted (unusable) responses
- [ ] Wi-Fi hotspot passthrough with zero passenger traffic inspection
- [ ] All 16 threats simulated with passing mitigations
- [ ] Compliance artefact generation: manifest + audit log satisfy Art. 30, Art. 17, Art. 25 checks

## 10. Regulatory Alignment

The testbed directly supports the paper's regulatory convergence timeline:

| Deadline | Regulation | What the testbed proves |
|----------|-----------|------------------------|
| Now (April 2026) | R155/R156 (live since July 2024) | Gateway enforcement blocks Annex 5 threats (4.3.1, 4.3.3, 4.3.6, 5.4.1) |
| Sept 2026 | CRA vulnerability reporting | Sealed sessions reduce blast radius; audit log provides incident scope evidence for ENISA 24h report |
| Aug 2026 | AI Act Annex III (high-risk) | Sealed ADAS events demonstrate Art. 10 data governance + Art. 12 record-keeping |
| Aug 2027 | AI Act Annex I (vehicles) | Full manifest + audit log = transparency (Art. 13) + human oversight (Art. 14) |
| Dec 2027 | CRA full enforcement | Ephemeral-by-default = secure by design (Annex I); manifest = lifecycle docs |

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| CARLA GPU requirement limits CI | Phase 2 tests can't run headless on CPU-only CI | Use CARLA's `--no-rendering` mode for data-only tests; GPU tests run locally or on GPU CI runners |
| Rust chamber repo may diverge from paper's architecture | Implementation mismatch | Fork and extend; paper author controls both |
| SoftHSM2 doesn't faithfully model hardware HSM latency | Performance benchmarks may not reflect production | Add configurable latency injection; document as simulation limitation |
| SUMO-CARLA co-simulation bridge stability | Phase 2 V2X tests may be fragile | Pin CARLA/SUMO versions; build retry logic for co-sim handshake |
| ROS 2 + Gazebo setup complexity | Phase 3 onboarding friction | Provide Docker Compose for full environment; document setup for macOS/Linux |

## 12. Deliverables

| Phase | Deliverable | Format |
|-------|------------|--------|
| 1 | SUMO scenario + Chambers gateway | Python + Rust workspace |
| 1 | Preservation manifest schema | JSON Schema |
| 1 | Data residue comparison report | Generated Markdown + charts |
| 1 | Audit log viewer | CLI tool (Rust) |
| 2 | CARLA drive session scenario | Python |
| 2 | Sealed ADAS event capture module | Rust |
| 2 | V2X pseudonym session manager | Rust |
| 2 | CARLA-SUMO co-sim V2X scenario | Python |
| 3 | ROS 2 vehicle model + ECU nodes | ROS 2 packages (Python/C++) |
| 3 | 5-EP integration test suite | pytest |
| 3 | Regulatory compliance artefact generator | Rust CLI |
| All | Docker Compose for each phase | docker-compose.yml |
| All | CI pipeline | GitHub Actions |

## 13. Open Questions

1. **Manifest taxonomy**: The paper notes that OEM cooperation is needed to define data categories. For simulation, do we define a canonical taxonomy or keep it pluggable?
2. **chamber repo integration depth**: Do we wrap the existing Rust substrate as a library, or fork and extend it with automotive-specific modules?
3. **Performance targets**: What are acceptable latency ceilings for gateway enforcement in a real vehicle? (Paper doesn't specify; need automotive benchmarking data.)
4. **CARLA version**: 0.9.15 vs. 0.10.0 (if available) — sensor fidelity vs. API stability tradeoff.
5. **Scope of Route 2 contribution**: If targeting AGL/AAOS contribution, should Phase 3 directly produce an AGL-compatible package, or remain simulator-only?
