# -*- coding: utf-8 -*-
"""矩阵面板的"清台 / 运行态还原"辅助（模块级函数，不往面板类加方法）。

为什么单独成文件（CLAUDE.md §10.1/§10.2 + 架构门禁）：
  · `MatrixSlideshowPanel` 是 6k+ 行的巨型类，任何新逻辑都只加**模块级函数**；
  · `matrix_slideshow_panel.py` 自身早已超 500 行且只许减，故这批新逻辑放这里，
    面板侧只留一行 import 与调用点。

两条真缺陷对应这里的实现：
  ③ **纯播放 recall 不还原运行参数**：`state['mode']`（ppt/pixel）与
     `pixel_params.mode`（像素效果 id）/threshold/波形造型等是**运行参数**、面板控件
     上看不见，而 R123/R130 把 `load_state` 限在 `for_edit=True` → 纯播放路径既不还原
     也不应用 → 点像素场景"没反应 / 出来的不是存的那个效果"（见
     `_apply_pixel_runtime_params` / `_quiesce_other_transport`）。
  ④ **帧序列与像素参数不随工程走**：`matrix_slideshow_state` 为空时 `load_state`
     早退 → 打开工程 A 做几帧，再打开/新建工程 B，Matrix 页里仍是 A 的帧序列，
     在 B 里一保存就写进 B（见 `_clear_matrix_stage` / `_maybe_clear_stage_on_project_switch`）。
"""
from __future__ import annotations

import copy
import logging

from ...utils.wing_block import normalize_wing_direction
from .matrix_pixel_music_catalog import MUSIC_DEFAULTS
from . import matrix_wing_flags
from .matrix_wing_flags import restore_direction as _restore_wing_direction

logger = logging.getLogger(__name__)


def _pm_project(panel):
    """面板当前绑定的工程对象（坏引用安全）。"""
    pm = getattr(panel, "_pm", None)
    if pm is None:
        return None
    try:
        return getattr(pm, "project", None) or getattr(pm, "_project", None)
    except Exception:
        return None


def _project_token(project) -> tuple:
    """工程身份指纹（换工程 = 换 `created_at` / `name`）。

    ★ 为什么不看对象 id：undo/redo 的域合并会**重建** Project 对象
      （`ProjectManager._merged_project`），对象 id 每次撤销都变 → 会把"同一工程"
      误判成换工程、把用户内存里还没保存的帧序列清掉。
      `created_at` 随工程序列化往返（`to_dict` / `_from_dict` 都带），撤销重建时
      保持不变；只有真正新建/打开另一个工程才会变 —— 用 `name` 兜同秒新建的极端情况。
    """
    if project is None:
        return ()
    try:
        return (str(getattr(project, "created_at", "") or ""),
                str(getattr(project, "name", "") or ""))
    except Exception:
        return ()


#: 清台时要复位的像素引擎参数（与 `save_state` 写出的 `pixel_params` 同口径）。
_PIXEL_RESET_KEYS = tuple(MUSIC_DEFAULTS) + (
    "threshold", "speed", "invert", "pixel_audio_reactive", "audio_controls_version",
    "matrix_wave_density", "matrix_wave_width", "matrix_wave_twist",
    "matrix_symmetry_mix", "matrix_effect_color", "wing_direction", "wing_mirror", "wing_reverse",
    "screen_region", "screen_brightness_boost", "screen_contrast",
    "screen_channel", "screen_smooth", "screen_gamma",
    "screen_threshold_low", "screen_threshold_high", "screen_fps",
    "video_path", "audio_amplitude", "audio_norm_offset", "audio_device",
    "audio_bar_direction", "audio_bar_style", "audio_band_layout",
    "audio_bar_gamma", "audio_bar_softness", "audio_bar_smoothing",
    "audio_bar_motion", "audio_peak_hold", "audio_peak_decay",
    "matrix_audio_mod_target", "matrix_audio_mod_source",
    "matrix_audio_mod_amount", "matrix_audio_mod_floor",
    "matrix_audio_mod_attack_ms", "matrix_audio_mod_release_ms",
    # ★ R183：视频扩展（摄像头/走带/变形/掩码）也随清台复位
    "video_source", "camera_device", "video_in_point", "video_out_point",
    "video_direction", "video_rate", "video_warp_corners",
    "sample_mask", "sample_mask_path",
)

