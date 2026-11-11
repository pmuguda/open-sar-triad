"""
The suite must not be able to switch itself off.

Skipping is how coverage disappears without anyone deciding it should. Every
workflow test skipped on every CI run for weeks because PyYAML was not in
requirements-dev.txt, and nine terrain tests skipped because nothing fetched
their data; both runs were green. tests/conftest.py now fails any skip, in CI,
whose reason is not on a short allow-list. These run a throwaway suite through
the real conftest to prove the guard does what it says.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ALLOWED_SKIPS, skip_is_allowed

HERE = Path(__file__).parent

SUITE = {
    "test_runtime.py": '''
import pytest

def test_passes():
    assert True

def test_missing_dependency():
    pytest.skip("pandas is not installed")

def test_no_qgis():
    pytest.skip("could not import 'qgis.core': No module named 'qgis.core'")

def test_no_tiles():
    pytest.skip("elevation tiles not cached")
''',
    "test_whole_file.py": '''
import pytest
nope = pytest.importorskip("a_package_that_does_not_exist")

def test_never_collected():
    assert True
''',
}


def build(tmp_path, *names):
    shutil.copy(HERE / "conftest.py", tmp_path / "conftest.py")
    for name in names:
        (tmp_path / name).write_text(SUITE[name])
    return tmp_path


@pytest.fixture
def suite(tmp_path):
    """Four tests: one passes, one skips for a missing package, two skip for
    reasons on the allow-list."""
    return build(tmp_path, "test_runtime.py")


@pytest.fixture
def suite_with_a_dead_file(tmp_path):
    """The same, plus a file that skips itself at import."""
    return build(tmp_path, "test_runtime.py", "test_whole_file.py")


def run(suite, strict):
    env = {k: v for k, v in os.environ.items() if k != "OST_STRICT_SKIPS"}
    if strict:
        env["OST_STRICT_SKIPS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider",
         "-q", "-rA", "-c", os.devnull, "--rootdir", str(suite)],
        capture_output=True, text=True, env=env, cwd=suite)


def test_outside_ci_a_skip_is_just_a_skip(suite_with_a_dead_file):
    r = run(suite_with_a_dead_file, strict=False)
    assert r.returncode == 0, r.stdout
    assert "1 passed" in r.stdout and "4 skipped" in r.stdout


def test_in_ci_a_missing_dependency_fails_the_run(suite):
    r = run(suite, strict=True)
    assert r.returncode != 0, r.stdout
    assert "test_missing_dependency" in r.stdout
    assert "pandas is not installed" in r.stdout


def test_in_ci_the_expected_skips_still_skip(suite):
    r = run(suite, strict=True)
    assert "1 passed" in r.stdout and "1 failed" in r.stdout and "2 skipped" in r.stdout, r.stdout
    for name in ("test_no_qgis", "test_no_tiles"):
        failed = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR")) and name in l]
        assert not failed, failed


def test_in_ci_a_file_that_skips_itself_whole_fails_the_run(suite_with_a_dead_file):
    """Module-level importorskip never produces a test, so a per-test check
    cannot see it. This is the case that hid the workflow tests."""
    r = run(suite_with_a_dead_file, strict=True)
    assert r.returncode != 0, r.stdout
    assert "a_package_that_does_not_exist" in r.stdout
    lines = [l for l in r.stdout.splitlines() if "test_whole_file" in l and l.startswith(("FAILED", "ERROR"))]
    assert lines, r.stdout


def test_the_failure_says_what_to_do(suite):
    r = run(suite, strict=True)
    assert "requirements-dev.txt" in r.stdout and "ALLOWED_SKIPS" in r.stdout


def test_the_allow_list_is_short_and_specific():
    assert len(ALLOWED_SKIPS) <= 5, "every entry is a hole; keep the list short"
    assert skip_is_allowed("could not import 'qgis.core'")
    assert not skip_is_allowed("could not import 'yaml'")
    assert not skip_is_allowed("catalog not present")
