"""Robot-owned settings for the optional OrderAutomation integration."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
SIBLING_ORDER_AUTOMATION = Path(__file__).resolve().parents[1].parent / "OrderAutomation"

DEFAULT_CONFIG = {
    "orderautomation_path": str(SIBLING_ORDER_AUTOMATION) if SIBLING_ORDER_AUTOMATION.is_dir() else "",
    "orderautomation_python": "",
    "packing_slip": True,
    "add_packing_slip_to_order": True,
    "print_mailing_label": True,
    "queue_multi_print_orders": False,
    "cleanup": False,
    "eufymake_dir": str(Path.home() / "Desktop" / "PrintFolder"),
    "downloaded_assets_dir": str(Path.home() / "Desktop" / "DownloadedAssets"),
    "packing_slip_dir": str(Path.home() / "Desktop" / "PackingSlips"),
    "gcs_bucket_name": "my-ccpf-assets-bucket",
    "company": {
        "name": "",
        "address_line1": "",
        "city_state_zip": "",
        "email": "",
        "website": "",
    },
}


def load_robot_config(path: Path = CONFIG_PATH) -> dict:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    if not isinstance(saved, dict):
        raise ValueError("Robot config.json must contain a JSON object.")
    config = {**DEFAULT_CONFIG, **saved}
    config["company"] = {**DEFAULT_CONFIG["company"], **saved.get("company", {})}
    return config


def save_robot_config(config: dict, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")