"""Original contiguous pending-frame copies with native row selection only."""
import numpy as np

from ..core.native_mask_math import selected_mask_rows


def _queue_batch_rows(items):
    prepared = {}
    if not items:
        return prepared
    count = len(items)
    buf_data = np.concatenate([item[1] for item in items]).reshape(count, 512)
    buf_active = np.concatenate([item[2] for item in items]).reshape(count, 512)
    keep_idx = selected_mask_rows(buf_active)
    if keep_idx.size:
        kept_data = buf_data[keep_idx]
        kept_active = buf_active[keep_idx]
        kept_universes = [items[int(index)][0] for index in keep_idx]
        for universe, data, active in zip(kept_universes, kept_data, kept_active):
            prepared[universe] = (data, active)
    return prepared
