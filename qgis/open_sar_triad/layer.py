"""Builds the footprint layer from scene records. Needs qgis.core."""

from __future__ import annotations

import json

from qgis.core import (
    Qgis,
    QgsAction,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from .records import FIELDS, PROVIDER_COLOURS, PROVIDER_LABELS

LAYER_NAME = "Open SAR Triad scenes"

# Field types. QGIS 3.38 moved QgsField from QVariant.Type to QMetaType.Type,
# and the Qt6 builds (QGIS 4) have no unscoped QVariant.String at all, so the
# type is chosen by the running version rather than written once.
if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    _QT_TYPES = {"string": QMetaType.Type.QString, "double": QMetaType.Type.Double}
else:
    from qgis.PyQt.QtCore import QVariant
    _QT_TYPES = {"string": QVariant.Type.String, "double": QVariant.Type.Double}


def _fields() -> QgsFields:
    fields = QgsFields()
    for name, kind in FIELDS:
        fields.append(QgsField(name, _QT_TYPES[kind]))
    return fields


def _renderer() -> QgsCategorizedSymbolRenderer:
    """One colour per provider, matching the web app, so the two agree."""
    categories = []
    for provider, colour in PROVIDER_COLOURS.items():
        fill = QColor(colour)
        fill.setAlpha(40)
        symbol = QgsFillSymbol.createSimple({
            "color": fill.name(QColor.NameFormat.HexArgb),
            "outline_color": colour,
            "outline_width": "0.5",
        })
        categories.append(QgsRendererCategory(provider, symbol, PROVIDER_LABELS[provider]))
    return QgsCategorizedSymbolRenderer("provider", categories)


def _actions(layer: QgsVectorLayer) -> None:
    """Right-click actions on a feature: open it in the web app or its thumbnail."""
    mgr = layer.actions()
    for title, field in (("Open in the web app", "web_url"), ("Open thumbnail", "thumbnail")):
        action = QgsAction(QgsAction.ActionType.OpenUrl, title, f'[% "{field}" %]',
                           "", False, "", {"Feature", "Canvas"})
        mgr.addAction(action)


def build_layer(records: list[dict], name: str = LAYER_NAME) -> QgsVectorLayer:
    """A memory layer in EPSG:4326 with one polygon per scene.

    Memory rather than a file on disk because the result set is a query, not a
    dataset: the next search replaces it. Anyone who wants to keep it can
    export the layer, which QGIS already does well.
    """
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(list(_fields()))
    layer.updateFields()

    feats = []
    for rec in records:
        geom = rec.get("geometry")
        if not geom:
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(_geojson_to_wkt(geom)))
        f.setAttributes([rec.get(name) for name, _ in FIELDS])
        feats.append(f)
    provider.addFeatures(feats)
    layer.updateExtents()

    layer.setRenderer(_renderer())
    _actions(layer)
    return layer


def _geojson_to_wkt(geom: dict) -> str:
    """Polygon or MultiPolygon GeoJSON to WKT, without a geometry library."""
    def ring(coords):
        return "(" + ", ".join(f"{x} {y}" for x, y, *_ in coords) + ")"

    def polygon(rings):
        return "(" + ", ".join(ring(r) for r in rings) + ")"

    t = geom.get("type")
    if t == "Polygon":
        return "POLYGON " + polygon(geom["coordinates"])
    if t == "MultiPolygon":
        return "MULTIPOLYGON (" + ", ".join(polygon(p) for p in geom["coordinates"]) + ")"
    raise ValueError(f"unsupported geometry {t!r}: {json.dumps(geom)[:80]}")
