#!/usr/bin/env python3
"""
Packages the QGIS plugin.

Two jobs:

  sync   copy python/opensartriad/client.py into the plugin's vendor/ folder.
         QGIS plugins cannot pip-install, so the client ships inside the
         plugin, and the only sane way to keep two copies honest is to make
         one of them mechanically derived from the other. ``--check`` reports
         drift without writing, which is what the test suite runs.

  zip    build dist/open_sar_triad-<version>.zip, laid out the way QGIS's
         "Install from ZIP" expects (a single top-level folder named after the
         package). Version comes from metadata.txt so there is one place to
         bump it.

Usage:
    python3 scripts/build_qgis_plugin.py            # sync, then zip
    python3 scripts/build_qgis_plugin.py --check    # exit 1 if vendor/ is stale
"""

from __future__ import annotations

import argparse
import configparser
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "qgis" / "open_sar_triad"
SOURCE = ROOT / "python" / "opensartriad" / "client.py"
VENDORED = PLUGIN / "vendor" / "client.py"
DIST = ROOT / "dist"

#: Never shipped: caches, compiled files, editor leftovers.
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".orig", ".swp"}


def version() -> str:
    cfg = configparser.ConfigParser()
    cfg.read(PLUGIN / "metadata.txt", encoding="utf-8")
    return cfg["general"]["version"]


def vendor_is_current() -> bool:
    return VENDORED.exists() and VENDORED.read_bytes() == SOURCE.read_bytes()


def sync() -> None:
    VENDORED.write_bytes(SOURCE.read_bytes())


def plugin_files():
    for p in sorted(PLUGIN.rglob("*")):
        if not p.is_file():
            continue
        if EXCLUDE_DIRS & set(p.relative_to(PLUGIN).parts):
            continue
        if p.suffix in EXCLUDE_SUFFIXES:
            continue
        yield p


def build_zip(out_dir: Path = DIST) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"open_sar_triad-{version()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in plugin_files():
            zf.write(p, Path(PLUGIN.name) / p.relative_to(PLUGIN))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="store_true",
                    help="verify vendor/client.py matches the source; write nothing")
    ap.add_argument("--out", type=Path, default=DIST)
    args = ap.parse_args()

    if args.check:
        if vendor_is_current():
            print("vendor/client.py is current")
            return 0
        print("vendor/client.py is stale; run scripts/build_qgis_plugin.py", file=sys.stderr)
        return 1

    sync()
    out = build_zip(args.out)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, v{version()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
