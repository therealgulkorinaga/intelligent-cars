# POSITION PAPER

# Chambers for Automotive

## Sealed Ephemeral Sessions for Connected Vehicle Data Sovereignty

**Arko Ganguli**

**April 2026 — Second Edition**

---

*This paper applies the Chambers sealed ephemeral computation model to connected vehicle architectures, addressing the convergence of EU regulatory frameworks between September 2026 and December 2027.*

*Scope: private passenger vehicles. Fleet and commercial use cases are noted but not fully addressed.*

*Second edition: incorporates empirical validation from a multi-simulator testbed exercising the Chambers architecture across 189 automated tests, all 16 identified channel threats, and four stakeholder data flow configurations.*

---

## 1. Executive Summary

A modern connected vehicle is a mobile sensor platform containing approximately 150 electronic control units, running 100 million lines of code, equipped with cameras, LiDAR, radar, GPS, and microphones, and maintaining a persistent cellular connection to the manufacturer's cloud backend. Every layer of this architecture generates data. None of it has a declared preservation boundary.

This paper proposes applying the Chambers sealed ephemeral computation model — an open-source project implemented as a working Rust substrate with 44 passing tests (github.com/therealguikorinaga/chamber) — to connected vehicle architectures. Chambers introduces three concepts absent from current automotive software stacks: sealed drive sessions with ephemeral encryption keys, a typed preservation manifest declaring what data survives and for whom, and cryptographic burn semantics that destroy undeclared data when a drive session ends.

The primary intervention point is the telematics gateway — the highest-volume data egress channel. This paper also extends the Chambers boundary to four additional channels — Bluetooth, Wi-Fi, OBD-II, and V2X — each with its own enforcement point, threat model, and session lifecycle. All five enforcement points share one manifest schema and one burn engine.

**New in this edition.** The architectural claims in the first edition were theoretical. This edition reports the results of a simulation testbed that exercises the Chambers gateway against realistic vehicle telemetry streams. The testbed comprises an automotive-specific Rust implementation (3,054 lines, 28 tests), a Python simulation adapter layer (3,360 lines, 68 tests), four mock stakeholder endpoints, and 93 integration and end-to-end tests covering all 16 identified threats across all five communication channels. Key empirical findings:

- The preservation manifest correctly enforces per-stakeholder, per-category data routing: the OEM receives only anonymised sensor health, the insurer receives only driving behaviour scores without GPS, the ADAS supplier receives only sealed safety events, and the Tier-1 supplier receives only component diagnostics. No undeclared data reaches any endpoint.
- The six-layer burn engine completes in under one second, rendering session data cryptographically irrecoverable. Post-burn decryption attempts fail deterministically.
- Consent revocation mid-session takes effect within one processing cycle. The revoked stakeholder receives zero further data; other stakeholders are unaffected.
- The HSM fallback mode blocks all telemetry when the hardware security module is unavailable, with zero data leakage during the fallback period.
- The HMAC-chained audit log detects any tampering, and the driver-facing session summary contains no cryptographic jargon.
- Fleet-scale simulation (10 vehicles, 1,000 records) demonstrates consistent enforcement across concurrent sessions with independent audit chains.

These results do not constitute production validation. The testbed uses simulated sensor data and a software HSM. However, they demonstrate that the Chambers architecture is implementable, that its enforcement mechanisms function as specified, and that the concept mapping from position paper to working code is sound.

This paper is scoped to private passenger vehicles. Fleet, commercial, and regulatory telematics use cases (e.g., tachographs, usage-based insurance black boxes) involve different data subjects, legal bases, and retention mandates. These are noted where relevant but are not fully addressed.

> *Legal disclaimer: The regulatory analysis in this paper identifies how the Chambers architecture supports compliance. It does not constitute a determination of compliance for any specific implementation. Legal determination requires case-by-case assessment by qualified counsel in the relevant jurisdiction.*


## 2. What Is Chambers

Chambers is a sealed ephemeral computation model. The reference implementation is an open-source Rust substrate (github.com/therealguikorinaga/chamber) comprising an encrypted memory pool, a six-layer burn engine, native application isolation, and 44 passing tests.

The model was originally developed for general-purpose privacy-preserving computation. Its core proposition is that the unit of destruction matters as much as the fact of destruction. Rather than improving persistent environments (as Tails, Qubes OS, and container-based systems do), Chambers rejects persistence as the default, treating a typed, grammar-constrained "world" as the primary unit of computation — and destruction.

The model introduces five core concepts:

- **World:** a sealed, temporary computational environment with bounded inputs and outputs.
- **Grammar:** a typed schema declaring what operations are permitted within a world and what outputs may survive its destruction.
- **Preservation law:** an explicit declaration of what data persists beyond the world's lifetime, for whom, and under what conditions.
- **Burn engine:** a multi-layer destruction mechanism (logical, cryptographic, storage, memory, semantic) that enforces the preservation law by destroying everything not explicitly declared.
- **Sealed event:** a bounded, time-limited data capture triggered by a specific condition, declared in the grammar, and retained as a declared exception to the ephemeral default.

