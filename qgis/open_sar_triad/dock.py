"""
The dock panel: filters on top, results and download below.

Built in code rather than from a .ui file so there is nothing to compile and
the whole plugin is readable as Python. The widget talks to QGIS through the
``iface`` object only for three things: the map canvas (for the extent filter),
the message bar, and the project the result layer is added to. Tests pass a
stand-in with those three.
"""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsSettings,
    QgsTask,
)
from qgis.PyQt.QtCore import QDate
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .layer import LAYER_NAME, build_layer
from .records import PROVIDER_LABELS
from .tasks import DownloadTask, SearchTask
from .vendor import FAMILIES, PROVIDERS, Catalog, OpenSarTriadError
from .vendor.client import DEFAULT_BASE_URL

SETTINGS_URL = "open_sar_triad/base_url"
ANY = "Any"


class OpenSarTriadDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("Open SAR Triad", parent)
        self.setObjectName("OpenSarTriadDock")
        self.iface = iface
        self.catalog: Catalog | None = None
        self.scenes = []                 # last search, by position
        self.layer = None                # the result layer currently in the project
        self._task = None                # keep a reference or QGIS garbage-collects it
        self._build()
        self._connect_catalog()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)

        self.status = QLabel("Loading catalog…")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        filters = QGroupBox("Filters")
        form = QFormLayout(filters)

        prov_row = QHBoxLayout()
        self.providers = {}
        for p in PROVIDERS:
            cb = QCheckBox(PROVIDER_LABELS[p])
            cb.setChecked(True)
            self.providers[p] = cb
            prov_row.addWidget(cb)
        form.addRow("Providers", prov_row)

        self.use_dates = QCheckBox("Limit to")
        self.date_from = QDateEdit(QDate(2021, 1, 1))
        self.date_to = QDateEdit(QDate.currentDate())
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.setEnabled(False)
        self.use_dates.toggled.connect(self.date_from.setEnabled)
        self.use_dates.toggled.connect(self.date_to.setEnabled)
        dates = QHBoxLayout()
        dates.addWidget(self.use_dates)
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.date_to)
        form.addRow("Dates", dates)

        self.mode = QComboBox()
        self.polarization = QComboBox()
        self.landuse = QComboBox()
        self.orbit = QComboBox()
        self.orbit.addItems([ANY, "ascending", "descending"])
        self.look = QComboBox()
        self.look.addItems([ANY, "left", "right"])
        self.family = QComboBox()
        self.family.addItems([ANY] + list(FAMILIES))
        for combo in (self.mode, self.polarization, self.landuse):
            combo.addItem(ANY)
        form.addRow("Sensor mode", self.mode)
        form.addRow("Polarization", self.polarization)
        form.addRow("Land use", self.landuse)
        form.addRow("Orbit", self.orbit)
        form.addRow("Look direction", self.look)
        form.addRow("Must publish", self.family)

        self.use_extent = QCheckBox("Only scenes intersecting the map canvas")
        self.use_extent.setChecked(True)
        form.addRow(self.use_extent)
        outer.addWidget(filters)

        self.search_btn = QPushButton("Search")
        self.search_btn.setEnabled(False)
        self.search_btn.clicked.connect(self.search)
        outer.addWidget(self.search_btn)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        outer.addWidget(self.result)

        dl = QGroupBox("Download")
        dl_form = QFormLayout(dl)
        self.dl_family = QComboBox()
        self.dl_family.addItems(list(FAMILIES))
        dl_form.addRow("Product family", self.dl_family)
        self.dl_btn = QPushButton("Download selected scenes…")
        self.dl_btn.setEnabled(False)
        self.dl_btn.clicked.connect(self.download)
        dl_form.addRow(self.dl_btn)
        hint = QLabel("Select footprints in the result layer first. Products go "
                      "to a folder you choose, one subfolder per provider.")
        hint.setWordWrap(True)
        dl_form.addRow(hint)
        outer.addWidget(dl)

        adv = QGroupBox("Catalog")
        adv_form = QFormLayout(adv)
        self.base_url = QLineEdit(QgsSettings().value(SETTINGS_URL, DEFAULT_BASE_URL))
        self.base_url.editingFinished.connect(self._connect_catalog)
        adv_form.addRow("API base URL", self.base_url)
        outer.addWidget(adv)

        outer.addStretch(1)
        self.setWidget(root)

    # -------------------------------------------------------------- catalog
    def _connect_catalog(self) -> None:
        """Point at the API and fill the combos from its stats, off-thread."""
        url = self.base_url.text().strip() or DEFAULT_BASE_URL
        QgsSettings().setValue(SETTINGS_URL, url)
        self.catalog = Catalog(url)
        self.search_btn.setEnabled(False)
        self.status.setText("Loading catalog…")
        catalog = self.catalog

        def load(task):
            return catalog.stats()

        def done(exc, stats=None):
            if exc or not stats:
                self.status.setText(f"Could not reach the catalog at {url}: {exc}")
                return
            self._fill_from_stats(stats)
            self.search_btn.setEnabled(True)

        self._task = QgsTask.fromFunction("Open SAR Triad: reading catalog stats", load, on_finished=done)
        QgsApplication.taskManager().addTask(self._task)

    def _fill_from_stats(self, stats: dict) -> None:
        def refill(combo: QComboBox, values) -> None:
            current = combo.currentText()
            combo.clear()
            combo.addItem(ANY)
            combo.addItems(sorted(values))
            i = combo.findText(current)
            combo.setCurrentIndex(i if i >= 0 else 0)

        refill(self.mode, (stats.get("by_mode") or {}).keys())
        refill(self.polarization, (stats.get("by_polarization") or {}).keys())
        refill(self.landuse, (stats.get("by_landuse") or {}).keys())
        ext = stats.get("temporal_extent") or []
        if len(ext) == 2:
            self.date_from.setDate(QDate.fromString(ext[0], "yyyy-MM-dd"))
            self.date_to.setDate(QDate.fromString(ext[1], "yyyy-MM-dd"))
        by_p = stats.get("by_provider") or {}
        self.status.setText(
            f"{stats.get('total', 0):,} scenes "
            f"({', '.join(f'{PROVIDER_LABELS.get(k, k)} {v:,}' for k, v in sorted(by_p.items()))}), "
            f"newest {ext[1] if len(ext) == 2 else '?'}. {stats.get('license', '')}.")

    # --------------------------------------------------------------- search
    def filters(self) -> dict:
        """The search() keyword arguments the panel currently describes."""
        def pick(combo: QComboBox):
            t = combo.currentText()
            return None if t in (ANY, "") else t

        f = {
            "providers": [p for p, cb in self.providers.items() if cb.isChecked()] or None,
            "mode": pick(self.mode),
            "polarization": pick(self.polarization),
            "landuse": pick(self.landuse),
            "orbit": pick(self.orbit),
            "look": pick(self.look),
            "family": pick(self.family),
        }
        if self.use_dates.isChecked():
            f["start"] = self.date_from.date().toString("yyyy-MM-dd")
            f["end"] = self.date_to.date().toString("yyyy-MM-dd")
        if self.use_extent.isChecked():
            bbox = self.canvas_bbox()
            if bbox:
                f["bbox"] = bbox
        return {k: v for k, v in f.items() if v is not None}

    def canvas_bbox(self):
        """The map canvas extent as (west, south, east, north) in EPSG:4326."""
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        extent = canvas.extent()
        src = canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        if src.isValid() and src != dst:
            extent = QgsCoordinateTransform(src, dst, QgsProject.instance()).transformBoundingBox(extent)
        return (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())

    def search(self) -> None:
        if self.catalog is None:
            return
        self.search_btn.setEnabled(False)
        self.result.setText("Searching…")
        self._task = SearchTask(self.catalog, self.filters(), self._search_done)
        QgsApplication.taskManager().addTask(self._task)

    def _search_done(self, ok: bool, scenes, records, error) -> None:
        self.search_btn.setEnabled(True)
        if not ok:
            self.result.setText(f"Search failed: {error or 'cancelled'}")
            self._message(f"Open SAR Triad: {error or 'search cancelled'}", level=2)
            return
        self.scenes = scenes
        self._replace_layer(records)
        by_p = {}
        for s in scenes:
            by_p[s.provider] = by_p.get(s.provider, 0) + 1
        self.result.setText(
            f"{len(scenes):,} scene(s)"
            + (f": {', '.join(f'{PROVIDER_LABELS[k]} {v:,}' for k, v in sorted(by_p.items()))}" if by_p else "")
            + ". Footprints added as a layer; select some to download.")
        self.dl_btn.setEnabled(bool(scenes))

    def _replace_layer(self, records: list[dict]) -> None:
        project = QgsProject.instance()
        if self.layer is not None and project.mapLayer(self.layer.id()) is not None:
            project.removeMapLayer(self.layer.id())
        self.layer = build_layer(records, LAYER_NAME)
        project.addMapLayer(self.layer)

    # ------------------------------------------------------------- download
    def download(self) -> None:
        if self.layer is None or not self.scenes:
            return
        selected = {f["id"] for f in self.layer.selectedFeatures()}
        if not selected:
            self._message("Select one or more footprints in the result layer first.", level=1)
            return
        chosen = [s for s in self.scenes if s.id in selected]
        family = self.dl_family.currentText()
        try:
            jobs = DownloadTask.plan(chosen, family)
        except OpenSarTriadError as e:
            self._message(f"Could not resolve download URLs: {e}", level=2)
            return
        data_n = sum(1 for j in jobs if j["kind"] == "data")
        if not data_n:
            self._message(f"None of the {len(chosen)} selected scene(s) publishes a "
                          f"{family} product.", level=1)
            return
        answer = QMessageBox.question(
            self, "Download",
            f"{data_n} product file(s) plus {len(jobs) - data_n} metadata file(s) from "
            f"{len(chosen)} scene(s).\n\nSAR products are large; a single file can exceed "
            f"1 GB. Continue?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        dest = QFileDialog.getExistingDirectory(self, "Download into")
        if not dest:
            return
        self.dl_btn.setEnabled(False)
        self._task = DownloadTask(jobs, dest, self._download_done)
        QgsApplication.taskManager().addTask(self._task)

    def _download_done(self, ok: bool, written, failed, dest) -> None:
        self.dl_btn.setEnabled(True)
        if failed:
            self._message(f"{len(written)} file(s) saved under {dest}; {len(failed)} failed: "
                          f"{failed[0]}", level=1)
        elif ok:
            self._message(f"{len(written)} file(s) saved under {dest}.", level=0)
        else:
            self._message(f"Download cancelled; {len(written)} file(s) kept under {dest}.", level=1)

    # -------------------------------------------------------------- helpers
    def _message(self, text: str, level: int = 0) -> None:
        bar = self.iface.messageBar() if self.iface else None
        if bar is not None:
            # A real enum, not the integer: Qt6 builds no longer coerce ints.
            levels = (Qgis.MessageLevel.Info, Qgis.MessageLevel.Warning, Qgis.MessageLevel.Critical)
            bar.pushMessage("Open SAR Triad", text, level=levels[level], duration=8)
        else:
            self.result.setText(text)
