"""Subprocess client for OrderAutomation's versioned JSON CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from robot_config import DEFAULT_CONFIG

PROTOCOL_VERSION = 1
CONFIG_KEYS = set(DEFAULT_CONFIG) - {"orderautomation_path", "orderautomation_python"}


class OrderAutomationClient:
    def __init__(
        self,
        repository_path: str,
        python_path: str = "",
        runtime_config: dict | None = None,
        timeout_seconds: float = 300,
    ) -> None:
        self.repository_path = Path(repository_path).expanduser().resolve()
        self.python_path = python_path
        self.runtime_config = runtime_config or {}
        self.timeout_seconds = timeout_seconds

    def prepare_next_order(self) -> dict:
        return self._request("prepare_next_order")

    def stage_print_unit(self, token: str, unit_index: int) -> dict:
        return self._request("stage_print_unit", token=token, unit_index=unit_index)

    def complete_print_unit(self, token: str, unit_index: int) -> dict:
        return self._request("complete_print_unit", token=token, unit_index=unit_index)

    def complete_order(self, token: str) -> dict:
        return self._request("complete_order", token=token)

    def _request(self, action: str, **values) -> dict:
        cli_path = self.repository_path / "modules" / "order_automation_cli.py"
        if not cli_path.is_file():
            raise FileNotFoundError(f"OrderAutomation CLI was not found under {self.repository_path}")
        interpreter = self._interpreter_path()
        request = {
            "version": PROTOCOL_VERSION,
            "action": action,
            "config": {key: self.runtime_config[key] for key in CONFIG_KEYS if key in self.runtime_config},
            **values,
        }
        try:
            completed = subprocess.run(
                [str(interpreter), "-m", "modules.order_automation_cli"],
                cwd=self.repository_path,
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"OrderAutomation {action} timed out.") from error
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            details = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise RuntimeError(f"OrderAutomation returned invalid JSON: {details}") from error
        if not isinstance(response, dict) or response.get("version") != PROTOCOL_VERSION:
            raise RuntimeError("OrderAutomation returned an unsupported response format.")
        if completed.returncode != 0 or response.get("status") == "error":
            error = response.get("error", {})
            message = error.get("message") or completed.stderr.strip() or "unknown error"
            raise RuntimeError(f"OrderAutomation {action} failed: {message}")
        if not isinstance(response.get("status"), str):
            raise RuntimeError("OrderAutomation response is missing its status.")
        return response

    def _interpreter_path(self) -> Path:
        if self.python_path.strip():
            return Path(self.python_path).expanduser().resolve()
        environment_python = self.repository_path / ".venv" / "Scripts" / "python.exe"
        if environment_python.is_file():
            return environment_python
        return Path(sys.executable)