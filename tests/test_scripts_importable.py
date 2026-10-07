"""
Every script must be importable without the full pipeline installed.

scripts/fetch_catalog.py used to run `sys.exit(1)` at module scope when pyarrow
was missing. CI installs only the dev requirements, so the moment a test
imported that module for a helper, collection raised SystemExit — and pytest
turns a collection error into an INTERNALERROR that aborts the whole run. Two
pushes went red with every test "failing" when only one import was at fault.

A module that kills the interpreter on import cannot be tested, reused, or
introspected, so this checks the property directly rather than the one symptom.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPTS = sorted(p for p in (ROOT / "scripts").glob("*.py"))


def test_there_are_scripts_to_check():
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_importing_a_script_does_not_exit(script):
    """Imported in a subprocess with the heavy dependencies hidden, which is
    what the CI test job actually looks like."""
    code = (
        "import sys, importlib.util\n"
        # Stand in for a machine that has not installed requirements.txt.
        "blocked = {'pyarrow', 'pyarrow.parquet', 'shapely', 'shapely.wkb',\n"
        "           'shapely.geometry', 'shapely.strtree', 'matplotlib'}\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name in blocked else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError(name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{script}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print('imported')\n"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        f"{script.name} is not importable without its optional dependencies:\n"
        f"{r.stderr[-800:]}"
    )
    assert "imported" in r.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_script_only_runs_from_the_command_line(script):
    """Work belongs under `if __name__ == '__main__'`, not at module scope."""
    src = script.read_text()
    assert 'if __name__ == "__main__":' in src, script.name