#: 纯播放语义下要灌回引擎的**运行参数**（面板控件上不可见的那部分）。
_PIXEL_RUNTIME_KEYS = (
    "threshold", "speed", "invert", "pixel_audio_reactive", "audio_controls_version",
    "matrix_wave_density", "matrix_wave_width", "matrix_wave_twist",
    "matrix_symmetry_mix", "matrix_effect_color",
)


def _try_setattr(obj, key, val) -> bool:
    """安全赋值（只读/坏属性不抛，返回是否成功）。"""
    try:
        setattr(obj, key, val)
        return True
    except Exception:
        logger.debug("matrix: set %s failed", key, exc_info=True)
        return False


def _snapshot_pixel_params(engine) -> dict:
    """像素引擎"出厂参数"快照（清台时据此复位，避免硬编码默认值漂移）。"""
    out: dict = {}
    if engine is None:
        return out
    for key in _PIXEL_RESET_KEYS:
        if hasattr(engine, key):
            try:
                out[key] = copy.deepcopy(getattr(engine, key))
            except Exception:
                out[key] = getattr(engine, key, None)
    out["_mode"] = str(getattr(engine, "_mode", "") or "")
    return out


def _init_stage_identity(panel) -> None:
    """构造期登记"清台基线"：像素引擎默认参数快照 + 当前工程身份指纹。"""
    panel._pixel_engine_defaults = _snapshot_pixel_params(
        getattr(panel, "_pixel_engine", None))
    panel._stage_project_token = _project_token(_pm_project(panel))


def _restore_video_default(px) -> None:
    """复位时清掉上一条视频（出厂默认视频路径为空）。"""
    try:
        if not str(getattr(px, "video_path", "") or "") and hasattr(px, "clear_video"):
            px.clear_video()
    except Exception:
        logger.debug("matrix: clear video on reset failed", exc_info=True)


def _reset_pixel_engine_defaults(panel) -> None:
    """把像素引擎参数复位到面板构造时的默认值（工程切换/清台用）。"""
    px = getattr(panel, "_pixel_engine", None)
    defaults = getattr(panel, "_pixel_engine_defaults", None)
    if px is None or not isinstance(defaults, dict):
        return
    for key, val in defaults.items():
        if key != "_mode":
            _try_setattr(px, key, copy.deepcopy(val))
    matrix_wing_flags.restore(panel, px, defaults, visible=True)
    _restore_video_default(px)
    _reset_output_gamma(px)      # ★ R183：分区 gamma 缓存由配置派生，单独复位
    mode = str(defaults.get("_mode", "") or "")
    if mode:
        try:
            px.set_mode(mode)
        except Exception:
            logger.debug("matrix: reset pixel mode failed", exc_info=True)
    from .matrix_pixel_workspace_ui import sync_pixel_editor
    sync_pixel_editor(panel)


def _reset_output_gamma(px) -> None:
    """清掉分区 gamma 缓存（helper；引擎无该方法时静默）。"""
    if not hasattr(px, "set_output_gamma"):
        return
    try:
        px.set_output_gamma(None)
    except Exception:
        logger.debug("matrix: reset output gamma failed", exc_info=True)


def _coerce_runtime_value(key, val):
    """运行参数值规整（颜色 → 3 元 int 元组；不合法返回 None = 跳过该键）。"""
    if key != "matrix_effect_color":
        return val
    try:
        return tuple(int(v) for v in list(val)[:3])
    except Exception:
        return None



def _apply_pixel_mode(eng, params: dict) -> None:
    """切到存档的像素效果 id（运行参数，面板控件上不一定可见）。"""
    mode = str(params.get("mode", "") or "")
    if not mode:
        return
    try:
        eng.set_mode(mode)
    except Exception:
        logger.debug("matrix: runtime mode restore failed", exc_info=True)


