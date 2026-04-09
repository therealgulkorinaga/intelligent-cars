# Phase 2 Setup: CARLA + Sensor-Level Chambers

Phase 2 proves sealed drive sessions with realistic sensor data (camera,
LiDAR, GPS, IMU). It demonstrates sealed ADAS events, V2X pseudonym rotation,
consent revocation mid-session, and HSM fallback mode.

## Goals

- CARLA ego-vehicle drive session sealed with ephemeral key
- Sealed ADAS event captures bounded temporal window around collision
- V2X pseudonym rotation burns cross-session linkage data (T14, T15, T16)
- HSM fallback mode: zero telemetry when HSM unavailable
- Consent revocation mid-session halts data flow within 1 message
- Sensor-to-gateway encryption latency < 10ms per frame

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Rust | 1.78+ | chambers-gateway |
| Python | 3.11+ | chambers-sim, mock-stakeholders |
| CARLA | 0.9.15+ | Ego-vehicle sensor simulation |
| SUMO | 1.20+ | Co-simulation for V2X traffic (optional) |
| GPU | NVIDIA recommended | CARLA rendering (CPU possible with --no-rendering) |

## Step 1: Install CARLA

### Option A: Download from GitHub Releases

Download CARLA 0.9.15 from the official releases:

```bash
# Linux
wget https://carla-releases.s3.us-east-005.backblazeb2.com/Linux/CARLA_0.9.15.tar.gz
mkdir -p ~/carla && tar -xzf CARLA_0.9.15.tar.gz -C ~/carla

# Set CARLA_ROOT
export CARLA_ROOT=~/carla
```

### Option B: Docker (Recommended for Reproducibility)

Pull the official CARLA Docker image:

```bash
docker pull carlasim/carla:0.9.15
```

### Install the CARLA Python Client

```bash
pip install carla==0.9.15
```

If the pip package is unavailable for your platform, install from the
CARLA distribution:

```bash
pip install $CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.15-*.whl
```

## Step 2: GPU Requirements

CARLA uses Unreal Engine 4 for rendering. GPU requirements:

| Mode | GPU | Notes |
|------|-----|-------|
| Full rendering | NVIDIA GTX 1080+ / RTX series | Best sensor fidelity |
| No rendering | CPU only | Data-only mode, no visual output |
| Docker (GPU) | NVIDIA + nvidia-docker | Required for containerised rendering |

### Verify GPU Access

```bash
nvidia-smi  # should show your NVIDIA GPU
```

For CPU-only testing (no camera/LiDAR rendering):

```bash
# CARLA will start in headless mode
./CarlaUE4.sh -RenderOffScreen -nosound
```

## Step 3: Docker Setup with nvidia-docker

If using Docker with GPU support, install the NVIDIA Container Toolkit:

### Install nvidia-docker (Linux)

```bash
# Ubuntu/Debian
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Run CARLA in Docker with GPU

```bash
docker run --privileged --gpus all --net=host \
    -e DISPLAY=$DISPLAY \
    carlasim/carla:0.9.15 \
    /bin/bash -c "./CarlaUE4.sh -RenderOffScreen -nosound"
```

### Full Phase 2 Docker Compose

```bash
docker compose -f docker/docker-compose.phase2.yml up --build
```

This starts:

| Service | Description | Port |
|---------|-------------|------|
| `gateway` | Chambers Rust gateway | internal |
| `stakeholders` | Mock FastAPI endpoints | 8000 |
| `carla` | CARLA simulator (GPU, render-off) | 2000, 2001 |
| `sim` | Python CARLA adapter | internal |

## Step 4: Run CARLA Scenario

### Start CARLA Server

If not using Docker, start CARLA manually:

```bash
cd $CARLA_ROOT
./CarlaUE4.sh -RenderOffScreen -nosound &
```

Wait for CARLA to finish loading (usually 10-20 seconds). You should see:

```
Listening to port 2000
```

### Run the Chambers Scenario

```bash
cd chambers-sim
pip install -e ".[carla]"

chambers-sim carla \
    --host localhost \
    --port 2000 \
    --town Town03 \
    --manifest ../manifests/demo_manifest.json \
    --duration 300
