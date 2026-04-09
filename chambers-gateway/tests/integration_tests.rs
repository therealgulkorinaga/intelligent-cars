use chambers_gateway::audit::{AuditLog, EventType};
use chambers_gateway::burn::{BurnEngine, SessionDataStore};
use chambers_gateway::core::{
    DataRecord, DataType, Granularity, Jurisdiction, SessionId, StakeholderId,
};
use chambers_gateway::gateway::Gateway;
use chambers_gateway::hsm::SoftwareHsm;
use chambers_gateway::manifest::{
    demo_manifest, CategoryDeclaration, ManifestEvaluator, PreservationManifest,
    StakeholderDeclaration,
};
use serde_json::json;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

// ── Helpers ────────────────────────────────────────────────────────────

fn make_speed_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("avg_speed".into(), json!(72.5));
    fields.insert("max_speed".into(), json!(118.3));
    fields.insert("driver_id".into(), json!("DRV-001"));
    DataRecord::new(session_id, "speed-sensor", DataType::Speed, fields)
}

fn make_position_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("latitude".into(), json!(48.8566));
    fields.insert("longitude".into(), json!(2.3522));
    fields.insert("altitude".into(), json!(35.0));
    fields.insert("driver_id".into(), json!("DRV-001"));
    DataRecord::new(session_id, "gnss-receiver", DataType::Position, fields)
}

fn make_driving_behaviour_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("harsh_braking".into(), json!(2));
    fields.insert("rapid_acceleration".into(), json!(1));
    fields.insert("cornering_g".into(), json!(0.45));
    fields.insert("latitude".into(), json!(48.857));
    fields.insert("longitude".into(), json!(2.353));
    fields.insert("driver_id".into(), json!("DRV-001"));
    DataRecord::new(
        session_id,
        "behaviour-analyser",
        DataType::DrivingBehaviour,
        fields,
    )
}

fn make_adas_event_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("event_type".into(), json!("AEB_triggered"));
    fields.insert("severity".into(), json!("high"));
    fields.insert("speed_at_trigger".into(), json!(45.2));
    fields.insert("driver_id".into(), json!("DRV-001"));
    fields.insert("vin".into(), json!("VIN-TEST-001"));
    DataRecord::new(session_id, "adas-ecu", DataType::AdasEvent, fields)
}

fn make_contact_sync_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("contact_name".into(), json!("Alice"));
    fields.insert("phone".into(), json!("+33 1 23 45 67 89"));
    DataRecord::new(session_id, "phone-bridge", DataType::ContactSync, fields)
}

fn make_v2x_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("message_type".into(), json!("CAM"));
    fields.insert("station_id".into(), json!(42));
    fields.insert("latitude".into(), json!(48.856));
    fields.insert("longitude".into(), json!(2.352));
    fields.insert("vin".into(), json!("VIN-TEST-001"));
    fields.insert("driver_id".into(), json!("DRV-001"));
    fields.insert("license_plate".into(), json!("AB-123-CD"));
    DataRecord::new(session_id, "v2x-obu", DataType::V2xCam, fields)
}

fn make_diagnostic_record(session_id: SessionId) -> DataRecord {
    let mut fields = HashMap::new();
    fields.insert("code".into(), json!("P0301"));
    fields.insert("description".into(), json!("Cylinder 1 Misfire Detected"));
    fields.insert("severity".into(), json!("warning"));
    DataRecord::new(session_id, "obd-reader", DataType::DiagnosticCode, fields)
}

// ── Test: Full session lifecycle ───────────────────────────────────────

