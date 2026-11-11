"""
Tests for the fetch itself: what happens when upstream is not there.

Everything downstream of scripts/fetch_catalog.py assumed it either produced a
fresh catalog or failed loudly. It had a third behaviour nobody wrote down: on
any download error it served the previous run's scenes for that provider and
exited 0, with a line in the log as the only trace. For a provider published as
several files it was worse than that, because the files that did arrive were
merged and the ones that did not were simply absent from the result.

These run without pyarrow (the CI test job does not install it) by replacing
read_parquet with a fake that returns DataFrames or raises, which is exactly
the boundary the behaviour lives on.
"""

import importlib.util
import json
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "fetch_catalog.py"

spec = importlib.util.spec_from_file_location("fetch_catalog", SCRIPT)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [10, 50], [10.1, 50], [10.1, 50.1], [10, 50.1], [10, 50]]]})


def rows(provider, ids):
    """A DataFrame shaped like one upstream parquet, minus the WKB geometry."""
    host = {
        "umbra": "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com",
        "iceye": "https://iceye-open-data-catalog.s3.amazonaws.com",
        "capella": "https://capella-open-data.s3.amazonaws.com",
    }[provider]
    return pd.DataFrame([{
        "id": sid,
        "geometry_geojson": SQUARE,
        "datetime": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "assets": json.dumps({"gec": {"href": f"{host}/d/{sid}.tif"}}),
        "sar:polarizations": "['VV']",
    } for sid in ids])


def fake_upstream(table):
    """read_parquet replacement: table maps file name -> DataFrame or Exception."""
    def _read(url):
        v = table[url.split("/")[-1]]
        if isinstance(v, Exception):
            raise v
        return v
    return _read


def healthy_upstream():
    t = {
        "iceye.parquet": rows("iceye", ["ICEYE_X1_GRD_SM_1"]),
        "umbra.parquet": rows("umbra", ["0000-umbra-1"]),
    }
    for fmt in fc._FORMAT_ORDER:
        t[f"capella_{fmt}.parquet"] = rows("capella", [f"CAPELLA_C02_SP_{fmt}_20260901_A"])
    return t


