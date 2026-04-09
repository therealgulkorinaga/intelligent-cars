"""Chambers Automotive Simulation Testbed.

Provides simulator adapters (SUMO, CARLA, ROS2) that bridge vehicle
simulators to the Chambers gateway for privacy-preserving data routing.
"""

__version__ = "0.1.0"

from chambers_sim.models.data_record import (
    BurnReceipt,
    ChannelType,
    DataRecord,
    DataType,
    FilteredDataRecord,
    Granularity,
    Jurisdiction,
    LegalBasis,
    ProcessingResult,
    SessionSummary,
)
from chambers_sim.models.manifest import PreservationManifest

__all__ = [
    "__version__",
    "BurnReceipt",
    "ChannelType",
    "DataRecord",
    "DataType",
    "FilteredDataRecord",
    "Granularity",
    "Jurisdiction",
    "LegalBasis",
    "PreservationManifest",
    "ProcessingResult",
    "SessionSummary",
]
