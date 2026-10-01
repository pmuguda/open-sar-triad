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
import os
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


def compare(stats: dict, expected: int, commit: str) -> tuple[bool, str | None]:
    """Does this published stats.json match what we just deployed?

    Prefers the build commit, which changes on every deploy. The scene count
    only moves on ingest weeks, so on a code-only deploy it is identical before
    and after and cannot tell a live build from a stale one still being served.

    Falls back to the count when no commit is available on either side: a build
    published before commit stamping existed, or a local run outside Actions.
    That is weaker, not wrong, and is better than refusing to check at all.
    """
    published_commit = stats.get("commit")
    if commit and published_commit:
        if published_commit == commit:
            return True, None
        return False, (f"the site was built from commit {published_commit[:7]} "
                       f"but {commit[:7]} was just deployed")

    published = stats.get("total")
    if not isinstance(published, int):
        return False, (f"the site's stats.json has no usable total: "
                       f"{stats.get('total')!r}")
    if published == expected:
        return True, None
    return False, (f"the site serves {published:,} scenes but "
                   f"{expected:,} are committed")


def check(base: str, expected: int, attempts: int, wait: int, cache_bust: str,
          commit: str = "", sleep=time.sleep) -> dict:
    """Poll until the site agrees, or until patience runs out.

    Returns a report dict. The last attempt's outcome is the one reported: a
    mismatch that resolves on attempt three is a slow deploy, not a broken one,
    and must not raise an alarm.
    """
    last_error = None
    stats: dict = {}
    for i in range(1, attempts + 1):
        try:
            stats = fetch_stats(base, cache_bust)
            ok, last_error = compare(stats, expected, commit)
            if ok:
                return {
                    "ok": True, "errors": [], "warnings": [],
                    "subject": "Site deploy",
                    "published_total": stats.get("total"),
                    "published_commit": stats.get("commit"),
                    "expected_total": expected, "expected_commit": commit,
                    "attempts_used": i, "url": base,
                }
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
                          "something other than what is committed."),
        "published_total": stats.get("total"),
        "published_commit": stats.get("commit"),
        "expected_total": expected, "expected_commit": commit,
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
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""),
                    help="the commit just deployed; preferred over the scene "
                         "count because it changes on every deploy")
    args = ap.parse_args()

    if not args.catalog.exists():
        print(f"ERROR: {args.catalog} not found", file=sys.stderr)
        return 1

    expected = local_total(args.catalog)
    bust = args.cache_bust or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    report = check(args.url, expected, args.attempts, args.wait, bust,
                   commit=args.commit)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))

    print(f"Site: {args.url}")
    print(f"  committed : {expected:,} scenes")
    print(f"  published : {report['published_total']!r}")
    if args.commit:
        print(f"  deployed  : {args.commit[:7]} | published build: "
              f"{(report['published_commit'] or '(none)')[:7]}")
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