def cached_catalog(path, n=3):
    """A previous run's catalog on disk, so there is something to fall back on."""
    feats = []
    for prov in ("iceye", "umbra", "capella"):
        for i in range(n):
            feats.append({"type": "Feature",
                          "geometry": json.loads(SQUARE),
                          "properties": {"id": f"{prov}-old-{i}", "provider": prov,
                                         "first_seen": "2026-09-07"}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """Run main() against a fake upstream and return the catalog it wrote."""
    out = tmp_path / "scenes.geojson"
    monkeypatch.setattr(fc, "OUT_PATH", out)

    def _run(table, cache=True):
        if cache:
            cached_catalog(out)
        monkeypatch.setattr(fc, "read_parquet", fake_upstream(table))
        fc.main()
        capsys.readouterr()
        return json.loads(out.read_text())
    return _run


# --------------------------------------------------------------------------- #
# A failed fetch is recorded, not just logged
# --------------------------------------------------------------------------- #
def test_a_clean_run_records_no_failures(run):
    doc = run(healthy_upstream())
    assert doc["fetch_failures"] == {}
    assert {f["properties"]["provider"] for f in doc["features"]} == {"iceye", "umbra", "capella"}


def test_a_provider_that_cannot_be_fetched_is_named_in_the_header(run):
    t = healthy_upstream()
    t["umbra.parquet"] = fc.FetchError("umbra.parquet: could not download (timed out)")
    doc = run(t)
    assert list(doc["fetch_failures"]) == ["umbra"]
    assert "timed out" in doc["fetch_failures"]["umbra"]


def test_the_failed_provider_is_served_from_the_cache(run):
    """The site must keep working; what changes is that the run now says so."""
    t = healthy_upstream()
    t["umbra.parquet"] = fc.FetchError("umbra.parquet: could not download (503)")
    doc = run(t)
    umbra = [f["properties"]["id"] for f in doc["features"] if f["properties"]["provider"] == "umbra"]
    assert umbra == ["umbra-old-0", "umbra-old-1", "umbra-old-2"]


def test_the_other_providers_are_still_fresh(run):
    """One provider being down must not cost the others their week."""
    t = healthy_upstream()
    t["umbra.parquet"] = fc.FetchError("umbra.parquet: could not download (503)")
    doc = run(t)
    iceye = [f["properties"]["id"] for f in doc["features"] if f["properties"]["provider"] == "iceye"]
    assert iceye == ["ICEYE_X1_GRD_SM_1"]


def test_a_transient_failure_does_not_fail_the_run(run):
    """Failing would block the commit and lose the other providers' scenes.

    Recording the failure is what makes exiting 0 acceptable: the validator
    turns the record into a warning and the health issue opens from that."""
    t = healthy_upstream()
    t["iceye.parquet"] = fc.FetchError("iceye.parquet: could not download (reset)")
    run(t)                                  # no SystemExit


def test_a_failure_with_nothing_cached_is_still_recorded(run):
    t = healthy_upstream()
    t["iceye.parquet"] = fc.FetchError("iceye.parquet: could not download (reset)")
    doc = run(t, cache=False)
    assert "iceye" in doc["fetch_failures"]
    assert not [f for f in doc["features"] if f["properties"]["provider"] == "iceye"]


# --------------------------------------------------------------------------- #
# A provider is fetched whole or not at all
# --------------------------------------------------------------------------- #
def test_one_missing_capella_file_fails_the_whole_provider(monkeypatch):
    """Six of seven files is not a catalog, it is a smaller catalog.

    Acquisitions that exist only in the missing file vanish, and every other
    scene loses that format from its product list. Both are silent. The
    previous code merged what arrived."""
    t = healthy_upstream()
    t["capella_GEO.parquet"] = fc.FetchError("capella_GEO.parquet: could not download (502)")
    monkeypatch.setattr(fc, "read_parquet", fake_upstream(t))
    with pytest.raises(fc.FetchError):
        fc.fetch_provider("capella")


def test_a_partial_capella_fetch_falls_back_whole(run):
    t = healthy_upstream()
    t["capella_GEO.parquet"] = fc.FetchError("capella_GEO.parquet: could not download (502)")
    doc = run(t)
    assert "capella" in doc["fetch_failures"]
    cap = [f["properties"]["id"] for f in doc["features"] if f["properties"]["provider"] == "capella"]
    assert cap == ["capella-old-0", "capella-old-1", "capella-old-2"], \
        "the six files that arrived were merged into a partial catalog"


def test_a_file_that_is_not_parquet_is_a_fetch_error(monkeypatch):
    """read_parquet used to return None here, the same silent path.

    The reader is stubbed because the CI test job has no pyarrow; what is
    under test is that a parse failure becomes a FetchError, not pyarrow."""
    class Reader:
        @staticmethod
        def read_table(buf):
            raise ValueError("Parquet magic bytes not found")
    monkeypatch.setattr(fc, "_parquet", lambda: Reader)
    monkeypatch.setattr(fc, "fetch_bytes", lambda url, **k: b"<html>rate limited</html>")
    with pytest.raises(fc.FetchError) as e:
        fc.read_parquet("https://x/iceye.parquet")
    assert "iceye.parquet" in str(e.value)


# --------------------------------------------------------------------------- #
# Transient errors are retried
# --------------------------------------------------------------------------- #
class Flaky:
    """urlopen that fails n times, then returns a response holding `body`."""

    def __init__(self, failures, body=b"ok"):
        self.failures, self.body, self.calls = list(failures), body, 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def http(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(fc.time, "sleep", waits.append)
    return waits


def test_a_dropped_connection_is_retried(monkeypatch, no_sleep):
    flaky = Flaky([urllib.error.URLError("reset")])
    monkeypatch.setattr(fc.urllib.request, "urlopen", flaky)
    assert fc.fetch_bytes("https://x/a.parquet") == b"ok"
    assert flaky.calls == 2


def test_a_server_error_is_retried(monkeypatch, no_sleep):
    flaky = Flaky([http(503), http(502)])
    monkeypatch.setattr(fc.urllib.request, "urlopen", flaky)
    assert fc.fetch_bytes("https://x/a.parquet") == b"ok"
    assert flaky.calls == 3


def test_retries_back_off(monkeypatch, no_sleep):
    flaky = Flaky([http(503), http(503)])
    monkeypatch.setattr(fc.urllib.request, "urlopen", flaky)
    fc.fetch_bytes("https://x/a.parquet", backoff=2.0)
    assert no_sleep == [2.0, 4.0]


def test_a_missing_file_is_not_retried(monkeypatch, no_sleep):
    """404 means the file moved. Three attempts would not find it."""
    flaky = Flaky([http(404)])
    monkeypatch.setattr(fc.urllib.request, "urlopen", flaky)
    with pytest.raises(urllib.error.HTTPError):
        fc.fetch_bytes("https://x/a.parquet")
    assert flaky.calls == 1 and no_sleep == []


def test_the_last_error_surfaces_after_the_last_attempt(monkeypatch, no_sleep):
    flaky = Flaky([http(503), http(503), urllib.error.URLError("gave up")])
    monkeypatch.setattr(fc.urllib.request, "urlopen", flaky)
    with pytest.raises(urllib.error.URLError) as e:
        fc.fetch_bytes("https://x/a.parquet", attempts=3)
    assert "gave up" in str(e.value)
    assert flaky.calls == 3


# --------------------------------------------------------------------------- #
# Row handling
# --------------------------------------------------------------------------- #
def test_the_future_cutoff_survives_a_leap_day(monkeypatch):
    """`now.replace(year=now.year + 1)` raised ValueError on 29 February."""
    class LeapDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2028, 2, 29, 3, 0, tzinfo=tz)
    monkeypatch.setattr(fc, "datetime", LeapDay)
    row = rows("umbra", ["u1"]).iloc[0]
    assert fc.normalize_row(row, "umbra") is not None


def test_a_scene_dated_years_ahead_is_dropped():
    """Umbra's parquet carries a few scenes dated 2035."""
    df = rows("umbra", ["u-2035"])
    df["datetime"] = datetime(2035, 1, 9, tzinfo=timezone.utc)
    assert fc.normalize_row(df.iloc[0], "umbra") is None


def test_a_collection_footprint_is_not_a_scene():
    row = rows("iceye", ["000-iceye-sar-collection-footprints"]).iloc[0]
    assert fc.normalize_row(row, "iceye") is None


# --------------------------------------------------------------------------- #
# The record of what upstream handed over
# --------------------------------------------------------------------------- #
import hashlib  # noqa: E402


def digesting_upstream(table):
    """Like fake_upstream, and records a digest per file as read_parquet does."""
    inner = fake_upstream(table)

    def _read(url):
        df = inner(url)
        fc.FILE_DIGESTS[url.split("/")[-1]] = hashlib.sha256(
            df.to_json(date_format="iso").encode()).hexdigest()
        return df
    return _read


@pytest.fixture
def run_tracked(tmp_path, monkeypatch, capsys):
    """Run main() repeatedly against one catalog file, with digests recorded."""
    out = tmp_path / "scenes.geojson"
    monkeypatch.setattr(fc, "OUT_PATH", out)

    def _run(table):
        monkeypatch.setattr(fc, "read_parquet", digesting_upstream(table))
        fc.main()
        capsys.readouterr()
        return json.loads(out.read_text())
    _run.path = out
    return _run


def rewrite(path, fn):
    doc = json.loads(path.read_text())
    fn(doc)
    path.write_text(json.dumps(doc))


TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_the_header_records_what_each_provider_amounted_to(run_tracked):
    up = run_tracked(healthy_upstream())["upstream"]
    assert set(up) == {"iceye", "umbra", "capella"}
    assert up["umbra"]["rows"] == 1 and up["umbra"]["scenes"] == 1
    assert up["capella"]["rows"] == 7, "seven format files, one row each"
    assert up["umbra"]["skipped"] == 0
    assert len(up["umbra"]["digest"]) == 64
    assert up["umbra"]["checked"] == TODAY


def test_identical_files_keep_the_date_they_were_last_seen_to_change(run_tracked):
    run_tracked(healthy_upstream())
    rewrite(run_tracked.path, lambda d: [
        r.update(unchanged_since="2026-09-27", checked="2026-11-02")
        for r in d["upstream"].values()])
    up = run_tracked(healthy_upstream())["upstream"]
    assert up["umbra"]["unchanged_since"] == "2026-09-27", "the date must carry forward"
    assert up["umbra"]["checked"] == TODAY, "and the check must be today's"


def test_a_changed_file_resets_the_date(run_tracked):
    run_tracked(healthy_upstream())
    rewrite(run_tracked.path, lambda d: [
        r.update(unchanged_since="2026-09-27", checked="2026-11-02")
        for r in d["upstream"].values()])
    t = healthy_upstream()
    t["umbra.parquet"] = rows("umbra", ["0000-umbra-1", "0000-umbra-2"])
    up = run_tracked(t)["upstream"]
    assert up["umbra"]["unchanged_since"] == TODAY
    assert up["iceye"]["unchanged_since"] == "2026-09-27", "only the provider that changed"


def test_a_provider_that_failed_keeps_its_previous_record(run_tracked):
    """Nothing was learned about upstream, so nothing may be claimed. In
    particular `checked` must not move, or the validator would read a failed
    fetch as proof that upstream was unchanged."""
    run_tracked(healthy_upstream())
    rewrite(run_tracked.path, lambda d: [
        r.update(unchanged_since="2026-09-27", checked="2026-11-02")
        for r in d["upstream"].values()])
    t = healthy_upstream()
    t["umbra.parquet"] = fc.FetchError("umbra.parquet: could not download (503)")
    doc = run_tracked(t)
    assert "umbra" in doc["fetch_failures"]
    assert doc["upstream"]["umbra"]["checked"] == "2026-11-02"
    assert doc["upstream"]["iceye"]["checked"] == TODAY


def test_the_first_tracked_run_dates_an_unchanged_provider_from_its_scenes(run_tracked):
    """Deploying this must not make every provider look freshly changed. With
    no earlier digest, a provider whose ids are exactly last run's has not
    changed since at least its newest first_seen."""
    run_tracked(healthy_upstream())

    def strip(d):
        d.pop("upstream")
        for f in d["features"]:
            if f["properties"]["provider"] == "umbra":
                f["properties"]["first_seen"] = "2026-09-28"
    rewrite(run_tracked.path, strip)
    up = run_tracked(healthy_upstream())["upstream"]
    assert up["umbra"]["unchanged_since"] == "2026-09-28"


def test_the_first_tracked_run_does_not_backdate_a_provider_that_changed(run_tracked):
    run_tracked(healthy_upstream())
    rewrite(run_tracked.path, lambda d: d.pop("upstream"))
    t = healthy_upstream()
    t["umbra.parquet"] = rows("umbra", ["0000-umbra-1", "0000-umbra-2"])
    assert run_tracked(t)["upstream"]["umbra"]["unchanged_since"] == TODAY


def test_no_digest_no_claim(run):
    """The plain fake records no digests, as a reader that could not hash the
    file would. The record must then carry none rather than invent one."""
    up = run(healthy_upstream())["upstream"]
    assert up["umbra"]["digest"] is None


def test_read_parquet_records_the_digest_of_the_bytes_it_read(monkeypatch):
    class Reader:
        @staticmethod
        def read_table(buf):
            class T:
                @staticmethod
                def to_pandas():
                    return rows("iceye", ["a"])
            return T
    monkeypatch.setattr(fc, "_parquet", lambda: Reader)
    monkeypatch.setattr(fc, "fetch_bytes", lambda url, **k: b"parquet bytes")
    fc.FILE_DIGESTS.clear()
    fc.read_parquet("https://x/iceye.parquet")
    assert fc.FILE_DIGESTS["iceye.parquet"] == hashlib.sha256(b"parquet bytes").hexdigest()
    assert fc.provider_digest("iceye") is not None
    assert fc.provider_digest("capella") is None, "six of its seven files are unknown"


def test_an_empty_upstream_file_does_not_overwrite_the_record(run_tracked):
    """A file that downloads and parses but holds no rows raises nothing: the
    provider is served from the cache as "fetched no scenes". Its record must
    stay the previous one, or an upstream that briefly published an empty file
    would be written down as a real change of content."""
    run_tracked(healthy_upstream())
    rewrite(run_tracked.path, lambda d: [
        r.update(unchanged_since="2026-09-27", checked="2026-11-02")
        for r in d["upstream"].values()])
    t = healthy_upstream()
    t["umbra.parquet"] = pd.DataFrame()
    doc = run_tracked(t)
    assert doc["fetch_failures"]["umbra"] == "fetched no scenes"
    assert doc["upstream"]["umbra"]["rows"] == 1, "the empty file replaced the record"
    assert doc["upstream"]["umbra"]["checked"] == "2026-11-02"