def _apply_pixel_runtime_params(panel, state: dict) -> None:
    """把 `state['pixel_params']` 里的**运行参数**灌进引擎（不碰控件/缩略图）。

    ★★ 2026-09-21 用户实报（"点像素场景没反应/效果不对"）：R123/R130 定案把
      `load_state` 限在 `for_edit=True`，但 `pixel_params.mode`（像素效果 id）、
      `threshold`、波形造型等是**运行参数**、面板控件上要么没有、要么看不见 →
      纯播放路径既不还原也不应用，面板停在调用前的效果上；随后
      `scene_takeover._start_matrix_output` 按面板当前参数补启动 →
      用户看到"点了没反应 / 出来的不是存的那个效果"。
    """
    params = (state or {}).get("pixel_params")
    if not isinstance(params, dict) or not params:
        return
    eng = getattr(panel, "_pixel_engine", None)
    if eng is None:
        return
    from .matrix_pixel_audio_modes import restore_audio_controls
    restore_audio_controls(eng, params)
    for key in _PIXEL_RUNTIME_KEYS:
        if key in params:
            val = _coerce_runtime_value(key, params[key])
            if val is not None:
                _try_setattr(eng, key, val)
    _restore_wing_direction(panel, eng, params)
    _apply_pixel_mode(eng, params)


def _quiesce_other_transport(panel, want_mode: str) -> None:
    """把"非目标模式"那一路的输出停掉（模式被运行态还原切换时用）。

    ★ 为什么必要：运行态还原用 `blockSignals` 直接改 `_mode`，不会触发模式按钮的
      toggled 回调 → 上一条输出（ppt 引擎在播 / 像素在跑）会残留。
      而 `scene_takeover._matrix_outputting` 看的是"引擎在播/像素在跑" →
      它会以为矩阵已在出光而**跳过补启动**，画面反而一行都不写（"点了没反应"）。
    """
    try:
        if want_mode == "pixel":
            eng = getattr(panel, "_engine", None)
            if eng is not None and bool(getattr(eng, "is_playing", False)):
                eng.pause()
        elif want_mode == "ppt":
            if bool(getattr(panel, "_pixel_running", False)):
                panel._stop_pixel_mode(full_release=False, clear_output=True)
    except Exception:
        logger.debug("matrix: quiesce other transport failed", exc_info=True)


def _clear_matrix_stage(panel, set_mode=None) -> None:
    """"清台"：帧序列 + 像素参数 + 工作模式全部回到空/默认。

    ★★ 2026-09-21 用户实报（数据串台）：`matrix_slideshow_state` 为空、或换了工程时
      `load_state` 早退（`if not state: return`），于是**帧序列与像素参数根本不跟着
      工程走** —— 打开工程 A 做几帧，再打开/新建工程 B，Matrix 页里还是 A 的帧序列，
      在 B 里一保存就写进 B。

    `set_mode` = 面板的 `_restore_panel_mode(panel, state)`（由调用方传入，避免本模块
    反向 import 面板造成循环依赖）。
    """
    from .matrix_running_outputs import stop_all
    stop_all(panel)
    eng = getattr(panel, "_engine", None)
    if eng is not None:
        try:
            eng.from_dict({})          # 清帧序列/配置（不动 nm 与设备注入）
        except Exception:
            logger.debug("matrix: clear slides on stage reset failed", exc_info=True)
        for step in ("_rebuild_thumbs", "_update_thumb_selection"):
            try:
                getattr(panel, step)()
            except Exception:
                logger.debug("matrix: %s after stage reset failed", step, exc_info=True)
    # 帧序列已被清掉 → 不能把上一工程的画面留在通道上（停输出 + 落层）。
    try:
        panel._stop_running_output()
        panel._clear_matrix_output_layers()
    except Exception:
        logger.debug("matrix: stop output on stage reset failed", exc_info=True)
    panel._manual_slice_states = None
    _reset_pixel_engine_defaults(panel)
    if callable(set_mode):
        set_mode(panel, {"mode": "ppt"})


