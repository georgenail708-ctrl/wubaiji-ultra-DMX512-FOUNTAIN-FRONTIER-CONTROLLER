# -*- coding: utf-8 -*-
"""教程「超链接模式」的功能映射表（**唯一真源**）。

教程页面里用 `data-feature="<id>"` 标记可点击的功能链接（或锚点 href=`#feature:<id>`），
内嵌 viewer 把点击转成 Qt 信号，MainWindow 调 `resolve()` 拿到「要打开的面板 + 要高亮的控件」，
再交给 `feature_callout.FeatureCalloutOverlay` 画引出标注。

设计原则（可维护性优先）：
  - **纯数据 + 一个解析函数**：新增功能说明 = 往 FEATURES 加一条，不改代码逻辑。
  - 只引用**稳定标识**（dock 属性名 / 控件 objectName），不硬编码坐标。
  - 解析失败**优雅降级**：objectName 找不到时回退到面板本体（仍能打开+高亮），
    dock 也找不到时返回 None，由调用方提示而不是崩溃。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True)
class FeatureSpec:
    """一条功能说明。

    id       : 教程 HTML 里 `data-feature` 用的稳定标识（英文点号，如 programmer.channels）
    title    : 标注框标题（短）
    body     : 标注框正文（1-3 句，讲「这是什么/怎么用」）
    dock     : MainWindow 上的 dock 属性名（如 "_programmer_dock"）——用于打开面板
    widget   : MainWindow 上的面板 widget 属性名（可选，优先于 dock 取控件）
    object   : 面板内目标控件的 objectName（可选；找不到则回退面板本体）
    group    : 分类（用于目录/检索，非必需）
    """
    id: str
    title: str
    body: str
    dock: str = ""
    widget: str = ""
    object: str = ""
    group: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  功能表（尽量配齐：主窗口 → 配接 → TopView → Programmer → 效果 → 矩阵 →
#  时间线 → 关联器 → 轨迹 → 音频 → 摇杆 → 网络 → 逻辑设备 → 工作流）
# ─────────────────────────────────────────────────────────────────────────────
_SPECS: List[FeatureSpec] = [
    # —— 主窗口与文件 ——
    FeatureSpec("main.window", "主窗口", "整体工作区：菜单栏、面板区与传输控制。"
                "所有编辑手段（Programmer/效果/矩阵/音频/摇杆）各写一层，"
                "由网络管理器按「基线 + 偏移」混合成唯一输出流。",
                widget="__self__", object="_panel_toolbar", group="主窗口"),
    FeatureSpec("main.import_", "导入 工程", "文件 ▸ 导入 工程(.)："
                "读取工程里的设备、配接与 3D 坐标，自动建 TopView 页面与设备组（按文件夹归档）。",
                widget="__self__", object="menuBar", group="文件"),
    FeatureSpec("main.save", "保存/另存", "文件 ▸ 保存：把工程（含设备、配接、TopView 页面、"
                "时间线、效果层）写入 .dmxt。",
                widget="__self__", object="menuBar", group="文件"),

    # —— 配接 ——
    FeatureSpec("patch.panel", "配接面板", "把设备定义（通道/模式）与实例配接到 DMX 地址；"
                "支持 CSV 与 工程导入。", dock="_patch_dock", group="配接"),

    # —— TopView ——
    FeatureSpec("topview.panel", "TopView", "二维俯视布局：设备按真实世界坐标摆放，"
                "文件夹归档成页，并自动拟合弧线/圆等参考图形。", dock="_topview_dock", group="TopView"),
    FeatureSpec("topview.groups", "设备组与选择", "页面内可直接框选设备；选择顺序即设备编号序，"
                "方向类效果按该顺序推进。", dock="_topview_dock", group="TopView"),

    # —— Programmer ——
    FeatureSpec("programmer.panel", "Programmer（编程器）", "手动控制台：按通道类型成行，"
                "拖条/摇杆直接写当前选区的通道值。", dock="_programmer_dock", group="Programmer"),
    FeatureSpec("programmer.channels", "通道行与控灯粒度", "每行一类通道；「单控灯/整体控灯」"
                "决定包内多个配灯是一个灯还是逐个独立值。", dock="_programmer_dock", group="Programmer"),
    FeatureSpec("programmer.cue", "Cue（暂存/快速调用）", "Cue 只是暂存与快速调用机制，"
                "本身不写入时间线；写入时间线的唯一途径是「录制」。", dock="_programmer_dock", group="Programmer"),

    # —— 效果库 ——
    FeatureSpec("effect.panel", "效果库", "内置效果预设（1000+）：选设备组 → 选效果 → 播放，"
                "效果作为「偏移层」叠加，不覆盖其它编辑层。", dock="_effect_browser_dock",
                widget="_effect_browser_widget", group="效果"),
    FeatureSpec("effect.direction", "应用方向", "「应用模式 / 方向」决定效果沿设备空间推进的方向"
                "（左→右/右→左/前→后/中间扩散…），按坐标轴定序。",
                dock="_effect_browser_dock", group="效果"),
    FeatureSpec("effect.spatial", "坐标系与控灯粒度", "坐标系（各自圆/整体矩阵）与控灯粒度正交："
                "各自坐标系=每台设备自己的圆；整体坐标系=全部灯一个矩阵。",
                dock="_effect_browser_dock", group="效果"),

    # —— 矩阵 ——
    FeatureSpec("matrix.panel", "矩阵（Matrix）", "像素/图案矩阵：把设备排成矩阵做像素级图案与动画。",
                dock="_matrix_pattern_dock", group="矩阵"),
    FeatureSpec("fixture_group.panel", "设备组管理", "创建/编辑设备组与逻辑组合设备，"
                "供 Programmer、效果与编曲复用。", dock="_fixture_group_dock", group="分组"),

    # —— 时间线 ——
    FeatureSpec("timeline.panel", "时间线", "编排中心：把「录制的片段」按轨道与时间摆放；"
                "片段右键菜单提供分组过渡/方向/平滑/灯跟踪等高级处理。", widget="_timeline_widget",
                group="时间线"),
    FeatureSpec("timeline.record", "录制", "录制=逐帧捕获信号总线的最终合成输出并写成片段——"
                "这是把编辑结果写入时间线的唯一途径。", widget="_timeline_widget", group="时间线"),
    FeatureSpec("timeline.clip_menu", "片段右键菜单", "方向（16 向）/组数/坐标系/批次/"
                "自动灯跟踪/冲突模式等都在片段右键菜单。", widget="_timeline_widget", group="时间线"),

    # —— 处理器 / 关联器 ——
    FeatureSpec("processor.panel", "处理器（Processor）", "集中处理工具区（含关联器等子面板）。",
                dock="_processor_dock", group="处理器"),
    FeatureSpec("linker.panel", "关联器（Track Linker）", "实时通道联动：每 25ms 读总线源通道"
                "→ 预设函数 → 写 linker 层。含「低值覆盖（跟水）」只减不加的遮罩语义。",
                dock="_processor_dock", group="处理器"),
    FeatureSpec("clipstudio.panel", "Clip 附加处理", "卡片画布 Studio：过渡方向/组数/平滑/"
                "灯跟踪/限速限位/泵主控等后处理，按 I/O sheet 组织并绑定到片段。",
                dock="_clip_studio_dock", widget="_clip_studio_widget", group="处理器"),

    # —— 编曲 / 轨迹 ——
    FeatureSpec("route.panel", "跑动编排", "道具沿轨道跑动的编排系统（火车/覆盖圈/道岔）。",
                dock="_route_order_dock", widget="_route_order_widget", group="编曲"),
    FeatureSpec("program.panel", "节目管理", "节目/序列的组织与调用。", dock="_program_dock", group="编曲"),

    # —— 网络 / 监视 ——
    FeatureSpec("network.panel", "网络", "Art-Net / sACN 的输入输出与域配置。", dock="_network_dock",
                group="网络"),
    FeatureSpec("monitor.panel", "监视器", "实时查看各域/各通道的数值与输出情况。", dock="_monitor_dock",
                group="网络"),

    # —— 教程自身 ——
    FeatureSpec("help.tutorial", "教程与说明书", "帮助 ▸ 使用教程：内置说明书与 外部文档，"
                "离线可读；说明书里的功能链接可直接打开对应面板并高亮标注。",
                dock="_tutorial_dock", group="帮助"),
]

FEATURES: Dict[str, FeatureSpec] = {s.id: s for s in _SPECS}


# ─────────────────────────────────────────────────────────────────────────────
#  说明书章节 id → 功能 id（**唯一真源**）
#  用途一：`tools/inject_manual_feature_links.py` 据此给章节注入可点击标记；
#  用途二：零基础模式靠它做「**随着浏览展示**」—— 读者滚到哪一章，就在界面上引出
#          该章对应功能的标注。新增章节映射只需在这里加一行。
#  只有「在界面上真的存在对应面板/控件」的章节才入表；纯概念章（前言/附录）不入。
# ─────────────────────────────────────────────────────────────────────────────
CHAPTER_FEATURES: Dict[str, str] = {
    "ch1": "main.window",
    "ch2": "patch.panel",
    "ch3": "topview.panel",
    "ch4": "programmer.panel",
    "ch5": "effect.panel",
    "ch6": "matrix.panel",
    "ch7": "timeline.panel",
    "ch8": "linker.panel",
    "ch9": "route.panel",
    "ch11": "network.panel",
    "ch12": "fixture_group.panel",
    "tutorial": "help.tutorial",
    # 逐控件参考章（滚到该章 → 标注该面板；控件级说明由 callout 正文承担）
    "ref-main": "main.window",
    "ref-patch": "patch.panel",
    "ref-topview": "topview.panel",
    "ref-topview-cad": "topview.panel",
    "ref-programmer": "programmer.panel",
    "ref-effect": "effect.panel",
    "ref-matrix": "matrix.panel",
    "ref-matrix-pattern": "matrix.panel",
    "ref-pattern-editor": "matrix.panel",
    "ref-slideshow": "matrix.panel",
    "ref-timeline": "timeline.panel",
    "ref-linker": "linker.panel",
    "ref-trajectory": "route.panel",
    "ref-audio": "clipstudio.panel",
    "ref-audio-bridge": "clipstudio.panel",
    "ref-effect-type": "effect.panel",
    "ref-modulation": "clipstudio.panel",
    "ref-joybind": "clipstudio.panel",
    "ref-network": "network.panel",
    "ref-fixgroup": "fixture_group.panel",
    "ref-logical": "fixture_group.panel",
    "ref-route": "route.panel",
    "ref-program": "program.panel",
    "ref-tutorial": "help.tutorial",
}


def feature_for_chapter(chapter_id: str) -> str:
    """章节 id → 功能 id；无映射返回空串（调用方静默跳过）。"""
    return CHAPTER_FEATURES.get(str(chapter_id or "").strip(), "")


def get(feature_id: str) -> Optional[FeatureSpec]:
    return FEATURES.get(str(feature_id or "").strip())


def all_features() -> List[FeatureSpec]:
    return list(_SPECS)


def groups() -> List[str]:
    seen: List[str] = []
    for s in _SPECS:
        if s.group and s.group not in seen:
            seen.append(s.group)
    return seen


def resolve(main_window, feature_id: str) -> Optional[Tuple[QWidget, FeatureSpec]]:
    """打开 feature 对应面板并返回 (要高亮的控件, 规格)；找不到返回 None。

    步骤：lazy 建面板 → 显示 dock → 取目标控件（objectName 优先，回退面板本体）。
    全程 try/except：任何一步失败都退化为"返回已能拿到的控件"，绝不抛给教程 UI。
    """
    spec = get(feature_id)
    if spec is None or main_window is None:
        return None
    dock = None
    if spec.dock:
        dock = getattr(main_window, spec.dock, None)
    if dock is not None:
        try:
            loader = getattr(main_window, "_lazy_load_panels", None)
            if callable(loader):
                loader(dock)
        except Exception:
            pass
        try:
            toggler = getattr(main_window, "_toggle_dock", None)
            if callable(toggler):
                toggler(dock, True)
        except Exception:
            pass
    # 目标控件：widget 属性 → objectName → dock → dock 内 widget
    #   widget="__self__" = 主窗口本身（用于菜单/整体说明这类没有独立面板的功能）；
    #   object="menuBar"  = 主窗口菜单栏（高亮它并说明"在某菜单下"）。
    target: Optional[QWidget] = None
    if spec.widget == "__self__":
        target = main_window
    elif spec.widget:
        target = getattr(main_window, spec.widget, None)
    if target is main_window and spec.object:
        # widget="__self__" 时 object 用作"主窗口上的控件属性/方法名"：
        # 便于把「整体工作区/菜单」这类说明锚到一个真实可见的控件上（而不是整窗）。
        try:
            cand = getattr(main_window, spec.object, None)
            if callable(cand):
                cand = cand()
            if isinstance(cand, QWidget):
                target = cand
        except Exception:
            pass
    if target is None and dock is not None:
        try:
            target = dock.widget()
        except Exception:
            target = None
    if target is None:
        target = dock
    if spec.object and target is not None:
        try:
            found = target.findChild(QWidget, spec.object)
            if found is not None and found.isVisible():
                target = found
        except Exception:
            pass
    if target is None:
        return None
    return target, spec


def resolve_anchor(main_window, feature_id: str) -> Optional[Tuple[QWidget, FeatureSpec, bool]]:
    """解析「可标注锚点」，但**绝不打开任何面板**（零基础模式·随浏览标注用）。

    与 `resolve()` 的分工：
      - `resolve()`：用户**主动点击**功能链接时调用 —— 会打开 dock，把对应面板带到眼前。
      - `resolve_anchor()`：用户**只是在读说明书**时调用 —— 只标注屏幕上**已经看得见**
        的东西，不因为翻页而弹出面板（旧实现预扫描全部功能，实测「一点击就把全部界面
        都打开了」，这是本条存在的唯一理由）。

    返回 `(锚点控件, 规格, 面板是否已打开)`：
      1. `widget="__self__"`（菜单/工具条这类常驻 chrome）→ 直接标注，视为已打开；
      2. `widget=<面板属性>` 且当前可见 → 标注真实控件；
      3. 面板 dock 当前可见 → 标注面板内目标控件；
      4. 面板未打开 → 标注该面板在顶栏的**开关按钮**（用户可见，语义即「此处打开」）；
      5. 都拿不到 → None，调用方静默跳过（不弹标注、不报错）。
    """
    spec = get(feature_id)
    if spec is None or main_window is None:
        return None

    target: Optional[QWidget] = None
    opened = False

    # 1) 常驻 chrome：菜单栏 / 面板工具条这类没有独立面板的锚点。
    if spec.widget == "__self__":
        cand: object = main_window
        if spec.object:
            try:
                cand = getattr(main_window, spec.object, None)
                if callable(cand):
                    cand = cand()
            except Exception:
                cand = None
        if isinstance(cand, QWidget) and cand.isVisible():
            target, opened = cand, True

    # 2) 显式面板 widget 属性，且当前可见。
    if target is None and spec.widget and spec.widget != "__self__":
        try:
            cand = getattr(main_window, spec.widget, None)
        except Exception:
            cand = None
        if isinstance(cand, QWidget) and cand.isVisible():
            target, opened = cand, True

    dock = None
    if spec.dock:
        try:
            dock = getattr(main_window, spec.dock, None)
        except Exception:
            dock = None

    # 3) 面板已打开 → 标注面板内的目标控件（objectName 优先，回退面板本体）。
    if target is None and dock is not None:
        try:
            if dock.isVisible() and not dock.isHidden():
                base = dock.widget()
                if spec.object and base is not None:
                    found = base.findChild(QWidget, spec.object)
                    if found is not None and found.isVisible():
                        base = found
                if isinstance(base, QWidget):
                    target, opened = base, True
        except Exception:
            pass

    # 4) 面板没开 → 标注顶栏开关按钮（不打开面板，用户可自行点它）。
    if target is None and dock is not None:
        try:
            btn_of = getattr(main_window, "_dock_toggle_button", None)
            btn = btn_of(dock) if callable(btn_of) else None
            if isinstance(btn, QWidget) and btn.isVisible():
                target, opened = btn, False
        except Exception:
            pass

    # 5) 最后兜底：常驻 chrome 即使当前判定不可见（如菜单未展开）也可标注工具条本身。
    if target is None and spec.widget == "__self__" and spec.object:
        try:
            cand = getattr(main_window, spec.object, None)
            if callable(cand):
                cand = cand()
            if isinstance(cand, QWidget):
                target, opened = cand, True
        except Exception:
            pass

    if target is None:
        return None
    return target, spec, opened
