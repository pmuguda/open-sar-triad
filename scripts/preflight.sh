#!/usr/bin/env bash
#
# Run what CI runs, the way CI runs it, before pushing.
#
# Half of this repository's red runs were the test workflow failing minutes
# after a push, every time for the same reason: the change had been tested on
# a machine with more installed than CI has. pyarrow present locally and
# absent in CI; PyYAML likewise; a newer pip. The tests passed where they were
# run and failed where it counted.
#
# This removes the difference. It builds a virtualenv from exactly what the
# test workflow installs (requirements-dev.txt and the client, nothing else),
# and runs the same commands with the same flags. tests/test_preflight.py
# fails if this file and .github/workflows/tests.yml drift apart.
#
# Usage:
#   scripts/preflight.sh              everything CI's test and build-api jobs do
#   scripts/preflight.sh -k landuse   extra arguments go to pytest
#
# To run it automatically before every push:
#   git config core.hooksPath .githooks
#
# The plugin's tests that need QGIS are skipped here, as they are in CI's plain
# test job; CI runs them in QGIS containers. Everything else that skips fails.

set -euo pipefail
cd "$(dirname "$0")/.."

venv="${OST_PREFLIGHT_VENV:-.venv-ci}"
python="${OST_PREFLIGHT_PYTHON:-python3}"

if [ ! -x "$venv/bin/python" ]; then
  echo "preflight: creating $venv"
  "$python" -m venv "$venv"
fi

# Reinstall only when what CI installs has changed.
want="$(cat requirements-dev.txt python/pyproject.toml | sha256sum | cut -d' ' -f1)"
if [ "$(cat "$venv/.installed" 2>/dev/null || true)" != "$want" ]; then
  echo "preflight: installing what the test workflow installs"
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/pip" install --quiet -r requirements-dev.txt
  "$venv/bin/pip" install --quiet -e python
  echo "$want" > "$venv/.installed"
fi

echo "preflight: tests"
OST_STRICT_SKIPS=1 "$venv/bin/python" -m pytest tests/ -q -rs -p no:cacheprovider "$@"

echo "preflight: the committed catalog validates"
"$venv/bin/python" scripts/validate_catalog.py > /dev/null

echo "preflight: the API builds from it"
"$venv/bin/python" scripts/build_api.py > /dev/null

echo "preflight: the plugin's vendored files are current"
"$venv/bin/python" scripts/build_qgis_plugin.py --check

echo "preflight: ok"
