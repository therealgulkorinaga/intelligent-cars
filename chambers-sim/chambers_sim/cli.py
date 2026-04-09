"""Click CLI for the Chambers Automotive Simulation Testbed."""

from __future__ import annotations

import asyncio
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.table import Table

from chambers_sim.models.data_record import ChannelType, DataRecord, DataType
from chambers_sim.models.manifest import PreservationManifest
from chambers_sim.utils.data_residue import DataResidueAnalyzer
from chambers_sim.utils.gateway_client import GatewayClient
from chambers_sim.utils.local_gateway import LocalGateway

logger = structlog.get_logger(__name__)
console = Console()


def _load_manifest(manifest_path: str | None) -> PreservationManifest:
    """Load manifest from file or use the default demo manifest."""
    if manifest_path:
        return PreservationManifest.from_file(manifest_path)
    return PreservationManifest.default_demo_manifest()


@click.group()
@click.version_option(version="0.1.0", prog_name="chambers-sim")
def cli() -> None:
    """Chambers Automotive Simulation Testbed.

    Run vehicle simulators with privacy-preserving data routing.
    """


@cli.command()
@click.option("--config", "sumo_config", required=True, help="Path to SUMO .sumocfg file")
@click.option("--manifest", "manifest_path", default=None, help="Path to manifest JSON")
@click.option("--duration", default=300, type=float, help="Simulation duration in seconds")
@click.option("--port", default=8813, type=int, help="TraCI port")
@click.option("--gui", is_flag=True, help="Use sumo-gui instead of sumo")
def sumo(
    sumo_config: str,
    manifest_path: str | None,
    duration: float,
    port: int,
    gui: bool,
) -> None:
    """Run a SUMO traffic simulation scenario."""
    from chambers_sim.adapters.sumo_adapter import SumoAdapter

    manifest = _load_manifest(manifest_path)
    gateway = LocalGateway()
    client = GatewayClient(local_gateway=gateway)

    adapter = SumoAdapter(
        sumo_config_path=sumo_config,
        port=port,
        use_gui=gui,
    )

    session_id: str | None = None
    records_total = 0

    async def run() -> None:
        nonlocal session_id, records_total

        await adapter.connect()
        session_id = await client.start_session("sumo-fleet", manifest)

        async def on_records(records: list[DataRecord]) -> None:
            nonlocal records_total
            for record in records:
                record.session_id = session_id  # type: ignore[assignment]
                await client.send_record(session_id, record)  # type: ignore[arg-type]
            records_total += len(records)

        console.print(f"[bold green]Starting SUMO simulation[/bold green] ({duration}s)")
        await adapter.run(duration, on_records)

        receipt = await client.end_session(session_id)  # type: ignore[arg-type]
        summary = await client.get_session_summary(session_id)  # type: ignore[arg-type]
        await client.close()

        _print_summary(summary, receipt)

    asyncio.run(run())


@cli.command()
@click.option("--town", default="Town01", help="CARLA town to load")
@click.option("--manifest", "manifest_path", default=None, help="Path to manifest JSON")
@click.option("--duration", default=60, type=float, help="Simulation duration in seconds")
@click.option("--host", default="localhost", help="CARLA server host")
@click.option("--port", default=2000, type=int, help="CARLA server port")
def carla(
    town: str,
    manifest_path: str | None,
    duration: float,
    host: str,
    port: int,
) -> None:
    """Run a CARLA driving simulation scenario."""
    from chambers_sim.adapters.carla_adapter import CarlaAdapter

    manifest = _load_manifest(manifest_path)
    gateway = LocalGateway()
    client = GatewayClient(local_gateway=gateway)

    adapter = CarlaAdapter(host=host, port=port, town=town)

    async def run() -> None:
        await adapter.connect()
        session_id = await adapter.start_session("carla-ego")
        gw_session = await client.start_session("carla-ego", manifest)

        adapter.setup_ego_vehicle()
        adapter.setup_sensors()

        async def on_records(records: list[DataRecord]) -> None:
            for record in records:
                record.session_id = gw_session
                await client.send_record(gw_session, record)

        console.print(f"[bold green]Starting CARLA simulation[/bold green] in {town} ({duration}s)")
        await adapter.run(duration, on_records)

        await adapter.end_session("carla-ego")
        receipt = await client.end_session(gw_session)
        summary = await client.get_session_summary(gw_session)
        await client.close()

        _print_summary(summary, receipt)

    asyncio.run(run())


