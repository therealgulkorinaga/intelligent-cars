# CARLA Scenarios for Chambers Testbed

Three CARLA-based scenarios demonstrating vehicle data simulation, V2X communication, and sealed ADAS event capture.

## Prerequisites

- **CARLA Simulator** >= 0.9.14
  - Download: https://carla.readthedocs.io/en/latest/start_quickstart/
  - Launch the CARLA server before running any scenario
- **CARLA Python API**:
  ```bash
  pip install carla
  ```
- **SUMO** (for v2x_cosim.py only):
  ```bash
  brew install sumo  # macOS
  sudo apt install sumo sumo-tools  # Ubuntu
  ```
- Python 3.10+

## Scenarios

### 1. urban_drive.py -- Full Urban Driving Scenario

Spawns an ego vehicle with a complete sensor suite in a populated urban environment and streams data to the Chambers gateway.

**Sensors attached:**
- RGB Camera (front, 1280x720, 90 FOV)
- LiDAR (roof, 32 channels, 10 Hz, 100 m range)
- GNSS (with configurable noise)
- IMU (6-axis)
- Collision detector

**Traffic:**
- 50 NPC vehicles with autopilot
- 20 pedestrians with AI walkers

```bash
# Start CARLA server first, then:
python urban_drive.py --host localhost --port 2000 --town Town03 --duration 300

# With Chambers manifest:
python urban_drive.py --manifest ../../manifests/demo_manifest.json

# All options:
python urban_drive.py --help
```

### 2. v2x_cosim.py -- CARLA-SUMO V2X Co-simulation

Runs CARLA and SUMO in tandem. CARLA handles the ego vehicle with 3D rendering; SUMO manages NPC traffic flow. All vehicles broadcast ETSI ITS-G5 Cooperative Awareness Messages (CAMs) with pseudonym rotation every 300 seconds.

```bash
# Requires both CARLA server running and SUMO installed
python v2x_cosim.py --carla-host localhost --carla-port 2000 \
                     --sumo-cfg ../sumo/urban_100v.sumocfg \
                     --duration 600

# All options:
python v2x_cosim.py --help
```

**Key features:**
- V2X CAM message generation per ETSI EN 302 637-2
- Pseudonym rotation every 300 s with linkage data destruction
- Synchronous co-simulation (CARLA + SUMO tick in lockstep)

### 3. sealed_event_demo.py -- Sealed ADAS Event Capture

Demonstrates the Chambers "sealed event" concept:
1. Ego vehicle approaches a green-lit intersection
2. An NPC vehicle is scripted to run the red light
3. On collision, a sealed event captures 5 s before + 2 s after
4. The event shows what data is *retained* (speed, IMU, GNSS, camera, LiDAR) vs. *burned* (driver face, cabin audio, personal data)
5. A SHA-256 seal hash provides tamper evidence

```bash
python sealed_event_demo.py --host localhost --port 2000
```

Output is written to `sealed_event_output.json` and printed to the console.

## Chambers Gateway Integration

All scenarios stream data to the Chambers gateway at `http://localhost:8080` by default. Start the gateway before running scenarios to see full data routing:

```bash
# From project root:
cd chambers-gateway && cargo run

# Then run a scenario:
cd scenarios/carla && python urban_drive.py
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: carla` | `pip install carla` or add CARLA PythonAPI egg to `PYTHONPATH` |
| Connection refused | Ensure CARLA server is running: `./CarlaUE4.sh` (Linux) or `CarlaUE4.exe` (Windows) |
| Map not found | Check available maps with `client.get_available_maps()` |
| Low frame rate | Reduce NPC count: `--npc-vehicles 20 --npc-walkers 10` |
| No collision in demo | Re-run `sealed_event_demo.py`; intersection geometry varies by map |
