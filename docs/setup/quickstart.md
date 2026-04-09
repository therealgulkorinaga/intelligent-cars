# Quickstart Guide

Get the Chambers Automotive Simulation Testbed running in under 5 minutes.

## Prerequisites

| Dependency | Version | Required For |
|-----------|---------|-------------|
| Rust | 1.78+ | `chambers-gateway` (Rust binary) |
| Python | 3.11+ | `chambers-sim`, `mock-stakeholders` |
| Docker + Docker Compose | Latest | Containerised setup (optional) |
| SUMO | 1.20+ | Phase 1 traffic simulation (optional for demo) |

Verify your environment:

```bash
rustc --version    # expect 1.78.0 or later
python3 --version  # expect 3.11 or later
docker --version   # optional
```

## Quick Demo (No Simulators Needed)

The fastest way to see Chambers in action. This runs a self-contained demo
with synthetic vehicle data -- no SUMO, CARLA, or ROS 2 required.

### Option A: Rust Gateway Demo

The Rust gateway includes a built-in demo that exercises the full session
lifecycle: session start, data processing, manifest evaluation, consent
revocation, 6-layer burn, and audit verification.

```bash
cd chambers-gateway
cargo build --release
cargo run -- demo
```

You will see output covering:

1. Session start with ephemeral key generation
2. Speed, GPS, driving behaviour, and ADAS event processing
3. Per-stakeholder routing (OEM gets aggregated, insurer gets trip scores)
4. Undeclared data (contact sync) blocked
5. Consent revocation for insurer mid-session
6. Post-revocation data routing (insurer excluded)
7. 6-layer burn with per-layer timing
8. Gateway metrics and audit summary
9. HMAC chain integrity verification
10. Human-readable driver summary

### Option B: Python Simulation Demo

The Python simulation runs synthetic vehicles through the local gateway
(pure-Python implementation of the manifest evaluator and burn protocol).

```bash
cd chambers-sim
pip install -e .
chambers-sim demo --vehicles 10 --duration 60
```

Options:

- `--vehicles N` -- number of synthetic vehicles (default: 5)
- `--duration N` -- simulation steps (default: 60)
- `--manifest PATH` -- custom manifest JSON (default: built-in demo)
- `--output PATH` -- write data residue report to file

### Option C: Both Together

Run the Python simulation with a data residue report to see the reduction
in persisted data:

```bash
cd chambers-sim
pip install -e .
chambers-sim demo --vehicles 20 --duration 120 --output residue_report.md
```

## Docker Quickstart

Run the full Phase 1 environment (gateway + mock stakeholders + simulation +
SUMO) in containers.

```bash
docker compose -f docker/docker-compose.phase1.yml up --build
```

This starts four services:

| Service | Description | Port |
|---------|-------------|------|
| `gateway` | Chambers Rust gateway | internal |
| `stakeholders` | Mock FastAPI endpoints (OEM, insurer, ADAS, Tier-1) | 8000 |
| `sim` | Python simulation adapter | internal |
| `sumo` | SUMO traffic simulator with TraCI | 8813 |

To run without SUMO (demo mode only):

```bash
docker compose -f docker/docker-compose.phase1.yml up --build gateway stakeholders sim
```

Stop and clean up:

```bash
docker compose -f docker/docker-compose.phase1.yml down -v
```

## Mock Stakeholder Endpoints

Start the mock stakeholder API server independently (useful for development):

```bash
cd mock-stakeholders
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Endpoints available at `http://localhost:8000`:

| Endpoint | Stakeholder | Accepts |
|----------|------------|---------|
| `POST /oem/telemetry` | OEM | anonymised sensor health aggregates |
| `POST /insurer/trip` | Insurer | driving behaviour trip scores (no GPS) |
| `POST /adas/event` | ADAS Supplier | sealed safety-critical events |
| `POST /tier1/diagnostics` | Tier-1 Supplier | component telemetry, diagnostic codes |
| `POST /broker/data` | Data Broker (rogue) | always accepts but logs SECURITY ALERT |
| `POST /foreign/telemetry` | Foreign (non-EU) | always accepts but logs JURISDICTION ALERT |
| `GET /admin/received` | Admin | view all received data |
| `GET /admin/stats` | Admin | payload counts per stakeholder |
| `DELETE /admin/reset` | Admin | clear all stored data |

## Running Tests

### Rust Tests (chambers-gateway)

```bash
cd chambers-gateway
cargo test
```

Runs unit tests for all modules: HSM (encrypt/decrypt, key destruction),
manifest evaluator (field filtering, granularity, jurisdiction, revocation),
burn engine (6-layer protocol), audit log (HMAC chain, tamper detection),
and gateway (session lifecycle).

### Python Tests (chambers-sim)

```bash
cd chambers-sim
pip install -e ".[dev]"
pytest
```

Runs tests for the local gateway, data models, and adapter logic.

### Mock Stakeholder Tests

```bash
cd mock-stakeholders
pip install -e ".[dev]"
pytest  # if tests exist in the package
```

### Integration Tests

```bash
pytest tests/integration/
```

Integration tests exercise the full pipeline: simulator adapter to gateway
to stakeholder endpoint.

### Linting

```bash
# Rust
cd chambers-gateway && cargo clippy

# Python
cd chambers-sim && ruff check .
cd mock-stakeholders && ruff check .
```

## Audit Log CLI

After running a demo or simulation, inspect the audit log:

```bash
cd chambers-gateway

# Verify HMAC chain integrity
cargo run -- audit verify <session-uuid>

# Show session summary
cargo run -- audit show <session-uuid>

# Export full audit log as JSON (for regulators)
cargo run -- audit export <session-uuid>

# Human-readable driver summary
cargo run -- audit driver-summary <session-uuid>
```

The session UUID is printed when a session starts during the demo.

## Manifest Files

Example manifests are in the `manifests/` directory:

| File | Description |
|------|-------------|
| `demo_manifest.json` | Full 4-stakeholder manifest (OEM, insurer, ADAS, Tier-1) |
| `minimal_manifest.json` | Minimal single-stakeholder manifest |
| `insurer_only_manifest.json` | Insurer-only for testing field filtering |
| `no_stakeholders_manifest.json` | Empty stakeholders list (all data blocked) |
| `schema.json` | JSON Schema definition for manifest validation |

## Next Steps

- **Phase 1 full setup**: See [docs/setup/phase1.md](phase1.md)
- **Phase 2 (CARLA)**: See [docs/setup/phase2.md](phase2.md)
- **Phase 3 (ROS 2)**: See [docs/setup/phase3.md](phase3.md)
- **Architecture overview**: See [docs/architecture/overview.md](../architecture/overview.md)
