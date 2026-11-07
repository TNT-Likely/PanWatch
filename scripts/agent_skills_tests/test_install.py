"""Project boundaries and ownership tests; do not invoke agent CLIs."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_hardlink_shares_every_resource_inode_and_is_idempotent(self):
        self.install(mode="hardlink")
        self.assertFalse(self.target.is_symlink())
        for relative in installer.inventory(self.source):
            with self.subTest(relative=relative):
                source = self.source / relative
                target = self.target / relative
                self.assertFalse(target.is_symlink())
                self.assertTrue(target.samefile(source))
                self.assertEqual(target.stat().st_ino, source.stat().st_ino)
                self.assertGreaterEqual(source.stat().st_nlink, 2)
        inode = (self.target / "SKILL.md").stat().st_ino
        self.install(mode="hardlink")
        self.assertEqual((self.target / "SKILL.md").stat().st_ino, inode)
        (self.source / "references/flow.md").write_text("Updated flow\n")
        self.assertEqual((self.target / "references/flow.md").read_text(), "Updated flow\n")
        self.install(check=True)
        self.install(mode="hardlink")
        self.install(uninstall=True)
        self.assertEqual((self.source / "references/flow.md").read_text(), "Updated flow\n")
        self.assertEqual((self.source / "SKILL.md").stat().st_nlink, 1)

    def test_hardlink_refreshes_atomic_replacements_even_with_same_content(self):
        self.install(mode="hardlink")
        source = self.source / "references/flow.md"
        for content in ("Initial flow\n", "Updated flow\n"):
            with self.subTest(content=content):
                replacement = self.source / "references/replacement.md"
                replacement.write_text(content)
                replacement.replace(source)
                self.assertFalse((self.target / "references/flow.md").samefile(source))
                with self.assertRaises(installer.InstallError):
                    self.install(check=True)
                self.install(mode="hardlink")
                self.assertTrue((self.target / "references/flow.md").samefile(source))
                self.install(check=True)

    def test_hardlink_refreshes_added_and_removed_resources(self):
        self.install(mode="hardlink")
        (self.source / "references/flow.md").unlink()
        (self.source / "references/new.md").write_text("New resource\n")
        with self.assertRaises(installer.InstallError):
            self.install(check=True)
        self.install(mode="hardlink")
        self.assertFalse((self.target / "references/flow.md").exists())
        self.assertTrue((self.target / "references/new.md").samefile(self.source / "references/new.md"))
        self.install(check=True)

    def test_locally_replaced_hardlink_blocks_update_and_uninstall(self):
        self.install(mode="hardlink")
        replacement = self.target / "replacement.md"
        replacement.write_text("Local replacement\n")
        replacement.replace(self.target / "references/flow.md")
        for options in ({"mode": "hardlink"}, {"check": True}, {"uninstall": True}):
            with self.subTest(options=options), self.assertRaises(installer.InstallError):
                self.install(**options)
        self.assertEqual((self.source / "references/flow.md").read_text(), "Initial flow\n")
        self.assertEqual((self.target / "references/flow.md").read_text(), "Local replacement\n")

    def test_hardlink_failure_cleans_stage_and_keeps_existing_install(self):
        self.install(mode="hardlink")
        (self.source / "references/new.md").write_text("New resource\n")
        old_inode = (self.target / "SKILL.md").stat().st_ino
        with patch.object(installer.os, "link", side_effect=OSError("Cross-device link")):
            with self.assertRaisesRegex(installer.InstallError, "Cannot create project hard link"):
                self.install(mode="hardlink")
        self.assertEqual((self.target / "SKILL.md").stat().st_ino, old_inode)
        self.assertFalse((self.target / "references/new.md").exists())
        self.assertFalse(list(self.target.parent.glob(".panwatch-skill-*")))

    def test_mode_switch_requires_uninstall_and_preserves_canonical_source(self):
        self.install()
        with self.assertRaisesRegex(installer.InstallError, "Uninstall the owned link"):
            self.install(mode="hardlink")
        self.assertTrue(self.target.is_symlink())
        self.install(uninstall=True)
        self.install(mode="hardlink")
        self.assertFalse(self.target.is_symlink())
        self.assertTrue((self.target / "SKILL.md").samefile(self.source / "SKILL.md"))

    def test_hardlink_dry_run_writes_nothing(self):
        self.install(mode="hardlink", dry_run=True)
        self.assertFalse((self.project / ".claude").exists())
        self.assertEqual((self.source / "SKILL.md").stat().st_nlink, 1)

    def test_source_symlinks_cannot_copy_external_content(self):
        private = Path(self.temp.name) / "secret"
        private.write_text("private")
        (self.source / "references/secret").symlink_to(private)
        for mode in ("copy", "hardlink"):
            with self.subTest(mode=mode), self.assertRaises(installer.InstallError):
                self.install(mode=mode)
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
