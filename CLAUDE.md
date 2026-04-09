# Chambers Automotive Simulation Testbed

## Project Structure
- `chambers-gateway/` — Rust workspace: core types, session seal, manifest, burn engine, audit log, gateway
- `chambers-sim/` — Python package: simulator adapters (SUMO, CARLA, ROS2), local gateway, CLI
- `mock-stakeholders/` — FastAPI mock endpoints for OEM, insurer, ADAS supplier, Tier-1
- `manifests/` — JSON Schema + example preservation manifests
- `scenarios/` — SUMO, CARLA, Gazebo simulation scenarios
- `tests/` — Integration and E2E tests
- `docker/` — Dockerfiles and compose files per phase

## Build Commands
- Rust: `cd chambers-gateway && cargo build --release && cargo test`
- Python: `cd chambers-sim && pip install -e ".[dev]" && pytest`
- Mock stakeholders: `cd mock-stakeholders && pip install -e ".[dev]" && uvicorn app.main:app`
- Demo (no simulator): `cd chambers-sim && chambers-sim demo --vehicles 10 --duration 60`

## Architecture
The system validates the Chambers sealed ephemeral computation model for connected vehicles:
1. Session Seal — ephemeral AES-256-GCM key per drive session via SoftHSM2
2. Preservation Manifest — typed schema declaring what data survives, for whom
3. Burn Engine — 6-layer cryptographic erasure on session end
4. Audit Log — HMAC-chained immutable record per session
5. Gateway — orchestrates all components at the telematics enforcement point

## Conventions
- Rust code uses `thiserror` for errors, `tracing` for logging
- Python uses Pydantic v2 models, structlog for logging
- All manifests must validate against `manifests/schema.json`
- Tests should be deterministic (no network calls, mock external deps)
