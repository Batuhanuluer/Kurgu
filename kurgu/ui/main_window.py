"""Ana pencere: paneller, menüler, kısayollar, proje işlemleri."""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import QSettings, QThread, Qt, Signal, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QImage, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QInputDialog,
                               QMainWindow, QMessageBox, QProgressDialog, QSpinBox, QLineEdit,
                               QPushButton, QHBoxLayout, QWidget, QSplitter, QApplication,
                               QLabel)

from .. import __version__
from ..i18n import _
from ..media import probe, thumbnail
from ..model import History, MediaItem, Project, new_id
from ..render import export
from .. import effects as fx
from .. import autosave, colormatch, updater
from .. import keyframes as kf
from ..rendercache import FrameCache, FrameSig, RenderWorker, heavy_frames, heavy_intervals
from .. import transitions
from .effects_panel import EffectsPanel
from .panels import Inspector, MediaBin
from .player import Player, ProgramMonitor
from .timeline import TimelinePanel

MEDIA_FILTER = ("*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mts *.m2ts *.mpg *.mpeg *.wmv *.flv "
                "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.opus *.png *.jpg *.jpeg *.bmp *.gif "
                "*.webp *.tif *.tiff")


class ExportWorker(QThread):
    progress = Signal(int)
    finished_ok = Signal(bool, str)

    def __init__(self, project: Project, path: str, opts: dict):
        super().__init__()
        self.project = Project.from_dict(project.to_dict())  # iş parçacığına kopya
        self.path, self.opts = path, opts
        self.cancel = False

    def run(self):
        from ..exporter import export_media
        try:
            ok = export_media(self.project, self.path,
                              progress=lambda f: self.progress.emit(int(f * 1000)),
                              cancelled=lambda: self.cancel, **self.opts)
            self.finished_ok.emit(ok, "" if ok else "cancelled")
        except Exception as e:  # noqa: BLE001
            if os.path.exists(self.path):
                try:
                    os.remove(self.path)
                except OSError:
                    pass
            self.finished_ok.emit(False, str(e))


class ExportDialog(QDialog):
    """Premiere "Dışa Aktar": hazır ayar, biçim, boyut, kalite, aralık."""

    def __init__(self, parent, project: Project, default_path: str, settings=None):
        from .. import exporter as ex
        super().__init__(parent)
        self.ex = ex
        self.setWindowTitle(_("Export Video"))
        self.setMinimumWidth(520)
        self.project = project
        self.settings = settings
        form = QFormLayout(self)
        self.preset = QComboBox()
        for pr in ex.PRESETS:
            if ex.available(ex.FORMATS[pr.fmt]):
                self.preset.addItem(_(pr.name), pr.key)
        self.preset.addItem(_("Custom"), "custom")
        form.addRow(_("Preset"), self.preset)
        self.fmt = QComboBox()
        for f in ex.FORMATS.values():
            if ex.available(f):
                self.fmt.addItem(_(f.name), f.key)
        form.addRow(_("Format"), self.fmt)
        self.size = QComboBox()
        pw, ph = project.width, project.height
        self.sizes = [(None, None, _("Match sequence") + f" ({pw} × {ph})")]
        for w, h in ((3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (1080, 1920),
                     (1080, 1350), (1080, 1080), (854, 480), (480, None)):
            label = f"{w} × {h}" if h else f"{w} px " + _("wide")
            self.sizes.append((w, h, label))
        for _w, _h, label in self.sizes:
            self.size.addItem(label)
        form.addRow(_("Resolution"), self.size)
        self.quality = QComboBox()
        for k, label in (("high", _("High")), ("medium", _("Medium")), ("small", _("Small file"))):
            self.quality.addItem(label, k)
        form.addRow(_("Quality"), self.quality)
        self.range = QComboBox()
        self.range.addItem(_("Entire sequence"), "all")
        if project.seq_in is not None or project.seq_out is not None:
            a, b = project.in_out_range()
            from .timeline import timecode
            self.range.addItem(_("Sequence In/Out") + f"  ({timecode(a, project.fps)} – "
                               f"{timecode(b, project.fps)})", "io")
            self.range.setCurrentIndex(1)
        form.addRow(_("Range"), self.range)
        from PySide6.QtWidgets import QCheckBox
        self.gpu = QCheckBox(_("Use GPU encoder (faster)"))
        self.gpu.setChecked(bool(settings.value("export_gpu", True, type=bool)) if settings
                            else True)
        form.addRow(self.gpu)
        self.path = QLineEdit(default_path)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.path, 1)
        rl.addWidget(browse)
        form.addRow(_("File"), row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#9aa0a6;")
        form.addRow(self.summary)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_("Export"))
        bb.button(QDialogButtonBox.Cancel).setText(_("Cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._applying = False
        self.preset.currentIndexChanged.connect(self._preset_changed)
        for w in (self.fmt, self.size, self.quality, self.range):
            w.currentIndexChanged.connect(self._custom)
        last = settings.value("export_preset", "match") if settings else "match"
        self.preset.setCurrentIndex(max(0, self.preset.findData(last)))
        self._preset_changed()

    def _preset_changed(self, *_a):
        key = self.preset.currentData()
        pr = next((p for p in self.ex.PRESETS if p.key == key), None)
        if pr is None:
            return
        self._applying = True
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(pr.fmt)))
        idx = next((i for i, (w, h, _l) in enumerate(self.sizes)
                    if (w, h) == (pr.width, pr.height)), 0)
        self.size.setCurrentIndex(idx)
        self.quality.setCurrentIndex(max(0, self.quality.findData(pr.quality)))
        self._applying = False
        self._update()

    def _custom(self, *_a):
        if not self._applying:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData("custom"))
            self.preset.blockSignals(False)
        self._update()

    def _update(self):
        f = self.ex.FORMATS[self.fmt.currentData()]
        base, _e = os.path.splitext(self.path.text())
        self.path.setText(base + "." + f.ext)
        o = self.values()[1]
        w, h = self.ex.target_size(self.project, None, o["width"], o["height"])
        a, b = (o["start"], o["end"])
        from .timeline import timecode
        txt = f"{_(f.name)} · {w} × {h} · {timecode(b - a, self.project.fps)}"
        if f.deep:
            txt += " · " + _("10-bit color pipeline")
        if (w / h > 0) and abs(w / h - self.project.width / self.project.height) > 0.01 \
                and f.vcodec:
            txt += "\n" + _("Aspect ratio differs from the sequence: black bars are added. "
                            "For a vertical video, set the sequence to 9:16 instead.")
        self.summary.setText(txt)
        quality_ok = f.crf
        self.quality.setEnabled(quality_ok or f.key == "mp3")
        from .. import hw
        enc = hw.hw_encoder(f.key) if f.key in ("h264", "hevc") else None
        self.gpu.setVisible(enc is not None)
        if enc:
            self.gpu.setToolTip(enc)
        self.size.setEnabled(f.vcodec is not None)

    def _browse(self):
        f = self.ex.FORMATS[self.fmt.currentData()]
        p, _f = QFileDialog.getSaveFileName(self, _("Export Video"), self.path.text(),
                                            f"{_(f.name)} (*.{f.ext})")
        if p:
            self.path.setText(p if p.lower().endswith("." + f.ext) else p + "." + f.ext)

    def values(self):
        w, h, _l = self.sizes[self.size.currentIndex()]
        f = self.ex.FORMATS[self.fmt.currentData()]
        pr = next((p for p in self.ex.PRESETS if p.key == self.preset.currentData()), None)
        if self.range.currentData() == "io":
            start, end = self.project.in_out_range()
        else:
            start, end = 0.0, self.project.duration
        if self.settings is not None:
            self.settings.setValue("export_preset", self.preset.currentData())
            self.settings.setValue("export_gpu", self.gpu.isChecked())
        return self.path.text(), {
            "hardware": self.gpu.isChecked() and self.gpu.isVisible(),
            "fmt_key": f.key, "width": w, "height": h, "quality": self.quality.currentData(),
            "start": start, "end": end,
            "max_fps": pr.max_fps if pr else (15 if f.key == "gif" else None)}


class SettingsDialog(QDialog):
    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.setWindowTitle(_("Project Settings…").rstrip("…"))
        form = QFormLayout(self)
        self.preset = QComboBox()
        self.presets = [("1920 × 1080 (16:9)", 1920, 1080), ("3840 × 2160 4K", 3840, 2160),
                        ("1280 × 720", 1280, 720), ("1080 × 1920 (9:16 Reels/Shorts)", 1080, 1920),
                        ("1080 × 1080 (1:1)", 1080, 1080)]
        self.preset.addItem("—")
        for name, *_r in self.presets:
            self.preset.addItem(name)
        self.preset.currentIndexChanged.connect(self._preset)
        form.addRow("Preset", self.preset)
        self.w = QSpinBox()
        self.w.setRange(16, 8192)
        self.w.setSingleStep(2)
        self.w.setValue(project.width)
        self.h = QSpinBox()
        self.h.setRange(16, 8192)
        self.h.setSingleStep(2)
        self.h.setValue(project.height)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setDecimals(3)
        self.fps.setValue(project.fps)
        form.addRow(_("Width"), self.w)
        form.addRow(_("Height"), self.h)
        form.addRow(_("Frame rate"), self.fps)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _preset(self, i):
        if i > 0:
            _n, w, h = self.presets[i - 1]
            self.w.setValue(w)
            self.h.setValue(h)