#[test]
fn test_session_lifecycle() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-TEST-001");

    // Start session
    let sid = gw.start_session("VIN-TEST-001", manifest).unwrap();
    assert!(gw.is_session_active(&sid));

    // Process several records
    let speed = make_speed_record(sid.clone());
    let result = gw.process_record(&sid, &speed).unwrap();
    assert!(!result.records_transmitted.is_empty(), "Speed should be transmitted to at least OEM");

    let position = make_position_record(sid.clone());
    let result = gw.process_record(&sid, &position).unwrap();
    assert!(
        result.records_transmitted.iter().any(|r| r.stakeholder_id == "oem-cloud"),
        "OEM should receive position data"
    );

    let behaviour = make_driving_behaviour_record(sid.clone());
    let result = gw.process_record(&sid, &behaviour).unwrap();
    assert!(
        result.records_transmitted.iter().any(|r| r.stakeholder_id == "insurer-api"),
        "Insurer should receive driving behaviour"
    );

    // End session (triggers burn)
    let state = gw.get_session_state(&sid).unwrap();
    let key_handle = state.key_handle;

    let receipt = gw.end_session(&sid).unwrap();
    assert!(receipt.overall_success, "Burn should succeed");
    assert_eq!(receipt.layers_completed.len(), 6, "All 6 layers should execute");
    assert!(!gw.is_session_active(&sid), "Session should be gone after burn");

    // Verify key is destroyed — cannot encrypt any more
    let hsm = gw.hsm();
    assert!(
        hsm.encrypt(key_handle, b"test").is_err(),
        "Key should be destroyed after burn"
    );

    // Verify audit chain
    assert!(
        gw.audit_log().verify_chain(&sid).unwrap(),
        "Audit HMAC chain should be intact"
    );

    // Verify summary
    let summary = gw.audit_log().get_session_summary(&sid).unwrap();
    assert!(summary.total_events > 0);
    assert!(summary.data_generated > 0);
    assert!(summary.data_transmitted > 0);
    assert_eq!(summary.burns_completed, 1);
    assert!(summary.chain_intact);
}

// ── Test: Manifest evaluation (OEM, insurer, ADAS) ─────────────────────

#[test]
fn test_manifest_evaluation() {
    let manifest = demo_manifest("VIN-TEST-002");
    let evaluator = ManifestEvaluator::new(manifest);

    let sid = SessionId::new();

    // -- OEM gets anonymised position data --
    let position = make_position_record(sid.clone());
    let results = evaluator.evaluate(&position).unwrap();
    let oem = results.iter().find(|(id, _)| id.0 == "oem-cloud");
    assert!(oem.is_some(), "OEM should receive position data");
    let (_, filtered) = oem.unwrap();
    assert_eq!(filtered.granularity, Granularity::Anonymised);
    // driver_id should be stripped
    assert!(
        !filtered.fields.contains_key("driver_id"),
        "OEM should not see driver_id in anonymised data"
    );
    // GPS should be coarsened (rounded to 2 decimal places)
    if let Some(lat) = filtered.fields.get("latitude") {
        let lat_str = lat.to_string();
        // 48.8566 should become 48.86 (rounded to nearest 0.01)
        assert!(
            lat_str.contains("48.86"),
            "Latitude should be coarsened, got: {}",
            lat_str
        );
    }

    // -- Insurer gets per-trip score for driving behaviour, without GPS --
    let behaviour = make_driving_behaviour_record(sid.clone());
    let results = evaluator.evaluate(&behaviour).unwrap();
    let insurer = results.iter().find(|(id, _)| id.0 == "insurer-api");
    assert!(insurer.is_some(), "Insurer should receive driving behaviour");
    let (_, filtered) = insurer.unwrap();
    assert_eq!(filtered.granularity, Granularity::PerTripScore);
    // Per-trip score collapses everything to a single score
    assert!(
        filtered.fields.contains_key("trip_score"),
        "Insurer should get a trip_score field"
    );
    // Should NOT have GPS
    assert!(
        !filtered.fields.contains_key("latitude"),
        "Insurer should not see latitude"
    );
    assert!(
        !filtered.fields.contains_key("longitude"),
        "Insurer should not see longitude"
    );

    // -- ADAS supplier gets raw events, minus driver_id and vin --
    let adas_event = make_adas_event_record(sid.clone());
    let results = evaluator.evaluate(&adas_event).unwrap();
    let adas = results.iter().find(|(id, _)| id.0 == "adas-supplier");
    assert!(adas.is_some(), "ADAS supplier should receive ADAS events");
    let (_, filtered) = adas.unwrap();
    assert_eq!(filtered.granularity, Granularity::Raw);
    assert!(
        !filtered.fields.contains_key("driver_id"),
        "ADAS supplier should not see driver_id"
    );
    assert!(
        !filtered.fields.contains_key("vin"),
        "ADAS supplier should not see VIN"
    );
    assert!(
        filtered.fields.contains_key("event_type"),
        "ADAS supplier should see event_type"
    );
    assert!(
        filtered.fields.contains_key("severity"),
        "ADAS supplier should see severity"
    );

    // -- Insurer should NOT receive ADAS events (not declared) --
    let insurer_adas = results.iter().find(|(id, _)| id.0 == "insurer-api");
    assert!(
        insurer_adas.is_none(),
        "Insurer should not receive ADAS events"
    );
}

