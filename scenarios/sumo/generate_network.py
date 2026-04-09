#!/usr/bin/env python3
"""Generate a 10x10 urban grid network for SUMO using netgenerate.

This script calls SUMO's netgenerate tool to produce a grid network file
suitable for the Chambers Automotive Simulation Testbed.

Network parameters:
    - 10x10 intersections (100 total)
    - 100m block length in both x and y
    - 2 lanes per direction
    - Speed limit: 50 km/h (13.89 m/s)
    - Traffic-light junctions with 30s green phases

Usage:
    python generate_network.py [--output urban_grid.net.xml] [--sumo-home /path/to/sumo]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Default network parameters
GRID_X_NUMBER = 10
GRID_Y_NUMBER = 10
GRID_X_LENGTH = 100  # metres per block
GRID_Y_LENGTH = 100
DEFAULT_LANE_NUMBER = 2
DEFAULT_SPEED = 13.89  # 50 km/h in m/s
JUNCTION_TYPE = "traffic_light"

# Traffic light timing: 30s green per phase
TLS_GREEN_TIME = 30
TLS_YELLOW_TIME = 4
TLS_RED_TIME = 2  # all-red clearance


def find_netgenerate() -> str:
    """Locate the SUMO netgenerate binary."""
    # Check SUMO_HOME environment variable first
    sumo_home = os.environ.get("SUMO_HOME", "")
    if sumo_home:
        candidate = os.path.join(sumo_home, "bin", "netgenerate")
        if os.path.isfile(candidate):
            return candidate

    # Fall back to PATH
    binary = shutil.which("netgenerate")
    if binary:
        return binary

    raise FileNotFoundError(
        "Could not find 'netgenerate'. "
        "Ensure SUMO is installed and SUMO_HOME is set, "
        "or that 'netgenerate' is on your PATH."
    )


def generate_tls_additional_file(output_dir: Path, net_file: str) -> Path | None:
    """Generate an additional file to configure traffic light timing.

    netgenerate creates default TLS programs; this function produces an
    additional file that overrides the phase durations to use 30s green.
    The file is applied during a netconvert post-processing step.
    """
    # We use a type file to set default TLS durations.  netgenerate's
    # --tls.green.time flag handles this directly, so we only write the
    # additional file as a reference for later manual tuning.
    additional_path = output_dir / "tls_timing.add.xml"
    additional_content = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!-- Auto-generated TLS timing reference for Chambers SUMO scenario.
     Phase durations: green={TLS_GREEN_TIME}s, yellow={TLS_YELLOW_TIME}s,
     all-red={TLS_RED_TIME}s.
     These defaults are applied via netgenerate flags.  Edit this file
     to customise individual intersection timings. -->
<additional>
    <!-- Example override for a specific junction:
    <tlLogic id="A0B0" type="static" programID="custom" offset="0">
        <phase duration="{TLS_GREEN_TIME}" state="GGGgrrrrGGGgrrrr"/>
        <phase duration="{TLS_YELLOW_TIME}" state="yyygrrrryyygrrrr"/>
        <phase duration="{TLS_RED_TIME}" state="rrrGrrrrrrrGrrrr"/>
        <phase duration="{TLS_GREEN_TIME}" state="rrrrGGGgrrrrGGGg"/>
        <phase duration="{TLS_YELLOW_TIME}" state="rrrryyygrrrryyYg"/>
        <phase duration="{TLS_RED_TIME}" state="rrrrrrrGrrrrrrrG"/>
    </tlLogic>
    -->
</additional>
"""
    additional_path.write_text(additional_content, encoding="utf-8")
    print(f"  TLS timing reference written to: {additional_path}")
    return additional_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a 10x10 urban grid network for SUMO."
    )
    parser.add_argument(
        "--output",
        default="urban_grid.net.xml",
        help="Output network file name (default: urban_grid.net.xml)",
    )
    parser.add_argument(
        "--sumo-home",
        default=None,
        help="Path to SUMO installation (overrides SUMO_HOME env var)",
    )
    parser.add_argument(
        "--grid-x", type=int, default=GRID_X_NUMBER,
        help=f"Number of junctions in x direction (default: {GRID_X_NUMBER})",
    )
    parser.add_argument(
        "--grid-y", type=int, default=GRID_Y_NUMBER,
        help=f"Number of junctions in y direction (default: {GRID_Y_NUMBER})",
    )
    parser.add_argument(
        "--block-length", type=int, default=GRID_X_LENGTH,
        help=f"Block length in metres (default: {GRID_X_LENGTH})",
    )
    args = parser.parse_args()

    if args.sumo_home:
        os.environ["SUMO_HOME"] = args.sumo_home

    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / args.output

    try:
        netgenerate = find_netgenerate()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Using netgenerate: {netgenerate}")
    print(f"Generating {args.grid_x}x{args.grid_y} grid network...")
    print(f"  Block size: {args.block_length}m x {args.block_length}m")
    print(f"  Lanes: {DEFAULT_LANE_NUMBER} per direction")
    print(f"  Speed limit: {DEFAULT_SPEED} m/s ({DEFAULT_SPEED * 3.6:.0f} km/h)")
    print(f"  Junction type: {JUNCTION_TYPE}")

    cmd = [
        netgenerate,
        "--grid",
        "--grid.x-number", str(args.grid_x),
        "--grid.y-number", str(args.grid_y),
        "--grid.x-length", str(args.block_length),
        "--grid.y-length", str(args.block_length),
        "--default.lanenumber", str(DEFAULT_LANE_NUMBER),
        "--default.speed", str(DEFAULT_SPEED),
        "--default-junction-type", JUNCTION_TYPE,
        # Traffic light green phase duration
        "--tls.green.time", str(TLS_GREEN_TIME),
        "--tls.yellow.time", str(TLS_YELLOW_TIME),
        # Turn-around lanes at grid boundary
        "--turn-lanes", "1",
        "--output-file", str(output_path),
    ]

    print(f"\nCommand: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            # netgenerate writes progress to stderr; print it informatively
            print(result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: netgenerate failed with return code {e.returncode}", file=sys.stderr)
        if e.stdout:
            print(e.stdout, file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        sys.exit(1)

    if output_path.exists():
        size_kb = output_path.stat().st_size / 1024
        print(f"Network file generated: {output_path} ({size_kb:.1f} KB)")
    else:
        print("ERROR: Output file was not created.", file=sys.stderr)
        sys.exit(1)

    # Generate TLS timing reference file
    generate_tls_additional_file(script_dir, args.output)

    print("\nDone. Next steps:")
    print(f"  1. Generate routes: python generate_routes.py")
    print(f"  2. Run simulation:  sumo -c urban_100v.sumocfg")


if __name__ == "__main__":
    main()
