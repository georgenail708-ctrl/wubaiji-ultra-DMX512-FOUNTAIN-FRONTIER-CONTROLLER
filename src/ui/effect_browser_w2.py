# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2.py — R183 W2-FX 效果浏览器扩展的**总装面**（薄门面）。

深查② §一 剩余 14 项按功能域拆成多个 ≤500 行模块（CLAUDE.md §10.2「新文件
≤500 行」），本模块只做两件事：
  1. **安装**：``install(panel)`` 把代理过滤层 / 轨迹旁路 / 悬停监听 / 缩略定时器
     挂到面板上（``_build_ui`` 末尾一行调用）；
  2. **再导出**：把各功能模块的入口函数在本命名空间聚合（主文件的历史调用点
     只认一个模块名），并转发少量仍在本文件的历史入口。

模块地图：
  w2_store    用户态数据（标签/使用计数/宏/跟随音频/偏好/预设包编解码，无 Qt）
  w2_search   搜索词法（前缀 token + 取反，纯函数）
  w2_proxy    QSortFilterProxyModel 判据层（#16 的"判据"）
  w2_view     列表视图落地（#16 行隐藏 / #12 兼容集 / #10 热度 / #14 开关）
  w2_paint    卡片附加绘制（#6 徽标 / #10 计数 / #12 暗罩 / #14 缩略条）
  w2_base     共享只读工具（参数区间/默认值/状态栏/预设切换收口）
  w2_tags     #3 标签编辑器      w2_audio   #13 跟随音频
  w2_macro    #8 参数宏           w2_ab      #6 A/B 对比槽
  w2_pack     #9 预设包导入导出   w2_random  #5 随机预设 + #15 骰子微扰
  w2_dirty    #17 改动记账/回到默认                w2_hover   #11 悬停试听
  w2_menus    两个既有右键菜单的增量区块
  w2_dialogs  六个模态问答的统一薄封装（异常/取消语义集中一处）
"""
from __future__ import annotations

import logging

from . import effect_browser_w2_ab as _ab
from . import effect_browser_w2_audio as _audio
from . import effect_browser_w2_base as _base
from . import effect_browser_w2_dirty as _dirty
from . import effect_browser_w2_hover as _hover
from . import effect_browser_w2_menus as _menus
from . import effect_browser_w2_paint as _paint
from . import effect_browser_w2_proxy as _proxy
from . import effect_browser_w2_random as _random
from . import effect_browser_w2_tags as _tags
from . import effect_browser_w2_view as _view

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
#  安装（``_build_ui`` 末尾一行）
# ══════════════════════════════════════════════════════════════════════════

def install(panel) -> None:
    """挂载全部 W2 扩展点（任一失败只记日志，绝不影响面板构建）。"""
    _install_proxy(panel)
    _install_trajectory(panel)
    _say(_hover.install, panel)
    _say(_paint.start_thumb_timer, panel, 600)
    delegate = getattr(panel, "_preset_delegate", None)
    if delegate is not None:
        delegate._w2_panel = panel        # 绘制层取面板态的唯一引用


def _install_proxy(panel) -> None:
    """构造过滤代理（只做判据；落地在 w2_view.apply_instant_filter）。"""
    model = getattr(panel, "_preset_model", None)
    if model is None:
        return
    proxy = _proxy.PresetFilterProxy(getattr(panel, "_preset_list", None))
    proxy.setSourceModel(model)
    panel._w2_proxy = proxy


def _install_trajectory(panel) -> None:
    """参数变化旁路（``controlTrajectoryEvent`` 汇口）：#17 dirty 记账。"""
    try:
        panel.controlTrajectoryEvent.connect(
            lambda topic, value: _dirty.note_trajectory(panel, topic, value))
    except Exception:
        logger.debug("effect_browser_w2: trajectory hook failed", exc_info=True)