// ── Test: Burn is irrecoverable ────────────────────────────────────────

#[test]
fn test_burn_irrecoverable() {
    let hsm = SoftwareHsm::new();
    let (handle, _) = hsm.generate_key().unwrap();

    // Encrypt some data
    let plaintext = b"sensitive telemetry data with GPS coordinates";
    let ciphertext = hsm.encrypt(handle, plaintext).unwrap();

    // Verify we can decrypt before burn
    let decrypted = hsm.decrypt(handle, &ciphertext).unwrap();
    assert_eq!(decrypted, plaintext);

    // Execute burn via burn engine
    let store = Arc::new(Mutex::new(SessionDataStore::new()));
    let sid = SessionId::new();
    {
        let mut s = store.lock().unwrap();
        s.store(&sid, ciphertext.data.clone());
    }

    let engine = BurnEngine::new(hsm.clone(), store.clone());
    engine.register_session(&sid, handle);
    let receipt = engine.burn_session(&sid, handle);

    assert!(receipt.overall_success, "Burn should succeed");
    assert!(
        receipt.destruction_receipt.is_some(),
        "Should have destruction receipt"
    );

    // After burn: decrypt MUST fail
    let decrypt_result = hsm.decrypt(handle, &ciphertext);
    assert!(
        decrypt_result.is_err(),
        "Decryption must fail after key is burned"
    );

    // After burn: encrypt MUST fail
    let encrypt_result = hsm.encrypt(handle, b"new data");
    assert!(
        encrypt_result.is_err(),
        "Encryption must fail after key is burned"
    );

    // Data store should be empty
    let store_guard = store.lock().unwrap();
    assert!(
        !store_guard.has_session(&sid),
        "Data store should not have session after burn"
    );
}

// ── Test: Audit chain integrity and tampering detection ────────────────

