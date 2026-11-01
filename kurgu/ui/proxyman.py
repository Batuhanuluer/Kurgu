"""Arka planda sırayla proxy üreten yönetici (arayüze sinyallerle bilgi verir)."""
from __future__ import annotations

import threading
from collections import deque

from PySide6.QtCore import QObject, Signal

from .. import proxy as px


class ProxyManager(QObject):
    progress = Signal(str, float)   # medya kimliği, 0..1
    finished = Signal(str, str, str)  # medya kimliği, proxy yolu, hata

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: deque = deque()
        self.active: str | None = None
        self.cancel_ids: set[str] = set()
        self.stop = False
        self.cond = threading.Condition()
        self.thread = threading.Thread(target=self._run, name="kurgu-proxy", daemon=True)
        self.thread.start()

    def enqueue(self, media):
        with self.cond:
            if media.id == self.active or any(m.id == media.id for m in self.queue):
                return
            self.cancel_ids.discard(media.id)
            # Kopya: arayüzdeki nesne değişse de üretim etkilenmesin
            from dataclasses import replace
            self.queue.append(replace(media))
            self.cond.notify()

    def cancel(self, media_id: str | None = None):
        with self.cond:
            if media_id is None:
                self.queue.clear()
                if self.active:
                    self.cancel_ids.add(self.active)
            else:
                self.queue = deque(m for m in self.queue if m.id != media_id)
                self.cancel_ids.add(media_id)

    def pending(self) -> int:
        return len(self.queue) + (1 if self.active else 0)

    def shutdown(self):
        self.cancel()
        with self.cond:
            self.stop = True
            self.cond.notify()

    def _run(self):
        while True:
            with self.cond:
                while not self.queue and not self.stop:
                    self.cond.wait()
                if self.stop:
                    return
                m = self.queue.popleft()
                self.active = m.id
            last = [-1.0]

            def prog(f, mid=m.id):
                if f - last[0] >= 0.01:
                    last[0] = f
                    try:
                        self.progress.emit(mid, f)
                    except RuntimeError:
                        pass
            try:
                path = px.make_proxy(m, progress=prog,
                                     cancelled=lambda mid=m.id: mid in self.cancel_ids or self.stop)
                err = ""
            except Exception as e:  # iptal ya da okunamayan dosya
                path, err = "", str(e)
            with self.cond:
                self.active = None
            try:
                self.finished.emit(m.id, path, err)
            except RuntimeError:
                pass
