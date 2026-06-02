import tempfile
import unittest
from pathlib import Path
from unittest import mock

import runners.cli as cli


class CliWorkflowTests(unittest.TestCase):
    def test_default_stages_exclude_arraylake(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "confignmme.yaml"
            config_path.write_text("models: [NASA-GEOSS2S]\n", encoding="utf-8")
            calls = []

            def fake_run_cmd(command, log_path):
                calls.append((command, log_path))

            with mock.patch.object(cli, "run_cmd", side_effect=fake_run_cmd):
                with mock.patch("sys.argv", [
                    "cli.py",
                    "--system",
                    "nmme",
                    "--config",
                    str(config_path),
                    "--init",
                    "202601",
                ]):
                    exit_code = cli.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(all("arraylake/run_arraylake_rt.sh" not in " ".join(call[0]) for call in calls))

    def test_arraylake_stage_dispatch_and_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "confignmme.yaml"
            config_path.write_text(
                """
models: [NASA-GEOSS2S]
arraylake:
  enabled: true
""",
                encoding="utf-8",
            )
            calls = []

            def fake_run_cmd(command, log_path):
                calls.append((command, log_path))

            with mock.patch.object(cli, "run_cmd", side_effect=fake_run_cmd):
                with mock.patch("sys.argv", [
                    "cli.py",
                    "--system",
                    "nmme",
                    "--config",
                    str(config_path),
                    "--init",
                    "202601",
                    "--stages",
                    "arraylake",
                    "--arraylake-dry-run",
                ]):
                    exit_code = cli.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(calls), 1)
            self.assertIn("arraylake/run_arraylake_rt.sh", " ".join(calls[0][0]))
            self.assertIn("ARRAYLAKE_DRY_RUN=1", calls[0][0][2])


if __name__ == "__main__":
    unittest.main()