@cli.command()
@click.option("--vehicles", default=5, type=int, help="Number of synthetic vehicles")
@click.option("--duration", default=60, type=int, help="Duration in seconds (steps)")
@click.option("--manifest", "manifest_path", default=None, help="Path to manifest JSON")
@click.option("--output", "output_path", default=None, help="Path to write residue report")
def demo(
    vehicles: int,
    duration: int,
    manifest_path: str | None,
    output_path: str | None,
) -> None:
    """Run a demo with synthetic data (no simulator required)."""
    manifest = _load_manifest(manifest_path)
    gateway = LocalGateway()
    client = GatewayClient(local_gateway=gateway)

    console.print(
        f"[bold green]Running synthetic demo[/bold green]: "
        f"{vehicles} vehicles, {duration} steps"
    )

    async def run() -> None:
        session_id = await client.start_session("demo-fleet", manifest)
        all_records: list[DataRecord] = []

        for step in range(duration):
            for v in range(vehicles):
                vid = f"vehicle_{v:03d}"
                records = _generate_synthetic_records(session_id, vid, step)
                all_records.extend(records)
                for record in records:
                    await client.send_record(session_id, record)

            if (step + 1) % 10 == 0:
                console.print(f"  Step {step + 1}/{duration}")

        receipt = await client.end_session(session_id)
        summary = await client.get_session_summary(session_id)
        await client.close()

        _print_summary(summary, receipt)

        # Optional residue report
        if output_path:
            analyzer = DataResidueAnalyzer()
            analyzer.run_chambers(all_records, manifest)
            analyzer.generate_report(output_path)
            console.print(f"\n[bold]Residue report written to:[/bold] {output_path}")

    asyncio.run(run())


@cli.command()
@click.option("--manifest", "manifest_path", default=None, help="Path to manifest JSON")
@click.option("--records", "records_path", required=True, help="Path to records JSON file")
@click.option("--output", "output_path", default="residue_report.md", help="Output report path")
def residue(
    manifest_path: str | None,
    records_path: str,
    output_path: str,
) -> None:
    """Run data residue comparison analysis."""
    manifest = _load_manifest(manifest_path)

    # Load records from JSON
    records_data = json.loads(Path(records_path).read_text(encoding="utf-8"))
    records = [DataRecord.model_validate(r) for r in records_data]

    console.print(f"[bold green]Analyzing data residue[/bold green]: {len(records)} records")

    analyzer = DataResidueAnalyzer()
    analyzer.run_chambers(records, manifest)
    report = analyzer.compare()

    # Print summary table
    table = Table(title="Data Residue Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total records", str(report.total_records))
    table.add_row("Bytes generated", f"{report.total_bytes_generated:,}")
    table.add_row("Bytes baseline", f"{report.total_bytes_baseline:,}")
    table.add_row("Bytes with Chambers", f"{report.total_bytes_chambers:,}")
    table.add_row("Reduction", f"{report.reduction_ratio:.1%}")
    console.print(table)

    analyzer.generate_report(output_path)
    console.print(f"\n[bold]Full report written to:[/bold] {output_path}")


