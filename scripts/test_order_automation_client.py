import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from order_automation_client import OrderAutomationClient
from robot_config import DEFAULT_CONFIG, load_robot_config, save_robot_config


class OrderAutomationClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        (self.root / "modules").mkdir()
        (self.root / "modules" / "order_automation_cli.py").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_robot_config_defaults_and_round_trip(self):
        path = self.root / "config.json"
        config = load_robot_config(path)
        self.assertEqual(config["packing_slip"], DEFAULT_CONFIG["packing_slip"])
        self.assertIn("company", config)
        config["cleanup"] = True
        save_robot_config(config, path)
        self.assertTrue(load_robot_config(path)["cleanup"])

    def test_client_sends_versioned_request_and_runtime_options(self):
        response = SimpleNamespace(returncode=0, stdout='{"version": 1, "status": "no_orders"}', stderr="")
        client = OrderAutomationClient(
            str(self.root),
            runtime_config={"packing_slip": False, "orderautomation_python": "ignored"},
        )
        with patch("order_automation_client.subprocess.run", return_value=response) as run:
            result = client.prepare_next_order()

        self.assertEqual(result["status"], "no_orders")
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["version"], 1)
        self.assertEqual(request["action"], "prepare_next_order")
        self.assertEqual(request["config"], {"packing_slip": False})
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_client_subprocess_round_trip_with_fake_cli(self):
        (self.root / "modules" / "order_automation_cli.py").write_text(
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "print(json.dumps({'version': 1, 'status': 'received', 'action': request['action']}))\n",
            encoding="utf-8",
        )
        client = OrderAutomationClient(str(self.root), python_path=sys.executable)

        response = client.prepare_next_order()

        self.assertEqual(response, {"version": 1, "status": "received", "action": "prepare_next_order"})

    def test_client_reports_cli_errors_and_malformed_output(self):
        client = OrderAutomationClient(str(self.root))
        failed = SimpleNamespace(
            returncode=1,
            stdout='{"version": 1, "status": "error", "error": {"message": "bad request"}}',
            stderr="",
        )
        with patch("order_automation_client.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "bad request"):
                client.prepare_next_order()

        malformed = SimpleNamespace(returncode=0, stdout="not json", stderr="traceback")
        with patch("order_automation_client.subprocess.run", return_value=malformed):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                client.prepare_next_order()


if __name__ == "__main__":
    unittest.main()