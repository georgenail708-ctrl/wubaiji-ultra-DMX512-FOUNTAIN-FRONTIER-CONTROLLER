"""
DMXTimeline — Network Manager  (v13)
统一管理 Art-Net / sACN 收发，单例

★ 全新混合逻辑：
  各层不再做单纯的 HTP / LTP 覆盖，而是分成 BASE（基线/绝对值）和 SHAPE（动态
  形状）两类，最终输出 = base + Σ(shape_k − anchor_k) × gain，被裁剪到 0..255。

  这样：
    • Programmer 推杆把通道设为 200（base）
    • Effect 引擎在该通道叠了一个 ±30 的正弦波（shape，anchor=值的中位）
    → 输出 = 200 + 偏移波，振幅保留，整体被推杆抬高，"形状不被抹掉"。

  不同形状层共存时，他们的偏移会被求平均后合成（避免过冲），各自的趋势仍然
  能传递到最终结果，达到"互相影响细节"的效果。

★ 稀疏层支持：每层用 (data, active_mask) 两个数组，区分 "未写入" 和 "显式写 0"。
  这是修复"摇杆 0/<128 被当作未写入而被覆盖"问题的根本手段。

★ frame_in 信号现在确实会发出（之前定义但从未 emit，造成 RecordEngine 收不到帧）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..core.models import DMXFrame, FilterRule, NetworkConfig, Protocol
from ..engine.native_ownership import mask_count, mask_without
from ..core.project_frame_origin import (
    ProjectFrameLease,
    queue_project_frame,
)
from .nm_layer_frames import clear_layer_frame, drain_layer_frames_locked, update_layer_frame
from .nm_batch_rows import _queue_batch_rows
from ..core.constants import (  # noqa: F401  单源真源
    ABSOLUTE_DIRECT_SHAPE_LAYERS, ABSOLUTE_HOME_MOTOR_CHANNELS, ALL_LAYERS,
    AUDIO_HTP_SHAPE_LAYERS, BASE_LAYERS, CH_COLOR_ALIASES, CLIP_LAYERS,
    MOTOR_CENTER_HOME, MOTOR_FINE_HOME, SHAPE_LAYERS,
)
from ..engine.xy_reference import (
    apply_xy_reference_to_physical,
    apply_xy_reference_to_virtual,
    build_xy_reference_map,
    channel_type_key,
)
from .artnet_node import (
    ARTNET_PORT,
    ArtNetReceiver,
    ArtNetSender,
    ReceiverBindError,
)
from .artpoll_discovery import ArtPollDiscovery
from .grand_master import DEFAULT_GM, apply_grand_master   # R183 T5 总控推杆
from .nic_broadcast import directed_broadcast_targets
from .nic_watch import NicWatch
from .output_sink import IOutputSink
from .output_router import OutputRouter
from .nm_output_binding import (
    apply_channel_defaults_locked, apply_fixture_defaults, configure_nm,
    before_nm_binding_mutation, begin_nm_binding, begin_nm_map_binding,
    final_output_reader, finish_nm_binding, initialize_output_services,
    invalidate_nm_binding, mark_nm_binding_failed, set_xy_from_project, start_nm,
)
from .nm_output_delivery import (
    accept_single_source, call_actual_sender, capture_nm_sources,
    deliver_prepared_batch, invoke_nm_batch, push_changed_sources,
    read_source_identity, route_single_source,
)
from .sacn_node import SACNReceiver, SACNSender

log = logging.getLogger(__name__)


def _release_known_universes(nm, known_sorted, defer_send: bool = False) -> None:
    """零帧混音+发送（release 收尾）。★ R194/AUD-05：``defer_send=True`` 时
    全部已知域置脏、零帧由 worker 下一拍混出（GUI 不持锁做全域混音+发送）；
    同步路径仍持 `_state_lock`（R185 tap 契约：接受路径必须持锁）。
    （模块级：locked 函数行数/CC 基线）"""
    if defer_send:
        nm._dirty_universes.update(set(known_sorted))
        return
    snapshots = {name: {} for name in ALL_LAYERS}
    for u in known_sorted:
        try:
            virtual_out = nm._mix_one_universe(snapshots, int(u), update_anchor=False)
            physical_out = nm._apply_xy_reference(int(u), virtual_out)
            nm._universe_virtual_snapshot[int(u)] = virtual_out.copy()
            nm._universe_snapshot[int(u)] = physical_out.copy()
            accept_single_source(nm, int(u), physical_out)
        except Exception:
            # C: 此前整段静默 pass → 该域不出光且无任何线索。行为不变,
            # 只补日志。release 为按需路径, 非热路径。
            log.exception(
                "release_all_outputs: zero-frame mix/send failed (universe=%s)", u)


def _mark_refresh_dirty(nm, universes) -> None:
    """★ R194/AUD-05：置脏刷新请求（模块级：request_universe_refresh 嵌套深度）。"""
    with nm._state_lock:
        if universes is None:
            for layer in nm._layers.values():
                nm._dirty_universes.update(layer.keys())
        else:
            nm._dirty_universes.update(int(u) for u in universes)


def _drain_output_worker_zombie(nm) -> bool:
    """★ R193/CC-02：排空停止超时的僵尸 output worker（同 _drain_gen_zombie）。

    worker 的唯一退出条件 `_output_worker_stop` 将在 start 里 clear()——不
    排空会把僵尸永久复活成双 40Hz 混音发送。返回 False=仍存活（拒绝重启）。"""
    zombie = getattr(nm, "_output_worker_zombie", None)
    if zombie is None:
        return True
    if zombie.is_alive():
        zombie.join(timeout=0.5)
    if zombie.is_alive():
        log.warning("上一 output worker 停止超时仍存活，拒绝重启以防双 worker 混音")
        return False
    nm._output_worker_zombie = None
    return True


def _channel_type_key(ch_type) -> str:
    s = channel_type_key(ch_type)
    return CH_COLOR_ALIASES.get(s, s)


#: 电机类通道（粗/精）——默认值由设备定义的显式值或归中常量决定。
_MOTOR_TYPES = {"motor_x", "motor_y", "motor_z"}
_MOTOR_FINE_TYPES = {"motor_x_fine", "motor_y_fine", "motor_z_fine"}


def _valid_channels(channels) -> List[int]:
    """通道序列 → 合法（0-511）的 int 列表；非法输入返回空列表。"""
    try:
        return [int(c) for c in (channels or ()) if 0 <= int(c) < 512]
    except (TypeError, ValueError):
        return []


def _active_of_layer(per_uni, universe: int):
    """取某层在某域的 active 掩码；该层无此域或全 False → None（跳过）。"""
    entry = per_uni.get(universe)
    if entry is None:
        return None
    active = np.asarray(entry[1], dtype=bool)
    return active if bool(active.any()) else None


#: 共享空 bool[512]（只读哨兵；R110 灯掩码未启用时返回它 → 零分配）
_EMPTY_512_BOOL = np.zeros(512, dtype=bool)


def _accumulate_layer_owners(layers, universe: int, valid, skip, acc) -> None:
    """把"某通道被哪些层控制"累加进 `acc`（调用方持锁）。

    与 `get_channels_owners` 的循环体同源；抽出来是为了让两个批量查询
    （owners / ownership）都能复用同一段"逐层 × 逐通道"遍历，避免两套漂移。
    """
    for layer_name, per_uni in layers.items():
        if layer_name in skip:
            continue
        active = _active_of_layer(per_uni, universe)
        if active is None:
            continue
        for c in valid:
            if bool(active[c]) and layer_name not in acc[c]:
                acc[c].append(layer_name)


def _fd_default(fd, ch_type_s: str, fallback: int) -> int:
    """读设备定义的显式通道默认值（缺失/非法 → fallback），夹到 0-255。"""
    fd_defaults = getattr(fd, "function_channel_defaults", {}) or {}
    try:
        val = int(fd_defaults.get(ch_type_s, fallback))
    except Exception:
        val = fallback
    return max(0, min(255, val))


def _idle_default_of(fd, ch_type_s: str) -> Optional[int]:
    """某通道的 idle 默认值；返回 None = **不登记**（无默认，不进输出基线）。

    ★★ 关键（2026-09-14 用户实报修正）：`function_*` 通道的 **0 = "未设默认"**，
      不登记。导入的工程元数据常带显式 0（真实工程 `To The Moon` 里
      `function_2=0` 有 174 个），那只是**记录值**、不是"要输出 0"；登记后会让这些
      通道在监视器里被标成"有归属"，用户看到"多出一堆不要的 0"。
      原话："0 只是一种检查逻辑判据，不是输出用的"。不登记的输出与登记时**完全
      一致**（0 本就是全零基线），只是不再假报"有归属"。

    ★ 电机类通道**必须照常登记，含 0**：`motor_z` 的 0 是"落底面"的**有意位置**
      （见 tests/test_network_nodes.py::test_lift_motor_z_idle_default_is_zero_not_center），
      精调通道 0 是"粗值即值"的既有语义——它们不是元数据噪声。
    """
    if ch_type_s in _MOTOR_TYPES or ch_type_s in _MOTOR_FINE_TYPES:
        if ch_type_s in ABSOLUTE_HOME_MOTOR_CHANNELS:
            # 绝对位置/升降轴（LED TUBE / Winch 的 Z）：取定义显式值，缺失回退归中
            return _fd_default(fd, ch_type_s, MOTOR_CENTER_HOME)
        return MOTOR_FINE_HOME if ch_type_s in _MOTOR_FINE_TYPES else MOTOR_CENTER_HOME
    if ch_type_s.startswith("function_"):
        v = _fd_default(fd, ch_type_s, 0)
        if v == 0:
            return None
        return v
    return None


def _bit_equal_u8(x, y) -> bool:
    """uint8 帧位等比价 (F-1): memoryview 视窗逐字节 C 级比对,
    命中不等立即短路, 比 numpy view+all 快 ~4x(实测 1.72→0.44µs)。
    位比较与逐元素相等逐位同义(uint8 无 NaN/-0 值语义)。
    尺寸/连续性/类型不满足时回退 array_equal。"""
    if (x.dtype == np.uint8 and y.dtype == np.uint8
            and x.shape == y.shape
            and x.flags.c_contiguous and y.flags.c_contiguous
            and x.size % 8 == 0):
        return memoryview(x).cast("Q") == memoryview(y).cast("Q")
    return bool(np.array_equal(x, y))


def _bit_equal_bool(x, y) -> bool:
    """bool 帧位等比价 (F-1): memoryview 不能跨非字节格式 cast,
    先 view(np.uint8) 零拷贝转字节再视窗比对; 语义同 _bit_equal_u8。"""
    if (x.dtype == np.bool_ and y.dtype == np.bool_
            and x.shape == y.shape
            and x.flags.c_contiguous and y.flags.c_contiguous
            and x.size % 8 == 0):
        return (memoryview(x.view(np.uint8)).cast("Q")
                == memoryview(y.view(np.uint8)).cast("Q"))
    return bool(np.array_equal(x, y))


def _frame_bit_equal(d0, a0, d1, a1) -> bool:
    """层帧位等比价 (D-5 + F-1): 逐位相等判定, 形状/类型异常回退
    array_equal —— 与旧路径语义完全一致。"""
    try:
        if not _bit_equal_u8(d0, d1):
            return False
        return bool(_bit_equal_bool(a0, a1))
    except Exception:
        return bool(np.array_equal(d0, d1) and np.array_equal(a0, a1))


def _build_filter_mask(rule: Optional[FilterRule]) -> Optional[np.ndarray]:
    """把 FilterRule 归一化为 512 通道布尔掩码（0-based，True=允许通过）。

    - rule 为 None 或 allows_all() → 返回 None，表示全通（不应用过滤）。
    - 空 channels（is_empty）→ 返回全 False 掩码（什么都不放行）。
    """
    if rule is None or rule.allows_all():
        return None
    mask = np.zeros(512, dtype=bool)
    for ch in rule.channels:
        idx = int(ch) - 1
        if 0 <= idx < 512:
            mask[idx] = True
    return mask


def _addr_mask(addrs) -> np.ndarray:
    """地址集合 → 512 位布尔掩码（编辑器 LTP 触点用；越界地址忽略）。"""
    mask = np.zeros(512, dtype=bool)
    for addr in addrs:
        idx = int(addr)
        if 0 <= idx < 512:
            mask[idx] = True
    return mask


def _apply_output_filter(mask: Optional[np.ndarray], data: np.ndarray) -> np.ndarray:
    """输出通道过滤：仅放行 `mask` 中的通道，其余置 0（mask=None → 全通）。

    ★ R141：`_send_to_hw` 一直有这道末级过滤，而混音器实际走的
      `_send_batch_to_hw` 漏了 → 用户设「只放行 N 号通道」批量路径照发全 512。
      两路共用本函数，「合批 = 逐帧串行」的输出字节语义才真正成立。
    """
    if mask is None:
        return data
    return np.where(mask, data, 0).astype(np.uint8)


def _uni_allowed(nm, uni: int, allowed: set) -> set:
    """校验点 #5：白名单判定；缓存可能过期时兜底重算（返回可能刷新的集合）。

    原 ``_send_batch_to_hw`` 内联逻辑原样外提（R178 变化域拆分让位）。
    """
    if uni in allowed:
        return allowed
    nm._output_allowlist_cache = None
    return nm._output_universe_allowlist()


def _batch_change_split(snapshot: Mapping[int, np.ndarray], batch: dict,
                        stats: dict) -> tuple:
    """把批帧按"与上次实发快照逐字节比对"拆为 (changed, keepalive)。

    ★ R178 变化域发送：与 ``_universe_snapshot`` 完全相同的域不再实发，
      只登记 keepalive（由 OutputRouter 按 keepalive_interval 重发上一帧，
      字节与上次实发一致）——DMX 接收端看到的字节流不变，UDP 包量骤降。
    """
    changed: Dict[int, np.ndarray] = {}
    keepalive: List[int] = []
    for uni, data in batch.items():
        snap = snapshot.get(uni)
        # ★ R183 F7：_bit_equal_u8 位等比价（memoryview 视窗，~4x 于
        #   np.array_equal，见 :146）——uint8 帧逐位同义，语义不变。
        if snap is not None and _bit_equal_u8(snap, data):
            keepalive.append(uni)
            stats["skipped"] += 1
        else:
            changed[uni] = data
            stats["sent"] += 1
    return changed, keepalive


# ─────────────────────────────────────────────────────────────────────────────
#  Art-Net 协议宇宙号转换
# ─────────────────────────────────────────────────────────────────────────────
def _to_artnet_universe(universe_1based: int) -> int:
    return universe_1based - 1


def _from_artnet_universe(universe_0based: int) -> int:
    return universe_0based + 1


def _sum_sender_counters(senders) -> tuple:
    """汇总发送器的 (packets, errors)（R182：状态查询读实际对象，不猜配置）。

    无 ``_packets`` / ``_errors`` 属性的 sink 贡献 0；读取异常逐条吞掉并记
    debug（状态展示失败绝不能影响发送路径）。
    """
    packets = errors = 0
    for sender in senders:
        if sender is None:
            continue
        try:
            packets += int(getattr(sender, "_packets", 0) or 0)
            errors += int(getattr(sender, "_errors", 0) or 0)
        except Exception:
            log.debug("读取发送器计数失败", exc_info=True)
    return packets, errors




def _clip_owned_span(fi, universe: int, defs_by_id: dict) -> tuple:
    """该设备在本宇宙的 (通道 lo, hi, id)；不适用时 hi <= lo（R122 小助手）。

    ★ R141：def 查找改**预建 dict**（原实现每台调一次 `get_fixture_def()` 线性扫）。
    """
    try:
        start = int(getattr(fi, "start_channel", 0))
        if start <= 0 or int(getattr(fi, "universe", 0)) != int(universe):
            return 0, 0, ""
        fdef = defs_by_id.get(str(getattr(fi, "def_id", "")))
        lo = start - 1
        return lo, min(lo + max(1, int(getattr(fdef, "channel_count", 0))), 512), \
            str(getattr(fi, "id", ""))
    except Exception:
        return 0, 0, ""


def _new_owned_state() -> list:
    """归属表工作区 `[owner, extra_idx, ids, extras]`（owner[c]=设备下标，-1=无）。"""
    return [np.full(512, -1, dtype=np.int32), np.full(512, -1, dtype=np.int32), [], []]


def _record_owned_span(state: list, lo: int, hi: int, fid: str) -> None:
    """把设备 `fid` 的 [lo, hi) 通道区间记进归属表；重叠通道的设备进 extras。"""
    owner, extra_idx, ids, extras = state
    span = owner[lo:hi]
    free = span < 0
    owner[lo:hi] = np.where(free, len(ids), span)
    for ch in (np.flatnonzero(~free) + lo).tolist():
        slot = int(extra_idx[ch])
        if slot < 0:
            extra_idx[ch] = len(extras)
            extras.append([len(ids)])
        else:
            extras[slot].append(len(ids))
    ids.append(fid)


def _build_owned_tables(all_fx, all_defs) -> Dict[int, tuple]:
    """一次遍历设备表，为**所有出现过的宇宙**建「通道 → 设备」归属表。

    每表 `(owner, extra_idx, ids, extras)`：`owner[c]` = 该通道首个归属设备的
    `ids` 下标（-1=无）；重叠配接的其余设备记 `extras`、经 `extra_idx[c]` 索引 →
    与旧的逐设备扫描（`_clip_owned_probe`）**逐设备等价**。
    ★ 必须单遍分桶：按宇宙各扫一遍 = 15k 台 × 10 域 15 万次解析（实测 +50ms）。
    """
    defs_by_id = {
        str(getattr(fd, "id", "")): fd
        for fd in (all_defs or []) if getattr(fd, "id", None)
    }
    state: Dict[int, list] = {}     # universe -> 归属表工作区
    for fi in (all_fx or []):
        try:
            uni = int(getattr(fi, "universe", 1) or 1)
        except Exception:
            uni = 1
        lo, hi, fid = _clip_owned_span(fi, uni, defs_by_id)
        if hi > lo:
            _record_owned_span(state.setdefault(uni, _new_owned_state()), lo, hi, fid)
    return state



def _merge_xy_changed_into_mask(active, pre, post) -> None:
    """把 XY/Z 变换**实际改写**的通道并入录制掩码（就地改 `active`）。

    ★★ R136 用户实报（"同步模式传着传着就莫名其妙丢失了"）：`_apply_xy_reference` 是
      **输出侧最终变换** —— 它写 X/Y/Z，但这些通道**不在任何混音层里**，所以层汇总出的
      掩码不含它们；而录制正是**按掩码取帧** → 变换推导出来的那条轴录不进去。

      真工程 `E:\水仙十字.dmxt` 现场（只读解析 + 独立复核实测）：
        · 51 台 `2-Axis Jet` 全是 `mode='sync'`、`global_angle=0` —— 用户**确实没改设置**；
        · 数控 Track 12 的 4 段掩码含每台 x+y（126 通道）→ 正常；
        · 数控 Track 13 的两段掩码只有 motor_x（32 通道）→ 缺 motor_y；回放时缺的那条轴
          停在机制默认（实测 Y 恒 128，而录制值 176）→ 世界方向极差 137.3° = 用户看到的
          "同步丢了"；补上掩码后回到 0.7°（A/B 对照）。

    **只并"确实改了值"的通道**（`pre != post`）：这正是"变换推导出的那条轴"的特征
      （输入只推了 X，Y 由变换从中心推到目标 → Y 也变化）；而"该段没碰电机的设备"
      （变换把中心值写回中心 = 无变化）不会被误占通道（旧案整宇宙并集会的副作用）。
    """
    if pre is None or post is None:
        return
    try:
        a = np.asarray(pre, dtype=np.uint8).reshape(-1)
        b = np.asarray(post, dtype=np.uint8).reshape(-1)
        n = min(a.size, b.size, 512)
        if n <= 0:
            return
        for idx in np.nonzero(a[:n] != b[:n])[0]:
            active[int(idx)] = True
    except Exception:
        log.debug("xy changed-channel mask merge failed", exc_info=True)


class NetworkManager(QObject):
    """单例：负责所有 DMX 收发 + 多层混音。"""

    frame_in       = pyqtSignal(object)
    frame_raw_in   = pyqtSignal(int, object)
    status_changed = pyqtSignal(str)
    timecode_received = pyqtSignal(int, int, int, int, int)  # hours, minutes, seconds, frames, tc_type

    _instance: Optional["NetworkManager"] = None

    # R178 网络输出革新：类级缺省（__init__ 行数基线冻结不可加行），
    # start() 时挂实例属性覆盖；stop() 时回收。
    _artpoll_discovery: Optional[ArtPollDiscovery] = None
    _nic_watch: Optional[QObject] = None
    _skip_stats: Optional[dict] = None
    # R182 P1-5：最近一次 start() 的接收端口绑定失败信息（UI 读 last_bind_error
    # 显示红字）。类级缺省理由同上。
    _last_bind_error: Optional[str] = None
    # R183 T5 Grand Master（总控推杆）：乘性**末级**增益
    # Reduce 语义）。只作用在真正下发硬件的字节上（见 `_apply_grand_master`
    # 调用点），混音层与快照(监控区取值)不受影响 —— "总控压暗"不改变编程值。
    # 类级缺省 + __init__ 一行实例化，理由同 `_last_bind_error`（init 行数冻结）。
    _grand_master: float = 1.0
    _final_output_tap = None
    _final_output_binding = None

    # ── 构造 / 单例 ──────────────────────────────────────────────────────────

    def __init__(self, parent=None, *, final_output_observer=None):
        super().__init__(parent)
        self._config: NetworkConfig = NetworkConfig()
        self._input_filter:  Optional[FilterRule] = None
        self._output_filter: Optional[FilterRule] = None
        # 预计算通道掩码（None=全通，不应用过滤）。发送/接收热路径只读掩码，
        # 避免每帧调用 FilterRule.allows_all()（会分配 512 元素 set）。
        self._input_filter_mask: Optional[np.ndarray] = None
        self._output_filter_mask: Optional[np.ndarray] = None

        self._artnet_rx: Optional[ArtNetReceiver] = None
        self._sacn_rx:   Optional[SACNReceiver]   = None
        self._artnet_tx: Optional[ArtNetSender]   = None
        self._sacn_tx_pool: Dict[int, SACNSender] = {}

        self._universe_snapshot: Dict[int, np.ndarray] = {}
        self._last_sent_snapshot: Dict[int, np.ndarray] = {}   # ★ R178：上次实发帧（变化域比较基准）
        self._universe_virtual_snapshot: Dict[int, np.ndarray] = {}

        self._running = False; self._record_start = 0.0

        # 性能优化：只混合/发送发生变化的 universe；空闲时按低频心跳维持输出。
        self._dirty_universes: set[int] = set()
        self._flush_scheduled: bool = False
        self._last_heartbeat_send: float = 0.0
        self._heartbeat_interval: float = self._read_float_env(
            "QASCADE_DMX_HEARTBEAT_SEC",
            default=0.10,
            minimum=0.025,
            maximum=2.0,
        )
        # 空闲心跳"轮转队列":心跳到期时把需保活的全部 universe 装入,之后每个 worker
        # tick 只混其中一批(按一个心跳周期内的 tick 数均分),把单次全量重混(~460路/55ms
        # 持锁)摊到多个 tick。每路在一个心跳周期内仍恰好刷新一次——刷新频率/混音逻辑/
        # 输出值完全不变,只是不再挤在同一次锁里,单次锁占用骤降,消除空闲态全局卡顿。
        # 脏路径(真实改动)不入队,照旧立即整体混音。
        self._heartbeat_queue: List[int] = []
        self._heartbeat_batch: int = 0

        # 每层数据：layer_name -> {universe -> (data:uint8[512], active:bool[512])}
        self._layers: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {
            name: {} for name in ALL_LAYERS
        }
        self._channel_defaults: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        self._project_ref = None
        self._project_frame_lease = ProjectFrameLease(None, 0)
        self._xy_reference_by_universe: Dict[int, list] = {}
        # 同步组 Z 命令（世界扇面目标角）与其作用范围（设备 id 集，见 setter）
        self._xy_reference_z_command_deg: Optional[float] = None
        self._xy_reference_z_command_ids: Optional[frozenset] = None
        # 计算路径（compute_universe_now）xy 变换输出缓存：{uni: (输入帧 bytes,
        # z_command_deg, 引用表版本, 输出)}。xy 变换是纯函数（输入帧 + 引用表 +
        # Z 命令 → 输出），录制 25ms 轮询 tick 之间层数据不变时输入帧逐字节相同
        # → 直接复用输出，避免每 tick 对全部同步引用逐条 Python 变换
        # （大工程实测 400 refs/域 ≈ 700µs，占轮询链 88%）。
        self._xy_ref_compute_cache: Dict[int, tuple] = {}
        # 引用表/变换语义版本：set_xy_reference_map 变更时 +1，缓存键之一。
        # 输入帧字节相同但引用表变化（如同步开关切换、引用通道恰在中心值）
        # 时输出仍可能不同 → 必须随版本失效。
        self._xy_ref_map_version: int = 0
        # 电机粗/精配对地址组按 universe 预计算缓存：{universe: [np.array([addr,...]), ...]}。
        # 纯结构(取决于配接布局,非帧值),避免每帧遍历全部设备×通道(曾达 65 万次/80帧)。
        # 键为结构签名(id+len),布局变即失效重算。
        self._motor_pair_groups_cache: Dict[int, list] = {}
        self._motor_pair_groups_sig = None

        # 形状层调制全局增益（>1 加强，<1 减弱）；`_layer_gains`=每层增益（场景淡入淡出真源）
        self._shape_gain: float = 1.0; self._layer_gains: Dict[str, float] = {}
        # T5 总控压暗（Reduce 末级；注释/语义见类级缺省 `_grand_master`）
        self._grand_master: float = DEFAULT_GM

        # 形状层混合模式：
        #   "direct" - 直出模式：shape 偏移直接叠加到 base，不做权重压制（默认）
        #   "soft"   - 软压制模式：base 越接近边界（0/255），shape 偏移权重越小，防止过冲截断
        self._shape_blend_mode: str = "direct"

        # H2: 混音缓冲池（opt-in）。默认 False → _mix_one_universe 完全走原
        # np.zeros/np.full 分配路径，逐字节不变。开启后复用下面这组预分配 scratch，
        # 每帧按各自原始初值 fill 再用。因混音持 _state_lock 串行、逐 universe 执行，
        # 且返回 out.astype(uint8) 为新数组（与 scratch 天然隔离，被快照 .copy() 前
        # 不会被复用），故复用安全。漏 fill 会串帧污染——下表初值已逐字段对齐原代码。
        # ★ 性能：scratch 复用已按初值逐字段对齐且 out 恒为新数组（astype），
        #   开启省掉每次 _mix_one_universe 的 17 个 512 数组分配
        #   （11382 台/73 域每 tick ~1241 次分配 ≈ 3-5ms）。字节等价由
        #   _ui_validation/probe_output_bytehash.py 哈希门禁验证。
        self._reuse_mix_scratch: bool = True
        self._mix_scratch: Dict[str, np.ndarray] = {
            # key                  dtype          原始初值
            "base_val":            np.empty(512, dtype=np.float32),   # 0.0 / default_data
            "base_active":         np.empty(512, dtype=bool),         # False / default_active
            "default_mask":        np.empty(512, dtype=bool),         # False / default_active
            "base_time":           np.empty(512, dtype=np.float64),   # -1.0
            "base_layer_active":   np.empty(512, dtype=bool),         # False
            "linker_active_mask":  np.empty(512, dtype=bool),         # False / la
            "shape_offset":        np.empty(512, dtype=np.float32),   # 0.0
            "shape_raw":           np.empty(512, dtype=np.float32),   # 0.0
            "shape_active":        np.empty(512, dtype=bool),         # False
            "n_layers_per_ch":     np.empty(512, dtype=np.int32),     # 0
            "direct_absolute":     np.empty(512, dtype=bool),         # False
            "direct_abs_value":    np.empty(512, dtype=np.float32),   # 0.0
            "direct_abs_time":     np.empty(512, dtype=np.float64),   # -1.0
            "audio_htp_active":    np.empty(512, dtype=bool),         # False
            "audio_htp_value":     np.empty(512, dtype=np.float32),   # 0.0
            "audio_htp_time":      np.empty(512, dtype=np.float64),   # -1.0
            "out":                 np.empty(512, dtype=np.float32),   # 0.0
        }

        # 批量混音 stack 层复用缓冲（thread-local：CE 发送线程与主混音线程可并发）。
        self._batch_scratch_local = threading.local()

        # 每个 SHAPE 层 / universe 的"运行中位"（指数移动平均估计的 (max+min)/2 ）。
        # 这是形状层 anchor 的来源 —— 让单帧的偏移能稳定地表达"高出/低于自己的常态"，
        # 从而在 base 上正确叠加。
        # _shape_runmin[layer][universe] = float32[512]
        # _shape_runmax[layer][universe] = float32[512]
        self._shape_runmin: Dict[str, Dict[int, np.ndarray]] = {n: {} for n in SHAPE_LAYERS}
        self._shape_runmax: Dict[str, Dict[int, np.ndarray]] = {n: {} for n in SHAPE_LAYERS}
        # 锚点跟踪窗口的 EMA 系数（值越小越平滑，越大越响应当下）
        self._shape_anchor_ema: float = 0.02

        # ★ LTP（最后操作接管）时间戳 —— 按通道粒度
        # _ltp_editor_time[universe] = float64[512]：编辑器（Programmer/Matrix/Effect 等）
        #   最后一次写入该通道的 monotonic 时间戳。
        # _ltp_clip_time[universe]   = float64[512]：clip 最后一次「接管」该通道的时间戳。
        #   只在 notify_clip_takeover() 被调用时更新（点击播放 / 拖针落点），
        #   播放过程中每帧不刷新，确保编辑器随时可以通过新操作抢回通道。
        self._ltp_editor_time: Dict[int, np.ndarray] = {}  # {uni: float64[512]}
        self._ltp_clip_time:   Dict[int, np.ndarray] = {}  # {uni: float64[512]}
        self._ltp_editor_layer_time: Dict[str, Dict[int, np.ndarray]] = {
            name: {} for name in BASE_LAYERS + SHAPE_LAYERS
        }
        # 录制快照路径（compute_universe_snapshots_now）XY 变换输入缓存：
        # {universe: (输入帧, 输出帧)}。输入帧字节相等 → 变换确定性 → 直接复用
        # 输出，跳过每 tick 全量同步旋转（大工程 8000+ 同步设备 ~11ms → ~1µs）。
        # XY refs / Z 组命令变更时由 setter 显式失效；层数据变化天然由字节比较
        # 捕获（同一帧才命中）。仅在 _state_lock 内读写（混音循环持锁）。
        self._snapshot_xy_cache: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        self._shape_default_anchor = np.full(512, 128.0, dtype=np.float32)
        self._direct_anchor_by_universe: Dict[int, np.ndarray] = {}
        self._perf_slow_mix_ms: float = 18.0
        self._perf_last_mix_log: float = 0.0
        self._perf_stats_enabled: bool = self._read_bool_env(
            "QASCADE_PERF_LOG",
            default=True,
        )
        self._perf_stats_interval: float = 2.0
        self._perf_stats_next_log: float = time.monotonic() + self._perf_stats_interval
        self._perf_stats_frames: int = 0
        self._perf_stats_universes: int = 0
        self._perf_stats_mix_ms: float = 0.0
        self._perf_stats_send_ms: float = 0.0
        self._mix_interval_sec: float = 0.025
        self._state_lock = threading.RLock()
        self._output_worker_enabled: bool = self._read_bool_env(
            "QASCADE_OUTPUT_WORKER",
            default=True,
        )
        # Phase 2：跨 universe 批量混音门控。开启后 _mix_and_send_locked 把 N 个 universe
        # 拼成 (N,512) 一次性向量化计算，把 numpy 调用次数从 ~104×N 降到 ~104(不随 N 增长),
        # 大幅缩小 GIL ping-pong 面。默认开(实测 750 universe-frame 全分支逐字节等价标量路径 +
        # harness verify 零回归);仅在 direct 混音模式下生效(soft 模式含 EMA anchor 跨帧状态,
        # 回退逐 universe)。需回退到旧标量路径时设 QASCADE_BATCH_MIX=0。
        self._batch_mix_enabled: bool = self._read_bool_env(
            "QASCADE_BATCH_MIX",
            default=True,
        )
        self._output_worker_thread: Optional[threading.Thread] = None
        self._output_worker_stop = threading.Event()
        self._output_worker_wake = threading.Event()
        # 第3步：生成线程活跃时暂停 worker 混音，改由生成线程 render 后同步 _mix_and_send，
        # 使 render+mix 在同一个 Python 线程串行 → 消除"两个重线程抢 GIL"的车队效应。
        # 暂停期间 worker 仅空转响应 stop；生成线程停止时 resume 恢复 worker 心跳。
        self._worker_mix_suspended = threading.Event()
        self._pending_layer_updates_lock = threading.Lock()
        self._pending_layer_updates: Dict[str, Dict[int, Any]] = {}

        # B4: OutputRouter 独立输出路由（默认开启；设 QASCADE_OUTPUT_ROUTER=0 回退旧路径）
        self._use_output_router: bool = self._read_bool_env(
            "QASCADE_OUTPUT_ROUTER", default=True,
        )
        self._output_router: Optional[OutputRouter] = None

        # 自动输出域扩展：输出白名单 = 显式配置域 ∪ 工程配接域（默认开），
        # 使"配接几个域就输出几个域、无需额外设置"。
        # 设 QASCADE_AUTO_OUTPUT_UNIVERSES=0 回退旧行为（只输出配置中的域）。
        self._auto_output_universes: bool = self._read_bool_env(
            "QASCADE_AUTO_OUTPUT_UNIVERSES", default=True,
        )
        # 白名单缓存：配接变更(set_fixture_defaults_from_project)/配置变更(configure)时失效。
        self._output_allowlist_cache: Optional[set[int]] = None

        # 混音定时器
        initialize_output_services(self, final_output_observer, QTimer)
        log.info("NetworkManager 实例已创建 (id=%s)", id(self))

    @classmethod
    def instance(cls, parent=None) -> "NetworkManager":
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance

    def final_output_reader(self):
        """Optional cached readonly final-output reader; never produces output."""
        return final_output_reader(self)

    @staticmethod
    def _read_bool_env(name: str, default: bool = False) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return bool(default)
        return str(raw).strip().lower() not in ("0", "false", "off", "no", "")

    @staticmethod
    def _read_float_env(
        name: str,
        default: float,
        minimum: Optional[float] = None,
        maximum: Optional[float] = None,
    ) -> float:
        raw = os.environ.get(name)
        try:
            value = float(raw) if raw is not None else float(default)
        except Exception:
            value = float(default)
        if minimum is not None:
            value = max(float(minimum), value)
        if maximum is not None:
            value = min(float(maximum), value)
        return value

    def set_realtime_worker_enabled(self, enabled: bool):
        """Enable/disable the realtime output worker for the next start cycle."""
        with self._state_lock:
            if self._running:
                log.warning("实时输出 worker 状态只能在停止网络输出后切换")
                return
            self._output_worker_enabled = bool(enabled)

    @property
    def realtime_worker_enabled(self) -> bool:
        return bool(getattr(self, "_output_worker_enabled", False))

    # ── 配置 ────────────────────────────────────────────────────────────────

    def configure(self, config: NetworkConfig):
        return configure_nm(self, config, log)

    def set_input_filter(self, rule: Optional[FilterRule]) -> None:
        """设置输入过滤规则（None=全通/不过滤）。

        接收路径 _dispatch_frame 消费此规则：仅放行规则中的通道，其余置 0。
        """
        with self._state_lock:
            self._input_filter = rule
            self._input_filter_mask = _build_filter_mask(rule)
        log.info("输入过滤已更新: %s",
                 rule.to_expression() if rule is not None else "全通(未过滤)")

    def set_output_filter(self, rule: Optional[FilterRule]) -> None:
        """设置输出过滤规则（None=全通/不过滤）。

        发送路径 _send_to_hw 消费此规则：仅放行规则中的通道，其余置 0。
        """
        with self._state_lock:
            self._output_filter = rule
            self._output_filter_mask = _build_filter_mask(rule)
            self._mark_all_known_universes_dirty()
        log.info("输出过滤已更新: %s",
                 rule.to_expression() if rule is not None else "全通(未过滤)")

    def set_shape_gain(self, gain: float):
        with self._state_lock:
            self._shape_gain = max(0.0, min(4.0, float(gain)))
            self._mark_all_known_universes_dirty()

    @property
    def shape_gain(self) -> float:
        return self._shape_gain

    # ── ★ R183 T5：Grand Master（总控推杆；行业通用语义）──────
    def set_grand_master(self, gain: float) -> None:
        """设置总控乘性增益（0.0 = 全黑，1.0 = 直通）。

        语义（Reduce 模式）：
        末级 `floor(value * gm + 0.5)`。本仓默认 JustChannels（全场总闸）。
        写入即置脏全部已知域 → 下一混音周期生效（与 `set_layer_gain` 同一唤醒方式）。
        `gain >= 1.0` 时不走缩放分支（零开销）。
        """
        try:
            g = max(0.0, min(1.0, float(gain)))
        except (TypeError, ValueError):
            return
        if g != g or g == self._grand_master:      # NaN / 无变化
            return
        with self._state_lock:
            self._grand_master = g
            self._mark_all_known_universes_dirty()

    def grand_master(self) -> float:
        """当前总控增益（默认 1.0 = 未压暗）。"""
        return float(getattr(self, "_grand_master", DEFAULT_GM))

    def apply_grand_master(self, data):
        """把总控增益应用到一帧（末级；`gain>=1.0` 直接返回原对象）。"""
        return apply_grand_master(data, self.grand_master())

    def set_layer_gain(self, layer_name: str, gain: float) -> None:
        """设置某层输出增益（1.0=原样，0.0=静音）；用于**场景淡入淡出**。

        斜坡由调用方（调度台）逐帧推进；这里只落地并置脏，保证下一混音周期生效。
        `gain >= 1.0` 时移除条目（回到零开销路径）。
        """
        name = str(layer_name or "")
        if not name:
            return
        try:
            g = max(0.0, min(1.0, float(gain)))
        except (TypeError, ValueError):
            return
        with self._state_lock:
            if g >= 1.0:
                if self._layer_gains.pop(name, None) is None:
                    return
            else:
                if self._layer_gains.get(name) == g:
                    return
                self._layer_gains[name] = g
            self._mark_all_known_universes_dirty()

    def layer_gain(self, layer_name: str) -> float:
        """该层当前输出增益（未设置 = 1.0）。"""
        return float(self._layer_gains.get(str(layer_name or ""), 1.0))

    def active_layer_gains(self) -> Dict[str, float]:
        """当前处于淡化中的层 → 增益（空 = 没有淡化）。"""
        with self._state_lock:
            return dict(self._layer_gains)

    def set_shape_blend_mode(self, mode: str):
        """设置形状层混合模式。
        mode='direct': 直出，shape 偏移直接叠加（默认）
        mode='soft':   软压制，base 越接近边界 shape 权重越小
        """
        if mode in ("direct", "soft"):
            with self._state_lock:
                if self._shape_blend_mode == mode:
                    return
                self._shape_blend_mode = mode
                self._reset_shape_anchor_states()
                self._mark_all_known_universes_dirty()

    @property
    def shape_blend_mode(self) -> str:
        return self._shape_blend_mode

    def set_channel_defaults(self, defaults: Optional[Dict[int, Dict[int, int]]]):
        """Set idle output defaults. Keys: universe -> 0-based DMX channel -> value."""
        new_defaults: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        for uni, ch_map in (defaults or {}).items():
            try:
                u = int(uni)
            except Exception:
                continue
            data = np.zeros(512, dtype=np.uint8)
            active = np.zeros(512, dtype=bool)
            for addr, val in (ch_map or {}).items():
                try:
                    idx = int(addr)
                except Exception:
                    continue
                if 0 <= idx < 512:
                    data[idx] = max(0, min(255, int(val)))
                    active[idx] = True
            if np.any(active):
                new_defaults[u] = (data, active)
        with self._state_lock:
            apply_channel_defaults_locked(self, new_defaults)

    def _reset_shape_anchor_states(self):
        for d in self._shape_runmin.values():
            d.clear()
        for d in self._shape_runmax.values():
            d.clear()

    def _mark_all_known_universes_dirty(self):
        with self._state_lock:
            universes = set()
            for layer_data in self._layers.values():
                universes.update(int(u) for u in layer_data.keys())
            universes.update(int(u) for u in self._universe_snapshot.keys())
            universes.update(int(u) for u in self._channel_defaults.keys())
            universes.update(self._output_universe_allowlist())
            for u in universes:
                self._mark_dirty_universe(int(u))

    def set_xy_reference_map(self, refs_by_universe: Optional[Dict[int, list]]):
        with self._state_lock:
            session = begin_nm_map_binding(self, refs_by_universe)
            success = False
            try:
                before_nm_binding_mutation(self)
                self._xy_ref_map_version += 1
                self._xy_ref_compute_cache.clear()
                old_unis = set(getattr(self, "_xy_reference_by_universe", {}).keys())
                self._xy_reference_by_universe = {
                    int(uni): list(refs or [])
                    for uni, refs in (refs_by_universe or {}).items()
                    if refs
                }
                self._universe_virtual_snapshot.clear()
                # XY 参考表变更 → 快照路径的变换输出不再有效，显式失效
                self._snapshot_xy_cache.clear()
                affected_unis = old_unis | set(self._xy_reference_by_universe.keys())
                for layer_data in self._layers.values():
                    affected_unis.update(int(u) for u in layer_data.keys())
                affected_unis.update(int(u) for u in self._universe_snapshot.keys())
                affected_unis.update(int(u) for u in self._channel_defaults.keys())
                affected_unis.update(self._output_universe_allowlist())
                for u in affected_unis:
                    self._mark_dirty_universe(int(u))
                success = True
            finally:
                finish_nm_binding(self, session, success=success)

    def set_xy_reference_z_command(self, angle_deg: Optional[float],
                                   fixture_ids: Optional[list] = None):
        """同步组 Z 命令（统一世界扇面目标角；None = 清除命令，回退扇面基准）。

        Programmer 在同步模式下拖动 Z 滑块/摇杆 Z 时由 UI 推送：引擎在物理
        输出侧把该统一角写给同步设备（每台按自己 Z 范围映射 DMX），保持世界
        扇面同角（2026-08-04 用户需求）。UI 线程调用，输出线程读取（属性赋值
        原子；变更标脏受影响 universe 强制重发）。不持久化到工程状态（会话级
        实时命令，与扇面角度控件/全局角的持久基准正交）。

        ★★ 2026-09-18 用户实报修正（仅选中 12 台，全部扇子 Z 仍动）：命令
          现在随身携带**本次实际写入的设备 id 集**（fixture_ids），引擎只对该
          集合内设备套用统一角，集合外设备不响应；fixture_ids=None 保持旧
          "全组广播"语义（老调用方逐字节兼容）。
        """
        with self._state_lock:
            old = getattr(self, "_xy_reference_z_command_deg", None)
            old_ids = getattr(self, "_xy_reference_z_command_ids", None)
            if angle_deg is None:
                new = None
                new_ids = None
            else:
                new = max(-180.0, min(180.0, float(angle_deg)))
                new_ids = (frozenset(str(f) for f in fixture_ids)
                           if fixture_ids is not None else None)
            if new == old and new_ids == old_ids:
                return
            session = begin_nm_binding(self, "z")
            success = False
            try:
                self._xy_reference_z_command_deg = new
                self._xy_reference_z_command_ids = new_ids
                self._snapshot_xy_cache.clear()
                affected = set(getattr(self, "_xy_reference_by_universe", {}).keys())
                for layer_data in self._layers.values():
                    affected.update(int(u) for u in layer_data.keys())
                for u in affected:
                    self._mark_dirty_universe(int(u))
                success = True
            finally:
                finish_nm_binding(self, session, success=success)

    def set_xy_reference_from_project(self, project):
        return set_xy_from_project(self, project, build_xy_reference_map, log)

    def set_fixture_defaults_from_project(self, project):
        """Apply fixture idle defaults whenever no layer owns those DMX channels."""
        return apply_fixture_defaults(self, project, _channel_type_key, _idle_default_of, log)

    def set_motor_defaults_from_project(self, project):
        """Backward-compatible alias for older callers."""
        self.set_fixture_defaults_from_project(project)

    def find_fixture_channel_addr(
        self,
        fixture_id: str,
        ch_type: str,
        universe: Optional[int] = None,
    ) -> Optional[int]:
        project = self._project_ref
        if project is None:
            return None
        try:
            target_fid = str(fixture_id)
            target_type = _channel_type_key(ch_type)
            fdefs = {
                str(getattr(fd, "id", "")): fd
                for fd in (getattr(project, "fixture_defs", []) or [])
                if getattr(fd, "id", None)
            }
            for fi in (getattr(project, "fixtures", []) or []):
                if str(getattr(fi, "id", "")) != target_fid:
                    continue
                uni = int(getattr(fi, "universe", 1) or 1)
                if universe is not None and uni != int(universe):
                    continue
                fd = fdefs.get(str(getattr(fi, "def_id", "")))
                channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
                start = int(getattr(fi, "start_channel", 1) or 1) - 1
                for offset, candidate in enumerate(channels):
                    if _channel_type_key(candidate) == target_type:
                        addr = start + int(offset)
                        if 0 <= addr < 512:
                            return addr
        except Exception:
            pass
        return None

    def _expand_motor_pair_mask_for_universe(self, universe: int, mask: np.ndarray) -> np.ndarray:
        expanded = np.asarray(mask, dtype=bool)
        if expanded.shape != (512,):
            fixed = np.zeros(512, dtype=bool)
            n = min(expanded.size, 512)
            if n > 0:
                fixed[:n] = expanded.reshape(-1)[:n]
            expanded = fixed
        else:
            expanded = expanded.copy()
        if not np.any(expanded) or self._project_ref is None:
            return expanded
        try:
            groups = self._motor_pair_groups_for_universe(int(universe))
            for grp in groups:
                # grp 是该设备一条电机(粗+精)链的地址数组；组内任一激活则整组置位
                if expanded[grp].any():
                    expanded[grp] = True
        except Exception:
            pass
        return expanded

    def _motor_pair_groups_for_universe(self, universe: int) -> list:
        """返回该 universe 的电机粗/精配对地址组(np.array 列表),结构性缓存。

        纯取决于配接布局(设备/通道/起始地址),与帧值无关。布局变(结构签名变)才重算,
        避免每帧遍历全部设备×通道。
        """
        proj = self._project_ref
        all_fx = getattr(proj, "fixtures", []) or []
        all_defs = getattr(proj, "fixture_defs", []) or []
        sig = (id(proj), id(all_fx), len(all_fx), id(all_defs), len(all_defs))
        # B(R51 身份缓存纪律): 除签名外必须 `is` 复核被引用对象 —— 否则对象回收
        # 后 id() 复用会错误命中旧配对布局（写错电机地址）。此处用缓存内强引用
        # 复核; set_fixture_defaults_from_project 的显式失效钩子保持不变。
        if (sig != self._motor_pair_groups_sig
                or getattr(self, "_motor_pair_groups_raw", None) is not all_fx
                or getattr(self, "_motor_pair_groups_defs", None) is not all_defs):
            self._motor_pair_groups_cache = {}
            self._motor_pair_groups_sig = sig
            self._motor_pair_groups_raw = all_fx
            self._motor_pair_groups_defs = all_defs
        cached = self._motor_pair_groups_cache.get(universe)
        if cached is not None:
            return cached
        groups: list = []
        fdefs = {
            str(getattr(fd, "id", "")): fd
            for fd in all_defs if getattr(fd, "id", None)
        }
        for fi in all_fx:
            if int(getattr(fi, "universe", 1) or 1) != int(universe):
                continue
            fd = fdefs.get(str(getattr(fi, "def_id", "")))
            channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
            start = int(getattr(fi, "start_channel", 1) or 1) - 1
            by_type: Dict[str, List[int]] = {}
            for offset, ch_type in enumerate(channels):
                addr = start + int(offset)
                if 0 <= addr < 512:
                    by_type.setdefault(_channel_type_key(ch_type), []).append(addr)
            for coarse_type, fine_type in (("motor_x", "motor_x_fine"),
                                           ("motor_y", "motor_y_fine"),
                                           ("motor_z", "motor_z_fine")):
                pair = list(by_type.get(coarse_type, []) or []) + list(by_type.get(fine_type, []) or [])
                if pair:
                    groups.append(np.array(pair, dtype=np.intp))
        self._motor_pair_groups_cache[universe] = groups
        return groups

    # ── 启停 ────────────────────────────────────────────────────────────────

    def start(self):
        return start_nm(self, SACNReceiver, NicWatch, OutputRouter, log)

    def _reset_change_baseline_locked(self):
        """清空"变化域比较基准"（持 _state_lock 调用；start/stop 共用）。

        两处都必须清：
          - ``_universe_snapshot``：R178 起混音快照逐 tick 覆写，重启后若残留
            会让"新会话首帧"看起来没变；
          - ``_last_sent_snapshot``：R182 P0-1 实锤——R178 后比较基准是**上次
            实发帧**，只清前者不够。stop 会清空 router 的 ``_last_frames``，
            而残留的比较基准把"内容相同的首帧"判为未变化 -> 只注入保活标记 ->
            router 无上一帧可重发 -> 整段输出静默（用户实报"停启后不发包"）。
        """
        self._universe_snapshot.clear()
        self._last_sent_snapshot.clear()

    def _on_receiver_bind_error(self, err: ReceiverBindError) -> None:
        """接收端口绑定失败（R182 P1-5）：记录给 UI 并停稳，不向调用方抛。

        端口占用（同机另一个 DMX 软件）是可预期故障而非崩溃——修前 sACN 抛
        OSError 弹全局异常框、Art-Net 静默假运行。UI 读 ``last_bind_error``
        显示红字「端口 6454/5568 被占用」。
        """
        log.warning("NetworkManager.start() 接收端口绑定失败: %s", err)
        self._last_bind_error = str(err)
        self.stop()

    def stop(self):
        with self._state_lock:
            self._running = False
            invalidate_nm_binding(self)
            self._mixer_timer.stop()
            self._reset_output_smoother_states()
        self._stop_output_worker()
        # B4: Stop output router BEFORE cleaning up senders
        if self._output_router is not None:
            self._output_router.stop()
            self._output_router = None
        self._close_receivers()
        self._stop_discovery_watch()
        with self._state_lock:
            if self._artnet_tx:
                self._artnet_tx.close()
                self._artnet_tx = None
            _close_sacn_pool(self._sacn_tx_pool)     # R183 清查③：确定性关闭
            # R183 F2："三清"补全——只 close 不 clear 会残留已关 sender（计入状态显示+跨协议滞留）
            self._sacn_tx_pool.clear()
            for layer in self._layers.values():
                layer.clear()
            self._dirty_universes.clear()
            self._flush_scheduled = False
            self._universe_virtual_snapshot.clear()
            # A10 S4 + R182 P0-1：物理/虚拟快照与实发基准全清（防 stop 后残影）
            self._universe_snapshot.clear()
            self._last_sent_snapshot.clear()
            for d in (*self._shape_runmin.values(), *self._shape_runmax.values()):
                d.clear()
            self._ltp_editor_time.clear(); self._ltp_clip_time.clear()
            for d in self._ltp_editor_layer_time.values():
                d.clear()
        self.status_changed.emit("已停止")

    # ── ArtNet 分支启动（R178：只发不收 + ArtPoll 发现 + 定向广播） ──────────

    def _start_artnet(self, cfg, input_enabled: bool) -> None:
        """ArtNet 分支启动：按需接收器 + 多网卡定向广播发送器 + ArtPoll 发现。"""
        # 只发不收：无 TimeCode 消费者且未开输入时不绑定 6454——
        # 接收器工作在 wildcard（收全网每包），无消费者纯拖累，还占用控台端口。
        needs_rx = bool(input_enabled) or self.receivers(self.timecode_received) > 0
        if needs_rx:
            self._artnet_rx = ArtNetReceiver(cfg.input_ip)
            self._artnet_rx._dmx_enabled = bool(input_enabled)
            self._artnet_rx.timecode_received.connect(self.timecode_received.emit)
            # R182 P1-5：绑定失败上抛 ReceiverBindError，由 start() 的 except 链
            # 转成 last_bind_error 供 UI 显示（修前只 warning+return 静默）。
            self._artnet_rx.start()
            if input_enabled:
                self._artnet_rx.frame_received.connect(self._on_raw_frame_artnet)
        else:
            log.info("输出专用模式，不占用 6454")
        out_ip, extra = self._artnet_output_targets()
        self._artnet_tx = ArtNetSender(
            out_ip,
            port=int(getattr(cfg, "output_port", 0) or ARTNET_PORT),
            extra_targets=extra,
        )
        # R182 P2：ArtPoll 发现表由 NetworkManager 单点持有——**无条件**建立
        # （不只在有接收器时）。修前只发不收的常用形态下 NM 无发现表，UI 面板
        # 便自建第二个实例 → 同机两份表各收各的，节点列表对不上。
        # 接收器不存在时仅无被动数据源，主动 poll_network() 仍可用。
        self._artpoll_discovery = ArtPollDiscovery(self, bind_ip=cfg.input_ip)
        self._artpoll_discovery.start()
        if self._artnet_rx is not None:
            # 被动接线：其他控台的 ArtPoll → 发现表。
            self._artnet_rx.poll_received.connect(self._artpoll_discovery.handle_poll)
            # R182 P2：广播到 6454 的 ArtPollReply 也进发现表（被动发现不再为空）。
            self._artnet_rx.reply_received.connect(self._artpoll_discovery.handle_reply)
        log.info("ArtPoll 发现已挂接（超时 30s；poll_network 可主动扫描）")

    def _artnet_output_targets(self) -> tuple:
        """单播只发指定目标；广播模式保留逐网卡定向广播与全网兜底。

        - 0.0.0.0/空 → 全网广播；127.0.0.1 纯同机 → 不附加任何广播
          （沿既有语义：避免打扰同子网）；
        - 每张活动网卡按其真实子网掩码生成定向广播（directed_broadcast_targets
          ifaddr 真掩码口径，见 nic_broadcast.py:123；全网广播跨网段不可达 →
          逐网卡直达）。
        """
        out_ip = (self._config.output_ip or "255.255.255.255").strip()
        if out_ip in ("0.0.0.0", ""):
            out_ip = "255.255.255.255"
        extra: List[str] = []
        if self._config.output_mode != "unicast" and out_ip != "127.0.0.1":
            # dict.fromkeys 保序去重（对上游纯函数的去重再加一道防线）。
            extra = [t for t in dict.fromkeys(directed_broadcast_targets())
                     if t != out_ip]
            if out_ip != "255.255.255.255" and "255.255.255.255" not in extra:
                extra.append("255.255.255.255")   # 全网广播保留为最后兜底
        return out_ip, tuple(extra)

    def _close_receivers(self):
        if self._artnet_rx:
            self._artnet_rx.stop()
            self._artnet_rx = None
        if self._sacn_rx:
            self._sacn_rx.stop()
            self._sacn_rx = None

    def _stop_discovery_watch(self):
        """停 ArtPoll 发现与网卡监视（start 的对偶；幂等）。"""
        if self._artpoll_discovery is not None:
            self._artpoll_discovery.stop()
            self._artpoll_discovery = None
        if self._nic_watch is not None:
            self._nic_watch.stop()
            self._nic_watch = None

    # ── 网卡热重建（R178） ───────────────────────────────────────────────────

    def _on_nics_changed(self) -> None:
        """网卡集合变化：运行中才重建发送链路（层状态/混音节奏一概不动）。"""
        if not self._running:
            return
        with self._state_lock:
            if not self._running:
                return
            self._rebuild_senders_locked()
        self.status_changed.emit("网络适配器变化：输出链路已热重建")
        log.info("NicWatch: 发送链路已热重建（层状态未动）")

    def _rebuild_senders_locked(self) -> None:
        """持 _state_lock 重建 ArtNet/sACN 发送器（网卡变化热重建）。

        旧 socket 关闭瞬间在途 sendto 会被发送器逐目标 try/except 吞掉
        （仅 _errors++），新目标对后续帧立即生效。
        """
        session = begin_nm_binding(self, "rebuild")
        success = False
        try:
            if self._artnet_tx is not None:
                self._artnet_tx.close()
                self._artnet_tx = None
            if self._config.protocol == Protocol.ARTNET:
                out_ip, extra = self._artnet_output_targets()
                self._artnet_tx = ArtNetSender(
                    out_ip,
                    port=int(getattr(self._config, "output_port", 0) or ARTNET_PORT),
                    extra_targets=extra,
                )
            for uni in list(self._sacn_tx_pool):
                old = self._sacn_tx_pool.pop(uni)
                # ★ A10 P2-6：重建继承旧序列号——sACN seq 是接收端乱序/丢包检测
                # 的单调流，重置为 0 会把"新会话"伪装成迟到旧包。
                old_seq = _locked_seq_of(old)            # R183 清查③#1b：持锁读
                try:
                    old.close()
                except Exception:
                    log.debug("sACN sender close failed (universe=%s)", uni, exc_info=True)
                # ★ R183 F3：seq 继承移入 _create_sacn_sender_locked（构造返回后、
                # 入池注册前完成）——未入池不可被发送路径取到，天然免锁。
                new = self._create_sacn_sender_locked(
                    uni, inherit_seq=old_seq if old_seq else None)
            # ★ A10 S1/G16：发送链已整组替换——"上次实发帧"比较基准随旧链作废。
            #   残留会让重建后的首拍把所有域判"未变化"（只进保活标记），而保活
            #   重发源在旧 router/旧帧上 → 热重建窗口输出整段丢失。清空 = 下拍
            #   全量按已变化实发（无此窗口：socket 构造期一次性建好）。
            self._last_sent_snapshot.clear()
            success = True
        finally:
            finish_nm_binding(self, session, success=success)

    def _start_output_worker_locked(self):
        if not _drain_output_worker_zombie(self):  # ★ R193/CC-02：僵尸未退拒绝重启
            return
        if self._output_worker_thread and self._output_worker_thread.is_alive():
            return
        self._output_worker_stop.clear()
        self._output_worker_wake.clear()
        self._output_worker_thread = threading.Thread(
            target=self._output_worker_loop,
            name="DMXOutputWorker",
            daemon=True,
        )
        self._output_worker_thread.start()
        log.info("实时输出 worker 已启用")

    def _stop_output_worker(self):
        with self._state_lock:
            thread = self._output_worker_thread
            if thread is None:
                return
            self._output_worker_stop.set()
            self._output_worker_wake.set()
        if thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if thread.is_alive(): self._output_worker_zombie = thread  # ★ R193/CC-02：超时留僵尸防复活
        with self._state_lock:
            if self._output_worker_thread is thread:
                self._output_worker_thread = None
            self._output_worker_wake.clear()

    def _output_worker_loop(self):
        interval = max(0.001, float(getattr(self, "_mix_interval_sec", 0.025)))
        next_run = time.monotonic()
        while not self._output_worker_stop.is_set():
            now = time.monotonic()
            wait_time = max(0.0, next_run - now)
            self._output_worker_wake.wait(wait_time)
            if self._output_worker_stop.is_set():
                break
            self._output_worker_wake.clear()
            now = time.monotonic()
            if now < next_run:
                continue
            next_run = now + interval
            # 生成线程活跃时让路：mix 由生成线程串行执行，worker 不再并发 mix(消除 GIL 车队)。
            if self._worker_mix_suspended.is_set():
                continue
            try:
                self._mix_and_send()
            except Exception:
                log.exception("实时输出 worker 混合发送失败")

    def suspend_worker_mix(self) -> None:
        """暂停 worker 混音(生成线程接管)。幂等。"""
        self._worker_mix_suspended.set()

    def resume_worker_mix(self) -> None:
        """恢复 worker 混音。幂等。"""
        self._worker_mix_suspended.clear()
        self._output_worker_wake.set()

    def _get_or_create_sacn_sender(self, universe_1based: int) -> Optional[SACNSender]:
        with self._state_lock:
            if universe_1based in self._sacn_tx_pool:
                return self._sacn_tx_pool[universe_1based]
            return self._create_sacn_sender_locked(universe_1based)

    def _create_sacn_sender_locked(self, universe_1based: int,
                                   inherit_seq: Optional[int] = None) -> Optional[SACNSender]:
        """创建并登记单域 sACN 发送器（持 _state_lock 调用；失败返回 None）。

        ★ R183 F3：``inherit_seq``（旧发送器 seq）在构造返回后、入池注册
        **之前**写入——未入池的 sender 不会被发送路径（_sacn_sender_fast /
        直发回退）取到，继承写天然免锁。修法消除了"先入池后覆写"窗口：
        router 线程曾在该窗口以 0 起步发包、随后被覆写成后向跳变（接收端
        按乱序丢包）。
        """
        # ★ A10 S1/G16 协议门：ArtNet 会话禁止建 sACN sender（热重建/直发
        # 回退路径曾跨协议建池 → 同机 5568 端口被无主占用 + 双协议并流）。
        if self._config.protocol != Protocol.SACN:
            return None
        try:
            cfg = self._config
            # sACN 单播目标与 ArtNet 同款归一化：空/0.0.0.0 → 广播地址，
            # 否则广播 IP 时 sendto 静默失败仅 _errors++（无 SO_BROADCAST 会丢包）。
            target_ip = ""
            if cfg.output_mode == "unicast":
                target_ip = (cfg.output_ip or "255.255.255.255").strip()
                if target_ip in ("0.0.0.0", ""):
                    target_ip = "255.255.255.255"
            sender = SACNSender(
                target_ip=target_ip,
                universe=universe_1based,
                multicast=(cfg.output_mode != "unicast"),
                priority=cfg.sacn_priority,
            )
            if inherit_seq:
                sender._seq = inherit_seq % 256
            self._sacn_tx_pool[universe_1based] = sender
            return sender
        except Exception:
            mark_nm_binding_failed(self)
            return None

    # ── 层数据写入 ──────────────────────────────────────────────────────────

    def _mark_dirty_universe(self, universe: int):
        try:
            with self._state_lock:
                self._dirty_universes.add(int(universe))
                if not self._running:
                    return
                if self._output_worker_enabled:
                    self._output_worker_wake.set()
                    return
                # 可靠性优先：所有操作路径写入层后，立即安排一次异步混合发送。
                # 这样即使 25ms mixer timer 被 UI 卡顿延迟，或者用户只操作了一次，
                # 也不会表现为“完全没反应”。_flush_scheduled 会合并同一事件循环内的大量写入。
                if not getattr(self, "_flush_scheduled", False):
                    self._flush_scheduled = True
                    try:
                        QTimer.singleShot(0, self._mix_and_send)
                    except Exception:
                        self._flush_scheduled = False
        except Exception:
            pass

    def queue_clip_layer_frame(
        self,
        layer_name: str,
        outputs: Mapping[int, Tuple[np.ndarray, np.ndarray]],
        clear_universes: Optional[set[int] | List[int] | Tuple[int, ...]] = None,
    ) -> set[int]:
        """Queue the latest clip-layer frame for the realtime output worker.

        Timeline playback must not wait behind network mixing or UDP sends.
        This queue is latest-frame-wins: if the worker is busy, older playback
        frames for the same layer/universe are replaced instead of piling up.
        """
        if layer_name not in CLIP_LAYERS:
            return set()

        items: List[Tuple[int, np.ndarray, np.ndarray]] = []
        for uni, entry in (outputs or {}).items():
            try:
                universe = int(uni)
                data, active = entry
            except Exception:
                continue
            d = np.asarray(data, dtype=np.uint8)
            if d.shape != (512,):
                fixed = np.zeros(512, dtype=np.uint8)
                n = min(d.size, 512)
                if n > 0:
                    fixed[:n] = d.reshape(-1)[:n]
                d = fixed
            a = np.asarray(active, dtype=bool)
            if a.shape != (512,):
                fixed_a = np.zeros(512, dtype=bool)
                n = min(a.size, 512)
                if n > 0:
                    fixed_a[:n] = a.reshape(-1)[:n]
                a = fixed_a
            items.append((universe, d, a))

        prepared = _queue_batch_rows(items)

        clear_set = {int(u) for u in (clear_universes or [])}
        if not prepared and not clear_set:
            return set()

        changed = set(prepared.keys()) | clear_set
        if not queue_project_frame(self, layer_name, prepared, clear_set):
            return set()

        if bool(getattr(self, "_running", False)):
            if bool(getattr(self, "_output_worker_enabled", False)):
                self._output_worker_wake.set()
            else:
                try:
                    QTimer.singleShot(0, self._mix_and_send)
                except Exception:
                    pass
        return changed

    def _drain_pending_layer_updates_locked(self) -> set[int]:
        return drain_layer_frames_locked(self, frame_equal=_frame_bit_equal)

    def update_layer(
        self,
        layer_name: str,
        universe: int,
        data: np.ndarray,
        active: Optional[np.ndarray] = None,
        touch_ltp: bool = True,
    ):
        """写入某一层的整帧数据。

        active=None 时默认所有通道均为显式写入（全 True）。
        建议调用方显式传 active 以避免歧义。
        """
        return update_layer_frame(self, layer_name, universe, data, active=active,
                                  touch_ltp=touch_ltp, frame_equal=_frame_bit_equal)

    def _touch_editor_ltp(self, universe: int, mask, layer_name: str) -> None:
        """编辑器层写入后的 LTP 触点：展开电机配对掩码 → 更新编辑器时间戳。

        原实现把这两行在 10 处逐字复刻（update_layer / update_layer_sparse* /
        merge_layer），抽成单点以统一「写入的通道集合 → LTP 触点」这条语义。
        """
        expanded = self._expand_motor_pair_mask_for_universe(int(universe), mask)
        self._ltp_touch_editor(int(universe), expanded, layer_name=layer_name)

    def update_layer_sparse(
        self, layer_name: str, universe: int, channels: Dict[int, int],
        touch_ltp: bool = True,
    ):
        """仅更新少数通道（稀疏写入）。channels: {dmx_addr_0_indexed: value}"""
        if layer_name not in self._layers:
            return
        try:
            universe = int(universe)
        except Exception:
            universe = 1
        ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
        with self._state_lock:
            prev = self._layers[layer_name].get(universe)
            valid_updates = []
            changed = prev is None
            can_fast_skip = prev is not None and universe in self._universe_snapshot
            for addr, val in channels.items():
                try:
                    addr = int(addr)
                except Exception:
                    continue
                if 0 <= addr < 512:
                    out_val = max(0, min(255, int(val)))
                    valid_updates.append((addr, out_val))
                    if can_fast_skip and (not bool(prev[1][addr]) or int(prev[0][addr]) != out_val):
                        changed = True
            if not valid_updates:
                return
            if can_fast_skip and not changed:
                if touch_ltp and ltp_layer:
                    self._touch_editor_ltp(int(universe), _addr_mask([a for a, _v in valid_updates]), layer_name)
                    self._mark_dirty_universe(universe)
                return

            if prev is None:
                d = np.zeros(512, dtype=np.uint8)
                a = np.zeros(512, dtype=bool)
            else:
                d = prev[0].copy()
                a = prev[1].copy()
            for addr, val in valid_updates:
                d[addr] = val
                a[addr] = True
            touch_mask = _addr_mask([a for a, _v in valid_updates])
            # H3: `changed` 已在上方对每个 valid_update 逐字节精确计算
            # （prev 未激活 或 值不同 → changed=True；prev is None → changed=True）。
            # data/active 的任何实际变化都被 changed 覆盖（motor-fine 扩展只影响
            # touch_mask 的 LTP，不改 d/a），故末尾冗余的整 512 array_equal 比较可去除。
            self._layers[layer_name][universe] = (d, a)
            # ★ LTP：编辑器层写入时更新对应通道的编辑器时间戳
            if touch_ltp and ltp_layer:
                self._touch_editor_ltp(int(universe), touch_mask, layer_name)
            self._mark_dirty_universe(universe)

    def update_layer_sparse_batch(
        self,
        layer_name: str,
        updates: Mapping[int, Mapping[int, int]],
        touch_ltp: bool = True,
    ) -> set[int]:
        """Batch sparse writes for several universes and wake the output worker once."""
        if layer_name not in self._layers or not updates:
            return set()
        changed_universes: set[int] = set()
        ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
        with self._state_lock:
            for universe, channels in updates.items():
                try:
                    universe_i = int(universe)
                except Exception:
                    universe_i = 1
                prev = self._layers[layer_name].get(universe_i)
                valid_updates = []
                changed = prev is None
                can_fast_skip = prev is not None and universe_i in self._universe_snapshot
                for addr, val in (channels or {}).items():
                    try:
                        addr_i = int(addr)
                    except Exception:
                        continue
                    if 0 <= addr_i < 512:
                        out_val = max(0, min(255, int(val)))
                        valid_updates.append((addr_i, out_val))
                        if can_fast_skip and (
                            not bool(prev[1][addr_i]) or int(prev[0][addr_i]) != out_val
                        ):
                            changed = True
                if not valid_updates:
                    continue
                if can_fast_skip and not changed:
                    if touch_ltp and ltp_layer:
                        self._touch_editor_ltp(
                            universe_i, _addr_mask([a for a, _v in valid_updates]), layer_name)
                        self._dirty_universes.add(universe_i)
                        changed_universes.add(universe_i)
                    continue

                if prev is None:
                    d = np.zeros(512, dtype=np.uint8)
                    a = np.zeros(512, dtype=bool)
                else:
                    d = prev[0].copy()
                    a = prev[1].copy()
                for addr_i, out_val in valid_updates:
                    d[addr_i] = out_val
                    a[addr_i] = True
                touch_mask = _addr_mask([x for x, _v in valid_updates])
                # H3: `changed` 已逐字节精确计算，末尾整 512 array_equal 冗余，去除。
                self._layers[layer_name][universe_i] = (d, a)
                if touch_ltp and ltp_layer:
                    self._touch_editor_ltp(universe_i, touch_mask, layer_name)
                self._dirty_universes.add(universe_i)
                changed_universes.add(universe_i)

            if changed_universes and self._running:
                if self._output_worker_enabled:
                    self._output_worker_wake.set()
                elif not getattr(self, "_flush_scheduled", False):
                    self._flush_scheduled = True
                    try:
                        QTimer.singleShot(0, self._mix_and_send)
                    except Exception:
                        self._flush_scheduled = False
        return changed_universes

    def update_layer_sparse_arrays(
        self,
        layer_name: str,
        universes,
        addrs,
        values,
        touch_ltp: bool = True,
    ) -> set[int]:
        """Vectorized sparse writes for hot realtime paths.

        universes/addrs/values are parallel arrays. This preserves the same
        layer semantics as update_layer_sparse_batch but avoids building large
        Python dictionaries for every frame.
        """
        if layer_name not in self._layers:
            return set()
        universes_arr = np.asarray(universes, dtype=np.int32).reshape(-1)
        addrs_arr = np.asarray(addrs, dtype=np.int32).reshape(-1)
        values_arr = np.asarray(values, dtype=np.uint8).reshape(-1)
        n = min(universes_arr.size, addrs_arr.size, values_arr.size)
        if n <= 0:
            return set()
        universes_arr = universes_arr[:n]
        addrs_arr = addrs_arr[:n]
        values_arr = values_arr[:n]
        valid = (addrs_arr >= 0) & (addrs_arr < 512)
        if not np.any(valid):
            return set()
        universes_arr = universes_arr[valid]
        addrs_arr = addrs_arr[valid]
        values_arr = values_arr[valid]
        changed_universes: set[int] = set()
        ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
        with self._state_lock:
            for universe_i in np.unique(universes_arr):
                uni = int(universe_i)
                mask = universes_arr == uni
                if not np.any(mask):
                    continue
                addr_u = addrs_arr[mask].astype(np.int32, copy=False)
                val_u = values_arr[mask].astype(np.uint8, copy=False)
                prev = self._layers[layer_name].get(uni)
                if prev is not None and uni in self._universe_snapshot:
                    active_prev = prev[1][addr_u]
                    data_prev = prev[0][addr_u]
                    if bool(np.all(active_prev)) and bool(np.array_equal(data_prev, val_u)):
                        if touch_ltp and ltp_layer:
                            self._touch_editor_ltp(uni, _addr_mask(addr_u), layer_name)
                            self._dirty_universes.add(uni)
                            changed_universes.add(uni)
                        continue
                if prev is None:
                    d = np.zeros(512, dtype=np.uint8)
                    a = np.zeros(512, dtype=bool)
                else:
                    d = prev[0].copy()
                    a = prev[1].copy()
                d[addr_u] = val_u
                a[addr_u] = True
                # H3: 上方早跳已对被写地址子集精确比对（全 active 且值相等才跳过）；
                # 走到这里必有地址变化/激活，末尾整 512 array_equal 冗余，去除。
                self._layers[layer_name][uni] = (d, a)
                if touch_ltp and ltp_layer:
                    self._touch_editor_ltp(uni, _addr_mask(addr_u), layer_name)
                self._dirty_universes.add(uni)
                changed_universes.add(uni)

            if changed_universes and self._running:
                if self._output_worker_enabled:
                    self._output_worker_wake.set()
                elif not getattr(self, "_flush_scheduled", False):
                    self._flush_scheduled = True
                    try:
                        QTimer.singleShot(0, self._mix_and_send)
                    except Exception:
                        self._flush_scheduled = False
        return changed_universes

    def update_layer_sparse_grouped_arrays(
        self,
        layer_name: str,
        grouped_updates,
        touch_ltp: bool = True,
    ) -> set[int]:
        """Vectorized sparse writes where callers already grouped by universe.

        grouped_updates: iterable of (universe, addrs, values).  This keeps the
        same sparse-layer semantics as update_layer_sparse_arrays while avoiding
        per-frame np.unique/mask work in hot paths that have stable address maps.
        """
        if layer_name not in self._layers or not grouped_updates:
            return set()
        changed_universes: set[int] = set()
        ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
        with self._state_lock:
            for item in grouped_updates:
                try:
                    universe_i, addrs, values = item
                except Exception:
                    continue
                try:
                    uni = int(universe_i)
                except Exception:
                    uni = 1
                addr_u = np.asarray(addrs, dtype=np.int32).reshape(-1)
                val_u = np.asarray(values, dtype=np.uint8).reshape(-1)
                n = min(addr_u.size, val_u.size)
                if n <= 0:
                    continue
                addr_u = addr_u[:n]
                val_u = val_u[:n]
                valid = (addr_u >= 0) & (addr_u < 512)
                if not np.all(valid):
                    if not np.any(valid):
                        continue
                    addr_u = addr_u[valid]
                    val_u = val_u[valid]

                prev = self._layers[layer_name].get(uni)
                if prev is not None and uni in self._universe_snapshot:
                    active_prev = prev[1][addr_u]
                    data_prev = prev[0][addr_u]
                    if bool(np.all(active_prev)) and bool(np.array_equal(data_prev, val_u)):
                        if touch_ltp and ltp_layer:
                            self._touch_editor_ltp(uni, _addr_mask(addr_u), layer_name)
                            self._dirty_universes.add(uni)
                            changed_universes.add(uni)
                        continue
                if prev is None:
                    d = np.zeros(512, dtype=np.uint8)
                    a = np.zeros(512, dtype=bool)
                else:
                    d = prev[0].copy()
                    a = prev[1].copy()
                d[addr_u] = val_u
                a[addr_u] = True
                # H3: 上方早跳已对被写地址子集精确比对（全 active 且值相等才跳过）；
                # 走到这里必有地址变化/激活，末尾整 512 array_equal 冗余，去除。
                self._layers[layer_name][uni] = (d, a)
                if touch_ltp and ltp_layer:
                    self._touch_editor_ltp(uni, _addr_mask(addr_u), layer_name)
                self._dirty_universes.add(uni)
                changed_universes.add(uni)

            if changed_universes and self._running:
                if self._output_worker_enabled:
                    self._output_worker_wake.set()
                elif not getattr(self, "_flush_scheduled", False):
                    self._flush_scheduled = True
                    try:
                        QTimer.singleShot(0, self._mix_and_send)
                    except Exception:
                        self._flush_scheduled = False
        return changed_universes

    def merge_layer(
        self,
        layer_name: str,
        universe: int,
        data: np.ndarray,
        active: Optional[np.ndarray] = None,
        touch_ltp: bool = True,
    ):
        """稀疏合并写入：只更新 active=True 的通道，保留该层其它通道已有的数据。

        ★ v23 新增：解决摇杆/音频操作时覆盖清零其它类型通道的问题。
        与 update_layer 的区别：update_layer 是全量替换，merge_layer 是增量合并。
        """
        if layer_name not in self._layers:
            return

        d = np.asarray(data, dtype=np.uint8)
        if d.shape != (512,):
            buf = np.zeros(512, dtype=np.uint8)
            n = min(d.size, 512)
            buf[:n] = d.flatten()[:n]
            d = buf

        if active is None:
            # active=None 等同于全量替换
            a = np.ones(512, dtype=bool)
        else:
            a = np.asarray(active, dtype=bool)
            if a.shape != (512,):
                buf = np.zeros(512, dtype=bool)
                n = min(a.size, 512)
                buf[:n] = a.flatten()[:n]
                a = buf

        ltp_layer = layer_name in BASE_LAYERS or layer_name in SHAPE_LAYERS
        with self._state_lock:
            prev = self._layers[layer_name].get(universe)
            if prev is not None:
                # 合并：只覆盖本次 active 的通道
                merged_d = prev[0].copy()
                merged_a = prev[1].copy()
                mask = a
                merged_d[mask] = d[mask]
                merged_a[mask] = True
                if (int(universe) in self._universe_snapshot
                        and np.array_equal(prev[0], merged_d) and np.array_equal(prev[1], merged_a)):
                    if touch_ltp and ltp_layer:
                        self._touch_editor_ltp(int(universe), a, layer_name)
                        self._mark_dirty_universe(universe)
                    return
                self._layers[layer_name][universe] = (merged_d, merged_a)
            else:
                # 该层该 universe 还没有数据，直接写入
                self._layers[layer_name][universe] = (d.copy(), a.copy())
            # ★ LTP：编辑器层写入时更新对应通道的编辑器时间戳
            if touch_ltp and ltp_layer:
                a = self._expand_motor_pair_mask_for_universe(int(universe), a)
                self._ltp_touch_editor(int(universe), a, layer_name=layer_name)
            self._mark_dirty_universe(universe)

    def _drop_pending_layer_updates(self, layer_name: str,
                                   universe: Optional[int] = None) -> None:
        """丢弃该层（或该层某 universe）已排队但尚未 drain 的帧。

        ★★ 2026-09-17 深查修正（release 后旧帧写回）：`_pending_layer_updates` 是
          "最新帧胜出"队列，`_drain_pending_layer_updates_locked` 在每次混音前把它
          提交进 `_layers`。**任何 clear/release 路径都不清它** → 清理与写入存在
          两个真源：停止播放后再清层，队列里那一帧随后被 drain 写回，clip/编辑器
          层"清完又亮"（且 clip LTP 已清零 → 该帧长期盖住编辑器输出）。
        """
        if not self._pending_layer_updates:
            return
        with self._pending_layer_updates_lock:
            if universe is None:
                self._pending_layer_updates.pop(str(layer_name), None)
            else:
                per_u = self._pending_layer_updates.get(str(layer_name))
                if isinstance(per_u, dict):
                    per_u.pop(int(universe), None)

    def _clear_all_pending_updates(self) -> None:
        """清空整个排队队列（全清空用；与 `_drop_pending_layer_updates` 同锁）。"""
        with self._pending_layer_updates_lock:
            self._pending_layer_updates.clear()

    def clear_layer(self, layer_name: str, universe: Optional[int] = None):
        return clear_layer_frame(self, layer_name, universe)

    def flush_universes_now(self, universes: Optional[List[int]] = None):
        """立即把当前混合结果发送到硬件。

        时间线最小回放的确定性兜底：PlaybackEngine 写 ``timeline`` 层后立即
        混合+发送，避免"clip 命中了但灯没有反应"。★ R193/P1-13：混音在锁内，
        发送出锁后统一执行（持锁逐域 sendto 挡 worker 25ms tick）。
        """
        with self._state_lock:
            try:
                if universes is None:
                    universe_set = set()
                    for layer in self._layers.values():
                        universe_set.update(layer.keys())
                    universes = sorted(universe_set)
                else:
                    universes = sorted({int(u) for u in universes})
                universe_set = {int(u) for u in universes}

                snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {name: {} for name in ALL_LAYERS}
                for name in ALL_LAYERS:
                    layer = self._layers[name]
                    for uni in universes:
                        entry = layer.get(int(uni))
                        if entry is not None:
                            snapshots[name][int(uni)] = entry

                for uni in universes:
                    try:
                        virtual_out, clip_win_mask = self._mix_one_universe(
                            snapshots, int(uni), update_anchor=False,
                            return_clip_mask=True)
                        physical_out = self._apply_xy_reference(
                            int(uni), virtual_out, clip_win_mask=clip_win_mask)
                        # ★ SafetyInterlock：flush 路径也过联锁
                        try:
                            from ..engine.safety_interlock import get_interlock as _get_ilock
                            _ilock = _get_ilock()
                            if _ilock.is_armed:
                                physical_out = _ilock.process(int(uni), physical_out)
                        except Exception:
                            # C: 安全联锁曾被静默吞掉（联锁失效无任何日志）。
                            # flush 为非热路径（按需触发），直接 exception 可见。
                            log.exception(
                                "SafetyInterlock.process failed on flush (universe=%s)", uni)
                        # ★ 性能：无 xy refs/联锁时 physical_out 即 virtual_out，
                        #   两份快照只拷贝一次（读者全部只读：get_virtual_universe_data
                        #   返回 .copy()；混音输入取 _layers 而非快照）。
                        if physical_out is virtual_out:
                            self._universe_snapshot[int(uni)] = physical_out.copy()
                            self._universe_virtual_snapshot[int(uni)] = self._universe_snapshot[int(uni)]
                        else:
                            self._universe_virtual_snapshot[int(uni)] = virtual_out.copy()
                            self._universe_snapshot[int(uni)] = physical_out.copy()
                        if self._running:
                            accept_single_source(self, int(uni), physical_out)
                    except Exception:
                        log.exception("flush_universes_now() failed for universe %s", uni)
                self._dirty_universes.difference_update(universe_set)
            except Exception:
                log.exception("flush_universes_now() failed")
        # ★ R193/P1-13 复盘：曾试把发送移出锁外，但 R185 tap 契约测试
        #   （test_r9_original_acceptance_paths_lock…）锁定"接受路径必须持
        #   _state_lock"的产品不变量——出锁发送违约。AUD-05 的 GUI 代价改由
        #   积压项处理（worker-enabled 早退语义，需产品决策）。

    def _release_all_outputs_locked(self, defer_send: bool = False):
        # ★ R194/AUD-05：零帧混音/发送抽至 _release_known_universes（defer 路径）
        # 1. 收集当前有数据的 universe（含配置/配接域）——清空前先记录
        known_universes: set = set(self._output_universe_allowlist())
        for layer_data in self._layers.values():
            known_universes.update(layer_data.keys())

        # 2. 清空所有层（含排队未 drain 的帧，否则清完立刻写回）
        self._clear_all_pending_updates()
        for name in list(self._layers.keys()):
            self._layers[name].clear()
        self._dirty_universes.clear(); self._layer_gains.clear()   # 淡化层增益一并归还（防永久压暗）
        self._universe_virtual_snapshot.clear()
        self._last_heartbeat_send = time.monotonic()
        self._reset_output_smoother_states()
        # 同步清空形状层的 anchor 缓存
        for name in list(self._shape_runmin.keys()):
            self._shape_runmin[name].clear()
            self._shape_runmax[name].clear()
        # 同步清空 LTP 时间戳
        self._ltp_editor_time.clear()
        self._ltp_clip_time.clear()
        for d in self._ltp_editor_layer_time.values():
            d.clear()

        # 3. 立即向所有已知 universe 发送全零帧（不等待 25ms 混音器）
        default_universes = set(getattr(self, "_channel_defaults", {}).keys())
        known_universes.update(default_universes)
        if self._running and known_universes:
            _release_known_universes(self, sorted(known_universes), defer_send=defer_send)
        log.info("release_all_outputs: 已清空所有层并发送全零帧 (universes=%s)",
                 sorted(known_universes))

    def request_universe_refresh(self, universes=None):
        """★ R194/AUD-05：GUI 刷新请求——worker 启用时置脏+唤醒（毫秒级出光），
        否则回退同步 flush。发送仍在 `_state_lock` 内（R185 tap 契约不变）；
        清台/XY 变更等 GUI 入口改走本方法，不再持锁做全域混音+发送。"""
        try:
            if self._running and self._output_worker_enabled:
                _mark_refresh_dirty(self, universes)
                self._output_worker_wake.set()
                return
        except Exception:
            log.exception("request_universe_refresh degraded to sync flush")
        self.flush_universes_now(universes)

    def release_all_outputs(self, defer_send: bool = False):
        """全清空：清除所有层的所有数据，并立即向所有已知 universe 发送全零帧。

        这是一个「紧急熄灭」操作，会让所有灯立即归零。
        调用后各引擎（PlaybackEngine / Programmer 等）的下一次写入会自然恢复正常输出，
        不需要任何额外重置操作。
        ★ R194/AUD-05：``defer_send=True`` 时零帧延后由 worker 下一拍混出
        （GUI 不再持 `_state_lock` 做全域混音+发送）；仅 worker 启用时使用。
        """
        with self._state_lock:
            self._release_all_outputs_locked(defer_send=defer_send)
        if defer_send:
            self._output_worker_wake.set()

    def clear_layer_channels(
        self, layer_name: str, universe: int, channels: List[int]
    ):
        with self._state_lock:
            prev = self._layers.get(layer_name, {}).get(universe)
            if prev is None:
                return
            d = prev[0].copy()
            a = prev[1].copy()
            for addr in channels:
                if 0 <= addr < 512:
                    a[addr] = False
                    d[addr] = 0
            if np.array_equal(prev[0], d) and np.array_equal(prev[1], a):
                return
            self._layers[layer_name][universe] = (d, a)
            clear_mask = np.zeros(512, dtype=bool)
            for addr in channels:
                if 0 <= addr < 512:
                    clear_mask[int(addr)] = True
            self._clear_layer_ltp_locked(layer_name, [int(universe)], {int(universe): clear_mask})
            self._mark_dirty_universe(universe)

    # ── 实时混合 ────────────────────────────────────────────────────────────

    def _mix_and_send(self):
        """25ms 定时混音器：唯一的硬件输出路径。
        ★ 性能优化：大量设备/多层信号时只处理脏 universe；空闲时低频心跳发送快照。
        ★ v3 (2026-08-04) 发送链优化：批量推送 — 一次 gate + 一次
          router.push_batch（单次持锁），替代旧逐域 _send_to_hw（逐域
          import/门禁/白名单/push O(n) 扫描），73 域/tick 的 Python 段
          大幅缩短，混音线程 GIL 占用与 router 线程等待双双下降。
        """
        packets: List[Tuple[int, np.ndarray]] = []
        with self._state_lock:
            packets = self._mix_and_send_locked()
            records = capture_nm_sources(self, packets)
        if not packets:
            return
        t0 = time.perf_counter()
        try:
            invoke_nm_batch(self, packets, records)
        except Exception:
            log.exception("DMX send failed for batch of %s universes", len(packets))
        send_ms = (time.perf_counter() - t0) * 1000.0
        self._record_perf_stats(0, 0.0, send_ms)

    def _send_batch_to_hw(self, packets: List[Tuple[int, np.ndarray]]) -> None:
        """批量推送一批已混音帧到硬件输出（worker 线程调用）。

        语义 = 逐帧 _send_to_hw 的合批（输出字节逐帧一致）：
          - 校验点 #4（白名单）逐域保留；validate_dmx_buffer 逐域保留；
          - ★ R141：输出通道过滤逐域套用（修前此处漏掉 → 规则整条失效）；
          - ★ R178：变化域发送 — 与 _universe_snapshot 逐字节相同的域不再
            实发，改由 router 按 keepalive_interval(1s) 重发上一帧；
          - B4 router：一次 push_batch（单次持锁 + 逐域 copy）。
        """
        if not packets:
            return
        allowed = self._output_universe_allowlist()
        batch: Dict[int, np.ndarray] = {}
        for uni, data in packets:
            # 校验点 #4（DMX 缓冲区验证）+ R141 输出通道过滤（与 _send_to_hw 同源）。
            data = self._prepare_batch_frame(int(uni), data)
            # 校验点 #5：只发送白名单中的 universe。
            allowed = _uni_allowed(self, int(uni), allowed)
            if int(uni) not in allowed:
                continue
            batch[int(uni)] = data
        if not batch:
            return
        deliver_prepared_batch(self, packets, batch)

    def _dispatch_via_router(self, batch: dict) -> bool:
        """router 路径：变化域实发 + 未变域 1s 保活标记（R178）。

        返回 False 表示 router 不可用，调用方回退逐帧直发。
        """
        router = self._output_router
        if not (self._use_output_router and router is not None and router.is_alive):
            return False
        stats = self._skip_stats
        if stats is None:
            stats = self._skip_stats = {"skipped": 0, "sent": 0}
        # ★ R178 P0 修复（审计 A）：比较基准必须是**上次实发帧**（_last_sent_snapshot）
        #   而非当拍混音快照 _universe_snapshot——后者每 tick 更新，与当拍 batch
        #   恒等 → 全部判"未变化"进 keepalive，而 keepalive 只重发实发记录
        #   （_last_frames 为空）→ 输出整体被吞（实测 7 帧变值 sink 收 0）。
        last_sent = getattr(self, "_last_sent_snapshot", None)
        if last_sent is None:
            last_sent = self._last_sent_snapshot = {}
        changed, keepalive = _batch_change_split(last_sent, batch, stats)
        if changed:
            push_changed_sources(self, router, changed)
            self._commit_sent_baseline(last_sent, changed)
        if keepalive:
            router.send_heartbeat(keepalive)
        return True

    def _commit_sent_baseline(self, last_sent: dict, changed: dict) -> None:
        """变化域入队后登记"上次实发"基准（R183 F7：持锁 + 运行态门）。

        ★ R183 F7：登记移入 _state_lock——修前在锁外写
          _last_sent_snapshot，而 _stop_output_worker join 超时 1s 不保证
          worker 已退，stop/start 清空基准后仍可能被残存 worker 写回 →
          残基线带进新会话（R182 P0-1 同症状复发窗口）。登记与 stop/
          start 的清空同锁全序，且 _running=False 时不再登记。
          注：登记时点实为"入队即登记"（router 异步实发），非实发后。
        """
        registered = {uni: np.array(data, copy=True)
                      for uni, data in changed.items()}
        with self._state_lock:
            if self._running:
                last_sent.update(registered)   # 入队即登记（router 异步实发）

    def get_send_skip_stats(self) -> dict:
        """变化域跳过/实发计数（诊断用；_dispatch_via_router 累计）。"""
        stats = self._skip_stats
        return dict(stats) if stats else {"skipped": 0, "sent": 0}

    def _prepare_batch_frame(self, universe: int, data: np.ndarray) -> np.ndarray:
        """批量路径的逐域末级处理：guards 验证 → 输出通道过滤（R141）→ 总控（T5）。

        修前输出过滤只有 `_send_to_hw` 有，而混音器走的是批量路径 →
        「合批 = 逐帧串行」的输出字节语义实际不成立。两路现共用同一批末级函数。

        ★ R183 T5：Grand Master 加在**过滤之后**（与 在 Universe 写完值后
        applyGM 同序）——总控是"对最终下发帧"的总闸，不该被通道过滤绕过。
        """
        try:
            from ..utils.guards import validate_dmx_buffer as _vdb
            data = _vdb(data, universe=int(universe))
        except ImportError:
            # C: guards 缺失曾完全静默 → DMX 缓冲区不再有防御校验。
            # 25ms 热路径, 用一次性标记避免每批刷屏（不改行为）。
            if not getattr(self, "_vdb_missing_logged", False):
                self._vdb_missing_logged = True
                log.warning(
                    "validate_dmx_buffer unavailable (guards import failed); "
                    "DMX buffer validation disabled")
        return self.apply_grand_master(_apply_output_filter(self._output_filter_mask, data))

    def _send_direct_frame(self, universe: int, data: np.ndarray) -> None:
        """router 关闭 / router 线程内的裸 I/O 分发（校验与过滤已在调用处完成）。

        ★ A10 S1/G16：按**会话协议**分派，不再按"_artnet_tx 是否存在"猜测——
        修前 ArtNet 会话在热重建窗口（_artnet_tx 短暂为 None）会落进 sACN
        分支并顺手建出 sACN sender（跨协议串包 + 池被错误占用）。ArtNet
        会话窗口内宁丢当帧（UDP 语义，下一拍心跳/变化即恢复），不串协议。
        """
        if self._config.protocol == Protocol.ARTNET:
            tx = self._artnet_tx
            if tx is not None:
                if self._final_output_tap is None:
                    tx.send_dmx(_to_artnet_universe(universe), data)
                else:
                    call_actual_sender(self, tx.send_dmx, _to_artnet_universe(universe), data,
                                       protocol="Art-Net", universe_1based=universe)
            return
        s = self._sacn_sender_fast(universe)
        if s is not None:
            if self._final_output_tap is None:
                s.send_dmx(universe, data)
            else:
                call_actual_sender(self, s.send_dmx, universe, data,
                                   protocol="sACN", universe_1based=universe)

    def _mix_and_send_locked(self) -> List[Tuple[int, np.ndarray]]:
        """Run one mix pass and return packets to send outside ``_state_lock``."""
        t0 = time.perf_counter()
        self._flush_scheduled = False
        if not self._running:
            return []
        self._drain_pending_layer_updates_locked()
        output_unis = self._output_universe_allowlist()
        output_unis.update(int(u) for u in getattr(self, "_channel_defaults", {}).keys())

        now = time.monotonic()
        dirty_unis = set(getattr(self, "_dirty_universes", set()))
        if dirty_unis:
            self._dirty_universes.difference_update(dirty_unis)
            all_unis = sorted(dirty_unis)
        else:
            # 空闲态:心跳到期时把需保活的全部 universe 装入轮转队列;每个 tick 只取一批,
            # 按"一个心跳周期内的 tick 数"均分,保证整批在下次心跳前刚好混完(每路刷新频率
            # 不变),但单次持锁只混一小批,锁占用大幅缩短。
            if now - getattr(self, "_last_heartbeat_send", 0.0) >= getattr(self, "_heartbeat_interval", 1.0):
                self._last_heartbeat_send = now
                queue = sorted(set(output_unis) | set(self._universe_snapshot.keys()))
                self._heartbeat_queue = queue
                interval = max(0.001, float(getattr(self, "_mix_interval_sec", 0.025)))
                hb = max(interval, float(getattr(self, "_heartbeat_interval", 0.10)))
                ticks_per_cycle = max(1, int(round(hb / interval)))
                # 每 tick 批量 = ceil(队列长 / 周期内tick数);至少 1。
                self._heartbeat_batch = max(1, -(-len(queue) // ticks_per_cycle)) if queue else 0
            if not self._heartbeat_queue:
                return []
            batch = self._heartbeat_batch if self._heartbeat_batch > 0 else len(self._heartbeat_queue)
            all_unis = set(self._heartbeat_queue[:batch])
            self._heartbeat_queue = self._heartbeat_queue[batch:]

        # 持锁期间 _layers 不会被并发改写，直接引用即可，避免每帧为全部 layer×uni
        # 重建 4043 项快照 dict（与旧路径逐字节等价，层数据只读）。
        snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]] = self._layers

        # 获取 OutputSmoother 实例（懒加载，失败时无平滑）
        smoother = self._get_output_smoother()

        # Phase 2：direct 模式 + 门控开启时，一次性批量混完所有 universe（削 GIL）。
        # 计算等价于逐 universe 的 _mix_one_universe(direct 路径)；后续 smoother/xy/融合/
        # 快照仍逐 universe 走原逻辑，保证输出与旧路径逐字节一致。
        batch_mixed: Optional[Dict[int, np.ndarray]] = None
        batch_clip_masks: Dict[int, np.ndarray] = {}
        if (
            self._batch_mix_enabled
            and self._shape_blend_mode == "direct"
            and all_unis
        ):
            try:
                batch_mixed, batch_clip_masks = self._mix_universes_batch(
                    snapshots, list(all_unis), return_clip_mask=True)
            except Exception:
                log.exception("批量混音失败，回退逐 universe")
                batch_mixed = None
                batch_clip_masks = {}

        packets: List[Tuple[int, np.ndarray]] = []
        # 批量不变量提到循环外：smoother 旁通、联锁 armed、增益融合函数每帧只取一次。
        smoother_bypass = True
        if smoother is not None:
            try:
                smoother_bypass = bool(getattr(smoother, "is_bypass", lambda: False)())
            except Exception:
                smoother_bypass = True
        ilock = None
        try:
            from ..engine.safety_interlock import get_interlock as _get_ilock
            ilock = _get_ilock()
        except Exception:
            ilock = None
        ilock_armed = bool(getattr(ilock, "is_armed", False)) if ilock is not None else False
        all_uni_list = [int(u) for u in all_unis]
        for uni in all_uni_list:
            # ★ A10 G17：单域混音异常不得拖垮全批——修前一个域抛异常直接
            #   冒出本方法，同拍其余全部域丢失（用户表现为"某一盏灯配错，
            #   整个输出停跳"）。逐域隔离：仅该域当拍丢弃，其余照发。
            # 脏集已在循环前 difference_update 清空（恢复语义保持）：丢弃的
            # 域不回填脏集，下一拍脏/心跳驱动照常，不放大重试风暴。
            try:
                if batch_mixed is not None:
                    mixed = batch_mixed.get(uni)
                    clip_win_mask = batch_clip_masks.get(uni)
                    if mixed is None:
                        mixed, clip_win_mask = self._mix_one_universe(
                            snapshots, uni, return_clip_mask=True)
                else:
                    mixed, clip_win_mask = self._mix_one_universe(
                        snapshots, uni, return_clip_mask=True)
                # ★ 用户输出平滑处理：仅在非旁通预设时生效。
                # 与 effect/shape 混音逻辑分离，旁通时严格输出原始混音结果。
                if not smoother_bypass:
                    mixed = self._smoother_process_safe(smoother, uni, mixed)
                # ★ SafetyInterlock：输出安全联锁（硬件保护，在 smoother 之后、
                #   _send_to_hw 之前。根据每设备约束钳位或停机。）
                if ilock_armed and ilock is not None:
                    mixed = self._interlock_process_safe(ilock, uni, mixed)
                virtual_out = mixed
                physical_out = self._apply_xy_reference(
                    uni, virtual_out, clip_win_mask=clip_win_mask)
                # ★ 性能：无 xy refs/联锁时 physical_out 即 virtual_out，
                #   快照只拷贝一次（读者全部只读，get_virtual_universe_data 返回 .copy()）；
                #   packets 保持独立 copy（_send_to_hw 隔离语义不变）。
                if physical_out is virtual_out:
                    self._universe_snapshot[uni] = physical_out.copy()
                    self._universe_virtual_snapshot[uni] = self._universe_snapshot[uni]
                else:
                    self._universe_virtual_snapshot[uni] = virtual_out.copy()
                    self._universe_snapshot[uni] = physical_out.copy()
                packets.append((uni, physical_out.copy()))
            except Exception:
                log.exception("单域混音失败，仅丢弃该域 (universe=%s)", uni)
                continue

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._record_perf_stats(len(all_unis), elapsed_ms, 0.0)
        if elapsed_ms >= self._perf_slow_mix_ms:
            now_log = time.monotonic()
            if now_log - self._perf_last_mix_log >= 2.0:
                self._perf_last_mix_log = now_log
                log.info(
                    "DMX mix/send slow frame: %.2f ms, universes=%s, dirty=%s",
                    elapsed_ms, len(all_unis), len(dirty_unis),
                )
        return packets

    def _smoother_process_safe(self, smoother, uni: int, mixed: np.ndarray) -> np.ndarray:
        """用户输出平滑单域处理（A10 G17 自逐域循环体迁出，行为逐字节一致）。

        平滑失败不静默也不中断混音：5s 节流 log.exception 后按未平滑值继续
        （time.monotonic 仅异常分支执行，正常路径零开销）。
        """
        try:
            return smoother.process(uni, mixed)
        except Exception:
            _now = time.monotonic()
            if _now - getattr(self, "_smoother_fail_log_t", 0.0) >= 5.0:
                self._smoother_fail_log_t = _now
                log.exception("OutputSmoother.process failed (universe=%s)", uni)
            return mixed

    def _interlock_process_safe(self, ilock, uni: int, mixed: np.ndarray) -> np.ndarray:
        """安全联锁单域处理（A10 G17 自逐域循环体迁出，行为逐字节一致）。

        联锁失败不静默也不中断混音（失败=钳位失效最严重）：5s 节流
        log.exception 后按未钳位值继续。
        """
        try:
            return ilock.process(uni, mixed)
        except Exception:
            _now = time.monotonic()
            if _now - getattr(self, "_ilock_fail_log_t", 0.0) >= 5.0:
                self._ilock_fail_log_t = _now
                log.exception("SafetyInterlock.process failed (universe=%s)", uni)
            return mixed

    def _record_perf_stats(self, universes: int, mix_ms: float, send_ms: float):
        if not getattr(self, "_perf_stats_enabled", False):
            return
        now = time.monotonic()
        self._perf_stats_frames += 1 if universes else 0
        self._perf_stats_universes += int(universes or 0)
        self._perf_stats_mix_ms += float(mix_ms or 0.0)
        self._perf_stats_send_ms += float(send_ms or 0.0)
        if now < getattr(self, "_perf_stats_next_log", 0.0):
            return
        frames = max(1, int(getattr(self, "_perf_stats_frames", 0) or 0))
        log.info(
            "DMX perf: frames=%s universes=%s avg_mix=%.2fms avg_send=%.2fms worker=%s",
            self._perf_stats_frames,
            self._perf_stats_universes,
            self._perf_stats_mix_ms / frames,
            self._perf_stats_send_ms / frames,
            bool(getattr(self, "_output_worker_enabled", False)),
        )
        self._perf_stats_next_log = now + float(getattr(self, "_perf_stats_interval", 2.0) or 2.0)
        self._perf_stats_frames = 0
        self._perf_stats_universes = 0
        self._perf_stats_mix_ms = 0.0
        self._perf_stats_send_ms = 0.0

    def _get_output_smoother(self):
        """懒加载 OutputSmoother 单例。"""
        if not hasattr(self, '_smoother_cache'):
            self._smoother_cache = None
            try:
                from ..engine.output_smoother import get_smoother
                self._smoother_cache = get_smoother()
            except Exception:
                pass
        return self._smoother_cache

    def _reset_output_smoother_states(self):
        """Drop cached smoothing state so idle/default frames are emitted exactly."""
        smoother = self._get_output_smoother()
        if smoother is not None and hasattr(smoother, "reset_states"):
            try:
                smoother.reset_states()
            except Exception:
                # R181 P2-3：平滑状态清不掉 → 空闲/默认帧可能带上帧平滑残留
                log.debug("复位输出平滑状态失败", exc_info=True)

    # ★ 回放冻结层：clip 层（播放/预览/拖针）存的是录制时的「当前输出值」
    #   （物理值）。输出路径对 clip 层活跃帧跳过 XY forward，防止已录物理值
    #   被二次旋转（2026-08-11 用户实测「同步模式录制回放=非同步轨迹」根因）。
    _CLIP_FREEZE_LAYERS = ("clip_playback", "clip_preview", "timeline")

    def _clip_freeze_active(self, universe: int) -> bool:
        """clip 层对 universe 是否存在活跃通道（回放冻结判定）。

        播放/预览/拖针期间 clip 层持续活跃；播放停止后层被清空 → 自动恢复
        编辑态 forward。录制快照路径（compute_universe_now /
        compute_universe_snapshots_now）显式传 clip_freeze=False：录制必须
        记录 forward 后的当前输出值，不受本冻结影响。
        """
        for name in self._CLIP_FREEZE_LAYERS:
            entry = (self._layers.get(name) or {}).get(int(universe))
            if entry is None:
                continue
            mask = np.asarray(entry[1], dtype=bool).reshape(-1)
            if mask.size and mask_count(mask):
                return True
        return False

    def _owned_channel_table(self, universe: int) -> Optional[tuple]:
        """该宇宙的「通道 → 设备」归属表（结构性缓存；R141 性能修复）。

        ★★ 修前实测：15000 台/10 宇宙 = 60.9 ms/帧、30000 台 = 104.5 ms/帧 ——
          全部耗在「每宇宙每帧遍历 proj.fixtures 全表 + 逐台线性扫 def 表」。
          布局纯取决于配接、与帧值无关 → 每宇宙只建一次，逐帧退化为向量化求交。
        失效纪律同 `_motor_pair_groups_for_universe`（签名 + `is` 复核，另有
        `set_fixture_defaults_from_project` 显式钩子）。
        """
        proj = self._project_ref
        if proj is None:
            return None
        all_fx = getattr(proj, "fixtures", None) or []
        all_defs = getattr(proj, "fixture_defs", None) or []
        sig = (id(proj), id(all_fx), len(all_fx), id(all_defs), len(all_defs))
        if (sig != getattr(self, "_owned_table_sig", None)
                or getattr(self, "_owned_table_raw", None) is not all_fx
                or getattr(self, "_owned_table_defs", None) is not all_defs):
            # 单遍建好全部宇宙的表（见 _build_owned_tables 的性能说明）。
            self._owned_table_cache = _build_owned_tables(all_fx, all_defs)
            self._owned_table_sig = sig
            self._owned_table_raw = all_fx
            self._owned_table_defs = all_defs
        return self._owned_table_cache.get(int(universe))

    def _clip_owned_fixture_ids(self, universe: int, clip_win_mask) -> set:
        """本帧被 clip 接管的**设备 id 集**（供 Z 组命令让位，R122）。

        ★★ 2026-09-20 用户实报（"一旦有一个 clip 是同步模式，后续的就算选默认都会
          一直强行同步"）：Z 组命令是**会话级**状态（不随工程保存、无人清理），
          而 clip 录下的才是"那条 clip 自己的状态"。做过同步的 clip 留下的命令会
          永久压掉后续 clip 的 Z。修法：clip 正在驱动某设备的 Z 时，该设备的 Z 由
          clip 值说了算 —— 本函数给出"被 clip 接管通道所覆盖的设备"。
        取不到配接/掩码时返回空集 = 旧行为（命令照常生效，零回归）。

        ★ R141：改查缓存归属表（`_owned_channel_table`），与旧逐设备扫描等价
          （含重叠配接），但每帧只做 2 次向量化查表。
        """
        # 掩码归一化：不足 512 补 False、超长截断 —— 与旧实现以 `mask.size` 作区间
        # 上界同义（越界通道不参与判定，DMX 只到 512）。
        try:
            mask = np.asarray(clip_win_mask, dtype=bool).reshape(-1)
        except Exception:
            return set()
        if mask.size != 512:
            fixed = np.zeros(512, dtype=bool)
            fixed[:min(mask.size, 512)] = mask[:512]
            mask = fixed
        if not mask_count(mask):
            return set()
        table = self._owned_channel_table(int(universe))
        if table is None:
            return set()
        owner, extra_idx, ids, extras = table
        owned = {ids[int(i)] for i in np.unique(owner[mask]).tolist() if int(i) >= 0}
        for i in np.unique(extra_idx[mask]).tolist():
            if int(i) >= 0:
                owned.update(ids[j] for j in extras[int(i)])
        owned.discard("")
        return owned

    def _maybe_suppress_lights(self, universe: int, data: np.ndarray,
                               enabled: bool = True) -> np.ndarray:
        """全局"灯输出禁用"应用点（R110）：灯通道置 0 出厂（不是写 0 值而是不输出）。

        · 会话级标志见 `engine.clip_output_filters.light_suppressed()`（不持久化）；
        · `enabled=False`（录制/计算路径）直接跳过 → 录制照常捕获原始值；
        · 关闭时零开销（只查一个模块级布尔）。
        """
        if not enabled:
            return data
        try:
            from ..engine.clip_output_filters import (
                apply_light_suppress, light_suppressed)
            if not light_suppressed(self._project_ref):
                return data
            return apply_light_suppress(self, int(universe), data, self._project_ref)
        except Exception:
            log.debug("light suppress failed (universe=%s)", universe, exc_info=True)
            return data

    def _ownership_active(self, universe: int, active: np.ndarray) -> np.ndarray:
        """归属查询用：灯禁用时把灯通道从 active 里扣掉（保持语义"不输出"）。

        否则监视器/调度台会把"被禁用的灯"显示成"有效果且值为 0"（写 0 值语义），
        与用户要求的"直接不输出"不符。
        """
        try:
            from ..engine.clip_output_filters import light_suppressed
            if not light_suppressed(self._project_ref):
                return active
            from ..engine.clip_output_filters import light_channel_mask
            mask = light_channel_mask(self, int(universe), self._project_ref)
            if mask.size != np.asarray(active).size:
                return active
            return mask_without(active, mask)
        except Exception:
            return active

    def _apply_xy_reference(
        self,
        universe: int,
        data: np.ndarray,
        *,
        clip_freeze: bool = True,
        clip_win_mask: Optional[np.ndarray] = None,
        light_suppress: bool = True,
    ) -> np.ndarray:
        # R110 全局灯输出禁用（输出末级；录制/计算路径传 light_suppress=False 豁免）。
        # 放在本函数入口：它是 mix / flush / release 全路径的必经点 → 一次设置全生效。
        data = self._maybe_suppress_lights(universe, data, light_suppress)
        try:
            if not (self._xy_reference_by_universe or {}).get(int(universe)):
                return data
            if clip_freeze and self._clip_freeze_active(int(universe)):
                if clip_win_mask is None:
                    # 回放冻结(非混音路径无掩码)：clip 层活跃 = 值已是录制
                    # 时的物理输出，整体跳过 forward。
                    return data
                # ★ 2026-08-13 EDGE-05: 回放冻结只保护 clip 接管通道 ——
                #   修复前整个 universe 跳过 forward, 同宇宙实时虚拟层
                #   (摇杆/效果/Programmer)以虚拟坐标直接输出(方向错误),
                #   且与录制快照(clip_freeze=False 仍 forward)不一致。
                #   修复: 先 forward 全缓冲(虚拟层正确旋转), 再回填 clip
                #   接管通道原值(已录物理值防二次旋转)。mask 全假时等价
                #   于纯 forward(clip 活跃但 LTP 全输给编辑器)。
                mask = np.asarray(clip_win_mask, dtype=bool).reshape(-1)
                # ★ R122：clip 接管的设备 → Z 组命令让位（否则旧 sync 命令永久压
                #   掉后续 clip 录下的 Z，"选默认也一直强行同步"）。
                _clip_owned = self._clip_owned_fixture_ids(int(universe), mask)
                # ★★ R131 修复：`clip_owned_z` 在被调方是**关键字专用**（签名里在 `*`
                #   之后）。原先按第 6 个**位置参数**传 → TypeError 被下面的
                #   `except Exception` 吞掉并 `return data`（虚拟值原样当物理值输出
                #   = 回放期同步设备方向全错），且每帧每宇宙刷一次 traceback。
                #   修前实测：90° 同步引用 + 活跃 clip 层，期望 (x=100,y=56)，
                #   实得 (x=200,y=100) 未变换。
                out = apply_xy_reference_to_physical(
                    data, self._xy_reference_by_universe, int(universe),
                    getattr(self, "_xy_reference_z_command_deg", None),
                    getattr(self, "_xy_reference_z_command_ids", None),
                    clip_owned_z=_clip_owned)
                if mask_count(mask):
                    out = np.asarray(out, dtype=np.uint8)
                    out[mask] = data[mask]
                return out
            return apply_xy_reference_to_physical(
                data, self._xy_reference_by_universe, int(universe),
                getattr(self, "_xy_reference_z_command_deg", None),
                getattr(self, "_xy_reference_z_command_ids", None))
        except Exception:
            log.exception("XY reference transform failed for universe %s", universe)
            return data

    def _apply_xy_reference_inverse(self, universe: int, data: np.ndarray) -> np.ndarray:
        try:
            if not (self._xy_reference_by_universe or {}).get(int(universe)):
                return data
            return apply_xy_reference_to_virtual(data, self._xy_reference_by_universe, int(universe))
        except Exception:
            log.exception("XY reference inverse transform failed for universe %s", universe)
            return data

    def _update_shape_anchor(
        self, layer: str, uni: int, d: np.ndarray, a: np.ndarray,
        update: bool = True,
    ) -> np.ndarray:
        """更新该形状层 / universe 的滑动 min/max，并返回当前 anchor (max+min)/2。

        update=False 时不修改 running stats，只读返回当前估计 anchor（供
        compute_universe_now 等同步路径使用，避免污染主混合循环的状态）。
        """
        df = d.astype(np.float32)
        rmin = self._shape_runmin[layer].get(uni)
        rmax = self._shape_runmax[layer].get(uni)
        if rmin is None or rmax is None:
            new_min = df.copy()
            new_max = df.copy()
        else:
            α = self._shape_anchor_ema
            decay_min = rmin * (1.0 - α) + df * α
            decay_max = rmax * (1.0 - α) + df * α
            new_min = np.minimum(decay_min, df)
            new_max = np.maximum(decay_max, df)

        if a is not None:
            mask = ~a
            new_min[mask] = 0.0
            new_max[mask] = 0.0

        if update:
            self._shape_runmin[layer][uni] = new_min
            self._shape_runmax[layer][uni] = new_max
        return (new_min + new_max) / 2.0

    def _mix_one_universe(
        self,
        snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]],
        uni: int,
        update_anchor: bool = True,
        exclude_ltp_editor_layers: Optional[set] = None,
        return_clip_mask: bool = False,
    ) -> np.ndarray:
        """对单一 universe 执行 base + shape 混合。

        return_clip_mask=True 时返回 (out, clip_win)：clip_win = 最终值来自
        clip 层接管的通道掩码（EDGE-05 回放冻结按通道拆分 forward 用）。
        """
        reuse = self._reuse_mix_scratch
        scratch = self._mix_scratch if reuse else None
        # 阶段 1：BASE LTP
        default_entry = self._channel_defaults.get(int(uni))
        if default_entry is not None:
            default_data, default_active = default_entry
            if reuse:
                base_val = scratch["base_val"]
                base_val[:] = default_data            # cast → float32
                base_active = scratch["base_active"]
                base_active[:] = default_active
                default_mask = scratch["default_mask"]
                default_mask[:] = default_active
            else:
                base_val = default_data.astype(np.float32)
                base_active = default_active.copy()
                default_mask = default_active.copy()
        else:
            if reuse:
                base_val = scratch["base_val"]
                base_val.fill(0.0)
                base_active = scratch["base_active"]
                base_active.fill(False)
                default_mask = scratch["default_mask"]
                default_mask.fill(False)
            else:
                base_val = np.zeros(512, dtype=np.float32)
                base_active = np.zeros(512, dtype=bool)
                default_mask = np.zeros(512, dtype=bool)
        if reuse:
            base_time = scratch["base_time"]
            base_time.fill(-1.0)
            base_layer_active = scratch["base_layer_active"]
            base_layer_active.fill(False)
        else:
            base_time = np.full(512, -1.0, dtype=np.float64)
            base_layer_active = np.zeros(512, dtype=bool)
        for name in BASE_LAYERS:
            entry = snapshots[name].get(uni)
            if entry is None:
                continue
            d, a = entry
            if not np.any(a):
                continue
            base_val[a] = _gain_vals(d[a], self._layer_gains.get(name))
            base_active[a] = True
            base_layer_active |= a
            layer_t = self._ltp_editor_layer_time.get(name, {}).get(uni)
            if layer_t is not None:
                base_time[a] = np.maximum(base_time[a], layer_t[a])
            else:
                base_time[a] = np.maximum(base_time[a], 0.0)

        if np.any(default_mask):
            non_default_active = base_layer_active.copy()
            for name in SHAPE_LAYERS + CLIP_LAYERS:
                entry = snapshots.get(name, {}).get(uni)
                if entry is None:
                    continue
                _data, active = entry
                if np.any(active):
                    non_default_active |= np.asarray(active, dtype=bool)
            # Defaults are idle values, not a live layer.  Once any authored
            # layer owns the channel, the default stays silent until release.
            default_suppressed = default_mask & non_default_active & ~base_layer_active
            if np.any(default_suppressed):
                base_active[default_suppressed] = False

        # ★ 提取 linker 控制的通道掩码（linker 只写入了目标通道的 DMX 地址）
        linker_entry = snapshots.get("linker", {}).get(uni)
        if reuse:
            linker_active_mask = scratch["linker_active_mask"]
            if linker_entry is not None:
                _, la = linker_entry
                linker_active_mask[:] = la
            else:
                linker_active_mask.fill(False)
        else:
            linker_active_mask = np.zeros(512, dtype=bool)
            if linker_entry is not None:
                _, la = linker_entry
                linker_active_mask = la.copy()

        # 阶段 2：SHAPE 层偏移
        if reuse:
            shape_offset = scratch["shape_offset"]
            shape_offset.fill(0.0)
            shape_raw = scratch["shape_raw"]
            shape_raw.fill(0.0)
            shape_active = scratch["shape_active"]
            shape_active.fill(False)
            n_layers_per_ch = scratch["n_layers_per_ch"]
            n_layers_per_ch.fill(0)
            direct_absolute = scratch["direct_absolute"]
            direct_absolute.fill(False)
            direct_abs_value = scratch["direct_abs_value"]
            direct_abs_value.fill(0.0)
            direct_abs_time = scratch["direct_abs_time"]
            direct_abs_time.fill(-1.0)
            audio_htp_active = scratch["audio_htp_active"]
            audio_htp_active.fill(False)
            audio_htp_value = scratch["audio_htp_value"]
            audio_htp_value.fill(0.0)
            audio_htp_time = scratch["audio_htp_time"]
            audio_htp_time.fill(-1.0)
        else:
            shape_offset = np.zeros(512, dtype=np.float32)
            shape_raw    = np.zeros(512, dtype=np.float32)
            shape_active = np.zeros(512, dtype=bool)
            n_layers_per_ch = np.zeros(512, dtype=np.int32)
            direct_absolute = np.zeros(512, dtype=bool)
            direct_abs_value = np.zeros(512, dtype=np.float32)
            direct_abs_time = np.full(512, -1.0, dtype=np.float64)
            audio_htp_active = np.zeros(512, dtype=bool)
            audio_htp_value = np.zeros(512, dtype=np.float32)
            audio_htp_time = np.full(512, -1.0, dtype=np.float64)

        for name in SHAPE_LAYERS:
            entry = snapshots[name].get(uni)
            if entry is None:
                continue
            d, a = entry
            if not np.any(a):
                continue
            shape_f = _gain_float(d, self._layer_gains.get(name))
            is_direct_absolute = (
                self._shape_blend_mode == "direct"
                and name in ABSOLUTE_DIRECT_SHAPE_LAYERS
            )
            if self._shape_blend_mode == "direct":
                anchor = self._direct_anchor_by_universe.get(int(uni), self._shape_default_anchor)
                if is_direct_absolute:
                    layer_t = self._ltp_editor_layer_time.get(name, {}).get(uni)
                    if name in AUDIO_HTP_SHAPE_LAYERS:
                        audio_htp_active |= a
                        higher_audio = a & (shape_f >= audio_htp_value)
                        audio_htp_value[higher_audio] = shape_f[higher_audio]
                        if layer_t is not None:
                            audio_htp_time[higher_audio] = np.maximum(audio_htp_time[higher_audio], layer_t[higher_audio])
                        else:
                            audio_htp_time[higher_audio] = np.maximum(audio_htp_time[higher_audio], 0.0)
                    else:
                        direct_absolute |= a
                        if layer_t is not None:
                            latest_mask = a & (layer_t >= direct_abs_time)
                            direct_abs_time[latest_mask] = layer_t[latest_mask]
                        else:
                            latest_mask = a & (0.0 >= direct_abs_time)
                            direct_abs_time[latest_mask] = 0.0
                        direct_abs_value[latest_mask] = shape_f[latest_mask]
            else:
                anchor = self._update_shape_anchor(name, uni, d, a, update=update_anchor)
            offset = (shape_f - anchor) * self._shape_gain
            # effect/matrix/audio 在 direct 模式下是绝对效果源，不应再把它们
            # 转成偏移量叠加一次；摇杆等相对层仍作为偏移叠加在最终源之上。
            if not is_direct_absolute:
                shape_offset[a] += offset[a]
            n_layers_per_ch[a] += 1
            shape_active |= a
            mask_higher = a & (shape_f > shape_raw)
            shape_raw[mask_higher] = shape_f[mask_higher]

        if np.any(audio_htp_active):
            direct_absolute |= audio_htp_active
            latest_mask = audio_htp_active & (audio_htp_time >= direct_abs_time)
            direct_abs_time[latest_mask] = audio_htp_time[latest_mask]
            direct_abs_value[latest_mask] = audio_htp_value[latest_mask]
            mask_higher = audio_htp_active & (audio_htp_value > shape_raw)
            shape_raw[mask_higher] = audio_htp_value[mask_higher]

        multi_mask = n_layers_per_ch > 1
        if np.any(multi_mask):
            shape_offset[multi_mask] = (
                shape_offset[multi_mask] / n_layers_per_ch[multi_mask].astype(np.float32)
            )

        # 阶段 3：合成
        if reuse:
            out = scratch["out"]
            out.fill(0.0)
        else:
            out = np.zeros(512, dtype=np.float32)
        only_base  = base_active & ~shape_active
        only_shape = shape_active & ~base_active
        both       = base_active & shape_active

        out[only_base]  = base_val[only_base]
        out[only_shape] = shape_raw[only_shape]
        only_direct_absolute = only_shape & direct_absolute
        if np.any(only_direct_absolute):
            out[only_direct_absolute] = (
                direct_abs_value[only_direct_absolute] + shape_offset[only_direct_absolute]
            )

        if np.any(both):
            both_linker    = both & linker_active_mask
            both_no_linker = both & ~linker_active_mask

            if np.any(both_no_linker):
                if self._shape_blend_mode == "soft":
                    # 软压制模式：base 越接近边界（0/255），shape 偏移权重越小，防止过冲截断
                    deviation = np.abs(base_val[both_no_linker] - 128.0) / 128.0
                    t = np.clip((deviation - 0.15) / (0.8 - 0.15), 0.0, 1.0)
                    weight = t * t * (3.0 - 2.0 * t)  # smoothstep
                    shape_factor = 1.0 - weight
                    blended = shape_offset[both_no_linker] * shape_factor * (1.0 - weight * 0.5)
                    out[both_no_linker] = base_val[both_no_linker] + blended
                else:
                    # 直出模式（默认）：shape 偏移直接叠加，不做权重压制
                    out[both_no_linker] = base_val[both_no_linker] + shape_offset[both_no_linker]

                    absolute_mask = both_no_linker & direct_absolute
                    if np.any(absolute_mask):
                        shape_newer = absolute_mask & (direct_abs_time >= base_time)
                        base_newer = absolute_mask & ~shape_newer
                        if np.any(shape_newer):
                            out[shape_newer] = (
                                direct_abs_value[shape_newer] + shape_offset[shape_newer]
                            )
                        if np.any(base_newer):
                            out[base_newer] = base_val[base_newer] + shape_offset[base_newer]

            if np.any(both_linker):
                # ★ v14 修复：linker 层是绝对覆盖（override），被控通道（如 pump）
                # 不应被 shape 层（effect/timeline 等）的偏移叠加污染。
                # 例如：effect 在 pump 通道生成了一个不同相位的正弦波偏移，
                # 叠加到 linker 计算的泵值上，导致输出始终偏高或"卡住"。
                # linker 作为最高优先级的 base 层，其结果直接使用，不再加 shape 偏移。
                out[both_linker] = base_val[both_linker]

        # ★ Clip 播放/预览层在最终阶段覆盖编辑器输出，但不清空编辑器层数据。
        # LTP 按通道接管：只覆盖 clip 接管时间 > 编辑器操作时间 的通道。
        # 编辑器发生新操作时，对应通道的 _ltp_editor_time 被刷新，立即高于 _ltp_clip_time，
        # 从而编辑器抢回该通道的控制权。
        editor_t = self._editor_ltp_time_for_mix(uni, exclude_ltp_editor_layers)
        clip_t   = self._ltp_clip_time.get(uni)    # float64[512] 或 None
        clip_blocked_by_linker = linker_active_mask if np.any(linker_active_mask) else None
        # ★ 2026-08-13 EDGE-05: clip 接管通道掩码 —— 回放冻结时仅这些通道
        #   免 forward(已录物理值), 其余通道(实时虚拟层)仍须 forward。
        clip_win = np.zeros(512, dtype=bool) if return_clip_mask else None
        for clip_layer in ("timeline", "clip_playback", "clip_preview"):
            clip_entry = snapshots.get(clip_layer, {}).get(uni)
            if clip_entry is None:
                continue
            clip_data, clip_active = clip_entry
            if not np.any(clip_active):
                continue
            if clip_blocked_by_linker is not None:
                clip_active = clip_active & ~clip_blocked_by_linker
                if not np.any(clip_active):
                    continue
            if editor_t is None or clip_t is None:
                # 还没有任何 LTP 记录：clip 直接覆盖（兴起时的默认行为）
                out[clip_active] = clip_data[clip_active].astype(np.float32)
                if clip_win is not None:
                    clip_win |= clip_active
            else:
                # 按通道比较：clip 接管时间 >= 编辑器时间 的通道，clip 才能覆盖
                ltp_mask = clip_active & (clip_t >= editor_t)
                if np.any(ltp_mask):
                    out[ltp_mask] = clip_data[ltp_mask].astype(np.float32)
                    if clip_win is not None:
                        clip_win |= ltp_mask

        np.clip(out, 0.0, 255.0, out=out)
        if return_clip_mask:
            return out.astype(np.uint8), clip_win
        return out.astype(np.uint8)

    def _mix_universes_batch(
        self,
        snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]],
        unis: List[int],
        return_clip_mask: bool = False,
    ) -> Dict[int, np.ndarray]:
        """跨 universe 批量混音（仅 direct 模式）。

        把 N 个 universe 的每层数据拼成 (N,512) 一次性向量化，逐字节等价于对每个
        universe 分别调用 _mix_one_universe（direct 模式路径）。numpy 调用次数从
        ~104×N 降到 ~104，大幅缩小 GIL ping-pong 面。

        ⚠️ 仅在 self._shape_blend_mode == "direct" 时可用：direct 模式不调
        _update_shape_anchor（无跨帧 EMA 状态），因此纯函数、可安全批量化。
        soft 模式请走逐 universe 路径。
        返回 {universe: uint8[512]}；return_clip_mask=True 时返回
        ({universe: uint8[512]}, {universe: bool[512]})（EDGE-05）。
        """
        N = len(unis)
        if N == 0:
            return ({}, {}) if return_clip_mask else {}
        uni_arr = [int(u) for u in unis]
        idx_of = {u: i for i, u in enumerate(uni_arr)}
        gain = self._shape_gain

        # ★ 批量 stack 层复用 thread-local 缓冲：CE 发送线程与主混音线程可并发，
        #   各自持有独立缓冲；同一线程内每次调用只 fill 不复分配。
        scratch = self._batch_scratch_local
        if getattr(scratch, "n", -1) != N:
            scratch.vals = np.zeros((N, 512), dtype=np.float32)
            scratch.acts = np.zeros((N, 512), dtype=bool)
            scratch.times = np.zeros((N, 512), dtype=np.float64)
            scratch.present = np.zeros((N, 1), dtype=bool)
            scratch.bufs = {
                "base_val": np.zeros((N, 512), dtype=np.float32),
                "base_active": np.zeros((N, 512), dtype=bool),
                "default_mask": np.zeros((N, 512), dtype=bool),
                "base_time": np.full((N, 512), -1.0, dtype=np.float64),
                "base_layer_active": np.zeros((N, 512), dtype=bool),
                "shape_offset": np.zeros((N, 512), dtype=np.float32),
                "shape_raw": np.zeros((N, 512), dtype=np.float32),
                "shape_active": np.zeros((N, 512), dtype=bool),
                "n_layers_per_ch": np.zeros((N, 512), dtype=np.int32),
                "direct_absolute": np.zeros((N, 512), dtype=bool),
                "direct_abs_value": np.zeros((N, 512), dtype=np.float32),
                "direct_abs_time": np.full((N, 512), -1.0, dtype=np.float64),
                "audio_htp_active": np.zeros((N, 512), dtype=bool),
                "audio_htp_value": np.zeros((N, 512), dtype=np.float32),
                "audio_htp_time": np.full((N, 512), -1.0, dtype=np.float64),
                "anchor_stack": np.empty((N, 512), dtype=np.float32),
                "editor_t": np.zeros((N, 512), dtype=np.float64),
                "editor_present": np.zeros((N, 1), dtype=bool),
                "clip_t": np.zeros((N, 512), dtype=np.float64),
                "clip_present": np.zeros((N, 1), dtype=bool),
                "out": np.zeros((N, 512), dtype=np.float32),
            }
            scratch.n = N
        _stack_vals = scratch.vals
        _stack_acts = scratch.acts
        _stack_times = scratch.times
        _stack_present = scratch.present
        bufs = scratch.bufs
        uni_items = list(idx_of.items())

        def stack_layer(name: str) -> Tuple[np.ndarray, np.ndarray]:
            """把某层的 (data, active) 拼成 (N,512)；增益按层决定是否乘（见 `_stack_pair`）。"""
            _stack_vals.fill(0.0)
            _stack_acts.fill(False)
            layer = snapshots.get(name, {})
            for u, i in uni_items:
                entry = layer.get(u)
                if entry is not None:
                    d, a = entry
                    np.copyto(_stack_vals[i], d, casting="unsafe")
                    _stack_acts[i] = a
            return _stack_pair(name, _stack_vals, _stack_acts, self._layer_gains)

        def stack_layer_time(name: str) -> Tuple[np.ndarray, np.ndarray]:
            """把 _ltp_editor_layer_time[name][uni] (float64[512]) 拼成 (N,512) + presence mask。
            缺失行 presence=False（对应标量路径里 layer_t is None → 用 0.0）。"""
            _stack_times.fill(0.0)
            _stack_present.fill(False)
            per_uni = self._ltp_editor_layer_time.get(name, {})
            for u, i in uni_items:
                lt = per_uni.get(u)
                if lt is not None:
                    _stack_times[i] = lt
                    _stack_present[i, 0] = True
            return _stack_times, _stack_present

        def stack_layer_active(name: str) -> np.ndarray:
            """只填 active 掩码，省去不需要 data 的调用（linker/默认抑制检查）。"""
            _stack_acts.fill(False)
            layer = snapshots.get(name, {})
            for u, i in uni_items:
                entry = layer.get(u)
                if entry is not None:
                    _stack_acts[i] = entry[1]
            return _stack_acts

        # ── 阶段 1：BASE LTP ──
        base_val = bufs["base_val"]; base_val.fill(0.0)
        base_active = bufs["base_active"]; base_active.fill(False)
        default_mask = bufs["default_mask"]; default_mask.fill(False)
        for u, i in idx_of.items():
            de = self._channel_defaults.get(u)
            if de is not None:
                dd, da = de
                base_val[i] = dd            # cast → float32
                base_active[i] = da
                default_mask[i] = da
        base_time = bufs["base_time"]; base_time.fill(-1.0)
        base_layer_active = bufs["base_layer_active"]; base_layer_active.fill(False)
        for name in BASE_LAYERS:
            d, a = stack_layer(name)
            if not a.any():
                continue
            base_val[a] = d[a]
            base_active |= a
            base_layer_active |= a
            lt, present = stack_layer_time(name)
            eff_t = lt          # 缺失层已由 stack_layer_time 填 0，无需再 np.where
            base_time[a] = np.maximum(base_time[a], eff_t[a])

        if default_mask.any():
            non_default_active = base_layer_active.copy()
            for name in SHAPE_LAYERS + CLIP_LAYERS:
                a = stack_layer_active(name)
                if a.any():
                    non_default_active |= a
            default_suppressed = default_mask & non_default_active & ~base_layer_active
            if default_suppressed.any():
                base_active[default_suppressed] = False

        # linker 掩码（拷贝：后续 stack_layer 会复用同一 acts 缓冲）
        linker_active_mask = stack_layer_active("linker").copy()

        # ── 阶段 2：SHAPE 层偏移（direct 模式） ──
        shape_offset = bufs["shape_offset"]; shape_offset.fill(0.0)
        shape_raw = bufs["shape_raw"]; shape_raw.fill(0.0)
        shape_active = bufs["shape_active"]; shape_active.fill(False)
        n_layers_per_ch = bufs["n_layers_per_ch"]; n_layers_per_ch.fill(0)
        direct_absolute = bufs["direct_absolute"]; direct_absolute.fill(False)
        direct_abs_value = bufs["direct_abs_value"]; direct_abs_value.fill(0.0)
        direct_abs_time = bufs["direct_abs_time"]; direct_abs_time.fill(-1.0)
        audio_htp_active = bufs["audio_htp_active"]; audio_htp_active.fill(False)
        audio_htp_value = bufs["audio_htp_value"]; audio_htp_value.fill(0.0)
        audio_htp_time = bufs["audio_htp_time"]; audio_htp_time.fill(-1.0)

        # direct anchor（逐 universe 可不同；缺失用 _shape_default_anchor）
        anchor_stack = bufs["anchor_stack"]
        for u, i in idx_of.items():
            anchor_stack[i] = self._direct_anchor_by_universe.get(u, self._shape_default_anchor)

        for name in SHAPE_LAYERS:
            d, a = stack_layer(name)
            if not a.any():
                continue
            shape_f = d                                  # already float32
            is_direct_absolute = name in ABSOLUTE_DIRECT_SHAPE_LAYERS
            if is_direct_absolute:
                lt, present = stack_layer_time(name)
                eff_t = lt
                if name in AUDIO_HTP_SHAPE_LAYERS:
                    audio_htp_active |= a
                    higher_audio = a & (shape_f >= audio_htp_value)
                    audio_htp_value[higher_audio] = shape_f[higher_audio]
                    audio_htp_time[higher_audio] = np.maximum(
                        audio_htp_time[higher_audio], eff_t[higher_audio])
                else:
                    direct_absolute |= a
                    latest_mask = a & (eff_t >= direct_abs_time)
                    direct_abs_time[latest_mask] = eff_t[latest_mask]
                    direct_abs_value[latest_mask] = shape_f[latest_mask]
            else:
                # 非 direct-absolute 层（joystick 等）作为相对偏移叠加
                offset = (shape_f - anchor_stack) * gain
                shape_offset[a] += offset[a]
            n_layers_per_ch[a] += 1
            shape_active |= a
            mask_higher = a & (shape_f > shape_raw)
            shape_raw[mask_higher] = shape_f[mask_higher]

        if audio_htp_active.any():
            direct_absolute |= audio_htp_active
            latest_mask = audio_htp_active & (audio_htp_time >= direct_abs_time)
            direct_abs_time[latest_mask] = audio_htp_time[latest_mask]
            direct_abs_value[latest_mask] = audio_htp_value[latest_mask]
            mask_higher = audio_htp_active & (audio_htp_value > shape_raw)
            shape_raw[mask_higher] = audio_htp_value[mask_higher]

        multi_mask = n_layers_per_ch > 1
        if multi_mask.any():
            # 只取 multi_mask 位置的分母；等价于标量路径只在 >1 时除，避免整阵 np.where
            shape_offset[multi_mask] /= n_layers_per_ch[multi_mask].astype(np.float32)

        # ── 阶段 3：合成 ──
        out = bufs["out"]; out.fill(0.0)
        only_base = base_active & ~shape_active
        only_shape = shape_active & ~base_active
        both = base_active & shape_active

        out[only_base] = base_val[only_base]
        out[only_shape] = shape_raw[only_shape]
        only_direct_absolute = only_shape & direct_absolute
        if only_direct_absolute.any():
            out[only_direct_absolute] = (
                direct_abs_value[only_direct_absolute] + shape_offset[only_direct_absolute]
            )

        if both.any():
            both_linker = both & linker_active_mask
            both_no_linker = both & ~linker_active_mask
            # direct 模式：shape 偏移直接叠加
            out[both_no_linker] = base_val[both_no_linker] + shape_offset[both_no_linker]
            absolute_mask = both_no_linker & direct_absolute
            if absolute_mask.any():
                shape_newer = absolute_mask & (direct_abs_time >= base_time)
                base_newer = absolute_mask & ~shape_newer
                out[shape_newer] = (
                    direct_abs_value[shape_newer] + shape_offset[shape_newer]
                )
                out[base_newer] = base_val[base_newer] + shape_offset[base_newer]
            if both_linker.any():
                out[both_linker] = base_val[both_linker]

        # ── clip 覆盖层（逐 universe 的 editor/clip 时间，拼成 (N,512)） ──
        editor_t = bufs["editor_t"]; editor_t.fill(0.0)
        editor_present = bufs["editor_present"]; editor_present.fill(False)
        clip_t = bufs["clip_t"]; clip_t.fill(0.0)
        clip_present = bufs["clip_present"]; clip_present.fill(False)
        for u, i in idx_of.items():
            et = self._editor_ltp_time_for_mix(u, None)
            if et is not None:
                editor_t[i] = et
                editor_present[i, 0] = True
            ct = self._ltp_clip_time.get(u)
            if ct is not None:
                clip_t[i] = ct
                clip_present[i, 0] = True
        # 该行是否两者都有 LTP 记录 → 按通道比较；否则 clip 直接覆盖
        both_ltp = editor_present & clip_present            # (N,1)

        # ★ 2026-08-13 EDGE-05: clip 接管通道掩码（同 _mix_one_universe）。
        clip_win = np.zeros((N, 512), dtype=bool) if return_clip_mask else None
        for clip_layer in ("timeline", "clip_playback", "clip_preview"):
            cd, ca = stack_layer(clip_layer)
            if not ca.any():
                continue
            clip_active = ca.copy()
            # linker 阻挡：仅对有 linker 的行做屏蔽（block 本身只在 linker 行非空）
            clip_active &= ~(clip_active & linker_active_mask)
            # 无 LTP 记录的行：clip 直接覆盖全部 active 通道
            cover_all = clip_active & ~both_ltp
            # 有 LTP 记录的行：仅 clip_t >= editor_t 的通道
            ltp_cover = clip_active & both_ltp & (clip_t >= editor_t)
            write_mask = cover_all | ltp_cover
            out[write_mask] = cd[write_mask]
            if clip_win is not None:
                clip_win |= write_mask

        np.clip(out, 0.0, 255.0, out=out)
        out_u8 = out.astype(np.uint8)
        if return_clip_mask:
            return ({u: out_u8[i] for u, i in idx_of.items()},
                    {u: clip_win[i] for u, i in idx_of.items()})
        return {u: out_u8[i] for u, i in idx_of.items()}

    # ── 输出到硬件 ──────────────────────────────────────────────────────────

    def _output_universe_allowlist(self) -> set[int]:
        """输出域白名单 = 显式配置域 ∪ 工程配接域（1-based）。

        默认配置只含域 1；为使"配接几个域就输出几个域、无需额外设置"，
        把工程中所有配接到的域（fixtures[].universe）并入白名单。
        显式配置的域始终被尊重；未配接也未配置的域仍被拦截（防御乱发）。

        性能：结果缓存于 _output_allowlist_cache，仅配接/配置变更时失效重算，
        热路径（_send_to_hw 每帧每域）只读缓存，零设备遍历。
        设 QASCADE_AUTO_OUTPUT_UNIVERSES=0 时只返回配置域（旧行为）。
        """
        cache = self._output_allowlist_cache
        if cache is not None:
            return set(cache)
        # 配置域始终保留（即使无配接/关闭自动扩展，也保持旧行为只发配置域）。
        # 自动扩展开启时把工程配接域合并进来，二者取并集——显式配置的域绝不被丢弃。
        allowed = {int(u) for u in (self._config.output_universes or [1]) if int(u) >= 1}
        if self._auto_output_universes:
            proj = self._project_ref
            if proj is not None:
                try:
                    for fi in (getattr(proj, "fixtures", []) or []):
                        try:
                            uni = int(getattr(fi, "universe", 1) or 1)
                        except Exception:
                            uni = 1
                        if uni >= 1:
                            allowed.add(uni)
                except Exception:
                    pass
        self._output_allowlist_cache = allowed
        return set(allowed)

    def _send_to_hw(self, universe_1based: int, data: np.ndarray):
        identity = read_source_identity(self, universe_1based, data)
        # ── DMX 缓冲区防御验证（guards 模块）────────────────────
        try:
            from ..utils.guards import validate_dmx_buffer as _vdb
            data = _vdb(data, universe=universe_1based)
        except ImportError:
            # C: 同 _send_batch_to_hw —— guards 缺失不再静默。热路径一次性告警。
            if not getattr(self, "_vdb_missing_logged", False):
                self._vdb_missing_logged = True
                log.warning(
                    "validate_dmx_buffer unavailable (guards import failed); "
                    "DMX buffer validation disabled")
        # ── 输出通道过滤：仅放行 _output_filter 中的通道，其余置 0 ──
        data = _apply_output_filter(self._output_filter_mask, data)
        # ★ R183 T5：Grand Master（总控）——末级乘性压暗，与批量路径同一函数。
        data = self.apply_grand_master(data)
        # 校验点 #5：只发送白名单中的 universe（显式配置域 ∪ 工程配接域）。
        # 避免向未配接/未配置的 universe 发送数据。
        allowed = self._output_universe_allowlist()
        if int(universe_1based) not in allowed:
            # 缓存可能过期（配接变更漏调失效钩子）：兜底重算一次再判定。
            # 重算结果写回缓存，正常热路径仍只读缓存、零设备遍历。
            self._output_allowlist_cache = None
            allowed = self._output_universe_allowlist()
            if int(universe_1based) not in allowed:
                log.debug("跳过白名单外 universe %s（白名单: %s）", universe_1based, sorted(allowed))
                return
        route_single_source(self, universe_1based, data, identity)

    def _sacn_sender_fast(self, universe_1based: int) -> Optional[SACNSender]:
        """sACN 发送器无锁快路径：池查找带锁兜底。

        ★ v3 发送链优化：router 线程每帧每域调用（73 次/tick）。旧实现
        每次走 _get_or_create_sacn_sender 的 with _state_lock（与混音线程
        抢锁）。运行期池只在 start/stop（router 已停）时变更 → 无锁 dict
        读安全（GIL 下单读无写），命中即返回；未命中才带锁创建。
        """
        s = self._sacn_tx_pool.get(universe_1based)
        if s is not None:
            return s
        return self._get_or_create_sacn_sender(universe_1based)

    # B4: Router-thread dispatch (skips _send_to_hw guard/re-entrancy)
    def _direct_send_dmx(self, universe_1based: int, data: np.ndarray) -> None:
        """Raw I/O dispatch called from the OutputRouter thread.

        This bypasses ``_send_to_hw`` validations/guard checks to avoid
        re-entrancy — all checks are already done at push time in the
        generator thread.
        """
        self._send_direct_frame(int(universe_1based), data)

    def send(self, universe: int, data: np.ndarray):
        self.update_layer("programmer", universe, data)

    def send_raw(self, universe: int, data: np.ndarray):
        physical = np.asarray(data, dtype=np.uint8).copy()
        with self._state_lock:
            if not self._running:
                return
            accept_single_source(self, universe, physical)
            self._universe_snapshot[universe] = physical.copy()
            self._universe_virtual_snapshot[universe] = self._apply_xy_reference_inverse(universe, physical)

    # ── 输入端 ──────────────────────────────────────────────────────────────

    def _on_raw_frame_artnet(self, universe: int, data: np.ndarray):
        self._dispatch_frame(_from_artnet_universe(universe), data)

    def _on_raw_frame_sacn(self, universe: int, data: np.ndarray):
        self._dispatch_frame(universe, data)

    def _dispatch_frame(self, universe: int, data: np.ndarray):
        if getattr(self, "_recording", False):
            return
        physical = np.asarray(data, dtype=np.uint8).copy()
        # ── 输入通道过滤：仅放行 _input_filter 中的通道，其余置 0 ──
        mask = self._input_filter_mask
        if mask is not None:
            physical = np.where(mask, physical, 0).astype(np.uint8)
        # 防御性短路:外部 DMX 源常以 ~40Hz 持续重发大量"值未变"的保活帧。若本 universe
        # 收到的物理数据与上一帧完全一致,直接跳过加锁写快照 + XY 逆变换 + 两次信号发射,
        # 避免入站洪流灌爆 UI 线程(即便将来开启输入监听)。
        with self._state_lock:
            prev = self._universe_snapshot.get(universe)
            if prev is not None and prev.shape == physical.shape and np.array_equal(prev, physical):
                return
            self._universe_snapshot[universe] = physical.copy()
            self._universe_virtual_snapshot[universe] = self._apply_xy_reference_inverse(universe, physical)
        try:
            self.frame_raw_in.emit(universe, physical)
        except Exception:
            pass
        try:
            ts = time.time() - self._record_start if self._record_start else 0.0
            frame = DMXFrame(universe=universe, timestamp=ts, data=physical.copy())
            self.frame_in.emit(frame)
        except Exception:
            pass

    def set_record_start(self, t0: float):
        with self._state_lock:
            self._record_start = float(t0)

    # ── 数据查询 ────────────────────────────────────────────────────────────

    def get_universe_data(self, universe: int) -> np.ndarray:
        with self._state_lock:
            data = self._universe_snapshot.get(universe)
            if data is not None:
                return data.copy()
        return np.zeros(512, dtype=np.uint8)

    def get_last_dmx(self, universe: int) -> np.ndarray:
        return self.get_universe_data(universe)

    def get_universe_ownership(self, universe: int) -> dict:
        """返回该 universe 的**通道归属**（哪个层在控制哪些通道）。

        ★ 为什么需要（2026-09-13 修，用户明确要求）：
          监视器原只读 `_universe_snapshot`（混合后的最终值），**不含归属信息**，
          因此无法区分两种语义完全不同的情况：
            · 用户人为把某通道设为 0  → 有效果（该层 `active=True`、值为 0）
            · 用户 release 掉该通道   → 无效果（该层 `active=False`，无归属）
          两者混合值都是 0，但前者是"有效果且暗"，后者是"已清空"。

        返回: {layer_name: np.ndarray(bool[512])}  仅含至少有 1 个 active 通道的层。
        用法（监视器）：`own = nm.get_universe_ownership(uni)`
          len(own) == 0            → 该域无任何归属（全部已清空）
          某通道 idx 在任一 own 里 → 有效果（即使值为 0）
        """
        out: dict = {}
        with self._state_lock:
            for layer_name, per_uni in self._layers.items():
                entry = per_uni.get(universe)
                if entry is None:
                    continue
                active = np.asarray(entry[1], dtype=bool)
                # R110：灯禁用时灯通道不算归属（监视器显示"已禁用"而非"值为 0"）
                active = self._ownership_active(universe, active)
                if bool(active.any()):
                    out[layer_name] = active.copy()
        return out

    def _light_off_mask(self, universe: int) -> np.ndarray:
        """灯禁用时的灯掩码（未禁用 → 空掩码，调用方逻辑零变化）。R110。"""
        try:
            from ..engine.clip_output_filters import light_channel_mask, light_suppressed
            if not light_suppressed(self._project_ref):
                return _EMPTY_512_BOOL
            return light_channel_mask(self, int(universe), self._project_ref)
        except Exception:
            return _EMPTY_512_BOOL

    def _light_off(self, universe: int, channel: int) -> bool:
        """单通道版：该通道是否为"被禁用的灯"。R110。"""
        mask = self._light_off_mask(universe)
        return bool(mask.size > channel and mask[channel])

    def get_channel_owners(self, universe: int, channel: int) -> list:
        """该通道当前被哪些层控制（按层名排序）。空列表 = 无归属（已清空）。"""
        owners = []
        with self._state_lock:
            for layer_name, per_uni in self._layers.items():
                entry = per_uni.get(universe)
                if entry is None:
                    continue
                if 0 <= channel < 512 and bool(entry[1][channel]):
                    owners.append(layer_name)
        if self._light_off(universe, channel):      # R110：灯禁用 = 无归属
            return []
        return sorted(owners)

    def get_channels_owners(self, universe: int, channels,
                            exclude_layers: Optional[Sequence[str]] = None,
                            ) -> Dict[int, Tuple[str, ...]]:
        """批量：若干通道**各自**被哪些层控制（全程**只加一次锁**）。

        ★ 为什么需要（2026-09-14 播放卡顿修复）：
          调度台一屏有上百通道，逐通道调 `get_channel_owners()` 会**每通道加一次
          `_state_lock`**；播放时 40Hz 混音器也在抢同一把锁 → 前端明显卡顿。
          本方法一次锁内算完整批，语义与 `get_channel_owners` 完全一致
          （每通道返回排序后的层名元组，无归属 = 空元组）。

        `exclude_layers`（只读筛选，默认 None = 不排除）：调度台监控区传
        `CLIP_LAYERS`，因为用户要求"只在**我真正在功能页面对该设备有编辑**时才出值，
        不要管 clip 播放引起的好像有值"。
        """
        out: Dict[int, Tuple[str, ...]] = {}
        valid = _valid_channels(channels)
        if not valid:
            return out
        acc: Dict[int, list] = {c: [] for c in valid}
        with self._state_lock:
            _accumulate_layer_owners(self._layers, universe, valid,
                                     set(exclude_layers or ()), acc)
        off = self._light_off_mask(universe)        # R110：灯禁用 = 无归属
        return {c: (() if (off.size > c and off[c]) else tuple(sorted(acc[c])))
                for c in valid}

    def get_channels_ownership(self, universe: int, channels,
                               exclude_layers: Optional[Sequence[str]] = None,
                               ) -> Dict[int, bool]:
        """批量判定若干通道**是否有归属**（任一层 `active=True`）。

        ★ 为什么需要（2026-09-13 调度台表格视图）：
          表格一屏可能有上千格，逐格调 `get_channel_owners()` 会**每格加一次锁**。
          本方法一次锁内算完整批，供表格刷新时"每域一次"取值（性能契约）。
          语义与 `get_universe_ownership` 完全一致：
            True  = 有效果（即使值为 0 —— "人为设 0"）
            False = 已清空（无任何层归属）

        `exclude_layers`（只读筛选，默认 None = 不排除）：调度台监控区传
        `CLIP_LAYERS` —— 用户要求"只在**我真正在功能页面对该设备有编辑**时才出值，
        不要管 clip 播放引起的好像有值"。
        """
        valid = _valid_channels(channels)
        result: Dict[int, bool] = {c: False for c in valid}
        if not valid:
            return result
        acc: Dict[int, list] = {c: [] for c in valid}
        with self._state_lock:
            _accumulate_layer_owners(self._layers, universe, valid,
                                     set(exclude_layers or ()), acc)
        off = self._light_off_mask(universe)        # R110：灯禁用 = 无归属
        for c in valid:
            result[c] = bool(acc[c]) and not (off.size > c and off[c])
        return result

    def get_layer_channel_values(self, layer: str, universe: int, channels) -> Dict[int, int]:
        """逐层读取若干通道的**原始值**（不混合其它层）。

        ★ 为什么需要：`get_universe_data()` 只给混合后的最终输出，**读不到单层**。
          调度台表格要按"该模块真实写入的那个层"取值（Programmer→fader 等），
          因此需要逐层原始值。缺层/缺域/未标记 active 的通道**不出现**在结果里
          （调用方据此区分"该层没写"与"写了 0"）。

        返回: {0-based 通道: 0-255 原始值}
        """
        result: Dict[int, int] = {}
        try:
            chs = [int(c) for c in (channels or ())]
        except (TypeError, ValueError):
            return result
        valid = [c for c in chs if 0 <= c < 512]
        if not valid:
            return result
        with self._state_lock:
            per_uni = self._layers.get(str(layer))
            entry = per_uni.get(universe) if per_uni else None
            if entry is None:
                return result
            data = np.asarray(entry[0])
            active = np.asarray(entry[1], dtype=bool)
            for c in valid:
                # ★ 只认 active 通道：`active=False` = 该层没在控制它（已清空/未写），
                #   此时残留的 data 值不应被读出去（否则"已释放"会被显示成有值）。
                if bool(active[c]):
                    result[c] = int(data[c])
        return result


    def get_virtual_universe_data(self, universe: int) -> np.ndarray:
        """Return the logical TopView XY-domain snapshot for internal calculations."""
        try:
            universe = int(universe)
        except Exception:
            universe = 1
        with self._state_lock:
            cached = self._universe_virtual_snapshot.get(universe)
            if cached is not None:
                return cached.copy()
            physical = self._universe_snapshot.get(universe)
            if physical is not None:
                return self._apply_xy_reference_inverse(universe, physical)
        return np.zeros(512, dtype=np.uint8)

    def get_last_virtual_dmx(self, universe: int) -> np.ndarray:
        return self.get_virtual_universe_data(universe)

    def compute_universe_now(self, universe: int, apply_output_smoothing: bool = False,
                             exclude_layers: Optional[List[str]] = None,
                             apply_xy_reference: bool = True,
                             exclude_ltp_editor_layers: Optional[set] = None) -> np.ndarray:
        """★ 立即基于当前各层数据计算并返回该 universe 的混合结果。

        录制路径必须用此方法，避免 _universe_snapshot 是 25ms 之前的过期值。
        exclude_layers 用于把 clip_playback / clip_preview / timeline 排除，确保录制只录编辑器数据。
        此方法不更新形状层滑动 anchor，避免污染主混合循环的状态。
        apply_output_smoothing=True 时才显式套用用户选择的输出平滑，
        从而把 effect/shape 自身混音与全局平滑开关彻底分开。
        """
        with self._state_lock:
            excluded = set(exclude_layers or [])
            snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {}
            for name in ALL_LAYERS:
                if name in excluded:
                    snapshots[name] = {}
                    continue
                entry = self._layers[name].get(universe)
                if entry is not None:
                    snapshots[name] = {universe: (entry[0].copy(), entry[1].copy())}
                else:
                    snapshots[name] = {}
            mixed = self._mix_one_universe(
                snapshots,
                universe,
                update_anchor=False,
                exclude_ltp_editor_layers=exclude_ltp_editor_layers or excluded,
            )
            if apply_output_smoothing:
                smoother = self._get_output_smoother()
                if smoother is not None and not getattr(smoother, "is_bypass", lambda: False)():
                    try:
                        mixed = smoother.process(universe, mixed)
                    except Exception:
                        pass
            if apply_xy_reference and not bool(getattr(self, "_skip_xy_reference_for_compute", False)):
                # ★ 录制/计算路径必须 forward（录「当前输出值」）：不走输出侧
                #   回放冻结（clip_freeze=False），否则 clip 层活跃时录制值
                #   会退化为虚拟值（2026-08-11 同步录制回放 bug 的录制侧保证）。
                if apply_output_smoothing:
                    # 平滑器有跨帧状态（同输入可产出不同输出）→ 禁止缓存，恒直算
                    mixed = self._apply_xy_reference(int(universe), mixed, clip_freeze=False, light_suppress=False)
                else:
                    # 纯函数缓存：输入帧字节 + 引用表版本 + Z 命令三者全同 → 输出
                    # 必然相同，直接复用（返回 .copy() 防调用方原地修改污染缓存）。
                    # 引用表由 set_xy_reference_map 版本化，Z 命令入键 → 语义严格
                    # 等价于每 tick 直算；录制轮询层数据静止时省掉逐引用变换。
                    uni_i = int(universe)
                    cache = self._xy_ref_compute_cache
                    key_b = mixed.tobytes()
                    zsig = (self._xy_reference_z_command_deg, self._xy_reference_z_command_ids)
                    hit = cache.get(uni_i)
                    if hit is not None and hit[0] == key_b and hit[1] == zsig \
                            and hit[2] == self._xy_ref_map_version:
                        mixed = hit[3].copy()
                    else:
                        mixed = self._apply_xy_reference(uni_i, mixed, clip_freeze=False, light_suppress=False)
                        # 存副本：miss 返回的 mixed 是给调用方的新对象，调用方原地
                        # 修改不得污染缓存（命中路径 hit[3].copy() 已有同等防护）。
                        cache[uni_i] = (key_b, zsig, self._xy_ref_map_version, mixed.copy())
            return mixed

    def compute_virtual_universe_now(
        self,
        universe: int,
        apply_output_smoothing: bool = False,
        exclude_layers: Optional[List[str]] = None,
        exclude_ltp_editor_layers: Optional[set] = None,
    ) -> np.ndarray:
        """Compute the current mixed output in TopView's virtual XY domain."""
        return self.compute_universe_now(
            universe,
            apply_output_smoothing=apply_output_smoothing,
            exclude_layers=exclude_layers,
            apply_xy_reference=False,
            exclude_ltp_editor_layers=exclude_ltp_editor_layers,
        )

    def compute_editor_universe_now(self, universe: int, apply_output_smoothing: bool = False,
                                    apply_xy_reference: bool = False) -> np.ndarray:
        """只计算编辑器/非 clip 层输出，供录制和连续动作过渡锚点使用。"""
        return self.compute_universe_now(
            universe,
            apply_output_smoothing=apply_output_smoothing,
            exclude_layers=["clip_playback", "clip_preview", "timeline"],
            apply_xy_reference=apply_xy_reference,
        )

    def compute_editor_active_mask(self, universe: int) -> np.ndarray:
        """Return union of active channels from editor layers, excluding clip layers."""
        try:
            universe = int(universe)
        except Exception:
            universe = 1
        mask = np.zeros(512, dtype=bool)
        with self._state_lock:
            for name in BASE_LAYERS + SHAPE_LAYERS:
                entry = self._layers.get(name, {}).get(universe)
                if entry is not None:
                    mask |= np.asarray(entry[1], dtype=bool)
        return mask

    def compute_active_snapshot_now(
        self,
        universes: Optional[set[int]] = None,
        exclude_layers: Optional[List[str]] = None,
        active_layers: Optional[List[str]] = None,
    ) -> Dict[int, np.ndarray]:
        """Batch active-mask snapshot for hot recording/linker paths."""
        excluded = set(exclude_layers or [])
        layer_names = list(active_layers) if active_layers is not None else [
            name for name in ALL_LAYERS if name not in excluded
        ]
        with self._state_lock:
            if universes is None:
                all_unis: set[int] = set()
                for name in layer_names:
                    if name in excluded:
                        continue
                    all_unis.update(int(u) for u in self._layers.get(name, {}).keys())
            else:
                all_unis = {int(u) for u in universes}

            result: Dict[int, np.ndarray] = {}
            for uni in all_unis:
                mask = np.zeros(512, dtype=bool)
                for name in layer_names:
                    if name in excluded:
                        continue
                    entry = self._layers.get(name, {}).get(int(uni))
                    if entry is None:
                        continue
                    try:
                        active = np.asarray(entry[1], dtype=bool).reshape(-1)
                    except Exception:
                        continue
                    if active.shape[0] == 512:
                        mask |= active
                    else:
                        fixed = np.zeros(512, dtype=bool)
                        n = min(int(active.shape[0]), 512)
                        if n > 0:
                            fixed[:n] = active[:n]
                        mask |= fixed
                if np.any(mask):
                    result[int(uni)] = mask
            return result

    def _snapshot_universe_has_mix_output(
        self,
        uni: int,
        snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]],
        excluded: set,
    ) -> bool:
        """快照 universe 是否可能产生非零混合输出（录制路径空层提前过滤）。

        _mix_one_universe 对「无默认值 + 全部层空 active + 无 clip 条目」的
        universe 恒输出零帧（base/shape 全空 → out 恒 0 → clip → uint8 零）。
        提前判空可跳过整段混合（scratch 填充 + 层循环 + 裁剪），空 universe
        单次成本 ~43µs → ~8µs；输出零帧与完整混合逐字节相同。有 XY 同步参考
        时零帧会被旋转为归位 128（同步语义），不可跳过。
        """
        de = self._channel_defaults.get(uni)
        if de is not None and bool(np.any(de[1])):
            return True
        if (self._xy_reference_by_universe or {}).get(uni):
            return True
        for name, layer_unis in snapshots.items():
            if name in excluded:
                continue
            entry = layer_unis.get(uni)
            if entry is not None and bool(np.any(entry[1])):
                return True
        return False

    def compute_universe_snapshots_now(
        self,
        universes: Optional[set[int]] = None,
        exclude_layers: Optional[List[str]] = None,
        apply_output_smoothing: bool = False,
        apply_xy_reference: bool = True,
        exclude_ltp_editor_layers: Optional[set] = None,
        active_layers: Optional[List[str]] = None,
    ) -> Tuple[Dict[int, np.ndarray], Dict[int, np.ndarray]]:
        """Batch current mixed snapshots and active masks.

        This preserves ``compute_universe_now`` semantics but avoids repeated
        lock acquisition, layer scanning, and snapshot construction when
        recording or the linker needs several universes in the same UI tick.
        """
        excluded = set(exclude_layers or [])
        with self._state_lock:
            if universes is None:
                all_unis: set[int] = set()
                for name in ALL_LAYERS:
                    if name in excluded:
                        continue
                    all_unis.update(int(u) for u in self._layers.get(name, {}).keys())
                all_unis.update(int(u) for u in self._channel_defaults.keys())
            else:
                all_unis = {int(u) for u in universes}

            snapshots: Dict[str, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {
                name: {} for name in ALL_LAYERS
            }
            for name in ALL_LAYERS:
                if name in excluded:
                    continue
                layer = self._layers.get(name, {})
                for uni in all_unis:
                    entry = layer.get(int(uni))
                    if entry is not None:
                        snapshots[name][int(uni)] = entry

            active_layer_names = ([name for name in ALL_LAYERS if name not in excluded]
                                  if active_layers is None else list(active_layers))
            smoother = self._get_output_smoother() if apply_output_smoothing else None
            mixed_by_universe: Dict[int, np.ndarray] = {}
            active_by_universe: Dict[int, np.ndarray] = {}
            for uni in sorted(all_unis):
                uni = int(uni)
                # 空层 universe（无默认值/无活跃通道/无 XY 参考）混合结果恒零帧，
                # 直接跳过混合与变换；输出零帧与完整计算逐字节相同。
                if not apply_output_smoothing and not self._snapshot_universe_has_mix_output(
                    uni, snapshots, excluded
                ):
                    mixed_by_universe[uni] = np.zeros(512, dtype=np.uint8)
                    continue
                mixed = self._mix_one_universe(
                    snapshots,
                    uni,
                    update_anchor=False,
                    exclude_ltp_editor_layers=exclude_ltp_editor_layers or excluded,
                )
                if smoother is not None and not getattr(smoother, "is_bypass", lambda: False)():
                    try:
                        mixed = smoother.process(uni, mixed)
                    except Exception:
                        pass
                if apply_xy_reference and not bool(getattr(self, "_skip_xy_reference_for_compute", False)):
                    # ★ 录制快照同 compute_universe_now：必须 forward（录当前
                    #   输出值），不走回放冻结。
                    cached = self._snapshot_xy_cache.get(uni)
                    if cached is not None and np.array_equal(cached[0], mixed):
                        mixed = cached[1].copy()
                        _xy_pre = cached[0]          # ★ R136：变换前值（掩码 diff 用）
                    else:
                        _xy_pre = np.asarray(mixed).copy()
                        xy_out = self._apply_xy_reference(uni, mixed, clip_freeze=False, light_suppress=False)
                        if xy_out is not mixed:
                            self._snapshot_xy_cache[uni] = (mixed.copy(), xy_out.copy())
                        mixed = xy_out
                mixed_by_universe[uni] = np.asarray(mixed, dtype=np.uint8)

                active = np.zeros(512, dtype=bool)
                for name in active_layer_names:
                    if name in excluded:
                        continue
                    entry = snapshots.get(name, {}).get(int(uni))
                    if entry is None:
                        continue
                    try:
                        mask = np.asarray(entry[1], dtype=bool).reshape(-1)
                    except Exception:
                        continue
                    if mask.shape[0] == 512:
                        active |= mask
                    else:
                        fixed = np.zeros(512, dtype=bool)
                        n = min(int(mask.shape[0]), 512)
                        if n > 0:
                            fixed[:n] = mask[:n]
                        active |= fixed
                _merge_xy_changed_into_mask(active, locals().get("_xy_pre"), mixed)
                if np.any(active): active_by_universe[int(uni)] = active
            return mixed_by_universe, active_by_universe

    # ── 属性 ────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def protocol_name(self) -> str:
        """实际发送协议名（R182 审计修：运行中读**实际 sender**，非 _config）。

        修前恒读 `_config.protocol`：运行中改协议未重启时，状态卡显示新协议
        而实际仍在用旧 sender 发包（实测"卡=sACN 运行中"而 Art-Net 在跑）。
        无发送器对象时（未运行/早期初始化）回退 _config。
        """
        with self._state_lock:
            actual = self._actual_protocol_locked()
            if actual:
                return actual
            return self._config.protocol.value if self._config else "未知"

    def _actual_protocol_locked(self) -> str:
        """按实际 sender 对象推断协议名（无对象/未运行 → ""）。"""
        try:
            if not getattr(self, "_running", False):
                return ""
            tx = getattr(self, "_artnet_tx", None)
            senders = [s for s in (getattr(self, "_sacn_tx_pool", {}) or {}).values()
                       if s is not None]
            return self._pick_protocol(tx, senders)
        except Exception:
            log.debug("实际 sender 协议推断失败，回退配置", exc_info=True)
            return ""

    @staticmethod
    def _pick_protocol(tx, senders) -> str:
        """Art-Net tx / sACN 池 → 协议名（两对象并存时以有线包者为准）。"""
        if tx is None and not senders:
            return ""
        if not senders:
            return "Art-Net"
        if tx is None:
            return "sACN"
        p_a = int(getattr(tx, "_packets", 0) or 0)
        p_s, _ = _sum_sender_counters(senders)
        return "Art-Net" if (p_a or not p_s) else "sACN"

    @property
    def last_bind_error(self) -> Optional[str]:
        """最近一次 start() 的接收端口绑定失败信息（None=正常）。

        R182 P1-5：UI 读此值显示红字「端口 6454/5568 被占用，请关闭其它
        DMX 软件」。修前 sACN 抛全局异常框、Art-Net 静默假运行。
        """
        return self._last_bind_error

    def get_sender_status(self) -> Dict[str, Any]:
        """实际发送器状态快照（R182 P1-4：UI 状态行不再只读 _config）。

        返回 ``{"protocol", "running", "artnet_target", "sacn_senders",
        "packets", "errors"}``——artnet_target 为 None 表示无 Art-Net 发送器，
        sacn_senders 为已创建的 sACN 发送器域数。全部走实际对象，不猜配置。
        """
        with self._state_lock:
            artnet = self._artnet_tx
            senders = [s for s in self._sacn_tx_pool.values() if s is not None]
            running = self._running
        packets, errors = _sum_sender_counters([artnet] + senders)
        return {
            "protocol": self.protocol_name,
            "running": running,
            "artnet_target": getattr(artnet, "target_ip", None),
            "sacn_senders": len(senders),
            "packets": packets,
            "errors": errors,
        }


    # ── LTP 辅助方法 ─────────────────────────────────────────────────────────

    def _editor_ltp_time_for_mix(
        self,
        universe: int,
        exclude_layers: Optional[set] = None,
    ) -> Optional[np.ndarray]:
        """Return editor LTP timestamps for mixing.

        Normal output uses the aggregate timestamp unchanged.  Internal snapshot
        readers can exclude their own layer so that the layer's previous writes
        do not affect the value they are trying to read.
        """
        excluded = set(exclude_layers or [])
        if not excluded:
            return self._ltp_editor_time.get(universe)

        combined: Optional[np.ndarray] = None
        for layer_name, per_uni in self._ltp_editor_layer_time.items():
            if layer_name in excluded:
                continue
            layer_t = per_uni.get(universe)
            if layer_t is None:
                continue
            if combined is None:
                combined = layer_t.copy()
            else:
                np.maximum(combined, layer_t, out=combined)
        return combined

    def _rebuild_editor_ltp_locked(self, universes: Optional[set[int]] = None):
        """Rebuild aggregate editor LTP timestamps from per-layer timestamps."""
        if universes is None:
            universes = set()
            for per_uni in self._ltp_editor_layer_time.values():
                universes.update(int(u) for u in per_uni.keys())
            universes.update(int(u) for u in self._ltp_editor_time.keys())
        for uni in list(universes or []):
            combined: Optional[np.ndarray] = None
            for per_uni in self._ltp_editor_layer_time.values():
                layer_t = per_uni.get(int(uni))
                if layer_t is None:
                    continue
                if combined is None:
                    combined = layer_t.copy()
                else:
                    np.maximum(combined, layer_t, out=combined)
            if combined is None or not np.any(combined > 0.0):
                self._ltp_editor_time.pop(int(uni), None)
            else:
                self._ltp_editor_time[int(uni)] = combined

    def _clear_layer_ltp_locked(
        self,
        layer_name: str,
        universes,
        active_by_universe: Optional[Mapping[int, np.ndarray]] = None,
    ):
        """Clear stale LTP timestamps for removed layer data."""
        affected = {int(u) for u in (universes or [])}
        if not affected:
            return

        if layer_name in CLIP_LAYERS:
            for uni in affected:
                t = self._ltp_clip_time.get(uni)
                if t is None:
                    continue
                remaining_active = np.zeros(512, dtype=bool)
                for other_layer in CLIP_LAYERS:
                    entry = self._layers.get(other_layer, {}).get(uni)
                    if entry is not None:
                        remaining_active |= np.asarray(entry[1], dtype=bool)
                raw_mask = (active_by_universe or {}).get(uni)
                if raw_mask is None:
                    t[~remaining_active] = 0.0
                    if not np.any(t > 0.0):
                        self._ltp_clip_time.pop(uni, None)
                    continue
                mask = np.asarray(raw_mask, dtype=bool).reshape(-1)
                if mask.shape[0] != 512:
                    fixed = np.zeros(512, dtype=bool)
                    n = min(mask.shape[0], 512)
                    if n > 0:
                        fixed[:n] = mask[:n]
                    mask = fixed
                t[mask & ~remaining_active] = 0.0
                if not np.any(t > 0.0):
                    self._ltp_clip_time.pop(uni, None)
            return

        if layer_name in self._ltp_editor_layer_time:
            per_uni = self._ltp_editor_layer_time[layer_name]
            for uni in affected:
                t = per_uni.get(uni)
                if t is None:
                    continue
                raw_mask = (active_by_universe or {}).get(uni)
                if raw_mask is None:
                    per_uni.pop(uni, None)
                    continue
                mask = np.asarray(raw_mask, dtype=bool).reshape(-1)
                if mask.shape[0] != 512:
                    fixed = np.zeros(512, dtype=bool)
                    n = min(mask.shape[0], 512)
                    if n > 0:
                        fixed[:n] = mask[:n]
                    mask = fixed
                t[mask] = 0.0
                if not np.any(t > 0.0):
                    per_uni.pop(uni, None)
            self._rebuild_editor_ltp_locked(affected)

    def _ltp_touch_editor(
        self,
        universe: int,
        active_mask: np.ndarray,
        layer_name: Optional[str] = None,
    ):
        """[LTP 内部] 编辑器层写入时，将对应通道的编辑器时间戳更新为当前时刻。

        该方法仅内部调用，由 update_layer / update_layer_sparse / merge_layer 在
        写入 BASE 或 SHAPE 层时自动触发。每次编辑器操作某通道，该通道的
        _ltp_editor_time 就会高于 _ltp_clip_time，混音时 clip 就不能再覆盖它。
        """
        now = time.monotonic()
        t = self._ltp_editor_time.get(universe)
        if t is None:
            t = np.zeros(512, dtype=np.float64)
            self._ltp_editor_time[universe] = t
        t[active_mask] = now
        if layer_name in self._ltp_editor_layer_time:
            layer_uni = self._ltp_editor_layer_time[layer_name]
            lt = layer_uni.get(universe)
            if lt is None:
                lt = np.zeros(512, dtype=np.float64)
                layer_uni[universe] = lt
            lt[active_mask] = now

    def notify_clip_takeover(
        self,
        universe: int,
        active_mask: np.ndarray,
    ):
        """[LTP 外部接口] 通知 NetworkManager clip 将接管指定通道。

        应在以下时机调用，每次只记一次时间戳：
          - 点击播放按键（play 开始）
          - 拖针落点（seek 完成）
        播放过程中的每一帧不应调用此方法，否则 clip 每帧刷新时间戳，
        编辑器将永远无法抢回通道。

        active_mask: bool[512]，指定本次 clip 接管的通道。
        """
        a = np.asarray(active_mask, dtype=bool)
        if a.shape != (512,):
            buf = np.zeros(512, dtype=bool)
            n = min(a.size, 512)
            buf[:n] = a.flatten()[:n]
            a = buf
        with self._state_lock:
            now = time.monotonic()
            t = self._ltp_clip_time.get(universe)
            if t is None:
                t = np.zeros(512, dtype=np.float64)
                self._ltp_clip_time[universe] = t
            a = self._expand_motor_pair_mask_for_universe(int(universe), a)
            t[a] = now
            if np.any(a):
                self._mark_dirty_universe(int(universe))

    def notify_clip_takeover_channels(
        self,
        universe: int,
        channel_indices: List[int],
    ):
        """[LTP 外部接口] 按索引列表通知 clip 接管。

        与 notify_clip_takeover 功能相同，但接受 0-indexed DMX 地址列表而非掩码。
        """
        if not channel_indices:
            return
        mask = np.zeros(512, dtype=bool)
        for idx in channel_indices:
            if 0 <= idx < 512:
                mask[int(idx)] = True
        with self._state_lock:
            now = time.monotonic()
            t = self._ltp_clip_time.get(universe)
            if t is None:
                t = np.zeros(512, dtype=np.float64)
                self._ltp_clip_time[universe] = t
            mask = self._expand_motor_pair_mask_for_universe(int(universe), mask)
            t[mask] = now
            if np.any(mask):
                self._mark_dirty_universe(int(universe))


def _close_sacn_pool(pool) -> None:
    """逐个 close sACN 发送器（stop/重建共用的确定性关闭纪律；R183 清查③）。"""
    for s in pool.values():
        try:
            s.close()
        except Exception:
            pass


def _locked_seq_of(sender) -> int:
    """持 _send_lock 读 sender._seq（router 线程可能并发发包推进序号）。"""
    try:
        with sender._send_lock:
            return int(getattr(sender, "_seq", 0))
    except Exception:
        return int(getattr(sender, "_seq", 0))


def _stack_pair(name, vals, acts, gains):
    """批量 stacking 的收尾：**按层决定是否乘增益**（clip 层永不淡化）。

    ★★ 深查修正（P0 两条路径给不同的光）：增益只对可淡化的编辑层（BASE+SHAPE）生效
      —— clip 层在标量路径从不乘增益，若此处无条件乘，就会出现"硬件（批量）被压暗、
      监控（标量）说满值"。clip 不属于编辑器，本就不该淡化
      （见 `cell_clear.CLEAR_LAYERS` 刻意排除 clip 层）。
    """
    if name in CLIP_LAYERS:
        return vals, acts
    return _apply_layer_gain(vals, gains.get(name)), acts


def _apply_layer_gain(vals, gain):
    """把层增益乘进 (N,512) 批量缓冲（`gain` 为 None / ≥1.0 时直通）。

    ★ 2026-09-14 深查修正 **A1**（本轮最严重）：批量混音路径取值只经 `stack_layer`
      一个入口（BASE/SHAPE 都走它）。原实现只在标量 `_mix_one_universe` 里乘增益 →
      默认 `QASCADE_BATCH_MIX=True` 时 `set_layer_gain` **完全不生效**，整个场景淡化是
      空操作（"设了淡化时间却毫无感觉"）。把分支放这里，也让批量函数本身不长行/不涨圈复杂度。
    """
    if gain is not None and gain < 1.0:
        np.multiply(vals, float(gain), out=vals)
    return vals


def _gain_vals(values, gain):
    """按层增益缩放 0-255 值（`gain` 为 None 或 ≥1.0 时零开销直通）。

    场景淡入淡出用：混音时缩放该层输出，故对"正在跑的效果"同样生效，
    与触发模式无关（用户 2026-09-14）。

    ★ 2026-09-14 深查修正 A2：判据必须是 `gain is None`。原写法 `if not gain` 把
      **增益 0.0 当成"没设增益"**直通 → 淡出到 0 的那一帧瞬间弹回满值（淡出永远失败）。
    """
    if gain is None or gain >= 1.0:
        return values.astype(np.float32)
    return values.astype(np.float32) * float(gain)


def _gain_float(data, gain):
    """按层增益缩放整帧 float32（同上；`gain is None`/≥1.0 直通；0.0 必须真的归零）。"""
    if gain is None or gain >= 1.0:
        return data.astype(np.float32)
    return data.astype(np.float32) * float(gain)
