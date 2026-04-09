# Issue List: Chambers Automotive Simulation Testbed

**Reference**: [PRD.md](./PRD.md)
**Notation**: `[PX]` = Phase, `[EPn]` = Enforcement Point, `[Tn]` = Threat from paper Section 10.1

---

## Epic 0: Project Bootstrap

### ISSUE-001: Repository initialisation and project structure
**Priority**: P0
**Phase**: 0
**Description**: Initialise the monorepo with the following structure:
```
intelligent-cars/
  chambers-gateway/        # Rust workspace (core, gateway, audit, burn)
  chambers-sim/            # Python package (SUMO/CARLA/ROS2 adapters)
  manifests/               # JSON Schema definitions + example manifests
  scenarios/               # SUMO .sumocfg, CARLA scripts, Gazebo worlds
  mock-stakeholders/       # FastAPI mock endpoints (OEM, insurer, ADAS, Tier-1)
  tests/                   # Integration tests
  docker/                  # Dockerfiles and docker-compose per phase
  docs/                    # Architecture diagrams, setup guides
```
Includes: `.gitignore`, `Cargo.toml` workspace, `pyproject.toml`, `README.md`, CI skeleton.

### ISSUE-002: Docker Compose for Phase 1 development environment
**Priority**: P0
**Phase**: 1
**Description**: Create `docker/docker-compose.phase1.yml` with services:
- SUMO (with TraCI server on port 8813)
- Chambers gateway (Rust binary)
- SoftHSM2 (PKCS#11 provider)
- Mock stakeholder endpoints (FastAPI)
- SQLite audit log volume

Developer should be able to `docker compose up` and have the full Phase 1 environment running.

### ISSUE-003: CI pipeline — GitHub Actions
**Priority**: P1
**Phase**: 0
**Description**: Set up GitHub Actions workflow:
- `cargo test` — Rust workspace (chambers-gateway)
- `pytest` — Python integration tests (chambers-sim)
- `cargo clippy` + `ruff` — linting
- Phase 1 integration test: SUMO scenario → gateway → stakeholder assertion
- Matrix: Ubuntu 22.04 (primary), macOS 14 (secondary)

---

## Epic 1: Chambers Core (Rust)

### ISSUE-010: Fork and integrate chamber reference implementation
**Priority**: P0
**Phase**: 1
**Description**: Fork `github.com/therealguikorinaga/chamber` into `chambers-gateway/core/`. Evaluate existing capabilities:
- Encrypted memory pool
- 6-layer burn engine
- Application isolation
- 44 existing tests

Determine what needs to be extended for automotive use (session lifecycle, manifest evaluation, audit log integration). Document integration plan.

### ISSUE-011: Session Seal — ephemeral key lifecycle
**Priority**: P0
**Phase**: 1
**Description**: Implement sealed drive session lifecycle:
1. **Session start** (ignition/drive event): generate ephemeral AES-256-GCM key via SoftHSM2 PKCS#11
2. **Active session**: all data flowing through gateway encrypted under session key
3. **Session end** (park event): trigger burn engine → destroy key via HSM `C_DestroyObject`
4. Key must not be extractable from HSM at any point

Interface: `SessionSeal::start() -> SessionId`, `SessionSeal::encrypt(data) -> CipherText`, `SessionSeal::burn(session_id) -> BurnReceipt`

**Acceptance criteria**:
- Key generation < 5ms
- Encrypted data irrecoverable after burn (test: attempt decryption post-burn, assert failure)
- BurnReceipt contains timestamp, session_id, key_handle, destruction_method

### ISSUE-012: Preservation Manifest — schema and evaluator
**Priority**: P0
**Phase**: 1
**Description**: Implement the typed preservation manifest from paper Section 9.1:
- JSON Schema definition (see PRD Section 7 for draft)
- Manifest parser and validator
- Manifest evaluator: given a data record + manifest, determine which stakeholders receive which fields at which granularity
- Anonymisation functions: strip identity, aggregate, compute severity scores
- Jurisdiction check: block transmission to endpoints outside declared jurisdictions

**Acceptance criteria**:
- Manifest schema validates against paper's stakeholder examples (OEM, insurer, ADAS, Tier-1)
- Evaluator correctly routes: insurer gets `[accel, braking, cornering]` but NOT `[gps, timestamps]`
- Invalid manifest (missing required fields) rejected at parse time
- Manifest is immutable once session starts (no mid-session additions, only consent revocation removes)

### ISSUE-013: Burn Engine — 6-layer cryptographic erasure
**Priority**: P0
**Phase**: 1
**Description**: Implement burn engine with the 6 layers from the chamber reference:
1. **Logical**: mark data as deleted
2. **Cryptographic**: destroy encryption key via HSM
3. **Storage**: overwrite encrypted data regions
4. **Memory**: zero memory pool (from chamber reference)
5. **Semantic**: destroy metadata linkages (session→key, session→data mappings)
6. **Verification**: produce BurnReceipt with proof of each layer's completion

**Acceptance criteria**:
- All 6 layers execute in sequence on session end
- BurnReceipt is append-only to audit log
- Total burn time < 1 second for a typical drive session (10,000 telemetry records)
- Post-burn forensic check: no recoverable plaintext in memory or storage

### ISSUE-014: Audit Log — immutable per-session record
**Priority**: P0
**Phase**: 1
**Description**: Implement append-only audit log per paper Section 7:
- SQLite backend with HMAC chain (each entry's HMAC covers previous entry's hash)
- Records per session: data generated (categories + counts), data declared (manifest snapshot), data transmitted (per stakeholder, per category), data burned (BurnReceipt)
- Tamper detection: verify HMAC chain integrity
- Export: JSON for regulator review, human-readable summary for driver

**Acceptance criteria**:
- Chain integrity verified on read (any tampering detected)
- Audit log entry created for every data flow decision (transmit, block, burn)
- Export produces valid Art. 30 processing record structure
- Log survives session end (stored outside session-encrypted boundary)

### ISSUE-015: Gateway — enforcement point router
**Priority**: P0
**Phase**: 1
**Description**: Implement the Chambers gateway process that ties all components together:
- Receives data from simulator adapter (TraCI/CARLA API/ROS2)
- Encrypts under active session key (Session Seal)
- Evaluates against preservation manifest (Manifest Evaluator)
- Routes declared data to appropriate stakeholder endpoints
- Blocks undeclared data
- On session end: triggers burn engine, writes audit log
- Exposes metrics: messages processed, blocked, transmitted, burn time

**Acceptance criteria**:
- Gateway processes 1,000 telemetry messages/second without dropping
- Every message is either transmitted (with audit entry) or blocked (with audit entry)
- No data path bypasses manifest evaluation

---

## Epic 2: SUMO Integration (Phase 1)

### ISSUE-020: SUMO urban traffic scenario
**Priority**: P0
**Phase**: 1
**Description**: Create a SUMO scenario representing urban driving:
- City grid network (20x20 blocks) or import from OpenStreetMap
- 100 vehicles with varied routes (commute patterns)
- Drive sessions: each vehicle does ignition → drive 10-30 min → park
- Multiple drive cycles per simulation run
- TraCI subscription for per-vehicle: position, speed, acceleration, heading, route, fuel consumption, CO2

Output: `scenarios/sumo/urban_100v.sumocfg` + network files + route files

### ISSUE-021: SUMO-to-Chambers adapter (TraCI bridge)
**Priority**: P0
**Phase**: 1
**Description**: Python adapter that bridges SUMO TraCI to the Chambers gateway:
- Subscribe to per-vehicle telemetry via TraCI
- Map SUMO vehicle lifecycle to Chambers sessions (vehicle depart = session start, vehicle arrive = session end)
- Convert TraCI data to Chambers data records with typed fields matching manifest schema
- Feed records to Chambers gateway via IPC (Unix socket or gRPC)
- Handle multiple concurrent vehicle sessions

**Acceptance criteria**:
- Adapter correctly maps SUMO vehicle lifecycle to Chambers session lifecycle
- All TraCI subscribed fields appear as typed fields in data records
- Adapter handles 100 concurrent vehicle sessions without blocking

### ISSUE-022: Mock stakeholder endpoints
**Priority**: P1
**Phase**: 1
**Description**: FastAPI application with 4 endpoints representing paper's stakeholders:
- `POST /oem/telemetry` — accepts anonymised sensor health aggregates
- `POST /insurer/trip` — accepts driving behaviour scores (no GPS)
- `POST /adas/event` — accepts sealed safety events
- `POST /tier1/diagnostics` — accepts component-specific telemetry

Each endpoint:
- Validates received data matches its manifest declaration (rejects undeclared fields)
- Logs all received data for test assertion
- Returns acknowledgement with timestamp

### ISSUE-023: Data residue comparison report
**Priority**: P1
**Phase**: 1
**Description**: Build a comparison tool that runs the SUMO scenario twice:
1. **Baseline** (no Chambers): all telemetry forwarded to all stakeholders, all data persisted indefinitely
2. **Chambers**: telemetry flows through gateway with manifest enforcement and burn

Generate report:
- Total bytes generated vs. bytes persisted (per stakeholder)
- Data categories leaked in baseline vs. blocked by Chambers
- Visualisation: waterfall chart showing data reduction per category
- Map to paper Section 3.2 "Data Residue Problem"

Output: Markdown report with embedded charts (matplotlib)

---

## Epic 3: Threat Simulation (Phase 1)

### ISSUE-030: T1 — Bulk telemetry exfiltration
**Priority**: P1
**Phase**: 1
**Description**: Simulate a scenario where raw vehicle telemetry is bulk-exported to an external endpoint.
- Configure a "rogue" stakeholder endpoint not in the manifest
- Attempt to route telemetry to it via the gateway
- **Assert**: gateway blocks all data to undeclared endpoints
- **Assert**: audit log records the blocked attempt

### ISSUE-031: T2 — OEM cloud data hoarding
**Priority**: P1
**Phase**: 1
**Description**: Simulate OEM endpoint requesting data categories beyond its manifest declaration.
- OEM manifest declares: `sensor_health` at `anonymised_aggregate` granularity
- OEM endpoint requests: raw GPS traces, individual driving behaviour, camera frames
- **Assert**: gateway transmits only declared categories at declared granularity
- **Assert**: undeclared requests logged as policy violations in audit log

### ISSUE-032: T3 — Third-party data selling
**Priority**: P1
**Phase**: 1
**Description**: Simulate the GM/LexisNexis scenario (paper Section 5.7):
- Vehicle generates driving behaviour data
- A "data broker" endpoint (not in manifest) attempts to receive it
- OEM endpoint attempts to forward received data to broker (out of scope — but test that broker never receives data directly from vehicle)
- **Assert**: no data reaches undeclared third-party endpoint
- **Assert**: manifest audit trail shows exactly what data left the vehicle and to whom

### ISSUE-033: T4 — Foreign state backend access
**Priority**: P1
**Phase**: 1
**Description**: Simulate data egress to a non-EU jurisdiction endpoint.
- Manifest declares jurisdiction constraint: `["EU"]`
- Configure mock endpoint with declared jurisdiction `"US"` or `"CN"`
- **Assert**: gateway blocks transmission based on jurisdiction mismatch
- **Assert**: audit log records jurisdiction violation

---

## Epic 4: CARLA Integration (Phase 2)

### ISSUE-040: CARLA ego-vehicle drive session scenario
**Priority**: P0
**Phase**: 2
**Description**: Create CARLA scenario:
- Urban environment (Town03 or Town05)
- Ego vehicle with sensor suite: RGB camera (front), LiDAR (roof), GNSS, IMU, collision detector
- NPC traffic (50 vehicles, 20 pedestrians)
- Drive route: 5-10 minutes of urban driving
- Session lifecycle: spawn = session start, destroy = session end

Output: `scenarios/carla/urban_drive.py`

### ISSUE-041: CARLA-to-Chambers adapter
**Priority**: P0
**Phase**: 2
**Description**: Python adapter bridging CARLA sensor callbacks to Chambers gateway:
- Subscribe to all ego-vehicle sensor streams
- Convert sensor data to Chambers data records:
  - Camera: frame metadata (resolution, timestamp, exposure) — not raw pixels for telemetry (raw pixels for sealed events only)
  - LiDAR: point cloud statistics (density, range histogram) — not raw point cloud
  - GNSS: position (lat/lon/alt)
  - IMU: acceleration, gyroscope
  - Collision: trigger sealed ADAS event
- Feed to gateway at sensor callback rate

### ISSUE-042: Sealed ADAS event capture
**Priority**: P0
**Phase**: 2
**Description**: Implement sealed event mechanism from paper Section 9.1:
- Trigger: CARLA collision sensor detects near-collision or actual collision
- Capture window: 5 seconds before trigger, 2 seconds after (configurable)
- Captured data: full sensor suite (camera frames, LiDAR point clouds, IMU, GNSS) within window
- Data anonymised per manifest (e.g., pedestrian faces blurred or excluded)
- Retained as declared exception to ephemeral default
- Tagged with: trigger type, timestamp, retention period (12 months per paper example), purpose (model retraining)

**Acceptance criteria**:
- Event capture starts within 1 frame of trigger
- Pre-trigger buffer correctly stores 5 seconds of rolling sensor data
- Event data encrypted under separate key with longer TTL than drive session key
- Event appears in audit log as declared retention exception

### ISSUE-043: CARLA-SUMO co-simulation for V2X
**Priority**: P1
**Phase**: 2
**Description**: Use CARLA's SUMO co-simulation bridge to simulate V2X:
- SUMO traffic provides surrounding vehicles
- Ego vehicle (CARLA) broadcasts simulated CAM messages (position, speed, heading)
- Implement pseudonym rotation: change vehicle identifier every 5 minutes (per C-ITS framework)
- Each pseudonym period = one Chambers world
- On rotation: burn linkage data between previous and current pseudonym

### ISSUE-044: V2X threat simulation (T14, T15, T16)
**Priority**: P1
**Phase**: 2
**Description**: Simulate V2X-specific threats:
- **T14**: Collect CAM broadcasts over 30-minute drive, attempt trajectory reconstruction across pseudonym rotations. Assert: cross-session linkage destroyed, reconstruction fails.
- **T15**: Apply trajectory analysis algorithms to re-identify vehicle across pseudonym changes. Assert: Chambers burn makes re-identification statistically harder.
- **T16**: Configure vehicle to hoard inbound V2X messages from other vehicles. Assert: inbound V2X data is used for real-time awareness only, not persisted.

### ISSUE-045: Consent revocation mid-session
**Priority**: P1
**Phase**: 2
**Description**: Implement paper Section 6.3:
- During active CARLA drive session, simulate driver revoking consent for insurer stakeholder
- Manifest updated: insurer declaration removed
- **Assert**: from revocation timestamp onward, zero data flows to insurer endpoint
- **Assert**: data already transmitted before revocation is not recalled (out of scope)
- **Assert**: audit log records revocation timestamp and affected categories

### ISSUE-046: HSM fallback mode
**Priority**: P1
**Phase**: 2
**Description**: Implement paper Section 6.2:
- During active session, simulate HSM becoming unavailable (kill SoftHSM2 process)
- **Assert**: vehicle remains "drivable" (CARLA simulation continues)
- **Assert**: gateway applies conservative default — no telemetry transmitted
- **Assert**: audit log records fallback event with duration and reason
- On HSM recovery: new session key generated, normal operation resumes
- **Assert**: compliance claim for fallback session is reduced (documented in audit log)

---

## Epic 5: ROS 2 + Gazebo ECU Integration (Phase 3)

### ISSUE-050: Gazebo vehicle model with simulated ECUs
**Priority**: P0
**Phase**: 3
**Description**: Create Gazebo Fortress model:
- Vehicle body with wheels, sensors (camera, LiDAR, GPS, IMU)
- Simulated ECUs as ROS 2 nodes:
  - `powertrain_ecu`: speed, RPM, throttle, brake pressure
  - `body_ecu`: door status, lights, wipers, seat sensors
  - `adas_ecu`: perception outputs, collision warnings
  - `infotainment_ecu`: media playback, Bluetooth state, navigation
  - `telematics_ecu`: cellular connectivity, OTA status
- CAN bus messages simulated as ROS 2 topics with automotive-standard message types

### ISSUE-051: Chambers gateway as ROS 2 node
**Priority**: P0
**Phase**: 3
**Description**: Wrap Chambers gateway as a ROS 2 node:
- Subscribe to all ECU topics (simulated CAN bus)
- Apply session seal, manifest evaluation, burn engine
- Publish to enforcement point topics (EP1-EP5)
- ROS 2 lifecycle node: configure → activate → deactivate maps to session start → active → session end/burn

### ISSUE-052: EP2 — Bluetooth pairing session (IVI)
**Priority**: P1
**Phase**: 3
**Description**: Simulate paper Section 10.2:
- Infotainment node simulates phone pairing via Bluetooth profiles (PBAP, MAP, A2DP)
- On pair: HSM generates pairing session key, contacts/call history/SMS encrypted under it
- During session: contact names displayed (manifest declaration)
- On disconnect: pairing session key destroyed, all synced data irrecoverable
- **Test scenarios**:
  - Rental car returned → previous renter's data gone
  - Vehicle sold → previous owner's phone data gone
  - Shared family car → each driver's data isolated to their pairing session

### ISSUE-053: EP3 — OBD-II diagnostic handler
**Priority**: P1
**Phase**: 3
**Description**: Simulate paper Section 10.4:
- Diagnostic handler node responds to simulated OBD-II requests (DTCs, PIDs, freeze frames)
- Authenticated diagnostic tool: receives decrypted responses (valid session credentials)
- Unauthenticated aftermarket dongle: receives encrypted (unusable) responses
- Insurance black box attempting to stream data via its own cellular: manifest controls apply at diagnostic handler level
- **Assert**: diagnostic data never leaves vehicle in plaintext without authentication

### ISSUE-054: EP5 — Wi-Fi hotspot passthrough
**Priority**: P1
**Phase**: 3
**Description**: Simulate paper Section 10.3:
- Wi-Fi node simulates vehicle hotspot
- Passenger device connects and generates traffic
- **Assert**: manifest declares "passthrough only, no inspection, no logging"
- **Assert**: gateway enforcement blocks any attempt to capture or store passenger traffic
- **Assert**: vehicle's own outbound data over Wi-Fi passes through same manifest check as cellular

### ISSUE-055: 5-EP integration test
**Priority**: P0
**Phase**: 3
**Description**: Full integration test exercising all 5 enforcement points simultaneously:
- Vehicle driving (EP1: cellular telemetry flowing)
- Phone paired (EP2: contacts synced)
- Diagnostic tool connected (EP3: authenticated session)
- V2X broadcasting (EP4: CAM messages with pseudonym rotation)
- Passenger on hotspot (EP5: Wi-Fi passthrough)
- All 5 EPs share one manifest, one HSM, one audit log
- **Assert**: each EP enforces its channel-specific rules
- **Assert**: single audit log captures all 5 channels coherently
- **Assert**: single burn engine destroys all session keys on vehicle park

---

## Epic 6: Compliance Artefact Generation

### ISSUE-060: GDPR Art. 30 processing record generator
**Priority**: P2
**Phase**: 1
**Description**: Generate a machine-readable processing record from manifest + audit log:
- Controller identity, DPO contact, processing purposes, data categories, recipients, retention periods, technical measures
- Auto-populated from manifest declarations and audit log entries
- Output: JSON + human-readable PDF

### ISSUE-061: GDPR Art. 17 erasure proof generator
**Priority**: P2
**Phase**: 1
**Description**: Generate cryptographic erasure proof from BurnReceipts:
- Per-session: session ID, key handle, destruction timestamp, HSM attestation, 6-layer completion status
- Aggregated: per-vehicle erasure history over time
- Output: signed JSON document suitable for DPA inquiry response

### ISSUE-062: R155 Annex 5 threat mitigation report
**Priority**: P2
**Phase**: 2
**Description**: Generate report mapping each simulated threat to R155 Annex 5 references:
- Threat 4.3.1 (abuse of privileges by staff) → manifest limits categories
- Threat 4.3.3 (unauthorised internet access) → session encryption
- Threat 4.3.6 (data breach by third party) → per-stakeholder boundaries
- Threat 5.4.1 (extraction from vehicle systems) → data encrypted at rest, key destroyed
- Output: structured report for type approval documentation

### ISSUE-063: AI Act Art. 10 data governance evidence
**Priority**: P2
**Phase**: 2
**Description**: Generate evidence that sealed ADAS events comply with AI Act data governance:
- Training data provenance: each sealed event has trigger, timestamp, capture window, anonymisation method
- Bias disclosure: sealed events are safety-critical only (biased sample — documented as limitation)
- Retention period and purpose declared in manifest
- Output: data governance report for AI Act conformity assessment

### ISSUE-064: CRA lifecycle documentation from manifest
**Priority**: P2
**Phase**: 3
**Description**: Generate CRA-required lifecycle documentation:
- Product description from manifest schema
- Vulnerability handling: sealed sessions reduce blast radius (document with incident simulation data)
- Security update mechanism: OTA channel in manifest
- Output: structured document for CRA conformity assessment (due Dec 2027)

---

## Epic 7: Observability and Developer Experience

### ISSUE-070: Chambers dashboard (terminal UI)
**Priority**: P2
**Phase**: 1
**Description**: Terminal-based dashboard (ratatui or textual) showing:
- Active sessions (count, duration, data volume)
- Manifest evaluation: messages passed vs. blocked (per stakeholder)
- Burn events (recent, with receipts)
- Audit log tail
- Enforcement point status (EP1-EP5)

### ISSUE-071: Audit log viewer CLI
**Priority**: P1
**Phase**: 1
**Description**: CLI tool for inspecting audit logs:
- `chambers-audit verify <session_id>` — verify HMAC chain integrity
- `chambers-audit show <session_id>` — display session summary (generated/declared/transmitted/burned)
- `chambers-audit export <session_id> --format json` — export for regulator
- `chambers-audit diff <session_id>` — show what was generated vs. what survived (data residue)

### ISSUE-072: Driver-readable session summary
**Priority**: P2
**Phase**: 2
**Description**: Implement paper's driver-facing transparency (Art. 13, Art. 14):
- Per-session summary: "This drive session produced 3 declared outputs: anonymised fault codes to manufacturer, an acceleration score to insurer, one sealed ADAS event to perception supplier. Everything else was destroyed."
- Human-readable, no technical jargon
- Generated automatically from audit log on session end

---

## Epic 8: Documentation and Contribution

### ISSUE-080: Architecture documentation
**Priority**: P1
**Phase**: 1
**Description**: Document the simulation testbed architecture:
- System context diagram (simulators → gateway → stakeholders)
- Component diagram (session seal, manifest, burn engine, audit log, gateway)
- Data flow diagrams per enforcement point
- Sequence diagrams: session lifecycle, sealed event capture, consent revocation, HSM fallback

### ISSUE-081: Setup guide per phase
**Priority**: P1
**Phase**: 1 (updated each phase)
**Description**: Developer setup guide:
- Prerequisites (Rust, Python, Docker, SUMO/CARLA/ROS2)
- Docker Compose quickstart
- Manual setup (macOS, Linux)
- Running tests
- Running scenarios

### ISSUE-082: Mapping document — paper section to code
**Priority**: P2
**Phase**: 3
**Description**: Create a traceability matrix mapping each section of the position paper to the simulation code that validates it:
- Paper Section → Issue → Code path → Test
- Ensures every claim in the paper has a corresponding simulation test

---

## Summary

| Epic | Issues | Phase | Description |
|------|--------|-------|-------------|
| 0: Bootstrap | 001-003 | 0 | Repo, Docker, CI |
| 1: Chambers Core | 010-015 | 1 | Rust: session seal, manifest, burn, audit, gateway |
| 2: SUMO Integration | 020-023 | 1 | Traffic sim, adapter, stakeholders, data residue report |
| 3: Threat Simulation | 030-033 | 1 | Threats T1-T4 (cellular channel) |
| 4: CARLA Integration | 040-046 | 2 | Sensors, ADAS events, V2X, consent, HSM fallback |
| 5: ROS 2 + Gazebo | 050-055 | 3 | ECUs, Bluetooth, OBD-II, Wi-Fi, 5-EP integration |
| 6: Compliance | 060-064 | 1-3 | GDPR, R155, AI Act, CRA artefact generators |
| 7: Observability | 070-072 | 1-2 | Dashboard, audit CLI, driver summary |
| 8: Documentation | 080-082 | 1-3 | Architecture, setup, paper-to-code traceability |

**Total**: 38 issues across 9 epics and 3 phases (+ bootstrap)
