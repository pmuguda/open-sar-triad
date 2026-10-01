#!/usr/bin/env python3
"""
Checks that the published site actually serves the catalog that is committed.

A green deploy means the artifact uploaded, not that anyone can read it. If
Pages succeeds and the CDN keeps serving yesterday's files, the repository and
the site disagree and nothing in the pipeline notices: the ingest reports
healthy because the commit landed, and the deploy reports healthy because it
finished. That gap is this script.

It fetches the published ``api/v1/stats.json`` and compares its scene count
against ``data/scenes.geojson`` in the working tree, then writes a report in the
shape ``report_health.py`` consumes, so a disagreement opens an issue.

Propagation is not instant, so a mismatch is retried before it is believed.
Reporting a stale site that was merely slow would be worse than not checking:
an alert that cries wolf gets muted, and then the real one is missed too.

Usage:
    python3 scripts/verify_deploy.py --url https://example.com/open-sar-triad
    python3 scripts/verify_deploy.py --url ... --json /tmp/deploy.json
    python3 scripts/verify_deploy.py --url ... --attempts 1 --wait 0
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CATALOG = ROOT / "data" / "scenes.geojson"

#: Pages plus a CDN in front of it can take a couple of minutes to turn over.
DEFAULT_ATTEMPTS = 6
DEFAULT_WAIT = 30


def local_total(path: Path) -> int:
    """Scene count from the committed catalog, counted without parsing 28 MB."""
    return path.read_bytes().count(b'"provider":"')


def fetch_stats(base: str, cache_bust: str, timeout: int = 30) -> dict:
    url = f"{base.rstrip('/')}/api/v1/stats.json?v={cache_bust}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "open-sar-triad-deploy-check",
        # Ask every hop for the current object rather than whatever it holds.
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def check(base: str, expected: int, attempts: int, wait: int,
          cache_bust: str, sleep=time.sleep) -> dict:
    """Poll until the site agrees, or until patience runs out.

    Returns a report dict. The last attempt's outcome is the one reported: a
    mismatch that resolves on attempt three is a slow deploy, not a broken one,
    and must not raise an alarm.
    """
    last_error = None
    published = None
    for i in range(1, attempts + 1):
        try:
            stats = fetch_stats(base, cache_bust)
            published = stats.get("total")
            if published == expected:
                return {
                    "ok": True, "errors": [], "warnings": [],
                    "subject": "Site deploy",
                    "published_total": published, "expected_total": expected,
                    "attempts_used": i, "url": base,
                }
            last_error = (f"the site serves {published:,} scenes but "
                          f"{expected:,} are committed"
                          if isinstance(published, int) else
                          f"the site's stats.json has no usable total: {stats.get('total')!r}")
        except (urllib.error.HTTPError, urllib.error.URLError,
                json.JSONDecodeError, OSError) as e:
            last_error = f"could not read the published stats.json: {e}"
        if i < attempts:
            sleep(wait)

    return {
        "ok": False,
        "errors": [f"{last_error}. Still wrong after {attempts} attempt(s) over "
                   f"~{attempts * wait}s, so this is not propagation delay."],
        "warnings": [],
        "subject": "Site deploy",
        "on_error_note": ("The deploy reported success, but the published site "
                          "and the repository disagree. Visitors are seeing "
                          "different data from what is committed."),
        "published_total": published, "expected_total": expected,
        "attempts_used": attempts, "url": base,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--url", required=True, help="published site base URL")
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT)
    ap.add_argument("--cache-bust", default="")
    args = ap.parse_args()

    if not args.catalog.exists():
        print(f"ERROR: {args.catalog} not found", file=sys.stderr)
        return 1

    expected = local_total(args.catalog)
    bust = args.cache_bust or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    report = check(args.url, expected, args.attempts, args.wait, bust)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))

    print(f"Site: {args.url}")
    print(f"  committed : {expected:,} scenes")
    print(f"  published : {report['published_total']!r}")
    print(f"  attempts  : {report['attempts_used']}")
    for e in report["errors"]:
        print(f"  ERROR:   {e}", file=sys.stderr)

    if report["errors"]:
        print("\nFAILED: the site does not match the repository.", file=sys.stderr)
        return 1
    print("\nPASSED: the site serves the committed catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
