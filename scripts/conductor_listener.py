"""Listen-mode worker that receives and runs Conductor assignments."""

from __future__ import annotations

import io
import json
import os
import stat
import threading
import time
import zipfile
from pathlib import Path

from controller import CycleRunner

MAX_BUNDLE_BYTES = 100 * 1024 * 1024
HEARTBEAT_SECONDS = 10


class RobotConductorListener:
    def __init__(
        self,
        runner: CycleRunner,
        client,
        loader_id: str,
        printer_directory: str,
        stop_event: threading.Event,
        readiness_event: threading.Event,
        on_progress=None,
    ) -> None:
        self.runner = runner
        self.client = client
        self.loader_id = loader_id
        self.printer_directory = Path(printer_directory)
        self.stop_event = stop_event
        self.readiness_event = readiness_event
        self.on_progress = on_progress or (lambda _message: None)
        self.interrupted = False

    def run(self) -> None:
        status = self._readiness_status()
        self.client.register(self.loader_id, status)
        last_heartbeat = 0.0
        last_status = None
        self.on_progress(f"Listening to Conductor as {self.loader_id}")

        while not self.stop_event.is_set():
            if self.interrupted:
                self._heartbeat("interrupted", last_heartbeat)
                last_heartbeat = time.monotonic()
                self.stop_event.wait(HEARTBEAT_SECONDS)
                continue

            status = self._readiness_status()
            if status != last_status or time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                try:
                    self.client.update_status(
                        self.loader_id,
                        status,
                        ready_shelves=sum(self.runner.substrate_ready),
                    )
                    last_heartbeat = time.monotonic()
                    last_status = status
                except Exception as error:
                    self.on_progress(f"Conductor connection unavailable: {error}")
                    self.stop_event.wait(2)
                    continue

            self.readiness_event.clear()
            if status != "ready":
                self.readiness_event.wait(1)
                continue

            try:
                assignment = self.client.get_assignment(self.loader_id)
            except Exception as error:
                self.on_progress(f"Conductor connection unavailable: {error}")
                self.stop_event.wait(2)
                continue
            if assignment is None:
                self.stop_event.wait(1)
                continue
            self._process_assignment(assignment)
            last_heartbeat = time.monotonic()

        if not self.interrupted:
            try:
                self.client.update_status(
                    self.loader_id,
                    "unavailable",
                    ready_shelves=sum(self.runner.substrate_ready),
                )
            except Exception:
                pass

    def _process_assignment(self, assignment: dict) -> None:
        order_id = assignment.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            self.interrupted = True
            self.on_progress("Conductor sent an invalid assignment")
            return
        heartbeat_stop = threading.Event()
        heartbeat_thread = None
        try:
            self.client.update_status(self.loader_id, "busy", order_id=order_id)
            heartbeat_thread = threading.Thread(
                target=self._busy_heartbeat,
                args=(heartbeat_stop, order_id),
                name="conductor-busy-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
            self.on_progress(f"Receiving Order {assignment.get('order_name', order_id)}")
            bundle_data = self.client.download_bundle(self.loader_id)
            manifest, archive = self._open_bundle(bundle_data, order_id, assignment)
            manifest["completed_units"] = assignment.get("completed_units", [])
            self._run_units(manifest, archive)
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
            final_status = self._readiness_status()
            self.client.complete_assignment(self.loader_id, order_id, final_status)
            self.on_progress(f"Order {assignment.get('order_name', order_id)} complete")
        except Exception as error:
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=1)
            try:
                self.client.report_interrupted(self.loader_id, order_id, str(error))
            except Exception:
                pass
            self.interrupted = True
            self.on_progress(f"Order {assignment.get('order_name', order_id)} interrupted: {error}")

    def _busy_heartbeat(self, stop_event: threading.Event, order_id: str) -> None:
        while not stop_event.wait(HEARTBEAT_SECONDS):
            try:
                self.client.update_status(
                    self.loader_id,
                    "busy",
                    order_id=order_id,
                    ready_shelves=sum(self.runner.substrate_ready),
                )
            except Exception as error:
                self.on_progress(f"Conductor heartbeat failed: {error}")

    @staticmethod
    def _open_bundle(bundle_data: bytes, expected_order_id: str, assignment: dict):
        if not bundle_data or len(bundle_data) > MAX_BUNDLE_BYTES:
            raise ValueError("Assignment bundle is empty or exceeds the size limit.")
        archive = zipfile.ZipFile(io.BytesIO(bundle_data))
        total_size = 0
        for info in archive.infolist():
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts or info.is_dir():
                raise ValueError("Assignment bundle contains an unsafe path.")
            total_size += info.file_size
        if total_size > MAX_BUNDLE_BYTES:
            raise ValueError("Expanded assignment bundle exceeds the size limit.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("version") != 1 or manifest.get("order_id") != expected_order_id:
            raise ValueError("Assignment manifest does not match the received order.")
        if manifest.get("print_units") != assignment.get("print_units"):
            raise ValueError("Assignment manifest does not match the order response.")
        for unit in manifest["print_units"]:
            unit_index = unit.get("index")
            if not isinstance(unit_index, int) or unit_index < 0:
                raise ValueError("Assignment contains an invalid print-unit index.")
            for filename in ("picture.png", "frame.png"):
                path = f"units/{unit_index}/{filename}"
                if path not in archive.namelist():
                    raise ValueError(f"Assignment bundle is missing {filename} for unit {unit_index}.")
        return manifest, archive

    def _run_units(self, manifest: dict, archive: zipfile.ZipFile) -> None:
        completed_units = set(manifest.get("completed_units", []))
        for unit in manifest["print_units"]:
            if unit.get("index") in completed_units:
                continue
            if self.stop_event.is_set():
                raise RuntimeError("Listen mode stopped before all print units completed.")
            while not any(self.runner.substrate_ready):
                if self.stop_event.is_set():
                    raise RuntimeError("Listen mode stopped while waiting for a ready shelf.")
                self.on_progress("Order waiting for a ready shelf")
                self.readiness_event.wait(1)
                self.readiness_event.clear()

            unit_index = unit["index"]

            def stage_before_print() -> None:
                self.printer_directory.mkdir(parents=True, exist_ok=True)
                for filename in ("picture.png", "frame.png"):
                    content = archive.read(f"units/{unit_index}/{filename}")
                    if not content:
                        raise ValueError(f"Print file is empty: {filename}")
                    destination = self.printer_directory / filename
                    if destination.exists():
                        os.chmod(destination, stat.S_IWRITE)
                        destination.unlink()
                    destination.write_bytes(content)

            while not self.stop_event.is_set():
                position = self.runner.run_one(before_print=stage_before_print)
                if position is not None:
                    break
                self.readiness_event.wait(0.5)
                self.readiness_event.clear()
            else:
                raise RuntimeError("Listen mode stopped before the print cycle began.")
            self.client.complete_print_unit(self.loader_id, manifest["order_id"], unit_index)

    def _heartbeat(self, status: str, _last_heartbeat: float) -> None:
        try:
            self.client.update_status(
                self.loader_id,
                status,
                ready_shelves=sum(self.runner.substrate_ready),
            )
        except Exception as error:
            self.on_progress(f"Conductor connection unavailable: {error}")

    def _readiness_status(self) -> str:
        return "ready" if any(self.runner.substrate_ready) else "unavailable"
