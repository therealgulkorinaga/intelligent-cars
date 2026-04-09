"""Tests for data residue analysis.

Verifies that the Chambers architecture reduces the total data exposed
to stakeholders compared to a no-Chambers baseline.
"""

from __future__ import annotations

import pytest

from chambers_sim.models.data_record import DataRecord, DataType
from chambers_sim.models.manifest import PreservationManifest
from chambers_sim.utils.data_residue import DataResidueAnalyzer
from chambers_sim.utils.local_gateway import LocalGateway


class TestResidueComparison:
    """Data residue: Chambers vs baseline comparison."""

    def test_residue_with_chambers_less_than_baseline(
        self,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Chambers-filtered data is strictly less than baseline (all-data-to-all)."""
        records = generate_drive_session(200, session_id="residue-test")

        analyzer = DataResidueAnalyzer()
        # run_chambers first so the manifest is set before baseline runs
        analyzer.run_chambers(records, demo_manifest)
        report = analyzer.compare()

        assert report.total_bytes_chambers < report.total_bytes_baseline
        assert report.reduction_ratio > 0.0

    def test_residue_no_stakeholders_zero_output(
        self,
        no_stakeholders_manifest,
        generate_drive_session,
    ) -> None:
        """Empty manifest (no stakeholders) means zero data transmitted via Chambers."""
        records = generate_drive_session(100, session_id="zero-output-test")

        analyzer = DataResidueAnalyzer()
        analyzer.run_chambers(records, no_stakeholders_manifest)
        report = analyzer.compare()

        assert report.total_bytes_chambers == 0

    def test_residue_per_category_breakdown(
        self,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Each data category shows individual residue reduction."""
        records = generate_drive_session(300, session_id="category-test")

        analyzer = DataResidueAnalyzer()
        # run_chambers first so manifest is set before internal baseline call
        analyzer.run_chambers(records, demo_manifest)
        report = analyzer.compare()

        assert len(report.per_category) > 0

        # Categories that are undeclared (e.g. ContactSync, MediaMetadata) should
        # have zero Chambers bytes
        for cat in report.per_category:
            if cat.data_type in ("ContactSync", "MediaMetadata", "V2xCam"):
                assert cat.bytes_chambers == 0, (
                    f"Undeclared category {cat.data_type} should have 0 Chambers bytes"
                )

    def test_residue_per_stakeholder_breakdown(
        self,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Each stakeholder receives filtered data that is less than baseline.

        The DataResidueAnalyzer baseline sends every record in full to every
        declared stakeholder. The Chambers mode only sends matching categories
        with field filtering. So per-stakeholder Chambers bytes must be less
        than per-stakeholder baseline bytes.
        """
        records = generate_drive_session(200, session_id="stakeholder-test")

        analyzer = DataResidueAnalyzer()
        # run_chambers first so the manifest is set, then baseline uses manifest stakeholders
        analyzer.run_chambers(records, demo_manifest)
        report = analyzer.compare()

        assert len(report.per_stakeholder) > 0
        for sh in report.per_stakeholder:
            assert sh.bytes_chambers <= sh.bytes_baseline, (
                f"Stakeholder {sh.stakeholder_id}: Chambers bytes ({sh.bytes_chambers}) "
                f"exceeds baseline ({sh.bytes_baseline})"
            )

    def test_residue_significant_reduction(
        self,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """For a typical drive with mixed data types, Chambers achieves significant reduction.

        The baseline sends every record in full to every stakeholder.
        Chambers applies field filtering, granularity transformation, and
        blocks undeclared categories. The reduction depends on the mix of
        data types and field sizes, but should always exceed 30%.
        """
        records = generate_drive_session(500, session_id="ratio-test")

        analyzer = DataResidueAnalyzer()
        # run_chambers first so manifest is set for baseline stakeholder tracking
        analyzer.run_chambers(records, demo_manifest)
        report = analyzer.compare()

        assert report.reduction_ratio > 0.30, (
            f"Expected >30% reduction, got {report.reduction_ratio:.1%}"
        )
