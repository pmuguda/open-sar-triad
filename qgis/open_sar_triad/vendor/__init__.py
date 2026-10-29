"""The open-sar-triad Python client, vendored.

QGIS plugins cannot pip-install anything, so the client ships inside the
plugin. ``client.py`` here is a byte-for-byte copy of
``python/opensartriad/client.py`` in the repository; ``scripts/build_qgis_plugin.py``
refreshes it and the test suite fails if the two drift apart. Edit the
original, never this copy.
"""

from .client import FAMILIES, PROVIDERS, Catalog, OpenSarTriadError, Scene, SceneCollection

__all__ = ["FAMILIES", "PROVIDERS", "Catalog", "OpenSarTriadError", "Scene", "SceneCollection"]