def _maybe_clear_stage_on_project_switch(panel, set_mode=None) -> bool:
    """工程身份变化 → 清台。返回是否真的清了。

    身份用 `_project_token`（`created_at` + `name`）而不是对象 id：undo/redo 的域合并
    会重建 Project 对象（`ProjectManager._merged_project`），用 id 判会把"同一工程"
    误判成换工程、清掉用户内存里还没保存的帧序列。
    """
    token = _project_token(_pm_project(panel))
    prev = getattr(panel, "_stage_project_token", None)
    if token == prev:
        return False
    panel._stage_project_token = token
    if prev is None:
        return False                        # 首次记录（刚构造）→ 无历史可清
    _clear_matrix_stage(panel, set_mode)
    return True


def refresh_project_fixture_scope(panel, set_mode=None) -> None:
    from .matrix_fixture_scope import refresh_project_fixture_scope as refresh
    refresh(panel, set_mode)


def _restore_pixel_video(panel, eng, video_path) -> None:
    """视频采样源还原（含"清掉上一条视频"与清除按钮可用态）。"""
    if video_path and hasattr(eng, 'load_video'):
        eng.load_video(video_path)
    elif hasattr(eng, 'clear_video'):
        eng.clear_video()
    try:
        if hasattr(panel._pixel_param_panel, '_video_clear_btn'):
            panel._pixel_param_panel._video_clear_btn.setEnabled(bool(video_path))
    except Exception:
        pass


#: 视频扩展字段（pixel_params 与 config 两处同键；还原时双向取值）。
_VIDEO_EXT_KEYS = (
    "video_source", "camera_device", "video_in_point", "video_out_point",
    "video_direction", "video_rate", "video_warp_corners", "sample_mask_path",
)


def _merge_video_ext_sources(panel, params: dict) -> dict:
    """pixel_params（新档）优先、缺键回落 config 同名字段（helper）。"""
    merged = dict(params or {})
    cfg = getattr(getattr(panel, "_engine", None), "config", None)
    if cfg is None:
        return merged
    for key in _VIDEO_EXT_KEYS:
        if key not in merged:
            merged[key] = getattr(cfg, key, None)
    return merged


def _restore_video_ext(panel, eng, params: dict) -> None:
    """★ R183：摄像头源/入出点/方向/速率/四角/掩码 的工程还原（helper）。

    取值优先 pixel_params（新档），缺 key 时回落 `slideshow_engine.config` 的
    同名字段（MatrixSlideshowConfig 直存路径），再缺 = 保留引擎当前值。
    摄像头只还原**源意图**（不在这里抓设备，播放开始由 `_ensure_video_capture` 打开）。
    """
    from .matrix_slideshow_panel import _pget
    from .matrix_video_cache import refresh_video_sample
    from .matrix_video_transforms import (
        normalize_corners, normalize_video_direction, normalize_video_rate, normalize_video_source_kind,
    )
    merged = _merge_video_ext_sources(panel, params)
    eng.video_in_point = _clamped_int(_pget(merged, 'video_in_point', eng, 0), 0)
    eng.video_out_point = _clamped_int(_pget(merged, 'video_out_point', eng, 0), 0)
    eng.video_direction = normalize_video_direction(
        _pget(merged, 'video_direction', eng, 'forward'))
    eng.video_rate = normalize_video_rate(_pget(merged, 'video_rate', eng, 1.0))
    eng.video_warp_corners = normalize_corners(
        merged.get('video_warp_corners')) if 'video_warp_corners' in merged else \
        getattr(eng, 'video_warp_corners', None)
    _restore_sample_mask(eng, merged.get('sample_mask_path'))
    # ★ W2 #4/#6：外部纹理源（spout/ndi）登记位也要随工程还原
    eng.video_source = normalize_video_source_kind(str(_pget(merged, 'video_source', eng, 'file') or 'file'))
    eng.camera_device = _clamped_int(_pget(merged, 'camera_device', eng, 0), 0)
    refresh_video_sample(eng, reset_history=True)


def _clamped_int(v, minimum: int = 0) -> int:
    """任意值 → int 且不低于 minimum（helper 压 CC）。"""
    try:
        return max(int(minimum), int(v or 0))
    except (TypeError, ValueError):
        return int(minimum)


