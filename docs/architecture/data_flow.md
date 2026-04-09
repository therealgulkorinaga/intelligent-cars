# Data Flow Documentation

Detailed data flow diagrams for every enforcement point, the manifest
evaluation pipeline, the burn protocol, and sealed event handling.

## Per-Enforcement-Point Data Flows

### EP1: Cellular Gateway

The highest-volume channel. All standard vehicle telemetry (position, speed,
acceleration, sensor health, diagnostics, driving behaviour, ADAS events)
flows through EP1 to stakeholder endpoints.

```
  ECU Telemetry
  (position, speed, accel, sensor_health, diagnostics, adas_events)
       |
       v
  +---------------------------+
  | Simulator Adapter         |
  | (SUMO TraCI / CARLA API)  |
  | Convert to DataRecord     |
  +---------------------------+
       |
       v
  +---------------------------+
  | Gateway: process_record   |
  +---------------------------+
       |
       v
  +---------------------------+
  | HSM: encrypt under        |
  | session AES-256-GCM key   |
  | Store ciphertext          |
  +---------------------------+
       |
       v
  +---------------------------+
  | Manifest Evaluator        |
  +---------------------------+
       |
       +---> OEM declared sensor_health?
       |     YES -> filter fields, apply anonymised_aggregate
       |            granularity, check jurisdiction = EU
       |            -> transmit to /oem/telemetry
       |
       +---> Insurer declared driving_behaviour?
       |     YES -> filter: keep [accel, braking, cornering]
       |            exclude: [gps_position, timestamps]
       |            apply per_trip_score granularity
       |            -> transmit to /insurer/trip
       |
       +---> ADAS supplier declared sealed_event?
       |     YES (if trigger = safety_critical) ->
       |            exclude: [driver_id, vin]
       |            apply anonymised granularity
       |            -> transmit to /adas/event
       |
       +---> Tier-1 declared component_telemetry / diagnostic_code?
       |     YES -> apply pseudonymised or raw granularity
       |            -> transmit to /tier1/diagnostics
       |
       +---> Undeclared data type / no stakeholder match?
       |     -> BLOCK
       |     -> Audit: DataBlocked (reason: undeclared_data_type)
       |
       +---> Rogue endpoint (data broker)?
       |     -> BLOCK (endpoint not in manifest)
       |     -> Audit: DataBlocked (reason: unknown_endpoint)
       |
       +---> Foreign jurisdiction endpoint?
             -> BLOCK (jurisdiction mismatch)
             -> Audit: JurisdictionBlocked
```

### EP2: Bluetooth / IVI

Bluetooth data has a pairing-session scope: data exists only while a phone
is connected. On disconnect, the pairing session key is burned.

```
  Phone Pairs via Bluetooth
  (PBAP contacts, MAP messages, A2DP media metadata)
       |
       v
  +---------------------------+
  | IVI / Infotainment ECU    |
  | (ROS 2 node in Phase 3)  |
  +---------------------------+
       |
       v
  +---------------------------+
  | HSM: generate pairing     |
  | session key (separate     |
  | from drive session key)   |
  +---------------------------+
       |
       v
  +---------------------------+
  | Encrypt contacts, call    |
  | history, SMS under        |
  | pairing session key       |
  +---------------------------+
       |
       v
  +---------------------------+
  | Manifest: check if any    |
  | stakeholder declares      |
  | bluetooth_pairing type    |
  +---------------------------+
       |
       +---> Typically NO stakeholder declares contacts
       |     -> contacts displayed locally only
       |     -> never transmitted externally
       |
       v
  Phone Disconnects
       |
       v
  +---------------------------+
  | Burn Engine: destroy      |
  | pairing session key       |
  | 6-layer protocol          |
  +---------------------------+
       |
       v
  +---------------------------+
  | All synced contacts,      |
  | call history, SMS are     |
  | irrecoverable             |
  +---------------------------+
       |
       v
  Audit: BurnCompleted (pairing session)

  Scenarios validated:
  - Rental car return -> previous renter's data gone
  - Vehicle resale -> previous owner's phone data gone
  - Shared family car -> each driver isolated to their session
```