def _say(fn, *args) -> None:
    """调一个"失败不影响主流程"的函数。"""
    try:
        fn(*args)
    except Exception:
        logger.debug("effect_browser_w2: install step failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════
#  卡片 delegate（主绘制在 ``PresetItemDelegate``，本层只叠加）
# ══════════════════════════════════════════════════════════════════════════

def make_delegate(parent, panel):
    """构造卡片 delegate：``PresetItemDelegate`` 子类，绘制后叠加 W2 附加层。

    为什么用子类而不是改主文件绘制函数：``PresetItemDelegate._paint_item`` 在
    门禁基线内零余量，新增分支即"债务恶化"ERROR；子类 + ``super().paint``
    等价，且实例仍挂 ``_preset_delegate`` 名（既有测试与该名兼容）。
    """
    return _delegate_class()(parent, panel)


def _panel_of_delegate(delegate):
    """delegate 所属面板：优先注入口，其次沿控件父链上溯（找不到 → None）。

    为什么不用强引用：``panel → list → delegate → panel`` 会形成引用环，且面板
    被 Qt 删除后 Python 侧仍持有"已删 C++ 对象"的包装体 —— 绘制路径上任何一次
    误用都是崩溃级。父链上溯是无环弱关系，加注入口只作首帧兜底。
    """
    panel = getattr(delegate, "_w2_panel", None)
    if panel is not None:
        return panel
    widget = delegate.parent()
    while widget is not None:
        if hasattr(widget, "_preset_model"):
            return widget
        widget = widget.parent() if hasattr(widget, "parent") else None
    return None


def _delegate_class():
    """delegate 子类（延迟构造，避免与 ``effect_browser`` 形成顶层 import 环）。"""
    global _DELEGATE_CLS
    if _DELEGATE_CLS is not None:
        return _DELEGATE_CLS
    from .effect_browser import PresetItemDelegate

    class W2CardExtrasDelegate(PresetItemDelegate):
        """卡片附加层 delegate：先走既有绘制（含批 A 星标），再叠 W2 层。"""

        def __init__(self, parent=None, w2_panel=None):
            super().__init__(parent)
            self._w2_panel = w2_panel

        def paint(self, painter, option, index):            # noqa: N802 (Qt 命名)
            super().paint(painter, option, index)
            panel = _panel_of_delegate(self)
            if panel is not None:
                _paint.paint_card_extras(panel, painter, option.rect, index)

    _DELEGATE_CLS = W2CardExtrasDelegate
    return _DELEGATE_CLS


#: delegate 子类缓存（每进程只构造一次）
_DELEGATE_CLS = None


# ══════════════════════════════════════════════════════════════════════════
#  UI 构建（``_build_ui`` 各一行）
# ══════════════════════════════════════════════════════════════════════════

def build_tools_row(panel):
    """W2 功能条（#5 随机预设 / #6 存 A·存 B·互换 / #17 回到默认 /
    #9 导入·导出 / #10 热度优先 / #11 试听 / #14 缩略 / #15 微扰）。

    构建期异常 → 返回空控件（面板照常可用，只是少了这排入口）。
    """
    from . import effect_browser_w2_ui as _ui
    row = _ui.ToolsRow(panel)
    panel._w2_tools_row = row
    return row


def build_macro_area(panel):
    """参数宏旋钮区（#8）：三个宏槽滑杆 + 已映射参数计数。"""
    from . import effect_browser_w2_ui as _ui
    area = _ui.MacroArea(panel)
    panel._w2_macro_area = area
    return area


def build_dirty_label(panel):
    """标题行 dirty 标签（#17）：显示「已改 N 项」。"""
    from . import effect_browser_w2_ui as _ui
    label = _ui.DirtyLabel(panel)
    panel._w2_dirty_lbl = label
    return label


# ══════════════════════════════════════════════════════════════════════════
#  主文件调用点（一行一条）
# ══════════════════════════════════════════════════════════════════════════

def apply_instant_filter(panel, text):
    """#16：防抖期间即时收窄可见行（判据走代理，落地走行隐藏）。"""
    return _view.apply_instant_filter(panel, text)


def refresh_after_populate(panel) -> None:
    """列表重建后收口（#12 兼容集 / #10 热度 / #16 代理同步 / #14 缩略缓存）。"""
    _view.refresh_after_populate(panel)


def randomize_or_perturb(panel) -> str:
    """#15：底部「随机」按钮路由（微扰 ↔ 全域随机）。"""
    return _random.randomize_or_perturb(panel)


def expand_audio_follow(panel, preset_id, params):
    """#13：渲染前按音频帧折算绑定参数（未绑定 → 原对象返回）。"""
    return _audio.expand_audio_follow(panel, preset_id, params)


def append_param_actions(menu, panel, key, label="") -> None:
    """参数右键菜单追加「跟随音频 / 分配到宏」。"""
    _menus.append_param_actions(menu, panel, key, label)


def extend_card_menu(menu, panel, preset_id) -> None:
    """卡片右键菜单追加「编辑标签 / 使用次数」。"""
    _menus.extend_card_menu(menu, panel, preset_id)


def preset_switched(panel, preset_id) -> None:
    """载入预设后收口（#17 dirty 归零 + #6 A/B 槽跨预设作废 + #8 宏计数）。"""
    _base.preset_switched(panel, preset_id)


def note_usage(preset_id) -> int:
    """#10：记一次使用（热度榜数据源）。"""
    return _base.note_usage(preset_id)


# ── 状态键 / 工程持久化（#6）──────────────────────────────────────────────

#: save_state / load_state 用的工程状态键
W2_STATE_KEY = _ab.W2_STATE_KEY


def state_for_save(panel) -> dict:
    """#6：A/B 槽的工程内快照（无内容 → {}，调用方据此不写键）。"""
    return _ab.state_for_save(panel)


def apply_saved_state(panel, state) -> bool:
    """#6：从工程状态恢复 A/B 槽（缺键/异形 → 忽略）。"""
    return _ab.apply_saved_state(panel, state)


# ── 供定测/其它面板直接使用的再导出 ───────────────────────────────────────

def random_preset(panel) -> str:
    """#5：随机预设并载入。"""
    return _random.random_preset(panel)


def ab_capture(panel, slot) -> bool:
    """#6：把当前参数快照存入 A/B 槽。"""
    return _ab.ab_capture(panel, slot)


def ab_swap(panel) -> str:
    """#6：当前参数与另一槽互换。"""
    return _ab.ab_swap(panel)


def ab_slot(panel) -> str:
    """#6：当前生效的 A/B 槽位名（未入槽 → ""）。"""
    return _ab.ab_slot(panel)


def bind_audio_follow(panel, param_key, band="", depth=0.5) -> bool:
    """#13：写一个参数的跟随音频绑定（band 非法 → 解绑）。"""
    return _audio.bind_audio_follow(panel, param_key, band, depth)


def spec_spans(panel) -> dict:
    """当前预设数值参数的区间 {key: (lo, hi)}（宏/微扰真源）。"""
    return _base.spec_spans(panel)


def tag_vocabulary() -> list:
    """#3：全库既有标签词表（内置 ∪ 用户）。"""
    return _tags.tag_vocabulary()


def reset_dirty(panel) -> int:
    """#17：只把改动过的参数恢复预设默认。"""
    return _dirty.reset_dirty(panel)


def dirty_hint(panel) -> str:
    """#17：标题行提示文本（无改动 → ""）。"""
    return _dirty.dirty_hint(panel)


def dirty_keys(panel):
    """#17：当前 dirty 参数键（排序稳定）。"""
    return _dirty.dirty_keys(panel)


__all__ = [
    "W2_STATE_KEY", "ab_capture", "ab_slot", "ab_swap", "apply_instant_filter",
    "apply_saved_state", "append_param_actions", "bind_audio_follow",
    "build_dirty_label", "build_macro_area", "build_tools_row", "dirty_hint",
    "dirty_keys", "expand_audio_follow", "extend_card_menu", "install",
    "make_delegate", "note_usage", "preset_switched", "random_preset",
    "randomize_or_perturb", "refresh_after_populate", "reset_dirty",
    "spec_spans", "state_for_save", "tag_vocabulary",
]


def known_preset_ids(ids) -> list:
    """历史/最近里的预设 id → 只留引擎仍登记的（C4：截图 13-22-08「历史使用」出现
    ``light_v37_dual_scanner`` 等裸 id——它们已被 effect_retire 下线/剪除，点击无反应）。

    引擎不可用时原样返回（不因诊断失败把历史整体清空）。
    """
    try:
        from .effect_engine import get_engine
        presets = get_engine().presets
    except Exception:
        return list(ids or [])
    return [pid for pid in (ids or []) if pid in presets]
