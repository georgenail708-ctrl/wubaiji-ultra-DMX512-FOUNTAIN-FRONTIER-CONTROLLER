from __future__ import annotations

from typing import Iterable, List, Sequence, TypeVar

T = TypeVar("T")

WING_DIRECTION_LOW_TO_HIGH = "low_to_high"
WING_DIRECTION_HIGH_TO_LOW = "high_to_low"
WING_DIRECTION_HIGH_LOW = "high_low"
WING_DIRECTION_LOW_HIGH = "low_high"
#: R183 批次 I（gMA3 Align `><` / `<>` 关键字、EFX 对称语义）：
#: center_out = 中央为锚、越靠两边偏移越大（由中心向两边展开）；
#: edges_in   = 两端为锚、中央偏移最大（由两边向中心收拢）。
WING_DIRECTION_CENTER_OUT = "center_out"
WING_DIRECTION_EDGES_IN = "edges_in"

WING_DIRECTION_MODES = (
    WING_DIRECTION_LOW_TO_HIGH,
    WING_DIRECTION_HIGH_TO_LOW,
    WING_DIRECTION_HIGH_LOW,
    WING_DIRECTION_LOW_HIGH,
    WING_DIRECTION_CENTER_OUT,
    WING_DIRECTION_EDGES_IN,
)

WING_DIRECTION_SYMBOLS = {
    WING_DIRECTION_LOW_TO_HIGH: "/",
    WING_DIRECTION_HIGH_TO_LOW: "\\",
    WING_DIRECTION_HIGH_LOW: "<",
    WING_DIRECTION_LOW_HIGH: ">",
    WING_DIRECTION_CENTER_OUT: "><",
    WING_DIRECTION_EDGES_IN: "<>",
}

WING_DIRECTION_LABELS = {
    WING_DIRECTION_LOW_TO_HIGH: "low to high",
    WING_DIRECTION_HIGH_TO_LOW: "high to low",
    WING_DIRECTION_HIGH_LOW: "high then low",
    WING_DIRECTION_LOW_HIGH: "low then high",
    WING_DIRECTION_CENTER_OUT: "center out (mid to ends)",
    WING_DIRECTION_EDGES_IN: "edges in (ends to mid)",
}


def normalize_wing_direction(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": WING_DIRECTION_LOW_TO_HIGH,
        "/": WING_DIRECTION_LOW_TO_HIGH,
        "up": WING_DIRECTION_LOW_TO_HIGH,
        "asc": WING_DIRECTION_LOW_TO_HIGH,
        "ascending": WING_DIRECTION_LOW_TO_HIGH,
        "ltr": WING_DIRECTION_LOW_TO_HIGH,
        "low_high": WING_DIRECTION_LOW_HIGH,
        "low_to_high_then_high_to_low": WING_DIRECTION_LOW_HIGH,
        "\\": WING_DIRECTION_HIGH_TO_LOW,
        "down": WING_DIRECTION_HIGH_TO_LOW,
        "desc": WING_DIRECTION_HIGH_TO_LOW,
        "descending": WING_DIRECTION_HIGH_TO_LOW,
        "rtl": WING_DIRECTION_HIGH_TO_LOW,
        "<": WING_DIRECTION_HIGH_LOW,
        "high_then_low": WING_DIRECTION_HIGH_LOW,
        "high_low": WING_DIRECTION_HIGH_LOW,
        "high_low_high": WING_DIRECTION_HIGH_LOW,
        ">": WING_DIRECTION_LOW_HIGH,
        "low_then_high": WING_DIRECTION_LOW_HIGH,
        "low_high_low": WING_DIRECTION_LOW_HIGH,
        # ★ R183：对称两模式。注意 "<>" / "><" 与旧 "<" / ">" **不是同一键**
        #   （旧键语义逐字节不变，见 test_r183_prog_wing_block 守卫测试）。
        #   下面还收了 WING_DIRECTION_LABELS 经 normalize 规范化后的形态
        #   （空格→下划线），保证按钮按 label/tooltip 反查模式时不会掉回默认值。
        "><": WING_DIRECTION_CENTER_OUT,
        "center_out": WING_DIRECTION_CENTER_OUT,
        "centre_out": WING_DIRECTION_CENTER_OUT,
        "mid_out": WING_DIRECTION_CENTER_OUT,
        "middle_out": WING_DIRECTION_CENTER_OUT,
        "center_to_edges": WING_DIRECTION_CENTER_OUT,
        "center_out_(mid_to_ends)": WING_DIRECTION_CENTER_OUT,
        "<>": WING_DIRECTION_EDGES_IN,
        "edges_in": WING_DIRECTION_EDGES_IN,
        "edge_in": WING_DIRECTION_EDGES_IN,
        "ends_in": WING_DIRECTION_EDGES_IN,
        "outside_in": WING_DIRECTION_EDGES_IN,
        "edges_to_center": WING_DIRECTION_EDGES_IN,
        "edges_in_(ends_to_mid)": WING_DIRECTION_EDGES_IN,
    }
    raw = aliases.get(raw, raw)
    return raw if raw in WING_DIRECTION_MODES else WING_DIRECTION_LOW_TO_HIGH


