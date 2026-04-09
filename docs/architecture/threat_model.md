# Threat Model

This document maps the 16 threats from the Chambers position paper (Section
10.1) to the simulation testbed implementation. Each threat is assigned to a
communication channel, an enforcement point, and a test scenario.

## Threat Matrix

| # | Channel | Threat Description | Chambers World Type | EP | Simulation Method | Test Ref |
|---|---------|-------------------|--------------------|----|-------------------|----------|
| T1 | Cellular | Bulk telemetry exfiltration | Drive session | EP1 | SUMO: attempt raw data egress to undeclared endpoint; gateway blocks undeclared fields | ISSUE-030 |
| T2 | Cellular | OEM cloud data hoarding | Drive session | EP1 | Mock OEM requests data beyond manifest (raw GPS, driving behaviour); gateway transmits only declared categories at declared granularity | ISSUE-031 |
| T3 | Cellular | Third-party data selling (GM/LexisNexis) | Drive session | EP1 | Route data to mock data broker endpoint not in manifest; gateway rejects; broker endpoint logs SECURITY ALERT | ISSUE-032 |
| T4 | Cellular | Foreign state backend access | Drive session | EP1 | Mock endpoint with non-EU jurisdiction; manifest jurisdiction constraint blocks transfer | ISSUE-033 |
| T5 | Bluetooth | Contact/SMS sync persists after disconnect | Pairing session | EP2 | Simulate phone disconnect; verify synced data irrecoverable after pairing key burn | ISSUE-052 |
| T6 | Bluetooth | Call history retained on vehicle resale | Pairing session | EP2 | Simulate ownership change; verify previous pairing sessions burned | ISSUE-052 |
| T7 | Bluetooth | Media metadata (A2DP) leakage | Pairing session | EP2 | Verify A2DP metadata does not persist beyond pairing session | ISSUE-052 |
| T8 | Wi-Fi | Passenger traffic inspection | Hotspot session | EP5 | Simulate hotspot connection; verify manifest enforces passthrough-only, no inspection | ISSUE-054 |
| T9 | Wi-Fi | Outbound vehicle data over Wi-Fi | Drive session | EP5 | Verify same manifest check applies to Wi-Fi egress as cellular (EP1 rules) | ISSUE-054 |
| T10 | Wi-Fi | Rogue AP data injection | Hotspot session | EP5 | Simulate rogue AP; verify gateway policy enforcement | ISSUE-054 |
| T11 | OBD-II | Casual diagnostic data extraction | Diagnostic session | EP3 | Simulate unauthenticated OBD reader; verify encrypted (unusable) responses | ISSUE-053 |
| T12 | OBD-II | Insurance dongle bypass | Diagnostic session | EP3 | Simulate insurance black box on OBD-II with its own cellular; manifest controls apply | ISSUE-053 |
| T13 | OBD-II | Stolen vehicle data dump | Drive session | EP3 | Verify data encrypted at rest; key destroyed on session end; no recoverable plaintext | ISSUE-053 |
| T14 | V2X | Position tracking via CAM broadcasts | Pseudonym session | EP4 | Collect CAMs over 30-min drive via CARLA-SUMO co-sim; attempt trajectory reconstruction across pseudonym rotations; reconstruction fails | ISSUE-044 |
| T15 | V2X | Trajectory re-identification | Pseudonym session | EP4 | Apply trajectory analysis across pseudonym changes; Chambers burn makes re-identification statistically harder | ISSUE-044 |
| T16 | V2X | Inbound V2X data hoarding | Pseudonym session | EP4 | Verify inbound V2X messages used for real-time awareness only, not persisted | ISSUE-044 |

## Per-Channel Analysis

### Cellular (T1-T4) -- Highest Volume, Gateway Is Primary Defense

The cellular channel carries the most data by volume: telemetry, sensor
health, driving behaviour, ADAS events, diagnostics, and OTA updates. The
Chambers gateway at EP1 is the primary defense for all four cellular threats.