#[test]
fn test_audit_chain_integrity() {
    let audit = AuditLog::new(":memory:").unwrap();
    let sid = SessionId::new();

    // Log several events
    audit
        .log_event(&sid, EventType::SessionStart, &json!({"vehicle": "V1"}))
        .unwrap();
    audit
        .log_event(
            &sid,
            EventType::DataGenerated,
            &json!({"type": "speed", "value": 72.5}),
        )
        .unwrap();
    audit
        .log_event(
            &sid,
            EventType::DataTransmitted,
            &json!({"to": "oem-cloud"}),
        )
        .unwrap();
    audit
        .log_event(&sid, EventType::BurnCompleted, &json!({"success": true}))
        .unwrap();
    audit
        .log_event(&sid, EventType::SessionEnd, &json!({}))
        .unwrap();

    // Chain should be valid
    assert!(audit.verify_chain(&sid).unwrap(), "Chain should be intact");

    // Tamper with an entry
    let entries = audit.get_entries(&sid).unwrap();
    assert!(entries.len() >= 3);
    audit
        .tamper_entry(entries[1].id, r#"{"type":"speed","value":999.9}"#)
        .unwrap();

    // Chain should now be broken
    assert!(
        !audit.verify_chain(&sid).unwrap(),
        "Chain should be broken after tampering"
    );
}

// ── Test: Jurisdiction blocking ────────────────────────────────────────

#[test]
fn test_jurisdiction_block() {
    // Create a manifest where one stakeholder has a US endpoint
    // but only declares EU jurisdiction
    let manifest = PreservationManifest {
        manifest_version: "1.0".to_string(),
        vehicle_id: "VIN-JURIS-001".to_string(),
        session_id: None,
        stakeholders: vec![
            StakeholderDeclaration {
                id: "eu-only-partner".to_string(),
                role: "Partner".to_string(),
                legal_basis: chambers_gateway::core::LegalBasis::ExplicitConsent,
                endpoint_jurisdiction: Jurisdiction::US, // endpoint is in US
                categories: vec![CategoryDeclaration {
                    data_type: DataType::Speed,
                    fields: None,
                    excluded_fields: None,
                    granularity: Granularity::Raw,
                    retention: "P90D".to_string(),
                    purpose: "Analytics".to_string(),
                    jurisdiction: vec![Jurisdiction::EU], // but only declared EU!
                }],
            },
            StakeholderDeclaration {
                id: "eu-partner".to_string(),
                role: "Partner".to_string(),
                legal_basis: chambers_gateway::core::LegalBasis::LegitimateInterest,
                endpoint_jurisdiction: Jurisdiction::EU, // endpoint is in EU
                categories: vec![CategoryDeclaration {
                    data_type: DataType::Speed,
                    fields: None,
                    excluded_fields: None,
                    granularity: Granularity::Raw,
                    retention: "P90D".to_string(),
                    purpose: "Analytics".to_string(),
                    jurisdiction: vec![Jurisdiction::EU], // declared EU
                }],
            },
        ],
        mandatory_retention: vec![],
    };

    let evaluator = ManifestEvaluator::new(manifest);
    let sid = SessionId::new();
    let record = make_speed_record(sid);

    let results = evaluator.evaluate(&record).unwrap();

    // US-endpoint stakeholder should be blocked (jurisdiction mismatch)
    let us_partner = results.iter().find(|(id, _)| id.0 == "eu-only-partner");
    assert!(
        us_partner.is_none(),
        "US-endpoint partner should be jurisdiction-blocked"
    );

    // EU-endpoint stakeholder should receive data
    let eu_partner = results.iter().find(|(id, _)| id.0 == "eu-partner");
    assert!(
        eu_partner.is_some(),
        "EU-endpoint partner should receive data"
    );
}

// ── Test: Consent revocation ───────────────────────────────────────────

#[test]
fn test_consent_revocation() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-TEST-003");
    let sid = gw.start_session("VIN-TEST-003", manifest).unwrap();

    // Before revocation: insurer receives driving behaviour
    let behaviour = make_driving_behaviour_record(sid.clone());
    let result = gw.process_record(&sid, &behaviour).unwrap();
    assert!(
        result
            .records_transmitted
            .iter()
            .any(|r| r.stakeholder_id == "insurer-api"),
        "Insurer should get data before revocation"
    );

    // Revoke insurer consent
    gw.revoke_consent(&sid, &StakeholderId::new("insurer-api"))
        .unwrap();

    // After revocation: insurer should NOT receive any data
    let behaviour2 = make_driving_behaviour_record(sid.clone());
    let result = gw.process_record(&sid, &behaviour2).unwrap();
    assert!(
        !result
            .records_transmitted
            .iter()
            .any(|r| r.stakeholder_id == "insurer-api"),
        "Insurer should NOT get data after revocation"
    );

    // Also check speed (insurer also declared speed)
    let speed = make_speed_record(sid.clone());
    let result = gw.process_record(&sid, &speed).unwrap();
    assert!(
        !result
            .records_transmitted
            .iter()
            .any(|r| r.stakeholder_id == "insurer-api"),
        "Insurer should NOT get speed data after revocation"
    );

    // OEM should still work
    assert!(
        result
            .records_transmitted
            .iter()
            .any(|r| r.stakeholder_id == "oem-cloud"),
        "OEM should still receive data"
    );

    // Verify audit has ConsentRevoked event
    let summary = gw.audit_log().get_session_summary(&sid).unwrap();
    assert_eq!(
        summary.consent_revocations, 1,
        "Should have 1 consent revocation"
    );

    gw.end_session(&sid).unwrap();
}

