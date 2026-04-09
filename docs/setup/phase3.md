# Phase 3 Setup: ROS 2 + Gazebo ECU-Level Integration

Phase 3 simulates intra-vehicle ECU communication, CAN bus messages, and
enforcement at the middleware layer using ROS 2 Humble and Gazebo Fortress.
This phase targets all 5 enforcement points simultaneously.

## Goals

- Full 5-enforcement-point simultaneous operation
- Bluetooth pairing session lifecycle: pair, sync, disconnect, burn (zero residual)
- OBD-II authenticated diagnostic access; unauthenticated requests receive encrypted responses
- Wi-Fi hotspot passthrough with zero passenger traffic inspection
- All 16 threats simulated with passing mitigations
- Compliance artefact generation (GDPR Art. 30, Art. 17, R155 Annex 5)

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Rust | 1.78+ | chambers-gateway |
| Python | 3.11+ | chambers-sim, mock-stakeholders |
| ROS 2 | Humble Hawksbill | ECU simulation, middleware |
| Gazebo | Fortress (Ignition) | Vehicle model, physics, sensors |
| Ubuntu | 22.04 LTS | Primary supported platform for ROS 2 |

ROS 2 Humble targets Ubuntu 22.04. macOS and other Linux distributions
are possible but require additional effort.

## Step 1: Install ROS 2 Humble

### Ubuntu 22.04

```bash
# Set locale
sudo apt update && sudo apt install locales
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8

# Add ROS 2 apt repository
sudo apt install software-properties-common
sudo add-apt-repository universe
sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# Install ROS 2 Humble desktop (includes Gazebo integration)
sudo apt update
sudo apt install ros-humble-desktop

# Source the setup script
source /opt/ros/humble/setup.bash
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc

# Install development tools
sudo apt install python3-colcon-common-extensions python3-rosdep
sudo rosdep init
rosdep update
```

### Verify Installation

```bash
source /opt/ros/humble/setup.bash
ros2 --version
ros2 topic list
```

## Step 2: Install Gazebo Fortress

Gazebo Fortress (also called Ignition Gazebo) is the simulation engine
for the vehicle model and sensors.

### Ubuntu 22.04

```bash
# Gazebo Fortress is included in ros-humble-desktop, but install
# the standalone package for additional tools:
sudo apt install ros-humble-ros-gz

# Verify
gz sim --version
```

### Alternative: Install Gazebo Standalone

```bash
sudo apt install gz-fortress
```

## Step 3: Build the ROS 2 Workspace

The Chambers ROS 2 workspace contains the ECU simulation nodes and the
Chambers gateway ROS 2 node.

### Create and Build the Workspace

```bash
# Create workspace
mkdir -p ~/chambers_ws/src
cd ~/chambers_ws/src

# Symlink or copy the relevant packages
ln -s /path/to/intelligent-cars/chambers-sim/ros2_packages/* .

# Install dependencies
cd ~/chambers_ws
rosdep install --from-paths src --ignore-src -r -y

# Build
colcon build --symlink-install

# Source the workspace
source install/setup.bash
echo "source ~/chambers_ws/install/setup.bash" >> ~/.bashrc
```

### Workspace Structure

The ROS 2 workspace should contain these packages:

```
chambers_ws/src/
  chambers_vehicle_model/     # Gazebo vehicle model (URDF/SDF)
  chambers_ecu_sim/           # Simulated ECU nodes
    powertrain_ecu_node       # Speed, RPM, throttle, brake
    body_ecu_node             # Door status, lights, wipers
    adas_ecu_node             # Perception, collision warnings
    infotainment_ecu_node     # Media, Bluetooth, navigation
    telematics_ecu_node       # Cellular, OTA status
  chambers_gateway_node/      # Chambers gateway as ROS 2 node
  chambers_ep_nodes/          # Enforcement point nodes (EP1-EP5)
  chambers_msgs/              # Custom message definitions
```

## Step 4: Launch ECU Simulation

### Launch the Full Vehicle Model