```
  Threat Landscape:
  +-------------------------------------------------------------+
  |  Vehicle TCU (Cellular)                                      |
  |                                                              |
  |  T1: Bulk exfiltration ----> Gateway blocks undeclared       |
  |  T2: OEM hoarding --------> Manifest limits granularity      |
  |  T3: Third-party selling --> Endpoint not in manifest        |
  |  T4: Foreign jurisdiction -> Jurisdiction check blocks        |
  |                                                              |
  +-------------------------------------------------------------+
                    |
                    v
            +---------------+
            |   GATEWAY     |
            |   (EP1)       |
            +-------+-------+
                    |
        +-----------+-----------+
        |           |           |
        v           v           v
   OEM (EU)   Insurer (EU)  BLOCKED
   anonymised  per_trip     (broker,
   aggregate   score        foreign)
```

**T1 -- Bulk telemetry exfiltration.** A rogue process or compromised TCU
firmware attempts to dump all raw telemetry to an external server. The gateway
intercepts all outbound data and evaluates it against the manifest. Since the
rogue endpoint is not listed as a stakeholder, all data is blocked. The audit
log records every blocked attempt.

**T2 -- OEM cloud data hoarding.** The OEM is a declared stakeholder but its
manifest declaration limits it to `sensor_health` at `anonymised_aggregate`
granularity. If the OEM endpoint requests raw GPS traces, individual driving
behaviour, or camera frames, the manifest evaluator blocks those categories.
The audit log records the violation.

**T3 -- Third-party data selling.** Modelled after the GM/OnStar/LexisNexis
incident (paper Section 5.7). A data broker endpoint (`/broker/data`) is
configured in the mock stakeholders but is never listed in the manifest.
The gateway blocks all data to unknown endpoints. Even if the OEM receives
data legitimately, post-transmission forwarding to a broker is outside the
Chambers boundary (documented as a limitation).

**T4 -- Foreign state backend access.** A mock endpoint declares jurisdiction
`US` or `CN`. The manifest's jurisdiction constraint (`["EU"]`) causes the
gateway to block the transfer. The audit log records a
`JurisdictionBlocked` event.

### Bluetooth (T5-T7) -- Pairing Session = Sealed World

Bluetooth data has a natural lifecycle that maps perfectly to the Chambers
sealed world concept: data enters on pairing, is used during the session,
and should vanish on disconnect.

```
  Threat Landscape:
  +-------------------------------------------------------------+
  |  Bluetooth / IVI                                             |
  |                                                              |
  |  T5: Contacts persist ---> Pairing key burn on disconnect    |
  |  T6: Call history on       -> Ownership change triggers      |
  |       resale                  full pairing history burn      |
  |  T7: Media metadata ------> Session-scoped, burned with key |
  |                                                              |
  +-------------------------------------------------------------+
                    |
                    v
       +--------------------+
       | Pairing Session    |
       | Key (HSM)          |
       +----+----------+----+
            |          |
        CONNECT    DISCONNECT
            |          |
            v          v
        Sync data   Burn key
        encrypted   -> all data
        under key   irrecoverable
```

**T5 -- Contact/SMS sync persists.** When a phone pairs via PBAP/MAP, the IVI
syncs contacts and messages encrypted under a pairing session key. On
disconnect, the burn engine destroys this key. The 6-layer protocol ensures
no recoverable plaintext remains.

**T6 -- Call history retained on resale.** Vehicle ownership transfer triggers
a burn of all previous pairing sessions. Each historical pairing session had
its own key; destroying all keys renders all synced data irrecoverable.

**T7 -- Media metadata leakage.** A2DP metadata (track names, artists) is
session-scoped. It exists only while the phone is connected and is encrypted
under the pairing session key. Burn on disconnect removes it.

### Wi-Fi (T8-T10) -- Passthrough Policy, Same Gateway Rules

