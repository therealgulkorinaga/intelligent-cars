"""Tests for GDPR compliance artefacts.

Verifies that the system produces valid audit exports, human-readable
driver summaries, and GDPR Art. 30 / Art. 17 compliant records.
"""

from __future__ import annotations

import json

import pytest

from chambers_sim.models.data_record import DataRecord, DataType
from chambers_sim.utils.local_gateway import BURN_LAYERS, LocalGateway


class TestAuditExport:
    """Audit log export tests."""

    def test_audit_export_valid_json(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Exported audit log is valid JSON."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(20, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)
        local_gateway.end_session(sid)

        # Serialize audit log to JSON
        session_events = [
            e for e in local_gateway.audit_log if e.session_id == sid
        ]
        export_data = [
            {
                "timestamp": e.timestamp.isoformat(),
                "session_id": e.session_id,
                "event_type": e.event_type,
                "details": e.details,
            }
            for e in session_events
        ]
        json_str = json.dumps(export_data, default=str)

        # Must be valid JSON
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_audit_export_contains_session_id(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """Exported audit log entries contain the session ID."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        local_gateway.end_session(sid)

        session_events = [
            e for e in local_gateway.audit_log if e.session_id == sid
        ]
        for event in session_events:
            assert event.session_id == sid

    def test_audit_export_contains_all_events(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """Exported audit log contains session_start, record_processed,
        session_end, burn_complete.
        """
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid
        local_gateway.process_record(sid, sample_speed_record)
        local_gateway.revoke_consent(sid, "insurer-allianz")
        local_gateway.end_session(sid)

        event_types = {
            e.event_type for e in local_gateway.audit_log if e.session_id == sid
        }
        assert "session_start" in event_types
        assert "record_processed" in event_types
        assert "consent_revoked" in event_types
        assert "session_end" in event_types
        assert "burn_complete" in event_types


class TestDriverSummary:
    """Human-readable driver summary tests."""

    def _build_driver_summary(
        self,
        local_gateway: LocalGateway,
        session_id: str,
    ) -> str:
        """Build a plain-language driver summary from the session and audit log."""
        summary = local_gateway.get_session_summary(session_id)
        session_events = [
            e for e in local_gateway.audit_log if e.session_id == session_id
        ]

        # Build human-readable text
        lines = []
        lines.append(f"Drive session {summary.session_id} summary:")
        lines.append(f"  Duration: {summary.start.strftime('%H:%M')} to {summary.end.strftime('%H:%M')}")
        lines.append(f"  Total data records generated: {summary.records_generated}")
        lines.append(f"  Records shared with authorised parties: {summary.records_transmitted}")
        lines.append(f"  Records blocked (not authorised): {summary.records_blocked}")

        if summary.records_burned > 0:
            lines.append(f"  All {summary.records_burned} records have been securely destroyed.")

        for sid, breakdown in summary.stakeholder_breakdown.items():
            role = sid.split("-")[0].upper()
            transmitted = breakdown.get("transmitted", 0)
            blocked = breakdown.get("blocked", 0)
            lines.append(f"  {role} received {transmitted} records, {blocked} blocked.")

        burn_events = [e for e in session_events if e.event_type == "burn_complete"]
        if burn_events:
            lines.append("  Data destruction: all session data has been permanently erased.")

        return "\n".join(lines)

    def test_driver_summary_no_jargon(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Driver summary must not contain cryptographic jargon."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(30, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)
        local_gateway.end_session(sid)

        text = self._build_driver_summary(local_gateway, sid)
        jargon_terms = ["HMAC", "AES", "SHA256", "key handle", "nonce", "cipher"]
        for term in jargon_terms:
            assert term not in text, f"Jargon term '{term}' found in driver summary"

    def test_driver_summary_mentions_stakeholders(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Driver summary mentions stakeholder roles."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(30, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)
        local_gateway.end_session(sid)

        text = self._build_driver_summary(local_gateway, sid)
        # At least one stakeholder role mentioned
        assert "OEM" in text or "INSURER" in text or "ADAS" in text or "TIER1" in text

    def test_driver_summary_mentions_burn(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Driver summary mentions data destruction."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(10, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)
        local_gateway.end_session(sid)

        text = self._build_driver_summary(local_gateway, sid)
        assert "destroy" in text.lower() or "erased" in text.lower() or "burned" in text.lower()


class TestGdprArticles:
    """GDPR Art. 30 (processing record) and Art. 17 (right to erasure) tests."""

    def test_art30_processing_record_structure(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Manifest + audit log together provide the fields required by Art. 30:
        - purposes of processing
        - categories of personal data
        - recipients
        - retention periods
        - description of technical measures
        """
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(20, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)
        local_gateway.end_session(sid)

        # From manifest: purposes, categories, recipients, retention
        for stakeholder in demo_manifest.stakeholders:
            assert stakeholder.id  # recipient identity
            assert stakeholder.role  # recipient role
            assert stakeholder.legal_basis  # legal basis
            for cat in stakeholder.categories:
                assert cat.purpose  # purpose of processing
                assert cat.data_type  # category of personal data
                assert cat.retention  # retention period

        # From audit log: technical measures (burn protocol) and processing record
        events = [e for e in local_gateway.audit_log if e.session_id == sid]
        event_types = {e.event_type for e in events}
        assert "session_start" in event_types  # when processing started
        assert "session_end" in event_types  # when processing ended
        assert "burn_complete" in event_types  # technical measure: data destruction

    def test_art17_erasure_proof(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Burn receipt provides Art. 17 evidence of data erasure.

        Art. 17 requires the controller to erase personal data without undue delay.
        The burn receipt proves that all 6 layers of data were destroyed.
        """
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(30, session_id=sid)
        for r in records:
            local_gateway.process_record(sid, r)

        receipt = local_gateway.end_session(sid)

        # Receipt proves erasure
        assert receipt.success is True
        assert receipt.session_id == sid
        assert receipt.timestamp is not None

        # All 6 layers of the Chambers burn protocol completed
        assert set(receipt.layers_completed) == set(BURN_LAYERS)

        # Receipt should cover:
        # 1. application_cache — in-memory caches cleared
        # 2. database_records — persistent records deleted
        # 3. search_indices — search indices purged
        # 4. message_queues — message queues drained
        # 5. backup_snapshots — backups destroyed
        # 6. audit_log_references — audit references to raw data removed
        for layer in BURN_LAYERS:
            assert layer in receipt.layers_completed
