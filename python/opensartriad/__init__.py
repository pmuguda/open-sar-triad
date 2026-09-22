"""
open-sar-triad: Python client for the open SAR scene catalog.

Discover and download open Synthetic Aperture Radar scenes from ICEYE, Umbra
and Capella through one interface.

    >>> from opensartriad import Catalog
    >>> cat = Catalog()
    >>> scenes = cat.search(bbox=(5.9, 47.2, 10.5, 55.1), start="2025-01-01")
    >>> scenes.download("data/", family="complex", dry_run=True)

Scene metadata is CC-BY 4.0; imagery is downloaded directly from each
provider's own storage and remains subject to that provider's terms. Call
``Catalog().license()`` for the full attribution notice.
"""

from .client import (
    FAMILIES,
    PROVIDERS,
    Catalog,
    OpenSarTriadError,
    Scene,
    SceneCollection,
)

__version__ = "1.0.0"
__all__ = [
    "Catalog",
    "Scene",
    "SceneCollection",
    "OpenSarTriadError",
    "FAMILIES",
    "PROVIDERS",
    "__version__",
]
