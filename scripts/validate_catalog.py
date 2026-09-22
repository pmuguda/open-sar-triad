#!/usr/bin/env python3
"""
Validates data/scenes.geojson and gates the weekly commit.

Runs between fetch_catalog.py and the commit step, so a bad ingestion fails the
workflow instead of being committed and deployed. Exits non-zero on any error.

Two kinds of check:

  invariants  -- things that must be true of any healthy catalog (ids unique,
                 coordinates on Earth, dates not in the future, URLs on the
                 providers' own hosts, Capella variants actually collapsed).

  regression  -- comparisons against the previously committed catalog. These
                 catch the failure mode that actually bit us: a run that
                 "succeeds" while quietly serving stale or shrunken data.

Usage:
    python3 scripts/validate_catalog.py
    python3 scripts/validate_catalog.py --baseline previous.geojson
    python3 scripts/validate_catalog.py --json report.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent.parent
CATALOG = ROOT / "data" / "scenes.geojson"

PROVIDERS = {"iceye", "umbra", "capella"}

#: Assets must live on the providers' own storage. Anything else means the
#: pipeline picked up a URL from somewhere unexpected.
ALLOWED_ASSET_HOSTS = {
    "iceye-open-data-catalog.s3.amazonaws.com",
    "umbra-open-data-catalog.s3.amazonaws.com",
    "umbra-open-data-catalog.s3.us-west-2.amazonaws.com",
    "capella-open-data.s3.amazonaws.com",
    "capella-open-data.s3.us-west-2.amazonaws.com",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: A collapsed Capella id must not still carry a per-format token.
CAPELLA_FORMAT_TOKEN = re.compile(r"_(GEC|GEO|SLC|SICD|SIDD|CPHD|CSI)_")

# Thresholds. Deliberately loose: they exist to catch collapse, not churn.
MAX_TOTAL_DROP_PCT = 5.0
MAX_PROVIDER_DROP_PCT = 15.0
FUTURE_TOLERANCE_DAYS = 3
EARLIEST_PLAUSIBLE = "2010-01-01"
STALE_INGEST_WARN_DAYS = 35


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: dict = {}

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _git_baseline(path: Path) -> dict | None:
    """The previously committed catalog, for regression comparison.

    Returns None rather than raising whenever a baseline cannot be obtained:
    the catalog may live outside the repo (tests, ad-hoc runs), git may be
    absent, or the file may be newly added with no committed version yet.
    """
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None  # not inside the repo, so there is nothing to compare against
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{rel.as_posix()}"],
            capture_output=True, check=True, text=False,
        )
        return json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError,
            FileNotFoundError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Invariants
# --------------------------------------------------------------------------- #
def check_structure(doc: dict, rep: Report) -> list[dict]:
    if doc.get("type") != "FeatureCollection":
        rep.error(f"top-level type is {doc.get('type')!r}, expected 'FeatureCollection'")
    feats = doc.get("features")
    if not isinstance(feats, list) or not feats:
        rep.error("features is missing, not a list, or empty")
        return []
    if not doc.get("generated_at"):
        rep.warn("generated_at is missing")
    return feats


def _coords(geom: dict):
    t = geom.get("type")
    if t == "Polygon":
        return [geom.get("coordinates") or []]
    if t == "MultiPolygon":
        return geom.get("coordinates") or []
    return None


def check_features(feats: list[dict], rep: Report) -> None:
    today = date.today()
    future_limit = (today + timedelta(days=FUTURE_TOLERANCE_DAYS)).isoformat()

    ids: list[str] = []
    bad_geom = bad_date = bad_url = no_assets = 0
    off_host: Counter = Counter()

    for f in feats:
        p = f.get("properties") or {}
        fid = p.get("id")
        if not isinstance(fid, str) or not fid.strip():
            rep.error("a feature has a missing or empty id")
            continue
        ids.append(fid)

        provider = p.get("provider")
        if provider not in PROVIDERS:
            rep.error(f"{fid}: provider {provider!r} not one of {sorted(PROVIDERS)}")

        # -- date
        d = p.get("date")
        if not isinstance(d, str) or not DATE_RE.match(d):
            bad_date += 1
        else:
            if d > future_limit:
                rep.error(f"{fid}: acquisition date {d} is in the future")
            if d < EARLIEST_PLAUSIBLE:
                rep.error(f"{fid}: acquisition date {d} predates {EARLIEST_PLAUSIBLE}")
            if p.get("year") not in (None, int(d[:4])):
                rep.error(f"{fid}: year {p.get('year')} disagrees with date {d}")

        # -- geometry
        geom = f.get("geometry") or {}
        rings = _coords(geom)
        if rings is None:
            bad_geom += 1
        else:
            for poly in rings:
                for ring in poly:
                    if not isinstance(ring, list) or len(ring) < 4:
                        bad_geom += 1
                        break
                    for pt in ring:
                        if (not isinstance(pt, (list, tuple)) or len(pt) < 2
                                or not all(isinstance(v, (int, float)) for v in pt[:2])
                                or not (-180.0 <= pt[0] <= 180.0)
                                or not (-90.0 <= pt[1] <= 90.0)):
                            bad_geom += 1
                            break
                    else:
                        continue
                    break

        # -- assets
        urls = list((p.get("products") or {}).values())
        if p.get("download"):
            urls.append(p["download"])
        if not urls:
            no_assets += 1
        for u in urls:
            if not isinstance(u, str) or not u.startswith(("http://", "https://")):
                bad_url += 1
                continue
            host = urlparse(u).netloc
            if host not in ALLOWED_ASSET_HOSTS:
                off_host[host] += 1

        # -- first_seen
        fs = p.get("first_seen")
        if fs is not None and (not isinstance(fs, str) or not DATE_RE.match(fs)):
            rep.error(f"{fid}: first_seen {fs!r} is not YYYY-MM-DD")

        # -- Capella collapse actually happened
        if provider == "capella" and CAPELLA_FORMAT_TOKEN.search(fid):
            rep.error(f"{fid}: Capella id still carries a per-format token; "
                      "the collapse step did not run")

    if bad_date:
        rep.error(f"{bad_date} feature(s) have a missing or malformed date")
    if bad_geom:
        rep.error(f"{bad_geom} feature(s) have invalid geometry "
                  "(wrong type, short ring, or coordinates off the Earth)")
    if bad_url:
        rep.error(f"{bad_url} asset URL(s) are not http(s)")
    if no_assets:
        rep.warn(f"{no_assets} feature(s) have no downloadable asset")
    for host, n in off_host.most_common():
        rep.error(f"{n} asset URL(s) point at unexpected host {host!r}")

    dupes = [i for i, c in Counter(ids).items() if c > 1]
    if dupes:
        rep.error(f"{len(dupes)} duplicate scene id(s), e.g. {dupes[:3]}")

    counts = Counter((f.get("properties") or {}).get("provider") for f in feats)
    for prov in sorted(PROVIDERS):
        if counts.get(prov, 0) == 0:
            rep.error(f"provider {prov!r} has zero scenes")
    rep.info["by_provider"] = {k: v for k, v in counts.items() if k}
    rep.info["total"] = len(feats)


def check_ingestion_freshness(feats: list[dict], rep: Report) -> None:
    """first_seen should be stamped, and should have moved reasonably recently."""
    seen = [(f.get("properties") or {}).get("first_seen") for f in feats]
    stamped = [s for s in seen if isinstance(s, str) and DATE_RE.match(s)]
    if not stamped:
        rep.warn("no scene carries first_seen; ingestion tracking is not running")
        return
    newest = max(stamped)
    rep.info["newest_first_seen"] = newest
    rep.info["ingested_by_date"] = dict(sorted(Counter(stamped).items()))
    try:
        age = (date.today() - datetime.strptime(newest, "%Y-%m-%d").date()).days
    except ValueError:
        rep.error(f"newest first_seen {newest!r} is not a valid date")
        return
    if age > STALE_INGEST_WARN_DAYS:
        rep.warn(f"no new scenes ingested for {age} days (newest first_seen {newest}). "
                 "Upstream may be quiet, or the pipeline may be stuck.")


# --------------------------------------------------------------------------- #
# Regression against the previous catalog
# --------------------------------------------------------------------------- #
def check_regression(feats: list[dict], baseline: dict | None, rep: Report) -> None:
    if baseline is None:
        rep.warn("no baseline catalog available; regression checks skipped")
        return
    old = baseline.get("features") or []
    if not old:
        rep.warn("baseline catalog has no features; regression checks skipped")
        return

    new_n, old_n = len(feats), len(old)
    rep.info["baseline_total"] = old_n
    rep.info["delta_total"] = new_n - old_n

    if old_n:
        drop = (old_n - new_n) / old_n * 100
        if drop > MAX_TOTAL_DROP_PCT:
            rep.error(f"scene count fell {drop:.1f}% ({old_n} -> {new_n}), "
                      f"more than the {MAX_TOTAL_DROP_PCT}% allowed")

    new_by = Counter((f.get("properties") or {}).get("provider") for f in feats)
    old_by = Counter((f.get("properties") or {}).get("provider") for f in old)
    for prov in sorted(PROVIDERS):
        o, n = old_by.get(prov, 0), new_by.get(prov, 0)
        if o and n == 0:
            rep.error(f"provider {prov!r} went from {o} scenes to zero")
        elif o:
            d = (o - n) / o * 100
            if d > MAX_PROVIDER_DROP_PCT:
                rep.error(f"provider {prov!r} fell {d:.1f}% ({o} -> {n}), "
                          f"more than the {MAX_PROVIDER_DROP_PCT}% allowed")

    old_ids = {(f.get("properties") or {}).get("id") for f in old}
    new_ids = {(f.get("properties") or {}).get("id") for f in feats}
    rep.info["added"] = len(new_ids - old_ids)
    rep.info["removed"] = len(old_ids - new_ids)

    # The failure that started all this: every run "succeeded" while the scene
    # set never changed, because the parser silently fell back to cached data.
    if new_ids == old_ids:
        rep.info["identical_to_baseline"] = True
        rep.warn("scene set is byte-identical to the previous catalog. That is "
                 "normal when upstream published nothing, but it is also what a "
                 "silent fallback looks like. Check the fetch log for parsed counts.")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the SAR scene catalog.")
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--baseline", type=Path, default=None,
                    help="previous catalog to compare against (default: git HEAD)")
    ap.add_argument("--json", type=Path, default=None, help="write a JSON report here")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    if not args.catalog.exists():
        print(f"ERROR: {args.catalog} not found", file=sys.stderr)
        return 1

    rep = Report()
    doc = _load(args.catalog)
    feats = check_structure(doc, rep)
    if feats:
        check_features(feats, rep)
        check_ingestion_freshness(feats, rep)
        baseline = _load(args.baseline) if args.baseline else _git_baseline(args.catalog)
        check_regression(feats, baseline, rep)

    # -- output
    print(f"Catalog: {args.catalog}")
    print(f"  total        : {rep.info.get('total', 0)}")
    print(f"  by provider  : {rep.info.get('by_provider', {})}")
    if "baseline_total" in rep.info:
        print(f"  vs baseline  : {rep.info['baseline_total']} -> {rep.info['total']} "
              f"({rep.info['delta_total']:+d}; +{rep.info.get('added', 0)} new, "
              f"-{rep.info.get('removed', 0)} gone)")
    if "newest_first_seen" in rep.info:
        print(f"  last ingest  : {rep.info['newest_first_seen']}")

    for w in rep.warnings:
        print(f"  WARNING: {w}")
    for e in rep.errors:
        print(f"  ERROR:   {e}", file=sys.stderr)

    if args.json:
        args.json.write_text(json.dumps({
            "ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings,
            "checked_at": datetime.now(timezone.utc).isoformat(), **rep.info,
        }, indent=2))

    if rep.errors:
        print(f"\nFAILED: {len(rep.errors)} error(s). Catalog not fit to commit.",
              file=sys.stderr)
        return 1
    if args.strict and rep.warnings:
        print(f"\nFAILED (strict): {len(rep.warnings)} warning(s).", file=sys.stderr)
        return 1
    print(f"\nPASSED{f' with {len(rep.warnings)} warning(s)' if rep.warnings else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
