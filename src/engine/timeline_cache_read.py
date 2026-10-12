"""原完整帧存储的播放消费适配；缺帧与合法空帧保持不同语义。

复用 DiskCache 5.6.3 Cache.get/with/close；固定版本与Apache2见资源台账。
只读窗口归原 TimelineFrameStore 所有，不建新播放器或缓存真源。
"""
from .timeline_bake_inputs import check_cancel

MISSING_FRAME = object()
_FIELDS = ("_timeline_render_frame_cache", "_timeline_render_record_source_frame_cache")


def read_cached_frame(cache, index, default=None):
    reader = getattr(cache, "read_frame", cache.get)
    return reader(index, default)


def prepare_cache_reads(engine, opts):
    """独占烘焙线程、发布锁外预热首窗；随后再做完整输入新鲜度复核。"""
    for field in _FIELDS:
        check_cancel(opts)
        cache = getattr(engine, field, {})
        if getattr(cache, "spilled", False):
            read_cached_frame(cache, 0, MISSING_FRAME)