class MainWindow(QMainWindow):
    def __init__(self, path: str | None = None):
        super().__init__()
        self.project = Project()
        self.history = History()
        self.thumbnails: dict[str, QImage] = {}
        self.dirty = False
        self.settings = QSettings("Kurgu", "Kurgu")
        # Render önbelleği ve arka plan işçisi
        self.version = 0
        from .. import autosave as _as
        from .. import hw as _hw
        _hw.MODE = str(self.settings.value("hw_decode", "auto"))
        disk_gb = float(self.settings.value("render_disk_gb", 10))
        self.cache = FrameCache(int(self.settings.value("cache_mb", 1024)),
                                os.path.join(_as.APP_DIR, "render") if disk_gb > 0 else None,
                                int(disk_gb * 1024))
        self.worker = RenderWorker(self.cache)
        self._sig = None
        self._sig_ver = -1
        self._snap = None
        self._snap_ver = -1
        self._prefetch = None
        self._render_status = (None, [])
        self.use_proxy = self.settings.value("use_proxy", True, type=bool)
        self.proxy_progress: dict[str, float] = {}
        from .proxyman import ProxyManager
        self.proxies = ProxyManager(self)
        self.proxies.progress.connect(self._proxy_progress)
        self.proxies.finished.connect(self._proxy_finished)
        self.player = Player(self)
        self.timeline_panel = TimelinePanel(self)
        self.timeline = self.timeline_panel.timeline
        self.monitor = ProgramMonitor(self)
        self.source_marks: dict[str, tuple] = {}  # medya kimliği -> (giriş, çıkış)
        from .source import SourceMonitor
        self.source = SourceMonitor(self)
        self.monitors = QSplitter(Qt.Horizontal)
        self.monitors.addWidget(self.source)
        self.monitors.addWidget(self.monitor)
        self.monitors.setStretchFactor(0, 1)
        self.monitors.setStretchFactor(1, 1)
        self.monitors.setChildrenCollapsible(True)
        self.monitors.setHandleWidth(4)
        self.bin = MediaBin(self)
        self.inspector = Inspector(self)
        self.setCentralWidget(self.monitors)
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)
        self.effects_panel = EffectsPanel(self)
        bin_dock = self._dock(_("Project"), self.bin, Qt.LeftDockWidgetArea)
        fx_dock = self._dock(_("Effects"), self.effects_panel, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(bin_dock, fx_dock)
        bin_dock.raise_()
        self.clipboard: list = []
        self.color_ref = None  # "Renk referansı olarak kullan" ile kopyalanan palet
        self.inspector_dock = self._dock(_("Inspector"), self.inspector, Qt.RightDockWidgetArea)
        self.inspector.setMinimumWidth(380)
        from .color_panel import ColorPanel
        from .scopes import ScopesPanel
        self.color_panel = ColorPanel(self)
        self.color_dock = self._dock(_("Lumetri Color"), self.color_panel, Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.inspector_dock, self.color_dock)
        self.inspector_dock.raise_()
        self._inspector_stale = False
        self.inspector_dock.visibilityChanged.connect(self._inspector_shown)
        self.color_dock.visibilityChanged.connect(
            lambda vis: vis and self.color_panel.refresh())
        from .captions_panel import CaptionsPanel
        self.captions_panel = CaptionsPanel(self)
        self.captions_dock = self._dock(_("Captions"), self.captions_panel,
                                        Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.color_dock, self.captions_dock)
        self.captions_dock.visibilityChanged.connect(
            lambda vis: vis and self.captions_panel.refresh())
        self.inspector_dock.raise_()
        self.scopes = ScopesPanel(self)
        self.scopes_dock = self._dock(_("Lumetri Scopes"), self.scopes, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(fx_dock, self.scopes_dock)
        from .mixer import MixerPanel
        self.mixer = MixerPanel(self)
        self.mixer_dock = self._dock(_("Audio Track Mixer"), self.mixer, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.scopes_dock, self.mixer_dock)
        self.mixer_dock.visibilityChanged.connect(lambda vis: vis and self.mixer.rebuild())
        bin_dock.raise_()
        self.bin.setMinimumWidth(240)
        tl_dock = self._dock(_("Timeline"), self.timeline_panel, Qt.BottomDockWidgetArea)
        self.resizeDocks([tl_dock], [340], Qt.Vertical)
        self.player.position.connect(self._on_play_position)
        self.timeline.playhead_changed.connect(self._on_scrub)
        self.timeline.selection_changed.connect(self._on_selection)
        self.timeline.edited.connect(lambda: self.on_edited())
        self._build_menus()
        # Enter = Render; yalnızca zaman çizelgesi / monitör odaktayken (metin kutularını bozmasın)
        from PySide6.QtGui import QShortcut
        for wdg in (self.timeline_panel, self.monitor):
            for k in ("Return", "Enter"):
                sc = QShortcut(QKeySequence(k), wdg)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(self.render_effects)
        self.resize(1440, 900)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        # Otomatik kaydetme ve çökme kurtarma (proje açılmadan önce hazır olmalı)
        self.session = autosave.Session()
        crashed = self.session.crashed_sessions()
        self.session.start()
        if path:
            self.open_project(path)
        from PySide6.QtCore import QTimer
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.do_autosave)
        self.set_autosave_interval(int(self.settings.value("autosave_min", 2)))
        from .. import app as _app
        _app.EMERGENCY.append(self.emergency_save)
        if crashed:
            QTimer.singleShot(300, lambda: self.offer_recovery(crashed))
        # Güncelleme denetimi (günde en fazla bir kez, arka planda)
        self._update_btn = None
        import time as _t
        if self.settings.value("check_updates", True, type=bool) and \
                _t.time() - float(self.settings.value("last_update_check", 0)) > 20 * 3600:
            QTimer.singleShot(4000, lambda: self.check_updates(manual=False))
        self._update_title()
        self.statusBar().showMessage(_("Ready"))
        self.monitor.refresh(0.0)

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_monitors_sized", False):
            self._monitors_sized = True
            from PySide6.QtCore import QTimer

            def even():  # iki monitör eşit genişlikte açılsın (kayıtlı düzen yoksa)
                saved = self.settings.value("monitor_split")
                if saved:
                    self.monitors.restoreState(saved)
                else:
                    w = self.monitors.width()
                    self.monitors.setSizes([w // 2, w - w // 2])
            QTimer.singleShot(0, even)

    # ------------------------------------------------------------ kurulum
    def _dock(self, title, widget, area):
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, list)
                                                       else [shortcut])])
        a.setCheckable(checkable)
        if checkable:
            a.triggered.connect(slot)
        else:  # Qt "checked" (False) değerini isteğe bağlı ilk parametreye göndermesin
            a.triggered.connect(lambda _checked=False, f=slot: f())
        if menu is not None:
            menu.addAction(a)
        else:
            self.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu(_("File"))
        self._act(f, _("New Project"), self.new_project, QKeySequence.New)
        self._act(f, _("Open Project…"), self.open_dialog, QKeySequence.Open)
        self._act(f, _("Save"), self.save, QKeySequence.Save)
        self._act(f, _("Save As…"), self.save_as, QKeySequence.SaveAs)
        f.addSeparator()
        self._act(f, _("Import Media…"), self.import_dialog, "Ctrl+I")
        self._act(f, _("Export…"), self.export_dialog, "Ctrl+M")
        self._act(f, _("Export Frame…"), self.export_frame, "Ctrl+Shift+E")
        f.addSeparator()
        am = f.addMenu(_("Auto Save"))
        grp = QActionGroup(self)
        current = int(self.settings.value("autosave_min", 2))
        for mins in (0, 1, 2, 5, 10):
            label = _("Off") if mins == 0 else _("Every {n} min").format(n=mins)
            a = am.addAction(label)
            a.setCheckable(True)
            a.setChecked(mins == current)
            a.triggered.connect(lambda _c=False, m=mins: self.set_autosave_interval(m))
            grp.addAction(a)
        am.addSeparator()
        am.addAction(_("Open Auto-Saved Project…"), self.open_autosave_dialog)
        f.addSeparator()
        self._act(f, _("Quit"), self.close, QKeySequence.Quit)

        e = mb.addMenu(_("Edit"))
        self._act(e, _("Undo"), self.undo, QKeySequence.Undo)
        self._act(e, _("Redo"), self.redo, [QKeySequence.Redo, "Ctrl+Shift+Z"])
        e.addSeparator()
        self._act(e, _("Split at Playhead"), self.split_at_playhead, "Ctrl+K")
        self._act(e, _("Delete"), self.delete_selected, [QKeySequence.Delete, "Backspace"])
        self._act(e, _("Ripple Delete"), lambda: self.delete_selected(ripple=True),
                  "Shift+Delete")
        self._act(e, _("Copy"), self.copy_selected, QKeySequence.Copy)
        self._act(e, _("Cut"), self.cut_selected, QKeySequence.Cut)
        self._act(e, _("Paste"), self.paste, QKeySequence.Paste)
        self._act(e, _("Paste Attributes"), self.paste_attributes, "Ctrl+Alt+V")
        self._act(e, _("Duplicate"), self.duplicate_selected, "Ctrl+Shift+D")
        self._act(e, _("Select All"), self.select_all, QKeySequence.SelectAll)
        e.addSeparator()
        self._act(e, _("Preferences…"), self.preferences, "Ctrl+,")

        s = mb.addMenu(_("Sequence"))
        self._act(s, _("Add Title"), self.add_title, "Ctrl+T")
        self._act(s, _("Add Marker"), self.add_marker, "M")
        self._act(s, _("Apply Default Transition"), self.apply_default_transition, "Ctrl+D")
        s.addSeparator()
        self._act(s, _("Normalize Loudness…"), self.normalize_dialog)
        s.addSeparator()
        self._act(s, _("Render Effects") + "\tEnter", self.render_effects)
        self._act(s, _("Delete Render Files"), self.clear_render_cache)
        s.addSeparator()
        self._act(s, _("Next keyframe"), lambda: self._jump_any_key(1), "Shift+K")
        self._act(s, _("Previous keyframe"), lambda: self._jump_any_key(-1), "Ctrl+Shift+K")
        s.addSeparator()
        self._act(s, _("Add Video Track"), lambda: self.add_track("video"))
        self._act(s, _("Add Audio Track"), lambda: self.add_track("audio"))
        s.addSeparator()
        self._act(s, _("Project Settings…"), self.project_settings)

        v = mb.addMenu(_("View"))
        tools = QActionGroup(self)
        from .timeline import TOOLS
        self.tool_actions = {}
        for key, sc, _icon, name in TOOLS:
            a = self._act(v, _(name), lambda _c=False, k=key: self.set_tool(k), sc, True)
            tools.addAction(a)
            self.tool_actions[key] = a
        self.select_tool = self.tool_actions["select"]
        self.razor_tool = self.tool_actions["razor"]
        self.select_tool.setChecked(True)
        v.addSeparator()
        ap = self._act(v, _("Auto-create proxies for heavy videos"),
                       lambda on: self.settings.setValue("auto_proxy", on), checkable=True)
        ap.setChecked(self.settings.value("auto_proxy", True, type=bool))
        snap = self._act(v, _("Snapping"), self.toggle_snap, "S", True)
        snap.setChecked(True)
        v.addSeparator()
        self._act(v, _("Lumetri Color"), lambda: self.show_dock(self.color_dock), "Shift+6")
        self._act(v, _("Lumetri Scopes"), lambda: self.show_dock(self.scopes_dock), "Shift+7")
        self._act(v, _("Color Workspace"), self.color_workspace)
        self._act(v, _("Captions"), lambda: self.show_dock(self.captions_dock))
        self._act(v, _("Audio Track Mixer"), lambda: self.show_dock(self.mixer_dock), "Shift+9")
        self.mc_view_action = self._act(v, _("Multi-Camera View"),
                                        lambda on: self.toggle_multicam_view(on), "Shift+0",
                                        checkable=True)
        v.addSeparator()
        self._act(v, _("Zoom In"), lambda: self.timeline.zoom(1.4), ["=", "+"])
        self._act(v, _("Zoom Out"), lambda: self.timeline.zoom(1 / 1.4), "-")
        self._act(v, _("Zoom to Fit"), self.timeline.zoom_fit, "\\")

        h = mb.addMenu(_("Help"))
        self._act(h, _("Keyboard Shortcuts"), self.show_shortcuts, "F1")
        h.addSeparator()
        self._act(h, _("Check for Updates…"), lambda: self.check_updates(manual=True))
        auto = self._act(h, _("Check for updates at startup"),
                         lambda on: self.settings.setValue("check_updates", on), checkable=True)
        auto.setChecked(self.settings.value("check_updates", True, type=bool))
        h.addSeparator()
        self._act(h, _("About Kurgu"), self.about)

        # Menüde görünmeyen oynatma kısayolları (kaynak monitörü odaktaysa ona gider)
        self._act(None, "play", lambda: self._player().toggle(), "Space")
        self._act(None, "j", lambda: self._player().shuttle(-1), "J")
        self._act(None, "k", lambda: self._player().shuttle(0), "K")
        self._act(None, "l", lambda: self._player().shuttle(1), "L")
        self._act(None, "prev", lambda: self.step_frames(-1), "Left")
        self._act(None, "next", lambda: self.step_frames(1), "Right")
        self._act(None, "prev10", lambda: self.step_frames(-10), "Shift+Left")
        self._act(None, "next10", lambda: self.step_frames(10), "Shift+Right")
        self._act(None, "home", self.go_start, "Home")
        self._act(None, "end", self.go_end, "End")
        self._act(None, "mark-in", self.mark_in, "I")
        self._act(None, "mark-out", self.mark_out, "O")
        self._act(None, "clear-io", self.clear_marks, "Ctrl+Shift+X")
        self._act(None, "insert", lambda: self.source.edit("insert"), ",")
        self._act(None, "esc", self.monitor.cancel_pen, "Escape")
        for n in range(1, 10):  # çoklu kamera: 1-9 ile kameraya kes
            self._act(None, f"cam{n}", lambda _c=False, k=n - 1: self.multicam_key(k), str(n))
        self._act(None, "overwrite", lambda: self.source.edit("overwrite"), ".")
        self._act(None, "up", lambda: self.jump_edit(-1), "Up")
        self._act(None, "nudge-1", lambda: self.nudge_selected(-1), "Alt+Left")
        self._act(None, "nudge+1", lambda: self.nudge_selected(1), "Alt+Right")
        self._act(None, "nudge-5", lambda: self.nudge_selected(-5), "Alt+Shift+Left")
        self._act(None, "nudge+5", lambda: self.nudge_selected(5), "Alt+Shift+Right")
        self._act(None, "down", lambda: self.jump_edit(1), "Down")
        for a in self.actions():
            a.setShortcutContext(Qt.WindowShortcut)

    # ------------------------------------------------------------ durum
    def _update_title(self):
        app_name = f"Kurgu {__version__}"
        if not self.project.path:
            self.setWindowTitle(("• " if self.dirty else "") + app_name)
            return
        name = os.path.basename(self.project.path)
        if getattr(self.project, "recovered", False):
            name += " [" + _("recovered") + "]"
        self.setWindowTitle(f"{'• ' if self.dirty else ''}{name} — Kurgu {__version__}")

    def before_edit(self):
        """Her değişiklikten önce çağrılır: geri alma geçmişine yazar."""
        self.history.push(self.project)
        self.dirty = True
        self._update_title()

    def on_edited(self, refresh_inspector=True):
        self.version += 1
        cp = getattr(self, "captions_panel", None)
        if cp is not None and cp.isVisible():
            cp.refresh()
        mx = getattr(self, "mixer", None)
        if mx is not None and mx.isVisible():
            mx.rebuild()
        ids = {c.id for c in self.project.clips}
        self.timeline.selected &= ids
        self.timeline.update()
        self.timeline_panel.sync_scrollbar()
        if refresh_inspector:
            self._on_selection()
        if not self.player.playing:
            self.monitor.refresh(self.timeline.playhead)
        self._update_title()

    def selected_clips(self):
        return [c for c in self.project.clips if c.id in self.timeline.selected]

    def _on_selection(self):
        self.monitor.view.update()
        st = self.timeline.sel_trans
        if st is not None:
            clip = self.project.clip_by_id(st[0])
            if clip is not None:
                self.inspector.set_transition(clip, st[1])
                return
            self.timeline.sel_trans = None
        sel = self.selected_clips()
        video = [c for c in sel if self.project.track_is_video(c.track)]
        self.inspector.set_clip((video or sel or [None])[0])
        self.color_panel.set_clip((video or [None])[0])

    def _on_play_position(self, t):
        self.timeline.set_playhead(t, emit=False)
        self.timeline.ensure_visible(t)
        self.monitor.refresh(t)
        self.inspector.sync_time(t)
        self.color_panel.sync_time(t)

    def _on_scrub(self, t):
        self.inspector.sync_time(t)
        self.color_panel.sync_time(t)
        if self.player.playing:
            rate = self.player.rate
            self.player.pause()
            self.player.play(rate)
        else:
            self.monitor.refresh(t)

    def _inspector_shown(self, vis):
        if vis and self._inspector_stale:
            self._inspector_stale = False
            self._on_selection()

    def show_dock(self, dock):
        dock.show()
        dock.raise_()

    def color_workspace(self):
        """Renk çalışma alanı: Lumetri Renk ve Skoplar öne gelir."""
        self.show_dock(self.color_dock)
        self.show_dock(self.scopes_dock)

    def current_frame_array(self):
        """Program monitöründeki karenin RGB dizisi (otomatik renk vb. için)."""
        from .scopes import qimage_to_rgb
        if self.monitor.view.image is None:
            self.monitor.refresh(self.timeline.playhead)
        return qimage_to_rgb(self.monitor.view.image, 640)

    def set_tool(self, tool):
        from .timeline import TOOLS
        self.timeline.tool = tool
        self.timeline.razor_hover = None
        self.timeline.snap_hit = None
        self.timeline.update()
        for key, b in self.timeline_panel.tool_buttons.items():
            b.setChecked(key == tool)
        a = getattr(self, "tool_actions", {}).get(tool)
        if a is not None and not a.isChecked():
            a.setChecked(True)
        name = next((n for k, _s, _i, n in TOOLS if k == tool), "Selection Tool")
        hints = {"ripple": _("drag an edge: later clips follow, no gaps"),
                 "roll": _("drag a cut: moves the edit point between two clips"),
                 "slip": _("drag a clip: changes which part of the source is used"),
                 "slide": _("drag a clip: moves it between its neighbours")}
        msg = _(name) + (" — " + hints[tool] if tool in hints else "")
        self.statusBar().showMessage(msg)

    def toggle_snap(self, on):
        self.timeline.snapping = on

    # ------------------------------------------------------------ gezinme
    def source_active(self) -> bool:
        """Klavye odağı kaynak monitöründe mi? (Space/J/K/L/oklar/I/O ona gider)"""
        w = QApplication.focusWidget()
        while w is not None:
            if w is self.source:
                return self.source.media is not None and not self.source.mc_mode
            w = w.parentWidget()
        return False

    def _player(self):
        return self.source.player if self.source_active() else self.player

    def step_frames(self, n):
        if self.source_active():
            self.source.step(n)
            return
        if self.player.playing:
            self.player.pause()
        self.timeline.set_playhead(self.timeline.playhead + n / self.project.fps)
        self.timeline.ensure_visible(self.timeline.playhead)

    def go_start(self):
        if self.source_active():
            self.source.seek(0.0)
            return
        self.timeline.set_playhead(0.0)
        self.timeline.ensure_visible(0.0)

    def go_end(self):
        if self.source_active():
            self.source.seek(self.source.duration())
            return
        self.timeline.set_playhead(self.project.duration)
        self.timeline.ensure_visible(self.project.duration)

    def jump_edit(self, direction):
        t = self.timeline.playhead
        pts = sorted(set(self.project.snap_points()))
        if direction > 0:
            cand = [p for p in pts if p > t + 1e-6]
            target = cand[0] if cand else t
        else:
            cand = [p for p in pts if p < t - 1e-6]
            target = cand[-1] if cand else 0.0
        self.timeline.set_playhead(target)
        self.timeline.ensure_visible(target)

    # ------------------------------------------------------------ medya
    def import_dialog(self):
        last = self.settings.value("last_dir", os.path.expanduser("~"))
        paths, _f = QFileDialog.getOpenFileNames(self, _("Import Media…"), last,
                                                 f"{_('Media files')} ({MEDIA_FILTER});;* (*)")
        if paths:
            self.settings.setValue("last_dir", os.path.dirname(paths[0]))
            self.import_paths(paths)

    def import_paths(self, paths) -> list[MediaItem]:
        items, errors = [], []
        for path in paths:
            if os.path.isdir(path):
                continue
            try:
                m = self.project.add_media(probe(path))
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                continue
            self._make_thumb(m)
            items.append(m)
        if items:
            self.dirty = True
            self.auto_proxy(items)
            self.bin.rebuild()
            self._update_title()
        if errors:
            QMessageBox.warning(self, _("Could not import"), "\n".join(errors))
        return items

    def _make_thumb(self, m: MediaItem):
        if m.id in self.thumbnails or not os.path.exists(m.path):
            return
        arr = thumbnail(m.path, m.kind)
        if arr is not None:
            h, w, _c = arr.shape
            self.thumbnails[m.id] = QImage(arr.data, w, h, arr.strides[0],
                                           QImage.Format_RGB888).copy()

    def remove_media(self, ids):
        self.before_edit()
        ids = set(ids)
        self.source.unload_if(ids)
        self.project.clips = [c for c in self.project.clips if c.media_id not in ids]
        for i in ids:
            self.project.media.pop(i, None)
        self.bin.rebuild()
        self.on_edited()

    # ------------------------------------------------------------ kaynak monitörü
    def open_in_source(self, m: MediaItem):
        self.source.load(m)

    def mark_in(self):
        """I: kaynak monitörü odaktaysa kaynağın, değilse sekansın giriş işareti."""
        if self.source_active():
            self.source.mark_in()
            return
        p = self.project
        t = round(self.timeline.playhead * p.fps) / p.fps
        self.before_edit()
        p.seq_in = t
        if p.seq_out is not None and p.seq_out <= t:
            p.seq_out = None
        self.on_edited(refresh_inspector=False)

    def mark_out(self):
        if self.source_active():
            self.source.mark_out()
            return
        p = self.project
        t = max(round(self.timeline.playhead * p.fps) / p.fps, 1 / p.fps)
        self.before_edit()
        p.seq_out = t
        if p.seq_in is not None and p.seq_in >= t:
            p.seq_in = None
        self.on_edited(refresh_inspector=False)

    def clear_marks(self):
        if self.source_active():
            self.source.clear_marks()
            return
        if self.project.seq_in is not None or self.project.seq_out is not None:
            self.before_edit()
            self.project.seq_in = self.project.seq_out = None
            self.on_edited(refresh_inspector=False)

    def source_edit(self, m: MediaItem, a, b, mode="overwrite"):
        """Kaynak monitöründeki işaretli parçayı oynatma kafasına koy (Ekle , / Üzerine Yaz .)."""
        p = self.project
        m = p.media.get(m.id) or p.add_media(m)  # geri alma sonrası nesne değişmiş olabilir
        t = self.timeline.playhead
        vt = next((i for i in p.track_indices("video") if not p.tracks[i].locked), None)
        at = next((i for i in p.track_indices("audio") if not p.tracks[i].locked), None)
        if (vt is None and (m.has_video or m.kind == "image")) and (at is None or not m.has_audio):
            self.statusBar().showMessage(_("All tracks are locked"), 4000)
            return
        if self.player.playing:
            self.player.pause()
        first = not any(p.track_is_video(c.track) for c in p.clips)
        self.before_edit()
        if first and self._mismatch(m):
            self._ask_match_sequence(m)
        fn = p.insert_media if mode == "insert" else p.place_media
        made = fn(m, t, vt, at, a, b, video=vt is not None, audio=at is not None)
        if not made:
            self.on_edited()
            return
        self.timeline.selected = {c.id for c in made}
        end = max(c.end for c in made)
        self.on_edited()
        self.timeline.set_playhead(end)
        self.timeline.ensure_visible(end)
        self.statusBar().showMessage(
            (_("Inserted") if mode == "insert" else _("Overwritten")) + f": {m.name}", 3000)

    def place_media(self, m: MediaItem, t: float, track_hint: int | None,
                    in_point: float | None = None, out_point: float | None = None):
        p = self.project
        vt = at = None
        if track_hint is not None and not p.tracks[track_hint].locked:
            if p.track_is_video(track_hint):
                vt = track_hint
            else:
                at = track_hint
        first = not any(p.track_is_video(c.track) for c in p.clips)
        self.before_edit()
        if first and self._mismatch(m):
            self._ask_match_sequence(m)
        made = p.place_media(m, t, vt, at, in_point, out_point)
        self.timeline.selected = {c.id for c in made}
        self.on_edited()

    # ------------------------------------------------------------ çoklu kamera
    def create_multicam_dialog(self, items):
        """Premiere "Çoklu Kamera Kaynak Sekansı Oluştur": kameraları sesten eşitle."""
        from PySide6.QtWidgets import QCheckBox
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Create Multi-Camera Source Sequence"))
        form = QFormLayout(dlg)
        name = QLineEdit(_("Multicam") + f" {len(self.project.multicams) + 1}")
        form.addRow(_("Name"), name)
        sync = QComboBox()
        sync.addItem(_("Audio (automatic)"), "audio")
        sync.addItem(_("Clip start"), "start")
        form.addRow(_("Synchronize by"), sync)
        audio = QComboBox()
        for i, m in enumerate(items):
            audio.addItem(f"{i + 1}: {m.name}", i)
        form.addRow(_("Audio from camera"), audio)
        order = QLabel("\n".join(f"{i + 1}. {m.name}" for i, m in enumerate(items)))
        order.setTextFormat(Qt.PlainText)
        form.addRow(_("Cameras"), order)
        place = QCheckBox(_("Add to timeline at playhead"))
        place.setChecked(True)
        form.addRow(place)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return None
        offsets, conf = [0.0] * len(items), [1.0] * len(items)
        if sync.currentData() == "audio":
            res = self._run_sync([m.path for m in items])
            if res is None:
                return None
            offsets, conf = res
        self.before_edit()
        gid = self.project.create_multicam(name.text().strip() or _("Multicam"), items,
                                           offsets, audio.currentData())
        self.bin.rebuild()
        if place.isChecked():
            self.place_multicam(gid, self.timeline.playhead, push=False)
        else:
            self.on_edited()
        weak = [items[i].name for i, c in enumerate(conf) if c < 0.15]
        msg = _("Multi-camera sequence created") + ": " + ", ".join(
            f"{i + 1}: {o:+.3f}s" for i, o in enumerate(offsets))
        self.statusBar().showMessage(msg, 8000)
        if weak:
            self._plain_box(QMessageBox.Warning, _("Synchronize by"),
                            _("These cameras could not be matched reliably by audio; check "
                              "their sync:") + "\n" + "\n".join(weak))
        return gid

    def _run_sync(self, paths):
        import threading
        from .. import multicam as mcm
        dlg = QProgressDialog(_("Synchronizing cameras by audio…"), _("Cancel"), 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        box = {"res": None, "err": None, "p": 0.0}

        def work():
            try:
                box["res"] = mcm.sync_by_audio(paths, progress=lambda f: box.__setitem__("p", f))
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)
            dlg.setValue(int(box["p"] * 100))
            QApplication.processEvents()
            if dlg.wasCanceled():
                return None
        dlg.close()
        if box["err"]:
            self._plain_box(QMessageBox.Warning, "Kurgu", box["err"])
            return None
        return box["res"]

    def place_multicam(self, gid, t, track_hint=None, push=True):
        p = self.project
        if gid not in p.multicams:
            return
        vt = track_hint if (track_hint is not None and p.track_is_video(track_hint)
                            and not p.tracks[track_hint].locked) else None
        if push:
            self.before_edit()
        g = p.multicams[gid]
        first = not any(p.track_is_video(c.track) for c in p.clips)
        m0 = p.media.get(g["angles"][0]["media_id"])
        if first and m0 is not None and self._mismatch(m0):
            self._ask_match_sequence(m0)
        made = p.place_multicam(gid, t, vt)
        self.timeline.selected = {c.id for c in made}
        self.on_edited()

    def rename_multicam(self, gid):
        g = self.project.multicams.get(gid)
        if not g:
            return
        text, ok = QInputDialog.getText(self, _("Rename…"), _("Name"), text=g["name"])
        if ok and text.strip():
            self.before_edit()
            g["name"] = text.strip()
            for c in self.project.clips:
                if c.mc == gid:
                    c.name = g["name"]
            self.bin.rebuild()
            self.on_edited()

    def remove_multicams(self, gids):
        self.before_edit()
        gids = set(gids)
        self.project.clips = [c for c in self.project.clips if c.mc not in gids]
        for g in gids:
            self.project.multicams.pop(g, None)
        self.bin.rebuild()
        self.on_edited()

    def multicam_key(self, angle: int):
        """1-9: oynatma kafasında (oynarken de) o kameraya kes."""
        p = self.project
        t = self.player.now() if self.player.playing else self.timeline.playhead
        c = p.multicam_clip_at(t)
        if c is None:
            self.statusBar().showMessage(_("No multi-camera clip at the playhead"), 3000)
            return
        g = p.multicams.get(c.mc)
        if not g or angle >= len(g["angles"]):
            return
        self.before_edit()
        p.multicam_cut(t, angle, c)
        self.on_edited(refresh_inspector=False)
        self.source.mc_flash(angle)
        self.statusBar().showMessage(_("Camera") + f" {angle + 1}", 1500)

    def toggle_multicam_view(self, on=None):
        on = (not self.source.mc_mode) if on is None else bool(on)
        self.source.set_multicam_mode(on)
        if hasattr(self, "mc_view_action"):
            self.mc_view_action.setChecked(on)

    # ------------------------------------------------------------ ses
    def normalize_dialog(self, target=None, auto_accept=False):
        """Seçili (yoksa tüm) ses kliplerinin ses yüksekliğini hedefe eşitle."""
        from .. import audio as au
        from ..media import SourcePool
        from ..render import Compositor
        p = self.project
        sel = [c for c in self.selected_clips() if not p.track_is_video(c.track)]
        clips = sel or [c for c in p.clips if not p.track_is_video(c.track)
                        and p.media.get(c.media_id) is not None]
        if not clips:
            self._plain_box(QMessageBox.Information, _("Normalize Loudness…"),
                            _("Select audio clips first."))
            return None
        if target is None:
            dlg = QDialog(self)
            dlg.setWindowTitle(_("Normalize Loudness…").rstrip("…"))
            form = QFormLayout(dlg)
            combo = QComboBox()
            for v, name in au.LOUDNESS_TARGETS:
                combo.addItem(_(name), v)
            form.addRow(_("Target"), combo)
            info = QLabel(_("{n} clips").format(n=len(clips)) + (
                "" if sel else " (" + _("all audio clips") + ")"))
            form.addRow(info)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            form.addRow(bb)
            if not auto_accept and not dlg.exec():
                return None
            target = combo.currentData()
        peak_mode = target > -5
        comp = Compositor(p, SourcePool(p.sample_rate))
        report = []
        self.before_edit()
        try:
            for c in clips:
                if kf.is_animated(c, "volume"):
                    report.append(f"{c.name}: " + _("skipped (volume is keyframed)"))
                    continue
                m = p.media.get(c.media_id)
                src = comp.pool.audio_source(m) if m else None
                if src is None:
                    continue
                n = int(c.duration * p.sample_rate)
                data = comp._clip_samples(c, src, c.start, n)
                if c.audio_fx:
                    data = au.process(data, c.audio_fx, p.sample_rate)
                g = au.normalize_gain(data, p.sample_rate, target, peak_mode)
                vol = min(max(g, 0.0), 4.0)
                for x in p.linked(c):
                    if not p.track_is_video(x.track):
                        x.volume = round(vol, 4)
                lu = au.integrated_loudness(data * vol, p.sample_rate)
                report.append(f"{c.name}: {au.lin2db(vol):+.1f} dB → "
                              + (f"{lu:.1f} LUFS" if lu > -100 else "—"))
        finally:
            comp.pool.close()
        self.on_edited()
        self.statusBar().showMessage(_("Loudness normalized") + f": {len(report)}", 5000)
        return report

    # ------------------------------------------------------------ altyazılar
    def add_caption(self):
        p = self.project
        t = round(self.timeline.playhead * p.fps) / p.fps
        self.before_edit()
        made = p.add_captions([(t, t + 3.0, _("New caption"))])
        self.timeline.selected = {c.id for c in made}
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()
        self.show_dock(self.captions_dock)

    def edit_caption(self, clip):
        text, ok = QInputDialog.getMultiLineText(self, _("Captions"), _("Caption text:"),
                                                 clip.text)
        if ok:
            self.before_edit()
            clip.text = text
            clip.name = text.replace("\n", " ")[:40]
            self.on_edited()

    def import_captions(self):
        from .. import captions as cap
        last = self.settings.value("last_dir", os.path.expanduser("~"))
        path, _f = QFileDialog.getOpenFileName(self, _("Import Captions"), last,
                                               _("Subtitles") + " (*.srt *.vtt)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                cues = cap.parse_srt(f.read())
        except OSError as e:
            self._plain_box(QMessageBox.Warning, "Kurgu", str(e))
            return
        if not cues:
            self._plain_box(QMessageBox.Warning, _("Import Captions"),
                            _("No captions found in this file."))
            return
        self.before_edit()
        self.project.add_captions(cues)
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()
        self.show_dock(self.captions_dock)
        self.statusBar().showMessage(_("{n} captions").format(n=len(cues)), 4000)

    def export_captions(self):
        from .. import captions as cap
        cues = self.project.caption_cues()
        if not cues:
            self._plain_box(QMessageBox.Information, _("Export Captions"),
                            _("There are no captions to export."))
            return
        base = os.path.splitext(self.project.path)[0] if self.project.path else \
            os.path.join(self.settings.value("last_dir", os.path.expanduser("~")), "captions")
        path, flt = QFileDialog.getSaveFileName(self, _("Export Captions"), base + ".srt",
                                                "SubRip (*.srt);;WebVTT (*.vtt)")
        if not path:
            return
        vtt = path.lower().endswith(".vtt") or "vtt" in flt.lower()
        if not path.lower().endswith((".srt", ".vtt")):
            path += ".vtt" if vtt else ".srt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(cap.format_vtt(cues) if vtt else cap.format_srt(cues))
        self.statusBar().showMessage(_("Saved to") + " " + path, 5000)

    def _ensure_whisper(self) -> bool:
        from .. import captions as cap
        if cap.have_whisper():
            return True
        import sys as _sys
        if getattr(_sys, "frozen", False):
            self._plain_box(QMessageBox.Warning, _("Captions"),
                            _("Speech recognition is not included in this build. Please "
                              "install the latest Kurgu release."))
            return False
        r = QMessageBox.question(
            self, _("Captions"),
            _("Speech recognition needs the free 'faster-whisper' package (about 100 MB). "
              "Install it now?"))
        if r != QMessageBox.Yes:
            return False
        import subprocess
        import threading
        dlg = QProgressDialog(_("Installing faster-whisper…"), None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        box = {}

        def work():
            box["r"] = subprocess.run([_sys.executable, "-m", "pip", "install",
                                       "faster-whisper"], capture_output=True, text=True)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)
            QApplication.processEvents()
        dlg.close()
        import importlib
        importlib.invalidate_caches()
        if box["r"].returncode != 0 or not cap.have_whisper():
            self._plain_box(QMessageBox.Warning, _("Captions"),
                            (box["r"].stderr or box["r"].stdout)[-1500:])
            return False
        return True

    def transcribe_dialog(self, engine=None, auto_accept=False):
        """Konuşmadan altyazı (Premiere "Metin → Transkript → Altyazı oluştur")."""
        from PySide6.QtWidgets import QCheckBox
        from .. import captions as cap
        p = self.project
        if p.duration <= 0:
            self._plain_box(QMessageBox.Information, _("Captions"), _("The timeline is empty."))
            return None
        if engine is None and not self._ensure_whisper():
            return None
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Transcribe Speech to Captions"))
        form = QFormLayout(dlg)
        lang = QComboBox()
        for code, name in cap.LANGUAGES:
            lang.addItem(_(name), code)
        lang.setCurrentIndex(max(0, lang.findData(self.settings.value("asr_lang", ""))))
        form.addRow(_("Language"), lang)
        model = QComboBox()
        for m in cap.MODEL_SIZES:
            model.addItem(m, m)
        model.setCurrentIndex(max(0, model.findData(self.settings.value("asr_model", "small"))))
        model.setToolTip(_("Larger models are more accurate but slower; the model is "
                           "downloaded once on first use."))
        form.addRow(_("Model"), model)
        chars = QSpinBox()
        chars.setRange(16, 80)
        chars.setValue(int(self.settings.value("asr_chars", 42)))
        form.addRow(_("Max characters per line"), chars)
        lines = QSpinBox()
        lines.setRange(1, 3)
        lines.setValue(2)
        form.addRow(_("Lines"), lines)
        replace = QCheckBox(_("Replace existing captions"))
        replace.setChecked(True)
        form.addRow(replace)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not auto_accept and not dlg.exec():
            return None
        for k, w in (("asr_lang", lang), ("asr_model", model)):
            self.settings.setValue(k, w.currentData())
        self.settings.setValue("asr_chars", chars.value())
        snap = Project.from_dict(p.to_dict())
        prog = QProgressDialog(_("Preparing audio…"), _("Cancel"), 0, 1000, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        import threading
        box = {"p": 0.0, "stage": "audio", "res": None, "err": None}
        cancelled = prog.wasCanceled

        def work():
            try:
                audio = cap.sequence_audio(snap, 0.0, snap.duration,
                                           progress=lambda f: box.__setitem__("p", 0.15 * f),
                                           cancelled=cancelled)
                box["stage"] = "asr"
                segs, language = cap.transcribe(
                    audio, lang.currentData(), model.currentData(),
                    progress=lambda f: box.__setitem__("p", 0.15 + 0.85 * f),
                    cancelled=cancelled, engine=engine)
                box["res"] = (cap.segments_to_cues(segs, chars.value(), lines.value()), language)
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)
            prog.setValue(int(box["p"] * 1000))
            if box["stage"] == "asr":
                prog.setLabelText(_("Recognizing speech… (the model is downloaded on first "
                                    "use)"))
            QApplication.processEvents()
        prog.close()
        if box["err"]:
            if box["err"] != "cancelled":
                self._plain_box(QMessageBox.Warning, _("Captions"), box["err"])
            return None
        cues, language = box["res"]
        if not cues:
            self._plain_box(QMessageBox.Information, _("Captions"), _("No speech was found."))
            return []
        self.before_edit()
        made = p.add_captions(cues, replace=replace.isChecked() and
                              p.caption_track(create=False) is not None)
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()
        self.show_dock(self.captions_dock)
        self.statusBar().showMessage(
            _("{n} captions").format(n=len(made)) + (f" · {language}" if language else ""), 6000)
        return made

    # ------------------------------------------------------------ sekans / klip boyutu
    def _mismatch(self, m) -> bool:
        if m.kind not in ("video", "image") or not m.width or not m.height:
            return False
        p = self.project
        return (m.width // 2 * 2, m.height // 2 * 2) != (p.width, p.height)

    def _ask_match_sequence(self, m):
        """Premiere'deki "Klip uyuşmazlığı" sorusu: boş sekansa ilk klip konunca."""
        p = self.project
        box = QMessageBox(self)
        box.setWindowTitle(_("Clip mismatch"))
        box.setText(_("This clip ({cw}×{ch}) does not match the project ({pw}×{ph})."
                      ).format(cw=m.width, ch=m.height, pw=p.width, ph=p.height))
        box.setInformativeText(_("Change the project settings to match the clip? "
                                 "(e.g. vertical phone video → vertical 9:16 project)"))
        change = box.addButton(_("Change project settings"), QMessageBox.AcceptRole)
        box.addButton(_("Keep existing settings"), QMessageBox.RejectRole)
        box.setDefaultButton(change)
        box.exec()
        if box.clickedButton() is change:
            self._match_sequence_to(m)

    def _match_sequence_to(self, m):
        p = self.project
        p.width, p.height = m.width // 2 * 2, m.height // 2 * 2
        if m.kind == "video" and m.fps and 1 <= m.fps <= 240:
            p.fps = round(m.fps, 3)
        self.monitor.comp = None
        self.cache.clear()
        self.statusBar().showMessage(_("Project set to {w}×{h} @ {fps} fps").format(
            w=p.width, h=p.height, fps=round(p.fps, 3)), 5000)

    def match_sequence_to_clip(self, clip):
        m = self.project.media.get(clip.media_id)
        if m is None:
            return
        self.before_edit()
        self._match_sequence_to(m)
        self.on_edited()

    def nudge_selected(self, frames: int):
        """Premiere: Alt+←/→ seçili klipleri 1 kare, Alt+Shift ile 5 kare kaydırır."""
        sel = self.selected_clips()
        if not sel:
            return
        p = self.project
        d = frames / p.fps
        if min(c.start for c in sel) + d < -1e-9:
            d = -min(c.start for c in sel)
        if abs(d) < 1e-9:
            return
        self.before_edit()
        ids = {c.id for c in sel}
        for c in sel:
            c.start = round((c.start + d) * p.fps) / p.fps
        for c in sel:  # Premiere gibi: altında kalanın üzerine yazar
            p._clear_space(c.track, c.start, c.end, ignore=ids)
        self.on_edited()
        from .timeline import timecode
        self.statusBar().showMessage(
            _("Moved {n} frame(s)").format(n=f"{frames:+d}") + "  →  "
            + timecode(min(c.start for c in sel), p.fps), 2500)

    def scale_clip(self, clip, mode: str):
        """'fill': boşluk kalmadan kareyi doldur (kenarlar kırpılır), 'fit': tamamı görünsün."""
        p = self.project
        m = p.media.get(clip.media_id)
        if m is None or not m.width:
            return
        fit = min(p.width / m.width, p.height / m.height)
        fill = max(p.width / m.width, p.height / m.height)
        value = round(fill / fit, 4) if mode == "fill" else 1.0
        self.before_edit()
        self.prop_edit(clip, "scale", value, push=False)
        if not kf.is_animated(clip, "pos_x"):
            clip.pos_x = clip.pos_y = 0.0
        self.on_edited()

    # ------------------------------------------------------------ düzenleme
    def undo(self):
        p = self.history.undo(self.project)
        if p is not None:
            self._swap_project(p)

    def redo(self):
        p = self.history.redo(self.project)
        if p is not None:
            self._swap_project(p)

    def _swap_project(self, p: Project):
        self.player.pause()
        self.project = p
        self.monitor.comp = None
        self.dirty = True
        self.bin.rebuild()
        self.on_edited()

    def split_at_playhead(self):
        t = self.timeline.playhead
        sel = {c.id for c in self.selected_clips() if c.contains(t)} or None
        self.before_edit()
        made = self.project.split_at(t, sel)
        if not made:
            self.history.undo_stack.pop()
        self.on_edited()

    def delete_selected(self, ripple=False):
        gap = self.timeline.sel_gap
        if gap is not None and not self.timeline.selected:
            self.timeline.sel_gap = None
            self.before_edit()
            if self.project.close_gap(*gap):
                self.statusBar().showMessage(_("Gap closed"), 3000)
            else:
                self.history.undo_stack.pop()
                self.statusBar().showMessage(
                    _("Can't close the gap: linked clips on another track would overlap"), 6000)
            self.on_edited()
            return
        st = self.timeline.sel_trans
        if st is not None:
            clip = self.project.clip_by_id(st[0])
            self.timeline.sel_trans = None
            if clip is not None:
                self.before_edit()
                self.project.apply_transition(clip, st[1], "")
                if st[1] == "out":  # sağ kenar: tek taraflı çıkış geçişini de temizle
                    for c in self.project.linked(clip):
                        c.trans_out = ""
                self.on_edited()
            return
        if not self.timeline.selected:
            return
        self.before_edit()
        self.project.delete(self.timeline.selected, ripple=ripple)
        self.timeline.selected.clear()
        self.on_edited()

    def duplicate_selected(self):
        sel = self.selected_clips()
        if not sel:
            return
        self.before_edit()
        offset = max(c.end for c in sel) - min(c.start for c in sel)
        links: dict[str, str] = {}
        new_ids = set()
        for c in sel:
            d = copy.deepcopy(c)
            d.id = new_id()
            d.start += offset
            if c.link_id:
                d.link_id = links.setdefault(c.link_id, new_id())
            self.project._clear_space(d.track, d.start, d.end)
            self.project.clips.append(d)
            new_ids.add(d.id)
        self.timeline.selected = new_ids
        self.on_edited()

    # ------------------------------------------------------------ efektler
    def apply_effect_key(self, key: str, clip=None, edge: str = "in"):
        """Efektler panelinden gelen 'effect:<id>' veya 'transition:<id>' anahtarını uygular."""
        kind, _sep, ident = key.partition(":")
        targets = [clip] if clip is not None else self.selected_clips()
        if not targets:
            self.statusBar().showMessage(_("Select a clip first"), 4000)
            return
        self.before_edit()
        p = self.project
        if kind == "effect":
            # Ses klibine bırakıldıysa bağlı video klibine uygula
            video = []
            for c in targets:
                for x in p.linked(c):
                    if p.track_is_video(x.track) and x.kind == "media" and x not in video:
                        video.append(x)
            if not video:
                self.history.undo_stack.pop()
                self.statusBar().showMessage(
                    _("Effects can only be applied to video or image clips"), 5000)
                return
            for c in video:
                c.effects.append(fx.new_effect(ident))
            self.timeline.selected = {x.id for c in video for x in p.linked(c)}
            self.timeline.sel_trans = None
            self.on_edited()
            self.inspector.focus_effects(len(video[0].effects) - 1)
            self.statusBar().showMessage(
                _(fx.EFFECTS[ident].name) + " " + _("applied") + " → " + video[0].name, 4000)
            return
        elif kind == "transition":
            done = set()
            lonely = through = False
            for c in targets:
                if c.id not in done:
                    neighbour = p.prev_adjacent(c) if edge == "in" else p.next_adjacent(c)
                    lonely |= neighbour is None
                    pair = (neighbour, c) if edge == "in" else (c, neighbour)
                    through |= p.is_through_edit(*pair)
                    p.apply_transition(c, edge, ident, 1.0)
                    done.update(x.id for x in p.linked(c))
            name = _(transitions.NAMES.get(ident, ident))
            if through:
                QMessageBox.information(self, name, _(
                    "These two clips are a continuous shot cut with the razor, so both sides "
                    "show the same picture at the cut. Wipes and irises replace the image with "
                    "the same image and look like nothing happens (Premiere behaves the same).\n\n"
                    "To see the transition: delete a piece between the two clips (or use two "
                    "different clips), then close the gap."))
            elif lonely:
                self.statusBar().showMessage(
                    name + ": " + _("no adjacent clip on this side, so the transition goes "
                                    "to/from black. Close the gap to blend two clips."), 8000)
            else:
                self.statusBar().showMessage(name + " " + _("applied"), 4000)
        if clip is not None:
            self.timeline.selected = {x.id for x in p.linked(clip)}
        self.on_edited()

    # ------------------------------------------------------------ anahtar kareler
    def _playhead_in(self, clip) -> bool:
        t = self.timeline.playhead
        if clip.start - 1e-6 <= t <= clip.end + 1e-6:
            return True
        self.statusBar().showMessage(
            _("Move the playhead over the clip to set keyframes"), 5000)
        return False

    def prop_edit(self, clip, prop, value, push=True):
        """Denetçi/monitör değer değişikliği. Özellik animasyonluysa oynatma kafasında
        anahtar kare oluşturur ya da günceller (Premiere'deki gibi)."""
        if push:
            self.inspector._push_coalesced((clip.id, prop))
        if kf.is_animated(clip, prop):
            if not self._playhead_in(clip):
                return
            kf.set_key(clip, prop, clip.source_time(self.timeline.playhead), value,
                       self.project.fps)
        else:
            kf.set_static(clip, prop, value)
        self.on_edited(refresh_inspector=False)
        self.inspector.sync_time(self.timeline.playhead)
        if prop.startswith("fx."):
            # Denetçi ile Lumetri paneli aynı değerleri gösterir; diğeri sekme değişince tazelenir
            if self.color_panel.clip is clip and not self.color_panel.isVisible():
                self.color_panel.refresh()
            if not self.inspector.isVisible():
                self._inspector_stale = True

    @staticmethod
    def _prop_group(prop):
        """Konum X ve Y Premiere'deki gibi tek özellik olarak birlikte anahtarlanır."""
        return ("pos_x", "pos_y") if prop in ("pos_x", "pos_y") else (prop,)

    def toggle_anim(self, clip, prop):
        if clip is None:
            return
        if not kf.is_animated(clip, prop) and not self._playhead_in(clip):
            self.inspector.sync_time(self.timeline.playhead)
            return
        self.before_edit()
        t = min(max(self.timeline.playhead, clip.start), clip.end)
        was = kf.is_animated(clip, prop)
        for pr in self._prop_group(prop):
            if kf.is_animated(clip, pr) == was:
                kf.toggle_animation(clip, pr, clip.source_time(t), self.project.fps)
        self.on_edited(refresh_inspector=False)
        self.inspector.sync_time(self.timeline.playhead)

    def toggle_key_here(self, clip, prop):
        if clip is None or not self._playhead_in(clip):
            return
        self.before_edit()
        s = clip.source_time(self.timeline.playhead)
        here = kf.key_index(kf.keys(clip, prop), s, self.project.fps) is not None
        for pr in self._prop_group(prop):
            if here:
                kf.remove_key(clip, pr, s, self.project.fps)
            else:
                kf.set_key(clip, pr, s, kf.value_at(clip, pr, self.timeline.playhead),
                           self.project.fps)
        self.on_edited(refresh_inspector=False)
        self.inspector.sync_time(self.timeline.playhead)

    def jump_key(self, clip, prop, direction):
        if clip is None:
            return
        times = [k[0] for k in kf.keys(clip, prop)] if prop else kf.all_key_times(clip)
        tl_times = [clip.start + (s - clip.in_point) / clip.speed for s in times]
        t = self.timeline.playhead
        eps = 0.5 / self.project.fps
        cand = [x for x in tl_times if (x > t + eps if direction > 0 else x < t - eps)]
        if cand:
            target = min(cand) if direction > 0 else max(cand)
            self.player.pause()
            self.timeline.set_playhead(target)
            self.timeline.ensure_visible(target)

    # ------------------------------------------------------------ güncellemeler
    def check_updates(self, manual=False):
        import threading
        import time as _t
        from PySide6.QtCore import QObject, Signal as _Signal

        class _B(QObject):
            done = _Signal(object, str)
        bridge = _B()
        self._upd_bridge = bridge

        def work():
            try:
                info = updater.fetch_latest()
                bridge.done.emit(info, "")
            except Exception as e:  # ağ yok vb.
                bridge.done.emit(None, str(e))

        def finished(info, err):
            self.settings.setValue("last_update_check", _t.time())
            if info and updater.is_newer(info["version"], __version__):
                self._show_update_button(info)
                if manual:
                    self.show_update_dialog(info)
            elif manual:
                if err:
                    QMessageBox.warning(self, _("Check for Updates…").rstrip("…"),
                                        _("Could not reach GitHub:") + f"\n{err}")
                else:
                    QMessageBox.information(self, _("Check for Updates…").rstrip("…"),
                                            _("You are using the latest version ({v}).")
                                            .format(v=__version__))
        bridge.done.connect(finished)
        threading.Thread(target=work, daemon=True).start()

    def _show_update_button(self, info):
        from PySide6.QtWidgets import QPushButton
        if self._update_btn is None:
            self._update_btn = QPushButton()
            self._update_btn.setFlat(True)
            self._update_btn.setStyleSheet("color:#7fd18b; font-weight:bold;")
            self.statusBar().addPermanentWidget(self._update_btn)
            self._update_btn.clicked.connect(lambda: self.show_update_dialog(self._update_info))
        self._update_info = info
        self._update_btn.setText("⬆ " + _("New version: {v}").format(v=info["version"]))

    def show_update_dialog(self, info):
        box = QMessageBox(self)
        box.setWindowTitle(_("Update available"))
        box.setText(_("Kurgu {new} is available (you have {old}).")
                    .format(new=info["version"], old=__version__))
        notes = info.get("notes", "").strip()
        if notes:
            box.setDetailedText(notes)
        upd = box.addButton(_("Update Now"), QMessageBox.AcceptRole) \
            if updater.can_self_update(info) else None
        page = box.addButton(_("Download Page"), QMessageBox.ActionRole)
        box.addButton(_("Later"), QMessageBox.RejectRole)
        box.exec()
        if upd is not None and box.clickedButton() is upd:
            self.self_update(info)
        elif box.clickedButton() is page:
            QDesktopServices.openUrl(QUrl(info["url"]))

    def self_update(self, info):
        """Windows: kurulum programını indir, Kurgu'yu kapat, sessizce kur ve yeniden aç."""
        if not self.maybe_save():
            return
        import threading
        from PySide6.QtCore import QObject, Signal as _Signal

        class _B(QObject):
            prog = _Signal(int)
            done = _Signal(str, str)
        b = _B()
        self._dl_bridge = b
        dlg = QProgressDialog(_("Downloading update…"), _("Cancel"), 0, 1000, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        state = {"cancel": False}
        dlg.canceled.connect(lambda: state.__setitem__("cancel", True))
        b.prog.connect(dlg.setValue)

        def work():
            try:
                path = updater.download_verified(
                    info, progress=lambda d, t: b.prog.emit(int(d * 1000 / t)),
                    cancelled=lambda: state["cancel"])
                b.done.emit(path, "")
            except Exception as e:
                b.done.emit("", str(e))

        def finished(path, err):
            dlg.close()
            if not path:
                if not state["cancel"]:
                    QMessageBox.warning(self, _("Update available"), err)
                return
            updater.run_installer(path)
            self.dirty = False
            self.close()
        b.done.connect(finished)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ proxy
    def auto_proxy(self, media_items):
        from .. import proxy as px
        for m in media_items:
            if m.kind != "video":
                continue
            if not m.proxy:
                m.proxy = px.existing_proxy(m)
            if not m.proxy and px.needs_proxy(m) and \
                    self.settings.value("auto_proxy", True, type=bool):
                self.proxy_progress[m.id] = 0.0
                self.proxies.enqueue(m)
        if self.proxy_progress:
            self.statusBar().showMessage(_("Creating proxies in the background for heavy videos "
                                           "(4K / HEVC / 10-bit)…"), 6000)

    def create_proxies(self, media_items):
        for m in media_items:
            if not m.proxy:
                self.proxy_progress[m.id] = 0.0
                self.proxies.enqueue(m)
        self.bin.update_labels()

    def delete_proxies(self, media_items):
        for m in media_items:
            self.proxies.cancel(m.id)
            self.proxy_progress.pop(m.id, None)
            if m.proxy:
                try:
                    os.remove(m.proxy)
                except OSError:
                    pass
                m.proxy = ""
        self._proxy_changed()

    def set_use_proxy(self, on: bool):
        self.use_proxy = bool(on)
        self.settings.setValue("use_proxy", self.use_proxy)
        self.monitor.pool.use_proxy = self.use_proxy
        self.source.pool.use_proxy = self.use_proxy
        self.source.pool.close()
        if self.source.media is not None:
            self.source._show(self.source.clock.playhead)
        self._proxy_changed()
        self.statusBar().showMessage(_("Proxies on: preview uses light copies") if on else
                                     _("Proxies off: preview uses original files"), 3000)

    def _proxy_changed(self):
        self.version += 1  # önbellek imzası proxy durumunu içerir
        self._render_status = (None, [])
        self.bin.update_labels()
        if not self.player.playing:
            self.monitor.refresh(self.timeline.playhead)
        self.timeline.update()

    def _proxy_progress(self, media_id, frac):
        self.proxy_progress[media_id] = frac
        self.bin.update_labels()
        m = self.project.media.get(media_id)
        if m is not None:
            self.statusBar().showMessage(
                _("Creating proxy: {name} {p}%").format(name=m.name, p=int(frac * 100)), 2000)

    def _proxy_finished(self, media_id, path, err):
        self.proxy_progress.pop(media_id, None)
        m = self.project.media.get(media_id)
        if m is not None and path:
            m.proxy = path
            self.statusBar().showMessage(_("Proxy ready: {name}").format(name=m.name), 4000)
        elif m is not None and err and err != "cancelled":
            self.statusBar().showMessage(_("Proxy failed: {name}").format(name=m.name)
                                         + f" ({err})", 8000)
        self._proxy_changed()

    # ------------------------------------------------------------ otomatik kaydetme
    def set_autosave_interval(self, minutes: int):
        self.settings.setValue("autosave_min", minutes)
        self.autosave_timer.stop()
        if minutes > 0:
            self.autosave_timer.start(minutes * 60 * 1000)

    def do_autosave(self, tag="auto") -> str | None:
        p = self.project
        if not self.dirty or not (p.clips or p.media):
            return None
        try:
            path = autosave.write_autosave(p, tag)
        except OSError as e:
            self.statusBar().showMessage(f"{_('Auto save failed')}: {e}", 8000)
            return None
        self.session.update(uid=p.uid, project_path=p.path, last_autosave=path)
        import time as _t
        self.statusBar().showMessage(_("Auto saved") + " " + _t.strftime("%H:%M"), 4000)
        return path

    def emergency_save(self):
        """Beklenmeyen bir hata olduğunda hemen yedek al."""
        try:
            self.do_autosave("crash")
        except Exception:
            pass

    def offer_recovery(self, crashed):
        import time as _t
        best = None
        for info in crashed:
            cand = autosave.recovery_candidate(info)
            if cand and (best is None or cand["mtime"] > best["mtime"]):
                best = cand
        for info in crashed:
            autosave.Session.discard(info)
        if best is None:
            return
        name = os.path.basename(best["original"]) if best["original"] else _("Untitled project")
        when = _t.strftime("%d.%m.%Y %H:%M", _t.localtime(best["mtime"]))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(_("Recover project"))
        box.setText(_("Kurgu did not close properly last time."))
        box.setInformativeText(_("An auto-saved copy of “{name}” from {when} is available. "
                                 "Open it?").format(name=name, when=when))
        rec = box.addButton(_("Recover"), QMessageBox.AcceptRole)
        box.addButton(_("Not now"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is rec:
            if self.dirty and not self.maybe_save():
                return
            self.open_project(best["path"])

    def open_autosave_dialog(self):
        if not self.maybe_save():
            return
        os.makedirs(autosave.AUTOSAVE_DIR, exist_ok=True)
        path, _f = QFileDialog.getOpenFileName(self, _("Open Auto-Saved Project…"),
                                               autosave.AUTOSAVE_DIR,
                                               f"{_('Kurgu project')} (*.kurgu)")
        if path:
            self.open_project(path)

    # ------------------------------------------------------------ render önbelleği
    def frame_sig(self) -> FrameSig:
        if self._sig is None or self._sig_ver != self.version or self._sig.p is not self.project:
            self._sig = FrameSig(self.project, self.use_proxy)
            self._sig_ver = self.version
        return self._sig

    def snapshot(self):
        """Arka plan işçisi için projenin değişmez kopyası (sürüm başına bir kez)."""
        if self._snap is None or self._snap_ver != self.version:
            from ..model import Project
            self._snap = Project.from_dict(self.project.to_dict())
            self._snap_ver = self.version
        return self._snap

    def prefetch(self, fi, w, h):
        """Oynatırken önümüzdeki ~2 saniyeyi arka planda hazırla."""
        ahead = int(self.project.fps * 2)
        st = self._prefetch
        if st and st[0] == self.version and st[1:3] == (w, h) and fi < st[3] - ahead // 2 \
                and fi >= st[4]:
            return
        last = int(self.project.duration * self.project.fps)
        frames = list(range(fi + 1, min(fi + 1 + ahead, last + 1)))
        if frames:
            self.worker.submit(self.snapshot(), frames, w, h, use_proxy=self.use_proxy)
            self._prefetch = (self.version, w, h, frames[-1], fi)

    def render_status(self):
        """Zaman çizelgesi çubuğu: [(t0, t1, render_edildi_mi)] (en fazla 2 kez/sn hesaplanır)."""
        import time
        w, h = self.monitor.frame_size(True)
        key = (self.version, w, h, self.cache.generation)
        last_key, runs, stamp = self._render_status if len(self._render_status) == 3 \
            else (None, [], 0)
        if key == last_key or (last_key and last_key[:3] == key[:3]
                               and time.monotonic() - stamp < 0.5):
            return runs
        sig = self.frame_sig()
        fps = self.project.fps
        runs = []
        for fi in heavy_frames(self.project):
            done = self.cache.has((fi, sig(fi / fps), w, h))
            t0, t1 = fi / fps, (fi + 1) / fps
            if runs and runs[-1][2] == done and abs(runs[-1][1] - t0) < 1e-6:
                runs[-1][1] = t1
            else:
                runs.append([t0, t1, done])
        self._render_status = (key, runs, time.monotonic())
        return runs

    def render_effects(self):
        """Premiere: Sekans → Efektleri Render Et (Enter). Efektli bölümleri oynatma
        çözünürlüğünde önceden hesaplar; o bölümler takılmadan oynar."""
        self.player.pause()
        w, h = self.monitor.frame_size(True)
        sig = self.frame_sig()
        fps = self.project.fps
        frames = [fi for fi in heavy_frames(self.project)
                  if not self.cache.has((fi, sig(fi / fps), w, h))]
        if not frames:
            self.statusBar().showMessage(_("Nothing to render: all effect sections are ready"), 4000)
            return
        if self.cache.disk_dir:  # kalıcı render dosyaları: sınır disk bütçesi (JPEG ~0,4 B/px)
            cap = int(self.cache.disk_budget * 0.8 // max(w * h * 0.4, 1))
        else:
            cap = int(self.cache.budget * 0.85 // self.cache.frame_bytes(w, h))
        if len(frames) > cap:
            start = int(self.timeline.playhead * fps)
            frames = sorted(frames, key=lambda f: (f < start, f))[:cap]
            QMessageBox.information(self, _("Render"), _(
                "The preview cache can hold about {n} frames at this playback resolution; "
                "rendering the first part from the playhead. Lower the playback resolution "
                "to render more.").format(n=cap))
        from PySide6.QtCore import QObject, Signal as _Signal

        class _Bridge(QObject):
            prog = _Signal(int, int)
            fin = _Signal(bool)
        bridge = _Bridge()
        dlg = QProgressDialog(_("Rendering effects…"), _("Cancel"), 0, len(frames), self)
        dlg.setWindowTitle(_("Render"))
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        bridge.prog.connect(lambda i, n: dlg.setValue(i))
        jid = self.worker.submit(self.snapshot(), frames, w, h, use_proxy=self.use_proxy,
                                 persist=True,
                                 progress=lambda i, n: bridge.prog.emit(i, n),
                                 done=lambda ok: bridge.fin.emit(ok))
        dlg.canceled.connect(self.worker.cancel)

        def finished(ok):
            dlg.close()
            self._render_status = (None, [])
            self.timeline.update()
            if ok:
                self.statusBar().showMessage(_("Render finished: effect sections play smoothly"), 5000)
        bridge.fin.connect(finished)
        self._render_bridge = (bridge, jid)

    def clear_render_cache(self):
        self.cache.clear(disk=True)
        self._render_status = (None, [])
        self.timeline.update()
        self.statusBar().showMessage(_("Render files deleted"), 3000)

    def _jump_any_key(self, direction):
        sel = [c for c in self.selected_clips() if c.keyframes]
        if sel:
            self.jump_key(sel[0], None, direction)

    # ------------------------------------------------------------ renk eşleştirme
    def _busy(self, fn, *a):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return fn(*a)
        finally:
            QGuiApplication.restoreOverrideCursor()

    def set_color_reference_media(self, media):
        try:
            px = self._busy(colormatch.sample_media_pixels, self.project, media)
        except Exception as e:
            QMessageBox.warning(self, _("Color Match"), str(e))
            return
        self.color_ref = colormatch.reference_from_pixels(px, media.name)
        self._announce_reference()

    def set_color_reference_clip(self, clip):
        try:
            px = self._busy(colormatch.sample_clip_pixels, self.project, clip)
        except Exception as e:
            QMessageBox.warning(self, _("Color Match"), str(e))
            return
        self.color_ref = colormatch.reference_from_pixels(px, clip.name)
        self._announce_reference()

    def _announce_reference(self):
        self.statusBar().showMessage(
            "🎨 " + _("Color reference copied: {name}. Select clips → right-click → Apply Color "
                      "Palette.").format(name=self.color_ref["ref_name"]), 10000)

    def apply_color_reference(self, clips=None, ref=None):
        ref = ref or self.color_ref
        if ref is None:
            return
        targets = []
        for c in (clips or self.selected_clips()):
            for x in self.project.linked(c):
                if self.project.track_is_video(x.track) and x.kind == "media" and x not in targets:
                    targets.append(x)
        if not targets:
            self.statusBar().showMessage(_("Select a clip first"), 4000)
            return
        self.before_edit()
        try:
            for c in targets:
                self._busy(colormatch.apply_reference, self.project, c, ref)
        except Exception as e:
            self.history.undo_stack.pop()
            QMessageBox.warning(self, _("Color Match"), str(e))
            return
        self.on_edited()
        idx = next(i for i, e in enumerate(targets[0].effects) if e["type"] == "color_match")
        self.inspector.focus_effects(idx)
        self.statusBar().showMessage("🎨 " + _("Color palette applied to {n} clip(s)")
                                     .format(n=len(targets)), 5000)

    def pick_color_reference_menu(self, clip, anchor):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        if self.color_ref is not None:
            menu.addAction(_("Copied reference") + f": {self.color_ref['ref_name']}",
                           lambda: self.apply_color_reference([clip]))
            menu.addSeparator()
        for m in self.project.media.values():
            if m.kind == "audio":
                continue

            def use(m=m):
                self.set_color_reference_media(m)
                if self.color_ref is not None:
                    self.apply_color_reference([clip])
            menu.addAction(("🖼 " if m.kind == "image" else "🎞 ") + m.name, use)
        if menu.isEmpty():
            menu.addAction(_("Import an image first (drag it into the Project panel)")).setEnabled(False)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def reanalyze_color_match(self, clip, push=True, quiet=False):
        effs = [e for e in clip.effects if e["type"] == "color_match" and e["params"].get("ref_mean")]
        if not effs:
            return
        if push:
            self.before_edit()
        for e in effs:
            e["params"].update(self._busy(colormatch.analyze_source, self.project, clip, e))
        self.on_edited(refresh_inspector=not quiet)
        if not quiet:
            self.statusBar().showMessage(_("Clip re-analyzed"), 3000)

    def remove_effect(self, clip, index):
        """index None ise tüm efektleri kaldırır."""
        self.before_edit()
        if index is None:
            for e in clip.effects:
                kf.drop_effect_keys(clip, e)
            clip.effects.clear()
        elif 0 <= index < len(clip.effects):
            kf.drop_effect_keys(clip, clip.effects[index])
            clip.effects.pop(index)
        self.on_edited()

    def apply_default_transition(self):
        """Premiere'deki Ctrl+D: seçili kliplerin (yoksa oynatma kafasındaki kesmenin)
        kenarına çapraz çözülme uygular."""
        p = self.project
        t = self.timeline.playhead
        sel = self.selected_clips()
        if not sel:
            # Oynatma kafasına en yakın kesmedeki klipleri bul
            sel = [c for c in p.clips if abs(c.start - t) < 0.5 and p.prev_adjacent(c)]
        if not sel:
            self.statusBar().showMessage(_("Select a clip first"), 4000)
            return
        self.before_edit()
        for c in sel:
            edge = "in" if abs(c.start - t) <= abs(c.end - t) else "out"
            p.apply_transition(c, edge, transitions.DEFAULT, 1.0)
        self.on_edited()

    def copy_selected(self):
        self.clipboard = [copy.deepcopy(c) for c in self.selected_clips()]
        if self.clipboard:
            self.statusBar().showMessage(f"{len(self.clipboard)} " + _("clip(s) copied"), 3000)

    def cut_selected(self):
        self.copy_selected()
        self.delete_selected()

    def paste(self):
        if not self.clipboard:
            return
        self.before_edit()
        base = min(c.start for c in self.clipboard)
        t = self.timeline.playhead
        links: dict[str, str] = {}
        new_ids = set()
        for src in self.clipboard:
            c = copy.deepcopy(src)
            c.id = new_id()
            c.start = t + (src.start - base)
            if c.link_id:
                c.link_id = links.setdefault(c.link_id, new_id())
            if c.track >= len(self.project.tracks):
                c.track = self.project.track_indices(
                    "video" if c.kind == "text" else "audio")[0]
            self.project._clear_space(c.track, c.start, c.end)
            self.project.clips.append(c)
            new_ids.add(c.id)
        self.timeline.selected = new_ids
        self.timeline.set_playhead(t + max(c.end for c in self.clipboard) - base)
        self.on_edited()

    def paste_attributes(self):
        """Kopyalanan klibin efektlerini, rengini, hareketini ve geçişlerini seçili kliplere yapıştırır."""
        if not self.clipboard or not self.timeline.selected:
            return
        attrs = ("opacity", "scale", "pos_x", "pos_y", "rotation", "brightness", "contrast",
                 "saturation", "volume", "fade_in", "fade_out")
        self.before_edit()
        for c in self.selected_clips():
            is_video = self.project.track_is_video(c.track)
            src = next((s for s in self.clipboard
                        if (s.track < len(self.project.tracks)
                            and self.project.track_is_video(s.track)) == is_video), None)
            if src is None:
                continue
            for a in attrs:
                setattr(c, a, getattr(src, a))
            if is_video and c.kind == "media":
                c.effects = copy.deepcopy(src.effects)
            c.keyframes = kf.retarget(copy.deepcopy(src.keyframes), src, c)
        self.on_edited()

    def unlink_selected(self):
        sel = self.selected_clips()
        if not sel:
            return
        self.before_edit()
        for c in sel:
            c.link_id = ""
        # Bağ koptuktan sonra yalnızca görüntü seçili kalsın; ses tek tıkla seçilebilir
        video = [c for c in sel if self.project.track_is_video(c.track)]
        self.timeline.selected = {c.id for c in (video or sel[:1])}
        self.statusBar().showMessage(_("Unlinked: video and audio can now be selected separately"),
                                     4000)
        self.on_edited()

    def link_selected(self):
        sel = self.selected_clips()
        if len(sel) < 2:
            return
        self.before_edit()
        lid = new_id()
        for c in sel:
            c.link_id = lid
        self.on_edited()

    def select_all(self):
        self.timeline.selected = {c.id for c in self.project.clips}
        self.timeline.update()
        self._on_selection()

    def add_title(self):
        text, ok = QInputDialog.getMultiLineText(self, _("Title"), _("Enter title text:"),
                                                 _("Your Title"))
        if ok and text.strip():
            self.before_edit()
            clip = self.project.add_text(text, self.timeline.playhead)
            self.timeline.selected = {clip.id}
            self.on_edited()

    def edit_title(self, clip):
        text, ok = QInputDialog.getMultiLineText(self, _("Title"), _("Enter title text:"),
                                                 clip.text)
        if ok:
            self.before_edit()
            clip.text = text
            clip.name = text.split("\n")[0][:24] or "Text"
            self.on_edited()

    def add_marker(self):
        self.before_edit()
        t = round(self.timeline.playhead * self.project.fps) / self.project.fps
        if t in self.project.markers:
            self.project.markers.remove(t)
        else:
            self.project.markers.append(t)
        self.on_edited()

    def add_track(self, kind):
        self.before_edit()
        self.project.add_track(kind)
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()

    def project_settings(self):
        dlg = SettingsDialog(self, self.project)
        if dlg.exec():
            self.before_edit()
            self.project.width = dlg.w.value() // 2 * 2
            self.project.height = dlg.h.value() // 2 * 2
            self.project.fps = dlg.fps.value()
            self.monitor.comp = None
            self.on_edited()

    # ------------------------------------------------------------ dosya
    def maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, _("Unsaved changes"), _("Save changes to the project?"),
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_project(self):
        if not self.maybe_save():
            return
        self.player.pause()
        self.project = Project()
        self.history = History()
        self.session.update(uid=self.project.uid, project_path=None, last_autosave=None)
        self.thumbnails.clear()
        self.timeline.waves.clear()
        self.timeline.selected.clear()
        self.monitor.reset()
        self.source.unload()
        self.source_marks.clear()
        self.cache.clear()
        self.worker.reset_sources()
        self.version += 1
        self.dirty = False
        self.bin.rebuild()
        self.timeline.set_playhead(0.0, emit=False)
        self.on_edited()

    def open_dialog(self):
        if not self.maybe_save():
            return
        last = self.settings.value("last_dir", os.path.expanduser("~"))
        path, _f = QFileDialog.getOpenFileName(self, _("Open Project…"), last,
                                               f"{_('Kurgu project')} (*.kurgu)")
        if path:
            self.open_project(path)

    def open_project(self, path):
        from ..safety import RemotePathsError
        try:
            try:
                p = Project.load(path)
            except RemotePathsError as e:
                p = Project.load(path, remote=self._ask_remote_paths(e.paths))
        except Exception as e:
            self._plain_box(QMessageBox.Critical, "Kurgu", str(e))
            return
        self.player.pause()
        self.project = p
        self.history = History()
        self.thumbnails.clear()
        self.timeline.waves.clear()
        self.timeline.selected.clear()
        self.monitor.reset()
        self.source.unload()
        self.source_marks.clear()
        self.cache.clear()
        self.worker.reset_sources()
        self.version += 1
        missing = [m.name for m in p.media.values() if not os.path.exists(m.path)]
        for m in p.media.values():  # proxy taşınmış/silinmiş olabilir
            if m.proxy and not os.path.exists(m.proxy):
                m.proxy = ""
        self.proxy_progress.clear()
        self.proxies.cancel()
        self.auto_proxy([m for m in p.media.values() if os.path.exists(m.path)])
        for m in p.media.values():
            self._make_thumb(m)
        self.dirty = p.recovered
        self.session.update(uid=p.uid, project_path=p.path, last_autosave=None)
        self.bin.rebuild()
        self.timeline.set_playhead(0.0, emit=False)
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()
        self.timeline.zoom_fit()
        if not p.recovered:
            self.settings.setValue("last_dir", os.path.dirname(path))
        else:
            self.statusBar().showMessage(
                _("Recovered project opened. Save it (Ctrl+S) to keep the changes."), 10000)
        if missing:
            self._plain_box(QMessageBox.Warning, _("Missing media"), "\n".join(missing))

    def _plain_box(self, icon, title, text):
        """Dosyadan gelen metin HTML olarak yorumlanmasın (ör. <img src=\\\\sunucu\\…>)."""
        box = QMessageBox(icon, title, text, QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)
        box.exec()

    def _ask_remote_paths(self, paths) -> str:
        """Projede ağ yolları var: açmak Windows'ta kimlik bilgisini o sunucuya gönderebilir."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(_("Network paths in project"))
        box.setTextFormat(Qt.PlainText)
        box.setText(_("This project refers to files on network locations. Opening them "
                      "contacts those computers and, on Windows, can send your login "
                      "credentials to them. Only allow this if you trust where the project "
                      "came from."))
        box.setDetailedText("\n".join(paths[:50]))
        allow = box.addButton(_("Allow network files"), QMessageBox.AcceptRole)
        safe = box.addButton(_("Open without them"), QMessageBox.RejectRole)
        box.setDefaultButton(safe)
        box.exec()
        return "allow" if box.clickedButton() is allow else "strip"

    def save(self) -> bool:
        if not self.project.path or getattr(self.project, "recovered", False):
            # Kurtarılan proje: hedefi her zaman kullanıcı onaylasın (Farklı Kaydet)
            return self.save_as()
        try:
            self.project.save(self.project.path)
        except Exception as e:
            QMessageBox.critical(self, "Kurgu", str(e))
            return False
        self.dirty = False
        self.project.recovered = False
        self.session.update(uid=self.project.uid, project_path=self.project.path)
        self._update_title()
        self.statusBar().showMessage(f"{_('Saved to')} {self.project.path}", 4000)
        return True

    def save_as(self) -> bool:
        from ..safety import valid_project_target
        last = self.settings.value("last_dir", os.path.expanduser("~"))
        default = self.project.path if valid_project_target(self.project.path) else \
            os.path.join(last, "project.kurgu")
        path, _f = QFileDialog.getSaveFileName(self, _("Save As…"), default,
                                               f"{_('Kurgu project')} (*.kurgu)")
        if not path:
            return False
        if not path.lower().endswith(".kurgu"):
            path += ".kurgu"
        self.project.path = path
        self.project.recovered = False
        return self.save()

    def export_dialog(self):
        if self.project.duration <= 0:
            QMessageBox.information(self, _("Nothing to export"), _("The timeline is empty."))
            return
        self.player.pause()
        base = (os.path.splitext(self.project.path)[0] if self.project.path
                else os.path.join(self.settings.value("last_dir", os.path.expanduser("~")),
                                  "video"))
        dlg = ExportDialog(self, self.project, base + ".mp4", self.settings)
        if not dlg.exec():
            return
        path, opts = dlg.values()
        if not path:
            return
        progress = QProgressDialog(_("Exporting…"), _("Cancel"), 0, 1000, self)
        progress.setWindowTitle(_("Export Video"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        worker = ExportWorker(self.project, path, opts)
        worker.progress.connect(progress.setValue)
        progress.canceled.connect(lambda: setattr(worker, "cancel", True))

        def done(ok, msg):
            progress.close()
            if ok:
                box = QMessageBox(self)
                box.setWindowTitle(_("Export finished"))
                box.setText(f"{_('Saved to')}\n{path}")
                open_btn = box.addButton(_("Open Containing Folder"), QMessageBox.ActionRole)
                box.addButton(QMessageBox.Ok)
                box.exec()
                if box.clickedButton() is open_btn:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            elif msg != "cancelled":
                QMessageBox.critical(self, _("Export failed"), msg)
            worker.deleteLater()
        worker.finished_ok.connect(done)
        self._export_worker = worker
        worker.start()

    def preferences(self):
        """Tercihler: önbellek, render dosyaları, donanım hızlandırma."""
        from .. import hw
        dlg = QDialog(self)
        dlg.setWindowTitle(_("Preferences…").rstrip("…"))
        form = QFormLayout(dlg)
        mem = QSpinBox()
        mem.setRange(256, 65536)
        mem.setSingleStep(256)
        mem.setSuffix(" MB")
        mem.setValue(int(self.settings.value("cache_mb", 1024)))
        form.addRow(_("Preview memory cache"), mem)
        disk = QDoubleSpinBox()
        disk.setRange(0, 2000)
        disk.setSuffix(" GB")
        disk.setValue(float(self.settings.value("render_disk_gb", 10)))
        disk.setToolTip(_("Rendered previews are kept on disk so they survive restarts. "
                          "0 = off."))
        form.addRow(_("Render files on disk"), disk)
        dec = QComboBox()
        dec.addItem(_("Automatic (GPU if available)"), "auto")
        dec.addItem(_("Off (CPU only)"), "off")
        dec.setCurrentIndex(max(0, dec.findData(self.settings.value("hw_decode", "auto"))))
        form.addRow(_("Hardware decoding"), dec)
        info = hw.summary()
        lines = [_("GPU decoders") + ": " + (", ".join(info["decoders"]) or _("none")),
                 "H.264 " + _("GPU encoder") + ": " + (info["h264"] or _("none")),
                 "H.265 " + _("GPU encoder") + ": " + (info["hevc"] or _("none"))]
        lab = QLabel("\n".join(lines))
        lab.setStyleSheet("color:#9aa0a6;")
        form.addRow(lab)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        self.settings.setValue("cache_mb", mem.value())
        self.settings.setValue("render_disk_gb", disk.value())
        self.settings.setValue("hw_decode", dec.currentData())
        self.cache.budget = mem.value() * 1024 * 1024
        self.cache.disk_budget = int(disk.value() * 1024 * 1024 * 1024)
        hw.MODE = dec.currentData()
        self.monitor.reset()
        self.worker.reset_sources()

    def export_frame(self):
        """Program monitöründeki kareyi PNG/JPEG olarak kaydet."""
        from ..exporter import export_frame
        from .timeline import timecode
        p = self.project
        tc = timecode(self.timeline.playhead, p.fps).replace(":", "-")
        base = (os.path.splitext(p.path)[0] if p.path else os.path.join(
            self.settings.value("last_dir", os.path.expanduser("~")), "frame"))
        path, _f = QFileDialog.getSaveFileName(self, _("Export Frame"), f"{base}_{tc}.png",
                                               "PNG (*.png);;JPEG (*.jpg)")
        if not path:
            return
        if not path.lower().endswith((".png", ".jpg", ".jpeg")):
            path += ".png"
        try:
            export_frame(p, self.timeline.playhead, path)
        except OSError as e:
            self._plain_box(QMessageBox.Warning, _("Export Frame"), str(e))
            return
        self.statusBar().showMessage(_("Saved to") + " " + path, 5000)

    # ------------------------------------------------------------ yardım
    def show_shortcuts(self):
        rows = [
            ("Space", _("Play / Pause")), ("J / K / L", "◀◀ / ■ / ▶▶"),
            ("← / →", _("Previous Frame") + " / " + _("Next Frame")),
            ("↑ / ↓", "◀| / |▶"), ("Home / End", _("Go to Start") + " / " + _("Go to End")),
            ("V", _("Selection Tool")), ("C", _("Razor Tool")), ("S", _("Snapping")),
            ("B", _("Ripple Edit Tool")), ("N", _("Rolling Edit Tool")),
            ("Y", _("Slip Tool")), ("U", _("Slide Tool")),
            ("I / O", _("Mark In") + " / " + _("Mark Out")),
            (", / .", _("Insert") + " / " + _("Overwrite")),
            ("Ctrl+K", _("Split at Playhead")), ("Delete", _("Delete")),
            ("Shift+Delete", _("Ripple Delete")), ("Ctrl+D", _("Apply Default Transition")),
            ("Ctrl+C / Ctrl+V", _("Copy") + " / " + _("Paste")),
            ("Ctrl+Alt+V", _("Paste Attributes")), ("Ctrl+Shift+K / Shift+K", _("Previous / next keyframe")),
            ("Enter", _("Render Effects")),
            ("Alt+← / Alt+→", _("Nudge clip 1 frame")), ("Alt+Shift+← / →", _("Nudge clip 5 frames")),
            (_("Alt while dragging"), _("Fine move (no magnet)")), ("Ctrl+Shift+D", _("Duplicate")),
            ("Ctrl+T", _("Add Title")), ("M", _("Add Marker")),
            ("+ / - / \\", _("Zoom In") + " / " + _("Zoom Out") + " / " + _("Zoom to Fit")),
            ("Ctrl+Z / Ctrl+Shift+Z", _("Undo") + " / " + _("Redo")),
            ("Ctrl+I", _("Import Media…")), ("Ctrl+M", _("Export…")),
        ]
        html = "<table cellpadding=4>" + "".join(
            f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        QMessageBox.information(self, _("Keyboard Shortcuts"), html)

    def about(self):
        QMessageBox.about(self, _("About Kurgu"),
                          f"<h2>Kurgu {__version__}</h2><p>{_('Free, open-source video editor.')}"
                          "</p><p>Python · PySide6 (Qt) · PyAV (FFmpeg)</p><p>MIT License</p>")

    # Pencerenin herhangi bir yerine bırakılan dosyalar içe aktarılır (zaman çizelgesi
    # ve medya kutusu kendi bırakma davranışlarını kullanır).
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        from .panels import _expand_dirs
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        projects = [p for p in paths if p.lower().endswith(".kurgu")]
        if projects:
            if self.maybe_save():
                self.open_project(projects[0])
        else:
            self.import_paths(_expand_dirs(paths))
        ev.acceptProposedAction()

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        self.player.pause()
        self.worker.shutdown()
        self.proxies.shutdown()
        self.autosave_timer.stop()
        self.session.end()  # düzgün kapanış: çökme kurtarma önerilmesin
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("monitor_split", self.monitors.saveState())
        ev.accept()
