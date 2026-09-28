"""Headless connect/home/run-all entry point with per-cycle timing evaluation.

Drives the same CycleRunner/FirmwareLink/ShelfSettings objects the Tkinter
dashboard uses, so shelf readiness stays single-sourced in
CycleRunner.substrate_ready. Intended for automated verification runs (e.g.
against Wokwi) without the GUI.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from controller import CycleRunner, FirmwareLink, N_SHELVES, ShelfSettings
from expected_timing import expected_cycle_seconds, expected_home_seconds, new_axis_state

CSV_HEADER = ["cycle", "shelf", "start_iso", "actual_sec", "expected_sec", "delta_sec", "delta_pct", "flag"]


def _failure(stage: str, exc: Exception) -> int:
    print(f"{stage} failed: {exc}")
    return 1


def run_headless(endpoint: Optional[str], ready_shelves: List[bool], log_path: Path, tolerance: float) -> int:
    if len(ready_shelves) != N_SHELVES:
        raise ValueError(f"Expected {N_SHELVES} shelf readiness values")

    settings = ShelfSettings()
    link = FirmwareLink(endpoint=endpoint)
    runner = CycleRunner(link, settings)

    print(f"Connecting to {link.endpoint} ...")
    try:
        link.connect()
    except Exception as exc:
        return _failure("Connect", exc)

    try:
        print("Connected. Homing PM, LM, RM ...")
        try:
            runner.reset_after_home()
        except Exception as exc:
            return _failure("Homing", exc)

        runner.substrate_ready[:] = ready_shelves
        print(f"Marked {sum(ready_shelves)} shelf(es) ready. Running all ...")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        axis_state = new_axis_state()
        delta_pcts: List[float] = []
        warning_count = 0

        with log_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADER)
            while not runner.pause_requested.is_set():
                start_iso = datetime.now().isoformat(timespec="seconds")
                start = time.monotonic()
                try:
                    position = runner.run_one()
                except Exception as exc:
                    return _failure("Cycle", exc)
                if position is None:
                    break
                actual_sec = time.monotonic() - start

                expected_sec = expected_cycle_seconds(position, settings.offsets_mm, axis_state)
                if runner.completed_cycles % 10 == 0:
                    expected_sec += expected_home_seconds(axis_state)

                delta_sec = actual_sec - expected_sec
                delta_pct = delta_sec / expected_sec if expected_sec else 0.0
                flag = "WARNING" if abs(delta_pct) > tolerance else "OK"
                if flag == "WARNING":
                    warning_count += 1
                delta_pcts.append(delta_pct)

                writer.writerow([
                    runner.completed_cycles, position, start_iso,
                    f"{actual_sec:.3f}", f"{expected_sec:.3f}", f"{delta_sec:.3f}",
                    f"{delta_pct:+.1%}", flag,
                ])
                handle.flush()
                print(
                    f"Cycle {runner.completed_cycles}: shelf {position:02d} "
                    f"actual={actual_sec:.2f}s expected={expected_sec:.2f}s "
                    f"({delta_pct:+.1%}) {flag}"
                )
    finally:
        link.disconnect()

    if delta_pcts:
        print(
            f"Done: {len(delta_pcts)} cycle(s), {warning_count} warning(s), "
            f"delta% min={min(delta_pcts):+.1%} max={max(delta_pcts):+.1%} "
            f"avg={sum(delta_pcts) / len(delta_pcts):+.1%}"
        )
    else:
        print("Done: no ready shelves were processed.")
    print(f"Timing log written to {log_path}")
    return 0
