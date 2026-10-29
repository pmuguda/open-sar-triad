"""QGIS plugin entry point. QGIS imports this package and calls classFactory."""


def classFactory(iface):
    from .plugin import OpenSarTriadPlugin
    return OpenSarTriadPlugin(iface)
