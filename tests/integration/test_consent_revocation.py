"""Tests for mid-session consent revocation.

Verifies that revoking a stakeholder's consent immediately stops data
flow to that stakeholder while leaving others unaffected.
"""

from __future__ import annotations

import pytest

from chambers_sim.models.data_record import DataRecord, DataType
from chambers_sim.utils.local_gateway import LocalGateway


class TestConsentRevocation:
    """Mid-session consent revocation tests."""

    def test_revoke_insurer_mid_session(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """After revoking insurer consent, insurer receives no more data."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid

        # Before revocation — insurer receives data
        result_before = local_gateway.process_record(sid, sample_driving_behaviour_record)
        insurer_before = [
            r for r in result_before.records_transmitted
            if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_before) == 1

        # Revoke consent
        local_gateway.revoke_consent(sid, "insurer-allianz")

        # After revocation — insurer blocked
        result_after = local_gateway.process_record(sid, sample_driving_behaviour_record)
        insurer_after = [
            r for r in result_after.records_transmitted
            if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_after) == 0
        assert "insurer-allianz" in result_after.records_blocked

    def test_revoke_does_not_affect_other_stakeholders(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """Revoking insurer does not affect OEM or Tier-1 data flow."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sensor_health_record.session_id = sid

        # Revoke insurer
        local_gateway.revoke_consent(sid, "insurer-allianz")

        # OEM still receives sensor health
        result = local_gateway.process_record(sid, sample_sensor_health_record)
        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        tier1_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        assert len(oem_records) == 1
        assert len(tier1_records) == 1

    def test_revoke_audit_logged(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """Consent revocation events appear in the audit log."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        local_gateway.revoke_consent(sid, "insurer-allianz")

        revocation_events = [
            e for e in local_gateway.audit_log
            if e.session_id == sid and e.event_type == "consent_revoked"
        ]
        assert len(revocation_events) == 1
        assert revocation_events[0].details["stakeholder_id"] == "insurer-allianz"

    def test_revoke_idempotent(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """Revoking the same stakeholder twice does not raise an error."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid

        # First revocation
        local_gateway.revoke_consent(sid, "insurer-allianz")
        # Second revocation — no error
        local_gateway.revoke_consent(sid, "insurer-allianz")

        # Insurer still blocked
        result = local_gateway.process_record(sid, sample_driving_behaviour_record)
        insurer_records = [
            r for r in result.records_transmitted
            if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 0

    def test_revoke_nonexistent_stakeholder(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """Revoking an unknown stakeholder is handled gracefully (no crash)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid

        # Revoke a stakeholder not in the manifest — should not raise
        local_gateway.revoke_consent(sid, "nonexistent-stakeholder-xyz")

        # Normal processing still works
        result = local_gateway.process_record(sid, sample_speed_record)
        assert len(result.records_transmitted) >= 1
