#!/usr/bin/env python3
"""
Pulls aggregated Google Search Console performance stats for the
open-sar-triad pages and writes them to data/usage.json, which the site's
"Usage" panel renders.

Search Console data is private to verified owners, so this never runs in the
browser. It runs in CI with a service-account key supplied as the
GSC_SERVICE_ACCOUNT_JSON secret; only aggregated numbers reach the repo.

Setup (one time):
  1. Google Cloud console: create a project, enable "Google Search Console API",
     create a service account, and download its JSON key.
  2. Search Console: add the service account's email as a user (Full or
     Restricted) on the property named by GSC_SITE_URL.
  3. GitHub: repo Settings -> Secrets and variables -> Actions -> new secret
     GSC_SERVICE_ACCOUNT_JSON with the full JSON key as its value.

Environment:
  GSC_SERVICE_ACCOUNT_JSON   service-account key JSON (required to run)
  GSC_SITE_URL               property, default https://www.pmuguda.com/open-sar-triad/
  GSC_PAGE_CONTAINS          page filter, default /open-sar-triad
  GSC_WINDOW_DAYS            reporting window, default 90

Exit codes: 0 on success or when not configured (secret absent, so the
scheduled job stays green before setup); 1 on a real API/auth error.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

OUT_PATH = Path(__file__).parent.parent / "data" / "usage.json"

# URL-prefix property: must match the Search Console property string exactly
# (scheme, www, trailing slash). A domain property would be "sc-domain:host".
SITE_URL      = os.environ.get("GSC_SITE_URL", "https://www.pmuguda.com/open-sar-triad/")
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


def query(service, start, end, dimensions, row_limit=1000):
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dimensionFilterGroups": [{
            "filters": [{
                "dimension": "page",
                "operator": "contains",
                "expression": PAGE_CONTAINS,
            }]
        }],
    }
    resp = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return resp.get("rows", [])


def rows_to_list(rows, key_name, top=10):
    out = []
    for r in rows:
        out.append({
            key_name:      r["keys"][0],
            "clicks":      int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
        })
    out.sort(key=lambda x: (-x["clicks"], -x["impressions"]))
    return out[:top]


def enrich_countries(rows):
    """Country rows for the world map. Search Console reports ISO 3166-1 alpha-3
    codes (lowercase, 'zzz' = unknown); the site's world-atlas polygons are keyed
    by ISO numeric id, so resolve name + numeric here and the page just joins."""
    try:
        import pycountry
    except ImportError:
        pycountry = None
    out = []
    for r in rows:
        code = (r["keys"][0] or "").upper()
        name, n3 = code, None
        if pycountry and code and code != "ZZZ":
            c = pycountry.countries.get(alpha_3=code)
            if c:
                name, n3 = c.name, c.numeric
        out.append({
            "code":        code,
            "name":        name,
            "iso_n3":      n3,
            "clicks":      int(r.get("clicks", 0)),
            "impressions": int(r.get("impressions", 0)),
        })
    out.sort(key=lambda x: (-x["clicks"], -x["impressions"]))
    return out


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
    print(f"DIAG: but GSC_SITE_URL is '{SITE_URL}'. Either set GSC_SITE_URL to one of "
          "the properties above, or grant the account access to that property.",
          file=sys.stderr)


def main():
    creds = load_credentials()
    if creds is None:
        print("GSC_SERVICE_ACCOUNT_JSON not set — usage stats not configured; skipping.")
        return 0

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    end   = date.today() - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=WINDOW_DAYS - 1)

    # Totals over the window (no dimension). A 403 here almost always means the
    # service account was not added as a user on the property; say which
    # properties it can actually see before failing.
    try:
        total_rows = query(service, start, end, [])
    except HttpError as e:
        if getattr(e.resp, "status", None) == 403:
            print_accessible_sites(service)
        raise
    t = total_rows[0] if total_rows else {}
    totals = {
        "clicks":      int(t.get("clicks", 0)),
        "impressions": int(t.get("impressions", 0)),
        "ctr":         round(float(t.get("ctr", 0.0)) * 100, 2),   # percent
        "position":    round(float(t.get("position", 0.0)), 1),
    }

    daily = [{
        "date":        r["keys"][0],
        "clicks":      int(r.get("clicks", 0)),
        "impressions": int(r.get("impressions", 0)),
    } for r in query(service, start, end, ["date"])]
    daily.sort(key=lambda x: x["date"])

    countries = enrich_countries(query(service, start, end, ["country"]))
    queries   = rows_to_list(query(service, start, end, ["query"]),   "query")
    devices   = rows_to_list(query(service, start, end, ["device"]),  "device", top=3)

    payload = {
        "source":      "Google Search Console",
        "property":    SITE_URL,
        "page_filter": PAGE_CONTAINS,
        "updated":     date.today().isoformat(),
        "window":      {"start": start.isoformat(), "end": end.isoformat(), "days": WINDOW_DAYS},
        "totals":      totals,
        "daily":       daily,
        "countries":   countries,
        "queries":     queries,
        "devices":     devices,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {OUT_PATH}: {totals['clicks']} clicks / {totals['impressions']} impressions "
          f"over {start} .. {end}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # real API / auth failure: go red so it is noticed
        print(f"ERROR: Search Console fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
