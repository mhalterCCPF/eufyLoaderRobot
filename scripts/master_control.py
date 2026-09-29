#!/usr/bin/env python3
"""Launch the EufyRobot instrument dashboard, or run headlessly with --headless."""

import argparse
import sys
from pathlib import Path

from controller import N_SHELVES, VIRTUAL_SERIAL_URL
from dashboard import EufyDashboard
from headless import run_headless


def _parse_ready(value: str) -> list:
    if value.strip().lower() == "all":
        return [True] * N_SHELVES
    shelves = {int(item) for item in value.split(",") if item.strip()}
    if not shelves.issubset(set(range(1, N_SHELVES + 1))):
        raise argparse.ArgumentTypeError(f"Shelf numbers must be from 1 to {N_SHELVES}")
    return [(index + 1) in shelves for index in range(N_SHELVES)]


def main() -> None:
    parser = argparse.ArgumentParser(description="EufyRobot three-axis dashboard")
    parser.add_argument("--port", help="serial URL or COM port; defaults from USE_REAL_COMPONENTS")
    parser.add_argument("--test-mode", action="store_true", help="Wokwi-only mode with 10x shorter linear travel")
    parser.add_argument("--headless", action="store_true", help="run connect/home/run-all without the GUI")
    parser.add_argument("--ready", default="all", type=_parse_ready,
                         help="comma-separated 1-based shelf numbers to mark ready, or 'all' (default: all)")
    parser.add_argument("--log-file", default="logs/cycle_times.csv", type=Path,
                         help="CSV path for per-cycle timing (default: scripts/logs/cycle_times.csv)")
    parser.add_argument("--tolerance", default=0.2, type=float,
                         help="fraction deviation from expected cycle time before flagging WARNING (default: 0.2)")
    arguments = parser.parse_args()

    if arguments.test_mode and arguments.port != VIRTUAL_SERIAL_URL:
        parser.error(f"--test-mode requires --port {VIRTUAL_SERIAL_URL}")
    if arguments.test_mode and arguments.headless:
        parser.error("--test-mode is available only with the dashboard, not --headless")

    if arguments.headless:
        # FirmwareLink resolves USE_REAL_COMPONENTS/VIRTUAL_SERIAL_URL itself when port is None.
        sys.exit(run_headless(arguments.port, arguments.ready, arguments.log_file, arguments.tolerance))

    app = EufyDashboard(endpoint=arguments.port, test_mode=arguments.test_mode)
    app.mainloop()


if __name__ == "__main__":
    main()