// ── Test: Undeclared stakeholder blocked ───────────────────────────────

#[test]
fn test_undeclared_stakeholder_blocked() {
    let manifest = demo_manifest("VIN-TEST-004");
    let evaluator = ManifestEvaluator::new(manifest);
    let sid = SessionId::new();

    // Contact sync is NOT declared by any stakeholder in the demo manifest
    let contact = make_contact_sync_record(sid.clone());
    let results = evaluator.evaluate(&contact).unwrap();
    assert!(
        results.is_empty(),
        "No stakeholder declared ContactSync — should get nothing"
    );

    // Full gateway test: should be blocked
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-TEST-004");
    let sid = gw.start_session("VIN-TEST-004", manifest).unwrap();

    let contact = make_contact_sync_record(sid.clone());
    let result = gw.process_record(&sid, &contact).unwrap();
    assert!(
        result.records_transmitted.is_empty(),
        "No stakeholder should receive undeclared data"
    );
    assert!(
        !result.records_blocked.is_empty(),
        "Undeclared data should be blocked"
    );

    gw.end_session(&sid).unwrap();
}

// ── Test: HSM fallback mode ────────────────────────────────────────────

#[test]
fn test_hsm_fallback() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-TEST-005");
    let sid = gw.start_session("VIN-TEST-005", manifest).unwrap();

    // Before fallback: data flows normally
    let speed = make_speed_record(sid.clone());
    let result = gw.process_record(&sid, &speed).unwrap();
    assert!(
        !result.records_transmitted.is_empty(),
        "Should transmit before fallback"
    );

    // Enter fallback mode
    gw.enter_fallback_mode(&sid).unwrap();

    // After fallback: ALL telemetry blocked
    let speed2 = make_speed_record(sid.clone());
    let result = gw.process_record(&sid, &speed2).unwrap();
    assert!(
        result.records_transmitted.is_empty(),
        "No data should be transmitted in fallback mode"
    );
    assert!(
        !result.records_blocked.is_empty(),
        "Data should be blocked in fallback mode"
    );
    assert!(
        result.records_blocked[0]
            .reason
            .contains("fallback"),
        "Block reason should mention fallback"
    );

    // Different data types also blocked
    let position = make_position_record(sid.clone());
    let result = gw.process_record(&sid, &position).unwrap();
    assert!(
        result.records_transmitted.is_empty(),
        "Position should also be blocked in fallback"
    );

    // Verify HsmFallback event in audit log
    let summary = gw.audit_log().get_session_summary(&sid).unwrap();
    assert!(summary.total_events > 0);

    gw.end_session(&sid).unwrap();
}

// ── Test: Audit driver summary ─────────────────────────────────────────

#[test]
fn test_audit_driver_summary() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-TEST-006");
    let sid = gw.start_session("VIN-TEST-006", manifest).unwrap();

    // Process various records
    let speed = make_speed_record(sid.clone());
    gw.process_record(&sid, &speed).unwrap();

    let position = make_position_record(sid.clone());
    gw.process_record(&sid, &position).unwrap();

    // Block something
    let contact = make_contact_sync_record(sid.clone());
    gw.process_record(&sid, &contact).unwrap();

    // End session
    gw.end_session(&sid).unwrap();

    // Get driver summary
    let summary = gw.audit_log().get_driver_summary(&sid).unwrap();

    // Verify human-readable content
    assert!(
        summary.contains("Your Driving Session Summary"),
        "Should have a title"
    );
    assert!(
        summary.contains("data points"),
        "Should mention data points"
    );
    assert!(
        summary.contains("permanently and irrecoverably destroyed"),
        "Should explain burn in plain language"
    );
    assert!(
        summary.contains("integrity") || summary.contains("verified"),
        "Should mention verification"
    );

    // Should NOT contain technical jargon
    assert!(
        !summary.contains("HMAC"),
        "Driver summary should not contain HMAC jargon"
    );
    assert!(
        !summary.contains("AES"),
        "Driver summary should not contain AES jargon"
    );
    assert!(
        !summary.contains("SHA256"),
        "Driver summary should not contain SHA256 jargon"
    );
}

