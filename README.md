# EufyRobot Three-Axis Controller

EufyRobot coordinates three stepper axes and a retrieve-gate servo using an ESP32-S3 firmware controller and a Python desktop dashboard.

## Repository Layout

- `firmware/`: PlatformIO ESP32-S3 firmware
- `scripts/controller.py`: motion protocol, cycle sequencing, shelf state, and configuration constants
- `scripts/dashboard.py`: Tkinter instrument dashboard
- `scripts/order_automation_client.py`: subprocess client for OrderAutomation's headless CLI
- `scripts/order_workflow.py`: order, batch, and continuous cycle coordination
- `scripts/robot_config.py` and `scripts/settings_dialog.py`: robot-owned integration settings
- `scripts/master_control.py`: dashboard launch entry point, also dispatches `--headless` mode
- `scripts/headless.py`: connect/home/run-all without the GUI, with per-cycle timing logged to CSV
- `scripts/expected_timing.py`: analytical cycle/home duration model used to evaluate logged timing
- `diagram.json`: Wokwi virtual hardware
- `wokwi.toml`: Wokwi firmware image paths

## Motion Hardware

- Push Motor (PM): A4988 driver and stepper motor; home switch L1
- Retrieve Motor (RM): A4988 driver and stepper motor; home switch L3
- Lift Motor (LM): A4988 driver and stepper motor; home switch L2
- Actuator 1 (A1): servo for the Retrieve Gate (RG), closed at 180 degrees and open at 0 degrees

The ESP32-S3 GPIO map is:

| Function | GPIO |
| --- | ---: |
| PM STEP / DIR | 4 / 5 |
| RM STEP / DIR | 6 / 7 |
| LM STEP / DIR | 10 / 11 |
| A1 servo signal | 14 |
| L1 PM home | 15 |
| L2 LM home | 16 |
| L3 RM home | 17 |
| Run-cycle green LED | 18 |
| Home-cycle green LED | 12 |
| Idle green LED | 13 |

Home switches are active-low with internal pull-ups. A4988 MS1/MS2/MS3 pins are grounded for full-step operation in Wokwi.

## Configuration Constants

All cycle values and calibration constants are grouped at the top of `scripts/controller.py`. The default is virtual serial at `rfc2217://localhost:4000`. Pass `--port COM3` (replace with the actual port) to connect to physical hardware; an explicit `--port` takes precedence, so switching modes does not require editing `USE_REAL_COMPONENTS`.

| Symbol | Meaning | Value |
| --- | --- | ---: |
| PS1 | Push speed 1 | 10 mm/s |
| PS2 | Push speed 2 | 2 mm/s |
| LS1 / LS2 | Lift speeds 1 / 2 | 10 / 2 mm/s |
| RS1 / RS2 | Retrieve speeds 1 / 2 | 10 / 5 mm/s |
| PM_D1 / PM_D2 / PM_D3 | Push distances from home | 150 / 220 / 225 mm |
| RM_D1 / RM_D2 / RM_D3 | Retrieve distances from home | 200 / 50 / 20 mm |
| O1 | Offset for pushing a substrate | 6 mm |
| O2 | Offset for retrieving a substrate | 6 mm |
| O3 | Final substrate slide into the shelf by RM | 24 mm |
| nShelves | Number of shelf positions | 12 |
| dShelves | Average shelf spacing | 38 mm |
| offsetShelf[] | Per-shelf calibration offsets, length `nShelves` | 0 mm initially |
| LMP[X] | Lift coordinate for shelf X | `X * dShelves + offsetShelf[X]` |
| offsetHome | Home approach and seek safeguard distance | 5 mm |
| T1 | Pause after PM's final push | 1 s |
| Lead screw | Linear travel per revolution | 8 mm/turn |

Shelf positions X are 1-based. In Python, `offsetShelf[X]` maps to list index `X - 1`. Motion calibration assumes 200 full steps/revolution and full-step drivers, or 25 steps/mm. Confirm the actual motor and driver configuration before connecting real hardware.

`substrate_ready[X]` is the dashboard's only shelf state. A ready shelf has a substrate available for printing. All shelves start not-ready each time the dashboard launches; the operator marks loaded shelves ready. A shelf becomes not-ready only after its complete cycle succeeds. Shelf offsets are persisted in `scripts/shelf_config.json`.

`X` is a 1-based shelf position from 1 through `nShelves`. A1 is actuator 1, which operates RG, the Retrieve Gate. L1, L2, and L3 are the PM, LM, and RM home limit switches, respectively.

