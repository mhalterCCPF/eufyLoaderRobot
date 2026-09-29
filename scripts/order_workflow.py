"""Coordinates physical print cycles with an OrderAutomation client."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from controller import CycleRunner


class RobotOrderCoordinator:
    def __init__(
        self,
        runner: CycleRunner,
        order_client,
        printer_directory: str,
        wait_for_ready: Callable[[], None] | None = None,
    ) -> None:
        self.runner = runner
        self.order_client = order_client
        self.printer_directory = Path(printer_directory)
        self.wait_for_ready = wait_for_ready

    def run_cycle(self) -> int:
        position = self.runner.run_one()
        if position is None:
            raise RuntimeError("Mark at least one shelf ready before running a cycle.")
        return position

    def run_next_order(self) -> dict:
        prepared = self.order_client.prepare_next_order()
        status = prepared.get("status")
        if status == "no_orders":
            return prepared
        if status != "prepared":
            raise RuntimeError(f"Unexpected OrderAutomation status: {status!r}")

        token = prepared.get("token")
        units = prepared.get("print_units")
        if not isinstance(token, str) or not isinstance(units, list):
            raise RuntimeError("OrderAutomation returned an invalid print manifest.")
        completed = set(prepared.get("completed_units", []))
        for unit in units:
            unit_index = unit.get("index")
            if not isinstance(unit_index, int):
                raise RuntimeError("OrderAutomation returned an invalid print-unit index.")
            if unit_index in completed:
                continue
            if not self._run_print_unit(token, unit_index):
                return {"status": "paused", "order_name": prepared.get("order_name")}
            self.order_client.complete_print_unit(token, unit_index)
            if self.runner.pause_requested.is_set():
                return {"status": "paused", "order_name": prepared.get("order_name")}

        return self.order_client.complete_order(token)

    def run_batch(self, order_count: int) -> dict:
        if order_count < 1:
            raise ValueError("Batch order count must be a positive integer.")
        completed_orders = 0
        for _ in range(order_count):
            result = self.run_next_order()
            if result.get("status") == "no_orders":
                return {"status": "no_orders", "completed_orders": completed_orders}
            if result.get("status") == "paused":
                return {"status": "paused", "completed_orders": completed_orders}
            if result.get("status") != "completed":
                raise RuntimeError(f"Unexpected order result: {result.get('status')!r}")
            completed_orders += 1
        return {"status": "completed", "completed_orders": completed_orders}

    def run_continuous(self) -> dict:
        completed_orders = 0
        while not self.runner.pause_requested.is_set():
            result = self.run_next_order()
            if result.get("status") == "no_orders":
                return {"status": "no_orders", "completed_orders": completed_orders}
            if result.get("status") == "paused":
                return {"status": "paused", "completed_orders": completed_orders}
            if result.get("status") != "completed":
                raise RuntimeError(f"Unexpected order result: {result.get('status')!r}")
            completed_orders += 1
        return {"status": "paused", "completed_orders": completed_orders}

    def _run_print_unit(self, token: str, unit_index: int) -> bool:
        def stage_before_print() -> None:
            response = self.order_client.stage_print_unit(token, unit_index)
            if response.get("status") != "staged":
                raise RuntimeError("OrderAutomation did not confirm print-file staging.")
            expected = {
                (self.printer_directory / "picture.png").resolve(),
                (self.printer_directory / "frame.png").resolve(),
            }
            actual = {Path(path).resolve() for path in response.get("files", [])}
            if actual != expected:
                raise RuntimeError("OrderAutomation staged files in an unexpected printer directory.")
            for path in expected:
                if not path.is_file() or not path.read_bytes():
                    raise RuntimeError(f"Expected print file is missing or empty: {path}")

        while True:
            while not any(self.runner.substrate_ready):
                if self.runner.pause_requested.is_set():
                    return False
                if self.runner.immediate_stop_requested.is_set():
                    raise RuntimeError("Run stopped immediately; Home is required.")
                if self.wait_for_ready is None:
                    raise RuntimeError("Mark a shelf ready before processing this print unit.")
                self.wait_for_ready()
            position = self.runner.run_one(before_print=stage_before_print)
            if position is not None:
                return True