### EP3: OBD-II Diagnostic

OBD-II access is session-based and authenticated. Unauthenticated devices
receive encrypted (unusable) responses.

```
  Diagnostic Tool Connects to OBD-II Port
       |
       v
  +---------------------------+
  | Authentication Check      |
  | (session credentials)     |
  +---------------------------+
       |
       +---> Authenticated (valid session)?
       |     |
       |     v
       |  +---------------------------+
       |  | Decrypt diagnostic data   |
       |  | (DTCs, PIDs, freeze       |
       |  |  frames) using session    |
       |  |  key                      |
       |  +---------------------------+
       |     |
       |     v
       |  +---------------------------+
       |  | Manifest Evaluator        |
       |  | Check: diagnostic_code    |
       |  | declared by Tier-1?       |
       |  +---------------------------+
       |     |
       |     +---> YES -> transmit filtered diagnostics
       |     +---> NO  -> return decrypted to tool only
       |                   (no external transmission)
       |
       +---> Unauthenticated (aftermarket dongle / rogue)?
             |
             v
          +---------------------------+
          | Return encrypted response |
          | (ciphertext unusable      |
          |  without session key)     |
          +---------------------------+
             |
             v
          Audit: DataBlocked (reason: unauthenticated_obd)

  Insurance dongle attempting to stream via its own cellular:
  -> manifest controls apply at diagnostic handler level
  -> same field filtering and jurisdiction checks as EP1
```

### EP4: V2X Stack

V2X uses pseudonym rotation as the session boundary. Each pseudonym period
is a separate Chambers world with its own linkage data.

```
  Vehicle Broadcasting CAM Messages
  (position, speed, heading, vehicle type)
       |
       v
  +---------------------------+
  | Pseudonym Manager         |
  | Current pseudonym ID      |
  | Rotation interval: 5 min |
  +---------------------------+
       |
       v
  +---------------------------+
  | Encrypt CAM under current |
  | pseudonym session key     |
  +---------------------------+
       |
       v
  +---------------------------+
  | Broadcast CAM to          |
  | surrounding vehicles      |
  | (CARLA-SUMO co-sim)       |
  +---------------------------+
       |
       v
  +---------------------------+
  | Manifest Evaluator        |
  | v2x_cam declared?         |
  +---------------------------+
       |
       +---> Smart city authority declared v2x_cam?
       |     YES -> anonymised granularity
       |            exclude: [vin, driver_id, license_plate]
       |            -> transmit to city-transport endpoint
       |
       +---> Inbound V2X from other vehicles?
             -> used for real-time awareness ONLY
             -> NOT persisted, NOT stored
             -> ephemeral in memory, discarded after use

  Pseudonym Rotation (every 5 minutes):
       |
       v
  +---------------------------+
  | Destroy linkage data      |
  | between old pseudonym     |
  | and new pseudonym         |
  +---------------------------+
       |
       v
  +---------------------------+
  | Burn Engine: destroy      |
  | pseudonym session key     |
  | Generate new pseudonym    |
  | and new session key       |
  +---------------------------+
       |
       v
  Audit: BurnCompleted (pseudonym rotation)
  -> cross-session trajectory reconstruction blocked
```

### EP5: Wi-Fi Hotspot

Wi-Fi has a split policy: passenger traffic is passthrough-only (no
inspection, no logging), while vehicle-originated outbound data follows the
same rules as EP1.

```
  +---------------------------+
  | Wi-Fi Hotspot Active      |
  +---------------------------+
       |
       +---> Passenger device connects
       |     |
       |     v
       |  +---------------------------+
       |  | Passthrough Policy        |
       |  | - NO inspection           |
       |  | - NO logging              |
       |  | - NO capture              |
       |  | - NO storage              |
       |  +---------------------------+
       |     |
       |     v
       |  Traffic passes through to internet unmodified
       |  Manifest declares: wifi_passthrough, passthrough-only
       |
       +---> Vehicle's own outbound data over Wi-Fi
              |
              v
           +---------------------------+
           | Same as EP1 (Cellular)    |
           | Gateway + Manifest check  |
           | Field filter + granularity|
           | Jurisdiction check        |
           +---------------------------+
              |
              v
           Route to declared stakeholders or BLOCK
```

