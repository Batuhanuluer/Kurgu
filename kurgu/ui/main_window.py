"""Ana pencere: paneller, menüler, kısayollar, proje işlemleri."""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import QSettings, QThread, Qt, Signal, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QImage, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QInputDialog,
                               QMainWindow, QMessageBox, QProgressDialog, QSpinBox, QLineEdit,
                               QPushButton, QHBoxLayout, QWidget)

from .. import __version__
from ..i18n import _
from ..media import probe, thumbnail
from ..model import History, MediaItem, Project, new_id
from ..render import export
from .. import effects as fx
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

    def __init__(self, project: Project, path: str, w: int, h: int, crf: int):
        super().__init__()
        self.project = Project.from_dict(project.to_dict())  # iş parçacığına kopya
        self.path, self.w, self.h, self.crf = path, w, h, crf
        self.cancel = False

    def run(self):
        try:
            ok = export(self.project, self.path, self.w, self.h, self.crf,
                        progress=lambda f: self.progress.emit(int(f * 1000)),
                        cancelled=lambda: self.cancel)
            if not ok and os.path.exists(self.path):
                os.remove(self.path)
            self.finished_ok.emit(ok, "" if ok else "cancelled")
        except Exception as e:
            self.finished_ok.emit(False, str(e))


class ExportDialog(QDialog):
    def __init__(self, parent, project: Project, default_path: str):
        super().__init__(parent)
        self.setWindowTitle(_("Export Video"))
        self.project = project
        form = QFormLayout(self)
        self.path = QLineEdit(default_path)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.path, 1)
        rl.addWidget(browse)
        form.addRow(_("File"), row)
        self.res = QComboBox()
        pw, ph = project.width, project.height
        self.res_options = [(pw, ph)]
        for h in (2160, 1440, 1080, 720, 480):
            if h < ph:
                self.res_options.append((int(pw * h / ph) // 2 * 2, h))
        for w, h in self.res_options:
            self.res.addItem(f"{w} × {h}")
        form.addRow(_("Resolution"), self.res)
        self.quality = QComboBox()
        self.quality.addItems([_("High"), _("Medium"), _("Small file")])
        form.addRow(_("Quality"), self.quality)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _browse(self):
        p, _f = QFileDialog.getSaveFileName(self, _("Export Video"), self.path.text(),
                                            "MP4 (*.mp4)")
        if p:
            self.path.setText(p if p.lower().endswith(".mp4") else p + ".mp4")

    def values(self):
        w, h = self.res_options[self.res.currentIndex()]
        crf = (18, 23, 28)[self.quality.currentIndex()]
        return self.path.text(), w, h, crf


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
        self.player = Player(self)
        self.timeline_panel = TimelinePanel(self)
        self.timeline = self.timeline_panel.timeline
        self.monitor = ProgramMonitor(self)
        self.bin = MediaBin(self)
        self.inspector = Inspector(self)
        self.setCentralWidget(self.monitor)
        self.setDockNestingEnabled(True)
        self.effects_panel = EffectsPanel(self)
        bin_dock = self._dock(_("Project"), self.bin, Qt.LeftDockWidgetArea)
        fx_dock = self._dock(_("Effects"), self.effects_panel, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(bin_dock, fx_dock)
        bin_dock.raise_()
        self.clipboard: list = []
        self._dock(_("Inspector"), self.inspector, Qt.RightDockWidgetArea)
        self.inspector.setMinimumWidth(320)
        self.bin.setMinimumWidth(240)
        tl_dock = self._dock(_("Timeline"), self.timeline_panel, Qt.BottomDockWidgetArea)
        self.resizeDocks([tl_dock], [340], Qt.Vertical)
        self.player.position.connect(self._on_play_position)
        self.timeline.playhead_changed.connect(self._on_scrub)
        self.timeline.selection_changed.connect(self._on_selection)
        self.timeline.edited.connect(lambda: self.on_edited())
        self._build_menus()
        self.resize(1440, 900)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        if path:
            self.open_project(path)
        self._update_title()
        self.statusBar().showMessage(_("Ready"))
        self.monitor.refresh(0.0)

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
        a.triggered.connect(slot)
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

        s = mb.addMenu(_("Sequence"))
        self._act(s, _("Add Title"), self.add_title, "Ctrl+T")
        self._act(s, _("Add Marker"), self.add_marker, "M")
        self._act(s, _("Apply Default Transition"), self.apply_default_transition, "Ctrl+D")
        s.addSeparator()
        self._act(s, _("Add Video Track"), lambda: self.add_track("video"))
        self._act(s, _("Add Audio Track"), lambda: self.add_track("audio"))
        s.addSeparator()
        self._act(s, _("Project Settings…"), self.project_settings)

        v = mb.addMenu(_("View"))
        tools = QActionGroup(self)
        self.select_tool = self._act(v, _("Selection Tool"), lambda: self.set_tool("select"),
                                     "V", True)
        self.razor_tool = self._act(v, _("Razor Tool"), lambda: self.set_tool("razor"), "C",
                                    True)
        tools.addAction(self.select_tool)
        tools.addAction(self.razor_tool)
        self.select_tool.setChecked(True)
        snap = self._act(v, _("Snapping"), self.toggle_snap, "S", True)
        snap.setChecked(True)
        v.addSeparator()
        self._act(v, _("Zoom In"), lambda: self.timeline.zoom(1.4), ["=", "+"])
        self._act(v, _("Zoom Out"), lambda: self.timeline.zoom(1 / 1.4), "-")
        self._act(v, _("Zoom to Fit"), self.timeline.zoom_fit, "\\")

        h = mb.addMenu(_("Help"))
        self._act(h, _("Keyboard Shortcuts"), self.show_shortcuts, "F1")
        self._act(h, _("About Kurgu"), self.about)

        # Menüde görünmeyen oynatma kısayolları
        self._act(None, "play", self.player.toggle, "Space")
        self._act(None, "j", lambda: self.player.shuttle(-1), "J")
        self._act(None, "k", lambda: self.player.shuttle(0), "K")
        self._act(None, "l", lambda: self.player.shuttle(1), "L")
        self._act(None, "prev", lambda: self.step_frames(-1), "Left")
        self._act(None, "next", lambda: self.step_frames(1), "Right")
        self._act(None, "prev10", lambda: self.step_frames(-10), "Shift+Left")
        self._act(None, "next10", lambda: self.step_frames(10), "Shift+Right")
        self._act(None, "home", self.go_start, "Home")
        self._act(None, "end", self.go_end, "End")
        self._act(None, "up", lambda: self.jump_edit(-1), "Up")
        self._act(None, "down", lambda: self.jump_edit(1), "Down")
        for a in self.actions():
            a.setShortcutContext(Qt.WindowShortcut)

    # ------------------------------------------------------------ durum
    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Untitled"
        self.setWindowTitle(f"{'• ' if self.dirty else ''}{name} — Kurgu {__version__}")

    def before_edit(self):
        """Her değişiklikten önce çağrılır: geri alma geçmişine yazar."""
        self.history.push(self.project)
        self.dirty = True
        self._update_title()

    def on_edited(self, refresh_inspector=True):
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

    def _on_play_position(self, t):
        self.timeline.set_playhead(t, emit=False)
        self.timeline.ensure_visible(t)
        self.monitor.refresh(t)

    def _on_scrub(self, t):
        if self.player.playing:
            rate = self.player.rate
            self.player.pause()
            self.player.play(rate)
        else:
            self.monitor.refresh(t)

    def set_tool(self, tool):
        self.timeline.tool = tool
        self.statusBar().showMessage(_("Razor Tool") if tool == "razor" else _("Selection Tool"))

    def toggle_snap(self, on):
        self.timeline.snapping = on

    # ------------------------------------------------------------ gezinme
    def step_frames(self, n):
        if self.player.playing:
            self.player.pause()
        self.timeline.set_playhead(self.timeline.playhead + n / self.project.fps)
        self.timeline.ensure_visible(self.timeline.playhead)

    def go_start(self):
        self.timeline.set_playhead(0.0)
        self.timeline.ensure_visible(0.0)

    def go_end(self):
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
        self.project.clips = [c for c in self.project.clips if c.media_id not in ids]
        for i in ids:
            self.project.media.pop(i, None)
        self.bin.rebuild()
        self.on_edited()

    def place_media(self, m: MediaItem, t: float, track_hint: int | None):
        p = self.project
        vt = at = None
        if track_hint is not None and not p.tracks[track_hint].locked:
            if p.track_is_video(track_hint):
                vt = track_hint
            else:
                at = track_hint
        self.before_edit()
        made = p.place_media(m, t, vt, at)
        self.timeline.selected = {c.id for c in made}
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

    def remove_effect(self, clip, index):
        """index None ise tüm efektleri kaldırır."""
        self.before_edit()
        if index is None:
            clip.effects.clear()
        elif 0 <= index < len(clip.effects):
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
        self.on_edited()

    def unlink_selected(self):
        self.before_edit()
        for c in self.selected_clips():
            c.link_id = ""
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
        self.thumbnails.clear()
        self.timeline.waves.clear()
        self.timeline.selected.clear()
        self.monitor.reset()
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
        try:
            p = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Kurgu", str(e))
            return
        self.player.pause()
        self.project = p
        self.history = History()
        self.thumbnails.clear()
        self.timeline.waves.clear()
        self.timeline.selected.clear()
        self.monitor.reset()
        missing = [m.name for m in p.media.values() if not os.path.exists(m.path)]
        for m in p.media.values():
            self._make_thumb(m)
        self.dirty = False
        self.bin.rebuild()
        self.timeline.set_playhead(0.0, emit=False)
        self.timeline.setMinimumHeight(self.timeline.content_height())
        self.on_edited()
        self.timeline.zoom_fit()
        self.settings.setValue("last_dir", os.path.dirname(path))
        if missing:
            QMessageBox.warning(self, _("Missing media"), "\n".join(missing))

    def save(self) -> bool:
        if not self.project.path:
            return self.save_as()
        try:
            self.project.save(self.project.path)
        except Exception as e:
            QMessageBox.critical(self, "Kurgu", str(e))
            return False
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"{_('Saved to')} {self.project.path}", 4000)
        return True

    def save_as(self) -> bool:
        last = self.settings.value("last_dir", os.path.expanduser("~"))
        path, _f = QFileDialog.getSaveFileName(self, _("Save As…"),
                                               os.path.join(last, "project.kurgu"),
                                               f"{_('Kurgu project')} (*.kurgu)")
        if not path:
            return False
        if not path.lower().endswith(".kurgu"):
            path += ".kurgu"
        self.project.path = path
        return self.save()

    def export_dialog(self):
        if self.project.duration <= 0:
            QMessageBox.information(self, _("Nothing to export"), _("The timeline is empty."))
            return
        self.player.pause()
        base = (os.path.splitext(self.project.path)[0] if self.project.path
                else os.path.join(self.settings.value("last_dir", os.path.expanduser("~")),
                                  "video"))
        dlg = ExportDialog(self, self.project, base + ".mp4")
        if not dlg.exec():
            return
        path, w, h, crf = dlg.values()
        if not path:
            return
        progress = QProgressDialog(_("Exporting…"), _("Cancel"), 0, 1000, self)
        progress.setWindowTitle(_("Export Video"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        worker = ExportWorker(self.project, path, w, h, crf)
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

    # ------------------------------------------------------------ yardım
    def show_shortcuts(self):
        rows = [
            ("Space", _("Play / Pause")), ("J / K / L", "◀◀ / ■ / ▶▶"),
            ("← / →", _("Previous Frame") + " / " + _("Next Frame")),
            ("↑ / ↓", "◀| / |▶"), ("Home / End", _("Go to Start") + " / " + _("Go to End")),
            ("V", _("Selection Tool")), ("C", _("Razor Tool")), ("S", _("Snapping")),
            ("Ctrl+K", _("Split at Playhead")), ("Delete", _("Delete")),
            ("Shift+Delete", _("Ripple Delete")), ("Ctrl+D", _("Apply Default Transition")),
            ("Ctrl+C / Ctrl+V", _("Copy") + " / " + _("Paste")),
            ("Ctrl+Alt+V", _("Paste Attributes")), ("Ctrl+Shift+D", _("Duplicate")),
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

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        self.player.pause()
        self.settings.setValue("geometry", self.saveGeometry())
        ev.accept()
