#!/usr/bin/env python3
"""
Turns a validation report into a GitHub issue, so the pipeline reaches you
instead of waiting to be checked.

Why this exists
---------------
Every detector added before this one wrote its finding into a place nobody
reads. fetch_catalog.py recorded ``new_this_run: 0`` in the catalog header for
nine consecutive weeks of a frozen catalog. validate_catalog.py wrote warnings
to stdout and to a JSON artifact. The workflow summary needs the Actions page
opened. Meanwhile all 19 runs of the weekly job reported success, because the
script exiting 0 is not the same thing as data arriving.

So the only reliable detector of a broken ingest was a human noticing the scene
count had not moved. This script inverts that: when a run is unhealthy it opens
an issue, which GitHub notifies on, and when the run is healthy again it closes
it. Silence then means working, which is the property the green tick never had.

Behaviour
---------
Unhealthy (the report carries any error or warning):
  * no open issue  -> create one
  * issue open     -> refresh its title and body in place; add a comment only
                      when the set of problems has changed, so a provider that
                      stays quiet for a year does not generate a year of
                      comments on the same thread
Healthy:
  * issue open     -> comment with the recovery and close it
  * no issue       -> do nothing at all

Usage:
    python3 scripts/report_health.py --report validation.json
    python3 scripts/report_health.py --report validation.json --dry-run

Needs GITHUB_TOKEN with issues:write, and GITHUB_REPOSITORY. Outside Actions it
does nothing unless both are supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
LABEL = "catalog-health"
LABEL_COLOR = "d93f0b"
LABEL_DESC = "Automated: the weekly catalog ingest needs attention"

#: Embedded in the issue body so a later run can tell whether the problems are
#: the same ones, without re-parsing prose.
FINGERPRINT = "<!-- catalog-health-fingerprint: {} -->"


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo, self.token = repo, token

    def _call(self, method: str, path: str, payload: dict | None = None):
        req = urllib.request.Request(
            f"{API}{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "open-sar-triad-health",
            })
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else None

    def ensure_label(self) -> None:
        try:
            self._call("POST", f"/repos/{self.repo}/labels",
                       {"name": LABEL, "color": LABEL_COLOR, "description": LABEL_DESC})
        except urllib.error.HTTPError as e:
            if e.code != 422:        # 422 is "already exists", which is the goal
                raise

    def open_issue(self) -> dict | None:
        found = self._call(
            "GET", f"/repos/{self.repo}/issues?state=open&labels={LABEL}&per_page=1")
        return found[0] if found else None

    def create(self, title: str, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repo}/issues",
                          {"title": title, "body": body, "labels": [LABEL]})

    def update(self, number: int, **fields) -> dict:
        return self._call("PATCH", f"/repos/{self.repo}/issues/{number}", fields)

    def comment(self, number: int, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/comments",
                          {"body": body})


def problems(report: dict) -> list[str]:
    """Everything a human should look at, errors first."""
    return list(report.get("errors") or []) + list(report.get("warnings") or [])


def fingerprint(items: list[str]) -> str:
    return hashlib.sha256("\u0000".join(sorted(items)).encode()).hexdigest()[:16]


def _run_link() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run}" if repo and run else ""


def build_title(report: dict, items: list[str]) -> str:
    n_err = len(report.get("errors") or [])
    if n_err:
        return f"Catalog ingest failing: {n_err} error(s)"
    stale = [p for p, fr in (report.get("provider_freshness") or {}).items()
             if fr.get("age_days", 0) > 21]
    if stale:
        return f"Catalog ingest: {', '.join(sorted(stale))} has stopped arriving"
    return f"Catalog ingest needs attention: {len(items)} warning(s)"


def build_body(report: dict, items: list[str]) -> str:
    lines = []
    errs = report.get("errors") or []
    warns = report.get("warnings") or []

    if errs:
        lines.append("The catalog **was not committed**. The site is still "
                     "serving the last good data.")
        lines.append("")
        lines.append("### Errors")
        lines += [f"- {e}" for e in errs]
        lines.append("")
    if warns:
        lines.append("### Warnings")
        lines += [f"- {w}" for w in warns]
        lines.append("")

    # A run that died before producing a catalog has nothing to say here, and an
    # empty "Catalog" heading reads as missing information rather than none.
    facts = []
    total = report.get("total")
    by_p = report.get("by_provider") or {}
    if total is not None:
        facts.append(f"- **{total:,} scenes** "
                     f"({', '.join(f'{k} {v:,}' for k, v in sorted(by_p.items()))})")
    if "delta_total" in report:
        facts.append(f"- change since the previous catalog: "
                     f"{report['delta_total']:+,} "
                     f"(+{report.get('added', 0):,} new, "
                     f"-{report.get('removed', 0):,} gone)")
    if "new_this_run" in report:
        facts.append(f"- the fetch reported `new_this_run: {report['new_this_run']}`")
    if facts:
        lines += ["### Catalog", *facts]

    freshness = report.get("provider_freshness") or {}
    if freshness:
        lines += ["", "| provider | last ingest | age |", "|---|---|---|"]
        for prov, fr in sorted(freshness.items()):
            stale = fr.get("age_days", 0) > 21
            lines.append(f"| {prov} | {fr.get('newest_first_seen', '?')} "
                         f"| {fr.get('age_days', '?')}d{' **stale**' if stale else ''} |")

    link = _run_link()
    if link:
        lines += ["", f"[Workflow run]({link})"]
    lines += ["",
              "_This issue is opened and closed automatically by "
              "`scripts/report_health.py`. It will close itself on the next "
              "healthy ingest._",
              "", FINGERPRINT.format(fingerprint(items))]
    return "\n".join(lines)


def run(report: dict, gh: GitHub, dry_run: bool = False) -> int:
    items = problems(report)
    existing = gh.open_issue() if not dry_run else None

    if not items:
        if existing:
            if not dry_run:
                gh.comment(existing["number"],
                           "Ingest is healthy again"
                           + (f" ([run]({_run_link()}))" if _run_link() else "")
                           + ". Closing.")
                gh.update(existing["number"], state="closed",
                          state_reason="completed")
            print(f"healthy: closed issue #{existing['number']}")
        else:
            print("healthy: nothing to report")
        return 0

    title, body = build_title(report, items), build_body(report, items)
    if dry_run:
        print(f"--- would post ---\n# {title}\n\n{body}")
        return 0

    gh.ensure_label()
    if existing is None:
        issue = gh.create(title, body)
        print(f"unhealthy: opened issue #{issue['number']}: {title}")
        return 0

    changed = FINGERPRINT.format(fingerprint(items)) not in (existing.get("body") or "")
    gh.update(existing["number"], title=title, body=body)
    if changed:
        gh.comment(existing["number"],
                   "The problems have changed since the last run."
                   + (f" ([run]({_run_link()}))" if _run_link() else ""))
        print(f"unhealthy: updated issue #{existing['number']} and commented")
    else:
        # Same problems as last time. Refresh the body so the counts stay
        # current, but do not add another comment to the thread.
        print(f"unhealthy: refreshed issue #{existing['number']} (unchanged)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be posted; touches nothing")
    args = ap.parse_args()

    if not args.report.exists():
        print(f"ERROR: {args.report} not found", file=sys.stderr)
        return 1
    report = json.loads(args.report.read_text())

    token = os.environ.get("GITHUB_TOKEN", "")
    if args.dry_run:
        return run(report, GitHub(args.repo, token), dry_run=True)
    if not token or not args.repo:
        # Local runs and forks without a token: say so and succeed. Reporting
        # health must never be the thing that fails the pipeline.
        print("no GITHUB_TOKEN/GITHUB_REPOSITORY; skipping health reporting")
        return 0

    try:
        return run(report, GitHub(args.repo, token))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"WARNING: could not report health: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