The Wi-Fi enforcement point has a dual role: passenger traffic is
passthrough-only, while vehicle-originated outbound data follows the same
gateway rules as cellular.

```
  Threat Landscape:
  +-------------------------------------------------------------+
  |  Wi-Fi Hotspot                                               |
  |                                                              |
  |  T8: Passenger traffic   --> Manifest: passthrough-only      |
  |       inspection              no capture, no logging          |
  |  T9: Vehicle outbound    --> Same as EP1 (cellular rules)    |
  |       over Wi-Fi                                              |
  |  T10: Rogue AP injection --> Gateway policy enforcement       |
  |                                                              |
  +-------------------------------------------------------------+
                    |
        +-----------+-----------+
        |                       |
  Passenger traffic      Vehicle outbound
        |                       |
        v                       v
  PASSTHROUGH              GATEWAY (EP1 rules)
  (no inspect,             manifest filter
   no log,                 + jurisdiction
   no store)               + granularity
```

**T8 -- Passenger traffic inspection.** The manifest declares
`wifi_passthrough` with passthrough-only treatment. The gateway enforces
zero inspection or storage of passenger device traffic.

**T9 -- Outbound data over Wi-Fi.** When the vehicle itself sends data over
Wi-Fi (as an alternative to cellular), the same manifest checks apply. The
enforcement point routes to the same gateway pipeline as EP1.

**T10 -- Rogue AP data injection.** If a rogue access point attempts to inject
data into the vehicle, the gateway policy enforcement blocks unrecognized
inbound connections. Only authenticated data paths are processed.

### OBD-II (T11-T13) -- Authenticated Access, Encrypted Responses

The OBD-II port provides physical access to vehicle diagnostics. Chambers
enforces authentication before data is released in usable form.

```
  Threat Landscape:
  +-------------------------------------------------------------+
  |  OBD-II Port                                                 |
  |                                                              |
  |  T11: Casual extraction ---> Unauthenticated = encrypted     |
  |  T12: Insurance dongle       response (unusable)             |
  |        bypass                                                |
  |  T13: Stolen vehicle    ---> Data encrypted at rest,         |
  |        data dump              key destroyed on session end   |
  |                                                              |
  +-------------------------------------------------------------+
                    |
        +-----------+-----------+
        |                       |
  Authenticated             Unauthenticated
  (valid credentials)       (aftermarket dongle)
        |                       |
        v                       v
  Decrypted response       Encrypted response
  (manifest-filtered)      (ciphertext, unusable
                            without session key)
```

**T11 -- Casual diagnostic extraction.** An aftermarket OBD reader plugged into
the port without proper authentication receives only encrypted responses.
Without the session key (held in the HSM, never extractable), the data is
unusable.

**T12 -- Insurance dongle bypass.** An insurance black box (e.g., plugged into
OBD-II with its own cellular) attempts to stream vehicle data. Since it
connects through the diagnostic handler, the manifest controls apply. Field
filtering and jurisdiction checks prevent uncontrolled data extraction.

**T13 -- Stolen vehicle data dump.** All vehicle data is encrypted at rest
under the session key. Once the session ends (or the vehicle is powered off),
the key is destroyed via the 6-layer burn protocol. A thief with physical
access to storage finds only ciphertext with no recoverable key.

### V2X (T14-T16) -- Pseudonym Rotation as Session Boundary

V2X communication uses pseudonyms that rotate every 5 minutes per the C-ITS
framework. Each pseudonym period is a separate Chambers world. On rotation,
linkage data between the old and new pseudonym is destroyed.

