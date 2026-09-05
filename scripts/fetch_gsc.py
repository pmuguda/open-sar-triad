#!/usr/bin/env python3
"""
Pulls aggregated Google Search Console performance stats for the
open-sar-triad pages and writes them to data/usage.json, which the site's
"Visitors" world map renders.

Search Console data is private to verified owners, so this never runs in the
browser. It runs in CI with a service-account key supplied as the
GSC_SERVICE_ACCOUNT_JSON secret; only aggregated numbers reach the repo.

The app is reachable under more than one Search Console property (the canonical
www.pmuguda.com URL and the GitHub Pages URL it is served from), so
GSC_SITE_URL accepts a comma-separated list and the results are merged.

Setup (one time):
  1. Google Cloud console: create a project, enable "Google Search Console API",
     create a service account, and download its JSON key.
  2. Search Console: add the service account's email as a user (Restricted is
     enough) on EACH property listed in GSC_SITE_URL.
  3. GitHub: repo Settings -> Secrets and variables -> Actions -> new secret
     GSC_SERVICE_ACCOUNT_JSON with the full JSON key as its value.

Environment:
  GSC_SERVICE_ACCOUNT_JSON   service-account key JSON (required to run)
  GSC_SITE_URL               comma-separated properties (URL-prefix strings must
                             match Search Console exactly, incl. trailing slash)
  GSC_PAGE_CONTAINS          page filter, default /open-sar-triad
  GSC_WINDOW_DAYS            reporting window, default 90

Exit codes: 0 on success or when not configured (secret absent, so the
scheduled job stays green before setup); 0 with a WARNING if at least one
property succeeded but another was refused; 1 when no property could be read
or on any other API/auth error.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

OUT_PATH = Path(__file__).parent.parent / "data" / "usage.json"

DEFAULT_SITES = "https://www.pmuguda.com/open-sar-triad/,https://pmuguda.github.io/open-sar-triad/"
SITE_URLS     = [s.strip() for s in os.environ.get("GSC_SITE_URL", DEFAULT_SITES).split(",") if s.strip()]
PAGE_CONTAINS = os.environ.get("GSC_PAGE_CONTAINS", "/open-sar-triad")
WINDOW_DAYS   = int(os.environ.get("GSC_WINDOW_DAYS", "90"))
# Search Console data lags by roughly 2-3 days; end the window 3 days ago so the
# last few days are complete rather than partial.
LAG_DAYS = 3


def load_credentials():
    raw = os.environ.get("GSC_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: GSC_SERVICE_ACCOUNT_JSON is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )


def query(service, site_url, start, end, dimensions, row_limit=1000):
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    # A page filter forces Search Console's "by page" aggregation, which drops
    # dimension rows (country, device, query) at low volumes — the first live run
    # returned totals but zero countries. The properties are already scoped to
    # the app, so the filter is only applied when explicitly configured (e.g. for
    # a whole-domain property).
    if PAGE_CONTAINS:
        body["dimensionFilterGroups"] = [{
            "filters": [{
                "dimension": "page",
                "operator": "contains",
                "expression": PAGE_CONTAINS,
            }]
        }]
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = resp.get("rows", [])
    print(f"    {site_url} {dimensions or ['(totals)']}: {len(rows)} rows")
    return rows


def country_meta(code):
    """Resolve a Search Console alpha-3 code ('usa', 'zzz' = unknown) to a display
    name and the ISO numeric id the site's world-atlas polygons are keyed by."""
    code = (code or "").upper()
    if not code or code == "ZZZ":
        return code or "ZZZ", None
    try:
        import pycountry
        c = pycountry.countries.get(alpha_3=code)
        if c:
            return c.name, c.numeric
    except ImportError:
        pass
    return code, None


def print_accessible_sites(service):
    """On a permission error, show which properties the service account CAN see,
    so a mismatch between GSC_SITE_URL and the property it was granted is obvious
    instead of a bare 403."""
    try:
        sites = service.sites().list().execute().get("siteEntry", [])
    except Exception:
        return
    if not sites:
        print("DIAG: the service account has access to NO Search Console properties. "
              "In Search Console open the property, then Settings -> Users and "
              "permissions -> Add user, and add the service account's email.",
              file=sys.stderr)
        return
    print("DIAG: the service account can see these properties:", file=sys.stderr)
    for s in sites:
        print(f"  - {s.get('siteUrl')}  ({s.get('permissionLevel')})", file=sys.stderr)
    print(f"DIAG: GSC_SITE_URL asks for: {', '.join(SITE_URLS)}. Grant the account access "
          "to any that are missing above, or drop them from GSC_SITE_URL.", file=sys.stderr)