// ── Test: Concurrent sessions ──────────────────────────────────────────

#[test]
fn test_concurrent_sessions() {
    let gw = Gateway::new(":memory:").unwrap();

    // Start multiple sessions
    let m1 = demo_manifest("VIN-CONCURRENT-001");
    let m2 = demo_manifest("VIN-CONCURRENT-002");
    let m3 = demo_manifest("VIN-CONCURRENT-003");

    let sid1 = gw.start_session("VIN-CONCURRENT-001", m1).unwrap();
    let sid2 = gw.start_session("VIN-CONCURRENT-002", m2).unwrap();
    let sid3 = gw.start_session("VIN-CONCURRENT-003", m3).unwrap();

    assert!(gw.is_session_active(&sid1));
    assert!(gw.is_session_active(&sid2));
    assert!(gw.is_session_active(&sid3));

    // Process records on each session
    let speed1 = make_speed_record(sid1.clone());
    let speed2 = make_speed_record(sid2.clone());
    let speed3 = make_speed_record(sid3.clone());

    let r1 = gw.process_record(&sid1, &speed1).unwrap();
    let r2 = gw.process_record(&sid2, &speed2).unwrap();
    let r3 = gw.process_record(&sid3, &speed3).unwrap();

    assert!(!r1.records_transmitted.is_empty());
    assert!(!r2.records_transmitted.is_empty());
    assert!(!r3.records_transmitted.is_empty());

    // End sessions in different order
    let receipt2 = gw.end_session(&sid2).unwrap();
    assert!(receipt2.overall_success);
    assert!(!gw.is_session_active(&sid2));
    assert!(gw.is_session_active(&sid1));
    assert!(gw.is_session_active(&sid3));

    let receipt1 = gw.end_session(&sid1).unwrap();
    assert!(receipt1.overall_success);

    let receipt3 = gw.end_session(&sid3).unwrap();
    assert!(receipt3.overall_success);

    // All sessions ended
    assert!(!gw.is_session_active(&sid1));
    assert!(!gw.is_session_active(&sid2));
    assert!(!gw.is_session_active(&sid3));

    // Each session's audit chain should be independently valid
    assert!(gw.audit_log().verify_chain(&sid1).unwrap());
    assert!(gw.audit_log().verify_chain(&sid2).unwrap());
    assert!(gw.audit_log().verify_chain(&sid3).unwrap());

    // Metrics
    let metrics = gw.metrics();
    assert_eq!(metrics.total_burned, 3);
    assert_eq!(metrics.active_sessions, 0);
    assert_eq!(metrics.total_processed, 3); // one record per session
}

// ── Test: V2X CAM anonymisation ────────────────────────────────────────

#[test]
fn test_v2x_anonymisation() {
    let manifest = demo_manifest("VIN-TEST-V2X");
    let evaluator = ManifestEvaluator::new(manifest);
    let sid = SessionId::new();

    let v2x = make_v2x_record(sid);
    let results = evaluator.evaluate(&v2x).unwrap();

    let city = results.iter().find(|(id, _)| id.0 == "city-transport");
    assert!(city.is_some(), "City transport should receive V2X data");

    let (_, filtered) = city.unwrap();
    assert_eq!(filtered.granularity, Granularity::Anonymised);

    // Identity fields should be stripped
    assert!(
        !filtered.fields.contains_key("vin"),
        "VIN should be stripped"
    );
    assert!(
        !filtered.fields.contains_key("driver_id"),
        "driver_id should be stripped"
    );
    assert!(
        !filtered.fields.contains_key("license_plate"),
        "license_plate should be stripped"
    );
}

