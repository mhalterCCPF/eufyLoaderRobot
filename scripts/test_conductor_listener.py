import io
import json
import threading
import tempfile
import unittest
import zipfile

from conductor_listener import RobotConductorListener


def make_bundle(order_id="order-1", print_units=None):
    print_units = print_units or [{"index": 0, "job_id": "job-1"}]
    stream = io.BytesIO()
    manifest = {
        "version": 1,
        "order_id": order_id,
        "order_name": "#1001",
        "print_units": print_units,
        "open_fulfillment_order_ids": ["fo-1"],
    }
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for unit in print_units:
            index = unit["index"]
            archive.writestr(f"units/{index}/picture.png", f"picture-{index}".encode())
            archive.writestr(f"units/{index}/frame.png", f"frame-{index}".encode())
    return stream.getvalue(), manifest


class FakeRunner:
    def __init__(self, ready=True):
        self.substrate_ready = [ready] * 12
        self.completed_cycles = 0

    def run_one(self, before_print=None):
        position = next((index + 1 for index, ready in enumerate(self.substrate_ready) if ready), None)
        if position is None:
            return None
        if before_print:
            before_print()
        self.substrate_ready[position - 1] = False
        self.completed_cycles += 1
        return position


class FakeConductorClient:
    def __init__(self, bundle, manifest):
        self.bundle = bundle
        self.manifest = manifest
        self.statuses = []
        self.completed = []
        self.stop_event = None
        self.completed_units = []

    def register(self, loader_id, status):
        self.statuses.append(status)

    def update_status(self, loader_id, status, **details):
        self.statuses.append(status)

    def get_assignment(self, loader_id):
        return {
            "order_id": self.manifest["order_id"],
            "order_name": self.manifest["order_name"],
            "print_units": self.manifest["print_units"],
            "completed_units": self.completed_units,
        }

    def download_bundle(self, loader_id):
        return self.bundle

    def complete_assignment(self, loader_id, order_id, status):
        self.completed.append((order_id, status))
        self.stop_event.set()

    def complete_print_unit(self, loader_id, order_id, unit_index):
        self.acknowledged_unit = (order_id, unit_index)

    def report_interrupted(self, loader_id, order_id, reason):
        self.stop_event.set()


class ConductorListenerTests(unittest.TestCase):
    def test_assignment_stages_print_files_and_reports_completion(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bundle, manifest = make_bundle()
            runner = FakeRunner()
            client = FakeConductorClient(bundle, manifest)
            stop_event = threading.Event()
            client.stop_event = stop_event
            listener = RobotConductorListener(
                runner, client, "loader-1", str(directory), stop_event, threading.Event()
            )

            listener.run()

            self.assertEqual((directory / "picture.png").read_bytes(), b"picture-0")
            self.assertEqual((directory / "frame.png").read_bytes(), b"frame-0")
            self.assertEqual(runner.completed_cycles, 1)
            self.assertEqual(client.completed, [("order-1", "ready")])
            self.assertEqual(client.acknowledged_unit, ("order-1", 0))
            self.assertIn("busy", client.statuses)

    def test_newly_ready_shelf_is_reported_without_waiting_for_heartbeat(self):
        bundle, manifest = make_bundle()
        runner = FakeRunner(ready=False)
        client = FakeConductorClient(bundle, manifest)
        stop_event = threading.Event()
        readiness_event = threading.Event()
        client.stop_event = stop_event

        def update_status(loader_id, status, **details):
            client.statuses.append(status)
            if status == "unavailable":
                runner.substrate_ready[0] = True
                readiness_event.set()

        client.update_status = update_status

        def no_assignment(loader_id):
            stop_event.set()
            return None

        client.get_assignment = no_assignment
        listener = RobotConductorListener(
            runner, client, "loader-1", ".", stop_event, readiness_event
        )

        listener.run()

        self.assertIn("unavailable", client.statuses)
        self.assertIn("ready", client.statuses)

    def test_resume_skips_units_acknowledged_by_conductor(self):
        from pathlib import Path
        import tempfile

        units = [{"index": 0, "job_id": "job-1"}, {"index": 1, "job_id": "job-2"}]
        bundle, manifest = make_bundle(print_units=units)
        runner = FakeRunner()
        client = FakeConductorClient(bundle, manifest)
        client.completed_units = [0]
        stop_event = threading.Event()
        client.stop_event = stop_event
        with tempfile.TemporaryDirectory() as temporary:
            listener = RobotConductorListener(
                runner, client, "loader-1", temporary, stop_event, threading.Event()
            )
            listener.run()
            self.assertEqual(runner.completed_cycles, 1)
            self.assertEqual((Path(temporary) / "picture.png").read_bytes(), b"picture-1")
            self.assertEqual(client.acknowledged_unit, ("order-1", 1))


if __name__ == "__main__":
    unittest.main()