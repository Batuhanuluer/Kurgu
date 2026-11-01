"""Kurgu veri modeli: proje, medya, izler ve klipler.

Model, arayüzden ve medya motorundan tamamen bağımsızdır; böylece
test edilmesi ve komut satırından kullanılması kolaydır.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable

FORMAT_VERSION = 1
IMAGE_DEFAULT_DURATION = 5.0
TEXT_DEFAULT_DURATION = 5.0


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class MediaItem:
    path: str
    kind: str  # "video", "audio", "image"
    name: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    id: str = field(default_factory=new_id)

    def __post_init__(self):
        if not self.name:
            self.name = os.path.basename(self.path)


@dataclass
class Clip:
    track: int  # iz dizini (project.tracks içindeki sıra)
    start: float  # zaman çizelgesindeki başlangıç (sn)
    in_point: float  # kaynaktaki giriş noktası (sn)
    out_point: float  # kaynaktaki çıkış noktası (sn)
    media_id: str = ""  # metin klipleri için boş
    kind: str = "media"  # "media" veya "text"
    name: str = ""
    link_id: str = ""  # aynı videodan gelen görüntü+ses klipleri bağlıdır
    # Hareket / görünüm
    speed: float = 1.0
    opacity: float = 1.0
    scale: float = 1.0
    pos_x: float = 0.0  # proje pikseli cinsinden kayma
    pos_y: float = 0.0
    rotation: float = 0.0
    # Renk
    brightness: float = 0.0  # -1 .. 1
    contrast: float = 1.0  # 0 .. 3
    saturation: float = 1.0  # 0 .. 3
    # Ses
    volume: float = 1.0
    # Geçişler
    fade_in: float = 0.0
    fade_out: float = 0.0
    # Geçişler (Efektler panelinden): klibin başında / sonunda
    trans_in: str = ""
    trans_in_dur: float = 1.0
    trans_out: str = ""
    trans_out_dur: float = 1.0
    # Video efektleri: [{"type": ..., "enabled": bool, "params": {...}}]
    effects: list = field(default_factory=list)
    # Metin
    text: str = ""
    font_size: int = 72
    color: str = "#ffffff"
    id: str = field(default_factory=new_id)

    @property
    def duration(self) -> float:
        return max(0.0, (self.out_point - self.in_point) / max(self.speed, 1e-6))

    @property
    def end(self) -> float:
        return self.start + self.duration

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end

    def source_time(self, t: float) -> float:
        return self.in_point + (t - self.start) * self.speed

    def fade_factor(self, t: float) -> float:
        """Zaman çizelgesindeki t anında giriş/çıkış geçişlerinin çarpanı."""
        f = 1.0
        local = t - self.start
        if self.fade_in > 0 and local < self.fade_in:
            f *= max(0.0, local / self.fade_in)
        remaining = self.end - t
        if self.fade_out > 0 and remaining < self.fade_out:
            f *= max(0.0, remaining / self.fade_out)
        return f


@dataclass
class Track:
    kind: str  # "video" veya "audio"
    name: str = ""
    muted: bool = False  # ses izleri için sessiz, video izleri için gizli
    locked: bool = False
    id: str = field(default_factory=new_id)


def _from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


class Project:
    def __init__(self, width: int = 1920, height: int = 1080, fps: float = 30.0,
                 sample_rate: int = 48000):
        self.width = width
        self.height = height
        self.fps = fps
        self.sample_rate = sample_rate
        self.media: dict[str, MediaItem] = {}
        self.tracks: list[Track] = []
        self.clips: list[Clip] = []
        self.path: str | None = None
        self.markers: list[float] = []
        self.add_track("video")
        self.add_track("video")
        self.add_track("audio")
        self.add_track("audio")

    # ------------------------------------------------------------ izler
    def add_track(self, kind: str) -> Track:
        n = sum(1 for t in self.tracks if t.kind == kind) + 1
        track = Track(kind=kind, name=("V" if kind == "video" else "A") + str(n))
        self.tracks.append(track)
        return track

    def track_indices(self, kind: str) -> list[int]:
        return [i for i, t in enumerate(self.tracks) if t.kind == kind]

    def display_order(self) -> list[int]:
        """Zaman çizelgesinde yukarıdan aşağıya iz sırası (V3 V2 V1 | A1 A2)."""
        return list(reversed(self.track_indices("video"))) + self.track_indices("audio")

    # ------------------------------------------------------------ medya
    def add_media(self, item: MediaItem) -> MediaItem:
        for existing in self.media.values():
            if os.path.abspath(existing.path) == os.path.abspath(item.path):
                return existing
        self.media[item.id] = item
        return item

    def clip_by_id(self, cid: str) -> Clip | None:
        for c in self.clips:
            if c.id == cid:
                return c
        return None

    def clips_on_track(self, track: int) -> list[Clip]:
        return sorted((c for c in self.clips if c.track == track), key=lambda c: c.start)

    def linked(self, clip: Clip) -> list[Clip]:
        if not clip.link_id:
            return [clip]
        return [c for c in self.clips if c.link_id == clip.link_id]

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def track_is_video(self, idx: int) -> bool:
        return self.tracks[idx].kind == "video"

    # ------------------------------------------------------------ düzenleme
    def place_media(self, media: MediaItem, start: float, video_track: int | None = None,
                    audio_track: int | None = None) -> list[Clip]:
        """Medyayı zaman çizelgesine koyar; videonun sesi bağlı ses klibi olur."""
        dur = media.duration if media.kind != "image" else IMAGE_DEFAULT_DURATION
        link = new_id() if (media.has_video and media.has_audio) else ""
        made: list[Clip] = []
        if media.has_video or media.kind == "image":
            vt = video_track if video_track is not None else self.track_indices("video")[0]
            made.append(Clip(track=vt, start=start, in_point=0.0, out_point=dur,
                             media_id=media.id, name=media.name, link_id=link))
        if media.has_audio:
            at = audio_track if audio_track is not None else self.track_indices("audio")[0]
            made.append(Clip(track=at, start=start, in_point=0.0, out_point=dur,
                             media_id=media.id, name=media.name, link_id=link))
        for c in made:
            self._clear_space(c.track, c.start, c.end)
            self.clips.append(c)
        return made

    def add_text(self, text: str, start: float, track: int | None = None) -> Clip:
        vt = track if track is not None else self.track_indices("video")[-1]
        clip = Clip(track=vt, start=start, in_point=0.0, out_point=TEXT_DEFAULT_DURATION,
                    kind="text", name=text[:24] or "Text", text=text)
        self._clear_space(vt, clip.start, clip.end)
        self.clips.append(clip)
        return clip

    def _clear_space(self, track: int, start: float, end: float, ignore: Iterable[str] = ()):
        """Bir izde [start, end) aralığını boşaltır (Premiere'deki üzerine yazma)."""
        ignore = set(ignore)
        for c in list(self.clips):
            if c.track != track or c.id in ignore or c.end <= start or c.start >= end:
                continue
            if c.start >= start and c.end <= end:
                self.clips.remove(c)
            elif c.start < start and c.end > end:
                self._split(c, end)  # sağ parça kalır
                self._trim_end_to(c, start)
            elif c.start < start:
                self._trim_end_to(c, start)
            else:
                self._trim_start_to(c, end)

    def _trim_end_to(self, c: Clip, t: float):
        c.out_point = c.in_point + (t - c.start) * c.speed

    def _trim_start_to(self, c: Clip, t: float):
        c.in_point = c.source_time(t)
        c.start = t

    def _split(self, c: Clip, t: float) -> Clip | None:
        if not (c.start + 1e-6 < t < c.end - 1e-6):
            return None
        right = copy.deepcopy(c)
        right.id = new_id()
        right.in_point = c.source_time(t)
        right.start = t
        right.fade_in = 0.0
        right.trans_in = ""
        c.out_point = right.in_point
        c.fade_out = 0.0
        c.trans_out = ""
        self.clips.append(right)
        return right

    def split_at(self, t: float, clip_ids: Iterable[str] | None = None) -> list[Clip]:
        """t anında klipleri böler. clip_ids verilmezse kilitli olmayan tüm izler."""
        ids = set(clip_ids) if clip_ids else None
        targets = [c for c in self.clips if c.contains(t)
                   and not self.tracks[c.track].locked and (ids is None or c.id in ids)]
        # Bağlı klipleri birlikte böl ve yeni bağlantı kimliği ver.
        new_links: dict[str, str] = {}
        result = []
        for c in targets:
            right = self._split(c, t)
            if right is None:
                continue
            if c.link_id:
                right.link_id = new_links.setdefault(c.link_id, new_id())
            result.append(right)
        return result

    def delete(self, clip_ids: Iterable[str], ripple: bool = False):
        ids = set(clip_ids)
        for c in list(self.clips):
            if c.id in ids:
                ids.update(x.id for x in self.linked(c))
        removed = [c for c in self.clips if c.id in ids]
        self.clips = [c for c in self.clips if c.id not in ids]
        if ripple:
            for r in sorted(removed, key=lambda c: -c.start):
                gap = r.duration
                for c in self.clips:
                    if c.track == r.track and c.start >= r.end - 1e-6:
                        c.start -= gap

    def move(self, clip: Clip, new_start: float, new_track: int | None = None):
        """Klibi (ve bağlı kliplerini) taşır; hedef alandaki klipler üzerine yazılır."""
        delta = max(new_start, 0.0) - clip.start
        group = self.linked(clip)
        min_start = min(c.start for c in group)
        if min_start + delta < 0:
            delta = -min_start
        track_shift = 0
        if new_track is not None and new_track != clip.track:
            if self.tracks[new_track].kind == self.tracks[clip.track].kind:
                same = self.track_indices(self.tracks[clip.track].kind)
                track_shift = same.index(new_track) - same.index(clip.track)
        ids = {c.id for c in group}
        for c in group:
            c.start += delta
            if c is clip or self.tracks[c.track].kind == self.tracks[clip.track].kind:
                if track_shift:
                    same = self.track_indices(self.tracks[c.track].kind)
                    i = min(max(same.index(c.track) + track_shift, 0), len(same) - 1)
                    c.track = same[i]
        for c in group:
            self._clear_space(c.track, c.start, c.end, ignore=ids)

    def max_out(self, clip: Clip) -> float:
        if clip.kind == "text":
            return 3600.0
        m = self.media.get(clip.media_id)
        if m is None or m.kind == "image":
            return 3600.0
        return m.duration

    def trim(self, clip: Clip, edge: str, t: float):
        """Klibin sol ('in') veya sağ ('out') kenarını zaman çizelgesinde t'ye çeker."""
        for c in self.linked(clip):
            if edge == "in":
                t2 = min(t, c.end - 1.0 / self.fps)
                src = c.source_time(t2)
                if src < 0:
                    t2 = c.start - c.in_point / c.speed
                    src = 0.0
                c.start, c.in_point = t2, src
            else:
                t2 = max(t, c.start + 1.0 / self.fps)
                out = c.in_point + (t2 - c.start) * c.speed
                c.out_point = min(out, self.max_out(c))
        ids = {c.id for c in self.linked(clip)}
        for c in self.linked(clip):
            self._clear_space(c.track, c.start, c.end, ignore=ids)

    def set_speed(self, clip: Clip, speed: float):
        speed = min(max(speed, 0.1), 16.0)
        for c in self.linked(clip):
            c.speed = speed

    # ------------------------------------------------------------ geçişler
    def prev_adjacent(self, clip: Clip) -> Clip | None:
        for c in self.clips:
            if c.track == clip.track and c is not clip and abs(c.end - clip.start) < 1e-3:
                return c
        return None

    def next_adjacent(self, clip: Clip) -> Clip | None:
        for c in self.clips:
            if c.track == clip.track and c is not clip and abs(c.start - clip.end) < 1e-3:
                return c
        return None

    def trans_in_info(self, clip: Clip):
        """Klibin başındaki geçiş: (önceki klip veya None, pencere başı, süre) ya da None.

        Komşu klip varsa geçiş kesmenin ortasına yerleşir (Premiere varsayılanı);
        yoksa klibin başından itibaren tek taraflı çalışır.
        """
        if not clip.trans_in:
            return None
        prev = self.prev_adjacent(clip)
        if prev is not None:
            d = max(min(clip.trans_in_dur, clip.duration, prev.duration), 1e-3)
            return prev, clip.start - d / 2, d
        d = max(min(clip.trans_in_dur, clip.duration), 1e-3)
        return None, clip.start, d

    def trans_out_info(self, clip: Clip):
        """Klibin sonundaki tek taraflı geçiş: (pencere başı, süre) ya da None."""
        if not clip.trans_out:
            return None
        nxt = self.next_adjacent(clip)
        if nxt is not None and nxt.trans_in:
            return None  # sonraki klibin geçişi bu kesmeyi zaten kapsıyor
        d = max(min(clip.trans_out_dur, clip.duration), 1e-3)
        return clip.end - d, d

    def apply_transition(self, clip: Clip, edge: str, kind: str, duration: float = 1.0):
        """Premiere gibi: kenara geçiş uygula. Sağ kenarda komşu varsa onun girişine yazılır.

        kind boş ise geçiş kaldırılır. Bağlı klipler (ses) de aynı geçişi alır.
        """
        for c in self.linked(clip):
            if edge == "in":
                c.trans_in, c.trans_in_dur = kind, duration
            else:
                nxt = self.next_adjacent(c)
                if nxt is not None:
                    nxt.trans_in, nxt.trans_in_dur = kind, duration
                    c.trans_out = ""
                else:
                    c.trans_out, c.trans_out_dur = kind, duration

    def snap_points(self, exclude: Iterable[str] = ()) -> list[float]:
        ex = set(exclude)
        pts = [0.0] + list(self.markers)
        for c in self.clips:
            if c.id not in ex:
                pts += [c.start, c.end]
        return pts

    # ------------------------------------------------------------ kayıt
    def to_dict(self) -> dict:
        return {
            "app": "Kurgu",
            "format_version": FORMAT_VERSION,
            "settings": {"width": self.width, "height": self.height, "fps": self.fps,
                         "sample_rate": self.sample_rate},
            "media": [asdict(m) for m in self.media.values()],
            "tracks": [asdict(t) for t in self.tracks],
            "clips": [asdict(c) for c in self.clips],
            "markers": list(self.markers),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        s = data.get("settings", {})
        p = cls(s.get("width", 1920), s.get("height", 1080), s.get("fps", 30.0),
                s.get("sample_rate", 48000))
        p.tracks = [_from_dict(Track, t) for t in data.get("tracks", [])] or p.tracks
        p.media = {}
        for m in data.get("media", []):
            item = _from_dict(MediaItem, m)
            p.media[item.id] = item
        p.clips = [_from_dict(Clip, c) for c in data.get("clips", [])]
        p.markers = list(data.get("markers", []))
        return p

    def save(self, path: str):
        data = self.to_dict()
        base = os.path.dirname(os.path.abspath(path))
        for m in data["media"]:  # taşınabilirlik için göreli yol da sakla
            try:
                m["relpath"] = os.path.relpath(m["path"], base)
            except ValueError:  # Windows'ta farklı sürücüler
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        self.path = path

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        base = os.path.dirname(os.path.abspath(path))
        for m in data.get("media", []):
            if not os.path.exists(m["path"]) and m.get("relpath"):
                cand = os.path.normpath(os.path.join(base, m["relpath"]))
                if os.path.exists(cand):
                    m["path"] = cand
        p = cls.from_dict(data)
        p.path = path
        return p


class History:
    """Anlık görüntü tabanlı geri al / yinele."""

    def __init__(self, limit: int = 200):
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.limit = limit

    def push(self, project: Project):
        self.undo_stack.append(project.to_dict())
        if len(self.undo_stack) > self.limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, project: Project) -> Project | None:
        if not self.undo_stack:
            return None
        self.redo_stack.append(project.to_dict())
        return self._restore(project, self.undo_stack.pop())

    def redo(self, project: Project) -> Project | None:
        if not self.redo_stack:
            return None
        self.undo_stack.append(project.to_dict())
        return self._restore(project, self.redo_stack.pop())

    @staticmethod
    def _restore(current: Project, data: dict) -> Project:
        p = Project.from_dict(data)
        p.path = current.path
        return p