def wing_direction_factor(local_index: int, total: int, direction: object) -> float:
    """该设备在序列里的插值因子 0..1（0=锚点值，1=目标值）。

    既有四模式取值**逐字节不变**（守卫测试 test_r183_prog_wing_block）：
      low_to_high → t；high_to_low → 1-t；high_low → |2t-1|；low_high → 1-|2t-1|。
    R183 对称两模式（与 high_low / low_high 形状同族，命名对齐 gMA3 Align 语义）：
      center_out → |2t-1|（中央 0、两端 1）；edges_in → 1-|2t-1|（两端 0、中央 1）。
    """
    if total <= 1:
        return 1.0
    t = max(0.0, min(1.0, float(local_index) / float(total - 1)))
    mode = normalize_wing_direction(direction)
    if mode == WING_DIRECTION_HIGH_TO_LOW:
        return 1.0 - t
    if mode == WING_DIRECTION_LOW_HIGH:
        return 1.0 - abs(t * 2.0 - 1.0)
    if mode == WING_DIRECTION_HIGH_LOW:
        return abs(t * 2.0 - 1.0)
    if mode == WING_DIRECTION_CENTER_OUT:
        return abs(t * 2.0 - 1.0)
    if mode == WING_DIRECTION_EDGES_IN:
        return 1.0 - abs(t * 2.0 - 1.0)
    return t


def chunk_blocks(items: Sequence[T], block_size: int) -> List[List[T]]:
    bs = max(1, int(block_size or 1))
    return [list(items[start:start + bs]) for start in range(0, len(items), bs)]


def split_wings(blocks: Sequence[List[T]], wings: int) -> List[List[List[T]]]:
    block_list = [list(block) for block in blocks if block]
    if not block_list:
        return []
    wing_count = max(1, min(int(wings or 1), len(block_list)))
    base_len = len(block_list) // wing_count
    remainder = len(block_list) % wing_count
    out: List[List[List[T]]] = []
    cursor = 0
    for wing_idx in range(wing_count):
        count = base_len + (1 if wing_idx < remainder else 0)
        wing = block_list[cursor:cursor + count]
        if wing:
            out.append(wing)
        cursor += count
    return out


def _center_out_order(count: int) -> List[int]:
    """由中心向两边展开的块序：中央先处理，两侧交替向外。

    count=1 → [0]；count=2 → [1,0]；count=5 → [2,3,1,4,0]；
    count=6 → [3,2,4,1,5,0]。用于 R183「中心向两边」方向（gMA3 Align `><`）。
    """
    if count <= 0:
        return []
    order: List[int] = []
    if count % 2 == 1:
        mid = count // 2
        order.append(mid)
        lo, hi = mid - 1, mid + 1
    else:
        lo, hi = count // 2 - 1, count // 2
    while lo >= 0 or hi < count:
        if hi < count:
            order.append(hi)
            hi += 1
        if lo >= 0:
            order.append(lo)
            lo -= 1
    return order