// ── Test: Diagnostic codes to OEM ──────────────────────────────────────

#[test]
fn test_diagnostic_codes_oem_raw() {
    let manifest = demo_manifest("VIN-DIAG-001");
    let evaluator = ManifestEvaluator::new(manifest);
    let sid = SessionId::new();

    let diag = make_diagnostic_record(sid);
    let results = evaluator.evaluate(&diag).unwrap();

    let oem = results.iter().find(|(id, _)| id.0 == "oem-cloud");
    assert!(oem.is_some(), "OEM should receive diagnostic codes");

    let (_, filtered) = oem.unwrap();
    assert_eq!(
        filtered.granularity,
        Granularity::Raw,
        "OEM gets raw diagnostic codes"
    );
    assert!(filtered.fields.contains_key("code"));
    assert!(filtered.fields.contains_key("description"));
}

// ── Test: Insurer field restriction on speed ───────────────────────────

#[test]
fn test_insurer_speed_field_restriction() {
    let manifest = demo_manifest("VIN-FIELD-001");
    let evaluator = ManifestEvaluator::new(manifest);
    let sid = SessionId::new();

    // Speed record with extra fields beyond what insurer declared
    let mut fields = HashMap::new();
    fields.insert("avg_speed".into(), json!(65.0));
    fields.insert("max_speed".into(), json!(110.0));
    fields.insert("min_speed".into(), json!(20.0));
    fields.insert("std_dev".into(), json!(15.2));
    fields.insert("driver_id".into(), json!("DRV-001"));
    let record = DataRecord::new(sid, "speed-sensor", DataType::Speed, fields);

    let results = evaluator.evaluate(&record).unwrap();
    let insurer = results.iter().find(|(id, _)| id.0 == "insurer-api");
    assert!(insurer.is_some());

    let (_, filtered) = insurer.unwrap();
    // Insurer declared fields: ["avg_speed", "max_speed"] only
    // After field filtering and Aggregated granularity:
    assert!(
        filtered.fields.contains_key("avg_speed"),
        "Should have avg_speed"
    );
    assert!(
        filtered.fields.contains_key("max_speed"),
        "Should have max_speed"
    );
    assert!(
        !filtered.fields.contains_key("min_speed"),
        "Should NOT have min_speed (not declared)"
    );
    assert!(
        !filtered.fields.contains_key("std_dev"),
        "Should NOT have std_dev (not declared)"
    );
    assert!(
        !filtered.fields.contains_key("driver_id"),
        "Should NOT have driver_id (not declared)"
    );
}

// ── Test: Audit export is valid JSON ───────────────────────────────────

#[test]
fn test_audit_export_json() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-EXPORT-001");
    let sid = gw.start_session("VIN-EXPORT-001", manifest).unwrap();

    let speed = make_speed_record(sid.clone());
    gw.process_record(&sid, &speed).unwrap();
    gw.end_session(&sid).unwrap();

    let export = gw.audit_log().export_session(&sid).unwrap();

    // Parse as JSON — should not fail
    let parsed: serde_json::Value = serde_json::from_str(&export)
        .expect("Audit export should be valid JSON");

    assert!(parsed.get("session_id").is_some());
    assert!(parsed.get("entry_count").is_some());
    assert!(parsed.get("chain_verified").is_some());
    assert!(parsed.get("entries").is_some());

    let entries = parsed.get("entries").unwrap().as_array().unwrap();
    assert!(!entries.is_empty(), "Should have audit entries");

    // Verify each entry has the required fields
    for entry in entries {
        assert!(entry.get("id").is_some());
        assert!(entry.get("session_id").is_some());
        assert!(entry.get("timestamp").is_some());
        assert!(entry.get("event_type").is_some());
        assert!(entry.get("hmac").is_some());
        assert!(entry.get("previous_hmac").is_some());
    }
}

