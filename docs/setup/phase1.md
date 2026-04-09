# Phase 1 Setup: SUMO + Telemetry-Level Chambers

Phase 1 validates the preservation manifest and burn semantics on vehicle
telemetry streams at fleet scale using SUMO (Simulation of Urban Mobility).

## Goals

- 100-vehicle SUMO scenario with per-vehicle Chambers sessions
- Manifest-driven data routing to 4 stakeholder endpoints
- Burn engine destroys session keys within 1 second of park event
- Audit log captures 100% of data flow decisions
- Data residue comparison: >90% reduction vs. no-Chambers baseline
- Threats T1-T4 (cellular channel) simulated and mitigated

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Rust | 1.78+ | chambers-gateway |
| Python | 3.11+ | chambers-sim, mock-stakeholders |
| SUMO | 1.20+ | Traffic simulation |

## Step 1: Install SUMO

### macOS (Homebrew)

```bash
brew install sumo
```

After installation, set the SUMO_HOME environment variable:

```bash
export SUMO_HOME=$(brew --prefix sumo)/share/sumo
```

Add this to your shell profile (`~/.zshrc` or `~/.bashrc`) for persistence.

### Ubuntu / Debian

```bash
sudo add-apt-repository ppa:sumo/stable
sudo apt-get update
sudo apt-get install sumo sumo-tools sumo-doc
```

Set SUMO_HOME:

```bash
export SUMO_HOME=/usr/share/sumo
```

### Verify Installation

```bash
sumo --version
netgenerate --version
```

Both should report SUMO 1.20.0 or later.

## Step 2: Generate Network and Routes

The testbed includes a network generator that produces a 10x10 urban grid.

### Generate the Road Network

```bash
cd scenarios/sumo
python generate_network.py
```

This creates:

- `urban_grid.net.xml` -- 10x10 intersection grid with traffic lights
- `tls_timing.add.xml` -- traffic light timing reference (30s green phases)

Network parameters:

| Parameter | Value |
|-----------|-------|
| Grid size | 10x10 intersections (100 junctions) |
| Block length | 100m |
| Lanes per direction | 2 |
| Speed limit | 50 km/h (13.89 m/s) |
| Junction type | Traffic light |

Options:

```bash
python generate_network.py --grid-x 20 --grid-y 20 --block-length 150
```

### Generate Vehicle Routes

Generate routes for 100 vehicles with varied commute patterns:

```bash
cd scenarios/sumo
python generate_routes.py  # if available
```

If the route generator is not yet implemented, you can use SUMO's built-in
random trip generator:

```bash
python $SUMO_HOME/tools/randomTrips.py \
    -n urban_grid.net.xml \
    -o urban_trips.xml \
    -r urban_routes.xml \
    --period 1.0 \
    --begin 0 \
    --end 3600 \
    -l
```

This generates ~3600 vehicle departures over a 1-hour simulation.

## Step 3: Configure the Preservation Manifest

The manifest defines which stakeholders receive which data categories.
Use one of the provided manifests or create a custom one.

### Demo Manifest (4 Stakeholders)

The demo manifest at `manifests/demo_manifest.json` configures:

| Stakeholder | Role | Data Type | Granularity | Jurisdiction |
|-------------|------|-----------|-------------|--------------|
| oem_volkswagen | OEM | sensor_health | anonymised_aggregate | EU |
| insurer_allianz | Insurer | driving_behaviour | per_trip_score | EU |
| adas_supplier_mobileye | ADAS Supplier | sealed_event | anonymised | EU |
| tier1_bosch | Tier-1 | component_telemetry, diagnostic_code | pseudonymised, raw | EU, EEA |

### Custom Manifest

Create a custom manifest validated against `manifests/schema.json`:

```bash
# Validate a manifest against the schema (requires jsonschema)
pip install jsonschema
python -c "
import json, jsonschema
schema = json.load(open('manifests/schema.json'))
manifest = json.load(open('manifests/demo_manifest.json'))
jsonschema.validate(manifest, schema)
print('Valid')
"
```

## Step 4: Start Mock Stakeholder Endpoints

In a dedicated terminal:

```bash
cd mock-stakeholders
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Verify the endpoints are running:

```bash
curl http://localhost:8000/admin/stats
```

Expected response:

```json
{"oem": 0, "insurer": 0, "adas": 0, "tier1": 0, "broker": 0, "foreign": 0}
```

## Step 5: Build the Chambers Gateway

In a dedicated terminal:

```bash
cd chambers-gateway
cargo build --release
```

## Step 6: Run SUMO Scenario with Chambers Gateway

### Option A: Python Adapter (Full Pipeline)

```bash
cd chambers-sim
pip install -e .
chambers-sim sumo \
    --config ../scenarios/sumo/urban_100v.sumocfg \
    --manifest ../manifests/demo_manifest.json \
    --duration 300 \
    --port 8813