```bash
# Terminal 1: Launch Gazebo with the vehicle model
ros2 launch chambers_vehicle_model vehicle_sim.launch.py

# Terminal 2: Launch all ECU nodes
ros2 launch chambers_ecu_sim all_ecus.launch.py

# Terminal 3: Launch the Chambers gateway node
ros2 launch chambers_gateway_node gateway.launch.py \
    manifest:=/path/to/manifests/demo_manifest.json
```

### Verify ECU Topics

```bash
ros2 topic list
```

Expected topics:

```
/vehicle/powertrain/speed
/vehicle/powertrain/rpm
/vehicle/powertrain/throttle
/vehicle/powertrain/brake_pressure
/vehicle/body/door_status
/vehicle/body/lights
/vehicle/adas/perception
/vehicle/adas/collision_warning
/vehicle/infotainment/media_state
/vehicle/infotainment/bluetooth_state
/vehicle/telematics/cellular_status
/vehicle/telematics/ota_status
/chambers/ep1/cellular_out
/chambers/ep2/bluetooth_out
/chambers/ep3/obd_out
/chambers/ep4/v2x_out
/chambers/ep5/wifi_out
/chambers/audit/events
```

### Monitor Data Flow

```bash
# Watch gateway decisions in real time
ros2 topic echo /chambers/audit/events

# Watch a specific enforcement point
ros2 topic echo /chambers/ep1/cellular_out
```

## Step 5: Run the 5-EP Integration Test

The integration test exercises all 5 enforcement points simultaneously,
validating that they share one manifest, one HSM, and one audit log.

### Test Scenario

The test drives the following concurrent activities:

```
  +-------------------------------------------+
  |  VEHICLE STATE DURING 5-EP TEST           |
  |                                            |
  |  EP1: Driving (cellular telemetry)         |
  |  EP2: Phone paired (contacts synced)       |
  |  EP3: Diagnostic tool connected (auth)     |
  |  EP4: V2X broadcasting (CAMs + pseudonym)  |
  |  EP5: Passenger on Wi-Fi hotspot           |
  +-------------------------------------------+
```

### Run the Test

```bash
# Option A: ROS 2 launch file
ros2 launch chambers_ecu_sim five_ep_integration.launch.py

# Option B: pytest (if wrapped as a test)
cd /path/to/intelligent-cars
pytest tests/integration/test_five_ep.py -v
```

### Expected Results

| EP | Assertion |
|----|-----------|
| EP1 | Telemetry routed to OEM (anonymised), insurer (trip score), ADAS (sealed events) |
| EP2 | Contacts encrypted under pairing key; on disconnect, pairing key burned, data irrecoverable |
| EP3 | Authenticated tool gets decrypted diagnostics; unauthenticated dongle gets encrypted response |
| EP4 | CAM broadcasts with pseudonym; rotation burns linkage; inbound V2X ephemeral only |
| EP5 | Passenger traffic passes through unmodified; vehicle outbound follows EP1 rules |

Cross-cutting assertions:

- Single audit log captures all 5 channels coherently
- Single burn engine destroys all session keys on vehicle park
- HMAC chain integrity verified across all entries
- Manifest shared across all enforcement points

## EP2: Bluetooth Pairing Session

### Simulate Phone Pairing

```bash
# Launch infotainment ECU with Bluetooth simulation
ros2 launch chambers_ecu_sim infotainment.launch.py \
    bluetooth_mode:=simulate_pairing

# Or trigger pairing via service call
ros2 service call /infotainment/bluetooth/pair \
    chambers_msgs/srv/BluetoothPair \
    "{device_name: 'iPhone-Driver', profiles: ['PBAP', 'MAP', 'A2DP']}"
```

### Verify Pairing Session Lifecycle

1. **Pair**: HSM generates pairing session key
2. **Sync**: Contacts, call history, SMS encrypted under pairing key
3. **Active**: Contact names displayed on IVI (manifest allows display)
4. **Disconnect**: Trigger disconnect event

```bash
ros2 service call /infotainment/bluetooth/disconnect \
    chambers_msgs/srv/BluetoothDisconnect \
    "{device_name: 'iPhone-Driver'}"
```

5. **Burn**: Pairing session key destroyed, 6-layer protocol
6. **Verify**: Attempt to read contacts -- must fail