// ── Test: Multiple data types in one session ───────────────────────────

#[test]
fn test_mixed_data_types_session() {
    let gw = Gateway::new(":memory:").unwrap();
    let manifest = demo_manifest("VIN-MIX-001");
    let sid = gw.start_session("VIN-MIX-001", manifest).unwrap();

    // Process speed (OEM + insurer)
    let speed = make_speed_record(sid.clone());
    let r = gw.process_record(&sid, &speed).unwrap();
    let speed_stakeholders: Vec<_> = r
        .records_transmitted
        .iter()
        .map(|t| t.stakeholder_id.as_str())
        .collect();
    assert!(speed_stakeholders.contains(&"oem-cloud"));
    assert!(speed_stakeholders.contains(&"insurer-api"));

    // Process position (OEM only)
    let pos = make_position_record(sid.clone());
    let r = gw.process_record(&sid, &pos).unwrap();
    let pos_stakeholders: Vec<_> = r
        .records_transmitted
        .iter()
        .map(|t| t.stakeholder_id.as_str())
        .collect();
    assert!(pos_stakeholders.contains(&"oem-cloud"));
    assert!(!pos_stakeholders.contains(&"insurer-api"));

    // Process ADAS event (adas-supplier only)
    let adas = make_adas_event_record(sid.clone());
    let r = gw.process_record(&sid, &adas).unwrap();
    let adas_stakeholders: Vec<_> = r
        .records_transmitted
        .iter()
        .map(|t| t.stakeholder_id.as_str())
        .collect();
    assert!(adas_stakeholders.contains(&"adas-supplier"));
    assert!(!adas_stakeholders.contains(&"oem-cloud"));
    assert!(!adas_stakeholders.contains(&"insurer-api"));

    // Process V2X (city-transport only)
    let v2x = make_v2x_record(sid.clone());
    let r = gw.process_record(&sid, &v2x).unwrap();
    let v2x_stakeholders: Vec<_> = r
        .records_transmitted
        .iter()
        .map(|t| t.stakeholder_id.as_str())
        .collect();
    assert!(v2x_stakeholders.contains(&"city-transport"));

    // Process contact sync (blocked - no one declared it)
    let contact = make_contact_sync_record(sid.clone());
    let r = gw.process_record(&sid, &contact).unwrap();
    assert!(r.records_transmitted.is_empty());
    assert!(!r.records_blocked.is_empty());

    // Verify metrics
    let metrics = gw.metrics();
    assert_eq!(metrics.total_processed, 5);

    gw.end_session(&sid).unwrap();
}

// ── Test: HSM encrypt/decrypt independent of gateway ───────────────────

#[test]
fn test_hsm_multiple_keys() {
    let hsm = SoftwareHsm::new();

    let (k1, _) = hsm.generate_key().unwrap();
    let (k2, _) = hsm.generate_key().unwrap();

    let ct1 = hsm.encrypt(k1, b"data for key 1").unwrap();
    let ct2 = hsm.encrypt(k2, b"data for key 2").unwrap();

    // Each key decrypts only its own data
    let d1 = hsm.decrypt(k1, &ct1).unwrap();
    assert_eq!(d1, b"data for key 1");

    let d2 = hsm.decrypt(k2, &ct2).unwrap();
    assert_eq!(d2, b"data for key 2");

    // Cross-key decryption fails
    assert!(
        hsm.decrypt(k1, &ct2).is_err(),
        "Key 1 should not decrypt key 2's data"
    );
    assert!(
        hsm.decrypt(k2, &ct1).is_err(),
        "Key 2 should not decrypt key 1's data"
    );

    // Destroy key 1, key 2 still works
    hsm.destroy_key(k1).unwrap();
    assert!(hsm.decrypt(k1, &ct1).is_err());
    let d2_again = hsm.decrypt(k2, &ct2).unwrap();
    assert_eq!(d2_again, b"data for key 2");
}