This paper applies these concepts to the connected vehicle domain. It does not claim novelty in the underlying cryptographic primitives (ephemeral key management, session-based encryption, and cryptographic erasure are established techniques used in the Signal Protocol, AWS Nitro Enclaves, and Apple's per-session encryption). The contribution is the application of sealed ephemeral semantics to automotive telematics as an architectural compliance mechanism across multiple regulatory frameworks simultaneously.


### 2.1 Related Work

The Chambers model intersects with several existing automotive and security standards:

- **AUTOSAR Crypto Stack:** provides cryptographic service interfaces for ECU software, including key management and secure communication. Chambers operates at a higher architectural level, using the Crypto Stack's primitives to implement session-level ephemeral boundaries.
- **ISO/SAE 21434:** defines cybersecurity engineering requirements for road vehicles. Chambers provides an implementation pattern for several 21434 requirements, including threat analysis (clause 8), risk treatment (clause 9), and vulnerability management (clause 12), but does not replace the standard's process requirements.
- **TCG TPM 2.0 for Automotive:** the Trusted Computing Group's TPM specification provides the hardware root of trust on which Chambers' HSM-based key management depends. The burn engine's key destruction relies on TPM or HSM secure key deletion functions.
- **Signal Protocol (sealed sender):** implements ephemeral key exchange for messaging. Chambers applies analogous principles to vehicle data flows, with the preservation manifest serving a role comparable to Signal's session management, but for data retention rather than communication confidentiality.
- **AWS Nitro Enclaves:** provides isolated compute environments with attestation. Chambers' sealed sessions share the enclave model's isolation properties but apply them temporally (per drive session) rather than spatially (per compute instance).


## 3. The Problem: Persistence as Default Ontology

The connected vehicle's software architecture treats data persistence as the default behaviour at every layer. The bootloader logs. The kernel logs. The hypervisor maintains state. The middleware publishes state changes. The applications generate telemetry. The cloud backend stores everything indefinitely.


### 3.1 The Architecture

The modern software-defined vehicle operates on a layered architecture:

| Layer | Component |
|-------|-----------|
| **Layer 0** | Hardware/silicon: SoC/MCU (Arm, RISC-V), HSM/TPM (root of trust), Sensors (Camera, LiDAR, Radar, GPS, Mic), Actuators (Brake, steer, motor) |
| **Layer 1** | Secure boot chain: ROM → 1st → 2nd → kernel |
| **Layer 2** | Hypervisor (QNX / L4Re / Xen) |
| **Layer 3** | Operating systems (per VM): AUTOSAR Classic (Safety RTOS, ASIL-D), Linux/QNX (ADAS, perception), Android Auto (Infotainment, apps) |
| **Layer 4** | Middleware (SOME/IP, DDS) |
| **Layer 5** | Applications (ADAS, body, telematics, OTA) |
| **Network** | CAN / CAN FD, LIN, FlexRay, Ethernet |
| **External** | 5G, V2X, Wi-Fi, Bluetooth, OEM cloud |

QNX deployed in 275+ million vehicles (Dec 2025). A typical vehicle runs ~100M lines of code across ~150 ECUs.

The hypervisor layer — deployed in over 275 million vehicles via QNX alone — provides computational isolation between virtual machines. However, this isolation is for safety, not privacy. The hypervisor stops code from crossing boundaries but lets data flow freely.


### 3.2 The Data Residue Problem

CAN bus metadata — even without payload content — reveals driving style, acceleration profiles, braking patterns, and route characteristics through message frequency and timing patterns alone.

From the driver's perspective, the data that genuinely needs to survive a drive session is minimal: trip distance for maintenance scheduling, fault codes if something broke, and safety-critical event records required by regulation. Everything else exists because logging is the default behaviour of every software layer.

Some OEMs do publish data retention policies (e.g., Volvo's integrated systems). However, a declared retention policy is not an architectural enforcement mechanism. The distinction between a policy promise and a system property is central to this paper.


### 3.3 Who Benefits from the Data

The vehicle generates data that flows outward to nine categories of recipient:

**Legitimate:** OEM cloud (telemetry, digital twin), OTA server (firmware, software), Tier-1 suppliers (diagnostics).

**Commercial:** Insurers (behaviour data), Data brokers (location, habits), Advertisers (profiles).

**Adversarial:** Law enforcement (subpoena), Foreign states (surveillance), Hackers (ransomware).

The first three categories — OEM, tier-1 suppliers, and OTA servers — have legitimate engineering needs. The remaining six exploit the absence of an architectural boundary between functional and commercially motivated data retention.

**Without Chambers: all nine get everything. With Chambers: row 1 gets declared data only. Rows 2 and 3 get nothing.**


## 4. The Incentive Failure

The capability to build ephemeral data boundaries exists at every layer. No participant in the value chain has a commercial incentive to build it.

| Participant | Incentive |
|-------------|-----------|
| Chip vendor (NXP, Qualcomm) | No incentive for policy |
| Hypervisor (BlackBerry QNX) | Customer is the OEM |
| Tier-1 (Elektrobit, Conti) | Builds to spec. No spec = nothing |
| OEM | Controls config. Monetises data. Privacy vs. revenue |
| **Driver** | **Wants privacy. Zero access to any layer** |

**Regulation forces the OEM to require it.** The capability exists at every layer. Only the mandate was missing.

This argument applies primarily to the private passenger vehicle market. Two exceptions should be noted:

- **Insurance telematics (black box) programmes:** drivers voluntarily consent to persistent data collection in exchange for usage-based premiums. These programmes require data to survive across sessions to calculate risk scores. Chambers accommodates this through the preservation manifest — the insurer's declaration would specify multi-session retention for specific data categories, with the driver's explicit consent recorded in the manifest. The key difference is that retention becomes declared and bounded rather than silent and indefinite.
- **Fleet and commercial vehicles:** the data subject may be the employer rather than the driver, the legal basis is typically contractual necessity or legitimate interest, and regulatory telematics (e.g., tachographs under Regulation (EU) No 165/2014) mandate specific retention periods. A full treatment of fleet use cases is outside the scope of this paper.


## 5. The Legal Framework

Six EU regulatory instruments apply directly to connected vehicle data processing. This section maps each relevant provision to the specific vehicle data problem it addresses and identifies how Chambers provides architectural evidence supporting compliance.

> *Throughout this section, "supports compliance with" means the architecture provides technical mechanisms and auditable evidence that facilitate meeting the legal obligation. It does not mean the architecture alone is sufficient for legal compliance, which requires additional organisational, procedural, and contractual measures.*


### 5.1 GDPR (Regulation (EU) 2016/679)

The GDPR applies to all processing of personal data by connected vehicles. The EDPB confirmed this scope in Guidelines 01/2020 on processing personal data in the context of connected vehicles.

**Article 5 — Principles relating to processing.** Article 5(1)(a) requires transparency. The preservation manifest provides per-session visibility into data flows. Article 5(1)(b) requires purpose limitation. The manifest binds each data category to a stated purpose per stakeholder. Article 5(1)(c) requires data minimisation. The manifest declares exactly what persists. Article 5(1)(e) requires storage limitation. The burn engine enforces automatic session expiry. Article 5(2) requires accountability. The audit log provides the accountability artefact.

**Article 6 — Lawfulness of processing.** The EDPB Guidelines 01/2020 state that consent for connected vehicle data processing must be provided separately from the vehicle purchase contract, for specific purposes, and may not be bundled. The manifest supports granular, per-stakeholder, per-category consent recording.

**Article 17 — Right to erasure.** The burn engine provides cryptographic erasure under current best practices: the session key is destroyed via the HSM's secure key deletion function, rendering encrypted session data unrecoverable under currently known cryptographic attacks.

> *Cryptographic erasure is not absolute erasure. If the encryption algorithm is broken in the future (including by quantum computing advances), or if the key was generated with insufficient entropy, residual risk exists. The paper characterises this as cryptographic erasure under current best practices, not provable mathematical erasure.*

Critically, cryptographic erasure applies only to data within the Chambers boundary. Once data has been transmitted to a third party under a manifest declaration (e.g., to an insurer's backend), erasure of that copy is the recipient's obligation under their own GDPR compliance.

**Article 25 — Data protection by design and by default.** Chambers directly supports both obligations. The sealed session layer is the technical measure integrated into the processing architecture (by design). The default state is ephemeral — all data is encrypted under a session key that will be destroyed unless a declaration explicitly preserves specific categories (by default).

**Article 30 — Records of processing activities.** The preservation manifest describes intended processing categories per stakeholder. The audit log records actual processing — what data was generated, what was declared, what was transmitted, and what was burned. Together, they provide the Article 30 record.

**Article 35 — DPIA.** The EDPB has confirmed that connected vehicle data processing likely requires a DPIA. The audit log, aggregated across sessions, provides the bounded data inventory needed for the DPIA's risk assessment.

**Articles 44–49 — International transfers.** The manifest can include jurisdiction constraints that block transmission to non-EU endpoints, supporting Chapter V compliance at the network layer.


### 5.2 ePrivacy Directive (2002/58/EC)

Article 5(3) applies to any entity that stores information on or gains access to information stored in terminal equipment. The EDPB has confirmed that a connected vehicle constitutes terminal equipment. Chambers supports ePrivacy compliance by ensuring that no data is accessed or transmitted without a corresponding declaration in the manifest.


### 5.3 UNECE R155 — Cybersecurity

R155 has been mandatory for all new vehicles since July 2024. Annex 5 lists 69 attack vectors. Several are directly relevant to data exfiltration:

| Annex 5 Ref | Threat | Chambers Mitigation |
|-------------|--------|-------------------|
| 4.3.1 | Abuse of privileges by staff | No raw data reaches backend; manifest limits categories |
| 4.3.3 | Unauthorised internet access to servers | Breach yields only current session's encrypted data |
| 4.3.6 | Data breach by third party | Per-stakeholder boundaries; each supplier sees only declared categories |
| 5.4.1 | Extraction from vehicle systems | Data encrypted at rest; key destroyed on session end |


### 5.4 EU Cyber Resilience Act (Regulation (EU) 2024/2847)

> *The CRA's Article 1(3) excludes products for which safety requirements are laid down in sectoral Union legislation, including type-approval regulations. Connected vehicles have type approval under Regulation (EU) 2018/858. Many legal commentators believe the entire vehicle, including embedded telematics, is excluded from CRA scope. The following analysis assumes a broad interpretation under which the OEM's cloud backend, mobile applications, and OTA infrastructure are in scope. This interpretation may not survive regulatory guidance or judicial review.*

The CRA entered into force on 10 December 2024. Reporting obligations apply from 11 September 2026. Full enforcement begins 11 December 2027. Chambers supports these requirements through ephemeral-by-default architecture and session-level encryption.


### 5.5 EU AI Act (Regulation (EU) 2024/1689)

The AI Act classifies AI in autonomous vehicles as high-risk under Annex I. The compliance deadline for Annex I products is August 2027. Article 10 requires that training data sets be subject to appropriate data governance. The Chambers sealed event mechanism captures bounded temporal windows around safety-critical events for model retraining, with declared purpose and retention periods. However, sealed events represent a biased sample (safety-critical events only).


### 5.6 EU Data Act (Regulation (EU) 2023/2854)

The Data Act, applicable from September 2025, requires that connected products be designed to make data accessible to the user (Article 3) and that users can share data with third parties (Article 4). The manifest provides the data inventory and access control mechanism supporting these requirements.


### 5.7 Enforcement Precedents

**GM/LexisNexis (2024).** General Motors shared detailed driving behaviour data with insurance data brokers LexisNexis and Verisk without meaningful driver consent. Under a Chambers architecture, this data flow would have been blocked at the gateway unless the driver had explicitly approved an insurer declaration in the manifest.

**EDPB Guidelines 01/2020.** The EDPB established that consent for vehicle data processing must not be bundled with the vehicle purchase contract; that Article 25 data protection by design applies to vehicle design; and that local processing within the vehicle should be preferred. Chambers implements these recommendations architecturally.

**Volkswagen/Cariad Data Exposure (2024).** Volkswagen's software subsidiary left location data for approximately 800,000 electric vehicles accessible in an unsecured cloud system. Under Chambers, this data would not have existed in the cloud in raw form — only anonymised outputs declared in the manifest would have been transmitted.


## 6. Safety-Critical Exceptions

Several regulatory mandates require data retention that cannot be subject to driver consent or ephemeral session boundaries. The Chambers architecture must accommodate these without compromising its core model.

| Mandate | Data Required | Retention | Chambers Treatment |
|---------|-------------|-----------|-------------------|
| eCall (Reg. (EU) 2015/758) | Minimum data set: location, timestamp, VIN, direction | Transmitted on crash detection | Exempt: outside Chambers boundary, hardware-triggered |
| Event Data Recorder (EU Vehicle General Safety Reg.) | Pre-crash speed, braking, steering, seatbelt status | Write-once, survives power loss | Exempt: write-once partition outside session boundary |
| R155 Security Event Logging | Cybersecurity incidents and anomalies | Must survive session for forensic analysis | Exempt: declared as mandatory retention in manifest |
| Tachograph (Reg. (EU) 165/2014) | Driving time, rest periods, speed | 28 days on vehicle, 365 days on driver card | Out of scope (commercial vehicles; noted for completeness) |


### 6.1 Architectural Treatment

Safety-critical data is handled through a hardware-protected, write-once partition that sits outside the Chambers sealed session boundary. This partition is physically separate from the session-encrypted data store, write-once (data can be appended but not modified or deleted by software), not subject to the burn engine, and declared in the manifest so the driver can see what is retained and why.


### 6.2 HSM Failure and Fallback

If the HSM fails or is unavailable, the system must not render the vehicle inoperable. The fallback mode operates as follows: the vehicle remains drivable, no telemetry is transmitted until the HSM is restored, the audit log records the fallback event, and compliance claims for fallback sessions are reduced.

**Simulation validation (new).** The testbed confirms this behaviour empirically. When the software HSM is terminated mid-session, the gateway immediately blocks all telemetry transmission. Zero data records reach any stakeholder endpoint during the fallback period. The audit log records the fallback event with timestamp and reason. On HSM recovery, a new session is established with a fresh key and normal operation resumes. The vehicle simulation continues to generate records throughout — they are simply not transmitted, confirming that the Chambers layer does not affect vehicle drivability.


### 6.3 Consent Revocation Mid-Session

If a driver revokes consent for a specific stakeholder during an active session, the manifest is updated immediately. Data already transmitted cannot be recalled. From the point of revocation onward, no further data passes through the gateway for that stakeholder. The audit log records the revocation timestamp and the affected categories.

**Simulation validation (new).** The testbed confirms that consent revocation takes effect within one processing cycle. After revoking the insurer stakeholder mid-session, the insurer endpoint receives exactly zero further data records. Other stakeholders (OEM, ADAS supplier, Tier-1) continue receiving their declared categories without interruption. The revocation is idempotent (revoking twice does not error) and is logged in the audit trail. Revoking a nonexistent stakeholder is handled gracefully without affecting the session.


### 6.4 Law Enforcement Data Requests

If a session key has been destroyed, compliance with a law enforcement request for historical session data is architecturally impossible. This is by design, not by defect. For prospective requests, the manifest can be updated to add a law enforcement declaration with appropriate legal basis.


## 7. Architected Regulatory Compliance Framework

This section consolidates the legal mappings from Section 5 into a structured framework tracing each Chambers component to the specific regulatory obligation it supports, the evidence it produces, and the verification method.

| Component | Obligation | Regulation | Evidence | Verification |
|-----------|-----------|------------|----------|-------------|
| **Session Seal** | Data minimisation | GDPR 5(1)(c) | Session-scoped lifecycle | Manifest audit |
| | Secure by default | CRA Annex I(1) | No persistent store | Arch. review |
| | Terminal equip. | ePrivacy 5(3) | No undeclared access | Gateway logs |
| | Confidentiality | CRA Annex I(2) | Encrypted session data | HSM attestation |
| | Attack surface | R155 Annex 5 | Ephemeral = nothing to steal | Pen test |
| **Manifest** | Purpose limitation | GDPR 5(1)(b) | Per-stakeholder purpose | Schema audit |
| | Processing records | GDPR Art. 30 | Machine-readable log | Auto export |
| | Transparency | GDPR Art. 13 | Session summary to driver | HMI inspect |
| | Data governance | AI Act Art. 10 | Training data provenance | Conformity |
| | Human oversight | AI Act Art. 14 | Driver visibility into AI | User testing |
| | User data access | Data Act Art. 3 | Data inventory | API verify |
| | Supply chain risk | R155 7.2.2.2 | Per-supplier boundaries | Supplier audit |
| | Lifecycle docs | CRA Art. 13 | Living documentation | Version ctrl |
| **Burn Engine** | Right to erasure | GDPR Art. 17 | Key destruction log | HSM audit |
| | Storage limitation | GDPR 5(1)(e) | Auto session expiry | Retention check |
| | Data minimisation | CRA I(3a) | Undeclared irrecoverable | Forensic verify |
| | Blast radius | CRA Art. 14 | Breach = burned data | Incident sim |
| | Extraction prevent | R155 5.4.1 | Encrypted, no key | Red team |
| **Audit Log** | Accountability | GDPR 5(2) | Per-session record | Regulator review |
| | Record-keeping | AI Act Art. 12 | Immutable event log | Integrity check |
| | Vuln reporting | CRA Art. 14 | Incident scope evidence | ENISA submit |
| | DPIA support | GDPR Art. 35 | Bounded data inventory | DPA review |
| | Gatekeeper block | Data Act 4(4) | Blocked tx log | Gateway audit |
| **Gateway** | By design | GDPR 25(1) | Architectural enforcer | Arch. review |
| | By default | GDPR 25(2) | Default = no egress | Config audit |
| | Transfer restrict | GDPR 44-49 | Jurisdiction constraints | Net monitor |
| | Frozen config | R155 L1 | Whitelisted destinations | Rule audit |
| | Sharing control | Data Act Art. 4 | Consent-gated sharing | Cross-check |

**28 obligations. 5 components. One compliance artefact.** Each row is a legal obligation satisfied architecturally, not through documentation.


## 8. The Regulatory Convergence Timeline

| Date | Event | Significance |
|------|-------|-------------|
| April 2026 | **Now** | R155/R156 already live since July 2024 |
| September 2026 | **CRA vulnerability reporting** | 24-hour mandatory reporting to ENISA. 5 months away. |
| August 2026 | **AI Act high-risk (Annex III)** | Autonomous driving classified high-risk. 4 months away. |
| August 2027 | AI Act Annex I (vehicles) | Full vehicle AI compliance. 16 months away. |
| December 2027 | **CRA full enforcement** | Non-compliant products banned. EUR 15M or 2.5%. 20 months away. |
| January 2028 | China GB 44495 | All vehicles. 21 months away. |

**Cumulative penalty exposure:** CRA: EUR 15M / 2.5%. AI Act: EUR 35M / 7%. GDPR: EUR 20M / 4%. R155: type approval refused. Data Act: EUR 20M / 4%.


## 9. The Chambers Architecture for Vehicles

### 9.1 Core Concepts

**Sealed drive sessions.** When the vehicle starts, the telematics gateway creates a session encrypted under an ephemeral key stored in the HSM. All data flowing through the gateway is bound to that key. When the vehicle parks, the key is destroyed. Undeclared data becomes cryptographically unrecoverable under current best practices.

**Typed preservation manifest.** A grammar-constrained declaration specifying, per stakeholder: data categories, granularity (raw/aggregated/anonymised), retention period, identity linkage, purpose, jurisdiction constraints, and legal basis. The manifest is signed and immutable within a session.

**Burn engine.** Cryptographic key destruction via the HSM. This is a system property that provides cryptographic erasure under current best practices. It does not constitute mathematical proof of absolute erasure (see Section 6 caveat on residual risk).


### 9.2 Insertion Point: The Telematics Gateway

The telematics gateway is the primary chokepoint through which the majority of vehicle telemetry passes over cellular. It is viable because it is not safety-rated (no ASIL certification required), does not affect type approval, and handles the highest-volume data egress path.

> *The telematics gateway is not the only data egress point. Bluetooth tethering, direct third-party modem access (Android Auto, CarPlay), OBD-II physical extraction, and V2X short-range communications are additional channels not controlled by the gateway. Chambers provides protection for the cellular telemetry path, which carries the vast majority of data volume, but is not a complete solution for all egress channels. A comprehensive architecture would need to extend the sealed session boundary to these additional interfaces.*


### 9.3 Concept Mapping

| Chambers | Vehicle | Regulation |
|----------|---------|-----------|
| **World** | Drive session | GDPR processing scope |
| **Grammar** | Preservation manifest | Art. 30 processing record |
| **Allowed ops** | Declared data / stakeholder | Art. 5(1)(c) minimisation |
| **Burn engine** | Session key destruction | Art. 17 erasure proof |
| **Preservation law** | What survives, for whom | CRA lifecycle docs |
| **Sealed event** | ADAS incident recording | AI Act Art. 10 |


### 9.4 Regulatory Compliance Mapping

Each regulation maps to specific Chambers components:

**GDPR:** Art. 5(1)(c) Data minimisation → Preservation manifest. Art. 17 Right to erasure → Burn engine. Art. 25 By design/default → Ephemeral default state. Art. 30 Processing records → Automatic audit log.

**R155:** CSMS / manage cyber risk → Sealed sessions. Annex 5 attack vectors → Gateway enforcement. Supplier risk management → Per-stakeholder manifest.

**CRA:** Secure by design → Ephemeral by default. 24h vuln reporting → Reduced blast radius. Lifecycle documentation → Manifest = documentation.

**AI Act:** Art. 10 Data governance → Sealed ADAS events. Art. 13 Transparency → Driver-readable manifest. Art. 12 Record-keeping → Immutable audit trail.

**Sovereignty:** Foreign exfiltration risk → Data never leaves unburned. No verification today → Machine-verifiable proof.


### 9.5 Stakeholder Data Flows Under Chambers

The preservation manifest does not eliminate data sharing. It makes data sharing explicit, auditable, and consent-gated:

**OEM:** Anonymised sensor health metrics, aggregated per drive cycle, stripped of location and identity. Retention: 90 days (example). Legal basis: legitimate interest. Purpose: predictive maintenance.

**Insurer:** Acceleration, braking, and cornering severity scores per trip. No GPS, no timestamps beyond date. Legal basis: explicit consent, obtained separately. Purpose: risk model inputs.

**ADAS supplier:** Sealed safety events only — bounded temporal windows around safety-critical triggers. Anonymised. Retention: 12 months. Legal basis: legitimate interest. Purpose: model retraining (component of training data governance under AI Act Art. 10).

**Tier-1 supplier:** Component-specific telemetry from their subsystem only. No driver identity. Retention: warranty period. Legal basis: contractual necessity.

**Simulation validation (new).** The testbed exercises all four stakeholder configurations simultaneously against a mixed data stream containing position, speed, acceleration, sensor health, driving behaviour, sealed events, diagnostic codes, contact sync, media metadata, and V2X CAM records. The results:

| Data type | OEM | Insurer | ADAS | Tier-1 | Burned |
|-----------|-----|---------|------|--------|--------|
| Sensor health (anonymised) | Received | Blocked | Blocked | Received | After session |
| Position (anonymised to ~1km) | Received | Blocked | Blocked | Blocked | After session |
| Driving behaviour (per-trip score) | Blocked | Received | Blocked | Blocked | After session |
| Sealed ADAS event | Blocked | Blocked | Received | Blocked | After retention |
| Diagnostic code | Blocked | Blocked | Blocked | Received | After session |
| Contact sync | Blocked | Blocked | Blocked | Blocked | Immediately |
| Media metadata | Blocked | Blocked | Blocked | Blocked | Immediately |
| V2X CAM | Blocked | Blocked | Blocked | Blocked | On pseudonym rotation |
| Camera frame | Blocked | Blocked | Blocked | Blocked | Immediately |
| Raw GPS trace | Blocked | Blocked | Blocked | Blocked | Immediately |

No data type reaches a stakeholder for which it is not explicitly declared. Undeclared types (contact sync, media metadata, raw GPS, camera frames) are blocked for all stakeholders and burned on session end.


## 10. Extended Chambers Boundary: Beyond the Telematics Gateway

The original Chambers proposal targets the telematics gateway — the highest-volume data egress path. However, four additional channels carry data out of the vehicle, each with its own threat model and architectural constraints. A comprehensive architecture requires enforcement points at each channel.

**Five enforcement points. One manifest. One burn engine.**

- EP 1: Telematics gateway (cellular) — covers bulk telemetry, OTA, cloud sync. Highest volume. Primary path.
- EP 2: Infotainment VM (Bluetooth) — covers contact sync, call history, media metadata. Per-pairing session lifecycle.
- EP 3: Diagnostic handler (OBD-II) — covers diagnostic extraction. Authenticated access with encrypted responses.
- EP 4: V2X stack (DSRC / C-V2X) — covers broadcast tracking and inbound data hoarding. Per-pseudonym session lifecycle.
- EP 5: Network layer (Wi-Fi) — covers hotspot passthrough and outbound data. Shares gateway policy.


### 10.1 Channel Analysis

| Channel | Threat | Chambers World | Enforcer |
|---------|--------|---------------|----------|
| **Cellular** | Bulk telemetry exfiltration | Drive session | Gateway |
| | OEM cloud data hoarding | Drive session | Gateway |
| | Third-party data selling | Drive session | Gateway |
| | Foreign state backend access | Drive session | Gateway |
| **Bluetooth** | Contact/SMS sync persists | Pairing session | IVI VM |
| | Call history retained on resale | Pairing session | IVI VM |
| | Media metadata leakage | Pairing session | IVI VM |
| **Wi-Fi** | Passenger traffic inspection | Hotspot session | Gateway |
| | Outbound data over Wi-Fi | Drive session | Gateway |
| | Rogue AP data injection | Connection session | Gateway |
| **OBD-II** | Casual diagnostic extraction | Diagnostic session | Diag handler |
| | Insurance dongle bypass | Diagnostic session | Diag handler |
| | Stolen vehicle data dump | N/A (at rest) | HSM encrypt |
| **V2X** | Position tracking via CAMs | Pseudonym session | V2X stack |
| | Trajectory re-identification | Pseudonym session | V2X stack |
| | Inbound V2X data hoarding | Drive session | V2X stack |

**16 threats. 5 channels. Each mapped to a Chambers world and enforcement point.** The cellular gateway covers 4 threats (highest volume). The remaining 12 require enforcement points at the infotainment VM, diagnostic handler, and V2X stack.


### 10.2 Bluetooth

When a phone pairs with the vehicle, the infotainment system pulls contacts, call history, SMS messages, and media metadata via Bluetooth profiles (PBAP for contacts, MAP for messages, A2DP for media metadata). When the phone disconnects, this data typically remains in the head unit indefinitely. A rental car returned, a vehicle sold, a shared family car — previous users' personal data persists.

Bluetooth pairing maps naturally to a Chambers sealed world: phone pairs → HSM generates pairing session key → all synced data encrypted → phone disconnects → session key burns via HSM → contacts, call logs, media metadata become irrecoverable.

**Manifest declaration for Bluetooth:** "Contact names displayed during session for caller ID. No persistence beyond disconnection."


### 10.3 Wi-Fi

Two attack surfaces exist. First, the vehicle as Wi-Fi hotspot: the Chambers manifest declares passthrough only, no inspection, no logging of passenger traffic. Second, vehicle outbound data over Wi-Fi passes through the same manifest check as cellular.


### 10.4 OBD-II

The hardest channel because it is a physical port. Chambers can help with encrypted diagnostic responses (ECUs encrypt under a session key, so only authenticated tools can read responses) and manifest declarations for OBD-II ("Authenticated access only, session-scoped diagnostic responses").


### 10.5 V2X (Vehicle-to-Everything)

V2X uses DSRC (5.9 GHz) or C-V2X (cellular) to broadcast Cooperative Awareness Messages (CAMs). Chambers treats pseudonym rotation as a session boundary: each pseudonym period is a Chambers world; on rotation, the previous pseudonym's linkage data burns. Inbound V2X data is ephemeral — used for real-time awareness but not stored.


### 10.6 Architectural Summary

EP 1 (cellular) is the minimum viable product. It covers the highest-volume, highest-risk channel and is deployable as a gateway firmware module without modifying the vehicle's safety-critical software. EPs 2–5 require deeper integration and represent a phased expansion of the Chambers boundary.


## 11. Simulation Testbed: Empirical Validation

*This section is new in the second edition.*

### 11.1 Motivation

The first edition identified as its primary limitation that "no production implementation exists. The concept mapping is architectural, not validated on production telematics hardware." To begin addressing this gap, we developed a multi-simulator testbed that exercises the Chambers gateway against realistic vehicle telemetry streams, validating that the architecture is implementable and that its enforcement mechanisms function as specified.

The testbed does not constitute production validation. It uses simulated sensor data and a software HSM. Its purpose is to demonstrate feasibility and correctness, not production readiness.


### 11.2 Testbed Architecture

The testbed comprises four layers:

```
 SIMULATOR LAYER
   SUMO (traffic telemetry, fleet scale)
   CARLA (ego-vehicle sensors, drive sessions)
   ROS 2 / Gazebo (ECU-level, CAN bus, sensor fusion)
          |
   TraCI / CARLA API / ROS 2 topics
          |
 CHAMBERS GATEWAY (Rust, 3,054 lines)
   Session Seal (AES-256-GCM via SoftHSM2 PKCS#11)
   Manifest Evaluator (typed schema, per-stakeholder filtering)
   Burn Engine (6-layer: logical → cryptographic → storage → memory → semantic → verification)
   Audit Log (SQLite, HMAC-SHA256 chain)
          |
 SIMULATION ADAPTER (Python, 3,360 lines)
   SUMO adapter (TraCI bridge, driving behaviour scoring)
   CARLA adapter (sensor suite, sealed event capture, V2X pseudonym rotation)
   ROS 2 adapter (ECU topics, Bluetooth pairing, OBD-II, Wi-Fi)
   Local Gateway (pure-Python manifest evaluation for standalone testing)
          |
 MOCK STAKEHOLDER ENDPOINTS (FastAPI)
   OEM Cloud | Insurer API | ADAS Supplier | Tier-1 Supplier
   Data Broker (rogue) | Foreign Endpoint (non-EU)
```

**Technology choices.** The Chambers gateway is implemented in Rust, extending the patterns from the reference implementation (github.com/therealguikorinaga/chamber). Cryptographic operations use the `ring` library (AES-256-GCM with random nonces for encryption, HMAC-SHA256 for audit chain integrity). The software HSM implements the PKCS#11 key lifecycle (generate, encrypt, decrypt, destroy) with keys that never leave the HSM boundary. The simulation adapter layer uses Python for compatibility with SUMO's TraCI protocol, CARLA's Python API, and ROS 2's rclpy bindings.


### 11.3 Preservation Manifest Schema

The testbed implements the typed preservation manifest as a JSON Schema with the following structure:

```json
{
  "manifest_version": "1.0",
  "vehicle_id": "<pseudonymised>",
  "session_id": "<uuid>",
  "stakeholders": [
    {
      "id": "oem-cloud",
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
    }
  ],
  "mandatory_retention": [
    {"type": "ecall", "regulation": "EU_2015_758", "treatment": "exempt_outside_boundary"}
  ]
}
```

The schema supports 14 data types, 4 granularity levels, 4 legal bases, and jurisdiction constraints. Each stakeholder declaration specifies which data types it receives, at what granularity, for what purpose, under what legal basis, and with what retention period. Fields can be explicitly included or excluded per category.


### 11.4 Results: Manifest Enforcement

The manifest evaluator was tested against all four stakeholder configurations from Section 9.5. For each data record entering the gateway, the evaluator produces a per-stakeholder filtered view containing only declared fields at declared granularity, or blocks the record entirely if the data type is not declared.

**Key findings:**

- **Field-level filtering works correctly.** The insurer receives driving behaviour records containing `acceleration`, `braking`, and `cornering_severity` but never `gps_position`, `timestamps`, or `route`. The manifest's `excluded_fields` declaration is enforced deterministically.
- **Granularity transforms are applied.** The OEM receives position data anonymised to approximately 1km grid resolution. The insurer receives per-trip scores rather than raw telemetry. The ADAS supplier receives raw sealed events (within the declared capture window). Each granularity level produces different output for the same input record.
- **Undeclared data types are blocked for all stakeholders.** Contact sync, media metadata, camera frames, and raw GPS traces are not declared in any stakeholder's categories. These records are blocked at the gateway for all endpoints and logged as blocked in the audit trail.
- **Jurisdiction constraints block cross-border transfer.** When a stakeholder endpoint is configured with a non-EU jurisdiction (US or CN), the gateway blocks all data transmission to that endpoint, regardless of whether the data type is otherwise declared. The jurisdiction check is logged as a separate policy violation in the audit trail.


### 11.5 Results: Session Lifecycle and Burn Engine

The testbed exercises the complete session lifecycle: session start (ephemeral key generation), active processing (encrypt, evaluate, route), and session end (6-layer burn).

**Key findings:**

- **Ephemeral key generation via SoftHSM2** produces a unique AES-256-GCM key per session. The key handle is returned to the gateway; the key material never leaves the HSM boundary.
- **The 6-layer burn engine completes in under one second** for a typical session. Each layer produces a completion receipt:
  1. *Logical deletion:* session data marked as deleted.
  2. *Cryptographic destruction:* HSM key destroyed via `destroy_key`. Post-destruction, `encrypt` and `decrypt` operations with the destroyed handle return errors.
  3. *Storage overwrite:* encrypted data regions overwritten with random bytes.
  4. *Memory zeroing:* in-memory buffers associated with the session zeroed.
  5. *Semantic destruction:* session-to-key and session-to-data mappings removed.
  6. *Verification:* all previous layers confirmed complete; key handle confirmed invalid.
- **Post-burn irrecoverability is deterministic.** After the burn engine completes, any attempt to decrypt session data fails. The test suite explicitly attempts decryption with the destroyed key handle and asserts failure.
- **Concurrent sessions are independent.** In fleet-scale simulation (10 vehicles), each vehicle's session has its own key, its own manifest evaluation, its own audit chain, and its own burn lifecycle. Burning one session does not affect any other.


### 11.6 Results: Threat Mitigation

All 16 threats from Section 10.1 were simulated as automated test scenarios:

| # | Threat | Simulation | Result |
|---|--------|-----------|--------|
| T1 | Bulk telemetry exfiltration | Attempt raw data egress to undeclared endpoint | **Blocked.** Gateway rejects all undeclared endpoints. |
| T2 | OEM cloud data hoarding | OEM requests data beyond manifest declaration | **Blocked.** OEM receives only declared categories at declared granularity. |
| T3 | Third-party data selling | Data broker endpoint (undeclared) attempts to receive data | **Blocked.** Zero data reaches undeclared third party. |
| T4 | Foreign state backend access | Non-EU jurisdiction endpoint attempts to receive data | **Blocked.** Jurisdiction check rejects non-EU destinations. |
| T5 | Bluetooth contact persistence | Contacts synced, then phone disconnects | **Burned.** Session end destroys pairing key; synced data irrecoverable. |
| T6 | Call history on resale | Previous owner's pairing session data queried after session end | **Burned.** Pairing session key destroyed; data irrecoverable. |
| T7 | Media metadata leakage | A2DP metadata checked for persistence after session | **Burned.** No persistence beyond pairing session. |
| T8 | Wi-Fi passenger inspection | Attempt to read passenger traffic through hotspot | **Blocked.** Manifest enforces passthrough-only policy. |
| T9 | Wi-Fi outbound data | Vehicle outbound data over Wi-Fi | **Filtered.** Same manifest check applied as cellular. |
| T10 | Rogue AP injection | Rogue AP attempts data injection | **Blocked.** Gateway policy enforcement applies. |
| T11 | OBD-II casual extraction | Unauthenticated OBD reader requests data | **Blocked.** Diagnostic handler requires authentication. |
| T12 | Insurance dongle bypass | Insurance black box attempts to stream via OBD-II | **Controlled.** Manifest controls apply to diagnostic channel. |
| T13 | Stolen vehicle data dump | Attempt to extract data from parked vehicle | **Encrypted.** Data encrypted at rest; session key already destroyed. |
| T14 | V2X position tracking | CAM broadcasts collected across pseudonym rotations | **Burned.** Cross-session linkage data destroyed on rotation. |
| T15 | V2X trajectory re-identification | Trajectory analysis attempted across pseudonym sessions | **Mitigated.** Linkage data between consecutive pseudonyms destroyed. |
| T16 | Inbound V2X data hoarding | Attempt to store inbound V2X messages | **Blocked.** Inbound V2X data treated as ephemeral. |

**All 16 threats mitigated.** 189 automated tests pass across the Rust gateway (28 tests), Python simulation layer (68 tests), and integration/end-to-end suite (93 tests).


### 11.7 Results: Audit Log and Compliance Artefacts

The HMAC-chained audit log was tested for integrity, completeness, and usability:

- **Chain integrity verification detects tampering.** If any audit log entry is modified after creation, the HMAC chain verification fails. The test suite explicitly modifies an entry and asserts that `verify_chain` returns false.
- **100% of data flow decisions are logged.** Every record entering the gateway produces an audit entry: either `DataTransmitted` (with stakeholder and category) or `DataBlocked` (with reason). Session start, session end, burn completion, and consent revocation events are also logged.
- **The audit export produces valid JSON** suitable for regulator review, containing session ID, all events with timestamps, and the complete HMAC chain.
- **The driver-facing session summary contains no cryptographic jargon.** The summary mentions stakeholders by role (not ID), describes data destruction in plain language, and does not include terms like "HMAC", "AES", "SHA-256", or "key handle". A representative summary:

> *"Your Driving Session Summary. During this trip, your vehicle collected 6 data points. 6 data points were shared with authorised partners (as you agreed). 1 data request was blocked because it was not covered by your agreements. You withdrew permission from 1 partner during this session. Their data access was stopped immediately. When your trip ended, all raw data was permanently and irrecoverably destroyed."*


### 11.8 Results: Data Residue Comparison

The testbed includes a data residue analyser that quantifies the difference between a no-Chambers baseline (all data forwarded to all stakeholders, all data persisted indefinitely) and the Chambers-enforced configuration.

In the fleet-scale scenario (10 vehicles, 100 records each, 12 data types):

- **Baseline:** 100% of data persisted across all stakeholders.
- **Chambers:** Only declared categories at declared granularity survive the session. Undeclared types are burned entirely. Declared types are filtered to stakeholder-specific views.
- **Reduction:** The testbed consistently demonstrates significant data reduction. With the demo manifest (4 stakeholders), fewer than 30% of generated data categories produce any output; the remaining 70%+ are blocked or burned entirely. Within the categories that produce output, field-level filtering and granularity transforms further reduce the data that survives.

This quantifies the paper's core claim from Section 3.2: the data that genuinely needs to survive a drive session is minimal. The Chambers architecture makes this architectural rather than aspirational.


### 11.9 Simulation Limitations

The testbed validates correctness, not production performance. Specific limitations:

- **Software HSM (SoftHSM2) does not model hardware HSM latency.** Cryptographic operations complete in microseconds in simulation; production HSMs may add milliseconds. The testbed includes support for configurable latency injection.
- **Simulated sensor data.** SUMO provides realistic traffic telemetry but does not produce real camera frames or LiDAR point clouds. CARLA provides realistic sensor data but in a controlled environment. Neither matches the noise, edge cases, and bandwidth of production sensors.
- **No real CAN bus.** The ROS 2 adapter simulates ECU message passing via ROS topics, not actual CAN/CAN FD frames. Protocol-level behaviour (bus arbitration, error frames, gateway filtering) is not tested.
- **Single-node deployment.** The testbed runs all components on a single machine. Production deployment requires distributed components across vehicle ECUs with different trust levels.
- **No adversarial cryptanalysis.** The threat tests verify that the gateway blocks undeclared data flows. They do not test whether a sophisticated attacker could extract data through side channels, timing attacks, or physical access to the HSM.


## 12. Implementation Path

**Route 1: Tier-1 middleware product.** Gateway firmware module sold to OEMs and tier-1 suppliers for integration. Fastest path to production.

**Route 2: Open-source SDV contribution.** Sealed session layer contributed to Automotive Grade Linux or Android Automotive OS. Wider adoption surface, slower path.

**Route 3: Fleet operator contractual mandate.** Fleet operators contractually require a preservation manifest in vehicle procurement specifications.

**New: Route 0 (simulation testbed).** The testbed described in Section 11 is itself a deliverable. It provides OEMs and tier-1 suppliers with a working reference implementation they can evaluate against their own vehicle architectures before committing to production integration. The testbed's Docker Compose configuration allows a full Phase 1 environment (SUMO traffic simulation, Chambers gateway, mock stakeholders, audit viewer) to be started with a single command.


## 13. Limitations

- ~~No production implementation exists.~~ **Updated:** A simulation testbed now validates the architectural concepts against realistic vehicle telemetry streams (see Section 11). However, the testbed uses simulated sensor data and a software HSM. Production validation on representative hardware (e.g., NXP S32G) remains required.
- The telematics gateway is the primary but not only data egress point. Section 10 extends the Chambers boundary to Bluetooth, Wi-Fi, OBD-II, and V2X, but EP 1 (cellular) is the minimum viable product. EPs 2–5 require deeper integration and represent a phased roadmap.
- The preservation manifest requires OEM cooperation to define the data taxonomy for each vehicle platform.
- Cryptographic erasure is not absolute. It provides protection under current best practices but is subject to future cryptographic advances, including quantum computing. The system should use post-quantum cryptographic algorithms as they become standardised for automotive use.
- The CRA's applicability to vehicle-embedded telematics is legally contested. The analysis assumes a broad interpretation.
- Safety-critical data flows (eCall, EDR, security event logs) require mandatory retention outside the Chambers boundary (see Section 6).
- Sealed ADAS events provide a biased training data sample. They are a component of AI Act data governance, not a complete solution.
- Once data is transmitted to a third party under a manifest declaration, erasure of that copy is the recipient's obligation. Chambers cannot enforce erasure at third-party systems.
- HSM availability is not universal, particularly in lower-cost vehicle segments. Deployment feasibility depends on the specific vehicle platform's hardware capabilities.
- The retention periods cited in stakeholder examples (90 days, 12 months, etc.) are illustrative. Actual periods would be determined by the OEM's DPIA and regulatory requirements.
- The legal analysis does not constitute legal advice. Compliance determination requires case-by-case assessment by qualified counsel.
- **New:** The simulation testbed validates correctness and feasibility but not production performance. Software HSM latency does not model hardware HSM latency. Simulated sensor data does not match production noise and bandwidth. The ROS 2 adapter simulates CAN bus via ROS topics, not actual CAN/CAN FD frames. See Section 11.9 for full simulation limitations.

## 14. Why the Industry Should Not Resist This

**Regulatory inevitability.** The compliance trajectory is clear. The question is not whether OEMs will have to limit data retention, but whether they get ahead of enforcement with an architecture that provides compliance evidence, or get caught in an enforcement action.

**Legal fragility.** Consent bundled with vehicle purchase is not freely given under GDPR Article 7 (per several DPA positions). If that interpretation holds, the legal basis for vehicle data collection collapses. The manifest provides fallback legal bases for specifically declared categories.

**Competitive differentiation.** One OEM with architecturally demonstrable privacy creates market pressure on every competitor.

**Data Act readiness.** The Data Act requires user data access and portability. The manifest provides the data inventory and access control mechanism needed to comply.

**Simulation evidence (new).** The testbed described in Section 11 reduces the integration risk for early adopters. An OEM can evaluate the Chambers gateway against simulated versions of their own telemetry pipelines before committing to production integration. The 189 passing tests provide a concrete quality baseline.


## 15. Conclusion

The connected vehicle industry faces a regulatory convergence across six EU frameworks that demands architectural answers to what have historically been policy questions. GDPR asks: can you demonstrate data minimisation? The CRA asks: is your product secure by design? The AI Act asks: where does your training data come from? R155 asks: have you mitigated the listed attack vectors? The Data Act asks: can the user access their data? The ePrivacy Directive asks: did you obtain consent before accessing terminal equipment?

Chambers provides architectural evidence supporting compliance across all six frameworks through one intervention at the telematics gateway. The preservation manifest — together with the audit log — provides the GDPR processing record, the R155 risk evidence, the CRA lifecycle documentation, and the AI Act data governance artefact.

The driver sees a manifest that says: this drive session produced three declared outputs — anonymised fault codes to the manufacturer, an acceleration score to the insurer, one sealed ADAS event to the perception supplier. Everything else was destroyed. That is the compliance evidence. That is the thing no car on the road can produce today.

**New in this edition:** that claim is no longer purely theoretical. The simulation testbed demonstrates a working Chambers gateway processing vehicle telemetry through manifest evaluation, stakeholder routing, and six-layer cryptographic burn. 189 automated tests confirm that declared data reaches its intended stakeholder, undeclared data is blocked, session keys are irrecoverable after burn, audit chains detect tampering, and driver summaries are human-readable. All 16 identified channel threats are mitigated. The architecture works.

The regulatory deadline is not theoretical. September 2026 is five months away. December 2027 is twenty months away. Vehicle development cycles are eighteen to thirty-six months. The window to build this is now.

---

*github.com/therealguikorinaga/chamber*

*Simulation testbed: github.com/arkoganguli/intelligent-cars (Rust + Python, 189 tests, Apache 2.0)*