### Test Scenarios

| Scenario | Expected Result |
|----------|----------------|
| Rental car return | Previous renter's contacts/calls/SMS irrecoverable |
| Vehicle resale | Previous owner's phone data gone after ownership transfer |
| Family car switch | Each driver's data isolated to their pairing session |

## EP3: OBD-II Diagnostic Handler

### Simulate Diagnostic Connection

```bash
# Authenticated diagnostic tool
ros2 service call /obd/diagnostic_request \
    chambers_msgs/srv/DiagnosticRequest \
    "{tool_id: 'official_tool_001', authenticated: true, \
      pid_request: ['0x0C', '0x0D', '0x05']}"

# Unauthenticated aftermarket dongle
ros2 service call /obd/diagnostic_request \
    chambers_msgs/srv/DiagnosticRequest \
    "{tool_id: 'cheap_dongle_ebay', authenticated: false, \
      pid_request: ['0x0C', '0x0D', '0x05']}"
```

### Expected Responses

| Tool | Authentication | Response |
|------|---------------|----------|
| Official diagnostic tool | Valid credentials | Decrypted DTCs, PIDs, freeze frames |
| Aftermarket dongle | None | Encrypted response (ciphertext, unusable) |
| Insurance black box | Own cellular | Manifest controls apply, field filtering enforced |

## EP5: Wi-Fi Hotspot Passthrough

### Simulate Hotspot Activity

```bash
# Launch Wi-Fi simulation node
ros2 launch chambers_ep_nodes wifi_hotspot.launch.py

# Simulate passenger traffic
ros2 service call /wifi/simulate_passenger \
    chambers_msgs/srv/WifiPassenger \
    "{device_mac: 'AA:BB:CC:DD:EE:FF', traffic_type: 'web_browsing'}"
```

### Verify Passthrough Policy

```bash
# Check that no passenger traffic was captured or logged
ros2 topic echo /chambers/ep5/wifi_out --once
# Should show: passthrough_only=true, captured_bytes=0
```

## Gazebo Vehicle Model

The Gazebo vehicle model includes:

| Component | Description | ROS 2 Topic Prefix |
|-----------|-------------|-------------------|
| Chassis | Vehicle body with 4 wheels | `/vehicle/odom` |
| Camera | Front-facing RGB | `/vehicle/camera/image` |
| LiDAR | Roof-mounted 3D | `/vehicle/lidar/points` |
| GPS | GNSS receiver | `/vehicle/gps/fix` |
| IMU | Inertial measurement | `/vehicle/imu/data` |

### Launch Gazebo Only

```bash
# Gazebo with the vehicle model (no ECU nodes)
ros2 launch chambers_vehicle_model gazebo_only.launch.py
```

## Docker Setup for Phase 3

A Docker Compose configuration for Phase 3 would include ROS 2, Gazebo,
and the Chambers gateway. Due to the complexity of ROS 2 + Gazebo in
containers, a dedicated Dockerfile is recommended.

```bash
# Build the Phase 3 image
docker build -t chambers-ros2 -f docker/Dockerfile.ros2 .

# Run with display forwarding (Linux with X11)
docker run -it --rm \
    --net=host \
    -e DISPLAY=$DISPLAY \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    chambers-ros2 \
    ros2 launch chambers_ecu_sim all_ecus.launch.py
```

## Troubleshooting

### ROS 2 packages not found

Ensure both the ROS 2 installation and workspace are sourced:

```bash
source /opt/ros/humble/setup.bash
source ~/chambers_ws/install/setup.bash
```

### Gazebo fails to start

```
[Err] [Server.cc:xxx] Unable to find fuel model
```

Ensure Gazebo Fortress is installed:

```bash
sudo apt install ros-humble-ros-gz
```

### colcon build fails

Install missing dependencies:

```bash
cd ~/chambers_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
```

### Display issues in Docker

For headless environments, use Gazebo in server mode:

```bash
gz sim -s  # server only, no GUI
```

Or use virtual framebuffer:

```bash
apt install xvfb
xvfb-run -a gz sim
```
