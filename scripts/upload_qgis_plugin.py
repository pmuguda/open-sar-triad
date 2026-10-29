#!/usr/bin/env python3
"""
Uploads the plugin ZIP to the official QGIS plugin repository.

plugins.qgis.org accepts uploads over XML-RPC with an OSGeo account, which is
what the QGIS plugin builder's plugin_upload.py and qgis-plugin-ci both use.
This is the same call with nothing else around it, so a release of this
repository can publish the plugin without anyone opening a browser.

Credentials come from the environment only, never from arguments, so they do
not end up in a shell history or a workflow log:

    OSGEO_USERNAME   the OSGeo user id (https://id.osgeo.org)
    OSGEO_PASSWORD   its password

Usage:
    python3 scripts/upload_qgis_plugin.py dist/open_sar_triad-1.0.0.zip
    python3 scripts/upload_qgis_plugin.py dist/open_sar_triad-1.0.0.zip --dry-run

The first upload of a new plugin is reviewed by the repository's maintainers
before it appears in the QGIS plugin manager; later versions appear at once.
"""

from __future__ import annotations

import argparse
import os
import sys
import xmlrpc.client
import zipfile
from pathlib import Path
from urllib.parse import quote

RPC_URL = "https://{user}:{password}@plugins.qgis.org:443/plugins/RPC2/"
#: The repository refuses anything larger.
MAX_BYTES = 20 * 1024 * 1024


def check_zip(path: Path) -> str:
    """The package name, after the checks the repository itself applies."""
    if path.stat().st_size > MAX_BYTES:
        raise SystemExit(f"{path.name} is over the repository's 20 MB limit")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    roots = {n.split("/", 1)[0] for n in names}
    if len(roots) != 1:
        raise SystemExit(f"{path.name} must contain exactly one top-level folder, has {sorted(roots)}")
    root = roots.pop()
    for required in ("metadata.txt", "__init__.py", "LICENSE"):
        if f"{root}/{required}" not in names:
            raise SystemExit(f"{path.name} is missing {root}/{required}, which the repository requires")
    return root


def upload(path: Path, user: str, password: str) -> tuple[int, int]:
    """Returns (plugin_id, version_id) as the repository reports them."""
    url = RPC_URL.format(user=quote(user, safe=""), password=quote(password, safe=""))
    server = xmlrpc.client.ServerProxy(url, verbose=False)
    with open(path, "rb") as fh:
        plugin_id, version_id = server.plugin.upload(xmlrpc.client.Binary(fh.read()))
    return plugin_id, version_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("zip", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="check the package; upload nothing")
    args = ap.parse_args()

    if not args.zip.exists():
        print(f"ERROR: {args.zip} not found", file=sys.stderr)
        return 1
    root = check_zip(args.zip)
    print(f"{args.zip.name}: package '{root}', {args.zip.stat().st_size // 1024} KB, required files present")
    if args.dry_run:
        return 0

    user, password = os.environ.get("OSGEO_USERNAME", ""), os.environ.get("OSGEO_PASSWORD", "")
    if not user or not password:
        print("ERROR: set OSGEO_USERNAME and OSGEO_PASSWORD", file=sys.stderr)
        return 1
    try:
        plugin_id, version_id = upload(args.zip, user, password)
    except xmlrpc.client.ProtocolError as e:
        print(f"ERROR: the repository refused the upload: HTTP {e.errcode} {e.errmsg}", file=sys.stderr)
        return 1
    except xmlrpc.client.Fault as e:
        print(f"ERROR: the repository rejected the package: {e.faultString}", file=sys.stderr)
        return 1
    print(f"uploaded: plugin {plugin_id}, version {version_id}")
    print("https://plugins.qgis.org/plugins/open_sar_triad/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
