"""The generated .env is what a new machine runs on, so its safety properties are worth a test."""
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup.py"
SETUP_CMD = REPO_ROOT / "scripts" / "setup.cmd"


def load_setup(backend: Path):
    spec = importlib.util.spec_from_file_location("signalgate_setup", SETUP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BACKEND = backend
    return module


class SetupScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copy(REPO_ROOT / "backend" / ".env.example", self.tmp / ".env.example")
        self.setup = load_setup(self.tmp)

    def generate(self, profile="laptop", overwrite=False):
        self.setup.step_env(profile, overwrite)
        return (self.tmp / ".env").read_text(encoding="utf-8")

    def settings_lines(self, text):
        return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    def test_no_credit_can_be_spent_before_the_operator_opts_in(self):
        lines = self.settings_lines(self.generate())
        self.assertIn("SECTORS_API_ENABLED=false", lines)
        self.assertIn("SECTORS_API_KEY=", lines)
        self.assertFalse([line for line in lines if line.startswith("SECTORS_API_KEY=") and len(line) > len("SECTORS_API_KEY=")])

    def test_the_chosen_profile_lands_in_the_file(self):
        self.assertIn("SIGNALGATE_PROFILE=workstation", self.settings_lines(self.generate("workstation")))

    def test_model_lines_are_left_commented_so_the_profile_decides(self):
        """An explicit OLLAMA_MODEL beats the profile, which would silently undo the profile's choice."""
        lines = self.settings_lines(self.generate("workstation"))
        self.assertFalse([line for line in lines if line.startswith(("OLLAMA_MODEL=", "OLLAMA_REVIEWER_MODELS="))])

    def test_an_existing_env_is_never_clobbered_by_default(self):
        (self.tmp / ".env").write_text("SECTORS_API_KEY=kunci-asli-yang-berharga\n", encoding="utf-8")
        self.setup.step_env("workstation", overwrite=False)
        self.assertIn("kunci-asli-yang-berharga", (self.tmp / ".env").read_text(encoding="utf-8"))

    def test_overwrite_is_possible_when_asked_for_explicitly(self):
        (self.tmp / ".env").write_text("SECTORS_API_KEY=kunci-lama\n", encoding="utf-8")
        lines = self.settings_lines(self.generate("laptop", overwrite=True))
        self.assertIn("SECTORS_API_KEY=", lines)
        self.assertNotIn("SECTORS_API_KEY=kunci-lama", lines)

    def test_every_key_from_the_example_survives(self):
        """A key added to .env.example must not be dropped on its way into the generated file."""
        def keys(text):
            return {line.split("=", 1)[0].strip() for line in text.splitlines()
                    if "=" in line and not line.lstrip().startswith("#")}
        example = (self.tmp / ".env.example").read_text(encoding="utf-8")
        missing = keys(example) - keys(self.generate()) - {"OLLAMA_MODEL", "OLLAMA_REVIEWER_MODELS"}
        self.assertEqual(missing, set())

    def test_the_example_still_carries_the_keys_the_script_rewrites(self):
        """If .env.example drops one of these, the script would silently stop enforcing it."""
        example = (self.tmp / ".env.example").read_text(encoding="utf-8")
        for key in ("SECTORS_API_KEY", "SECTORS_API_ENABLED", "SIGNALGATE_PROFILE"):
            self.assertIn(f"{key}=", example)


class WindowsCompatibilityTests(unittest.TestCase):
    """Nobody on this project develops on Windows, so the Windows paths need a test, not a hope."""

    def setUp(self):
        self.setup = load_setup(REPO_ROOT / "backend")

    def test_the_script_is_pure_ascii(self):
        """A legacy Windows console is cp1252: one stray em dash aborts the run with UnicodeEncodeError."""
        offenders = [(no, line.strip()) for no, line in
                     enumerate(SETUP_SCRIPT.read_text(encoding="utf-8").splitlines(), 1)
                     if any(ord(char) > 127 for char in line)]
        self.assertEqual(offenders, [])

    def test_the_venv_interpreter_lives_under_scripts_on_windows(self):
        with patch.object(os, "name", "nt"):
            self.assertEqual(self.setup.venv_python().parts[-2:], ("Scripts", "python.exe"))
        with patch.object(os, "name", "posix"):
            self.assertEqual(self.setup.venv_python().parts[-2:], ("bin", "python"))

    def rendered(self, name: str) -> str:
        buffer = io.StringIO()
        with patch.object(os, "name", name), redirect_stdout(buffer):
            self.setup.report("workstation", ["qwen2.5:14b"])
        return buffer.getvalue()

    def test_closing_instructions_use_windows_commands_on_windows(self):
        printed = self.rendered("nt")
        self.assertIn(".venv\\Scripts\\Activate.ps1", printed)
        self.assertIn("copy .env.example .env", printed)
        self.assertNotIn("source .venv", printed)
        self.assertNotIn("cp .env.example", printed)

    def test_windows_instructions_never_chain_with_double_ampersand(self):
        """Windows PowerShell 5.1: "The token '&&' is not a valid statement separator"."""
        self.assertNotIn("&&", self.rendered("nt"))

    def test_posix_instructions_still_chain(self):
        printed = self.rendered("posix")
        self.assertIn("source .venv/bin/activate", printed)
        self.assertIn("&&", printed)

    def test_the_windows_wrapper_never_invokes_python3(self):
        """`python3` does not exist on Windows; it only triggers the Microsoft Store alias stub."""
        body = SETUP_CMD.read_text(encoding="utf-8")
        invocations = [line.strip() for line in body.splitlines()
                       if line.strip().startswith(("py ", "python ", "python3 "))]
        self.assertTrue(invocations)
        self.assertFalse([line for line in invocations if line.startswith("python3 ")], invocations)

    def test_the_windows_wrapper_tries_both_launchers_before_giving_up(self):
        body = SETUP_CMD.read_text(encoding="utf-8")
        self.assertIn("py -3", body)
        self.assertIn("python --version", body)
        self.assertIn("python.org/downloads", body)


class DistributionTests(unittest.TestCase):
    """A setup script that never reaches the other machine is the same as no setup script."""

    def ignored(self, path: Path) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT, capture_output=True)
        return result.returncode == 0

    def test_the_setup_entrypoints_are_not_gitignored(self):
        """scripts/ is ignored wholesale; these two have to be excepted or a clone cannot run setup."""
        for path in (SETUP_SCRIPT, SETUP_CMD):
            self.assertTrue(path.exists(), path)
            self.assertFalse(self.ignored(path), f"{path.name} tidak akan ikut ter-commit")

    def test_the_rest_of_scripts_stays_local(self):
        other = [path for path in (REPO_ROOT / "scripts").iterdir()
                 if path.is_file() and path not in {SETUP_SCRIPT, SETUP_CMD}]
        for path in other:
            self.assertTrue(self.ignored(path), f"{path.name} tak sengaja ikut terbuka")

    def test_the_batch_wrapper_keeps_crlf_line_endings(self):
        """cmd.exe can mis-parse a batch file saved with bare LF."""
        data = SETUP_CMD.read_bytes()
        self.assertEqual(data.count(b"\n") - data.count(b"\r\n"), 0)

    def test_gitattributes_pins_batch_line_endings(self):
        """Without this, git checkout on another machine can normalise the CRLF straight back to LF."""
        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.cmd text eol=crlf", attributes)


if __name__ == "__main__":
    unittest.main()
