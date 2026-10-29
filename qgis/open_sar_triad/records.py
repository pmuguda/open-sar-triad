"""
Turns a Scene into the flat attribute record a QGIS feature carries.

Kept free of any qgis import on purpose: this is the part of the plugin that
decides what a user sees in the attribute table, and it is tested on plain
Python where the test suite runs, not only inside a QGIS build.
"""

from __future__ import annotations

from .vendor import FAMILIES, Scene

WEB_APP = "https://www.pmuguda.com/open-sar-triad/"

PROVIDER_COLOURS = {"iceye": "#00FF87", "umbra": "#00C9FF", "capella": "#FF6B35"}
PROVIDER_LABELS = {"iceye": "ICEYE", "umbra": "Umbra", "capella": "Capella"}

#: Attribute table columns, in order. Type names are resolved to Qt types in
#: layer.py so this module stays importable without QGIS.
FIELDS = [
    ("id", "string"),
    ("provider", "string"),
    ("date", "string"),
    ("mode", "string"),
    ("orbit", "string"),
    ("look", "string"),
    ("polarization", "string"),
    ("landuse", "string"),
    ("formats", "string"),
    ("incidence_angle", "double"),
    ("off_nadir", "double"),
    ("thumbnail", "string"),
    ("web_url", "string"),
] + [(f"url_{family}", "string") for family in FAMILIES]


def web_url(scene: Scene) -> str:
    """A link into the web app, framed on this scene.

    The app has no per-scene route; it has a shareable hash that pins the
    bounding box, the month window and which providers are shown. Pinning all
    three narrows the map to this scene closely enough to find it.
    """
    if not scene.bbox or len(scene.bbox) != 4:
        return WEB_APP
    w, s, e, n = scene.bbox
    month = (scene.date or "")[:7]
    hide = ",".join(p for p in PROVIDER_COLOURS if p != scene.provider)
    parts = [f"bbox={w:.3f},{s:.3f},{e:.3f},{n:.3f}"]
    if month:
        parts.append(f"from={month}&to={month}")
    parts.append(f"hide={hide}")
    parts.append(f"lat={(s + n) / 2:.2f}&lng={(w + e) / 2:.2f}&z=11")
    return WEB_APP + "#" + "&".join(parts)


def scene_record(scene: Scene) -> dict:
    """Everything the feature carries, plus its GeoJSON geometry.

    Reads the scene's full STAC record, which the client fetches per provider
    on first use. Call this off the GUI thread for anything but a handful of
    scenes: the Umbra file is several megabytes.
    """
    props = scene.properties
    assets = scene.assets
    rec = {
        "id": scene.id,
        "provider": scene.provider,
        "date": scene.date,
        "mode": scene.mode,
        "orbit": scene.orbit,
        "look": scene.look,
        "polarization": ", ".join(scene.pol or []),
        "landuse": ", ".join(scene.landuse or []),
        "formats": ", ".join(scene.formats or []),
        "incidence_angle": props.get("view:incidence_angle"),
        "off_nadir": props.get("view:off_nadir"),
        "thumbnail": (assets.get("thumbnail") or {}).get("href"),
        "web_url": web_url(scene),
    }
    for family in FAMILIES:
        fmt = scene.resolve(family)
        rec[f"url_{family}"] = scene.url(fmt) if fmt else None
    rec["geometry"] = scene.geometry
    return rec
