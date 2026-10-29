"""
Background work. Everything that touches the network runs in a QgsTask so the
QGIS window never freezes on a slow connection: the index is 2.5 MB, a
provider's item file up to a few MB more, and a single SAR product can be over
a gigabyte.

``run`` executes on a worker thread and must not touch Qt widgets. ``finished``
is called back on the GUI thread with the result, which is where the layer is
built and the message bar updated.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from qgis.core import QgsTask

from .records import scene_record
from .vendor import Catalog, SceneCollection
from .vendor.client import USER_AGENT


class SearchTask(QgsTask):
    """Run a catalog search and resolve each hit to a full record."""

    def __init__(self, catalog: Catalog, filters: dict, on_done):
        super().__init__("Open SAR Triad: searching the catalog", QgsTask.CanCancel)
        self.catalog, self.filters, self.on_done = catalog, filters, on_done
        self.scenes = []
        self.records: list[dict] = []
        self.error: str | None = None

    def run(self) -> bool:
        try:
            self.scenes = list(self.catalog.search(**self.filters))
            n = len(self.scenes)
            for i, scene in enumerate(self.scenes):
                if self.isCanceled():
                    return False
                self.records.append(scene_record(scene))
                if n and i % 50 == 0:
                    self.setProgress(100.0 * i / n)
            return True
        except Exception as e:                      # surfaced in finished()
            self.error = str(e)
            return False

    def finished(self, ok: bool) -> None:
        self.on_done(ok, self.scenes, self.records, self.error)


class DownloadTask(QgsTask):
    """Download resolved product URLs into ``dest/<provider>/``.

    Mirrors SceneCollection.download(), reimplemented here because that
    method prints progress and cannot be cancelled; a plugin needs both to go
    through QGIS instead.
    """

    def __init__(self, jobs: list[dict], dest: Path, on_done):
        super().__init__(f"Open SAR Triad: downloading {len(jobs)} file(s)", QgsTask.CanCancel)
        self.jobs, self.dest, self.on_done = jobs, Path(dest), on_done
        self.written: list[Path] = []
        self.failed: list[str] = []

    @staticmethod
    def plan(scenes, family: str) -> list[dict]:
        """What would be fetched. Exposed so the UI can show the count first."""
        return SceneCollection(scenes).download_urls(family=family)

    def run(self) -> bool:
        for i, job in enumerate(self.jobs):
            if self.isCanceled():
                return False
            out_dir = self.dest / job["provider"]
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / job["url"].rsplit("/", 1)[-1].split("?")[0]
            if out.exists() and out.stat().st_size > 0:
                self.written.append(out)
            else:
                tmp = out.with_suffix(out.suffix + ".part")
                try:
                    req = urllib.request.Request(job["url"], headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
                        shutil.copyfileobj(r, fh, length=1024 * 256)
                    tmp.replace(out)
                    self.written.append(out)
                except Exception as e:              # one bad asset must not stop the batch
                    tmp.unlink(missing_ok=True)
                    self.failed.append(f"{out.name}: {e}")
            self.setProgress(100.0 * (i + 1) / len(self.jobs))
        return True

    def finished(self, ok: bool) -> None:
        self.on_done(ok, self.written, self.failed, self.dest)
