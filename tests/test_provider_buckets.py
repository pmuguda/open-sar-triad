"""
Tests for reading the providers' own buckets.

The catalog used to wait on one aggregator's weekly crawl, so a scene a
provider uploaded on a Tuesday could not appear before the next Monday, and a
scene ICEYE had uploaded but not yet catalogued could not appear at all.
scripts/provider_buckets.py lists each bucket and fetches what the catalog
lacks. These run it against an in-memory S3 that speaks the same list protocol
(prefixes, pages, continuation tokens) and serves the same three layouts the
real buckets have, taken from real keys.
"""

import importlib.util
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("provider_buckets", ROOT / "scripts" / "provider_buckets.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)
OLD = "2026-10-30T11:22:00.000Z"

SQUARE = {"type": "Polygon", "coordinates": [[[10, 50], [10.1, 50], [10.1, 50.1], [10, 50.1], [10, 50]]]}


def item(item_id, when="2026-11-10T04:16:54Z", **props):
    return {"type": "Feature", "id": item_id, "geometry": SQUARE, "collection": None,
            "properties": {"datetime": when, "sar:instrument_mode": "spotlight",
                           "sar:polarizations": ["VV"], **props},
            "assets": {}}


class FakeS3:
    """Serves list-type=2 listings and object GETs for any of the three hosts."""

    def __init__(self, page_size=1000):
        self.objects = {}            # (host, key) -> (bytes, last_modified)
        self.page_size = page_size
        self.requests = []
        self.fail = lambda url: None  # return an Exception to raise for that URL

    def put(self, provider, key, body=b"x", modified=OLD):
        host = urllib.parse.urlsplit(pb.BUCKETS[provider]).netloc
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.objects[(host, key)] = (body, modified)

    def __call__(self, url, **kwargs):
        self.requests.append(url)
        err = self.fail(url)
        if err:
            raise err
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        if "list-type" in query:
            return self._list(parts.netloc, query)
        key = urllib.parse.unquote(parts.path).lstrip("/")
        if (parts.netloc, key) not in self.objects:
            raise OSError(f"404 {key}")
        return self.objects[(parts.netloc, key)][0]

    def _list(self, host, query):
        prefix = query.get("prefix", [""])[0]
        keys = sorted(k for (h, k) in self.objects if h == host and k.startswith(prefix))
        start = int(query.get("continuation-token", ["0"])[0])
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        body = ['<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">']
        for k in page:
            k_xml = k.replace("&", "&amp;")
            body.append(f"<Contents><Key>{k_xml}</Key><LastModified>{self.objects[(host, k)][1]}"
                        "</LastModified></Contents>")
        body.append(f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>")
        if more:
            body.append(f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>")
        body.append("</ListBucketResult>")
        return "".join(body).encode()

    def object_gets(self):
        return [u for u in self.requests if "list-type" not in u]


@pytest.fixture
def s3():
    return FakeS3()


# --- the three layouts, from real keys ------------------------------------- #
def umbra_scene(s3, task, stem, scene_id, products=("GEC", "SICD", "SIDD"), modified=OLD, root="task-data"):
    d = f"sar-data/{root}/{task}/{stem}"
    s3.put("umbra", f"{d}/{stem}.stac.v2.json", item(scene_id), modified)
    ext = {"GEC": "tif", "CSI": "tif", "SICD": "nitf", "SIDD": "nitf", "CPHD": "cphd"}
    for p in products:
        s3.put("umbra", f"{d}/{stem}_{p}.{ext[p]}", modified=modified)
    return f"{d}/{stem}.stac.v2.json"


def iceye_catalogued(s3, scene_id, mode="spot"):
    it = item(scene_id)
    base = f"https://iceye-open-data-catalog.s3.amazonaws.com/data/{mode}/{scene_id}/{scene_id}"
    it["assets"] = {"grd-cog": {"href": f"{base}_GRD.tif"}, "slc-cog": {"href": f"{base}_SLC.tif"}}
    s3.put("iceye", f"stac-items/2026/09/{scene_id}.json", it)
    iceye_folder(s3, scene_id, mode)


def iceye_folder(s3, scene_id, mode="dwell-fine", modified=OLD, sidecars=("GRD", "SLC")):
    d = f"data/{mode}/{scene_id}"
    for s in sidecars:
        side = item(f"ICEYE_10481669_{s}-COG_5bed384b_1", **{"sar:product_type": f"{s}-COG",
                                                              "view:incidence_angle": 31.2})
        s3.put("iceye", f"{d}/{scene_id}_{s}.json", side, modified)
        s3.put("iceye", f"{d}/{scene_id}_{s}.tif", modified=modified)
    s3.put("iceye", f"{d}/{scene_id}_THM.png", modified=modified)


def capella_product(s3, scene_id, modified=OLD):
    day = "capella-open-data-2026/capella-open-data-2026-09/capella-open-data-2026-09-09"
    it = item(scene_id)
    it["assets"] = {"HH": {"href": f"https://capella-open-data.s3.amazonaws.com/data/2026/9/9/{scene_id}/{scene_id}.tif"}}
    s3.put("capella", f"stac/capella-open-data-by-datetime/{day}/{scene_id}/{scene_id}.json", it, modified)


ICEYE_A = "ICEYE_WYPY1N_20260904T145307Z_10445347_X47_SLEDP"
ICEYE_B = "ICEYE_SWPJQ4_20260911T084830Z_10481669_X61_SLEDF"
CAP_GEC = "CAPELLA_C13_SP_GEC_HH_20260909150138_20260909150146"
CAP_SLC = "CAPELLA_C13_SP_SLC_HH_20260909150138_20260909150146"


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
def test_a_listing_follows_every_page():
    s3 = FakeS3(page_size=3)
    for i in range(10):
        s3.put("capella", f"stac/x/{i:02d}.json")
    keys = pb.list_bucket(pb.BUCKETS["capella"], "stac/", s3)
    assert len(keys) == 10 and len(s3.requests) == 4


def test_a_listing_keeps_to_its_prefix(s3):
    s3.put("iceye", "stac-items/a.json")
    s3.put("iceye", "data/b.tif")
    assert [k for k, _ in pb.list_bucket(pb.BUCKETS["iceye"], "data/", s3)] == ["data/b.tif"]


def test_a_listing_that_fails_part_way_is_an_error_not_a_short_list():
    """Half a listing would make scenes that are there look withdrawn."""
    s3 = FakeS3(page_size=3)
    for i in range(10):
        s3.put("capella", f"stac/x/{i:02d}.json")
    s3.fail = lambda url: OSError("reset") if "continuation-token=6" in url else None
    with pytest.raises(pb.BucketError):
        pb.list_bucket(pb.BUCKETS["capella"], "stac/", s3)


def test_a_listing_marked_truncated_with_no_way_to_continue_is_an_error():
    """Returning what arrived would silently be a short list."""
    body = (b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<Contents><Key>stac/a.json</Key><LastModified>x</LastModified></Contents>"
            b"<IsTruncated>true</IsTruncated></ListBucketResult>")
    with pytest.raises(pb.BucketError, match="truncated"):
        pb.list_bucket(pb.BUCKETS["capella"], "stac/", lambda url: body)


def test_a_body_that_is_not_a_listing_is_an_error(s3):
    with pytest.raises(pb.BucketError):
        pb.list_bucket(pb.BUCKETS["capella"], "stac/", lambda url: b"<html>Service Unavailable</html")


def test_keys_with_spaces_and_ampersands_survive(s3):
    """Umbra names task folders after places."""
    key = umbra_scene(s3, "Trinidad & Tobago Port", "2026-11-10-04-16-54_UMBRA-09", "u1", root="tasks")
    found = pb.discover("umbra", pb.list_bucket(pb.BUCKETS["umbra"], "sar-data/", s3), NOW)
    assert key in found
    assert "%20" in pb.public_url("umbra", key) and " " not in pb.public_url("umbra", key)


# --------------------------------------------------------------------------- #
# Umbra
# --------------------------------------------------------------------------- #
def test_a_new_umbra_scene_is_read_from_the_bucket(s3):
    umbra_scene(s3, "4d0d855f", "2026-11-10-04-16-54_UMBRA-09", "8624c0ce")
    rows, stats = pb.supplement("umbra", set(), s3, now=NOW)
    assert stats["error"] is None and stats["added"] == 1
    assert rows[0]["id"] == "8624c0ce"
    assert json.loads(rows[0]["geometry_geojson"]) == SQUARE


def test_umbra_assets_are_the_files_that_exist_not_the_private_links(s3):
    """The item's own assets point into Umbra's private bucket. The public
    files sit beside it, and only some formats are published per scene."""
    umbra_scene(s3, "4d0d855f", "2026-11-10-04-16-54_UMBRA-09", "u1", products=("GEC", "SICD"))
    rows, _ = pb.supplement("umbra", set(), s3, now=NOW)
    assets = rows[0]["assets"]
    assert set(assets) == {"gec", "sicd"}
    assert assets["gec"]["href"].endswith("/2026-11-10-04-16-54_UMBRA-09_GEC.tif")
    assert assets["gec"]["href"].startswith(pb.BUCKETS["umbra"])


def test_an_umbra_scene_the_catalog_has_is_not_fetched_again(s3):
    key = umbra_scene(s3, "4d0d855f", "2026-11-10-04-16-54_UMBRA-09", "u1")
    feature = {"properties": {"id": "u1", "products": {
        "GEC": pb.public_url("umbra", key.replace(".stac.v2.json", "_GEC.tif"))}}}
    rows, stats = pb.supplement("umbra", pb.known_keys("umbra", [feature]), s3, now=NOW)
    assert rows == [] and stats["missing"] == 0
    assert s3.object_gets() == [], "a known scene's item was downloaded anyway"


def test_an_umbra_scene_is_recognised_through_a_dead_link_too(s3):
    """The aggregator writes a CSI link for every scene whether or not the file
    exists. A scene known only by such a link is still known."""
    key = umbra_scene(s3, "4d0d855f", "2026-11-10-04-16-54_UMBRA-09", "u1")
    dead = pb.public_url("umbra", key.replace(".stac.v2.json", "_CSI.tif"))
    assert pb.known_keys("umbra", [{"properties": {"id": "u1", "products": {"CSI": dead}}}]) == {key}


def test_an_umbra_folder_with_no_public_files_is_not_a_scene(s3):
    umbra_scene(s3, "4d0d855f", "2026-11-10-04-16-54_UMBRA-09", "u1", products=())
    rows, stats = pb.supplement("umbra", set(), s3, now=NOW)
    assert rows == [] and stats["failed"] == 1


# --------------------------------------------------------------------------- #
# ICEYE
# --------------------------------------------------------------------------- #
def test_an_iceye_scene_in_its_catalogue_is_read_from_the_catalogue(s3):
    iceye_catalogued(s3, ICEYE_A)
    rows, stats = pb.supplement("iceye", set(), s3, now=NOW)
    assert [r["id"] for r in rows] == [ICEYE_A]
    assert stats["from_folders"] == 0
    assert set(rows[0]["assets"]) == {"grd-cog", "slc-cog"}


def test_an_iceye_scene_not_yet_catalogued_is_read_from_its_folder(s3):
    """24 scenes sat in ICEYE's bucket for weeks with no catalogue record. Each
    product's sidecar is a STAC Feature with the footprint and the time."""
    iceye_folder(s3, ICEYE_B)
    rows, stats = pb.supplement("iceye", set(), s3, now=NOW)
    assert stats["added"] == 1 and stats["from_folders"] == 1
    row = rows[0]
    assert row["id"] == ICEYE_B, "the scene's id is its folder, not the sidecar's product id"
    assert row["view:incidence_angle"] == 31.2
    assert json.loads(row["geometry_geojson"]) == SQUARE


def test_folder_assets_use_iceyes_own_key_names(s3):
    """So a scene read from its folder looks exactly like a catalogued one."""
    iceye_folder(s3, ICEYE_B)
    rows, _ = pb.supplement("iceye", set(), s3, now=NOW)
    assets = rows[0]["assets"]
    assert {"grd-cog", "grd-json", "slc-cog", "slc-json", "thumbnail"} <= set(assets)
    assert assets["grd-cog"]["href"] == f"{pb.BUCKETS['iceye']}/data/dwell-fine/{ICEYE_B}/{ICEYE_B}_GRD.tif"
    assert assets["thumbnail"]["href"].endswith("_THM.png")


def test_a_catalogued_iceye_scene_is_not_also_read_from_its_folder(s3):
    iceye_catalogued(s3, ICEYE_A)
    found = pb.discover("iceye", pb.list_bucket(pb.BUCKETS["iceye"], "", s3), NOW)
    assert found[ICEYE_A]["kind"] == "item"
    assert len(found) == 1


def test_an_iceye_folder_still_being_uploaded_is_left_for_next_time(s3):
    """Half a folder would publish a scene with half its products."""
    iceye_folder(s3, ICEYE_B, modified="2026-11-11T14:30:00.000Z")     # 90 minutes before NOW
    rows, stats = pb.supplement("iceye", set(), s3, now=NOW)
    assert rows == [] and stats["listed"] == 0


def test_an_iceye_folder_falls_back_to_another_sidecar(s3):
    iceye_folder(s3, ICEYE_B, sidecars=("SLC",))
    rows, _ = pb.supplement("iceye", set(), s3, now=NOW)
    assert rows[0]["id"] == ICEYE_B


def test_iceyes_summary_and_footprint_files_are_not_scenes(s3):
    s3.put("iceye", "stac-items/000-iceye-sar-dwell-footprints.json", item("x"))
    s3.put("iceye", "stac-items/summary/footprint-summary-points.geojson", item("y"))
    assert pb.supplement("iceye", set(), s3, now=NOW)[1]["listed"] == 0


def test_an_iceye_scene_the_catalog_has_is_known_by_its_id(s3):
    iceye_catalogued(s3, ICEYE_A)
    rows, _ = pb.supplement("iceye", pb.known_keys("iceye", [{"properties": {"id": ICEYE_A}}]), s3, now=NOW)
    assert rows == []


# --------------------------------------------------------------------------- #
# Capella
# --------------------------------------------------------------------------- #
def test_a_new_capella_product_is_read(s3):
    capella_product(s3, CAP_SLC)
    rows, stats = pb.supplement("capella", set(), s3, now=NOW)
    assert [r["id"] for r in rows] == [CAP_SLC] and stats["added"] == 1


def test_a_capella_scene_is_known_product_by_product(s3):
    """The catalog collapses a scene's products into one record with a
    shortened id, so the scene's id alone cannot say which products it has.
    Each product is recognised by the folder its file sits in."""
    cap_sicd = CAP_GEC.replace("_GEC_", "_SICD_")
    for product in (CAP_GEC, cap_sicd, CAP_SLC):
        capella_product(s3, product)
    base = "https://capella-open-data.s3.amazonaws.com/data/2026/9/9"
    collapsed = {"properties": {"id": "CAPELLA_C13_SP_HH_20260909150138_20260909150146", "products": {
        "GEC": f"{base}/{CAP_GEC}/{CAP_GEC}.tif", "SICD": f"{base}/{cap_sicd}/{cap_sicd}.ntf"}}}
    known = pb.known_keys("capella", [collapsed])
    assert known == {CAP_GEC, cap_sicd}, "every product the scene has, not just its first"
    rows, _ = pb.supplement("capella", known, s3, now=NOW)
    assert [r["id"] for r in rows] == [CAP_SLC], "the product the catalog lacks, and only that one"


def test_capellas_catalog_files_are_not_scenes(s3):
    s3.put("capella", "stac/capella-open-data-by-datetime/catalog.json", {"type": "Catalog"})
    s3.put("capella", "stac/capella-open-data-by-datetime/capella-open-data-2026/catalog.json", {"type": "Catalog"})
    assert pb.supplement("capella", set(), s3, now=NOW)[1]["listed"] == 0


# --------------------------------------------------------------------------- #
# Failing soft
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("provider", ["umbra", "iceye", "capella"])
def test_a_bucket_that_cannot_be_listed_is_reported_not_raised(provider):
    def down(url, **k):
        raise OSError("connection timed out")
    rows, stats = pb.supplement(provider, set(), down, now=NOW)
    assert rows == []
    assert "timed out" in stats["error"]
    assert stats["listing"] is None, "no listing must mean no link-checking either"


def test_one_unreadable_item_does_not_cost_the_others(s3):
    umbra_scene(s3, "t1", "2026-11-10-04-16-54_UMBRA-09", "good")
    bad = umbra_scene(s3, "t2", "2026-11-10-05-00-00_UMBRA-09", "bad")
    s3.fail = lambda url: OSError("503") if urllib.parse.quote(bad) in url else None
    rows, stats = pb.supplement("umbra", set(), s3, now=NOW)
    assert [r["id"] for r in rows] == ["good"]
    assert stats["added"] == 1 and stats["failed"] == 1


@pytest.mark.parametrize("body", [b"not json", b'{"type": "Feature"}', b'{"geometry": null, "id": "x"}', b"[]"])
def test_an_item_that_is_not_a_usable_stac_item_is_counted_as_failed(s3, body):
    key = umbra_scene(s3, "t1", "2026-11-10-04-16-54_UMBRA-09", "x")
    s3.put("umbra", key, body)
    rows, stats = pb.supplement("umbra", set(), s3, now=NOW)
    assert rows == [] and stats["failed"] == 1


def test_the_number_fetched_in_one_run_is_capped_newest_first(s3):
    """If the aggregator ever stops, every scene a provider has looks new at
    once. The run must stay bounded, and take the newest uploads first."""
    for i in range(6):
        umbra_scene(s3, f"t{i}", f"2026-11-0{i + 1}-00-00-00_UMBRA-09", f"u{i}",
                    modified=f"2026-11-0{i + 1}T00:00:00.000Z")
    rows, stats = pb.supplement("umbra", set(), s3, now=NOW, max_items=2)
    assert stats["missing"] == 6 and stats["truncated"] is True
    assert sorted(r["id"] for r in rows) == ["u4", "u5"]


# --------------------------------------------------------------------------- #
# Product links that point at nothing
# --------------------------------------------------------------------------- #
def umbra_feature(stem_key, formats):
    ext = {"GEC": "_GEC.tif", "CSI": "_CSI.tif", "SICD": "_SICD.nitf", "SIDD": "_SIDD.nitf", "CPHD": "_CPHD.cphd"}
    products = {f: pb.public_url("umbra", stem_key.replace(".stac.v2.json", ext[f])) for f in formats}
    return {"properties": {"id": "u1", "products": products, "download": next(iter(products.values()))}}


def test_links_to_files_that_do_not_exist_are_removed(s3):
    """5,260 of the catalog's 11,904 Umbra CSI links pointed at nothing."""
    key = umbra_scene(s3, "Purdue University Farm Plot", "2025-10-28-15-39-32_UMBRA-05", "u1",
                      products=("GEC", "SICD"), root="tasks")
    f = umbra_feature(key, ["GEC", "CSI", "SICD", "SIDD", "CPHD"])
    listing = {k for k, _ in pb.list_bucket(pb.BUCKETS["umbra"], "sar-data/", s3)}
    assert pb.prune_missing_products("umbra", [f], listing) == 3
    assert set(f["properties"]["products"]) == {"GEC", "SICD"}


def test_the_primary_download_moves_if_it_was_one_of_the_dead_links(s3):
    key = umbra_scene(s3, "t1", "2025-10-28-15-39-32_UMBRA-05", "u1", products=("SICD",))
    f = umbra_feature(key, ["GEC", "SICD"])
    listing = {k for k, _ in pb.list_bucket(pb.BUCKETS["umbra"], "sar-data/", s3)}
    pb.prune_missing_products("umbra", [f], listing)
    assert f["properties"]["download"].endswith("_SICD.nitf")


def test_a_scene_with_no_live_link_at_all_is_left_alone(s3):
    """Blanking a scene's downloads on the strength of one listing is the
    more destructive mistake."""
    key = "sar-data/task-data/gone/2025-01-01-00-00-00_UMBRA-05/2025-01-01-00-00-00_UMBRA-05.stac.v2.json"
    f = umbra_feature(key, ["GEC", "CSI"])
    before = json.dumps(f)
    assert pb.prune_missing_products("umbra", [f], {"sar-data/other.tif"}) == 0
    assert json.dumps(f) == before


def test_a_link_on_another_host_is_never_judged(s3):
    """The listing says nothing about files that live somewhere else. A scene
    with one live Umbra link and one link elsewhere keeps both."""
    key = umbra_scene(s3, "t1", "2025-10-28-15-39-32_UMBRA-05", "u1", products=("GEC",))
    f = umbra_feature(key, ["GEC", "CSI"])
    f["properties"]["products"]["SICD"] = "https://example.org/mirror/a_SICD.nitf"
    listing = {k for k, _ in pb.list_bucket(pb.BUCKETS["umbra"], "sar-data/", s3)}
    assert pb.prune_missing_products("umbra", [f], listing) == 1
    assert set(f["properties"]["products"]) == {"GEC", "SICD"}


@pytest.mark.parametrize("scene_id", [
    "ICEYE_WYPY1N_20260904T145307Z_10445347_X47_SLEDP",
    "ICEYE_6GV56S_20251105T131734Z_6908561_X31_SLF3L",      # digits in the mode suffix
    "ICEYE_BD6KBV_20251110T112927Z_7002051_X25_SLF1L",
    "ICEYE_X90WFU_20260422T113751Z_9547693_X58_SM",
])
def test_every_shape_of_iceye_id_is_recognised(s3, scene_id):
    """A letters-only pattern for the mode suffix skipped 64 real scenes."""
    iceye_folder(s3, scene_id)
    rows, _ = pb.supplement("iceye", set(), s3, now=NOW)
    assert [r["id"] for r in rows] == [scene_id]


def test_the_iceye_pattern_matches_every_id_in_the_committed_catalog():
    """The real ids are the specification. If ICEYE introduces a new shape the
    aggregator will carry it into the catalog, and this will say the folder
    reader cannot see scenes of that shape."""
    catalog = ROOT / "data" / "scenes.geojson"
    if not catalog.exists():
        pytest.skip("catalog not present")
    ids = [f["properties"]["id"] for f in json.loads(catalog.read_text())["features"]
           if f["properties"]["provider"] == "iceye"]
    assert len(ids) > 500
    unmatched = [i for i in ids if not pb.ICEYE_ID.match(i)]
    assert not unmatched, f"{len(unmatched)} ICEYE ids do not match, e.g. {unmatched[:3]}"
