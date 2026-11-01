"""Hata Bildir penceresi: metni göster, düzenlet, GitHub'da aç ya da panoya kopyala."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from .. import report
from ..i18n import _


class ReportDialog(QDialog):
    def __init__(self, parent=None, error: str = ""):
        super().__init__(parent)
        from ..app import LOG_PATH
        self.setWindowTitle(_("Report a Problem"))
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        intro = QLabel(_("Describe what you did and what went wrong. Nothing is sent "
                         "automatically: the report opens in your browser on GitHub and you "
                         "can edit it first. Personal folder names are hidden."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.title = QLineEdit()
        first = (error.strip().splitlines() or [""])[-1][:90]
        self.title.setText(first or "")
        self.title.setPlaceholderText(_("Short title, e.g. 'Export stops at 50%'"))
        lay.addWidget(self.title)
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText(_("What were you doing? What did you expect?"))
        self.desc.setFixedHeight(90)
        lay.addWidget(self.desc)
        lay.addWidget(QLabel(_("This will be included (you can edit it):")))
        self.body = QPlainTextEdit()
        self.error, self.log = error, report.log_tail(LOG_PATH)
        lay.addWidget(self.body, 1)
        self.desc.textChanged.connect(self._refresh)
        self._refresh()
        row = QHBoxLayout()
        copy = QPushButton(_("Copy to Clipboard"))
        copy.clicked.connect(self.copy)
        gh = QPushButton(_("Open on GitHub"))
        gh.setDefault(True)
        gh.clicked.connect(self.open_github)
        close = QPushButton(_("Close"))
        close.clicked.connect(self.reject)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(gh)
        lay.addLayout(row)

    def _refresh(self):
        self.body.setPlainText(report.build_body(self.desc.toPlainText(), self.error,
                                                 self.log))

    def url(self) -> str:
        return report.issue_url(self.title.text().strip() or "Hata bildirimi",
                                self.body.toPlainText())

    def copy(self):
        QGuiApplication.clipboard().setText(self.body.toPlainText())

    def open_github(self):
        self.copy()  # uzun günlük URL'ye sığmazsa panodan yapıştırılabilsin
        QDesktopServices.openUrl(QUrl(self.url()))
        self.accept()
