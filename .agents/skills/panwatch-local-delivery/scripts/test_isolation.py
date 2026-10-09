"""Isolation checks use synthetic repositories and never import the real server."""

import importlib.util
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("isolated_qa", Path(__file__).with_name("isolated_qa.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


class IsolationTests(unittest.TestCase):
    def test_active_listener_is_rejected_but_closed_connection_can_restart(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            port = server.getsockname()[1]
            server.listen(1)
            with self.assertRaises(qa.IsolationError):
                qa.assert_port_available(port)
            with socket.create_connection(("127.0.0.1", port)) as client:
                accepted, _ = server.accept()
                accepted.close()  # Server side enters TIME_WAIT after peer closes.
        qa.assert_port_available(port)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        files = {
            ".gitignore": ".env\ndata/\n.docs/\n.venv/\n.claude/\n",
            str(qa.SKILL_PATH / "SKILL.md"): "---\nname: panwatch-local-delivery\ndescription: Test\nmetadata:\n  version: '1.0.0'\n---\nTest\n",
            "server.py": "import os\nstatic_dir = os.path.join(os.path.dirname(__file__), 'static')\nbundle_path = os.path.join(os.path.dirname(__file__), 'data', 'ca-bundle.pem')\n",
            "src/platform/persistence/database.py": "import os\nDB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'panwatch.db')\n",
            "src/platform/marketdata/stock_list.py": "from pathlib import Path\nPROJECT_ROOT = Path(__file__).resolve().parents[3]\nDATA_DIR = PROJECT_ROOT / 'data'\n",
            "src/platform/runtime/config.py": "class Settings:\n    model_config = {'env_file': '.env'}\n",
            "tracked.py": "original = True\n",
            "deleted.py": "remove_me = True\n",
        }
        for name in qa.LOCAL_MODULES:
            files[f"packages/{name}/src/{name}/__init__.py"] = "# Source origin fixture\n"
        for relative, text in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def prepare(self):
        return qa.prepare(self.repo, self.root, Path(sys.executable))

    def test_snapshot_captures_current_changes_without_existing_data(self):
        (self.repo / "tracked.py").write_text("modified = True\n")
        (self.repo / "deleted.py").unlink()
        (self.repo / "new.py").write_text("new = True\n")
        (self.repo / ".env").write_text("AI_API_KEY=existing-secret\n")
        (self.repo / "AGENTS.md").write_text("Local rules\n")
        (self.repo / "data").mkdir()
        original_db = self.repo / "data/panwatch.db"
        original_db.write_bytes(b"protected-existing-db")
        run = self.prepare()
        self.assertEqual((run / "source/tracked.py").read_text(), "modified = True\n")
        self.assertTrue((run / "source/new.py").exists())
        self.assertFalse((run / "source/deleted.py").exists())
        self.assertFalse((run / "source/AGENTS.md").exists())
        self.assertEqual((run / "source/.env").read_bytes(), b"")
        self.assertFalse((run / "source/data/panwatch.db").exists())
        self.assertEqual(original_db.read_bytes(), b"protected-existing-db")
        self.assertEqual(run.stat().st_mode & 0o777, 0o700)
        qa.preflight(run, check_source=True)

    def test_run_inside_checkout_is_rejected_before_creation(self):
        with self.assertRaises(qa.IsolationError):
            qa.prepare(self.repo, self.repo, Path(sys.executable))
        self.assertFalse(any(self.repo.glob("panwatch-qa-*")))

    def test_source_symlink_to_external_file_is_rejected(self):
        secret = self.root / "secret"
        secret.write_text("private")
        (self.repo / "leak.py").symlink_to(secret)
        with self.assertRaises(qa.IsolationError):
            self.prepare()

    def test_changed_original_source_requires_new_run(self):
        run = self.prepare()
        (self.repo / "tracked.py").write_text("changed = True\n")
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run, check_source=True)
        qa.preflight(run)  # A fixed-version snapshot remains independently identifiable.

    def test_changed_snapshot_or_added_code_is_rejected(self):
        run = self.prepare()
        injected = run / "source/injected.py"
        injected.write_text("unexpected = True\n")
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)

    def test_known_path_mapping_change_fails_without_importing_server(self):
        path = self.repo / "src/platform/persistence/database.py"
        path.write_text("DB_PATH = '/outside/panwatch.db'\n")
        with self.assertRaises(qa.IsolationError):
            self.prepare()
        self.assertFalse((self.repo / "data").exists())

    def test_database_symlink_is_rejected_and_original_remains_intact(self):
        run = self.prepare()
        protected = self.root / "existing.db"
        protected.write_bytes(b"protected")
        (run / "source/data/panwatch.db").symlink_to(protected)
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)
        self.assertEqual(protected.read_bytes(), b"protected")

    def test_nonempty_dotenv_is_rejected(self):
        run = self.prepare()
        (run / "source/.env").write_text("AI_API_KEY=unexpected\n")
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)

    def test_permission_and_ownership_mismatch_are_rejected(self):
        run = self.prepare()
        run.chmod(0o755)
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)
        run.chmod(0o700)
        (run / ".panwatch-qa-run").write_text("other-run")
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)

    def test_manifest_escape_path_is_rejected(self):
        run = self.prepare()
        manifest = json.loads((run / "manifest.json").read_text())
        manifest["paths"]["database"] = "../existing.db"
        qa.write_json(run / "manifest.json", manifest)
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run)

    def test_launch_environment_excludes_existing_service_credentials(self):
        run = self.prepare()
        existing = {"AI_API_KEY": "private-ai", "NOTIFY_TELEGRAM_BOT_TOKEN": "private-notify", "HTTP_PROXY": "http://private-proxy", "OTEL_EXPORTER_OTLP_ENDPOINT": "private-otel", "PYTHONPATH": "/existing/source"}
        with patch.dict(os.environ, existing):
            env = qa.child_environment(run)
        self.assertTrue(set(existing).isdisjoint(env))
        self.assertEqual(env["DATA_DIR"], str(run / "source/data"))
        self.assertEqual(env["PLAYWRIGHT_SKIP_BROWSER_INSTALL"], "1")
        self.assertTrue(env["AUTH_USERNAME"].startswith("qa-"))
        self.assertNotEqual(env["AUTH_PASSWORD"], "private-ai")

    def test_frontend_seal_and_tamper_detection(self):
        run = self.prepare()
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run, require_frontend=True)
        dist = run / "source/frontend/dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html>synthetic build fixture</html>")
        qa.seal_frontend(run)
        qa.preflight(run, require_frontend=True)
        (run / "source/static/index.html").write_text("different build")
        with self.assertRaises(qa.IsolationError):
            qa.preflight(run, require_frontend=True)

    def test_snapshot_module_origin_checks_use_exported_packages(self):
        run = self.prepare()
        probe = qa.BOOTSTRAP.split("from src.platform.persistence import database")[0] + "\nprint(json.dumps(origins))\n"
        result = subprocess.run([sys.executable, "-c", probe, str(run)], cwd=run / "source", capture_output=True, text=True, check=True)
        origins = json.loads(result.stdout)
        self.assertEqual(set(origins), set(qa.LOCAL_MODULES))
        self.assertTrue(all(Path(origin).is_relative_to(run / "source") for origin in origins.values()))

    def test_module_origin_check_refuses_original_editable_fallback(self):
        run = self.prepare()
        (run / "source/packages/marketdata/src/marketdata/__init__.py").unlink()
        probe = qa.BOOTSTRAP.split("from src.platform.persistence import database")[0]
        env = dict(os.environ, PYTHONPATH=str(self.repo / "packages/marketdata/src"))
        result = subprocess.run([sys.executable, "-c", probe, str(run)], cwd=run / "source", env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("QA module origin rejected: marketdata", result.stderr)


if __name__ == "__main__":
    unittest.main()
