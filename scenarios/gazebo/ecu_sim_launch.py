#!/usr/bin/env python3
"""ROS 2 launch file for the Chambers Gazebo ECU simulation.

Starts:
  1. Gazebo Fortress with the vehicle model
  2. Five ECU simulator nodes (powertrain, body, adas, infotainment, telematics)
  3. Chambers gateway bridge node
  4. ros_gz_bridge for sensor topic bridging

All nodes are configured via a single YAML parameter file.

Usage:
    ros2 launch ecu_sim_launch.py

    # With custom params:
    ros2 launch ecu_sim_launch.py params_file:=/path/to/custom_params.yaml

Requirements:
    - ROS 2 Humble or Iron
    - Gazebo Fortress (gz-sim)
    - ros_gz_bridge package
    - chambers_sim Python package
"""

from __future__ import annotations

import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    GroupAction,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
    RegisterEventHandler,
    SetEnvironmentVariable,
    TimerAction,
)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit, OnProcessStart
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    FindExecutable,
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCENARIO_DIR = Path(__file__).resolve().parent
SDF_MODEL_PATH = str(SCENARIO_DIR / "vehicle_model.sdf")
DEFAULT_PARAMS_FILE = str(SCENARIO_DIR / "ecu_sim_params.yaml")


def generate_launch_description() -> LaunchDescription:
    """Build the full launch description."""

    # ---- Launch arguments ----
    params_file_arg = DeclareLaunchArgument(
        "params_file",
        default_value=DEFAULT_PARAMS_FILE,
        description="Path to the ECU simulator parameter YAML file",
    )

    use_gui_arg = DeclareLaunchArgument(
        "use_gui",
        default_value="true",
        description="Launch Gazebo with GUI (set false for headless CI)",
    )

    gateway_url_arg = DeclareLaunchArgument(
        "gateway_url",
        default_value="http://localhost:8080",
        description="Chambers gateway URL for data ingestion",
    )

    manifest_file_arg = DeclareLaunchArgument(
        "manifest_file",
        default_value=str(SCENARIO_DIR.parent.parent / "manifests" / "demo_manifest.json"),
        description="Path to Chambers preservation manifest JSON",
    )

    vehicle_id_arg = DeclareLaunchArgument(
        "vehicle_id",
        default_value="gz-vehicle-001",
        description="Unique vehicle identifier for this session",
    )

    # ---- Gazebo ----
    gz_sim = ExecuteProcess(
        cmd=[
            "gz", "sim", "-r",
            SDF_MODEL_PATH,
            "--gui-config", "",
        ],
        output="screen",
        additional_env={"GZ_SIM_RESOURCE_PATH": str(SCENARIO_DIR)},
    )

    # ---- ros_gz_bridge: bridge Gazebo topics to ROS 2 ----
    bridge_node = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_bridge",
        output="screen",
        parameters=[{
            "config_file": "",  # Using topic remapping instead
        }],
        arguments=[
            # Camera
            "/chambers/camera/front/image_raw@sensor_msgs/msg/Image[gz.msgs.Image",
            "/chambers/camera/front/camera_info@sensor_msgs/msg/CameraInfo[gz.msgs.CameraInfo",
            # LiDAR
            "/chambers/lidar/roof/points@sensor_msgs/msg/PointCloud2[gz.msgs.PointCloudPacked",
            # IMU
            "/chambers/imu/data@sensor_msgs/msg/Imu[gz.msgs.IMU",
            # NavSat (GPS)
            "/chambers/navsat/fix@sensor_msgs/msg/NavSatFix[gz.msgs.NavSat",
            # Odometry
            "/chambers/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry",
            # Cmd vel (ROS -> Gazebo)
            "/chambers/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist",
            # Joint states
            "/chambers/joint_states@sensor_msgs/msg/JointState[gz.msgs.Model",
        ],
    )

    # ---- ECU Simulator Nodes ----
    # Each ECU node simulates a vehicle domain controller and publishes
    # telemetry data that the Chambers gateway node collects.

    powertrain_ecu = Node(
        package="chambers_sim_ros",
        executable="ecu_simulator",
        name="powertrain_ecu",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "ecu_name": "powertrain",
                "ecu_domain": "powertrain",
                "publish_rate_hz": 10.0,
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "signals": [
                    "engine_rpm",
                    "engine_torque_nm",
                    "throttle_position",
                    "transmission_gear",
                    "fuel_level_pct",
                    "coolant_temp_c",
                    "oil_pressure_kpa",
                    "battery_voltage_v",
                ],
            },
        ],
        remappings=[
            ("ecu_telemetry", "/chambers/ecu/powertrain/telemetry"),
            ("diagnostic_codes", "/chambers/ecu/powertrain/dtc"),
        ],
    )

    body_ecu = Node(
        package="chambers_sim_ros",
        executable="ecu_simulator",
        name="body_ecu",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "ecu_name": "body",
                "ecu_domain": "body_control",
                "publish_rate_hz": 2.0,
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "signals": [
                    "door_lock_status",
                    "window_position_pct",
                    "headlight_state",
                    "wiper_state",
                    "exterior_temp_c",
                    "cabin_temp_c",
                    "hvac_mode",
                    "seatbelt_status",
                ],
            },
        ],
        remappings=[
            ("ecu_telemetry", "/chambers/ecu/body/telemetry"),
            ("diagnostic_codes", "/chambers/ecu/body/dtc"),
        ],
    )

    adas_ecu = Node(
        package="chambers_sim_ros",
        executable="ecu_simulator",
        name="adas_ecu",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "ecu_name": "adas",
                "ecu_domain": "advanced_driver_assist",
                "publish_rate_hz": 20.0,
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "signals": [
                    "lane_departure_warning",
                    "forward_collision_warning",
                    "blind_spot_detection",
                    "adaptive_cruise_target_speed",
                    "adaptive_cruise_distance_m",
                    "aeb_status",
                    "lane_keep_assist_active",
                    "traffic_sign_detected",
                ],
            },
        ],
        remappings=[
            ("ecu_telemetry", "/chambers/ecu/adas/telemetry"),
            ("diagnostic_codes", "/chambers/ecu/adas/dtc"),
            ("sealed_events", "/chambers/ecu/adas/sealed_events"),
        ],
    )

    infotainment_ecu = Node(
        package="chambers_sim_ros",
        executable="ecu_simulator",
        name="infotainment_ecu",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "ecu_name": "infotainment",
                "ecu_domain": "infotainment",
                "publish_rate_hz": 1.0,
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "signals": [
                    "media_source",
                    "volume_level",
                    "navigation_active",
                    "bluetooth_connected_devices",
                    "voice_command_active",
                    "display_brightness",
                    "phone_projection_mode",
                    "wifi_hotspot_clients",
                ],
            },
        ],
        remappings=[
            ("ecu_telemetry", "/chambers/ecu/infotainment/telemetry"),
            ("diagnostic_codes", "/chambers/ecu/infotainment/dtc"),
        ],
    )

    telematics_ecu = Node(
        package="chambers_sim_ros",
        executable="ecu_simulator",
        name="telematics_ecu",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "ecu_name": "telematics",
                "ecu_domain": "telematics_connectivity",
                "publish_rate_hz": 5.0,
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "signals": [
                    "cellular_signal_strength_dbm",
                    "data_usage_mb",
                    "ota_update_status",
                    "v2x_cam_broadcast_active",
                    "remote_diagnostics_session",
                    "geofence_status",
                    "ecall_status",
                    "gnss_fix_quality",
                ],
            },
        ],
        remappings=[
            ("ecu_telemetry", "/chambers/ecu/telematics/telemetry"),
            ("diagnostic_codes", "/chambers/ecu/telematics/dtc"),
        ],
    )

    # ---- Chambers Gateway Bridge Node ----
    # Collects all ECU telemetry and sensor data, applies the preservation
    # manifest, and forwards filtered data to the Chambers gateway.
    chambers_gateway_node = Node(
        package="chambers_sim_ros",
        executable="gateway_bridge",
        name="chambers_gateway_bridge",
        output="screen",
        parameters=[
            LaunchConfiguration("params_file"),
            {
                "gateway_url": LaunchConfiguration("gateway_url"),
                "manifest_file": LaunchConfiguration("manifest_file"),
                "vehicle_id": LaunchConfiguration("vehicle_id"),
                "ingest_rate_hz": 10.0,
                "batch_size": 50,
                "enable_privacy_filter": True,
                "enable_burn_on_session_end": True,
            },
        ],
        remappings=[
            # Subscribe to all ECU telemetry topics
            ("powertrain_telemetry", "/chambers/ecu/powertrain/telemetry"),
            ("body_telemetry", "/chambers/ecu/body/telemetry"),
            ("adas_telemetry", "/chambers/ecu/adas/telemetry"),
            ("infotainment_telemetry", "/chambers/ecu/infotainment/telemetry"),
            ("telematics_telemetry", "/chambers/ecu/telematics/telemetry"),
            # Subscribe to sensor topics
            ("camera_image", "/chambers/camera/front/image_raw"),
            ("lidar_points", "/chambers/lidar/roof/points"),
            ("imu_data", "/chambers/imu/data"),
            ("navsat_fix", "/chambers/navsat/fix"),
            ("odometry", "/chambers/odom"),
            # Subscribe to sealed events
            ("sealed_events", "/chambers/ecu/adas/sealed_events"),
            # Subscribe to diagnostic codes from all ECUs
            ("powertrain_dtc", "/chambers/ecu/powertrain/dtc"),
            ("body_dtc", "/chambers/ecu/body/dtc"),
            ("adas_dtc", "/chambers/ecu/adas/dtc"),
            ("infotainment_dtc", "/chambers/ecu/infotainment/dtc"),
            ("telematics_dtc", "/chambers/ecu/telematics/dtc"),
        ],
    )

    # ---- Delayed start for ECU nodes (wait for Gazebo) ----
    delayed_ecus = TimerAction(
        period=5.0,  # Wait 5 seconds for Gazebo to start
        actions=[
            LogInfo(msg="Starting ECU simulator nodes..."),
            powertrain_ecu,
            body_ecu,
            adas_ecu,
            infotainment_ecu,
            telematics_ecu,
        ],
    )

    delayed_gateway = TimerAction(
        period=7.0,  # Wait for ECUs to initialise
        actions=[
            LogInfo(msg="Starting Chambers gateway bridge..."),
            chambers_gateway_node,
        ],
    )

    # ---- Assemble launch description ----
    return LaunchDescription([
        # Arguments
        params_file_arg,
        use_gui_arg,
        gateway_url_arg,
        manifest_file_arg,
        vehicle_id_arg,

        # Log startup info
        LogInfo(msg="=== Chambers Gazebo ECU Simulation ==="),
        LogInfo(msg=["Vehicle ID: ", LaunchConfiguration("vehicle_id")]),
        LogInfo(msg=["Manifest: ", LaunchConfiguration("manifest_file")]),
        LogInfo(msg=["Gateway: ", LaunchConfiguration("gateway_url")]),

        # Gazebo
        gz_sim,

        # Bridge (start immediately, will wait for topics)
        bridge_node,

        # ECU nodes (delayed to allow Gazebo startup)
        delayed_ecus,

        # Gateway node (delayed to allow ECU startup)
        delayed_gateway,
    ])