```

This connects to SUMO via TraCI, subscribes to per-vehicle telemetry, maps
SUMO vehicle lifecycle to Chambers sessions (depart = session start,
arrive = session end), and feeds records through the gateway.

Add `--gui` to use SUMO's graphical interface:

```bash
chambers-sim sumo \
    --config ../scenarios/sumo/urban_100v.sumocfg \
    --gui \
    --duration 300
```

### Option B: Docker (Complete Environment)

```bash
docker compose -f docker/docker-compose.phase1.yml up --build
```

This starts SUMO, the gateway, mock stakeholders, and the simulation adapter
in containers.

### Option C: Demo Mode (No SUMO Required)

If you want to skip the SUMO installation and test with synthetic data:

```bash
cd chambers-sim
chambers-sim demo --vehicles 100 --duration 300
```

## Step 7: Inspect Audit Logs

After a simulation run, the audit log is stored in
`chambers_audit.db` (SQLite).

### Verify Chain Integrity

```bash
cd chambers-gateway
cargo run -- audit verify <session-uuid>
```

Expected output:

```
HMAC chain integrity VERIFIED for session <session-uuid>
```

### View Session Summary

```bash
cargo run -- audit show <session-uuid>
```

Expected output:

```
Session Summary: <session-uuid>
  Total events:          156
  Data generated:        100
  Data transmitted:      75
  Data blocked:          25
  Burns completed:       1
  Consent revocations:   0
  Jurisdiction blocks:   0
  Policy violations:     0
  Chain intact:          YES
```

### Export for Regulator Review

```bash
cargo run -- audit export <session-uuid> > audit_export.json
```

Produces a JSON document containing all audit entries with HMAC chain and
a verification status -- suitable for GDPR Art. 30 processing record
evidence.

### Human-Readable Driver Summary

```bash
cargo run -- audit driver-summary <session-uuid>
```

Produces a summary such as:

```
Your Driving Session Summary
============================
During this trip, your vehicle collected 100 data points.
75 data points were shared with authorised partners (as you agreed).
25 data requests were blocked because they were not covered by your agreements.

When your trip ended, all raw data was permanently and irrecoverably destroyed.
This means nobody -- not even the vehicle manufacturer -- can access the
original data.

The integrity of your data log has been verified. No records have been altered.
```

## Step 8: Generate Data Residue Report

Run the data residue comparison to quantify the difference between
Chambers-enforced and baseline (no Chambers) data flows:

```bash
cd chambers-sim
chambers-sim demo \
    --vehicles 50 \
    --duration 120 \
    --output residue_report.md
```

The report includes:

- Total bytes generated vs. bytes persisted (per stakeholder)
- Data categories leaked in baseline vs. blocked by Chambers
- Reduction ratio (target: >90%)
- Mapping to paper Section 3.2 "Data Residue Problem"

## Running Phase 1 Threat Scenarios

### T1: Bulk Telemetry Exfiltration

Configure a rogue stakeholder endpoint and attempt to route telemetry to it:

```bash
# The /broker/data endpoint is already configured in mock-stakeholders
# Verify no data reaches it after a simulation run:
curl http://localhost:8000/admin/received/broker
```

Expected: empty list `[]`

### T2: OEM Data Hoarding

Check that the OEM endpoint only received its declared data types at its
declared granularity:

```bash
curl http://localhost:8000/admin/received/oem
```

Verify: only `sensor_health` records at `anonymised_aggregate` granularity.
No raw GPS, no driving behaviour, no camera frames.

### T3: Third-Party Data Selling

```bash
curl http://localhost:8000/admin/received/broker
```

Expected: empty list. The data broker never receives any data.

### T4: Foreign Jurisdiction

```bash
curl http://localhost:8000/admin/received/foreign
```

Expected: empty list. The non-EU endpoint is blocked by jurisdiction checks.

## Troubleshooting

### SUMO not found

```
ERROR: Could not find 'netgenerate'
```

Ensure SUMO is installed and SUMO_HOME is set:

```bash
export SUMO_HOME=$(brew --prefix sumo)/share/sumo  # macOS
export SUMO_HOME=/usr/share/sumo                     # Linux
```

### TraCI connection refused

```
Connection refused on port 8813
```

Start SUMO with remote port enabled:

```bash
sumo -c scenarios/sumo/urban_100v.sumocfg --remote-port 8813
```

Or use `sumo-gui` for the graphical version.

### Rust compilation errors

Ensure you have Rust 1.78+ and the `bundled` feature for SQLite:

```bash
rustup update
cd chambers-gateway && cargo build --release
```
