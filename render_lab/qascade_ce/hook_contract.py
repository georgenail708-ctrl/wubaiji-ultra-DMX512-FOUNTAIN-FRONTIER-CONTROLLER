"""CE hook 契约注册表 —— **接线脆弱性的单一真源（防"静默脱靶"）**。

## 为什么需要它（2026-09-13 用户实录）

CE 的 50 个替换点靠**硬编码的 Python 属性名**做 monkeypatch（`module.Class.method = wrapper`）。
一旦 `src/` 里有人重命名/重构了被替换的符号，CE **不会报错**，只会静默回退到原版：

    · 功能看起来正常（回退路径刻意字节等价）
    · 门1 哈希恒 PASS（所以哈希门抓不到）
    · 但性能悄悄变差（R56 事故：渲染 11.19s → 61.32s，门1 依然 PASS）

用户反馈"CE 接连因为其他修改而出错、反反复复"，根因就是这个**静默性**。

## 本模块做什么

1. `HOOKS`：把每个 hook 的**目标符号 / 门控 / C 符号 / 计数器 / 用途**集中声明。
2. `snapshot_targets()` / `verify_installed()`：安装前后对比目标是否真被替换
   —— **改名即被检出**，而不是静默失效。
3. `derive_env_defaults()`：门控默认值的唯一来源（文档由它派生，防漂移）。

`tests/test_ce_contract.py` 会断言本表与 `src/` 实际符号一致（改名即测试失败）。
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("qascade_ce.contract")


@dataclass(frozen=True)
class HookSpec:
    """一个 CE 替换点的契约。

    target 用 `模块:类.方法` 或 `模块:函数` 表示（模块用 import 名，如
    `src.engine.playback_engine`）。resolution 失败 = 该 hook 已脱靶。
    """
    name: str                       # 'K0'
    label: str                      # 'K0 _mix_clips 帧合成'
    target: str                     # 'src.engine.playback_engine:PlaybackEngine._indexed_active_clip_items'
    gate_env: Optional[str] = None  # None = 常开（主开关）
    gate_default: str = "1"         # '1'=默认开 / '0'=默认关（与 _ENV_DEFAULT_MATRIX 同源）
    c_symbols: tuple[str, ...] = ()  # 依赖的 C 导出符号（缺失 → 恒回退）
    counters: tuple[str, ...] = ()   # 活性计数器名（>0 才算真执行）
    purpose: str = ""                # 一句话：加速什么
    optional: bool = False           # True = 允许缺失（如依赖外部 DLL 的 GPU 项）


# ── 契约表（与 install() 内的实际赋值一一对应）────────────────────────
# 维护规则：src/ 里改被替换符号名 → 必须同步这里；test_ce_contract 会拦住。
HOOKS: tuple[HookSpec, ...] = (
    HookSpec("K0", "K0 _mix_clips 帧合成内核",
             "src.engine.playback_engine:_get_render_kernels",
             c_symbols=("ce_mix_clips",), counters=("_K0_CNT",),
             purpose="逐帧 clip 场合成（帧合成内核句柄）"),
    HookSpec("P7", "indexed_holds 索引保持",
             "src.engine.playback_engine:PlaybackEngine._indexed_active_clip_items",
             purpose="hold 覆盖率增量计数（纯 Python，无 C 调用）"),
    HookSpec("P5a", "direction_fade 并行增益",
             "src.engine.playback_engine:PlaybackEngine._compute_parallel_fixture_gains",
             c_symbols=("ce_parallel_fixture_gains",),
             purpose="并行方向淡入淡出增益"),
    HookSpec("P5b", "direction_fade 顺序增益",
             "src.engine.playback_engine:PlaybackEngine._compute_sequential_fixture_gains",
             c_symbols=("ce_sequential_fixture_gains",),
             purpose="顺序方向淡入淡出增益"),
    HookSpec("T3", "T3 单域全链混合",
             "src.network.network_manager:NetworkManager._mix_one_universe",
             c_symbols=("ce_full_mix",), counters=("_T3_MIX_CNT",),
             purpose="单 universe 全层混合（direct 模式；soft 回退）"),
    HookSpec("T4", "T4 效果 compute_at",
             "src.ui.effect_engine:EffectEngine.compute_at",
             purpose="参数包络绑定求值"),
    HookSpec("T6b", "T6b compute 批量序列",
             "src.ui.effect_engine:EffectEngine.compute",
             c_symbols=("ce_compute_sequence",),
             purpose="批量序列求值"),
    HookSpec("T6", "precharge 泵预充电",
             "src.engine.pump_precharge:PrechargeScheduler.register",
             counters=("_PRECHARGE_CNT",),
             purpose="泵 VFD 预充状态机（数学在 C）"),
    HookSpec("T6tick", "precharge tick",
             "src.engine.pump_precharge:PrechargeScheduler.tick",
             purpose="预充 tick"),
    HookSpec("T5b", "flush_batch 批量 flush",
             "src.network.network_manager:NetworkManager.flush_universes_now",
             gate_env="QASCADE_CE_FLUSH_BATCH", gate_default="1",
             c_symbols=("ce_full_mix_many",), counters=("_T5B_FLUSH_CNT",),
             purpose="按域 OpenMP 批量混合"),
    HookSpec("T9", "heartbeat 空闲心跳",
             "src.network.network_manager:NetworkManager._mix_and_send_locked",
             counters=("_HEARTBEAT_CNT",),
             purpose="空闲心跳批处理"),
    HookSpec("H3-1", "xy_ref_build 参考表",
             "src.network.network_manager:build_xy_reference_map",
             c_symbols=("ce_xy_ref_build",), counters=("_XYREF_CNT",),
             purpose="XY 同步参考表构建"),
    HookSpec("T12", "T12 关联器整段计算",
             "src.processing.track_linker_engine:TrackLinkerEngine._process_rule",
             gate_env="QASCADE_CE_LINKER", gate_default="1",
             c_symbols=("ce_linker_rule", "ce_linker_preset_eval"),
             counters=("_LINKER_HIT_CNT", "_LINKER_MISS_CNT"),
             purpose="关联器 25ms tick 全规则遍历+逐通道读写 → C（实测占 82.1%）"),
    HookSpec("T13", "T13 矩阵像素引擎整段计算",
             "src.ui.matrix_slideshow.matrix_pixel_engine_ext_v40:_compute_pattern",
             gate_env="QASCADE_CE_MATRIX_PIXEL", gate_default="1",
             c_symbols=("ce_mtx_pattern", "ce_mtx_particle"),
             counters=("_MTX_HIT_CNT", "_MTX_MISS_CNT"),
             purpose="像素引擎 v40 图案/粒子族逐设备标量计算 → C"
                     "（1356 台实测 27.8ms/帧）"),
    HookSpec("T13p", "T13 矩阵像素引擎粒子族",
             "src.ui.matrix_slideshow.matrix_pixel_engine_ext_v40:_compute_particle",
             gate_env="QASCADE_CE_MATRIX_PIXEL", gate_default="1",
             c_symbols=("ce_mtx_particle",),
             counters=("_MTX_HIT_CNT", "_MTX_MISS_CNT"),
             purpose="像素引擎 v40 粒子族（逐设备×逐粒子 exp 核, 全引擎最重段）"),
    HookSpec("T14", "T14 effect _tick_play 写回层",
             "src.ui.effect_browser:EffectBrowserWidget._write_direct_channel_vectorized",
             gate_env="QASCADE_CE_EFFECT_WRITE", gate_default="1",
             c_symbols=("ce_eff_write_direct", "ce_eff_write_rgbw"),
             counters=("_EFF_WRITE_HIT_CNT", "_EFF_WRITE_MISS_CNT"),
             purpose="effect 脚手架逐域地址散写 + RGBW 亮度模拟 → C"
                     "（1356 台实测_总 _write_channel_vectorized 占整帧 31.3%）"),
    HookSpec("T14b", "T14 effect RGBW 亮度模拟",
             "src.ui.effect_browser:EffectBrowserWidget._write_rgbw_channel_vectorized",
             gate_env="QASCADE_CE_EFFECT_WRITE", gate_default="1",
             c_symbols=("ce_eff_write_rgbw",),
             counters=("_EFF_WRITE_HIT_CNT", "_EFF_WRITE_MISS_CNT"),
             purpose="dimmer/level 无物理 dimmer 时的 RGBW 亮度模拟"
                     "（聚合→默认值→缩放→max 写）"),
    HookSpec("H3-2", "smoother 输出平滑器",
             "src.engine.output_smoother:OutputSmoother.process",
             c_symbols=("ce_smoother_process",), counters=("_SMOOTHER_CNT",),
             purpose="输出平滑（状态在 C）"),
    HookSpec("H3-3", "interlock 安全联锁",
             "src.engine.safety_interlock:SafetyInterlock.process",
             c_symbols=("ce_interlock_process",), counters=("_INTERLOCK_CNT",),
             purpose="安全联锁限幅"),
    HookSpec("H3-4", "audio 音频特征",
             "src.ui.audio_driver:AudioAnalyzer._analyze_file_at_position",
             gate_env="QASCADE_CE_AUDIO_BATCH", gate_default="1",
             c_symbols=("ce_audio_frame_energy",), counters=("_AUDIO_BATCH_CNT",),
             purpose="音频帧能量/频带/平坦度"),
    HookSpec("H3-4msa", "audio_msa 结构分析",
             "src.audio.music_structure_analyzer:MusicStructureAnalyzer._on_frame",
             gate_env="QASCADE_CE_AUDIO_MSA", gate_default="1",
             purpose="音乐结构分析环形缓冲"),
    HookSpec("T10", "topview 同步/编组",
             "src.ui.topview_panel:TopViewPanel._compute_xy_reference_angles",
             gate_env="QASCADE_CE_T10", gate_default="1",
             counters=("_TOPVIEW_CNT",),
             purpose="同步角 / RANSAC / 编组"),
    HookSpec("K2", "timeline_range 离线整帧融合",
             "src.engine.playback_engine:PlaybackEngine._render_timeline_frame_range",
             gate_env="QASCADE_CE_TIMELINE_RANGE", gate_default="1",
             c_symbols=("ce_timeline_frame",),
             counters=("_TIMELINE_RANGE_CNT", "_TIMELINE_RANGE_FALLBACK_CNT"),
             purpose="离线整段帧 fetch+resolve+mix"),
    HookSpec("K5", "lt_light_tracking 灯跟踪",
             "src.engine.playback_engine:PlaybackEngine._apply_clip_light_tracking",
             gate_env="QASCADE_CE_LT", gate_default="1",
             c_symbols=("ce_lt_apply",),
             counters=("_LT_APPLY_CNT", "_LT_APPLY_FALLBACK_CNT"),
             purpose="灯跟踪束计算"),
    HookSpec("P9b", "send_pipeline 发送流水线",
             "src.network.network_manager:NetworkManager.flush_universes_now",
             gate_env="QASCADE_CE_SEND_RING", gate_default="1",
             purpose="常驻发送线程 + C 发送环"),
    HookSpec("PACK", "pack_batch 批量打包",
             "src.network.output_router:OutputRouter._run",
             gate_env="QASCADE_CE_PACK", gate_default="1",
             c_symbols=("ce_artnet_pack_batch",),
             purpose="ArtNet/sACN 批量协议打包"),
    HookSpec("T11", "topview_interact 交互几何",
             "src.ui.topview_panel:TopViewPanel._ordered_selected_ids",
             gate_env="QASCADE_CE_T11", gate_default="1",
             counters=("_T11_CNT",),
             purpose="画布交互几何批量"),
    # ★ R178：K3 分派点实际在 T6b compute 包装内（ce_compute → ce_bake_sequence_omp），
    #   原锚点 _render_position 只因 P4b 替换它才"显示已接入"；P4b（连同其内 P6/P8）
    #   按 10.3 退役后改锚真实分派点，不再借别人的替换冒充活性。
    HookSpec("K3", "离线烘焙批量",
             "src.ui.effect_engine:EffectEngine.compute",
             c_symbols=("ce_bake_sequence_omp",), counters=("_K3_BAKE_CNT",),
             purpose="离线整曲烘焙（T6b compute 包装内分派）"),
    HookSpec("K3gpu", "GPU 烘焙分派",
             "src.ui.effect_engine:EffectEngine.compute",
             gate_env="QASCADE_CE_GPU", gate_default="auto",
             purpose="离线烘焙 GPU 分派（可选后端；auto=有则用）", optional=True),
)

# 门控默认值文档/矩阵的唯一来源
_GATE_DEFAULTS: dict[str, str] = {}
for _h in HOOKS:
    if _h.gate_env:
        _GATE_DEFAULTS.setdefault(_h.gate_env, _h.gate_default)
# 非 hook 但属门控矩阵的项（未验证 → 恒关）。R178: RESOLVE_MANY 只在已退役的
# P4b 替代渲染路径内读取 → 与 P6/P8 同批移出（无读点 = 死配置）。
_GATE_DEFAULTS.update({
    "QASCADE_CE_FLUSH_ASYNC": "0",   # 真机未验证（历史: 与 RESOLVE_MANY 同开竞态）
    "QASCADE_CE_BAKE_THREADS": "16",
})


def derive_env_defaults() -> dict[str, str]:
    """门控默认值的**单一真源**（`_mount_ce._ENV_DEFAULT_MATRIX` 应与它一致）。"""
    return dict(_GATE_DEFAULTS)


# ── 目标解析与安装校验 ────────────────────────────────────────────────
def resolve_target(spec: str) -> tuple[bool, Any, str]:
    """把 '模块:类.方法' 解析成实际对象。

    返回 (ok, obj, 说明)。ok=False 时说明含失败原因 —— 这正是"改名脱靶"
    的检出点（旧行为：静默回退，无人知晓）。
    """
    try:
        mod_path, _, attr_path = spec.partition(":")
        if not mod_path or not attr_path:
            return False, None, f"契约格式错误: {spec!r}"
        mod = importlib.import_module(mod_path)
        obj: Any = mod
        for part in attr_path.split("."):
            if not hasattr(obj, part):
                return False, None, f"{mod_path} 缺少 {part!r}（可能已改名/删除）"
            obj = getattr(obj, part)
        return True, obj, ""
    except Exception as e:
        return False, None, f"导入 {spec!r} 失败: {type(e).__name__}: {e}"


def snapshot_targets() -> dict[str, Any]:
    """安装前快照所有目标对象（用于装后比对是否真被替换）。"""
    snap: dict[str, Any] = {}
    for h in HOOKS:
        ok, obj, _why = resolve_target(h.target)
        if ok:
            snap[h.target] = obj
    return snap


def verify_installed(before: dict[str, Any]) -> dict:
    """安装后校验：哪些目标真的被替换了。

    判定：同一定位路径取到的对象与快照**不是同一个对象** ⇒ 被 monkeypatch。

    返回 {"attached": [...], "not_attached": [...], "missing_target": [...]}
    """
    attached, not_attached, missing = [], [], []
    for h in HOOKS:
        if h.optional:
            continue
        ok, obj, why = resolve_target(h.target)
        if not ok:
            missing.append({"hook": h.name, "target": h.target, "why": why})
            continue
        old = before.get(h.target)
        if old is not None and obj is not old:
            attached.append(h.name)
        else:
            not_attached.append({"hook": h.name, "target": h.target})
    return {"attached": attached, "not_attached": not_attached, "missing_target": missing}


def contract_summary() -> str:
    """一行式契约概览（用于启动日志/boot_trace）。"""
    gates = sum(1 for h in HOOKS if h.gate_env)
    return (f"hook 契约 {len(HOOKS)} 项（含门控 {gates}、可选 "
            f"{sum(1 for h in HOOKS if h.optional)}）；门控默认 {len(_GATE_DEFAULTS)} 键")
