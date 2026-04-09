# Gazebo + ROS 2 ECU Simulation Scenario

Full vehicle simulation with 5 ECU domain controllers, sensor plugins, and Chambers gateway integration.

## Prerequisites

- **ROS 2** Humble Hawksbill or Iron Irwini
  - Install: https://docs.ros.org/en/humble/Installation.html
- **Gazebo Fortress** (Ignition)
  - Install: https://gazebosim.org/docs/fortress/install
- **ros_gz_bridge** package:
  ```bash
  sudo apt install ros-humble-ros-gz-bridge  # Ubuntu
  ```
- **chambers_sim_ros** package (from this project):
  ```bash
  cd /path/to/intelligent-cars
  colcon build --packages-select chambers_sim_ros
  source install/setup.bash
  ```
- Python 3.10+

## Architecture

```
+------------------+     +------------------+
| Gazebo Fortress  |     | ros_gz_bridge    |
| (vehicle_model)  +---->+ (topic bridging) +---+
+------------------+     +------------------+   |
                                                |  ROS 2 Topics
+------------------+                            |
| Powertrain ECU   +---> /chambers/ecu/powertrain/telemetry
+------------------+                            |
| Body ECU         +---> /chambers/ecu/body/telemetry
+------------------+                            |
| ADAS ECU         +---> /chambers/ecu/adas/telemetry
+------------------+                            |
| Infotainment ECU +---> /chambers/ecu/infotainment/telemetry
+------------------+                            |
| Telematics ECU   +---> /chambers/ecu/telematics/telemetry
+------------------+                            |
                                                v
                        +---------------------------+
                        | Chambers Gateway Bridge    |
                        | (privacy filter + routing) |
                        +------------+--------------+
                                     |
                                     v
                        +---------------------------+
                        | Chambers Gateway (Rust)    |
                        | http://localhost:8080      |
                        +---------------------------+
```

## Files

| File | Description |
|------|-------------|
| `vehicle_model.sdf` | Gazebo SDF world with vehicle (chassis, 4 wheels, sensors) |
| `ecu_sim_launch.py` | ROS 2 launch file starting all components |
| `ecu_sim_params.yaml` | Configuration for ECU nodes, signal ranges, and gateway |

## Quick Start

```bash
# Source ROS 2 and workspace
source /opt/ros/humble/setup.bash
source /path/to/intelligent-cars/install/setup.bash

# Launch everything (Gazebo + ECUs + Gateway bridge)
ros2 launch scenarios/gazebo/ecu_sim_launch.py

# Launch with custom parameters
ros2 launch scenarios/gazebo/ecu_sim_launch.py \
  params_file:=/path/to/custom_params.yaml \
  vehicle_id:=my-test-vehicle \
  gateway_url:=http://192.168.1.100:8080

# Launch headless (no GUI, for CI)
ros2 launch scenarios/gazebo/ecu_sim_launch.py use_gui:=false
```

## Standalone Gazebo (no ROS)

```bash
# View the vehicle model in Gazebo
gz sim -r scenarios/gazebo/vehicle_model.sdf

# Send velocity commands
gz topic -t /chambers/cmd_vel -m gz.msgs.Twist -p 'linear: {x: 5.0}'
```

## ECU Simulator Nodes

Each ECU node publishes simulated signals at the configured rate:

| ECU | Rate | Signals |
|-----|------|---------|
| Powertrain | 10 Hz | RPM, torque, throttle, gear, fuel, coolant, oil pressure, battery |
| Body | 2 Hz | Doors, windows, lights, wipers, temps, HVAC, seatbelts |
| ADAS | 20 Hz | LDW, FCW, BSD, ACC, AEB, LKA, TSR |
| Infotainment | 1 Hz | Media, Bluetooth, nav, voice, display |
| Telematics | 5 Hz | Cellular, OTA, V2X, eCall, geofence, GNSS quality |

All ECUs also publish diagnostic trouble codes (DTCs) at a low stochastic rate.

## Customisation

Edit `ecu_sim_params.yaml` to:
- Change signal publish rates
- Adjust signal simulation ranges
- Modify DTC generation probability
- Configure the gateway connection
- Block specific stakeholders for testing
- Toggle privacy filtering and burn-on-end behaviour

## Sensors

The vehicle model includes these Gazebo sensor plugins:

| Sensor | Type | Topic | Rate |
|--------|------|-------|------|
| Front camera | RGB 1280x720 | `/chambers/camera/front/image_raw` | 10 Hz |
| Roof LiDAR | 32-ch, 100 m | `/chambers/lidar/roof/points` | 10 Hz |
| IMU | 6-axis | `/chambers/imu/data` | 100 Hz |
| GPS | NavSat | `/chambers/navsat/fix` | 10 Hz |