## Manifest Evaluation Flow

Every data record follows this evaluation pipeline inside
`ManifestEvaluator::evaluate`:

```
  DataRecord arrives
       |
       v
  [1] For each stakeholder in manifest.stakeholders:
       |
       +---> [2] Is stakeholder consent revoked?
       |          YES -> skip (blocked)
       |          NO  -> continue
       |
       +---> [3] Does stakeholder declare this data_type?
       |          Match record.data_type against category.data_type
       |          NO match -> skip (no declaration for this type)
       |          MATCH -> continue with matched CategoryDeclaration
       |
       +---> [4] Jurisdiction check
       |          Is stakeholder.endpoint_jurisdiction in
       |          category.jurisdiction[]?
       |          NO  -> skip (jurisdiction blocked)
       |          YES -> continue
       |
       +---> [5] Filter fields
       |          If category.fields is set:
       |            keep ONLY listed fields (allow-list)
       |          If category.excluded_fields is set:
       |            remove listed fields (deny-list)
       |
       +---> [6] Apply granularity transformation
       |          Raw:        no change
       |          Aggregated: round numerics, strip identity fields
       |          Anonymised: strip all identity, coarsen GPS to ~1km,
       |                      round numerics aggressively
       |          PerTripScore: collapse all numerics into single
       |                        trip_score value (0-100)
       |
       +---> [7] Produce FilteredDataRecord
                  (data_type, filtered fields, granularity, timestamp)

  Collect results:
  - transmitted[]: list of (StakeholderId, FilteredDataRecord)
  - blocked[]:     list of (category, reason)

  If no stakeholder matched at all:
  -> BlockRecord(reason: "No stakeholder declared this data type")

  Each transmit/block decision logged to audit log.
```

### Identity Fields Stripped During Granularity

The following fields are automatically removed during anonymisation:

| Granularity | Fields Removed |
|-------------|---------------|
| Aggregated | `driver_id`, `vin`, `name`, `email`, `phone`, `contact`, `ssn`, `license_plate` |
| Anonymised | All of the above plus `device_id`, `imei`, `mac_address` |
| PerTripScore | All original fields replaced with single `trip_score` |

## Burn Flow

When a session ends (park event, disconnect, pseudonym rotation), the
6-layer burn protocol executes:

```
  Session End Trigger
       |
       v
  +-------------------------------------------------------+
  |  Layer 1: LOGICAL DELETION                             |
  |  Mark all session data entries as deleted               |
  |  (SessionDataStore::mark_deleted)                       |
  +-------------------------------------------------------+
       |
       v
  +-------------------------------------------------------+
  |  Layer 2: CRYPTOGRAPHIC DESTRUCTION                    |
  |  Destroy session encryption key via HSM                 |
  |  (SoftwareHsm::destroy_key -> zero key bytes,           |
  |   mark KeyState::Destroyed)                              |
  |  Produces DestructionReceipt:                            |
  |    key_handle, destroyed_at, zeroed=true                 |
  +-------------------------------------------------------+
       |
       v
  +-------------------------------------------------------+
  |  Layer 3: STORAGE OVERWRITE                            |
  |  Overwrite all encrypted data regions with random bytes |
  |  (SessionDataStore::overwrite_with_random)               |
  +-------------------------------------------------------+
       |
       v
  +-------------------------------------------------------+
  |  Layer 4: MEMORY ZEROING                               |
  |  Zero all in-memory buffers for this session            |
  |  (SessionDataStore::zero_memory)                         |
  +-------------------------------------------------------+
       |
       v
  +-------------------------------------------------------+
  |  Layer 5: SEMANTIC DESTRUCTION                         |
  |  Destroy all metadata linkages:                         |
  |    - session-to-key mapping (session_keys HashMap)       |
  |    - session-to-data mapping (data_store entries)         |
  +-------------------------------------------------------+
       |
       v
  +-------------------------------------------------------+
  |  Layer 6: VERIFICATION                                 |
  |  Confirm each previous layer succeeded:                 |
  |    - Layers 1-5 reported success                         |
  |    - Key confirmed destroyed in HSM (is_key_active=false)|
  |    - Session data removed from store (has_session=false)  |
  |    - Session-key mapping removed                          |
  +-------------------------------------------------------+
       |
       v
  BurnReceipt {
      session_id,
      timestamp,
      layers_completed: [LayerResult x 6],
      key_handle,
      destruction_receipt: Some(DestructionReceipt),
      overall_success: true/false
  }
       |
       v
  Appended to Audit Log as BurnCompleted event
```

