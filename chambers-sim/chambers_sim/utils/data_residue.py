"""Data residue comparison tool.

Compares baseline (no-Chambers) vs Chambers-filtered data to quantify
the reduction in data exposure per stakeholder and per category.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from chambers_sim.models.data_record import DataRecord, DataType, FilteredDataRecord
from chambers_sim.models.manifest import PreservationManifest
from chambers_sim.utils.local_gateway import LocalGateway

logger = structlog.get_logger(__name__)


def _estimate_bytes(obj: Any) -> int:
    """Estimate the serialized size of an object in bytes."""
    return len(json.dumps(obj, default=str).encode("utf-8"))


@dataclass
class CategoryBreakdown:
    """Per-category data residue statistics."""

    data_type: str
    records_count: int = 0
    bytes_baseline: int = 0
    bytes_chambers: int = 0

    @property
    def reduction_ratio(self) -> float:
        if self.bytes_baseline == 0:
            return 0.0
        return 1.0 - (self.bytes_chambers / self.bytes_baseline)


@dataclass
class StakeholderBreakdown:
    """Per-stakeholder data residue statistics."""

    stakeholder_id: str
    records_received_baseline: int = 0
    records_received_chambers: int = 0
    bytes_baseline: int = 0
    bytes_chambers: int = 0

    @property
    def reduction_ratio(self) -> float:
        if self.bytes_baseline == 0:
            return 0.0
        return 1.0 - (self.bytes_chambers / self.bytes_baseline)


@dataclass
class ResidueReport:
    """Full data residue comparison report."""

    total_records: int = 0
    total_bytes_generated: int = 0
    total_bytes_baseline: int = 0
    total_bytes_chambers: int = 0
    per_category: list[CategoryBreakdown] = field(default_factory=list)
    per_stakeholder: list[StakeholderBreakdown] = field(default_factory=list)

    @property
    def reduction_ratio(self) -> float:
        if self.total_bytes_baseline == 0:
            return 0.0
        return 1.0 - (self.total_bytes_chambers / self.total_bytes_baseline)


class DataResidueAnalyzer:
    """Compares data exposure with and without Chambers.

    *Baseline*: every record is forwarded in full to every stakeholder.
    *Chambers*: records are filtered through the preservation manifest.
    """

    def __init__(self) -> None:
        self._records: list[DataRecord] = []
        self._manifest: PreservationManifest | None = None
        self._baseline_results: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._chambers_results: dict[str, list[FilteredDataRecord]] = {}
        self._category_baseline: dict[str, CategoryBreakdown] = {}
        self._category_chambers: dict[str, CategoryBreakdown] = {}
        self._stakeholder_baseline: dict[str, StakeholderBreakdown] = {}
        self._stakeholder_chambers: dict[str, StakeholderBreakdown] = {}

    def run_baseline(self, records: list[DataRecord]) -> None:
        """Simulate no-Chambers: all data forwarded to all stakeholders."""
        self._records = records

        for record in records:
            dt_key = record.data_type.value

            # Baseline category tracking
            cat = self._category_baseline.setdefault(
                dt_key, CategoryBreakdown(data_type=dt_key)
            )
            record_bytes = _estimate_bytes(record.fields)
            cat.records_count += 1
            cat.bytes_baseline += record_bytes

        # In baseline mode, if a manifest is set, every stakeholder gets
        # every record in full. If no manifest, we count total exposure as
        # records * number of "potential" recipients (assume 4 stakeholders).
        if self._manifest:
            stakeholder_ids = [s.id for s in self._manifest.stakeholders]
        else:
            stakeholder_ids = ["all_stakeholders"]

        for record in records:
            record_bytes = _estimate_bytes(record.fields)
            for sid in stakeholder_ids:
                sh = self._stakeholder_baseline.setdefault(
                    sid, StakeholderBreakdown(stakeholder_id=sid)
                )
                sh.records_received_baseline += 1
                sh.bytes_baseline += record_bytes

    def run_chambers(
        self,
        records: list[DataRecord],
        manifest: PreservationManifest,
    ) -> None:
        """Simulate Chambers: gateway evaluation with manifest filtering."""
        self._records = records
        self._manifest = manifest

        gateway = LocalGateway()
        session_id = gateway.start_session(manifest.vehicle_id or "analyzer", manifest)

        for record in records:
            dt_key = record.data_type.value

            # Category tracking for Chambers
            cat = self._category_chambers.setdefault(
                dt_key, CategoryBreakdown(data_type=dt_key)
            )
            cat.records_count += 1

            result = gateway.process_record(session_id, record)

            # Sum bytes of filtered records
            chambers_bytes = 0
            for filtered in result.records_transmitted:
                fb = _estimate_bytes(filtered.fields)
                chambers_bytes += fb

                sh = self._stakeholder_chambers.setdefault(
                    filtered.stakeholder_id,
                    StakeholderBreakdown(stakeholder_id=filtered.stakeholder_id),
                )
                sh.records_received_chambers += 1
                sh.bytes_chambers += fb

            cat.bytes_chambers += chambers_bytes

        # Also run baseline if not already done
        if not self._category_baseline:
            self.run_baseline(records)

        gateway.end_session(session_id)

    def compare(self) -> ResidueReport:
        """Produce the residue comparison report."""
        # Merge category data
        all_categories: dict[str, CategoryBreakdown] = {}
        for dt_key, base_cat in self._category_baseline.items():
            merged = CategoryBreakdown(
                data_type=dt_key,
                records_count=base_cat.records_count,
                bytes_baseline=base_cat.bytes_baseline,
                bytes_chambers=self._category_chambers.get(dt_key, CategoryBreakdown(data_type=dt_key)).bytes_chambers,
            )
            all_categories[dt_key] = merged

        # Merge stakeholder data
        all_stakeholders: dict[str, StakeholderBreakdown] = {}
        all_sids = set(self._stakeholder_baseline.keys()) | set(self._stakeholder_chambers.keys())
        for sid in all_sids:
            base = self._stakeholder_baseline.get(sid, StakeholderBreakdown(stakeholder_id=sid))
            cham = self._stakeholder_chambers.get(sid, StakeholderBreakdown(stakeholder_id=sid))
            merged = StakeholderBreakdown(
                stakeholder_id=sid,
                records_received_baseline=base.records_received_baseline,
                records_received_chambers=cham.records_received_chambers,
                bytes_baseline=base.bytes_baseline,
                bytes_chambers=cham.bytes_chambers,
            )
            all_stakeholders[sid] = merged

        total_generated = sum(c.bytes_baseline for c in all_categories.values())
        total_baseline = sum(s.bytes_baseline for s in all_stakeholders.values())
        total_chambers = sum(s.bytes_chambers for s in all_stakeholders.values())

        return ResidueReport(
            total_records=len(self._records),
            total_bytes_generated=total_generated,
            total_bytes_baseline=total_baseline,
            total_bytes_chambers=total_chambers,
            per_category=list(all_categories.values()),
            per_stakeholder=list(all_stakeholders.values()),
        )

    def generate_report(self, output_path: str | Path) -> None:
        """Write a Markdown report with matplotlib charts."""
        report = self.compare()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate charts
        chart_dir = output_path.parent / "charts"
        chart_dir.mkdir(exist_ok=True)
        bar_chart_path = chart_dir / "category_comparison.png"
        waterfall_chart_path = chart_dir / "reduction_waterfall.png"

        self._generate_bar_chart(report, bar_chart_path)
        self._generate_waterfall_chart(report, waterfall_chart_path)

        # Write Markdown
        lines = [
            "# Data Residue Comparison Report",
            "",
            "## Summary",
            "",
            f"- **Total records processed**: {report.total_records}",
            f"- **Total bytes generated**: {report.total_bytes_generated:,}",
            f"- **Total bytes (baseline, no Chambers)**: {report.total_bytes_baseline:,}",
            f"- **Total bytes (with Chambers)**: {report.total_bytes_chambers:,}",
            f"- **Overall reduction**: {report.reduction_ratio:.1%}",
            "",
            "## Per-Category Breakdown",
            "",
            "| Data Type | Records | Baseline (bytes) | Chambers (bytes) | Reduction |",
            "|-----------|---------|-------------------|------------------|-----------|",
        ]

        for cat in report.per_category:
            lines.append(
                f"| {cat.data_type} | {cat.records_count} | "
                f"{cat.bytes_baseline:,} | {cat.bytes_chambers:,} | "
                f"{cat.reduction_ratio:.1%} |"
            )

        lines.extend([
            "",
            "## Per-Stakeholder Breakdown",
            "",
            "| Stakeholder | Records (Baseline) | Records (Chambers) | Bytes (Baseline) | Bytes (Chambers) | Reduction |",
            "|-------------|--------------------|--------------------|------------------|------------------|-----------|",
        ])

        for sh in report.per_stakeholder:
            lines.append(
                f"| {sh.stakeholder_id} | {sh.records_received_baseline} | "
                f"{sh.records_received_chambers} | {sh.bytes_baseline:,} | "
                f"{sh.bytes_chambers:,} | {sh.reduction_ratio:.1%} |"
            )

        lines.extend([
            "",
            "## Charts",
            "",
            f"![Category Comparison]({bar_chart_path.name})",
            "",
            f"![Reduction Waterfall]({waterfall_chart_path.name})",
            "",
        ])

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("report_generated", path=str(output_path))

    @staticmethod
    def _generate_bar_chart(report: ResidueReport, path: Path) -> None:
        """Generate a bar chart comparing baseline vs Chambers per data category."""
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
        except ImportError:
            logger.warning("matplotlib_not_available", chart="bar_chart")
            return

        categories = [c.data_type for c in report.per_category]
        baseline_vals = [c.bytes_baseline for c in report.per_category]
        chambers_vals = [c.bytes_chambers for c in report.per_category]

        x = np.arange(len(categories))
        width = 0.35

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.bar(x - width / 2, baseline_vals, width, label="Baseline (no Chambers)", color="#e74c3c")
        ax.bar(x + width / 2, chambers_vals, width, label="With Chambers", color="#2ecc71")

        ax.set_xlabel("Data Category")
        ax.set_ylabel("Bytes")
        ax.set_title("Data Exposure: Baseline vs Chambers")
        ax.set_xticks(x)
        ax.set_xticklabels(categories, rotation=45, ha="right")
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

    @staticmethod
    def _generate_waterfall_chart(report: ResidueReport, path: Path) -> None:
        """Generate a waterfall chart showing data reduction per stakeholder."""
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
        except ImportError:
            logger.warning("matplotlib_not_available", chart="waterfall_chart")
            return

        stakeholders = [s.stakeholder_id for s in report.per_stakeholder]
        reductions = [
            s.bytes_baseline - s.bytes_chambers for s in report.per_stakeholder
        ]

        fig, ax = plt.subplots(figsize=(10, 6))
        colors = ["#e74c3c" if r > 0 else "#2ecc71" for r in reductions]

        # Cumulative waterfall
        cumulative = [0.0]
        for r in reductions:
            cumulative.append(cumulative[-1] + r)

        x = np.arange(len(stakeholders))
        ax.bar(x, reductions, bottom=[cumulative[i] for i in range(len(stakeholders))], color=colors)

        # Add total bar
        total_reduction = sum(reductions)
        ax.bar(
            len(stakeholders),
            total_reduction,
            color="#3498db",
            label=f"Total reduction: {total_reduction:,} bytes",
        )

        labels = stakeholders + ["TOTAL"]
        ax.set_xticks(np.arange(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Bytes Reduced")
        ax.set_title("Data Reduction Waterfall by Stakeholder")
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