def _edges_in_order(count: int) -> List[int]:
    """由两边向中心收拢的块序：左右两端交替向中央。

    count=1 → [0]；count=2 → [0,1]；count=5 → [0,4,1,3,2]；
    count=6 → [0,5,1,4,2,3]。用于 R183「两边向中心」方向（gMA3 Align `<>`）。
    """
    if count <= 0:
        return []
    lo, hi = 0, count - 1
    order: List[int] = []
    while lo <= hi:
        order.append(lo)
        lo += 1
        if lo <= hi:
            order.append(hi)
            hi -= 1
    return order


def _direction_order(count: int, direction: object) -> List[int]:
    if count <= 0:
        return []
    mode = normalize_wing_direction(direction)
    if mode == WING_DIRECTION_HIGH_TO_LOW:
        return list(range(count - 1, -1, -1))
    if mode == WING_DIRECTION_LOW_HIGH:
        split = (count + 1) // 2
        return list(range(0, split)) + list(range(count - 1, split - 1, -1))
    if mode == WING_DIRECTION_HIGH_LOW:
        split = (count + 1) // 2
        return list(range(split - 1, -1, -1)) + list(range(split, count))
    if mode == WING_DIRECTION_CENTER_OUT:
        return _center_out_order(count)
    if mode == WING_DIRECTION_EDGES_IN:
        return _edges_in_order(count)
    return list(range(count))


def _wing_orders(wing_blocks, direction, mirror, reverse):
    orders = [_direction_order(len(wing), direction) for wing in wing_blocks]
    for wing_index, order in enumerate(orders):
        if bool(reverse) != (bool(mirror) and wing_index % 2 == 1):
            order.reverse()
    return orders


def build_wing_block_layout(items, wings=1, block_size=1,
                            direction=WING_DIRECTION_LOW_TO_HIGH, *,
                            mirror=False, reverse=False):
    """Keep each wing's complete domain and its transformed indivisible blocks."""
    originals = split_wings(chunk_blocks(list(items), block_size), wings)
    orders = _wing_orders(originals, direction, mirror, reverse)
    return [(wing, [wing[index] for index in order])
            for wing, order in zip(originals, orders)]


def wing_block_distribution_slots(items, wings=1, block_size=1, *, mirror=False, reverse=False):
    layout = build_wing_block_layout(items, wings, block_size, mirror=mirror, reverse=reverse)
    return [(item, index, len(wing))
            for index in range(max((len(wing) for _original, wing in layout), default=0))
            for _original, wing in layout if index < len(wing)
            for item in wing[index]]


def build_wing_block_units(
    items: Sequence[T],
    wings: int = 1,
    block_size: int = 1,
    direction: object = WING_DIRECTION_LOW_TO_HIGH,
    *,
    mirror: bool = False,
    reverse: bool = False,
) -> List[List[T]]:
    """Group equal phases, reflecting alternate wings and reversing all wings.

    Blocks remain indivisible; both flags are opt-in for old showfile parity.
    """
    layout = build_wing_block_layout(items, wings, block_size, direction,
                                     mirror=mirror, reverse=reverse)
    unit_count = max((len(wing) for _original, wing in layout), default=0)
    units: List[List[T]] = []
    for pos in range(unit_count):
        unit: List[T] = []
        for _original, wing in layout:
            if pos < len(wing):
                unit.extend(wing[pos])
        if unit:
            units.append(unit)
    return units


def _group_count_active(params, key):
    try:
        return int(float(params.get(key, 1) or 1)) > 1
    except (TypeError, ValueError, OverflowError):
        return False


def spatial_transform_active(params):
    params = params or {}
    if params.get("wing_mirror") or params.get("wing_reverse"):
        return True
    return any(_group_count_active(params, key) for key in
               ("local_wings", "wings", "local_block_size", "block_size"))