After burn completes:
- Encrypted data is irrecoverable (key destroyed, data overwritten, then zeroed)
- Session state removed from Gateway active_sessions
- ManifestEvaluator removed for this session
- Metrics updated (total_burned incremented, active_sessions decremented)
- Audit log is the only surviving record of what happened

## Sealed Event Flow

Sealed events are bounded temporal captures around safety-critical triggers.
They are declared exceptions to the ephemeral-by-default model.

```
  Safety Trigger Detected
  (e.g., CARLA collision sensor fires)
       |
       v
  +---------------------------+
  | Trigger evaluation        |
  | Match against manifest:   |
  | sealed_event category     |
  | trigger = safety_critical |
  +---------------------------+
       |
       v
  +---------------------------+
  | Capture Window            |
  | before: PT5S (5 seconds   |
  |   of rolling buffer)      |
  | after: PT2S (2 seconds    |
  |   of continued capture)   |
  +---------------------------+
       |
       v
  +---------------------------+
  | Captured data:            |
  | - Full sensor suite       |
  |   (camera, LiDAR, IMU,   |
  |    GNSS) within window    |
  | - Anonymised per manifest |
  |   (faces excluded, IDs    |
  |    stripped)               |
  +---------------------------+
       |
       v
  +---------------------------+
  | Encrypt under SEPARATE    |
  | key with longer TTL       |
  | (not drive session key)   |
  +---------------------------+
       |
       v
  +---------------------------+
  | Tag with metadata:        |
  | - trigger type            |
  | - timestamp               |
  | - retention: P12M         |
  | - purpose: model_retraining|
  +---------------------------+
       |
       v
  +---------------------------+
  | Audit: DataTransmitted    |
  | (sealed_event to ADAS     |
  |  supplier, anonymised     |
  |  granularity)             |
  +---------------------------+
       |
       v
  Sealed event survives drive session burn
  (retained as declared exception)
  Burned after retention period expires (P12M)
```

## Audit Log Chain Structure

Every audit event is HMAC-chained to its predecessor within the same session:

```
  +------------------+     +------------------+     +------------------+
  | Entry 1          |     | Entry 2          |     | Entry 3          |
  | SessionStart     |     | DataGenerated    |     | DataTransmitted  |
  |                  |     |                  |     |                  |
  | prev_hmac:       |     | prev_hmac:       |     | prev_hmac:       |
  |   "genesis"      |     |   hmac_1         |     |   hmac_2         |
  |                  |     |                  |     |                  |
  | hmac_1 =         |     | hmac_2 =         |     | hmac_3 =         |
  |  HMAC-SHA256(    |     |  HMAC-SHA256(    |     |  HMAC-SHA256(    |
  |   key,           |     |   key,           |     |   key,           |
  |   "genesis" +    |     |   hmac_1 +       |     |   hmac_2 +       |
  |   payload_1)     |     |   payload_2)     |     |   payload_3)     |
  +------------------+     +------------------+     +------------------+

  Verification: recompute each HMAC from its inputs.
  If any stored HMAC differs from the recomputed value,
  the chain is broken and tampering is detected.
```
