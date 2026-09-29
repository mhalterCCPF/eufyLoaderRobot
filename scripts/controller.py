"""Motion protocol, shelf state, and cycle sequencing for EufyRobot."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

# Configuration constants
USE_REAL_COMPONENTS = False
VIRTUAL_SERIAL_URL = "rfc2217://localhost:4000"
TEST_MODE_DISTANCE_SCALE = 0.1
BAUD_RATE = 115_200
READ_TIMEOUT_SEC = 0.25
COMMAND_TIMEOUT_SEC = 45.0

N_SHELVES = 12
SHELF_SPACING_MM = 38.0
LEAD_SCREW_MM_PER_TURN = 8.0
MOTOR_FULL_STEPS_PER_TURN = 200
MICROSTEP_DIVISOR = 1
STEPS_PER_MM = MOTOR_FULL_STEPS_PER_TURN * MICROSTEP_DIVISOR / LEAD_SCREW_MM_PER_TURN

PS1 = 10.0
PS2 = 2.0
LS1 = PS1
LS2 = PS2
RS1 = PS1
RS2 = 5.0

PM_D1 = 150.0
PM_D2 = 220.0
PM_D3 = 225.0
RM_D1 = 200.0
RM_D2 = 50.0
RM_D3 = 20.0
O1 = 6.0
O2 = 6.0
O3 = 24.0
OFFSET_HOME = 5.0
T1_SEC = 1.0
PRINT_PLACEHOLDER_SEC = 1.0
SHELF_OFFSET_STEP_MM = 1.0
GATE_OPEN_DEG = 0
GATE_CLOSED_DEG = 180

CONFIG_PATH = Path(__file__).with_name("shelf_config.json")
AXES = ("PM", "RM", "LM")


def lmp(shelf_position: int, offsets: list[float]) -> float:
    """Return the calibrated absolute lift position for a 1-based shelf."""
    if not 1 <= shelf_position <= N_SHELVES:
        raise ValueError(f"Shelf position must be from 1 to {N_SHELVES}")
    if len(offsets) != N_SHELVES:
        raise ValueError(f"Expected {N_SHELVES} shelf offsets")
    return shelf_position * SHELF_SPACING_MM + offsets[shelf_position - 1]


def mm_to_steps(distance_mm: float) -> int:
    return round(distance_mm * STEPS_PER_MM)


def mm_per_second_to_steps_per_second(speed_mm_per_second: float) -> float:
    if speed_mm_per_second <= 0:
        raise ValueError("Speed must be greater than zero")
    return speed_mm_per_second * STEPS_PER_MM


def get_next_position(current_position: int, substrate_ready: list[bool]) -> Optional[int]:
    """Choose the nearest ready shelf, with lower-index tie breaking."""
    if len(substrate_ready) != N_SHELVES:
        raise ValueError(f"Expected {N_SHELVES} shelf readiness values")
    if not 1 <= current_position <= N_SHELVES:
        raise ValueError(f"Current position must be from 1 to {N_SHELVES}")
    candidates = (index + 1 for index, ready in enumerate(substrate_ready) if ready)
    return min(candidates, key=lambda position: (abs(position - current_position), position), default=None)


class MotionInterface(Protocol):
    def move_to(self, axis: str, target_mm: float, speed_mm_per_second: float) -> None: ...
    def approach_home(self, axis: str, target_mm: float, speed_mm_per_second: float) -> None: ...
    def home_axis(self, axis: str, seek_speed_mm_per_second: float, safeguard_mm: float) -> None: ...
    def set_gate(self, angle_degrees: int) -> None: ...
    def set_operation_state(self, state: str) -> None: ...
    def stop_immediately(self) -> None: ...


class ShelfSettings:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.offsets_mm = [0.0] * N_SHELVES
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            offsets = data.get("offsetShelf", [])
            if len(offsets) == N_SHELVES and all(isinstance(value, (int, float)) for value in offsets):
                self.offsets_mm = [float(value) for value in offsets]
        except (OSError, ValueError, TypeError, AttributeError):
            self.offsets_mm = [0.0] * N_SHELVES

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"offsetShelf": self.offsets_mm}, indent=2) + "\n",
            encoding="utf-8",
        )


def find_real_serial_port() -> Optional[str]:
    import serial.tools.list_ports

    for info in serial.tools.list_ports.comports():
        description = info.description.lower()
        if any(key in description for key in ("cp210", "ch340", "ch341", "uart", "esp32")):
            return info.device
    return None


class FirmwareLink:
    """Serialized request/reply client for the firmware motion protocol."""

    def __init__(self, endpoint: Optional[str] = None, serial_factory=None, test_mode: bool = False) -> None:
        self.endpoint = endpoint or (find_real_serial_port() if USE_REAL_COMPONENTS else VIRTUAL_SERIAL_URL)
        self.test_mode = test_mode
        self._validate_test_endpoint()
        self.serial_factory = serial_factory
        self.connection = None
        self.connected = False
        self.homed_axes: set[str] = set()
        self._stop_reader = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._write_lock = threading.Lock()

    @property
    def homed(self) -> bool:
        return self.homed_axes == set(AXES)

    def connect(self) -> None:
        self._validate_test_endpoint()
        if not self.endpoint:
            raise RuntimeError("No real serial port found; provide a port explicitly")
        serial_factory = self.serial_factory
        if serial_factory is None:
            import serial

            serial_factory = serial.serial_for_url
        self.connection = serial_factory(
            self.endpoint,
            baudrate=BAUD_RATE,
            timeout=READ_TIMEOUT_SEC,
        )
        self.connected = True
        self._stop_reader.clear()
        self._reader = threading.Thread(target=self._read_loop, name="firmware-rx", daemon=True)
        self._reader.start()

    def _validate_test_endpoint(self) -> None:
        if self.test_mode and self.endpoint != VIRTUAL_SERIAL_URL:
            raise ValueError(
                f"Test mode is restricted to the local Wokwi endpoint {VIRTUAL_SERIAL_URL}."
            )

    def _distance_to_steps(self, distance_mm: float) -> int:
        if self.test_mode:
            distance_mm *= TEST_MODE_DISTANCE_SCALE
        return mm_to_steps(distance_mm)

    def disconnect(self) -> None:
        self.connected = False
        self._stop_reader.set()
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.homed_axes.clear()

    def _read_loop(self) -> None:
        connection = self.connection
        while not self._stop_reader.is_set() and connection is not None:
            try:
                raw = connection.readline()
                if raw:
                    self._lines.put(raw.decode("utf-8", errors="replace").strip())
            except OSError as exc:
                self._lines.put(f"ERROR: serial connection lost: {exc}")
                self.connected = False
                self.homed_axes.clear()
                break

    def _send(self, command: str, expected_done: str, timeout_sec: float = COMMAND_TIMEOUT_SEC) -> None:
        if not self.connected or self.connection is None:
            raise RuntimeError("Not connected to the motion controller")
        with self._write_lock:
            self.connection.write((command + "\n").encode("ascii"))
            self.connection.flush()

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                line = self._lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if not self.connected:
                    self.homed_axes.clear()
                    raise RuntimeError("Serial connection lost while waiting for motion")
                continue
            if line.startswith("ERROR:"):
                self.homed_axes.clear()
                raise RuntimeError(line.partition(":")[2].strip())
            if line == expected_done:
                return
        self.homed_axes.clear()
        try:
            self.stop_immediately()
        except OSError:
            pass
        raise TimeoutError(f"Timed out waiting for {expected_done}")

    def move_to(self, axis: str, target_mm: float, speed_mm_per_second: float) -> None:
        self._validate_axis(axis)
        if axis not in self.homed_axes:
            raise RuntimeError(f"{axis} position is unknown; Home is required")
        if speed_mm_per_second <= 0:
            raise ValueError("Speed must be greater than zero")
        self._send(
            f"MOVE {axis} {self._distance_to_steps(target_mm)} {mm_per_second_to_steps_per_second(speed_mm_per_second):.3f}",
            f"DONE: MOVE {axis}",
        )

    def approach_home(self, axis: str, target_mm: float, speed_mm_per_second: float) -> None:
        self._validate_axis(axis)
        if speed_mm_per_second <= 0:
            raise ValueError("Speed must be greater than zero")
        self._send(
            f"HOME_APPROACH {axis} {self._distance_to_steps(target_mm)} {mm_per_second_to_steps_per_second(speed_mm_per_second):.3f}",
            f"DONE: HOME_APPROACH {axis}",
        )

    def home_axis(self, axis: str, seek_speed_mm_per_second: float, safeguard_mm: float) -> None:
        self._validate_axis(axis)
        if seek_speed_mm_per_second <= 0 or safeguard_mm >= 0:
            raise ValueError("Homing needs a positive speed and negative safeguard")
        self._send(
            f"HOME {axis} {mm_per_second_to_steps_per_second(seek_speed_mm_per_second):.3f} {self._distance_to_steps(safeguard_mm)}",
            f"DONE: HOME {axis}",
        )
        self.homed_axes.add(axis)

    def set_gate(self, angle_degrees: int) -> None:
        if not 0 <= angle_degrees <= 180:
            raise ValueError("Gate angle must be between 0 and 180 degrees")
        self._send(f"GATE {angle_degrees}", "DONE: GATE")

    def set_operation_state(self, state: str) -> None:
        if state not in ("IDLE", "RUN", "HOME"):
            raise ValueError("Operation state must be IDLE, RUN, or HOME")
        self._send(f"STATE {state}", "DONE: STATE")

    def stop_immediately(self) -> None:
        if not self.connected or self.connection is None:
            self.homed_axes.clear()
            return
        self.homed_axes.clear()
        with self._write_lock:
            self.connection.write(b"STOP\n")
            self.connection.flush()

    @staticmethod
    def _validate_axis(axis: str) -> None:
        if axis not in AXES:
            raise ValueError(f"Axis must be one of {', '.join(AXES)}")


class CycleRunner:
    def __init__(
        self,
        motion: MotionInterface,
        settings: ShelfSettings,
        on_progress: Optional[Callable[[str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.motion = motion
        self.settings = settings
        self.on_progress = on_progress or (lambda _message: None)
        self.sleep = sleep
        self.substrate_ready = [False] * N_SHELVES
        self.current_position = 1
        self.completed_cycles = 0
        self.pause_requested = threading.Event()
        self.immediate_stop_requested = threading.Event()

    def _run_step(self, label: str, action: Callable[[], None]) -> None:
        if self.immediate_stop_requested.is_set():
            raise RuntimeError("Cycle stopped immediately; Home is required")
        self.on_progress(label)
        action()

    def _run_indicated(self, state: str, action: Callable[[], None]) -> None:
        try:
            self.motion.set_operation_state(state)
            action()
        finally:
            self.motion.set_operation_state("IDLE")

    def run_cycle(self, shelf_position: int, before_print: Optional[Callable[[], None]] = None) -> None:
        if not 1 <= shelf_position <= N_SHELVES:
            raise ValueError(f"Shelf position must be from 1 to {N_SHELVES}")
        shelf_lift_position = lmp(shelf_position, self.settings.offsets_mm)
        move = self.motion.move_to

        def run_steps() -> None:
            self._run_step("LM positioning for push", lambda: move("LM", shelf_lift_position + O1, LS1))
            self._run_step("PM first push", lambda: move("PM", PM_D1, PS1))
            self._run_step("LM aligning with shelf", lambda: move("LM", shelf_lift_position, LS1))
            self._run_step("PM second push", lambda: move("PM", PM_D2, PS1))
            self._run_step("PM final push", lambda: move("PM", PM_D3, PS2))
            self._run_step("Settling", lambda: self.sleep(T1_SEC))
            self._run_step("PM initial retract", lambda: move("PM", PM_D2, PS2))
            self._run_step("PM retract", lambda: move("PM", OFFSET_HOME, PS1))
            def print_step() -> None:
                if before_print:
                    before_print()
                self.sleep(PRINT_PLACEHOLDER_SEC)

            self._run_step("Print placeholder", print_step)
            self._run_step("RM first retrieve", lambda: move("RM", RM_D1, RS1))
            self._run_step("Close retrieve gate", lambda: self.motion.set_gate(GATE_CLOSED_DEG))
            self._run_step("LM retrieve clearance", lambda: move("LM", shelf_lift_position - O2, LS2))
            self._run_step("RM return to shelf", lambda: move("RM", RM_D2, RS1))
            self._run_step("Open retrieve gate", lambda: self.motion.set_gate(GATE_OPEN_DEG))
            self._run_step("LM final shelf position", lambda: move("LM", shelf_lift_position - O3, LS1))
            self._run_step("RM final return", lambda: move("RM", RM_D3, RS1))

        self._run_indicated("RUN", run_steps)

    def home_cycle(self) -> None:
        def home_steps() -> None:
            for axis, approach_speed, seek_speed in (
                ("PM", PS1, PS2),
                ("LM", LS1, LS2),
                ("RM", RS1, RS2),
            ):
                self._run_step(
                    f"{axis} home approach",
                    lambda axis=axis, speed=approach_speed: self.motion.approach_home(axis, OFFSET_HOME, speed),
                )
                self._run_step(
                    f"{axis} home seek",
                    lambda axis=axis, speed=seek_speed: self.motion.home_axis(axis, speed, -OFFSET_HOME),
                )
            self.current_position = 1

        self._run_indicated("HOME", home_steps)

    def run_one(self, before_print: Optional[Callable[[], None]] = None) -> Optional[int]:
        shelf_position = get_next_position(self.current_position, self.substrate_ready)
        if shelf_position is None:
            return None
        self.run_cycle(shelf_position, before_print=before_print)
        self.substrate_ready[shelf_position - 1] = False
        self.current_position = shelf_position
        self.completed_cycles += 1
        if self.completed_cycles % 10 == 0:
            self.home_cycle()
        return shelf_position

    def run_all(self) -> list[int]:
        completed_positions = []
        while not self.pause_requested.is_set():
            shelf_position = self.run_one()
            if shelf_position is None:
                break
            completed_positions.append(shelf_position)
        return completed_positions

    def request_pause(self) -> None:
        self.pause_requested.set()

    def request_immediate_stop(self) -> None:
        self.immediate_stop_requested.set()
        self.motion.stop_immediately()

    def reset_after_home(self) -> None:
        self.immediate_stop_requested.clear()
        self.home_cycle()
