"""Standard-library HTTP client for OrderAutomation's Conductor API."""

from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 100 * 1024 * 1024


class ConductorClient:
    def __init__(self, host: str, port: int, token: str, timeout_seconds: float = 60):
        self.base_url = f"http://{host}:{int(port)}/api/v1"
        self.token = token
        self.timeout_seconds = timeout_seconds

    def register(self, loader_id: str, status: str) -> dict:
        return self._json_request("POST", "/loaders/register", {
            "loader_id": loader_id,
            "status": status,
        })

    def update_status(self, loader_id: str, status: str, **details) -> dict:
        return self._json_request(
            "POST",
            f"/loaders/{quote(loader_id, safe='')}/state",
            {"status": status, **details},
        )

    def get_assignment(self, loader_id: str) -> dict | None:
        path = f"/loaders/{quote(loader_id, safe='')}/assignment"
        try:
            data = self._request("GET", path)
        except HTTPError as error:
            if error.code == 204:
                return None
            raise
        if not data:
            return None
        response = json.loads(data)
        return response.get("assignment")

    def download_bundle(self, loader_id: str) -> bytes:
        path = f"/loaders/{quote(loader_id, safe='')}/assignment/bundle"
        data = self._request(
            "GET", path, content_type="application/zip", max_bytes=MAX_RESPONSE_BYTES
        )
        if not data:
            raise RuntimeError("Conductor returned an empty assignment bundle.")
        return data

    def complete_assignment(self, loader_id: str, order_id: str, status: str) -> dict:
        return self._json_request(
            "POST",
            f"/loaders/{quote(loader_id, safe='')}/assignments/{quote(order_id, safe='')}/complete",
            {"status": status},
        )

    def complete_print_unit(self, loader_id: str, order_id: str, unit_index: int) -> dict:
        return self._json_request(
            "POST",
            f"/loaders/{quote(loader_id, safe='')}/assignments/{quote(order_id, safe='')}/unit-complete",
            {"unit_index": unit_index},
        )

    def report_interrupted(self, loader_id: str, order_id: str, reason: str) -> dict:
        return self._json_request(
            "POST",
            f"/loaders/{quote(loader_id, safe='')}/assignments/{quote(order_id, safe='')}/interrupted",
            {"reason": reason},
        )

    def _json_request(self, method: str, path: str, payload: dict) -> dict:
        response = self._request(method, path, json.dumps(payload).encode("utf-8"))
        return json.loads(response) if response else {}

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type="application/json",
        max_bytes: int = 1024 * 1024,
    ) -> bytes:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": content_type,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise RuntimeError("Conductor response exceeds the configured size limit.")
                return data
        except HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")
            if error.code == 204:
                raise
            raise RuntimeError(f"Conductor returned HTTP {error.code}: {message}") from error
