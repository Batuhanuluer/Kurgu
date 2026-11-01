"""Efektler paneli: video efektleri ve geçişler; arama, sürükle-bırak, çift tıkla uygula."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import (QAbstractItemView, QLineEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import effects as fx
from .. import transitions
from ..i18n import _

EFFECT_MIME = "application/x-kurgu-effect"


class _Tree(QTreeWidget):
    def mimeData(self, items):
        md = QMimeData()
        if items and items[0].data(0, Qt.UserRole):
            md.setData(EFFECT_MIME, items[0].data(0, Qt.UserRole).encode())
        return md

    def mimeTypes(self):
        return [EFFECT_MIME]


class EffectsPanel(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search effects…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.tree = _Tree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.itemDoubleClicked.connect(self._apply)
        self.tree.setToolTip(_("Drag onto a clip, or double-click to apply to the selection"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)
        self._build()

    def _build(self):
        root_v = QTreeWidgetItem([_("Video Effects")])
        root_t = QTreeWidgetItem([_("Video Transitions")])
        for root in (root_v, root_t):
            root.setFlags(root.flags() & ~Qt.ItemIsDragEnabled)
            self.tree.addTopLevelItem(root)
        for cat in fx.CATEGORIES:
            node = QTreeWidgetItem(root_v, ["📁 " + _(cat)])
            node.setFlags(node.flags() & ~Qt.ItemIsDragEnabled)
            for d in fx.EFFECTS.values():
                if d.category == cat:
                    it = QTreeWidgetItem(node, ["✦ " + _(d.name)])
                    it.setData(0, Qt.UserRole, "effect:" + d.id)
        for cat in transitions.CATEGORIES:
            node = QTreeWidgetItem(root_t, ["📁 " + _(cat)])
            node.setFlags(node.flags() & ~Qt.ItemIsDragEnabled)
            for kid, name, c in transitions.TRANSITIONS:
                if c == cat:
                    it = QTreeWidgetItem(node, ["⇄ " + _(name)])
                    it.setData(0, Qt.UserRole, "transition:" + kid)
        self.tree.expandAll()

    def _filter(self, text):
        text = text.strip().lower()

        def visit(item) -> bool:
            own = text in item.text(0).lower()
            child_match = False
            for i in range(item.childCount()):
                child_match |= visit(item.child(i))
            show = not text or own or child_match
            item.setHidden(not show)
            return own or child_match
        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
        self.tree.expandAll()

    def _apply(self, item):
        key = item.data(0, Qt.UserRole)
        if key:
            self.main.apply_effect_key(key, None)
