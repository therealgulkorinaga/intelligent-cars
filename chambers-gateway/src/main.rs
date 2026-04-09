use chambers_gateway::audit::AuditLog;
use chambers_gateway::core::{DataRecord, DataType, SessionId, StakeholderId};
use chambers_gateway::gateway::Gateway;
use chambers_gateway::manifest::demo_manifest;
use clap::{Parser, Subcommand};
use std::collections::HashMap;
use uuid::Uuid;

#[derive(Parser)]
#[command(
    name = "chambers",
    about = "Chambers Automotive Simulation Testbed - Vehicle Data Sovereignty Gateway",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the gateway server
    Gateway,

    /// Audit log operations
    Audit {
        #[command(subcommand)]
        action: AuditAction,
    },

    /// Run a self-contained demo
    Demo,
}

#[derive(Subcommand)]
enum AuditAction {
    /// Verify HMAC chain integrity for a session
    Verify {
        /// Session UUID
        session_id: String,
    },
    /// Show session summary
    Show {
        /// Session UUID
        session_id: String,
    },
    /// Export session audit log as JSON (for regulators)
    Export {
        /// Session UUID
        session_id: String,
    },
    /// Show human-readable driver summary
    DriverSummary {
        /// Session UUID
        session_id: String,
    },
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Gateway => run_gateway(),
        Commands::Audit { action } => run_audit(action),
        Commands::Demo => run_demo(),
    }
}

fn run_gateway() {
    println!("Chambers Gateway Server");
    println!("=======================");
    println!();
    println!("In a production deployment this would listen on a Unix socket");
    println!("for the simulator adapter. For now, use `chambers demo` to see");
    println!("the full session lifecycle.");
    println!();
    println!("Hint: run `chambers demo` for a self-contained demonstration.");
}

fn run_audit(action: AuditAction) {
    let db_path = "chambers_audit.db";
    let audit_log = match AuditLog::new(db_path) {
        Ok(log) => log,
        Err(e) => {
            eprintln!("Failed to open audit database at {}: {}", db_path, e);
            std::process::exit(1);
        }
    };

    match action {
        AuditAction::Verify { session_id } => {
            let sid = parse_session_id(&session_id);
            match audit_log.verify_chain(&sid) {
                Ok(true) => println!("HMAC chain integrity VERIFIED for session {}", session_id),
                Ok(false) => {
                    println!("HMAC chain integrity FAILED for session {}", session_id);
                    std::process::exit(1);
                }
                Err(e) => {
                    eprintln!("Error verifying chain: {}", e);
                    std::process::exit(1);
                }
            }
        }
        AuditAction::Show { session_id } => {
            let sid = parse_session_id(&session_id);
            match audit_log.get_session_summary(&sid) {
                Ok(summary) => {
                    println!("Session Summary: {}", summary.session_id);
                    println!("  Total events:          {}", summary.total_events);
                    println!("  Data generated:        {}", summary.data_generated);
                    println!("  Data transmitted:      {}", summary.data_transmitted);
                    println!("  Data blocked:          {}", summary.data_blocked);
                    println!("  Burns completed:       {}", summary.burns_completed);
                    println!("  Consent revocations:   {}", summary.consent_revocations);
                    println!("  Jurisdiction blocks:   {}", summary.jurisdiction_blocks);
                    println!("  Policy violations:     {}", summary.policy_violations);
                    println!(
                        "  Chain intact:          {}",
                        if summary.chain_intact { "YES" } else { "NO" }
                    );
                }
                Err(e) => {
                    eprintln!("Error: {}", e);
                    std::process::exit(1);
                }
            }
        }
        AuditAction::Export { session_id } => {
            let sid = parse_session_id(&session_id);
            match audit_log.export_session(&sid) {
                Ok(json) => println!("{}", json),
                Err(e) => {
                    eprintln!("Error: {}", e);
                    std::process::exit(1);
                }
            }
        }
        AuditAction::DriverSummary { session_id } => {
            let sid = parse_session_id(&session_id);
            match audit_log.get_driver_summary(&sid) {
                Ok(summary) => println!("{}", summary),
                Err(e) => {
                    eprintln!("Error: {}", e);
                    std::process::exit(1);
                }
            }
        }
    }
}