```

Parameters:

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | localhost | CARLA server hostname |
| `--port` | 2000 | CARLA server port |
| `--town` | Town01 | CARLA map (Town01-Town07) |
| `--duration` | 60 | Simulation duration in seconds |
| `--manifest` | built-in demo | Path to preservation manifest |

The scenario creates:

- Ego vehicle with sensor suite (RGB camera, LiDAR, GNSS, IMU, collision)
- NPC traffic (50 vehicles, 20 pedestrians)
- 5-10 minute urban drive route
- Session sealed under ephemeral key

### Recommended Towns

| Town | Description | Best For |
|------|-------------|----------|
| Town01 | Default town | General testing |
| Town03 | Large town, highway + urban | Mixed driving scenarios |
| Town05 | Multi-lane urban + bridge | Complex traffic patterns |

## Step 5: Run V2X Co-simulation

V2X testing uses the CARLA-SUMO co-simulation bridge. CARLA provides the
ego vehicle while SUMO provides surrounding traffic.

### Prerequisites

- Both CARLA and SUMO installed
- SUMO network generated (from Phase 1)

### Run Co-simulation

```bash
# Terminal 1: Start CARLA
cd $CARLA_ROOT && ./CarlaUE4.sh -RenderOffScreen -nosound &

# Terminal 2: Start the co-simulation scenario
cd scenarios/carla
python urban_drive.py \
    --v2x \
    --sumo-config ../sumo/urban_100v.sumocfg
```

The V2X scenario:

1. Ego vehicle (CARLA) broadcasts simulated CAM messages
2. Surrounding vehicles (SUMO) populate the traffic
3. Pseudonym rotation every 5 minutes
4. Each rotation triggers a burn of linkage data
5. Audit log records pseudonym sessions

### V2X Threat Validation

After the co-simulation completes, validate:

```bash
# T14: Check that trajectory reconstruction fails
# across pseudonym rotations
python -c "
# Trajectory analysis script
# Collect CAMs, attempt cross-session stitching
# Assert: cross-session linkage data destroyed
"

# T16: Verify inbound V2X data not persisted
# Check audit log for any inbound V2X storage events
cd chambers-gateway
cargo run -- audit export <session-uuid> | \
    python -c "import json,sys; d=json.load(sys.stdin); \
    print('Inbound V2X stored:', any('v2x_inbound_stored' in str(e) for e in d['entries']))"
```

## Step 6: Run Sealed ADAS Event Demo

Sealed events capture bounded temporal windows around safety-critical
triggers.

### Trigger a Collision Event

The CARLA scenario includes NPC vehicles that can cause near-collisions.
When the collision sensor fires:

1. Rolling buffer captures the preceding 5 seconds of sensor data
2. Forward capture continues for 2 seconds post-trigger
3. Captured data is anonymised per manifest
4. Event is encrypted under a separate key with P12M retention
5. Event is tagged with trigger type, timestamp, and purpose

### Verify Sealed Event

```bash
# Check ADAS supplier received the event
curl http://localhost:8000/admin/received/adas
```

Expected: event payload with `trigger_type: "safety_critical"`, anonymised
fields, no `driver_id` or `vin`.

## Phase 2 Specific Features

### Consent Revocation Mid-Session

During an active CARLA drive session, revoke insurer consent:

```python
# In the simulation code or via API:
gateway.revoke_consent(session_id, "insurer_allianz")
```

Verify: from the revocation timestamp onward, zero data flows to the
insurer endpoint. The audit log records a `ConsentRevoked` event.

### HSM Fallback Mode

Simulate HSM becoming unavailable during a drive:

```python
# Kill/pause the SoftHSM2 process or call:
gateway.enter_fallback_mode(session_id)
```

Expected behaviour:

- Vehicle remains drivable (CARLA simulation continues)
- Gateway blocks ALL telemetry (conservative default)
- Audit log records `HsmFallback` event
- On HSM recovery: new session key generated, normal operation resumes

## Troubleshooting

### CARLA connection refused

```
RuntimeError: time-out of 10000ms while waiting for the simulator
```

Ensure CARLA is running and the port matches:

```bash
# Check CARLA is listening
lsof -i :2000
```

### CARLA out of memory (GPU)

Reduce rendering quality or use no-rendering mode:

```bash
./CarlaUE4.sh -RenderOffScreen -nosound -quality-level=Low
```

### No NVIDIA GPU available

Run CARLA in no-rendering mode (data-only, no camera/LiDAR output):

```bash
./CarlaUE4.sh -RenderOffScreen -nosound
```

Camera and LiDAR sensors will still produce data, but at lower fidelity.
For full sensor simulation, an NVIDIA GPU is recommended.

### CARLA-SUMO co-simulation bridge errors

Pin compatible versions:

- CARLA 0.9.15
- SUMO 1.20+

Ensure both simulators are started before launching the co-simulation
script. The bridge performs a handshake that may time out if either
simulator is slow to start.
