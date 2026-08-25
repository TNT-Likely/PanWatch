import os
from pathlib import Path
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def dry_run(*targets: str, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["make", "--dry-run", *targets],
        cwd=PROJECT_ROOT,
        env={**os.environ, "OS": "Windows_NT", **(environment or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class WindowsMakefileTests(unittest.TestCase):
    def test_dev_api_uses_windows_virtual_environment_and_command_shell(self):
        output = dry_run("dev-api")

        self.assertIn(r".venv\Scripts\python.exe", output)
        self.assertNotIn(".venv/bin/activate", output)
        self.assertNotIn("python3 -m venv", output)

    def test_build_delegates_to_windows_build_script(self):
        output = dry_run("build", "VERSION=0.10.3")

        self.assertIn(r"scripts\build.ps1", output)

    def test_comspec_selects_windows_commands_when_os_is_not_set(self):
        output = dry_run(
            "dev-api",
            environment={
                "OS": "",
                "ComSpec": r"C:\Windows\System32\cmd.exe",
            },
        )

        self.assertIn(r".venv\Scripts\python.exe", output)

    def test_windows_build_script_parses_in_powershell(self):
        build_script = PROJECT_ROOT / "scripts" / "build.ps1"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "$ErrorActionPreference = 'Stop'; "
                "[void][ScriptBlock]::Create("
                f"(Get-Content -Raw -LiteralPath '{build_script}'))",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
