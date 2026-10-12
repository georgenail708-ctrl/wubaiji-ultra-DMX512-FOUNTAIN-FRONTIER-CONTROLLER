# -*- coding: utf-8 -*-
"""工程 undo/redo 的**面板/仓库回灌**（补齐 `_reload_panels_from_project` 的缺口）。

背景（2026-09-13 审计发现，用户要求整治）：
  `MainWindow._snapshot_for_undo` 会先把各面板/仓库收集进 `project` 再快照，
  所以**快照里字段是全的**。但 `_reload_panels_from_project()`（undo 后调用）
  只把 `topview_state` / `route_order_state` 回灌给面板，**其余一律没回灌内存**：

      programmer_state · effect_browser_state · matrix_slideshow_state ·
      audio_driver_state · processor_state · audio_bridge_state ·
      programmer_cues_data · param_envelopes_data · custom_effects_data ·
      trajectory_curves_data · baked_arrangement_data · dispatch_state

  后果：undo 之后这些字段在内存里**没有真正还原**；而下一次保存又会用"没还原的
  内存"覆盖刚恢复的 project 值 —— **undo 对它们等于静默无效**。

本模块提供 `restore_stores_from_project(project)`：把上述字段按"仓库单例
`from_dict` / 面板 `load_state`"逐一灌回。设计纪律：
  · **只读 project、只调既有公开接口**（各 store 的 from_dict、面板的 load_state）；
  · 每个字段独立 try/except，任一失败不影响其余（避免一处坏掉整个 undo）；
  · 由 `_reload_panels_from_project` **一行调用**接入（不往上帝类加方法）。
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any, Callable, Optional

from .scoped_state_restore import safe_attribute

log = logging.getLogger(__name__)


def _safe(what: str, fn: Callable[[], Any]) -> bool:
    """执行一个回灌步骤；失败记日志但不抛出（undo 不能因一个仓库坏掉而整体失败）。"""
    try:
        fn()
        return True
    except Exception:
        log.debug("undo 回灌失败: %s", what, exc_info=True)
        return False


def _append_scene_panel(out: list, holder: Any) -> None:
    """`holder` 若是主窗（持 `_dispatch_widget`）→ 把其已加载的调度台面板收进 `out`。"""
    try:
        w = getattr(holder, "_dispatch_widget", None)
    except Exception:
        return
    if w is not None and w not in out:
        out.append(w)


def _scene_panels(main_window: Any = None) -> list:
    """尽力收集"当前活着的调度台面板"（拿不到给空列表，绝不抛）。

    ★★ R136 用户实报（undo/redo 回滚场景库后"幽灵卡片 + 场景永久丢失"）：
      `dispatch_state` 被 `from_dict` **整体替换**，而卡片列表是画出来的副本 →
      不重画就画着库里已不存在的条目（点它毫无反应），库里新出现的条目没有卡片
      （不可达 = 场景永久丢失）。而 `MainWindow._reload_panels_from_project` 不引用
      `_dispatch_widget`，故这里**尽力**定位已加载的面板：优先用调用方给的主窗，
      否则在 QApplication 的顶层窗口里找持 `_dispatch_widget` 的那个。
      找不到（UI 未加载 / 无 QApplication）→ 空列表 = 跳过：**回灌层不依赖 UI**，
      面板晚建时会在懒建路径自行读库（见 main_window 的 `_load_dispatch_library`）。
    """
    out: list = []
    if main_window is not None:
        _append_scene_panel(out, main_window)
        return out
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        tops = list(app.topLevelWidgets()) if app is not None else []
    except Exception:
        tops = []
    for top in tops:
        _append_scene_panel(out, top)
    return out


def _refresh_one_scene_panel(panel: Any) -> int:
    """对单个调度台面板做一件事：重画卡片列表（**不动**运行期记账）。

    ★★ R183 审计⑦B P0-2 修复（任意一次 Ctrl+Z 把运行期记账整表归零）：
      修前这里还会调 `reset_scene_runtime_state`（清激活集 / 激活顺序 / 层增益），
      而它是**库换新**（新建工程 / 打开工程）专用语义。放在"每次 undo 回灌"里
      造成：点亮 A（高亮 + 层增益记账）→ 随便按一次 Ctrl+Z（哪怕撤回的是别处的
      改动）→ active=[]、顺序=[]、层增益归还，**但面板输出没停**（ch4 仍是 200）
      → 卡片不亮、右键"关闭"被"未在播放"守卫拒绝 → 该场景既亮着又关不掉
      （幽灵出光）；同时场景推杆的 30% 也被跳回 100%（同根因 P2-10）。
      正解：undo 只把**数据**画出来（`refresh_saved`），运行期状态是"当下谁在
      亮着"的事实，撤销一次数据改动不该改变它。

      条数变少（被回滚掉的场景）时的高亮/记账残留由**释放/清台链**自然收敛
      （用户下一次关闭或存场景时的清台都会摘账），不再靠"每次 undo 清表"。
    """
    try:
        panel.refresh_saved()
        return 1
    except Exception:
        log.debug("undo 后刷新调度台卡片列表失败", exc_info=True)
        return 0


def refresh_dispatch_scene_ui(main_window: Any = None) -> int:
    """把"场景库已整体换新"告诉**已加载的**调度台面板（返回做成的项数）。

    见 `_scene_panels` 的说明；`main_window` 为 None 时自行尽力定位。
    """
    ok = 0
    for panel in _scene_panels(main_window):
        ok += _refresh_one_scene_panel(panel)
    return ok


def restore_stores_from_project(project: Any, domain=None) -> int:
    """把 project 里的各仓库/面板状态回灌进**内存单例**（`project` = undo 后的快照）。"""
    if project is None:
        return 0
    from .scoped_state_restore import restore_if_requested
    safe = partial(restore_if_requested, domain=domain)
    ok = 0

    def _g(name: str, default=None):
        """安全读属性。

        ★ 必须 try/except：`getattr` 在属性访问器自身抛异常时**仍会抛出**
          （`default` 只兜住 AttributeError）。实测传入一个 `__getattr__` 抛
          RuntimeError 的对象时，函数会在第一步就整体失败——与"每步独立、
          一处坏不影响其余"的设计目标相违。
        """
        try:
            return getattr(project, name, default)
        except Exception:
            return default

    # ── 1) 纯数据仓库（单例 from_dict；主窗收集/加载路径同源）────────────────
    try:
        from ...processing.dispatch import (get_effect_cache_store,
                                            get_release_registry)
    except Exception:  # pragma: no cover - 包结构异常时降级
        get_effect_cache_store = None  # type: ignore[assignment]

    if get_effect_cache_store is not None:
        ds = _g("dispatch_state", {}) or {}

        def _restore_dispatch():
            get_effect_cache_store().from_dict(ds)
            get_release_registry().from_dict(ds.get("releases", {}) or {})
        ok += safe("dispatch_state", _restore_dispatch)

    # ★★ R136：库已被整体替换 → 已加载的调度台必须重画卡片并归还运行期记账
    #   （否则幽灵卡片 + 场景永久丢失；见 `refresh_dispatch_scene_ui`）。
    ok += safe("dispatch_scene_ui", refresh_dispatch_scene_ui)

    def _ui_store(mod: str, attr: str, getter: str, field: str):
        """通用：`src.ui.<mod>.<getter>()` 的 from_dict(field)。"""
        def _run():
            import importlib
            m = importlib.import_module(mod)
            store = getattr(m, getter)()
            store.from_dict(_g(field, {}) or {})
        return _run

    for mod, getter, field in (
        ("src.ui.programmer_cue_store", "get_programmer_cue_store", "programmer_cues_data"),
        ("src.ui.param_envelope", "get_param_envelope_store", "param_envelopes_data"),
        ("src.ui.trajectory_curve", "get_trajectory_curve_store", "trajectory_curves_data"),
    ):
        ok += safe(field, _ui_store(mod, "", getter, field))

    # 自动编曲预烘焙（引擎侧单例，接口名不同故单独处理）
    def _restore_baked():
        from ...engine.auto_arrange.baked_arrangement import get_baked_arrangement_store
        get_baked_arrangement_store().from_dict(_g("baked_arrangement_data", {}) or {})
    ok += safe("baked_arrangement_data", _restore_baked)

    # ★ R178：custom_effects_data 的回灌/引擎注册已删（自定义效果库整体移除，
    #   旧快照里的该字段安全忽略——迁移在工程装载点做，undo 不再需要管它）。

    return ok


def _restore_panel_state(attr, panel, state):
    if attr == "_programmer_widget":
        from ..project_selection_restore import load_programmer_passively
        return load_programmer_passively(panel, state)
    return panel.load_state(state)


def restore_panels_from_project(main_window: Any, project: Any, domain=None) -> int:
    """把面板级状态回灌给**已加载的面板**（未加载的跳过——懒加载后自会读到）。

    只对已存在的 widget 调其既有 `load_state`，不构造新面板（避免 undo 触发
    一堆懒加载）。返回成功项数。
    """
    if main_window is None or project is None:
        return 0
    from .scoped_state_restore import restore_if_requested
    safe = partial(restore_if_requested, domain=domain)
    ok = 0

    pairs = (
        ("_programmer_widget", "programmer_state"),
        ("_effect_browser_widget", "effect_browser_state"),
        ("_matrix_pattern_widget", "matrix_slideshow_state"),
        ("_processor_widget", "processor_state"),
    )
    for attr, field in pairs:
        w = safe_attribute(main_window, attr)
        if w is None or not hasattr(w, "load_state"):
            continue
        state = safe_attribute(project, field)
        if state is None:
            continue

        def _run(w=w, state=state):
            # 部分面板的 load_state 对空 dict 会清状态；只在有内容时回灌
            if state or not isinstance(state, dict):
                _restore_panel_state(attr, w, state)
        ok += safe(field, _run)

    # Programmer 音频驱动参数（另一入口，签名不同）
    pw = safe_attribute(main_window, "_programmer_widget")
    audio_state = safe_attribute(project, "audio_driver_state")
    if pw is not None and audio_state and hasattr(pw, "load_audio_driver_state"):
        ok += safe("audio_driver_state", lambda: pw.load_audio_driver_state(audio_state))

    # 音频桥接（全局单例）
    bridge_state = safe_attribute(project, "audio_bridge_state")
    if bridge_state:
        def _restore_bridge():
            from ...audio import get_global_bridge
            b = get_global_bridge()
            if b is not None:
                b.load_dict(bridge_state)
        ok += safe("audio_bridge_state", _restore_bridge)

    return ok


__all__ = ["restore_stores_from_project", "restore_panels_from_project",
           "refresh_dispatch_scene_ui"]