## Setup

### Build Firmware

From the repository root in PowerShell, build the Wokwi timing-test firmware:

```powershell
cd firmware
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -e wokwi-test
```

To build production firmware instead:

```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -e esp32-s3-devkitc-1
```

The `wokwi-test` profile defines `WOKWI_FORCE_HOME_SWITCHES`: simulated homing completes when an axis reaches position zero, without reading the simulated GPIO switches. The `esp32-s3-devkitc-1` production profile reads the actual home-switch GPIO pins. Never upload the `wokwi-test` firmware to physical hardware.

### Start Wokwi

Build the `wokwi-test` firmware first, then use the Wokwi for VS Code extension and select **Wokwi: Start Simulator**. `wokwi.toml` points to this profile and enables an RFC2217 serial server on `localhost:4000`; stop and restart the simulator after rebuilding firmware so it loads the latest image. Keep the Wokwi simulator tab visible while the dashboard is connected. The diagram contains PM, RM, LM, their home buttons, and the gate servo.

### Install and Start the Dashboard

From the repository root:

```powershell
cd scripts
python -m pip install -r requirements.txt
python master_control.py
```

The dashboard defaults to virtual components and `rfc2217://localhost:4000`. Start Wokwi first, then click **Connect** in the dashboard; the connection status must read **CONNECTED** before pressing **Home**. This is the active simulation test mode.

