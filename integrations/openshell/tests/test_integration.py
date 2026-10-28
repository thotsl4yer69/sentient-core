"""Hermetic contract tests; no external hardware or OpenShell required."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

preflight = mod("preflight")
agent = mod("diagnostic_agent")

class IntegrationTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(preflight.kernel_tuple("5.15.148-tegra"), (5, 15))
        self.assertEqual(preflight.kernel_tuple("6.8.0-foo"), (6, 8))
        self.assertEqual(preflight.parse_version("Docker version 28.5.1"), (28, 5, 1))

    def test_preflight_blocks_old_kernel(self):
        with patch.object(preflight.shutil, "which", return_value=None):
            result = preflight.probe(release="5.15.148-tegra", arch="aarch64", landlock_probe=lambda: 2)
            self.assertFalse(result["ready_for_install"])
            self.assertTrue(any("Kernel" in reason for reason in result["blocking"]))

    def test_preflight_accepts_ready_host(self):
        class Completed:
            returncode = 0
            stdout = "28.1.0"
        with patch.object(preflight.shutil, "which", return_value="/usr/bin/docker"):
            with patch.object(preflight, "run", return_value=Completed()):
                result = preflight.probe(release="6.8.0", arch="aarch64", landlock_probe=lambda: 5)
        self.assertTrue(result["ready_for_install"])

    def test_endpoint_fail_closed(self):
        self.assertEqual(agent.endpoints(agent.DEFAULT_URL), "http://host.openshell.internal:11435")
        for bad in ("http://192.168.1.160:11434/v1", "http://host.openshell.internal:11434/v1",
                    "https://host.openshell.internal:11435/v1",
                    "http://host.openshell.internal:11435/v1?x=1",
                    "http://bad@host.openshell.internal:11435/v1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                agent.endpoints(bad)

    def test_model_selection(self):
        tags = {"models": [{"name": "big:latest"}, {"name": "llama3.2:3b-instruct"}]}
        self.assertEqual(agent.select_model(tags), "llama3.2:3b-instruct")
        self.assertEqual(agent.select_model(tags, "big:latest"), "big:latest")
        with self.assertRaises(ValueError):
            agent.select_model(tags, "not-installed")
        with self.assertRaises(ValueError):
            agent.select_model({"models": []})

    def test_check_only_fetches_tags(self):
        with patch.object(agent, "request_json", return_value={"models": [{"name": "llama3.2:3b"}]}) as req:
            self.assertEqual(agent.main(["--check"]), 0)
            req.assert_called_once_with("http://host.openshell.internal:11435/api/tags")

    def test_chat_uses_native_ollama_api(self):
        with patch.object(agent, "request_json", side_effect=[
            {"models": [{"name": "llama3.2:3b"}]},
            {"choices": [{"message": {"content": "SENTIENT CORE CONNECTED"}}]}
        ]) as req:
            self.assertEqual(agent.main(["--prompt", "test"]), 0)
            args = req.call_args.args
            self.assertEqual(args[0], "http://host.openshell.internal:11435/v1/chat/completions")
            self.assertEqual(args[1]["model"], "llama3.2:3b")
            self.assertFalse(args[1]["stream"])

if __name__ == "__main__":
    unittest.main()
