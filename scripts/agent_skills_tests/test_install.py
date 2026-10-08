"""Project boundaries and ownership tests; do not invoke agent CLIs."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("installer", Path(__file__).resolve().parents[1] / "install-agent-skills.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = (Path(self.temp.name) / "project").resolve()
        self.source = self.project / ".agents/skills/example"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: example\ndescription: Example project skill\n---\nContent\n")
        (self.source / "references").mkdir()
        (self.source / "references/flow.md").write_text("Initial flow\n")
        self.target = self.project / ".claude/skills/example"

    def install(self, **kwargs):
        return installer.operate(self.project, ("codex", "claude", "cursor", "gemini"), **kwargs)

    def test_native_agents_need_no_extra_directory(self):
        installer.operate(self.project, ("codex", "cursor", "gemini"))
        self.assertEqual({p.name for p in self.project.iterdir()}, {".agents"})

    def test_relative_link_is_idempotent_and_tracks_source(self):
        self.install()
        link = self.target.readlink()
        self.assertFalse(link.is_absolute())
        self.assertEqual(self.target.resolve(), self.source)
        self.install()
        (self.source / "references/flow.md").write_text("Updated flow\n")
        self.assertEqual((self.target / "references/flow.md").read_text(), "Updated flow\n")
        self.install(check=True)

    def test_dry_run_writes_nothing(self):
        self.install(dry_run=True)
        self.assertFalse((self.project / ".claude").exists())

    def test_uninstall_keeps_source_and_other_skills(self):
        self.install()
        other = self.target.parent / "personal"
        other.mkdir()
        (other / "SKILL.md").write_text("User-owned\n")
        self.install(uninstall=True)
        self.assertFalse(self.target.exists())
        self.assertTrue((other / "SKILL.md").exists())
        self.assertTrue((self.source / "SKILL.md").exists())

    def test_existing_user_directory_is_preserved(self):
        self.target.mkdir(parents=True)
        (self.target / "SKILL.md").write_text("User-owned\n")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual((self.target / "SKILL.md").read_text(), "User-owned\n")

    def test_symlinked_agent_root_cannot_write_outside_project(self):
        external = Path(self.temp.name) / "global-profile"
        external.mkdir()
        (self.project / ".claude").symlink_to(external, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(list(external.iterdir()), [])

    def test_other_target_link_is_preserved(self):
        other = (Path(self.temp.name) / "other-skill").resolve()
        other.mkdir()
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(other, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(self.target.resolve(), other)

    def test_matching_unmanaged_link_is_not_adopted_or_uninstalled(self):
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(self.source, target_is_directory=True)
        self.install()
        with self.assertRaises(installer.InstallError):
            self.install(uninstall=True)
        self.assertTrue(self.target.is_symlink())

    def test_copy_updates_only_unmodified_owned_content(self):
        self.install(mode="copy")
        self.assertFalse(self.target.is_symlink())
        (self.source / "references/flow.md").write_text("Updated flow\n")
        with self.assertRaises(installer.InstallError):
            self.install(check=True)
        self.install(mode="copy")
        self.install(check=True)
        self.assertEqual((self.target / "references/flow.md").read_text(), "Updated flow\n")

    def test_local_copy_changes_block_update_and_uninstall(self):
        self.install(mode="copy")
        (self.target / "references/flow.md").write_text("User edit\n")
        for options in ({"mode": "copy"}, {"uninstall": True}):
            with self.assertRaises(installer.InstallError):
                self.install(**options)
        self.assertEqual((self.target / "references/flow.md").read_text(), "User edit\n")

    def test_source_symlinks_cannot_copy_external_content(self):
        private = Path(self.temp.name) / "secret"
        private.write_text("private")
        (self.source / "references/secret").symlink_to(private)
        with self.assertRaises(installer.InstallError):
            self.install(mode="copy")
        self.assertFalse((self.project / ".claude").exists())

    def test_malformed_receipt_does_not_grant_ownership(self):
        self.install()
        receipt = self.target.parent / ".example.panwatch-install.json"
        receipt.write_text(json.dumps({"owner": "someone-else"}))
        with self.assertRaises(installer.InstallError):
            self.install(uninstall=True)
        self.assertTrue(self.target.is_symlink())


if __name__ == "__main__":
    unittest.main()
