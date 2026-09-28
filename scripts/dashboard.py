"""Tkinter instrument dashboard for the three-axis EufyRobot controller."""

from __future__ import annotations

import argparse
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable, Optional

from controller import (
    N_SHELVES,
    SHELF_OFFSET_STEP_MM,
    USE_REAL_COMPONENTS,
    CycleRunner,
    FirmwareLink,
    ShelfSettings,
)


COLORS = {
    "background": "#10171b",
    "panel": "#1b272d",
    "panel_alt": "#233139",
    "border": "#34434a",
    "text": "#edf1ec",
    "muted": "#a0ada9",
    "accent": "#e28a45",
    "ready": "#447b61",
    "empty": "#39464b",
    "danger": "#a53f36",
    "blue": "#557c89",
}


class EufyDashboard(tk.Tk):
    def __init__(self, endpoint: Optional[str] = None) -> None:
        super().__init__()
        self.title("EufyRobot | Motion Instrument")
        self.geometry("1180x760")
        self.minsize(980, 680)
        self.configure(bg=COLORS["background"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.settings = ShelfSettings()
        self.link = FirmwareLink(endpoint=endpoint)
        self.runner = CycleRunner(self.link, self.settings, on_progress=self._post_progress)
        self._worker_active = False
        self._pauseable_operation = False
        self._closing = False
        self.endpoint_var = tk.StringVar(value=endpoint or self.link.endpoint or "")
        self.mode_var = tk.StringVar(value=self._mode_text())
        self.endpoint_var.trace_add("write", lambda *_args: self.mode_var.set(self._mode_text()))
        self.connection_var = tk.StringVar(value="DISCONNECTED")
        self.position_var = tk.StringVar(value="01")
        self.cycle_var = tk.StringVar(value="0")
        self.operation_var = tk.StringVar(value="Standby")
        self.homed_var = tk.StringVar(value="NOT HOMED")
        self.shelf_buttons: list[tk.Button] = []
        self._build_ui()
        self._refresh_shelves()
        self._refresh_controls()
        self.after(350, self._poll_state)
        self._write_log("Dashboard ready. Connect to the selected controller, then Home before motion.")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=COLORS["background"], padx=22, pady=15)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(
            header, text="EUFYROBOT", bg=COLORS["background"], fg=COLORS["text"],
            font=("Bahnschrift SemiBold", 20),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header, text="THREE-AXIS MOTION CONTROL", bg=COLORS["background"],
            fg=COLORS["accent"], font=("Bahnschrift", 10),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Label(
            header, textvariable=self.mode_var, bg=COLORS["panel_alt"], fg=COLORS["accent"],
            font=("Bahnschrift SemiBold", 10), padx=12, pady=7,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        connect_bar = tk.Frame(self, bg=COLORS["panel"], padx=18, pady=11)
        connect_bar.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 14))
        connect_bar.grid_columnconfigure(1, weight=1)
        tk.Label(connect_bar, text="SERIAL ENDPOINT", bg=COLORS["panel"], fg=COLORS["muted"],
                 font=("Bahnschrift", 9)).grid(row=0, column=0, sticky="w", padx=(0, 12))
        endpoint_entry = tk.Entry(
            connect_bar, textvariable=self.endpoint_var, bg=COLORS["background"],
            fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat",
            font=("Consolas", 11),
        )
        endpoint_entry.grid(row=0, column=1, sticky="ew", ipady=7)
        self.connect_button = self._button(connect_bar, "CONNECT", self._connect, COLORS["blue"])
        self.connect_button.grid(row=0, column=2, padx=(12, 18))
        tk.Label(connect_bar, textvariable=self.connection_var, bg=COLORS["panel"],
                 fg=COLORS["muted"], font=("Consolas", 10), width=16).grid(row=0, column=3)

        body = tk.Frame(self, bg=COLORS["background"], padx=22)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, uniform="main")
        body.grid_columnconfigure(1, weight=2, uniform="main")
        body.grid_rowconfigure(0, weight=1)

        shelves_panel = self._panel(body, "SHELF READINESS")
        shelves_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        shelves_panel.grid_columnconfigure(0, weight=1)
        shelf_grid = tk.Frame(shelves_panel, bg=COLORS["panel"])
        shelf_grid.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 14))
        for column in range(4):
            shelf_grid.grid_columnconfigure(column, weight=1, uniform="shelf")
        for row in range(3):
            shelf_grid.grid_rowconfigure(row, weight=1, uniform="shelf")
        for index in range(N_SHELVES):
            button = tk.Button(
                shelf_grid, command=lambda index=index: self._toggle_ready(index),
                relief="flat", bd=0, cursor="hand2", font=("Bahnschrift SemiBold", 11),
                height=3, activeforeground=COLORS["text"],
            )
            button.grid(row=index // 4, column=index % 4, sticky="nsew", padx=5, pady=5)
            self.shelf_buttons.append(button)

        shelf_footer = tk.Frame(shelves_panel, bg=COLORS["panel"])
        shelf_footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        shelf_footer.grid_columnconfigure(0, weight=1)
        tk.Label(shelf_footer, text="Select a shelf to toggle substrate readiness", bg=COLORS["panel"],
                 fg=COLORS["muted"], font=("Bahnschrift", 9)).grid(row=0, column=0, sticky="w")
        self.ready_count_var = tk.StringVar(value="0 READY")
        tk.Label(shelf_footer, textvariable=self.ready_count_var, bg=COLORS["panel"],
                 fg=COLORS["accent"], font=("Consolas", 10, "bold")).grid(row=0, column=1, sticky="e")

        control_panel = self._panel(body, "MOTION CONTROL")
        control_panel.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        control_panel.grid_columnconfigure(0, weight=1)
        readouts = tk.Frame(control_panel, bg=COLORS["panel"])
        readouts.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        for column in range(3):
            readouts.grid_columnconfigure(column, weight=1)
        self._readout(readouts, "POSITION", self.position_var, 0)
        self._readout(readouts, "CYCLES", self.cycle_var, 1)
        self._readout(readouts, "AXES", self.homed_var, 2)

        actions = tk.Frame(control_panel, bg=COLORS["panel"])
        actions.grid(row=2, column=0, sticky="ew", padx=16)
        actions.grid_columnconfigure(0, weight=1)
        actions.grid_columnconfigure(1, weight=1)
        self.run_single_button = self._button(actions, "RUN SINGLE", self._run_single, COLORS["accent"])
        self.run_all_button = self._button(actions, "RUN ALL", self._run_all, COLORS["ready"])
        self.pause_button = self._button(actions, "PAUSE AFTER CYCLE", self._pause, COLORS["panel_alt"])
        self.stop_button = self._button(actions, "PAUSE IMMEDIATELY", self._immediate_stop, COLORS["danger"])
        self.home_button = self._button(actions, "HOME", self._home, COLORS["blue"])
        self.run_single_button.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=4)
        self.run_all_button.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=4)
        self.pause_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        self.stop_button.grid(row=2, column=0, sticky="ew", padx=(0, 5), pady=4)
        self.home_button.grid(row=2, column=1, sticky="ew", padx=(5, 0), pady=4)

        tk.Label(actions, text="CURRENT OPERATION", bg=COLORS["panel"], fg=COLORS["muted"],
                 font=("Bahnschrift", 9)).grid(row=3, column=0, columnspan=2, sticky="w", pady=(15, 3))
        tk.Label(actions, textvariable=self.operation_var, bg=COLORS["panel_alt"], fg=COLORS["text"],
                 anchor="w", padx=10, pady=10, font=("Consolas", 10)).grid(
                     row=4, column=0, columnspan=2, sticky="ew")

        config_frame = tk.Frame(control_panel, bg=COLORS["panel"])
        config_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(15, 12))
        config_frame.grid_columnconfigure(0, weight=1)
        self.configure_all_button = self._button(config_frame, "CONFIGURE SHELF POSITIONS", self._configure_all, COLORS["panel_alt"])
        self.configure_single_button = self._button(config_frame, "CONFIGURE SINGLE SHELF", self._configure_single, COLORS["panel_alt"])
        self.configure_all_button.grid(row=0, column=0, sticky="ew", pady=3)
        self.configure_single_button.grid(row=1, column=0, sticky="ew", pady=3)

        log_panel = tk.Frame(self, bg=COLORS["panel"], padx=14, pady=10)
        log_panel.grid(row=3, column=0, sticky="nsew", padx=22, pady=(14, 18))
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)
        tk.Label(log_panel, text="EVENT LOG", bg=COLORS["panel"], fg=COLORS["muted"],
                 font=("Bahnschrift SemiBold", 9)).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.log_text = tk.Text(
            log_panel, height=6, bg=COLORS["background"], fg=COLORS["text"],
            insertbackground=COLORS["text"], relief="flat", wrap="word",
            font=("Consolas", 9), state="disabled",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(log_panel, command=self.log_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _panel(self, parent: tk.Widget, title: str) -> tk.Frame:
        panel = tk.Frame(parent, bg=COLORS["panel"], highlightthickness=1,
                         highlightbackground=COLORS["border"])
        panel.grid_rowconfigure(1, weight=1)
        tk.Label(panel, text=title, bg=COLORS["panel"], fg=COLORS["accent"],
                 font=("Bahnschrift SemiBold", 10), anchor="w").grid(
                     row=0, column=0, sticky="ew", padx=16, pady=(14, 12))
        return panel

    def _mode_text(self) -> str:
        endpoint = self.endpoint_var.get().strip().lower()
        if endpoint.startswith("rfc2217://"):
            return "VIRTUAL COMPONENTS"
        if endpoint:
            return "REAL COMPONENTS"
        return "REAL COMPONENTS" if USE_REAL_COMPONENTS else "VIRTUAL COMPONENTS"

    def _button(self, parent: tk.Widget, text: str, command: Callable, color: str) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=color, fg=COLORS["text"],
            activebackground=COLORS["accent"], activeforeground=COLORS["background"],
            relief="flat", bd=0, padx=10, pady=10, cursor="hand2",
            font=("Bahnschrift SemiBold", 9),
        )

    def _readout(self, parent: tk.Widget, title: str, variable: tk.StringVar, column: int) -> None:
        frame = tk.Frame(parent, bg=COLORS["panel_alt"], padx=8, pady=8)
        frame.grid(row=0, column=column, sticky="nsew", padx=3)
        tk.Label(frame, text=title, bg=COLORS["panel_alt"], fg=COLORS["muted"],
                 font=("Bahnschrift", 8)).pack(anchor="w")
        tk.Label(frame, textvariable=variable, bg=COLORS["panel_alt"], fg=COLORS["text"],
                 font=("Consolas", 11, "bold")).pack(anchor="w", pady=(5, 0))

    def _refresh_shelves(self) -> None:
        for index, button in enumerate(self.shelf_buttons):
            ready = self.runner.substrate_ready[index]
            state = "READY" if ready else "NOT READY"
            button.configure(
                text=f"SHELF {index + 1:02d}\n{state}",
                bg=COLORS["ready"] if ready else COLORS["empty"],
                fg=COLORS["text"],
                state="disabled" if self._worker_active else "normal",
            )
        self.ready_count_var.set(f"{sum(self.runner.substrate_ready)} READY")

    def _refresh_controls(self) -> None:
        connected = self.link.connected
        busy = self._worker_active
        self.connection_var.set("CONNECTED" if connected else "DISCONNECTED")
        self.homed_var.set("HOMED" if self.link.homed else "HOME REQUIRED")
        self.position_var.set(f"{self.runner.current_position:02d}")
        self.cycle_var.set(str(self.runner.completed_cycles))
        self.connect_button.configure(state="disabled" if busy else "normal")
        self.connect_button.configure(text="DISCONNECT" if connected else "CONNECT")
        self.run_single_button.configure(state="normal" if connected and self.link.homed and not busy else "disabled")
        self.run_all_button.configure(state="normal" if connected and self.link.homed and not busy and any(self.runner.substrate_ready) else "disabled")
        self.pause_button.configure(state="normal" if busy and self._pauseable_operation else "disabled")
        self.stop_button.configure(state="normal" if connected else "disabled")
        self.home_button.configure(state="normal" if connected and not busy else "disabled")
        self.configure_all_button.configure(state="normal" if not busy else "disabled")
        self.configure_single_button.configure(state="normal" if not busy else "disabled")
        self._refresh_shelves()

    def _toggle_ready(self, index: int) -> None:
        if self._worker_active:
            return
        self.runner.substrate_ready[index] = not self.runner.substrate_ready[index]
        self._refresh_controls()

    def _connect(self) -> None:
        if self.link.connected:
            self._launch_worker("Disconnecting", self.link.disconnect, require_connected=False)
            return
        endpoint = self.endpoint_var.get().strip()
        if not endpoint:
            messagebox.showerror("Connection", "Enter a serial endpoint or COM port.", parent=self)
            return
        self.link.endpoint = endpoint
        self._launch_worker("Connecting", self.link.connect, require_connected=False)

    def _run_single(self) -> None:
        self.runner.pause_requested.clear()
        self._launch_worker("Running one cycle", self.runner.run_one, require_homed=True, pauseable=True)

    def _run_all(self) -> None:
        self.runner.pause_requested.clear()
        self._launch_worker("Running all ready shelves", self.runner.run_all, require_homed=True, pauseable=True)

    def _pause(self) -> None:
        self.runner.request_pause()
        self.operation_var.set("Pause requested; finishing current cycle")
        self._write_log("Pause requested; no new cycle will start after the active cycle.")

    def _immediate_stop(self) -> None:
        if not self.link.connected:
            return
        self.runner.immediate_stop_requested.set()
        self.operation_var.set("Immediate stop sent; Home required")
        self._write_log("Immediate stop requested. Axis positions are no longer trusted.")
        threading.Thread(target=self._send_stop, name="immediate-stop", daemon=True).start()

    def _send_stop(self) -> None:
        try:
            self.link.stop_immediately()
        except Exception as exc:
            self.after(0, lambda message=str(exc): self._write_log(f"Stop command failed: {message}"))
        self.after(0, self._refresh_controls)

    def _home(self) -> None:
        self._launch_worker("Homing all axes", self.runner.reset_after_home, require_connected=True)

    def _launch_worker(self, label: str, operation: Callable, require_homed: bool = False,
                       require_connected: bool = True, pauseable: bool = False) -> None:
        if self._worker_active:
            return
        if require_connected and not self.link.connected:
            messagebox.showwarning("Motion unavailable", "Connect to the controller first.", parent=self)
            return
        if require_homed and not self.link.homed:
            messagebox.showwarning("Home required", "Home all three axes before running a cycle.", parent=self)
            return
        self._worker_active = True
        self._pauseable_operation = pauseable
        self.operation_var.set(label)
        self._refresh_controls()
        self._write_log(label)

        def worker() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._worker_finished(label, message))
            else:
                self.after(0, lambda value=result: self._worker_finished(label, None, value))

        threading.Thread(target=worker, name="robot-operation", daemon=True).start()

    def _worker_finished(self, label: str, error: Optional[str], result=None) -> None:
        self._worker_active = False
        self._pauseable_operation = False
        if error:
            self.operation_var.set("Fault: Home required" if self.runner.immediate_stop_requested.is_set() else "Operation failed")
            self._write_log(f"{label} failed: {error}")
            if label == "Connecting":
                messagebox.showerror("Connection failed", error, parent=self)
        else:
            self.operation_var.set("Standby" if result is None else "Run complete")
            if isinstance(result, list):
                self._write_log(f"Run complete: {len(result)} shelf(s) processed.")
            elif isinstance(result, int):
                self._write_log(f"Shelf {result} cycle complete.")
            elif result is None:
                self._write_log(f"{label} complete.")
        self._refresh_controls()

    def _post_progress(self, message: str) -> None:
        self.after(0, lambda: self.operation_var.set(message))

    def _configure_all(self) -> None:
        self._open_offset_dialog(list(range(N_SHELVES)))

    def _configure_single(self) -> None:
        shelf = simpledialog.askinteger(
            "Configure Single Shelf Position", "Shelf position (1-12):",
            minvalue=1, maxvalue=N_SHELVES, parent=self,
        )
        if shelf is not None:
            self._open_offset_dialog([shelf - 1])

    def _open_offset_dialog(self, indices: list[int]) -> None:
        offsets = self.settings.offsets_mm.copy()
        current = 0
        dialog = tk.Toplevel(self)
        dialog.title("Shelf Position Calibration")
        dialog.configure(bg=COLORS["background"])
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("410x300")

        tk.Label(dialog, text="SHELF POSITION", bg=COLORS["background"], fg=COLORS["accent"],
                 font=("Bahnschrift SemiBold", 10)).pack(pady=(24, 8))
        position_label = tk.Label(dialog, bg=COLORS["background"], fg=COLORS["text"],
                                  font=("Bahnschrift SemiBold", 21))
        position_label.pack()
        offset_label = tk.Label(dialog, bg=COLORS["background"], fg=COLORS["text"],
                                font=("Consolas", 18))
        offset_label.pack(pady=12)

        adjust = tk.Frame(dialog, bg=COLORS["background"])
        adjust.pack()
        self._button(adjust, "- 1 mm", lambda: adjust_offset(-SHELF_OFFSET_STEP_MM), COLORS["panel_alt"]).grid(row=0, column=0, padx=6)
        self._button(adjust, "+ 1 mm", lambda: adjust_offset(SHELF_OFFSET_STEP_MM), COLORS["panel_alt"]).grid(row=0, column=1, padx=6)

        buttons = tk.Frame(dialog, bg=COLORS["background"])
        buttons.pack(side="bottom", fill="x", padx=22, pady=20)
        next_button = self._button(buttons, "NEXT", lambda: advance(), COLORS["accent"])
        next_button.pack(side="right", padx=(8, 0))
        self._button(buttons, "CANCEL", dialog.destroy, COLORS["panel_alt"]).pack(side="right")

        def render() -> None:
            shelf_index = indices[current]
            position_label.configure(text=f"SHELF {shelf_index + 1:02d} / {N_SHELVES:02d}")
            offset_label.configure(text=f"{offsets[shelf_index]:+.1f} mm")
            next_button.configure(text="SAVE & EXIT" if current == len(indices) - 1 else "NEXT")

        def adjust_offset(delta: float) -> None:
            shelf_index = indices[current]
            offsets[shelf_index] += delta
            render()

        def advance() -> None:
            nonlocal current
            if current == len(indices) - 1:
                self.settings.offsets_mm = offsets
                self.settings.save()
                self._write_log("Shelf position offsets saved.")
                dialog.destroy()
            else:
                current += 1
                render()

        render()

    def _write_log(self, message: str) -> None:
        if self._closing:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_state(self) -> None:
        if self._closing:
            return
        self._refresh_controls()
        self.after(350, self._poll_state)

    def _on_close(self) -> None:
        self._closing = True
        if self._worker_active and self.link.connected:
            self.runner.immediate_stop_requested.set()
            try:
                self.link.stop_immediately()
            except Exception:
                pass
        self.link.disconnect()
        self.destroy()


def main() -> None:
    # Standalone GUI-only launch (`python dashboard.py`); master_control.py is the documented entry point.
    parser = argparse.ArgumentParser(description="EufyRobot three-axis dashboard")
    parser.add_argument("--port", help="serial URL or COM port; defaults from USE_REAL_COMPONENTS")
    arguments = parser.parse_args()
    app = EufyDashboard(endpoint=arguments.port)
    app.mainloop()


if __name__ == "__main__":
    main()
