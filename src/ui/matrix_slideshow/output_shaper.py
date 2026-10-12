"""
output_shaper.py — 矩阵输出全局平滑整形（attack/release 淡入淡出）
=================================================================

用户口径（R178）：**不是只针对泵类通道**，而是给 Matrix 输出整体加一层的
"全局平滑淡入淡出"参数 —— 目标权重突变时，实际输出（预览画布 + NM 写层）
按 attack 秒淡入 / release 秒淡出，曲线形状取自
``src/engine/output_smoother.py`` 的 ``EASE_FUNCS_V``（与全局输出平滑同一套
缓动函数：linear / ease_in / ease_out / ease_in_out / exponential）。

设计要点：
  * **纯逻辑核心，无 Qt 依赖** —— 面板预览与 NM 输出共用同一 ``OutputShaper``
    实例，保证"所见即所得"（预览画面 == 实际写出的值）。
  * **逐 fixture 状态机**：每个 fid 维护当前平滑电平；目标升高走 attack
    （从当前电平爬向新目标），目标降低走 release（从当前电平落向新目标，
    目标消失即落向 0）。重新锚定永远从**当前电平**出发 → 瞬时全开/全关
    都不会跳变。
  * **逐帧权重平滑，不往 NM 层写渐变帧**（R69 教训：动效每帧整帧重写层）——
    整形发生在面板 ``_on_engine_output`` 写层之前，只改"这一帧给多少权重"。
  * **关闭即透传**（``enabled=False`` 时逐字节行为不变，状态清零）。
  * **内存上限**：``_levels`` 只保留"在场（target 内）或衰减中"的 fid；
    不在场且已衰减到 0 的条目当帧删除。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ...engine.output_smoother import EASE_FUNCS_V

#: attack/release 秒数边界（与面板 QDoubleSpinBox 0–3s 一致）
SEC_MIN, SEC_MAX = 0.0, 3.0
#: 电平收敛判定（低于该值视为已到 0，可从状态表删除）
_LEVEL_EPS = 1e-4
#: dt 钳制上限（秒）——长时间暂停后的第一帧不允许一次走完全程
_DT_MAX = 1.0

#: 工程文件里的默认参数（enabled=False ⇒ 旧工程行为逐字节不变）
OUTPUT_SHAPE_DEFAULTS: Dict[str, object] = {
    "enabled": False,
    "attack": 0.3,     # 淡入秒数（0→255 的爬升时间）
    "release": 0.6,    # 淡出秒数（值→0 的衰减时间）
    "curve": "ease_in_out",
}

#: UI 曲线下拉选项：(EASE_FUNCS_V 键, 中文标签)
CURVE_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("linear", "线性"),
    ("ease_in", "缓入"),
    ("ease_out", "缓出"),
    ("ease_in_out", "缓入缓出"),
    ("exponential", "指数"),
)


def _clamp01(v: float) -> float:
    """钳到 [0,1]。"""
    f = float(v)
    if f != f:  # NaN
        return 0.0
    return 0.0 if f <= 0.0 else (1.0 if f >= 1.0 else f)


def _clamp_sec(v: object) -> float:
    """attack/release 秒数钳到 [0, SEC_MAX]（容错 NaN / 非数）。"""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return SEC_MIN if f <= SEC_MIN else (SEC_MAX if f >= SEC_MAX else f)


def _curve_fn(name: str):
    """按名字解析缓动函数；未知名字回退 linear。"""
    fn = EASE_FUNCS_V.get(str(name or "").strip().lower())
    return fn if fn is not None else EASE_FUNCS_V["linear"]


def curve_value(name: str, t: float) -> float:
    """curve(t)，t 先钳到 [0,1]，结果再钳到 [0,1]（scalar 化，摆脱 numpy 标量）。"""
    try:
        return _clamp01(float(_curve_fn(name)(_clamp01(t))))
    except Exception:
        return _clamp01(t)


@dataclass
class OutputShapeParams:
    """输出整形参数（面板弹层 ↔ 工程配置 dict 的载体）。"""
    enabled: bool = False
    attack: float = 0.3      # 淡入秒数（值 0→255 的爬升时间）
    release: float = 0.6     # 淡出秒数（值→0 的衰减时间）
    curve: str = "ease_in_out"

    def to_dict(self) -> Dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "attack": float(self.attack),
            "release": float(self.release),
            "curve": str(self.curve),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "OutputShapeParams":
        n = normalize_output_shape_dict(d)
        return cls(enabled=bool(n["enabled"]), attack=float(n["attack"]),
                   release=float(n["release"]), curve=str(n["curve"]))


def normalize_output_shape_dict(d: Optional[dict]) -> Dict[str, object]:
    """任意 dict → 补全默认 + 钳制后的合法 output_shape dict（保旧工程兼容）。"""
    out: Dict[str, object] = dict(OUTPUT_SHAPE_DEFAULTS)
    for k, v in (d or {}).items():
        if k in out:
            out[k] = v
    out["enabled"] = bool(out.get("enabled"))
    out["attack"] = _clamp_sec(out.get("attack"))
    out["release"] = _clamp_sec(out.get("release"))
    cur = str(out.get("curve") or "").strip().lower()
    out["curve"] = cur if cur in EASE_FUNCS_V else "linear"
    return out


def params_from_dict(d: Optional[dict]) -> OutputShapeParams:
    """工程配置 dict → 参数对象（每帧构建开销可忽略；关闭时只看 enabled）。"""
    return OutputShapeParams.from_dict(d)


class _FidLevel:
    """单个 fixture 的平滑相位状态。

    ``frm→to`` 是本段爬升/衰减的锚点区间，``elapsed`` 是已走秒数；
    电平 = frm + (to-frm) * curve(elapsed/dur)。目标变化即从**当前电平**
    重新锚定 → 永不跳变。
    """
    __slots__ = ("level", "frm", "to", "elapsed", "rising")

    def __init__(self, level: float = 0.0):
        self.level = float(level)
        self.frm = self.level
        self.to = self.level
        self.elapsed = 0.0
        self.rising: Optional[bool] = None   # None = 已到位（静止）


class OutputShaper:
    """逐 fixture 的输出值平滑状态机（帧驱动；预览与 NM 输出共用同一实例=同一视觉）。

    用法（面板每帧）::

        shaper = OutputShaper()
        shaped = shaper.feed(target_weights, dt_seconds, params)
    """

    def __init__(self):
        self._levels: Dict[str, _FidLevel] = {}   # fid → 当前平滑后 0..1

    # ── 对外接口 ──────────────────────────────────────────────────────────

    def feed(self, target: Dict[str, float], dt: float,
             p: Optional[OutputShapeParams]) -> Dict[str, float]:
        """目标权重 → 平滑后权重。attack: 向目标爬升；release: 向 0 衰减。

        关闭（enabled=False / p 为空）时原样透传并清空内部状态
        （逐字节行为不变）；不在最新 target 里的 fid 按 release 衰减到 0
        后从 ``_levels`` 删除（内存不随历史 fixture 集增长）。
        """
        if p is None or not p.enabled:
            self.reset()
            return dict(target or {})
        return self._feed_smoothed(target, dt, p)

    def reset(self) -> None:
        """清空全部平滑状态（切模式 / 关闭整形 / 工程切换时调用）。"""
        self._levels.clear()

    @property
    def active_fids(self) -> Tuple[str, ...]:
        """当前持有平滑状态的 fid（测试/诊断用）。"""
        return tuple(self._levels.keys())

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _feed_smoothed(self, target: Dict[str, float], dt: float,
                       p: OutputShapeParams) -> Dict[str, float]:
        step = min(max(float(dt or 0.0), 0.0), _DT_MAX)
        tgt = {str(k): _clamp01(v) for k, v in (target or {}).items()}
        out: Dict[str, float] = {}
        # 并集遍历：在场 fid 向目标推进；消失 fid 向 0 衰减；新 fid 从 0 爬升。
        for fid in set(tgt) | set(self._levels):
            st = self._levels.get(fid)
            if st is None:
                st = _FidLevel(0.0)
                self._levels[fid] = st
            self._advance(st, tgt.get(fid, 0.0), step, p)
            if st.level <= _LEVEL_EPS and fid not in tgt:
                del self._levels[fid]    # 清场完成 → 内存回收（输出也摘掉此键）
                continue
            out[fid] = st.level
        return out

    def _advance(self, st: _FidLevel, tv: float, dt: float,
                 p: OutputShapeParams) -> None:
        """把一个 fid 的电平向目标 tv 推进 dt 秒（锚点模型，见 _FidLevel）。

        ★ R181 P2-1b（逐帧爬升目标"半死不活"）：同向目标更新且**本段未走满**
        时**保进度续追**（只更新 to、不重置 elapsed/frm）——修前每帧重锚定
        elapsed=0 → 只走 curve(dt/dur) 的当帧微小比例 → 爬升目标滞后
        （实测 max_err 0.68@60fps）。恒定目标/方向切换/到位后重目标路径
        与修前逐位相同（既有 28 条回归 + 全量 31 项全绿）。
        """
        diff = tv - st.level
        if abs(diff) <= _LEVEL_EPS:      # 已到位 → 静止，等待下一次目标变化
            st.level = tv
            st.rising = None
            return
        rising = diff > 0.0
        dur = max(float(p.attack if rising else p.release), 0.0)
        if st.rising is None or st.rising != rising:
            st.rising = rising           # 方向切换 → 重新锚定（永远从当前电平出发）
            st.frm = st.level
            st.to = tv
            st.elapsed = 0.0
        elif abs(st.to - tv) > _LEVEL_EPS:
            if st.elapsed >= dur:        # 已到位（走满）→ 从当前位置重锚（防跳变）
                st.frm = st.level
                st.elapsed = 0.0
            st.to = tv                   # 运动中 → 只换目标，保留进度（续追）
        st.elapsed += dt
        if dur <= 0.0:
            st.level = tv                # 0 秒 = 即时（不做平滑）
        else:
            k = curve_value(p.curve, st.elapsed / dur)
            st.level = st.frm + (st.to - st.frm) * k
        if st.elapsed >= dur:
            st.level = st.to             # 浮点收尾：时间走满即精确到位
