import tempfile
import unittest
from pathlib import Path

import controller
from order_workflow import RobotOrderCoordinator


class FakeMotion:
    def __init__(self):
        self.calls = []

    def move_to(self, axis, target_mm, speed_mm_per_second):
        self.calls.append(("MOVE", axis))

    def approach_home(self, axis, target_mm, speed_mm_per_second):
        self.calls.append(("APPROACH", axis))

    def home_axis(self, axis, seek_speed_mm_per_second, safeguard_mm):
        self.calls.append(("HOME", axis))

    def set_gate(self, angle_degrees):
        self.calls.append(("GATE", angle_degrees))

    def set_operation_state(self, state):
        self.calls.append(("STATE", state))

    def stop_immediately(self):
        self.calls.append(("STOP",))


class FakeOrderClient:
    def __init__(self, printer_directory, units=None, status="prepared"):
        self.printer_directory = Path(printer_directory)
        self.units = units or [{"index": 0, "job_id": "job1"}]
        self.status = status
        self.calls = []
        self.missing_file = False

    def prepare_next_order(self):
        self.calls.append(("prepare",))
        if self.status == "no_orders":
            return {"status": "no_orders"}
        return {
            "status": "prepared",
            "token": "token-1",
            "order_name": "1001",
            "print_units": self.units,
            "completed_units": [],
        }

    def stage_print_unit(self, token, unit_index):
        self.calls.append(("stage", unit_index))
        self.printer_directory.mkdir(parents=True, exist_ok=True)
        files = [self.printer_directory / "picture.png", self.printer_directory / "frame.png"]
        files[0].write_bytes(b"picture")
        if not self.missing_file:
            files[1].write_bytes(b"frame")
        return {"status": "staged", "files": [str(path) for path in files]}

    def complete_print_unit(self, token, unit_index):
        self.calls.append(("unit_completed", unit_index))
        return {"status": "unit_completed"}

    def complete_order(self, token):
        self.calls.append(("complete",))
        return {"status": "completed", "order_name": "1001"}


class OrderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.settings = controller.ShelfSettings(self.root / "shelves.json")
        self.motion = FakeMotion()
        self.runner = controller.CycleRunner(self.motion, self.settings, sleep=lambda _seconds: None)
        self.printer_directory = self.root / "printer"

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_run_next_stages_each_unit_at_print_boundary_then_completes(self):
        client = FakeOrderClient(self.printer_directory, units=[
            {"index": 0, "job_id": "job1"},
            {"index": 1, "job_id": "job2"},
        ])
        self.runner.substrate_ready[:2] = [True, True]
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))

        result = coordinator.run_next_order()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(client.calls, [
            ("prepare",), ("stage", 0), ("unit_completed", 0),
            ("stage", 1), ("unit_completed", 1), ("complete",),
        ])
        self.assertEqual(self.runner.completed_cycles, 2)
        self.assertFalse(any(self.runner.substrate_ready[:2]))

    def test_missing_print_file_blocks_motion_and_completion(self):
        client = FakeOrderClient(self.printer_directory)
        client.missing_file = True
        self.runner.substrate_ready[0] = True
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))

        with self.assertRaisesRegex(RuntimeError, "missing or empty"):
            coordinator.run_next_order()

        self.assertEqual(self.runner.completed_cycles, 0)
        self.assertTrue(self.runner.substrate_ready[0])
        self.assertNotIn(("unit_completed", 0), client.calls)
        self.assertNotIn(("complete",), client.calls)

    def test_no_orders_does_not_start_motion(self):
        client = FakeOrderClient(self.printer_directory, status="no_orders")
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))

        self.assertEqual(coordinator.run_next_order()["status"], "no_orders")
        self.assertEqual(client.calls, [("prepare",)])
        self.assertEqual(self.motion.calls, [])

    def test_waits_for_shelf_and_pause_does_not_acknowledge_unprinted_unit(self):
        client = FakeOrderClient(self.printer_directory)
        coordinator = RobotOrderCoordinator(
            self.runner,
            client,
            str(self.printer_directory),
            wait_for_ready=lambda: self.runner.substrate_ready.__setitem__(0, True),
        )
        result = coordinator.run_next_order()
        self.assertEqual(result["status"], "completed")

        client = FakeOrderClient(self.printer_directory)
        self.runner.pause_requested.set()
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))
        result = coordinator.run_next_order()
        self.assertEqual(result["status"], "paused")
        self.assertNotIn(("stage", 0), client.calls)
        self.assertNotIn(("unit_completed", 0), client.calls)

    def test_resume_skips_physically_completed_manifest_units(self):
        client = FakeOrderClient(self.printer_directory, units=[
            {"index": 0, "job_id": "job1"},
            {"index": 1, "job_id": "job2"},
        ])
        client.prepare_next_order = lambda: {
            "status": "prepared",
            "token": "token-1",
            "order_name": "1001",
            "print_units": client.units,
            "completed_units": [0],
        }
        self.runner.substrate_ready[0] = True
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))

        result = coordinator.run_next_order()

        self.assertEqual(result["status"], "completed")
        self.assertNotIn(("stage", 0), client.calls)
        self.assertIn(("stage", 1), client.calls)
        self.assertEqual(self.runner.completed_cycles, 1)

    def test_batch_counts_orders_and_stops_on_empty_queue(self):
        client = FakeOrderClient(self.printer_directory)
        coordinator = RobotOrderCoordinator(self.runner, client, str(self.printer_directory))
        results = iter([
            {"status": "completed"},
            {"status": "no_orders"},
        ])
        coordinator.run_next_order = lambda: next(results)

        self.assertEqual(
            coordinator.run_batch(5),
            {"status": "no_orders", "completed_orders": 1},
        )
        with self.assertRaisesRegex(ValueError, "positive integer"):
            coordinator.run_batch(0)


if __name__ == "__main__":
    unittest.main()