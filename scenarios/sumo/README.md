# SUMO Urban Grid Scenario

10x10 urban grid network with 100 vehicles for the Chambers Automotive Simulation Testbed.

## Prerequisites

- **SUMO** (Simulation of Urban Mobility) >= 1.18.0
  - Install: https://sumo.dlr.de/docs/Installing/index.html
  - macOS: `brew install sumo`
  - Ubuntu: `sudo apt install sumo sumo-tools`
- Set `SUMO_HOME` environment variable:
  ```bash
  export SUMO_HOME=/usr/share/sumo        # Linux
  export SUMO_HOME=/opt/homebrew/share/sumo # macOS (Homebrew)
  ```
- Python 3.10+

## Network Parameters

| Parameter | Value |
|-----------|-------|
| Grid size | 10x10 intersections |
| Block length | 100 m |
| Lanes | 2 per direction |
| Speed limit | 50 km/h (13.89 m/s) |
| Junction type | Traffic light |
| Green phase | 30 s |

## Quick Start

```bash
# 1. Generate the network
python generate_network.py

# 2. Generate vehicle routes (100 vehicles, staggered over 5 min)
python generate_routes.py

# 3. Create output directory
mkdir -p output

# 4a. Run headless
sumo -c urban_100v.sumocfg

# 4b. Run with GUI
sumo-gui -c urban_100v.sumocfg
```

## Running with Chambers Adapter (TraCI)

```python
import asyncio
from chambers_sim.adapters.sumo_adapter import SumoAdapter

async def main():
    adapter = SumoAdapter(
        sumo_config_path="scenarios/sumo/urban_100v.sumocfg",
        gateway_url="http://localhost:8080",
        use_gui=True,
    )
    await adapter.connect()
    await adapter.run(duration_seconds=1800, callback=my_callback)

asyncio.run(main())
```

## Output Files

After running, find results in `output/`:
- `fcd_trace.xml` - Floating car data (positions at each timestep)
- `trip_info.xml` - Per-vehicle trip statistics
- `emissions.xml` - CO2 and fuel consumption data

## Customisation

- Edit `generate_network.py` flags to change grid size, speed limits, or lane count
- Edit `generate_routes.py` to adjust vehicle count, trip length mix, or departure window
- Edit `tls_timing.add.xml` to customise individual intersection signal timing