def _sync_video_ext_to_config(panel) -> None:
    """★ R183：像素引擎的视频扩展字段回写 `engine.config`（落盘前调用）。

    MatrixSlideshowConfig 是这些字段的存读真源（save_state 里 pixel_params 仅作
    旧档兼容副本）；掩码只存**路径**，图片数据不落工程。
    """
    eng = getattr(panel, "_pixel_engine", None)
    cfg = getattr(getattr(panel, "_engine", None), "config", None)
    if eng is None or cfg is None:
        return
    try:
        _copy_video_ext_fields(eng, cfg)
    except Exception:
        logger.debug("matrix: sync video ext to config failed", exc_info=True)


def _copy_video_ext_fields(eng, cfg) -> None:
    """逐字段拷贝 + 类型规整（helper 压 CC）。"""
    for key in ("video_source", "camera_device", "video_in_point",
                "video_out_point", "video_direction", "video_rate",
                "sample_mask_path"):
        setattr(cfg, key, getattr(eng, key, getattr(cfg, key)))
    corners = getattr(eng, "video_warp_corners", None)
    cfg.video_warp_corners = _corners_payload(corners)
    cfg.video_rate = float(cfg.video_rate or 1.0)
    cfg.camera_device = _clamped_int(cfg.camera_device, 0)
    cfg.video_in_point = _clamped_int(cfg.video_in_point, 0)
    cfg.video_out_point = _clamped_int(cfg.video_out_point, 0)
    cfg.video_source = str(cfg.video_source or "file")
    try:
        from .matrix_video_transforms import normalize_video_source_kind
        cfg.video_source = normalize_video_source_kind(cfg.video_source)
    except Exception:
        logger.debug("matrix: normalize video_source failed", exc_info=True)
    cfg.video_direction = str(cfg.video_direction or "forward")
    cfg.sample_mask_path = str(cfg.sample_mask_path or "")


def _corners_payload(corners):
    """角点（ndarray/list/None）→ JSON 可存列表或 None（helper）。"""
    if corners is None:
        return None
    if hasattr(corners, "tolist"):
        return [list(p) for p in corners.tolist()]
    return list(corners)


def _restore_sample_mask(eng, mask_path) -> None:
    """掩码路径 → 引擎掩码数组（无路径 → 清除；读失败 → 保留路径但无掩码）。"""
    path = str(mask_path or "")
    eng.sample_mask_path = path
    if not path:
        eng.sample_mask = None
        return
    try:
        from .matrix_video_transforms import load_mask_array
        mask = load_mask_array(path)
    except Exception:
        mask = None
    eng.sample_mask = mask


