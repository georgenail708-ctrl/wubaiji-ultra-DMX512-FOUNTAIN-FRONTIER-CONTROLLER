"""失败的私有range缓存立即释放；完成store仍由原发布链移交。"""


def clear_failed_range(frames, records, incremental):
    """Traceback may retain local stores; GC is not a cancellation boundary."""
    stores = [frames, records]
    if incremental is not None:
        stores += [incremental.frames, incremental.records]
        temporal = getattr(incremental, "temporal", None)
        if temporal is not None:
            stores.append(temporal.store)
    seen = set()
    for store in stores:
        if id(store) not in seen:
            seen.add(id(store))
            store.clear()
