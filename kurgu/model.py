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
    codec: str = ""
    bit_depth: int = 8
    rotation: int = 0  # görüntülerken saat yönünün tersine 90°'lik dönüş sayısı (telefonlar)
    proxy: str = ""  # hafif çalışma kopyası (önizleme için), boşsa yok
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
    # Anahtar kareler: {"özellik": [[kaynak_zamanı, değer, enterpolasyon], ...]}
    keyframes: dict = field(default_factory=dict)
    # Metin
    text: str = ""
    font_size: int = 72
    color: str = "#ffffff"
    # Altyazı kelime zamanları (vurgulu altyazı): [[başla, bitir, kelime], ...] klibin
    # yerel zamanında (in_point ile aynı ölçek)
    words: list = field(default_factory=list)
    # Çoklu kamera: mc = grup kimliği; in/out grup zamanındadır, gösterilen kamera mc_angle
    mc: str = ""
    mc_angle: int = 0
    # Karıştırma modu (masks.BLEND_MODES), maskeler ve iz matı
    blend: str = "normal"
    masks: list = field(default_factory=list)
    matte: dict = field(default_factory=dict)  # {"track": iz, "type": alpha|luma, "invert"}
    audio_fx: list = field(default_factory=list)  # ses efektleri (audio.AUDIO_FX)
    nest: str = ""  # iç içe sekans kimliği (kind == "nest")
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
    captions: bool = False  # altyazı izi (video türünde; klipleri "caption")
    volume_db: float = 0.0  # ses izi düzeyi (mikser)
    pan: float = 0.0        # -1 sol .. 1 sağ
    solo: bool = False
    style: dict = field(default_factory=dict)  # altyazı izi stili (captions.STYLE_DEFAULTS)
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
        self.uid = new_id()  # otomatik kayıtları projeyle eşleştirmek için
        self.recovered = False  # otomatik kayıttan açıldıysa
        self.markers: list[float] = []
        # Çoklu kamera grupları: {kimlik: {"name", "angles": [{"media_id", "offset"}],
        #                                  "audio": açı_indeksi}}
        self.multicams: dict[str, dict] = {}
        # Sekans giriş/çıkış işaretleri (I/O): dışa aktarma aralığı
        self.seq_in: float | None = None
        self.seq_out: float | None = None
        self.master_db = 0.0  # ana çıkış düzeyi
        # İç içe sekanslar: {kimlik: {"name", "settings", "tracks", "clips", "markers", "hash"}}
        self.nests: dict[str, dict] = {}
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
    def _new_clips(self, media: MediaItem, start: float, video_track, audio_track,
                   in_point, out_point, video=True, audio=True) -> list[Clip]:
        full = media.duration if media.kind != "image" else IMAGE_DEFAULT_DURATION
        a = 0.0 if in_point is None else max(0.0, in_point)
        b = full if out_point is None else min(out_point, full if media.kind != "image" else 3600)
        b = max(b, a + 1.0 / self.fps)
        want_v = video and (media.has_video or media.kind == "image")
        want_a = audio and media.has_audio
        link = new_id() if (want_v and want_a) else ""
        made: list[Clip] = []
        if want_v:
            vt = video_track if video_track is not None else self.track_indices("video")[0]
            made.append(Clip(track=vt, start=start, in_point=a, out_point=b,
                             media_id=media.id, name=media.name, link_id=link))
        if want_a:
            at = audio_track if audio_track is not None else self.track_indices("audio")[0]
            made.append(Clip(track=at, start=start, in_point=a, out_point=b,
                             media_id=media.id, name=media.name, link_id=link))
        return made

    def place_media(self, media: MediaItem, start: float, video_track: int | None = None,
                    audio_track: int | None = None, in_point: float | None = None,
                    out_point: float | None = None, video: bool = True,
                    audio: bool = True) -> list[Clip]:
        """Medyayı zaman çizelgesine koyar (Üzerine Yaz); videonun sesi bağlı ses klibi olur.
        in_point/out_point verilirse kaynağın yalnızca o aralığı kullanılır."""
        made = self._new_clips(media, start, video_track, audio_track, in_point, out_point,
                               video, audio)
        for c in made:
            self._clear_space(c.track, c.start, c.end)
            self.clips.append(c)
        return made

    def insert_media(self, media: MediaItem, start: float, video_track: int | None = None,
                     audio_track: int | None = None, in_point: float | None = None,
                     out_point: float | None = None, video: bool = True,
                     audio: bool = True) -> list[Clip]:
        """Ekle (Premiere ","): oynatma kafasındaki klipleri böler, sonrasını iterek araya
        koyar; hiçbir şey silinmez. Tüm kilitsiz izler birlikte kayar (senkron korunur)."""
        made = self._new_clips(media, start, video_track, audio_track, in_point, out_point,
                               video, audio)
        if not made:
            return made
        d = made[0].duration
        eps = self.adj_tol
        unlocked = [i for i, tr in enumerate(self.tracks) if not tr.locked]
        self.split_at(start, [c.id for c in self.clips
                              if c.track in unlocked and c.start + eps < start < c.end - eps])
        for c in self.clips:
            if c.track in unlocked and c.start >= start - eps:
                c.start += d
        self.markers = [m + d if m >= start - eps else m for m in self.markers]
        self.clips.extend(made)
        return made

    def in_out_range(self) -> tuple[float, float]:
        """Sekans I/O aralığı (işaret yoksa tüm sekans)."""
        a = self.seq_in if self.seq_in is not None else 0.0
        b = self.seq_out if self.seq_out is not None else self.duration
        if b <= a:
            return 0.0, self.duration
        return a, min(b, max(self.duration, b))

    # ------------------------------------------------------------ iç içe sekanslar
    def visual_size(self, clip: Clip):
        """Görsel klibin kendi kare boyutu (medya ya da iç içe sekans)."""
        if clip.kind == "nest":
            n = self.nests.get(clip.nest) or {}
            st = n.get("settings", {})
            return st.get("width", self.width), st.get("height", self.height)
        m = self.media.get(clip.media_id)
        return (m.width, m.height) if m is not None and m.width else (None, None)

    def nest_duration(self, nid: str) -> float:
        n = self.nests.get(nid)
        if not n:
            return 0.0
        end = 0.0
        for c in n.get("clips", []):
            sp = max(float(c.get("speed", 1.0)), 1e-6)
            end = max(end, float(c.get("start", 0)) +
                      (float(c.get("out_point", 0)) - float(c.get("in_point", 0))) / sp)
        return end

    def nest_project(self, nid: str) -> "Project":
        """İç içe sekansı düzenlenebilir/çizilebilir bir Project olarak açar.
        Medya, çoklu kamera ve iç içe sekans sözlükleri ana projeyle ortaktır."""
        n = self.nests[nid]
        child = Project.from_dict({"settings": n.get("settings", {}), "tracks": n["tracks"],
                                   "clips": n["clips"], "markers": n.get("markers", [])})
        child.media = self.media
        child.multicams = self.multicams
        child.nests = self.nests
        child.nest_id = nid
        return child

    @staticmethod
    def _content_hash(d: dict) -> str:
        import hashlib
        data = json.dumps([d.get("settings"), d.get("tracks"), d.get("clips")],
                          sort_keys=True, default=str)
        return hashlib.blake2b(data.encode(), digest_size=10).hexdigest()

    def store_nest(self, nid: str, child: "Project", name: str | None = None):
        """İçeride yapılan düzenlemeleri ana projeye yazar (medya ve diğer sekanslar dahil)."""
        d = child.to_dict()
        old = self.nests.get(nid, {})
        n = {"name": name or old.get("name", "Nest"), "settings": d["settings"],
             "tracks": d["tracks"], "clips": d["clips"], "markers": d["markers"]}
        n["hash"] = self._content_hash(n)
        if child.media is not self.media:  # geri al sonrası kopya olabilir
            for k, m in child.media.items():
                self.media.setdefault(k, m)
        if child.nests is not self.nests:
            for k, v in child.nests.items():
                if k != nid:
                    self.nests[k] = v
        if child.multicams is not self.multicams:
            self.multicams.update(child.multicams)
        self.nests[nid] = n

    def nest_clips(self, clip_ids, name: str = "Nest") -> list[Clip]:
        """Premiere "İç İçe Sekans": seçili klipleri (bağlılarıyla) yeni bir sekansa taşır ve
        zaman çizelgesinde onların yerine tek bir (görüntü + ses) iç içe klip koyar."""
        ids = set(clip_ids)
        for c in list(self.clips):
            if c.id in ids:
                ids |= {x.id for x in self.linked(c)}
        group = [c for c in self.clips if c.id in ids]
        if not group:
            return []
        t0 = min(c.start for c in group)
        t1 = max(c.end for c in group)
        nid = new_id()
        inner = Project(self.width, self.height, self.fps, self.sample_rate)
        inner.tracks = copy.deepcopy(self.tracks)  # iz düzeni aynen korunur
        for t in inner.tracks:
            t.locked = False
        for c in group:
            k = copy.deepcopy(c)
            k.start -= t0
            k.link_id = k.link_id  # bağlar iç sekansta da geçerli
            inner.clips.append(k)
        inner.media = self.media
        self.nests[nid] = {}
        self.store_nest(nid, inner, name)
        self.clips = [c for c in self.clips if c.id not in ids]
        vis = [c for c in group if self.track_is_video(c.track)]
        aud = [c for c in group if not self.track_is_video(c.track)]
        link = new_id() if (vis and aud) else ""
        made = []
        for lst in (vis, aud):
            if not lst:
                continue
            tr = min(c.track for c in lst)
            nc = Clip(track=tr, start=t0, in_point=0.0, out_point=t1 - t0, kind="nest",
                      name=name, link_id=link, nest=nid)
            self._clear_space(nc.track, nc.start, nc.end)
            self.clips.append(nc)
            made.append(nc)
        return made

    def unnest(self, clip: Clip) -> list[Clip]:
        """İç içe klibi açar: içindeki klipleri (kırpılan aralıkla) ana sekansa geri koyar."""
        if clip.kind != "nest" or clip.nest not in self.nests:
            return []
        child = self.nest_project(clip.nest)
        a, b = clip.in_point, clip.out_point
        out = []
        group = [x for x in self.linked(clip) if x.kind == "nest" and x.nest == clip.nest]
        self.clips = [c for c in self.clips if c not in group]
        for c in child.clips:
            if c.end <= a or c.start >= b or c.track >= len(self.tracks):
                continue
            k = copy.deepcopy(c)
            if k.start < a:
                k.in_point += (a - k.start) * k.speed
                k.start = a
            if k.end > b:
                k.out_point -= (k.end - b) * k.speed
            k.start = clip.start + (k.start - a)
            k.id = new_id()
            self._clear_space(k.track, k.start, k.end)
            self.clips.append(k)
            out.append(k)
        return out

    # ------------------------------------------------------------ altyazılar
    def caption_tracks(self) -> list[int]:
        return [i for i, t in enumerate(self.tracks) if t.kind == "video" and t.captions]

    def caption_track(self, create: bool = True) -> int | None:
        ct = self.caption_tracks()
        if ct:
            return ct[0]
        if not create:
            return None
        n = len(self.tracks)
        tr = Track(kind="video", name="CC", captions=True)
        # Video izlerinin en üstüne (listede son video izinden sonra)
        vids = self.track_indices("video")
        pos = (vids[-1] + 1) if vids else 0
        self.tracks.insert(pos, tr)
        for c in self.clips:  # araya iz girdi: sonraki izlerin indeksleri kayar
            if c.track >= pos:
                c.track += 1
        return pos if pos < n + 1 else n

    def add_captions(self, cues, track: int | None = None, replace: bool = False) -> list[Clip]:
        """Altyazı bloklarını ([Cue] ya da (başla, bitir, metin)) altyazı izine ekler."""
        ti = self.caption_track() if track is None else track
        if replace:
            self.clips = [c for c in self.clips if c.track != ti]
        made = []
        for cue in cues:
            a, b, text = (cue.start, cue.end, cue.text) if hasattr(cue, "text") else cue[:3]
            words = getattr(cue, "words", None) or (cue[3] if not hasattr(cue, "text")
                                                    and len(cue) > 3 else None) or []
            if b - a < 1.0 / self.fps:
                continue
            c = Clip(track=ti, start=float(a), in_point=0.0, out_point=float(b - a),
                     kind="caption", name=text.replace("\n", " ")[:40], text=text,
                     words=[[float(w[0]), float(w[1]), str(w[2])] for w in words])
            self._clear_space(ti, c.start, c.end)
            self.clips.append(c)
            made.append(c)
        return made

    def caption_cues(self, track: int | None = None):
        from .captions import Cue
        tis = [track] if track is not None else self.caption_tracks()
        return [Cue(c.start, c.end, c.text) for c in sorted(self.clips, key=lambda c: c.start)
                if c.track in tis and c.kind == "caption"]

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
        if clip.kind in ("text", "caption"):
            return 3600.0
        if clip.kind == "nest":
            return self.nest_duration(clip.nest)
        if clip.mc and clip.mc in self.multicams:
            return self.multicam_duration(self.multicams[clip.mc])
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
    @property
    def adj_tol(self) -> float:
        return 0.5 / max(self.fps, 1.0)  # yarım kare: kayan nokta kaymalarına dayanıklı

    def prev_adjacent(self, clip: Clip) -> Clip | None:
        for c in self.clips:
            if c.track == clip.track and c is not clip and abs(c.end - clip.start) < self.adj_tol:
                return c
        return None

    def next_adjacent(self, clip: Clip) -> Clip | None:
        for c in self.clips:
            if c.track == clip.track and c is not clip and abs(c.start - clip.end) < self.adj_tol:
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

    def is_through_edit(self, a: Clip | None, b: Clip | None) -> bool:
        """a'nın bittiği yerde b aynı çekimin tam devamı mı? (jiletle kesilmiş, arası silinmemiş)

        Böyle bir kesmede iki taraf aynı görüntüyü gösterir; silme/iris gibi geçişler görünmez.
        """
        if a is None or b is None or a.kind != "media" or a.media_id != b.media_id:
            return False
        return abs(a.out_point - b.in_point) < 1.0 / max(self.fps, 1.0) and \
            abs(a.speed - b.speed) < 1e-6

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

    # ------------------------------------------------------------ boşluklar
    def gap_at(self, track: int, t: float):
        """İzde t anındaki boşluk: (başlangıç, bitiş) — yalnızca sağında klip varsa."""
        eps = self.adj_tol
        clips = self.clips_on_track(track)
        if any(c.start - eps <= t < c.end + eps for c in clips):
            return None
        after = [c for c in clips if c.start >= t]
        if not after:
            return None
        end = min(c.start for c in after)
        start = max((c.end for c in clips if c.end <= t + eps), default=0.0)
        return (start, end) if end - start > eps else None

    def close_gap(self, track: int, start: float, end: float) -> bool:
        """Boşluğu siler; sağdaki klipler sola kayar (Premiere: Ripple Delete).

        Diğer izler de o aralıkta boşsa tüm izler birlikte kayar (senkron korunur).
        Değilse yalnızca bu izdeki klipler ve onlara bağlı klipler kayar; bağlı
        klipler başka bir klibin üzerine binecekse işlem yapılmaz ve False döner.
        """
        d = end - start
        eps = self.adj_tol
        if d <= eps:
            return False
        unlocked = [i for i, tr in enumerate(self.tracks) if not tr.locked]
        if track not in unlocked:
            return False
        others_free = all(
            not (c.start < end - eps and c.end > start + eps)
            for c in self.clips if c.track != track and c.track in unlocked)
        if others_free:
            movers = [c for c in self.clips if c.track in unlocked and c.start >= end - eps]
        else:
            movers = []
            for c in self.clips:
                if c.track == track and c.start >= end - eps:
                    for x in self.linked(c):
                        if x not in movers:
                            movers.append(x)
            ids = {c.id for c in movers}
            for c in movers:
                if c.track == track:
                    continue
                if self.tracks[c.track].locked:
                    return False
                for o in self.clips:
                    if o.track == c.track and o.id not in ids and \
                            o.start < c.end - d - eps and o.end > c.start - d + eps:
                        return False
        for c in movers:
            c.start = max(0.0, c.start - d)
        for i, m in enumerate(self.markers):
            if m >= end - eps and others_free:
                self.markers[i] = m - d
        return True

    # ------------------------------------------------------------ kesme araçları
    def _frame(self) -> float:
        return 1.0 / self.fps

    def _shift_after(self, t: float, d: float, skip: set):
        """t ve sonrasında başlayan klipleri (kilitsiz izlerde) d kadar kaydır."""
        eps = self.adj_tol
        for c in self.clips:
            if c.id not in skip and not self.tracks[c.track].locked and c.start >= t - eps:
                c.start += d

    def ripple_trim(self, clip: Clip, edge: str, t: float) -> float:
        """Ripple (B): kenarı t'ye çek, sonraki klipler boşluk bırakmadan kaysın.
        Döndürülen değer: zaman çizelgesindeki kayma miktarı."""
        group = self.linked(clip)
        ids = {c.id for c in group}
        f = self._frame()
        old_end = clip.end
        if edge == "out":
            max_end = clip.start + (self.max_out(clip) - clip.in_point) / clip.speed
            new_end = min(max(t, clip.start + f), max_end)
            d = new_end - old_end
            for c in group:
                c.out_point = c.in_point + (new_end - c.start) * c.speed
        else:  # in: klip yerinde kalır, içeriği ve sonu kayar
            d = t - clip.start
            d = max(d, -clip.in_point / clip.speed)  # kaynağın başından öteye gidemez
            d = min(d, clip.duration - f)
            for c in group:
                c.in_point += d * c.speed
            d = -d
        self._shift_after(old_end, d, ids)
        return d

    def _roll_pair(self, left: Clip, right: Clip, t: float) -> float:
        f = self._frame()
        lo = max(left.start + f, right.start - right.in_point / right.speed)
        hi = min(right.end - f, left.start + (self.max_out(left) - left.in_point) / left.speed)
        t = min(max(t, lo), hi)
        left.out_point = left.in_point + (t - left.start) * left.speed
        right.in_point = right.source_time(t)
        right.start = t
        return t

    def roll(self, left: Clip, t: float) -> float:
        """Roll (N): iki bitişik klibin arasındaki kesmeyi t'ye taşı (toplam süre değişmez)."""
        right = self.next_adjacent(left)
        if right is None:
            return left.end
        t = self._roll_pair(left, right, t)
        for lp in self.linked(left):  # bağlı ses/görüntü de aynı kesmeyi kaydırsın
            if lp is left:
                continue
            rp = self.next_adjacent(lp)
            if rp is not None and right.link_id and rp.link_id == right.link_id:
                self._roll_pair(lp, rp, t)
        return t

    def slip(self, clip: Clip, d_src: float) -> float:
        """Slip (Y): klibin yeri ve süresi sabit; kaynakta d_src saniye kaydır."""
        group = self.linked(clip)
        lo = max(-c.in_point for c in group)
        hi = min(self.max_out(c) - c.out_point for c in group)
        d = min(max(d_src, lo), hi)
        for c in group:
            c.in_point += d
            c.out_point += d
        return d

    def slide(self, clip: Clip, dt: float) -> float:
        """Slide (U): klibi komşuları arasında kaydır; soldaki uzar/kısalır, sağdaki tersine.
        Klibin içeriği ve süresi değişmez, toplam süre değişmez."""
        f = self._frame()
        group = self.linked(clip)
        pairs = [(c, self.prev_adjacent(c), self.next_adjacent(c)) for c in group]
        lo, hi = -1e9, 1e9
        for c, l, r in pairs:
            if l is not None:
                lo = max(lo, -(l.duration - f))
                hi = min(hi, (self.max_out(l) - l.out_point) / l.speed)
            else:
                lo = max(lo, -c.start)
            if r is not None:
                hi = min(hi, r.duration - f)
                lo = max(lo, -r.in_point / r.speed)
        dt = min(max(dt, lo), hi)
        for c, l, r in pairs:
            if l is not None:
                l.out_point += dt * l.speed
            if r is not None:
                r.in_point += dt * r.speed
                r.start += dt
            c.start += dt
        return dt

    # ------------------------------------------------------------ çoklu kamera
    def media_offset(self, clip: Clip) -> float:
        """Grup zamanı -> medya zamanı farkı (multicam değilse 0)."""
        if not clip.mc:
            return 0.0
        g = self.multicams.get(clip.mc)
        if not g or not (0 <= clip.mc_angle < len(g["angles"])):
            return 0.0
        return float(g["angles"][clip.mc_angle].get("offset", 0.0))

    def multicam_duration(self, g: dict) -> float:
        end = 0.0
        for a in g["angles"]:
            m = self.media.get(a["media_id"])
            if m is not None:
                end = max(end, a.get("offset", 0.0) + (m.duration or 0.0))
        return end

    def create_multicam(self, name: str, media_items, offsets, audio: int = 0) -> str:
        gid = new_id()
        self.multicams[gid] = {
            "name": name, "audio": int(audio),
            "angles": [{"media_id": m.id, "offset": float(o)}
                       for m, o in zip(media_items, offsets)]}
        return gid

    def place_multicam(self, gid: str, start: float, video_track: int | None = None,
                       audio_track: int | None = None, angle: int = 0,
                       insert: bool = False) -> list[Clip]:
        """Grubu zaman çizelgesine koyar: görüntü 'angle' kamerasından, ses grubun ses
        kamerasından; ikisi bağlı. in/out grup zamanında."""
        g = self.multicams[gid]
        dur = self.multicam_duration(g)
        vt = video_track if video_track is not None else self.track_indices("video")[0]
        at = audio_track if audio_track is not None else self.track_indices("audio")[0]
        vm = self.media.get(g["angles"][angle]["media_id"])
        ai = g.get("audio", 0)
        am = self.media.get(g["angles"][ai]["media_id"])
        link = new_id()
        made = [Clip(track=vt, start=start, in_point=0.0, out_point=dur, media_id=vm.id,
                     kind="media", name=g["name"], link_id=link, mc=gid, mc_angle=angle)]
        if am is not None and am.has_audio:
            made.append(Clip(track=at, start=start, in_point=0.0, out_point=dur,
                             media_id=am.id, kind="media", name=g["name"], link_id=link,
                             mc=gid, mc_angle=ai))
        if insert:
            self.split_at(start)
            self._shift_after(start, dur, set())
        for c in made:
            self._clear_space(c.track, c.start, c.end)
            self.clips.append(c)
        return made

    def multicam_switch(self, clip: Clip, angle: int) -> bool:
        """Klibin gösterdiği kamerayı değiştir (yalnız görüntü; kesimler yerinde kalır)."""
        g = self.multicams.get(clip.mc)
        if not g or not (0 <= angle < len(g["angles"])) or \
                not self.track_is_video(clip.track):
            return False
        clip.mc_angle = angle
        clip.media_id = g["angles"][angle]["media_id"]
        return True

    def multicam_clip_at(self, t: float) -> Clip | None:
        """t anında görünen en üstteki multicam görüntü klibi."""
        best = None
        for c in self.clips:
            if c.mc and self.track_is_video(c.track) and c.contains(t) and \
                    not self.tracks[c.track].locked:
                if best is None or c.track > best.track:
                    best = c
        return best

    def multicam_cut(self, t: float, angle: int, clip: Clip | None = None) -> Clip | None:
        """Oynatırken 1-9: t anında keser ve sağdaki parçayı 'angle' kamerasına geçirir.
        Kesim noktası kare sınırına yuvarlanır; zaten başlangıçtaysa yalnız açı değişir."""
        c = clip or self.multicam_clip_at(t)
        if c is None:
            return None
        t = round(t * self.fps) / self.fps
        if c.mc_angle == angle:
            return c
        if t - c.start < 0.5 / self.fps:
            target = c
        else:
            # Bağlı ses de aynı noktada bölünür (Premiere'deki gibi: her parça kendi sesiyle)
            parts = self.split_at(t, [x.id for x in self.linked(c)])
            target = next((x for x in parts if x.track == c.track), None)
            if target is None:
                return None
        self.multicam_switch(target, angle)
        return target

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
            "uid": self.uid,
            "settings": {"width": self.width, "height": self.height, "fps": self.fps,
                         "sample_rate": self.sample_rate},
            "media": [asdict(m) for m in self.media.values()],
            "tracks": [asdict(t) for t in self.tracks],
            "clips": [asdict(c) for c in self.clips],
            "markers": list(self.markers),
            "multicams": self.multicams,
            "seq_in": self.seq_in,
            "master_db": self.master_db,
            "nests": {k: dict(v) for k, v in self.nests.items()},
            "seq_out": self.seq_out,
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
        p.multicams = {k: v for k, v in (data.get("multicams") or {}).items()
                       if isinstance(v, dict) and isinstance(v.get("angles"), list)}
        p.master_db = float(data.get("master_db", 0.0) or 0.0)
        p.nests = {k: v for k, v in (data.get("nests") or {}).items()
                   if isinstance(v, dict) and isinstance(v.get("clips"), list)}
        for k in ("seq_in", "seq_out"):
            v = data.get(k)
            setattr(p, k, float(v) if isinstance(v, (int, float)) else None)
        p.uid = data.get("uid") or p.uid
        return p

    def save(self, path: str):
        from .safety import PROJECT_EXT
        if not str(path).lower().endswith(PROJECT_EXT):
            raise ValueError(f"Proje yalnızca {PROJECT_EXT} uzantılı dosyaya kaydedilir: {path}")
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
    def load(cls, path: str, remote: str = "error",
             autosave_dir: str | None = None) -> "Project":
        """Proje dosyasını açar. Dosya güvenilmeyen bir kaynaktan gelebilir:

        - remote: ağ (UNC/URL) yolları için "error" (RemotePathsError fırlat, hiçbirine
          dokunma), "allow" (kullanıcı onayladı) ya da "strip" (yolları boşalt).
        - "autosave" bilgisi (kaydedilecek asıl yol) yalnızca dosya gerçekten otomatik kayıt
          klasöründeyse dikkate alınır; aksi halde kötü niyetli bir dosya kaydetme yolunu
          diskte istediği yere yönlendirebilirdi.
        """
        from . import safety
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Geçersiz proje dosyası")
        bad = safety.remote_paths(data)
        if bad and remote == "error":
            raise safety.RemotePathsError(bad)
        if bad and remote == "strip":
            safety.strip_remote_paths(data)
        base = os.path.dirname(os.path.abspath(path))
        for m in data.get("media", []):
            rel = m.get("relpath")
            if rel and (os.path.isabs(rel) or safety.is_remote_path(rel)):
                rel = None  # göreli yol olmalı
            if rel and not os.path.exists(m.get("path") or ""):
                cand = os.path.normpath(os.path.join(base, rel))
                if os.path.exists(cand):
                    m["path"] = cand
        p = cls.from_dict(data)
        # Yalnızca .kurgu dosyasının kendisine geri kaydedilir; başka uzantı -> Farklı Kaydet
        p.path = path if path.lower().endswith(safety.PROJECT_EXT) else None
        if autosave_dir is None:
            from .autosave import AUTOSAVE_DIR as autosave_dir
        info = data.get("autosave")
        if isinstance(info, dict) and safety.is_inside(path, autosave_dir):
            orig = info.get("original_path")
            p.path = orig if safety.valid_project_target(orig) else None
            p.recovered = True
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
