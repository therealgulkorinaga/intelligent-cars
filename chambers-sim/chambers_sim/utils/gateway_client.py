"""Client for communicating with the Chambers gateway (remote or local)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from chambers_sim.models.data_record import (
    BurnReceipt,
    DataRecord,
    ProcessingResult,
    SessionSummary,
)
from chambers_sim.models.manifest import PreservationManifest

logger = structlog.get_logger(__name__)


class GatewayClient:
    """Async HTTP client for the Chambers gateway REST API.

    When *local_gateway* is provided, calls are dispatched in-process
    without HTTP, enabling standalone testing with no Rust gateway.
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:8080",
        local_gateway: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self._local = local_gateway
        self._http: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.gateway_url,
                timeout=self._timeout,
            )
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ---- Session lifecycle ----

    async def start_session(
        self,
        vehicle_id: str,
        manifest: PreservationManifest,
    ) -> str:
        """Start a new data session, returning the session_id."""
        if self._local is not None:
            return self._local.start_session(vehicle_id, manifest)

        client = await self._client()
        resp = await client.post(
            "/api/v1/sessions",
            json={
                "vehicle_id": vehicle_id,
                "manifest": manifest.model_dump(mode="json"),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        session_id = data["session_id"]
        logger.info("session_started", session_id=session_id, vehicle_id=vehicle_id)
        return session_id

    async def send_record(
        self,
        session_id: str,
        record: DataRecord,
    ) -> ProcessingResult:
        """Send a single data record through the gateway for evaluation."""
        if self._local is not None:
            return self._local.process_record(session_id, record)

        client = await self._client()
        resp = await client.post(
            f"/api/v1/sessions/{session_id}/records",
            json=record.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return ProcessingResult.model_validate(resp.json())

    async def send_records(
        self,
        session_id: str,
        records: list[DataRecord],
    ) -> list[ProcessingResult]:
        """Send multiple records, returning a result per record."""
        results = []
        for record in records:
            result = await self.send_record(session_id, record)
            results.append(result)
        return results

    async def end_session(self, session_id: str) -> BurnReceipt:
        """End a session and trigger the burn protocol."""
        if self._local is not None:
            return self._local.end_session(session_id)

        client = await self._client()
        resp = await client.post(f"/api/v1/sessions/{session_id}/end")
        resp.raise_for_status()
        return BurnReceipt.model_validate(resp.json())

    async def revoke_consent(self, session_id: str, stakeholder_id: str) -> None:
        """Revoke consent for a stakeholder within an active session."""
        if self._local is not None:
            self._local.revoke_consent(session_id, stakeholder_id)
            return

        client = await self._client()
        resp = await client.post(
            f"/api/v1/sessions/{session_id}/revoke",
            json={"stakeholder_id": stakeholder_id},
        )
        resp.raise_for_status()

    async def get_session_summary(self, session_id: str) -> SessionSummary:
        """Retrieve the summary for a session."""
        if self._local is not None:
            return self._local.get_session_summary(session_id)

        client = await self._client()
        resp = await client.get(f"/api/v1/sessions/{session_id}/summary")
        resp.raise_for_status()
        return SessionSummary.model_validate(resp.json())
