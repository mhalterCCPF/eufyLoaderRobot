import tempfile
import queue
import unittest
from pathlib import Path

import controller


class FakeMotion:
    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def _record(self, call):
        self.calls.append(call)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated motion failure")

    def move_to(self, axis, target_mm, speed_mm_per_second):
        self._record(("MOVE", axis, target_mm, speed_mm_per_second))

    def approach_home(self, axis, target_mm, speed_mm_per_second):
        self._record(("APPROACH", axis, target_mm, speed_mm_per_second))

    def home_axis(self, axis, seek_speed_mm_per_second, safeguard_mm):
        self._record(("HOME", axis, seek_speed_mm_per_second, safeguard_mm))

    def set_gate(self, angle_degrees):
        self._record(("GATE", angle_degrees))

    def set_operation_state(self, state):
        self._record(("STATE", state))

    def stop_immediately(self):
        self._record(("STOP",))


class FakeSerialPort:
    def __init__(self, error_response=None):
        self.writes = []
        self.incoming = queue.Queue()
        self.closed = False
        self.error_response = error_response

    def write(self, payload):
        command = payload.decode("ascii").strip()
        self.writes.append(command)
        if self.error_response:
            self.incoming.put((self.error_response + "\n").encode("ascii"))
            return len(payload)
        parts = command.split()
        if parts[0] == "MOVE":
            reply = f"DONE: MOVE {parts[1]}"
        elif parts[0] == "HOME_APPROACH":
            reply = f"DONE: HOME_APPROACH {parts[1]}"
        elif parts[0] == "HOME":
            reply = f"DONE: HOME {parts[1]}"
        elif parts[0] == "STATE":
            reply = "DONE: STATE"
        else:
            reply = "DONE: GATE"
        if parts[0] != "STOP":
            self.incoming.put((reply + "\n").encode("ascii"))
        return len(payload)

    def flush(self):
        pass

    def readline(self):
        try:
            return self.incoming.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self):
        self.closed = True


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.motion = FakeMotion()
        self.settings_file = tempfile.TemporaryDirectory()
        self.settings = controller.ShelfSettings(Path(self.settings_file.name) / "settings.json")
        self.sleeps = []
        self.runner = controller.CycleRunner(
            self.motion,
            self.settings,
            sleep=self.sleeps.append,
        )

    def tearDown(self):
        self.settings_file.cleanup()

    def test_units_and_shelf_positions(self):
        self.assertEqual(controller.mm_to_steps(8), 200)
        self.assertEqual(controller.mm_per_second_to_steps_per_second(10), 250)
        self.assertEqual(controller.lmp(3, [0.0] * 12), 114.0)

    def test_next_position_uses_nearest_index_and_lower_tie(self):
        ready = [False] * 12
        ready[1] = True
        ready[3] = True
        self.assertEqual(controller.get_next_position(3, ready), 2)
        self.assertIsNone(controller.get_next_position(3, [False] * 12))

    def test_cycle_runs_in_requested_order(self):
        self.runner.settings.offsets_mm[2] = 1.5
        self.runner.run_cycle(3)
        self.assertEqual(
            self.motion.calls,
            [
                ("STATE", "RUN"),
                ("MOVE", "LM", 121.5, controller.LS1),
                ("MOVE", "PM", controller.PM_D1, controller.PS1),
                ("MOVE", "LM", 115.5, controller.LS1),
                ("MOVE", "PM", controller.PM_D2, controller.PS1),
                ("MOVE", "PM", controller.PM_D3, controller.PS2),
                ("MOVE", "PM", controller.PM_D2, controller.PS2),
                ("MOVE", "PM", controller.OFFSET_HOME, controller.PS1),
                ("MOVE", "RM", controller.RM_D1, controller.RS1),
                ("GATE", controller.GATE_CLOSED_DEG),
                ("MOVE", "LM", 109.5, controller.LS2),
                ("MOVE", "RM", controller.RM_D2, controller.RS1),
                ("GATE", controller.GATE_OPEN_DEG),
                ("MOVE", "LM", 91.5, controller.LS1),
                ("MOVE", "RM", controller.RM_D3, controller.RS1),
                ("STATE", "IDLE"),
            ],
        )
        self.assertEqual(self.sleeps, [controller.T1_SEC, controller.PRINT_PLACEHOLDER_SEC])

    def test_before_print_callback_runs_after_push_and_before_retrieval(self):
        callback_calls = []
        self.runner.run_cycle(1, before_print=lambda: callback_calls.append(len(self.motion.calls)))
        self.assertEqual(callback_calls, [8])
        self.assertEqual(self.motion.calls[8], ("MOVE", "RM", controller.RM_D1, controller.RS1))

    def test_failed_cycle_restores_idle_state(self):
        self.runner.motion = FakeMotion(fail_on_call=3)
        with self.assertRaises(RuntimeError):
            self.runner.run_cycle(1)
        self.assertEqual(self.runner.motion.calls[0], ("STATE", "RUN"))
        self.assertEqual(self.runner.motion.calls[-1], ("STATE", "IDLE"))

    def test_home_cycle_uses_home_state_and_restores_idle(self):
        self.runner.home_cycle()
        self.assertEqual(self.motion.calls[0], ("STATE", "HOME"))
        self.assertEqual(self.motion.calls[-1], ("STATE", "IDLE"))

    def test_failed_home_cycle_restores_idle_state(self):
        self.runner.motion = FakeMotion(fail_on_call=3)
        with self.assertRaises(RuntimeError):
            self.runner.home_cycle()
        self.assertEqual(self.runner.motion.calls[0], ("STATE", "HOME"))
        self.assertEqual(self.runner.motion.calls[-1], ("STATE", "IDLE"))

    def test_failed_cycle_does_not_consume_readiness(self):
        self.runner.substrate_ready[0] = True
        self.runner.motion = FakeMotion(fail_on_call=2)
        with self.assertRaises(RuntimeError):
            self.runner.run_one()
        self.assertTrue(self.runner.substrate_ready[0])
        self.assertEqual(self.runner.completed_cycles, 0)

    def test_successful_cycle_consumes_readiness(self):
        self.runner.substrate_ready[0] = True
        self.assertEqual(self.runner.run_one(), 1)
        self.assertFalse(self.runner.substrate_ready[0])
        self.assertEqual(self.runner.current_position, 1)

    def test_run_ready_cycle_requires_a_ready_shelf(self):
        with self.assertRaisesRegex(RuntimeError, "Mark at least one shelf ready"):
            self.runner.run_ready_cycle()
        self.runner.substrate_ready[2] = True
        self.assertEqual(self.runner.run_ready_cycle(), 3)

    def test_tenth_cycle_homes_before_return(self):
        self.runner.substrate_ready[0] = True
        self.runner.completed_cycles = 9
        self.runner.run_one()
        self.assertEqual(
            [call[:2] for call in self.motion.calls if call[0] in ("APPROACH", "HOME")],
            [("APPROACH", "PM"), ("HOME", "PM"),
             ("APPROACH", "LM"), ("HOME", "LM"),
             ("APPROACH", "RM"), ("HOME", "RM")],
        )
        self.assertEqual(self.runner.current_position, 1)

    def test_run_all_stops_after_pause_boundary(self):
        self.runner.substrate_ready[:3] = [True, True, True]
        original_run_one = self.runner.run_one
        completed = []

        def run_one_and_pause():
            position = original_run_one()
            completed.append(position)
            self.runner.request_pause()
            return position

        self.runner.run_one = run_one_and_pause
        self.assertEqual(self.runner.run_all(), [1])
        self.assertEqual(completed, [1])
        self.assertTrue(self.runner.substrate_ready[1])

    def test_offsets_persist(self):
        self.settings.offsets_mm[4] = -2.5
        self.settings.save()
        loaded = controller.ShelfSettings(self.settings.path)
        self.assertEqual(loaded.offsets_mm[4], -2.5)

    def test_firmware_command_contract(self):
        port = FakeSerialPort()
        link = controller.FirmwareLink(endpoint="fake", serial_factory=lambda *args, **kwargs: port)
        link.connect()
        link.homed_axes = set(controller.AXES)
        link.move_to("PM", 150, 10)
        link.approach_home("LM", 5, 10)
        link.home_axis("RM", 5, -5)
        link.set_gate(180)
        link.set_operation_state("RUN")
        link.set_operation_state("HOME")
        link.set_operation_state("IDLE")
        link.stop_immediately()
        self.assertEqual(
            port.writes,
              ["MOVE PM 3750 250.000", "HOME_APPROACH LM 125 250.000",
               "HOME RM 125.000 -125", "GATE 180", "STATE RUN", "STATE HOME",
               "STATE IDLE", "STOP"],
        )
        self.assertFalse(link.homed)
        link.disconnect()

    def test_test_mode_scales_linear_commands_but_not_speeds(self):
        port = FakeSerialPort()
        link = controller.FirmwareLink(
            endpoint=controller.VIRTUAL_SERIAL_URL,
            serial_factory=lambda *args, **kwargs: port,
            test_mode=True,
        )
        link.connect()
        link.homed_axes = set(controller.AXES)

        link.move_to("PM", 8, 10)
        link.approach_home("LM", 8, 10)
        link.home_axis("RM", 5, -8)

        self.assertEqual(
            port.writes,
            ["MOVE PM 20 250.000", "HOME_APPROACH LM 20 250.000", "HOME RM 125.000 -20"],
        )
        link.disconnect()

    def test_test_mode_rejects_nonlocal_or_physical_endpoints(self):
        for endpoint in ("COM3", "rfc2217://192.168.1.20:4000"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, "restricted to the local Wokwi endpoint"):
                    controller.FirmwareLink(endpoint=endpoint, test_mode=True)

        link = controller.FirmwareLink(
            endpoint=controller.VIRTUAL_SERIAL_URL,
            serial_factory=lambda *args, **kwargs: self.fail("serial factory must not be called"),
            test_mode=True,
        )
        link.endpoint = "COM3"
        with self.assertRaisesRegex(ValueError, "restricted to the local Wokwi endpoint"):
            link.connect()

    def test_test_mode_can_only_be_toggled_disconnected_on_local_wokwi(self):
        link = controller.FirmwareLink(endpoint=controller.VIRTUAL_SERIAL_URL)
        self.assertFalse(link.test_mode)
        link.set_test_mode(True)
        self.assertTrue(link.test_mode)
        link.set_test_mode(False)
        self.assertFalse(link.test_mode)

        link.connected = True
        with self.assertRaisesRegex(RuntimeError, "only be changed while disconnected"):
            link.set_test_mode(True)
        link.connected = False

        link.endpoint = "COM3"
        with self.assertRaisesRegex(ValueError, "restricted to the local Wokwi endpoint"):
            link.set_test_mode(True)
        self.assertFalse(link.test_mode)

    def test_invalid_operation_state_is_rejected(self):
        link = controller.FirmwareLink(endpoint="fake")
        with self.assertRaisesRegex(ValueError, "IDLE, RUN, or HOME"):
            link.set_operation_state("BUSY")

    def test_motion_error_invalidates_homed_state(self):
        port = FakeSerialPort(error_response="ERROR: HOME_PM_SAFEGUARD_REACHED")
        link = controller.FirmwareLink(endpoint="fake", serial_factory=lambda *args, **kwargs: port)
        link.connect()
        link.homed_axes = set(controller.AXES)
        with self.assertRaisesRegex(RuntimeError, "HOME_PM_SAFEGUARD_REACHED"):
            link.move_to("PM", 150, 10)
        self.assertFalse(link.homed)
        link.disconnect()


if __name__ == "__main__":
    unittest.main()