def fetch_site(service, site_url, start, end):
    """All the rows for one property. Raises HttpError on failure."""
    return {
        "totals":    query(service, site_url, start, end, []),
        "daily":     query(service, site_url, start, end, ["date"]),
        "countries": query(service, site_url, start, end, ["country"]),
        "queries":   query(service, site_url, start, end, ["query"]),
        "devices":   query(service, site_url, start, end, ["device"]),
    }


def merge(results):
    """Sum per-property rows into one dataset. CTR is recomputed from the summed
    clicks/impressions; position is impression-weighted."""
    clicks = impressions = 0
    pos_weighted = 0.0
    daily = defaultdict(lambda: {"clicks": 0, "impressions": 0})
    countries = defaultdict(lambda: {"clicks": 0, "impressions": 0})
    queries = defaultdict(lambda: {"clicks": 0, "impressions": 0})
    devices = defaultdict(lambda: {"clicks": 0, "impressions": 0})

    def add(bucket, key, r):
        bucket[key]["clicks"]      += int(r.get("clicks", 0))
        bucket[key]["impressions"] += int(r.get("impressions", 0))

    for res in results:
        for r in res["totals"]:
            c, i = int(r.get("clicks", 0)), int(r.get("impressions", 0))
            clicks += c
            impressions += i
            pos_weighted += float(r.get("position", 0.0)) * i
        for r in res["daily"]:     add(daily,     r["keys"][0], r)
        for r in res["countries"]: add(countries, (r["keys"][0] or "").upper(), r)
        for r in res["queries"]:   add(queries,   r["keys"][0], r)
        for r in res["devices"]:   add(devices,   r["keys"][0], r)

    totals = {
        "clicks":      clicks,
        "impressions": impressions,
        "ctr":         round(clicks / impressions * 100, 2) if impressions else 0.0,
        "position":    round(pos_weighted / impressions, 1) if impressions else 0.0,
    }
    by_clicks = lambda kv: (-kv[1]["clicks"], -kv[1]["impressions"])

    country_rows = []
    for code, v in sorted(countries.items(), key=by_clicks):
        name, n3 = country_meta(code)
        country_rows.append({"code": code, "name": name, "iso_n3": n3, **v})

    return {
        "totals":    totals,
        "daily":     [{"date": d, **v} for d, v in sorted(daily.items())],
        "countries": country_rows,
        "queries":   [{"query": q, **v}  for q, v in sorted(queries.items(), key=by_clicks)[:10]],
        "devices":   [{"device": d, **v} for d, v in sorted(devices.items(), key=by_clicks)[:3]],
    }


def main():
    creds = load_credentials()
    if creds is None:
        print("GSC_SERVICE_ACCOUNT_JSON not set — usage stats not configured; skipping.")
        return 0
    if not SITE_URLS:
        print("ERROR: GSC_SITE_URL is empty.", file=sys.stderr)
        return 1

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    end   = date.today() - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=WINDOW_DAYS - 1)

    results, ok, refused = [], [], []
    diagnosed = False
    for site in SITE_URLS:
        try:
            results.append(fetch_site(service, site, start, end))
            ok.append(site)
            print(f"  [{site}] ok")
        except HttpError as e:
            # 403 = the service account was not added as a user on this property.
            # Say which properties it can see, keep going, and decide at the end.
            if getattr(e.resp, "status", None) == 403:
                print(f"  [{site}] refused: no permission", file=sys.stderr)
                refused.append(site)
                if not diagnosed:
                    print_accessible_sites(service)
                    diagnosed = True
                continue
            raise

    if not ok:
        print("ERROR: no Search Console property could be read.", file=sys.stderr)
        return 1

    merged = merge(results)
    payload = {
        "source":            "Google Search Console",
        "properties":        SITE_URLS,
        "properties_ok":     ok,
        "properties_failed": refused,
        "page_filter":       PAGE_CONTAINS,
        "updated":           date.today().isoformat(),
        "window":            {"start": start.isoformat(), "end": end.isoformat(), "days": WINDOW_DAYS},
        **merged,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))
    t = merged["totals"]
    print(f"Wrote {OUT_PATH}: {t['clicks']} clicks / {t['impressions']} impressions "
          f"over {start} .. {end} from {len(ok)} of {len(SITE_URLS)} properties")

    if refused:
        print(f"WARNING: {len(refused)} property(ies) refused and NOT counted: "
              f"{', '.join(refused)}. Add the service account as a user there.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # real API / auth failure: go red so it is noticed
        print(f"ERROR: Search Console fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