fn parse_session_id(s: &str) -> SessionId {
    match Uuid::parse_str(s) {
        Ok(u) => SessionId::from_uuid(u),
        Err(e) => {
            eprintln!("Invalid session UUID '{}': {}", s, e);
            std::process::exit(1);
        }
    }
}

fn run_demo() {
    println!("==========================================================");
    println!("  Chambers Automotive Simulation Testbed - Demo");
    println!("==========================================================");
    println!();

    // Create gateway with in-memory audit DB
    let gw = Gateway::new(":memory:").expect("Failed to create gateway");

    // Load demo manifest
    let manifest = demo_manifest("VIN-DEMO-2026-001");

    println!("[1] Starting session for vehicle VIN-DEMO-2026-001...");
    let session_id = gw
        .start_session("VIN-DEMO-2026-001", manifest)
        .expect("Failed to start session");
    println!("    Session ID: {}", session_id);
    println!();

    // -- Process various data records --

    // Speed record
    println!("[2] Processing speed record...");
    let mut fields = HashMap::new();
    fields.insert("avg_speed".into(), serde_json::json!(72.5));
    fields.insert("max_speed".into(), serde_json::json!(118.3));
    fields.insert("driver_id".into(), serde_json::json!("DRV-DEMO-001"));
    let record = DataRecord::new(session_id.clone(), "speed-sensor", DataType::Speed, fields);
    let result = gw.process_record(&session_id, &record).unwrap();
    println!(
        "    Transmitted to: {:?}",
        result
            .records_transmitted
            .iter()
            .map(|r| format!("{} ({})", r.stakeholder_id, r.granularity))
            .collect::<Vec<_>>()
    );
    if !result.records_blocked.is_empty() {
        println!(
            "    Blocked: {:?}",
            result
                .records_blocked
                .iter()
                .map(|r| format!("{}: {}", r.category, r.reason))
                .collect::<Vec<_>>()
        );
    }
    println!();

    // Position record
    println!("[3] Processing GPS position record...");
    let mut fields = HashMap::new();
    fields.insert("latitude".into(), serde_json::json!(48.8566));
    fields.insert("longitude".into(), serde_json::json!(2.3522));
    fields.insert("altitude".into(), serde_json::json!(35.0));
    fields.insert("driver_id".into(), serde_json::json!("DRV-DEMO-001"));
    let record = DataRecord::new(
        session_id.clone(),
        "gnss-receiver",
        DataType::Position,
        fields,
    );
    let result = gw.process_record(&session_id, &record).unwrap();
    println!(
        "    Transmitted to: {:?}",
        result
            .records_transmitted
            .iter()
            .map(|r| format!("{} ({})", r.stakeholder_id, r.granularity))
            .collect::<Vec<_>>()
    );
    println!();

    // Driving behaviour (insurer gets per-trip score)
    println!("[4] Processing driving behaviour record...");
    let mut fields = HashMap::new();
    fields.insert("harsh_braking".into(), serde_json::json!(2));
    fields.insert("rapid_acceleration".into(), serde_json::json!(1));
    fields.insert("cornering_g".into(), serde_json::json!(0.45));
    fields.insert("latitude".into(), serde_json::json!(48.8570));
    fields.insert("longitude".into(), serde_json::json!(2.3525));
    fields.insert("driver_id".into(), serde_json::json!("DRV-DEMO-001"));
    let record = DataRecord::new(
        session_id.clone(),
        "behaviour-analyser",
        DataType::DrivingBehaviour,
        fields,
    );
    let result = gw.process_record(&session_id, &record).unwrap();
    println!(
        "    Transmitted to: {:?}",
        result
            .records_transmitted
            .iter()
            .map(|r| format!("{} ({})", r.stakeholder_id, r.granularity))
            .collect::<Vec<_>>()
    );
    println!();

    // ADAS event
    println!("[5] Processing ADAS event...");
    let mut fields = HashMap::new();
    fields.insert("event_type".into(), serde_json::json!("AEB_triggered"));
    fields.insert("severity".into(), serde_json::json!("high"));
    fields.insert("speed_at_trigger".into(), serde_json::json!(45.2));
    fields.insert("driver_id".into(), serde_json::json!("DRV-DEMO-001"));
    fields.insert("vin".into(), serde_json::json!("VIN-DEMO-2026-001"));
    let record = DataRecord::new(session_id.clone(), "adas-ecu", DataType::AdasEvent, fields);
    let result = gw.process_record(&session_id, &record).unwrap();
    println!(
        "    Transmitted to: {:?}",
        result
            .records_transmitted
            .iter()
            .map(|r| format!("{} ({})", r.stakeholder_id, r.granularity))
            .collect::<Vec<_>>()
    );
    println!();

    // Undeclared data type — should be blocked
    println!("[6] Processing contact sync (undeclared by any stakeholder)...");
    let mut fields = HashMap::new();
    fields.insert("contact_name".into(), serde_json::json!("Alice"));
    fields.insert("phone".into(), serde_json::json!("+33 1 23 45 67 89"));
    let record = DataRecord::new(
        session_id.clone(),
        "phone-bridge",
        DataType::ContactSync,
        fields,
    );
    let result = gw.process_record(&session_id, &record).unwrap();
    println!(
        "    Blocked: {:?}",
        result
            .records_blocked
            .iter()
            .map(|r| format!("{}: {}", r.category, r.reason))
            .collect::<Vec<_>>()
    );
    println!();

    // Consent revocation
    println!("[7] Revoking insurer consent...");
    gw.revoke_consent(&session_id, &StakeholderId::new("insurer-api"))
        .unwrap();
    println!("    Insurer consent revoked.");
    println!();

    // Try sending speed again — insurer should no longer receive
    println!("[8] Processing another speed record (post-revocation)...");
    let mut fields = HashMap::new();
    fields.insert("avg_speed".into(), serde_json::json!(85.0));
    fields.insert("max_speed".into(), serde_json::json!(95.0));
    let record = DataRecord::new(session_id.clone(), "speed-sensor", DataType::Speed, fields);
    let result = gw.process_record(&session_id, &record).unwrap();
    let insurer_got_data = result
        .records_transmitted
        .iter()
        .any(|r| r.stakeholder_id == "insurer-api");
    println!(
        "    Insurer received data: {} (expected: false)",
        insurer_got_data
    );
    println!(
        "    Transmitted to: {:?}",
        result
            .records_transmitted
            .iter()
            .map(|r| format!("{} ({})", r.stakeholder_id, r.granularity))
            .collect::<Vec<_>>()
    );
    println!();

    // End session (triggers 6-layer burn)
    println!("[9] Ending session (triggering 6-layer burn)...");
    let receipt = gw.end_session(&session_id).unwrap();
    println!("    Burn success: {}", receipt.overall_success);
    for layer in &receipt.layers_completed {
        println!(
            "    Layer {}: {} - {} ({}ms)",
            layer.layer_number,
            layer.layer_name,
            if layer.success { "OK" } else { "FAIL" },
            layer.duration_ms,
        );
    }
    println!();

    // Show metrics
    let metrics = gw.metrics();
    println!("[10] Gateway Metrics:");
    println!("     Total processed:   {}", metrics.total_processed);
    println!("     Total transmitted:  {}", metrics.total_transmitted);
    println!("     Total blocked:      {}", metrics.total_blocked);
    println!("     Total burned:       {}", metrics.total_burned);
    println!("     Active sessions:    {}", metrics.active_sessions);
    println!();

    // Show audit summary
    println!("[11] Audit Summary:");
    let summary = gw.audit_log().get_session_summary(&session_id).unwrap();
    println!("     Total events:         {}", summary.total_events);
    println!("     Data generated:       {}", summary.data_generated);
    println!("     Data transmitted:     {}", summary.data_transmitted);
    println!("     Data blocked:         {}", summary.data_blocked);
    println!("     Burns completed:      {}", summary.burns_completed);
    println!("     Consent revocations:  {}", summary.consent_revocations);
    println!("     Chain intact:         {}", summary.chain_intact);
    println!();

    // Verify HMAC chain
    println!("[12] Verifying HMAC chain integrity...");
    let chain_ok = gw.audit_log().verify_chain(&session_id).unwrap();
    println!(
        "     Chain integrity: {}",
        if chain_ok { "VERIFIED" } else { "BROKEN" }
    );
    println!();

    // Driver summary
    println!("[13] Driver Summary (human-readable):");
    println!("-------------------------------------");
    let driver_summary = gw.audit_log().get_driver_summary(&session_id).unwrap();
    println!("{}", driver_summary);
    println!();

    println!("==========================================================");
    println!("  Demo complete. All data has been irrecoverably burned.");
    println!("==========================================================");
}
