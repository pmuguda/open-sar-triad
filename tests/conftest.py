"""Shared fixtures. Tests build tiny synthetic catalogs rather than loading the
real 28 MB file, so the suite runs in under a second."""

import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def make_scene(sid, provider="umbra", date="2025-06-01", mode="spotlight",
               lon=10.0, lat=50.0, products=None, first_seen="2025-06-02"):
    """One well-formed feature, shaped exactly like fetch_catalog.py emits."""
    host = {
        "umbra": "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com",
        "iceye": "https://iceye-open-data-catalog.s3.amazonaws.com",
        "capella": "https://capella-open-data.s3.amazonaws.com",
    }[provider]
    if products is None:
        products = {"GEC": f"{host}/d/{sid}_GEC.tif", "SICD": f"{host}/d/{sid}_SICD.nitf"}
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon, lat], [lon + 0.1, lat], [lon + 0.1, lat + 0.1],
            [lon, lat + 0.1], [lon, lat],
        ]]},
        "properties": {
            "id": sid, "provider": provider, "provider_label": provider.title(),
            "color": "#00C9FF", "date": date, "year": int(date[:4]),
            "sensor_mode": mode, "polarization": "VV", "orbit_state": "ascending",
            "look_dir": "right", "first_seen": first_seen,
            "products": products, "download": next(iter(products.values())),
        },
    }


def make_catalog(n_per_provider=4):
    feats = []
    for provider in ("iceye", "umbra", "capella"):
        for i in range(n_per_provider):
            feats.append(make_scene(f"{provider}-scene-{i:03d}", provider=provider,
                                    lon=10.0 + i, lat=50.0))
    return {
        "type": "FeatureCollection",
        "generated_at": "2026-09-21T08:00:00+00:00",
        "features": feats,
    }


@pytest.fixture
def catalog():
    return make_catalog()


@pytest.fixture
def write_catalog(tmp_path):
    """Write a catalog dict to disk and return its path."""
    def _write(doc, name="scenes.geojson"):
        p = tmp_path / name
        p.write_text(json.dumps(doc))
        return p
    return _write


@pytest.fixture
def mutate():
    """Deep-copy a catalog and apply a mutation, so cases never share state."""
    def _mutate(doc, fn):
        c = copy.deepcopy(doc)
        fn(c)
        return c
    return _mutate
