import time

from kurgu import effects as fx
from kurgu.media import probe
from kurgu.model import Project
from kurgu.rendercache import FrameCache, FrameSig, RenderWorker, heavy_frames


def test_signature_invalidates_only_edited_clip(red_video, blue_video):
    p = Project(320, 240, 25)
    a = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    b = p.place_media(p.add_media(probe(blue_video)), a.end)[0]
    s1 = FrameSig(p)
    before_a, before_b = s1(0.5), s1(b.start + 0.5)
    b.effects.append(fx.new_effect("blur"))
    s2 = FrameSig(p)
    assert s2(0.5) == before_a  # dokunulmayan klibin kareleri önbellekte kalır
    assert s2(b.start + 0.5) != before_b
    assert heavy_frames(p)[0] == round(b.start * p.fps)


def test_worker_fills_cache(qapp, red_video):
    p = Project(320, 240, 25)
    c = p.place_media(p.add_media(probe(red_video)), 0.0)[0]
    c.effects.append(fx.new_effect("chroma_key"))
    cache = FrameCache(64)
    worker = RenderWorker(cache)
    done = []
    frames = heavy_frames(p)
    worker.submit(Project.from_dict(p.to_dict()), frames, 160, 120, done=done.append)
    t0 = time.time()
    while not done and time.time() - t0 < 30:
        time.sleep(0.02)
    worker.shutdown()
    assert done == [True]
    sig = FrameSig(p)
    assert all(cache.has((fi, sig(fi / p.fps), 160, 120)) for fi in frames)


def test_cache_budget_evicts_oldest(qapp):
    from PySide6.QtGui import QImage
    cache = FrameCache(1)  # 1 MB
    for i in range(10):
        cache.put(i, QImage(320, 240, QImage.Format_RGB32))  # 300 KB
    assert cache.bytes <= cache.budget and not cache.has(0) and cache.has(9)