def _restore_pixel_params(panel, pixel_params: dict, state: dict) -> None:
    """把 `pixel_params` 灌回像素引擎 + 同步参数控件（**编辑语义**，`load_state` 用）。

    原为 `matrix_slideshow_panel.load_state` 里的内联长块；`load_state` 因此逼近门禁
    行数上限，抽成模块函数（逐行搬移，语义零变化：`_pget` 仍是"缺 key 就不动当前值"）。
    """
    from .matrix_slideshow_panel import _pget   # 调用期 import：避免模块级循环依赖
    eng = panel._pixel_engine
    from .matrix_pixel_audio_modes import restore_audio_controls
    restore_audio_controls(eng, pixel_params)
    eng.threshold = _pget(pixel_params, 'threshold', eng, 0.5)
    eng.speed = _pget(pixel_params, 'speed', eng, 1.0)
    eng.invert = _pget(pixel_params, 'invert', eng, False)
    eng.pixel_audio_reactive = _pget(pixel_params, 'pixel_audio_reactive', eng, True)
    eng.matrix_wave_density = _pget(pixel_params, 'matrix_wave_density', eng, 1.0)
    panel._wing_direction = normalize_wing_direction(
        pixel_params.get('wing_direction', state.get('wing_direction', panel._wing_direction)))
    eng.wing_direction = panel._wing_direction
    panel._sync_wing_direction_buttons()
    matrix_wing_flags.restore(panel, eng, dict(state, **pixel_params), visible=True)
    eng.matrix_wave_width = _pget(pixel_params, 'matrix_wave_width', eng, 0.12)
    eng.matrix_wave_twist = _pget(pixel_params, 'matrix_wave_twist', eng, 0.0)
    eng.matrix_symmetry_mix = _pget(pixel_params, 'matrix_symmetry_mix', eng, 0.5)
    eng.screen_region = pixel_params.get('screen_region')
    eng.screen_brightness_boost = _pget(pixel_params, 'screen_brightness_boost', eng, 1.0)
    eng.screen_contrast = _pget(pixel_params, 'screen_contrast', eng, 1.0)
    eng.screen_channel = _pget(pixel_params, 'screen_channel', eng, 'luminance')
    eng.screen_smooth = _pget(pixel_params, 'screen_smooth', eng, 0.6)
    eng.screen_gamma = _pget(pixel_params, 'screen_gamma', eng, 1.0)
    eng.screen_threshold_low = _pget(pixel_params, 'screen_threshold_low', eng, 0.0)
    eng.screen_threshold_high = _pget(pixel_params, 'screen_threshold_high', eng, 1.0)
    eng.screen_fps = _pget(pixel_params, 'screen_fps', eng, 25.0)
    _restore_pixel_video(panel, eng, _pget(pixel_params, 'video_path', eng, ''))
    _restore_video_ext(panel, eng, pixel_params)
    eng.audio_amplitude = _pget(pixel_params, 'audio_amplitude', eng, 2.0)
    eng.audio_norm_offset = _pget(pixel_params, 'audio_norm_offset', eng, 0.0)
    eng.audio_bar_direction = _pget(pixel_params, 'audio_bar_direction', eng, 'bottom')
    eng.audio_bar_style = _pget(pixel_params, 'audio_bar_style', eng, 'solid')
    eng.audio_band_layout = _pget(pixel_params, 'audio_band_layout', eng, 'linear')
    eng.audio_bar_gamma = _pget(pixel_params, 'audio_bar_gamma', eng, 0.85)
    eng.audio_bar_softness = _pget(pixel_params, 'audio_bar_softness', eng, 0.08)
    eng.audio_bar_smoothing = _pget(pixel_params, 'audio_bar_smoothing', eng, 0.35)
    eng.audio_bar_motion = _pget(pixel_params, 'audio_bar_motion', eng, 1.0)
    eng.audio_peak_hold = _pget(pixel_params, 'audio_peak_hold', eng, 0.45)
    eng.audio_peak_decay = _pget(pixel_params, 'audio_peak_decay', eng, 0.035)
    eng.matrix_audio_mod_target = _pget(pixel_params, 'matrix_audio_mod_target', eng, 'brightness_speed')
    eng.matrix_audio_mod_source = _pget(pixel_params, 'matrix_audio_mod_source', eng, 'rms')
    eng.matrix_audio_mod_amount = _pget(pixel_params, 'matrix_audio_mod_amount', eng, 0.6)
    eng.matrix_audio_mod_floor = _pget(pixel_params, 'matrix_audio_mod_floor', eng, 0.15)
    eng.matrix_audio_mod_attack_ms = _pget(pixel_params, 'matrix_audio_mod_attack_ms', eng, 35.0)
    eng.matrix_audio_mod_release_ms = _pget(pixel_params, 'matrix_audio_mod_release_ms', eng, 220.0)
    color = _pget(pixel_params, 'matrix_effect_color', eng, (255, 255, 255))
    try:
        eng.matrix_effect_color = tuple(int(v) for v in color[:3])
    except Exception:
        eng.matrix_effect_color = (255, 255, 255)
    try:
        panel._pixel_param_panel._sync_pixel_param_widgets()
    except Exception:
        pass
    mode = pixel_params.get('mode', '')
    if mode:
        eng.set_mode(mode)
    from .matrix_pixel_workspace_ui import sync_pixel_editor
    sync_pixel_editor(panel)


__all__ = [
    "_apply_pixel_runtime_params", "_clear_matrix_stage", "_init_stage_identity",
    "_maybe_clear_stage_on_project_switch", "_pm_project", "_project_token",
    "_quiesce_other_transport", "_reset_pixel_engine_defaults",
    "_restore_pixel_params", "_restore_video_ext", "_snapshot_pixel_params",
]