```
  Threat Landscape:
  +-------------------------------------------------------------+
  |  V2X Stack (DSRC / C-V2X)                                   |
  |                                                              |
  |  T14: Position tracking ---> Pseudonym rotation every 5 min  |
  |        via CAM broadcasts     + linkage data burned           |
  |  T15: Trajectory re-ID  ---> Cross-session linkage destroyed |
  |  T16: Inbound V2X       ---> Ephemeral awareness only,      |
  |        hoarding                not persisted                  |
  |                                                              |
  +-------------------------------------------------------------+
                    |
                    v
  +---------------------------+
  | Pseudonym Period 1        |
  | (0:00 - 5:00)             |
  | Pseudonym: ABCD-1234      |
  | CAM broadcasts encrypted  |
  +---------------------------+
         |
         | 5-minute rotation
         v
  +---------------------------+
  | BURN linkage data         |
  | Destroy pseudonym key     |
  | No mapping old -> new     |
  +---------------------------+
         |
         v
  +---------------------------+
  | Pseudonym Period 2        |
  | (5:00 - 10:00)            |
  | Pseudonym: EFGH-5678      |
  | New key, no link to prev  |
  +---------------------------+
```

**T14 -- Position tracking via CAMs.** An adversary collecting CAM broadcasts
attempts to reconstruct a vehicle's trajectory. With pseudonym rotation every
5 minutes and linkage data burned on each rotation, the adversary can only
reconstruct 5-minute segments. Cross-segment stitching requires linkage data
that no longer exists.

**T15 -- Trajectory re-identification.** Advanced trajectory analysis
algorithms attempt to re-identify a vehicle across pseudonym changes based
on movement patterns. The Chambers burn of linkage data makes this
statistically harder, though not impossible with very distinctive patterns
(acknowledged as a limitation).

**T16 -- Inbound V2X data hoarding.** The vehicle receives V2X messages (CAMs,
DENMs) from surrounding vehicles. These are used for real-time situational
awareness only (collision avoidance, traffic light timing). The Chambers
model treats them as ephemeral: used in memory, never persisted to storage.

## Limitations

The following threats are acknowledged but outside the scope of the Chambers
simulation testbed:

### Physical Access Attacks

Chambers assumes the HSM provides a trust anchor. Attacks that compromise the
HSM hardware itself (side-channel, fault injection, decapping) are outside
the threat model. The simulation uses a software HSM (`SoftwareHsm`) that
does not model physical tamper resistance.

Specific physical threats not covered:

- Direct memory probing while the vehicle is running
- JTAG/debug port access to the telematics ECU
- HSM hardware compromise or key extraction via physical attack
- Cold boot attacks on volatile memory

### Post-Transmission Enforcement

Once data leaves the vehicle boundary via a declared stakeholder endpoint,
Chambers cannot enforce how the stakeholder handles it. Examples:

- OEM receives anonymised aggregates per manifest, then internally
  cross-references with other data sources to re-identify individuals
- Insurer receives trip scores, then shares with third-party actuarial firms
- ADAS supplier receives sealed events, then retains beyond the declared
  retention period

The manifest + audit log provide evidence of what was transmitted and under
what terms, enabling contractual and regulatory enforcement after the fact,
but the system cannot technically prevent post-transmission misuse.

### Quantum Risk

The testbed uses AES-256-GCM for session encryption. While AES-256 is
considered quantum-resistant for symmetric encryption, the broader
cryptographic ecosystem (key exchange, certificate validation) may be
vulnerable to future quantum computers. This is a long-term risk documented
in the paper but not addressed in the simulation.

### Additional Acknowledged Limitations

- **Simulation fidelity.** SoftHSM2 does not model hardware HSM latency;
  performance benchmarks may not reflect production hardware. Configurable
  latency injection is planned.

- **Burn verification on physical storage.** The 6-layer burn operates on
  in-memory structures. On actual flash/eMMC storage, wear leveling and
  journaling may leave residual copies. Production implementation would
  require hardware-level secure erase.

- **Side-channel leakage.** The simulation does not model timing attacks,
  power analysis, or electromagnetic emanation from the gateway process.

- **Network-level attacks.** Man-in-the-middle on the cellular, Wi-Fi, or
  V2X channels is partially addressed by TLS/DTLS in production but not
  simulated in the testbed.
