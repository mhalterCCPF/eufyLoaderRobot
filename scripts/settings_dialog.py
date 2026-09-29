"""Settings dialog for OrderAutomation integration and artifact options."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from robot_config import load_robot_config, save_robot_config


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_saved):
        super().__init__(parent)
        self.parent = parent
        self.on_saved = on_saved
        self.title("EufyRobot Settings")
        self.geometry("650x720")
        self.minsize(560, 600)
        self.transient(parent)
        self.grab_set()
        self.config_data = load_robot_config()
        self.vars = {}
        self.flags = {}

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=18)
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._section(content, "OrderAutomation")
        self._path_row(content, "Repository path", "orderautomation_path", directory=True)
        self._path_row(content, "Python interpreter (optional)", "orderautomation_python", file=True)

        self._section(content, "Order Options")
        for key, label in (
            ("packing_slip", "Generate packing slip"),
            ("add_packing_slip_to_order", "Attach packing slip to Shopify order"),
            ("print_mailing_label", "Mailing label option (placeholder)"),
            ("queue_multi_print_orders", "Queue multi-print orders"),
            ("cleanup", "Clean up downloaded assets after printing"),
        ):
            variable = tk.BooleanVar(value=self.config_data.get(key, False))
            self.flags[key] = variable
            ttk.Checkbutton(content, text=label, variable=variable).pack(anchor="w", pady=3)

        self._section(content, "Directories and GCS")
        self._path_row(content, "Printer directory", "eufymake_dir", directory=True)
        self._path_row(content, "Downloaded assets directory", "downloaded_assets_dir", directory=True)
        self._path_row(content, "Packing slip directory", "packing_slip_dir", directory=True)
        self._text_row(content, "GCS bucket", "gcs_bucket_name")

        self._section(content, "Company Information")
        for key, label in (
            ("name", "Company name"),
            ("address_line1", "Address"),
            ("city_state_zip", "City, state, ZIP"),
            ("email", "Email"),
            ("website", "Website"),
        ):
            self._text_row(content, label, f"company.{key}")

        footer = ttk.Frame(self, padding=12)
        footer.pack(fill="x")
        ttk.Button(footer, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="Save Settings", command=self._save).pack(side="right", padx=(0, 8))

    @staticmethod
    def _section(parent, title):
        ttk.Label(parent, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(12, 5))

    def _text_row(self, parent, label, key):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(5, 1))
        variable = tk.StringVar(value=self._get_value(key))
        self.vars[key] = variable
        ttk.Entry(parent, textvariable=variable).pack(fill="x", pady=(0, 4))

    def _path_row(self, parent, label, key, directory=False, file=False):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(5, 1))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 4))
        variable = tk.StringVar(value=self._get_value(key))
        self.vars[key] = variable
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        if directory:
            command = lambda: self._browse_directory(variable)
        else:
            command = lambda: self._browse_file(variable)
        ttk.Button(row, text="Browse", command=command).pack(side="left", padx=(6, 0))

    def _get_value(self, key):
        if key.startswith("company."):
            return self.config_data.get("company", {}).get(key.split(".", 1)[1], "")
        return self.config_data.get(key, "")

    @staticmethod
    def _browse_directory(variable):
        selected = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()))
        if selected:
            variable.set(selected)

    @staticmethod
    def _browse_file(variable):
        selected = filedialog.askopenfilename(initialdir=str(Path.home()), filetypes=[("Python", "python.exe"), ("All files", "*.*")])
        if selected:
            variable.set(selected)

    def _save(self):
        repository_path = self.vars["orderautomation_path"].get().strip()
        if repository_path and not (Path(repository_path) / "modules" / "order_automation_cli.py").is_file():
            messagebox.showerror("Invalid path", "Select an OrderAutomation repository containing its headless CLI.", parent=self)
            return

        config = dict(self.config_data)
        for key, variable in self.flags.items():
            config[key] = variable.get()
        for key, variable in self.vars.items():
            if key.startswith("company."):
                config.setdefault("company", {})[key.split(".", 1)[1]] = variable.get().strip()
            else:
                config[key] = variable.get().strip()
        save_robot_config(config)
        self.on_saved()
        self.destroy()
