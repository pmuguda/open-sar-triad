"""Registers the toolbar button and menu entry, and owns the dock."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:                                    # Qt6 (QGIS 4) moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:                     # Qt5 (QGIS 3) has it in QtWidgets
    from qgis.PyQt.QtWidgets import QAction

from .dock import OpenSarTriadDock

MENU = "&Open SAR Triad"


class OpenSarTriadPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock: OpenSarTriadDock | None = None
        self.action: QAction | None = None

    def initGui(self) -> None:
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Open SAR Triad", self.iface.mainWindow())
        self.action.setToolTip("Search and download open SAR scenes from ICEYE, Umbra and Capella")
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToWebMenu(MENU, self.action)

    def unload(self) -> None:
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginWebMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def toggle(self, checked: bool) -> None:
        if self.dock is None:
            self.dock = OpenSarTriadDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.setVisible(checked)