def _generate_synthetic_records(
    session_id: str, vehicle_id: str, step: int
) -> list[DataRecord]:
    """Generate synthetic vehicle data records for one timestep."""
    now = datetime.now(timezone.utc)
    records: list[DataRecord] = []

    # Position
    lat = 51.5074 + step * 0.0002 + random.gauss(0, 0.0001)
    lon = -0.1278 + step * 0.0001 + random.gauss(0, 0.0001)
    records.append(
        DataRecord(
            session_id=session_id,
            timestamp=now,
            source=f"synthetic:{vehicle_id}",
            data_type=DataType.POSITION,
            fields={
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "altitude": round(30 + random.gauss(0, 1), 1),
                "heading": round(random.uniform(0, 360), 1),
            },
            channel=ChannelType.CELLULAR,
        )
    )

    # Speed
    speed = max(0, 15 + 10 * math.sin(step * 0.1) + random.gauss(0, 2))
    records.append(
        DataRecord(
            session_id=session_id,
            timestamp=now,
            source=f"synthetic:{vehicle_id}",
            data_type=DataType.SPEED,
            fields={
                "speed_mps": round(speed, 3),
                "speed_limit": 50.0,
                "road_type": "urban",
            },
            channel=ChannelType.CELLULAR,
        )
    )

    # Acceleration
    accel = random.gauss(0, 2)
    records.append(
        DataRecord(
            session_id=session_id,
            timestamp=now,
            source=f"synthetic:{vehicle_id}",
            data_type=DataType.ACCELERATION,
            fields={
                "longitudinal": round(accel, 3),
                "lateral": round(random.gauss(0, 0.5), 3),
                "vertical": round(random.gauss(0, 0.1), 3),
            },
            channel=ChannelType.CELLULAR,
        )
    )

    # Driving behaviour (every 30 steps)
    if step % 30 == 0 and step > 0:
        records.append(
            DataRecord(
                session_id=session_id,
                timestamp=now,
                source=f"synthetic:{vehicle_id}",
                data_type=DataType.DRIVING_BEHAVIOUR,
                fields={
                    "score": round(random.uniform(60, 100), 1),
                    "harsh_braking_count": random.randint(0, 3),
                    "harsh_accel_count": random.randint(0, 2),
                    "distance_km": round(step * 0.015, 2),
                    "duration_minutes": round(step / 60, 1),
                    "time_of_day_bucket": "midday",
                    "raw_speed_trace": [round(random.uniform(10, 30), 1) for _ in range(5)],
                    "raw_accel_trace": [round(random.gauss(0, 2), 2) for _ in range(5)],
                },
                channel=ChannelType.CELLULAR,
            )
        )

    # Sensor health (every 15 steps)
    if step % 15 == 0:
        records.append(
            DataRecord(
                session_id=session_id,
                timestamp=now,
                source=f"synthetic:{vehicle_id}",
                data_type=DataType.SENSOR_HEALTH,
                fields={
                    "sensor_id": "powertrain",
                    "status": "ok",
                    "temperature": round(85 + random.gauss(0, 3), 1),
                    "uptime_hours": round(step / 3600, 2),
                    "error_count": 0,
                },
                channel=ChannelType.CELLULAR,
            )
        )

    # Diagnostic code (every 50 steps)
    if step % 50 == 0 and step > 0:
        records.append(
            DataRecord(
                session_id=session_id,
                timestamp=now,
                source=f"synthetic:{vehicle_id}",
                data_type=DataType.DIAGNOSTIC_CODE,
                fields={
                    "dtc_code": random.choice(["P0300", "P0171", "P0420", "P0440"]),
                    "severity": random.choice(["low", "moderate"]),
                    "module": random.choice(["engine", "fuel_system", "exhaust"]),
                    "mileage_km": round(random.uniform(10000, 50000)),
                    "vin": "WBA00000000000000",
                    "driver_id": "driver-001",
                },
                channel=ChannelType.CELLULAR,
            )
        )

    return records


def _print_summary(summary: Any, receipt: Any) -> None:
    """Pretty-print the session summary and burn receipt."""
    table = Table(title="Session Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Session ID", summary.session_id)
    table.add_row("Records generated", str(summary.records_generated))
    table.add_row("Records transmitted", str(summary.records_transmitted))
    table.add_row("Records blocked", str(summary.records_blocked))
    table.add_row("Records burned", str(summary.records_burned))
    console.print(table)

    if summary.stakeholder_breakdown:
        sh_table = Table(title="Stakeholder Breakdown")
        sh_table.add_column("Stakeholder", style="bold")
        sh_table.add_column("Transmitted", justify="right")
        sh_table.add_column("Blocked", justify="right")
        for sid, counts in summary.stakeholder_breakdown.items():
            sh_table.add_row(
                sid,
                str(counts.get("transmitted", 0)),
                str(counts.get("blocked", 0)),
            )
        console.print(sh_table)

    console.print(f"\n[bold]Burn receipt:[/bold] {'SUCCESS' if receipt.success else 'FAILED'}")
    console.print(f"  Layers completed: {', '.join(receipt.layers_completed)}")


if __name__ == "__main__":
    cli()