To switch to physical hardware, first build and upload the production profile (replace `COM3` with the ESP32's port):

```powershell
cd firmware
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -e esp32-s3-devkitc-1 -t upload --upload-port COM3
cd ..\scripts
python master_control.py --port COM3
```

To switch back, run this from the repository root, restart Wokwi, then connect and run **Home** before starting a cycle:

```powershell
cd firmware
& "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run -e wokwi-test
cd ..\scripts
python master_control.py --port rfc2217://localhost:4000
```

Connect, then run **Home** before starting a cycle. At startup, all axes are considered unhomed.

For short-travel Wokwi integration testing only, start the simulator first, then launch:

```powershell
cd scripts
python master_control.py --port rfc2217://localhost:4000 --test-mode
```

Test mode requires that exact local Wokwi endpoint and is rejected for COM ports, other hosts, or headless runs. It scales every linear target and homing safeguard to 10%; speeds, servo angles, delays, shelf calibration files, and normal-mode motion remain unchanged. The dashboard displays **WOKWI TEST MODE | 10x SHORTER TRAVEL** while enabled.

### Headless Mode

For automated verification runs (e.g. against Wokwi) without the GUI, `master_control.py --headless` connects, homes all axes, marks shelves ready, and runs cycles until no ready shelf remains. It reuses the same `CycleRunner`/`FirmwareLink`/`ShelfSettings` objects as the dashboard, so shelf readiness stays single-sourced.

```powershell
cd scripts
python master_control.py --headless --port rfc2217://localhost:4000
```

Flags:

- `--ready`: comma-separated 1-based shelf numbers to mark ready, or `all` (default: `all`).
- `--log-file`: CSV path for per-cycle timing (default: `scripts/logs/cycle_times.csv`).
- `--tolerance`: fraction deviation from the analytically expected cycle time before a cycle is flagged `WARNING` (default: `0.2`).

Each cycle's actual duration is compared against an expected duration computed from the speed/distance/pause constants in `controller.py` (see `scripts/expected_timing.py`); the CSV logs `cycle, shelf, start_iso, actual_sec, expected_sec, delta_sec, delta_pct, flag` for every cycle, and a summary prints at the end. Gate (servo) motion time is not modeled and per-command serial round-trip overhead is not subtracted, so a small, consistent positive `delta_pct` is expected even on a healthy run.

## Dashboard Controls

- Shelf buttons toggle each shelf between ready and not-ready.
- **Run Cycle** runs one nearest-ready-shelf cycle without contacting OrderAutomation.
- **Run Next** obtains or resumes one Shopify order and runs one robot cycle per line-item quantity.
- **Run Batch** processes the requested number of Shopify orders; the count is orders, not print units.
- **Run Continuous** processes orders until OrderAutomation reports that no orders remain.
- **Settings** configures OrderAutomation's repository path, optional Python interpreter, non-secret runtime options, and output directories.
- **Pause After Cycle** finishes the current cycle and then stops scheduling cycles.
- **Pause Immediately** halts step pulses; axis positions become untrusted and Home is required before motion resumes.
- **Home** homes PM, LM, and RM sequentially.
- **Configure Shelf Positions** edits all offsets in order from shelf 1 to shelf 12.
- **Configure Single Shelf** selects one shelf and opens the same offset editor.

Offset controls adjust by 1 mm. Next advances to the next shelf; Save & Exit commits the edits. Cancel discards changes made in the open editor.

After each tenth successfully completed cycle, all axes home before another cycle starts. Order runs can be paused at a cycle boundary. If an order has more print units than ready shelves, the dashboard waits between cycles; load a shelf and toggle it ready to continue.

OrderAutomation remains a separate repository and desktop application. The robot uses its headless JSON CLI as a subprocess, defaults to the sibling `../OrderAutomation` checkout when available, and prefers that repository's `.venv` Python. Use Settings to select a different checkout or interpreter. The robot saves non-secret settings in its ignored root `config.json`; Shopify and GCP credentials remain only in OrderAutomation's `keys/` directory. A prepared order is persisted and resumed after interruption; each physical cycle is acknowledged separately, and cleanup is deferred until every unit completes. The `print_mailing_label` option is forwarded, but label generation is not implemented yet.

## Cycle Sequence

### Listen Mode

Connect and Home the robot controller before clicking **Listen**. Enter the Conductor computer's private-LAN hostname/IP, port, and shared token shown in its Conductor window. The robot saves these values and its generated loader ID in the ignored `scripts/config.json` file. Do not share the token outside the trusted LAN.

While listening, motion, Home, calibration, local run, and Settings controls are disabled. Shelf readiness buttons remain active because this firmware has no shelf-presence sensors. The loader reports ready when any shelf is marked ready, busy while processing an assignment, and unavailable when no shelf is ready. Marking a shelf ready from unavailable sends a ready update immediately.

Each assignment arrives as a manifest and ZIP bundle. The robot validates the manifest and files, stages each unit at the cycle's pre-print point, and acknowledges the order only after all physical cycles succeed. If a connection or cycle fails, the Conductor retains the assignment as interrupted for operator recovery; it is not automatically replayed. Clicking **Exit Listen** requests a safe boundary exit. The separate local Run Next/Batch/Continuous flow remains available outside Listen mode.

## Cycle Sequence

For selected shelf X:

1. LM to `LMP[X] + O1` at LS1.
2. PM to PM_D1 at PS1.
3. LM to `LMP[X]` at LS1.
4. PM to PM_D2 at PS1, then PM_D3 at PS2.
5. Pause T1; retract PM to PM_D2 at PS2, then to `offsetHome` at PS1.
6. Wait one second for the print placeholder.
7. RM to RM_D1 at RS1; close A1 to 180 degrees.
8. LM to `LMP[X] - O2` at LS2; RM to RM_D2 at RS1.
9. Open A1 to 0 degrees; LM to `LMP[X] - O3` at LS1.
10. RM to RM_D3 at RS1. Mark shelf X not-ready.

The nearest ready shelf minimizes absolute shelf-index distance from the current position. Shelf 1 and shelf 12 do not wrap around; ties choose the lower index. The current position starts at 1 and is updated after a successful cycle.

## Home Cycle

For PM, LM, then RM:

1. Move to `+offsetHome` at that axis's speed 1.
2. Seek toward the home switch at speed 2, with `-offsetHome` as the travel safeguard.
3. On switch contact, set the axis coordinate to zero. If the safeguard is reached first, report an error and keep the axis unhomed.

A failed or immediately stopped motion must be resolved and homed before normal moves can run.

## Serial Protocol

Commands are newline-terminated:

- `MOVE <PM|RM|LM> <target_steps> <speed_steps_s>`
- `HOME_APPROACH <PM|RM|LM> <target_steps> <speed_steps_s>`
- `HOME <PM|RM|LM> <seek_speed_steps_s> <negative_safeguard_steps>`
- `GATE <angle_degrees>`
- `STOP`

The dashboard converts millimeters and mm/s to steps using the single calibration section in `controller.py`; firmware receives step counts and step rates. Firmware replies with `BOOT`, `READY`, `OK`, `DONE`, or `ERROR` lines. A normal move completes with `DONE: MOVE <axis>`, a home seek with `DONE: HOME <axis>`, and the gate command with `DONE: GATE`. `ERROR: MOTION_STOPPED` indicates an immediate halt interrupted motion. No periodic shelf occupancy telemetry is used; shelf readiness is managed in the dashboard.
