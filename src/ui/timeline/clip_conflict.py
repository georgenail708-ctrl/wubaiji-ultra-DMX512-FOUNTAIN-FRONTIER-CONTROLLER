"""
Qascade V1.0 — 多 Clip 冲突处理（分析 + 策略生成）

本模块只做「纯逻辑」：输入一批 clip（鸭子类型，读 start_time / duration /
playback_rate / clip_universes / active_channels_by_universe / fade_in / fade_out /
id），输出对各 clip 的过渡参数修改建议（edits）。

不引入 Qt，不依赖回放引擎，可被独立脚本喂模拟 clip 单测。

两种模式都「复用」回放引擎已有机制，不新造引擎：

1) 过渡模式 transition（前一个动作没结束、后一个动作放早了）：
   为后接管的 clip 设置 fade_in = 重叠区时长，使其在重叠区内从「上一个动作的
   输出位置」平滑插值接管（引擎 fade_in 分支已实现 prev + gain*(clip-prev)）。
   crossfade=True 时同时给前一个 clip 设置同长度 fade_out，形成对称交叉淡变。
   复用字段：Clip.fade_in / Clip.fade_out（引擎 _render 已读取）。

2) 合成模式 composite（就是要把多个 clip 放同一段时间一起输出）：
   引擎本身已能同时段输出重叠 clip——不同通道由 _mix_clips 叠加合成，相同通道
   按 LTP（change-score 最高者接管）。本模式不新造合成引擎，而是清掉会让某个
   clip 被淡出压暗的过渡淡入淡出（例如之前跑过过渡模式遗留的 fade_out），
   使这组 clip 在重叠区一起满输出，交给引擎现有合并能力混合。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

EPSILON = 1e-3


# ── clip 字段读取（鸭子类型，容错）──────────────────────────────────────────
def _f(clip: Any, name: str, default: float = 0.0) -> float:
    try:
        return float(getattr(clip, name, default) or default)
    except Exception:
        return default


def clip_start(clip: Any) -> float:
    return max(0.0, _f(clip, "start_time", 0.0))


def clip_duration(clip: Any) -> float:
    return max(0.0, _f(clip, "duration", 0.0))


def clip_end(clip: Any) -> float:
    return clip_start(clip) + clip_duration(clip)


def clip_id(clip: Any) -> str:
    cid = getattr(clip, "id", None)
    return str(cid) if cid is not None else str(id(clip))


def clip_channels(clip: Any) -> Tuple[List[int], Dict[int, set]]:
    """返回 (universes, explicit_channels_by_universe)。

    explicit_channels_by_universe[u] 为该 universe 下显式有效通道集合（1based）。
    若某 universe 不在该字典中，则视为「整宇宙通配」（active_channels 未记录，
    等同占用该宇宙全部通道）——与引擎在缺省时按整宇宙输出的行为一致。
    """
    universes: List[int] = []
    for u in (getattr(clip, "clip_universes", None) or [1]):
        try:
            universes.append(int(u))
        except Exception:
            continue
    if not universes:
        universes = [1]

    explicit: Dict[int, set] = {}
    acbu = getattr(clip, "active_channels_by_universe", None) or {}
    try:
        items = acbu.items()
    except Exception:
        items = []
    for u, chans in items:
        try:
            ui = int(u)
        except Exception:
            continue
        s = set()
        for c in (chans or []):
            try:
                s.add(int(c))
            except Exception:
                continue
        if s:
            explicit[ui] = s
    return universes, explicit


# ── 重叠判定 ────────────────────────────────────────────────────────────────
def time_overlap(a: Any, b: Any) -> float:
    """返回时间重叠时长（秒）。<=0 表示不重叠。"""
    return min(clip_end(a), clip_end(b)) - max(clip_start(a), clip_start(b))


def channels_overlap(a: Any, b: Any) -> bool:
    """两个 clip 是否占用同一宇宙下的共同通道（含整宇宙通配）。"""
    ua, ea = clip_channels(a)
    ub, eb = clip_channels(b)
    common = set(ua) & set(ub)
    if not common:
        return False
    for u in common:
        sa = ea.get(u)
        sb = eb.get(u)
        # 任一为整宇宙通配 → 视为占用相同通道
        if sa is None or sb is None:
            return True
        if sa & sb:
            return True
    return False


def conflicts_with(a: Any, b: Any, epsilon: float = EPSILON) -> bool:
    """同通道 + 时间重叠 = 构成冲突。"""
    return time_overlap(a, b) > epsilon and channels_overlap(a, b)


# ── 冲突分析 ────────────────────────────────────────────────────────────────
def analyze_conflicts(clips: List[Any], epsilon: float = EPSILON) -> List[Dict[str, Any]]:
    """两两扫描，返回所有「同通道 + 时间重叠」的冲突对。

    每项：{
      'earlier_id', 'later_id', 'overlap',
      'earlier_start', 'later_start', 'window'(可用过渡窗，clamp 到两者时长)
    }
    其中 earlier = start_time 较小者（同起点按列表先后稳定）。
    """
    items = list(clips)
    out: List[Dict[str, Any]] = []
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            ov = time_overlap(a, b)
            if ov <= epsilon or not channels_overlap(a, b):
                continue
            # earlier = 起点较早者；同起点保持列表顺序（i 在前）
            if clip_start(b) < clip_start(a):
                earlier, later = b, a
            else:
                earlier, later = a, b
            window = min(
                clip_end(earlier) - clip_start(later),
                clip_duration(later),
                clip_duration(earlier),
            )
            out.append({
                "earlier_id": clip_id(earlier),
                "later_id": clip_id(later),
                "overlap": float(ov),
                "earlier_start": clip_start(earlier),
                "later_start": clip_start(later),
                "window": float(max(0.0, window)),
            })
    return out


def involved_clip_ids(clips: List[Any], epsilon: float = EPSILON) -> set:
    """参与任意冲突的 clip id 集合。"""
    ids: set = set()
    for c in analyze_conflicts(clips, epsilon):
        ids.add(c["earlier_id"])
        ids.add(c["later_id"])
    return ids


# ── 过渡模式：生成淡入淡出建议 ──────────────────────────────────────────────
def plan_transition(clips: List[Any], crossfade: bool = True,
                    epsilon: float = EPSILON) -> Dict[str, Dict[str, float]]:
    """过渡模式：后接管 clip 渐入接管前者。

    返回 {clip_id: {'fade_in': x?, 'fade_out': y?}}（仅含被修改字段）。
    - 后接管 clip：fade_in = 重叠窗（取所有与之相关重叠对的最大窗）。fade_in 锚在
      clip 自身起点（引擎 local_t<fade_in 分支，见 playback_engine.py:3251），其覆盖
      区 [later_start, later_start+window] 恰是重叠区开头，两种相交情形都成立，故
      later 恒设 fade_in。
    - crossfade=True 时前一个 clip 是否设 fade_out 需区分相交方式，因为引擎 fade_out
      锚在 clip 自身末尾 [duration-fade_out, duration]（见 playback_engine.py:3257 /
      2688）：
        (a) later 末尾穿出 earlier 末尾（earlier_end <= later_end，正常接管）：重叠区
            落在 earlier 末尾 [earlier_end-window, earlier_end]，与 fade_out 锚点一致
            —— 给 earlier 设 fade_out 形成对称交叉淡出。
        (b) later 完全被包含在 earlier 内部（later_end < earlier_end）：重叠区在
            earlier 中段而非末尾，若给 earlier 末尾设 fade_out 会错误淡出非重叠的尾段
            —— 不给 earlier 设 fade_out，仅靠 later 的 fade_in 把被包含的后段平滑叠入。
    窗口已 clamp 到 clip 自身时长，调用方写回时仍会再次按 duration 钳制。
    """
    by_id = {clip_id(c): c for c in clips}
    edits: Dict[str, Dict[str, float]] = {}
    for conf in analyze_conflicts(clips, epsilon):
        window = conf["window"]
        if window <= epsilon:
            continue
        later_id = conf["later_id"]
        earlier_id = conf["earlier_id"]
        # later 恒淡入接管：fade_in 锚点(clip 起点)对齐重叠区开头，两种情形通用
        e_later = edits.setdefault(later_id, {})
        e_later["fade_in"] = max(e_later.get("fade_in", 0.0), window)
        if crossfade:
            earlier = by_id.get(earlier_id)
            later = by_id.get(later_id)
            # 仅当重叠区落在 earlier 末尾（情形 a）时，earlier 末尾锚定的 fade_out
            # 才正确；later 完全内含（情形 b）则跳过 earlier 的 fade_out。
            tail_overlap = (
                earlier is not None
                and later is not None
                and clip_end(later) >= clip_end(earlier) - epsilon
            )
            if tail_overlap:
                e_earlier = edits.setdefault(earlier_id, {})
                e_earlier["fade_out"] = max(e_earlier.get("fade_out", 0.0), window)
    # 钳制到 clip 时长
    for cid, e in edits.items():
        c = by_id.get(cid)
        if c is None:
            continue
        dur = clip_duration(c)
        if "fade_in" in e:
            e["fade_in"] = max(0.0, min(e["fade_in"], dur))
        if "fade_out" in e:
            e["fade_out"] = max(0.0, min(e["fade_out"], dur))
    return edits


# ── 合成模式：标记为同时段合成（清掉会压暗的过渡）──────────────────────────
def plan_composite(clips: List[Any], epsilon: float = EPSILON) -> Dict[str, Dict[str, float]]:
    """合成模式：让冲突组在重叠区一起输出。

    引擎已具备同时段合成能力（不同通道叠加 / 相同通道 LTP），本模式不新造引擎，
    只把参与冲突的 clip 的过渡淡入淡出清零，避免某个 clip 被（例如此前过渡模式
    遗留的）fade_out 压暗，使其满输出交给引擎现有合并逻辑混合。

    返回 {clip_id: {'fade_in':0.0,'fade_out':0.0}}。
    """
    edits: Dict[str, Dict[str, float]] = {}
    ids = involved_clip_ids(clips, epsilon)
    for c in clips:
        cid = clip_id(c)
        if cid in ids:
            edits[cid] = {"fade_in": 0.0, "fade_out": 0.0}
    return edits


def build_plan(clips: List[Any], mode: str, crossfade: bool = True,
               epsilon: float = EPSILON) -> Dict[str, Dict[str, float]]:
    """统一入口：mode in ('transition','composite')。未知模式返回空。"""
    m = str(mode or "").strip().lower()
    if m == "transition":
        return plan_transition(clips, crossfade=crossfade, epsilon=epsilon)
    if m == "composite":
        return plan_composite(clips, epsilon=epsilon)
    return {}
