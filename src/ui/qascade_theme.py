# -*- coding: utf-8 -*-
"""
src/ui/qascade_theme.py — 暖米色公共 chrome 与深编辑画布令牌

设计令牌为项目自研暖色体系，

主题令牌契约：
  - 旧 BRAND dict 仅作别名保留（runner_panel 等历史引用）。
  - GLOBAL_QSS 覆盖所有通用控件四态 + 焦点环，页面内联 QSS 只做局部特化。

注意：Qt QSS 中 rgba() 第 4 参是 0-255 整数（约等于 alpha 字节），不是 CSS 的 0-1 浮点；
     全项目禁止使用 #AARRGGBB 形式的 8 位 hex。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont

# ═══════════════════════════════════════════════════════════════════
#  设计令牌（自研暗色体系设计值）
# ═══════════════════════════════════════════════════════════════════

# ---- 画布层级 -----------------------------------------------------
BG_ROOT        = "#1D1D1D"   # 最深底（主窗根）
BG_DEEP        = "#181818"   # 弹层/最深容器
BG_EFFECT_DIM  = "#101010"   # Existing incompatible-effect overlay, preserved.
BG_EDITOR      = "#303030"   # 面板/卡片基底
BG_VIEWPORT    = "#3D3D3D"   # 抬升面板
BG_PANEL       = "#3D3D3D"
BG_PANEL_SUB   = "#363636"
BG_INPUT       = "#1D1D1D"   # 输入框内底
BG_INPUT_EDIT  = "#181818"   # 输入框聚焦态
BG_BOX         = "#2D2D2D"

# ---- 控件面 -------------------------------------------------------
BG_BUTTON          = "#545454"
BG_BUTTON_HOVER    = "#656565"
SELECT_BLUE        = "#4772B3"   # 选中/按下/checked 填充
SELECT_BLUE_HOVER  = "#5480C5"   # 品牌蓝按钮 hover（与 GLOBAL_QSS 既有字面值同源）
SELECT_BLUE_PRESSED = "#35507C"  # 品牌蓝按钮 pressed / checked:disabled
SLIDER_BLUE_HOVER = "#4F7BC0"   # Existing numeric slider hover, preserved.
FOCUS_BLUE         = "#71A8FF"   # 焦点环/高亮数值文本
ACTIVE_ORANGE      = "#FFA028"
SELECT_ORANGE      = "#ED5700"
WARN_ORANGE        = "#AC8737"
ERROR_RED          = "#991616"
SUCCESS_GREEN      = "#188625"
INFO_BLUE          = "#28487D"

# ---- 文本 ---------------------------------------------------------
TEXT_PRIMARY    = "#E6E6E6"
TEXT_SECONDARY  = "#999999"
TEXT_TERTIARY   = "#CCCCCC"
TEXT_DISABLED   = "#8B8B8B"
TEXT_SELECTED_FG = "#FFFFFF"

# ---- 描边/分隔 ------------------------------------------------------
BORDER_NORMAL = "#3D3D3D"
POPUP_BORDER  = "#242424"
SEP_LINE      = "rgba(255,255,255,21)"   # ≈ #FFFFFF15
SEP_LINE_DARK  = "#282828"   # 深底分隔（QToolButton 底同族；范围条带下沿）
SEP_LINE_FAINT = "#242424"   # 更暗一档分隔（工具栏下沿）

# ---- 辅助高亮（仅文字/线条/小面积，避免暗底不可读）-------------------
BRIGHT_SUCCESS = "#56B65B"
BRIGHT_WARN    = "#E6B455"
BRIGHT_ERROR   = "#E36855"
BRIGHT_INFO    = "#6FA8DC"
KEYFRAME_GOLD  = "#B29700"
ANIM_GREEN     = "#38A600"

SCROLLBAR_HANDLE = "#545454"

# ---- 字体 ---------------------------------------------------------
FONT_MAIN = "'Inter','Segoe UI','Microsoft YaHei UI'"
DISPLAY_FONT_FAMILIES = ("Georgia", "Noto Serif CJK SC", "SimSun")
# R185: shared light chrome; only actual output domains use OUTPUT_* resources.
BRAND_ORANGE = "#F27624"
BRAND_ORANGE_INK = "#A54718"
BRAND_ORANGE_TEXT = "#873813"
BRAND_ORANGE_SELECTED = "#EDB77A"
STATUS_SUCCESS_INK = "#1C602B"
STATUS_WARNING_INK = "#704910"
EDITOR_IVORY = {
    "root": "#CFC8BB", "panel": "#ECE7DD", "surface": "#F7F2E9",
    "input": "#D3CDC1", "hover": "#DED7CA", "border": "#786F60",
    "ink": "#2D2B26", "muted": "#534E45", "accent": BRAND_ORANGE_TEXT,
    "selected": BRAND_ORANGE_SELECTED, "focus": BRAND_ORANGE_INK, "disabled": "#777166",
    "danger": "#A44034", "brand": BRAND_ORANGE,
}
# Canvas presentation roles are separate from chrome and real output values.
# Reuse: quiet grid/clear selection; foreground-separated
# material layers. Values are adapted to the approved warm beige/orange system.
CANVAS_REVISION = 1
CANVAS_BG = "#DDD6C9"
CANVAS_BG_TOP = "#E5DFD4"
CANVAS_BG_BOTTOM = "#D8D0C2"
CANVAS_GRID_MINOR = "#C8C0B2"
CANVAS_GRID_MAJOR = "#B5AB9A"
CANVAS_AXIS = "#998C78"
CANVAS_CARD = "#F7F2E9"
CANVAS_CARD_BOTTOM = "#E9E1D3"
CANVAS_HEADER = "#C9BEAC"
CANVAS_BORDER = "#8A7B65"
CANVAS_INK = "#2D2B26"
CANVAS_MUTED = "#615748"
CANVAS_SELECTION = BRAND_ORANGE_INK
CANVAS_SELECTION_FILL = BRAND_ORANGE_SELECTED
CANVAS_OUTPUT_BG = "#302D28"
CANVAS_OUTPUT_IDLE = "#49443B"
CANVAS_OUTPUT_IDLE_BORDER = "#A89B84"
CANVAS_OUTPUT_TEXT = "#DDD2BD"
# Actual output surfaces retain their original neutral values in this canonical
# token owner. output_surface_tokens only re-exports these for older imports.
OUTPUT_BG_ROOT = "#1D1D1D"
OUTPUT_BG_DEEP = "#181818"
OUTPUT_BG_EDITOR = "#303030"
OUTPUT_BG_PANEL = "#3D3D3D"
OUTPUT_BG_PANEL_SUB = "#363636"
OUTPUT_BG_BOX = "#2D2D2D"
OUTPUT_BG_INPUT = "#1D1D1D"
OUTPUT_BG_INPUT_EDIT = "#181818"
OUTPUT_BG_BUTTON = "#545454"
OUTPUT_BORDER_NORMAL = "#3D3D3D"
OUTPUT_TEXT_PRIMARY = "#E6E6E6"
OUTPUT_TEXT_SECONDARY = "#999999"
OUTPUT_TEXT_DISABLED = "#8B8B8B"
OUTPUT_TEXT_SELECTED_FG = "#FFFFFF"

# Exact legacy matching resources; these identify old declarations, they are
# not new rendering colors. Separate roles preserve each existing mapper.
LEGACY_CHROME_COLOR_ROLES = {
    "#1d1d1d": "root", "#181818": "input", "#303030": "panel",
    "#3d3d3d": "border", "#363636": "panel", "#282828": "surface",
    "#2e2e2e": "border", "#333333": "hover", "#242424": "input",
    "#222222": "input", "#454545": "border", "#474747": "hover",
    "#5c5c5c": "border", "#262626": "border", "#545454": "surface",
    "#656565": "hover", "#8a8a8a": "accent", "#4772b3": "selected",
    "#5480c5": "hover", "#35507c": "selected", "#4f7bc0": "hover",
    "#71a8ff": "focus", "#e6e6e6": "ink", "#ffffff": "ink",
    "#d9d9d9": "ink", "#e0e0e0": "ink", "#cccccc": "ink",
    "#999999": "muted", "#989898": "muted", "#b7b7b7": "muted",
    "#838383": "muted", "#8b8b8b": "disabled",
}
LEGACY_EFFECT_TEXT_ROLES = {
    "#e6e6e6": "ink", "#ffffff": "ink", "#d9d9d9": "ink",
    "#e0e0e0": "ink", "#cccccc": "muted", "#999999": "muted",
    "#989898": "muted", "#b7b7b7": "muted", "#838383": "muted",
    "#8b8b8b": "disabled",
}
LEGACY_EFFECT_SELECTED_COLORS = frozenset((
    "#4772b3", "#5480c5", "#35507c", "#4f7bc0", "#71a8ff",
))
LEGACY_EFFECT_NEUTRAL_COLORS = frozenset((
    "#1d1d1d", "#181818", "#303030", "#3d3d3d", "#363636",
    "#2d2d2d", "#545454", "#656565", "#282828", "#242424",
    "#474747", "#333333", "#555555", "#444444", "#1e1e1e",
    "#252526", "#202020", "#262626", "#454545", "#6b6b6b",
))
LEGACY_EFFECT_INPUT_COLORS = frozenset(("#1d1d1d", "#181818", "#242424", "#222222"))
LEGACY_HELP_COLOR_ROLES = {"#3D2D1D": "selected", "#1D3D2D": "panel"}
LEGACY_HELP_SUCCESS_COLOR = "#56B65B"
LEGACY_LINKER_HOVER = "#3a3a5a"
# Legacy neutral aliases now describe ordinary chrome, tables and graph editors.
# Data/status/selection colors above are retained; output paints opt in explicitly.
BG_ROOT = EDITOR_IVORY["root"]
BG_DEEP = BG_EDITOR = BG_PANEL_SUB = BG_BOX = EDITOR_IVORY["panel"]
BG_VIEWPORT = BG_PANEL = BG_BUTTON = EDITOR_IVORY["surface"]
BG_INPUT = BG_INPUT_EDIT = EDITOR_IVORY["input"]
BG_BUTTON_HOVER = EDITOR_IVORY["hover"]
TEXT_PRIMARY = EDITOR_IVORY["ink"]
TEXT_SECONDARY = TEXT_TERTIARY = EDITOR_IVORY["muted"]
TEXT_DISABLED = EDITOR_IVORY["disabled"]
BORDER_NORMAL = POPUP_BORDER = SCROLLBAR_HANDLE = EDITOR_IVORY["border"]
SEP_LINE = SEP_LINE_DARK = SEP_LINE_FAINT = EDITOR_IVORY["border"]
STUDIO_PRESET_COLORS = (
    ("#FFFFFF", "白"), ("#E67E22", "橙"), ("#E4574C", "红"), ("#57B364", "绿"),
    ("#5688D6", "蓝"), ("#E0A93E", "琥珀"), ("#9B6DD6", "紫"), ("#54B9C9", "青"),
)
FONT_MONO = "'Consolas','DejaVu Sans Mono'"
FS_BODY   = 12    # 正文 px
FS_SMALL  = 11    # 次级说明 px（全项目最小允许 10）
MIN_CONTROL_HEIGHT = 22
SPACING_SCALE = (2, 4, 6, 8, 12, 16)
RADIUS = 4            # 统一圆角；滚动条把手取半径一半全圆

# ---- 内置 tabler 图标路径（viewBox 24、stroke=currentColor、MIT）----
_ICON_DIR = Path(__file__).resolve().parent / "icon_assets" / "tabler" / "package" / "icons" / "outline"
ICON_CHEVRON_DOWN = str(_ICON_DIR / "chevron-down.svg")
ICON_CHEVRON_RIGHT = str(_ICON_DIR / "chevron-right.svg")
ICON_CHECK = str(_ICON_DIR / "check.svg")

# ═══════════════════════════════════════════════════════════════════
#  语义常量（跨页面共享，禁止页面自造）
# ═══════════════════════════════════════════════════════════════════

#: DMX 通道类别统一色（此前 patch/programmer/matrix/controller 四套漂移，
#: 本次统一引用此处；键名语义不变——改键名必须同步四个页面）。
CATEGORY_COLORS = {
    "intensity": "#E67E22",
    "dimmer":    "#E67E22",
    "red":       "#E4574C",
    "green":     "#57B364",
    "blue":      "#5688D6",
    "white":     "#E0E0D9",
    "amber":     "#E0A93E",
    "uv":        "#9B6DD6",
    "cyan":      "#54B9C9",
    "pan":       "#E36855",
    "tilt":      "#56B65B",
    "gobo":      "#B29700",
    "prism":     "#9B6DD6",
    "focus":     "#E36855",
    "zoom":      "#54B9C9",
    "fx":        "#7091C9",
    "other":     "#8A8A8A",
}


def app_icon_path() -> str:
    """官方应用图标绝对路径（R52 图标三查修复）。

    候选顺序覆盖三种运行形态：源码态(工作区根)、PyInstaller onefile
    (sys._MEIPASS)、绿色目录(exe 旁)。都不存在则回退至冬版/旧版。
    """
    import os as _os
    import sys as _sys
    here = _os.path.dirname(_os.path.abspath(__file__))
    exe_dir = _os.path.dirname(_sys.executable) if getattr(_sys, "frozen", False) else ""
    meipass = getattr(_sys, "_MEIPASS", "")
    names = ("雾白纪ULTRA图标.png", "雾白纪ULTRA图标.ico", "雾白纪新版图标.png", "雾白纪新版图标.ico", "至冬软件新版图标.ico", "app.ico")
    cands = []
    for _n in names:
        if _n.endswith(".png"):
            cands += [_os.path.join(exe_dir, _n) if exe_dir else "",
                      _os.path.join(meipass, _n) if meipass else "",
                      _os.path.join(here, "..", "..", _n)]
        else:
            cands += [_os.path.join(meipass, _n) if meipass else "",
                      _os.path.join(exe_dir, _n) if exe_dir else "",
                      _os.path.join(here, "..", "..", _n)]
    for c in cands:
        try:
            if c and _os.path.isfile(c):
                return _os.path.abspath(c)
        except Exception:
            continue
    return ""


def app_icon_pixmap(size: int = 64):
    """官方图标 QPixmap（R53 修复：frozen 态禁用 PIL——其导入在 onefile
    环境会 C 级 abort 整个进程；改走 QIcon 延迟解码 + 程序化品牌标兜底，
    保证任何环境下标题栏/任务栏都有可辨识的软件图标）。"""
    from PyQt6.QtGui import QPixmap, QPainter, QBrush, QColor, QPen, QFont, QIcon
    from PyQt6.QtCore import Qt, QRectF, QPointF
    path = app_icon_path()
    if path:
        try:
            if path.lower().endswith(".png"):
                pm = QPixmap(path)          # PNG/libpng 解码路径超稳
                if not pm.isNull():
                    return pm
            else:
                ic = QIcon(path)            # ico 仅源码态使用
                pm = ic.pixmap(size, size)
                if not pm.isNull():
                    return pm
        except Exception:
            pass
    # 程序化兜底品牌标
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#1C2430")))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.18, size * 0.18)
    p.setBrush(QBrush(QColor("#E8C48A")))
    p.drawEllipse(QRectF(size*0.22, size*0.3, size*0.2, size*0.2))
    pen = QPen(QColor("#7FA88F"), max(2.0, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(size*0.62, size*0.22), QPointF(size*0.42, size*0.62))
    p.drawLine(QPointF(size*0.74, size*0.34), QPointF(size*0.56, size*0.68))
    p.end()
    return pm


def app_icon_qicon(size: int = 64):
    """返回官方图标 QIcon（内部走 app_icon_pixmap 的三级回退）。"""
    from PyQt6.QtGui import QIcon
    return QIcon(app_icon_pixmap(size))


def category_color(name: str) -> str:
    """按类别名取统一色（未知类别回退 other）。"""
    key = (name or "").strip().lower()
    return CATEGORY_COLORS.get(key, CATEGORY_COLORS["other"])


#: 状态文本色（绿=正常 黄=警告 红=错误 蓝=信息），替代各页散落的临时 hex。
STATUS_TEXT = {
    "success": STATUS_SUCCESS_INK,
    "warn":    STATUS_WARNING_INK,
    "error":   ERROR_RED,
    "info":    INFO_BLUE,
    "dim":     TEXT_SECONDARY,
}

THEME_ALL = {
    "bg_root": BG_ROOT, "bg_deep": BG_DEEP, "bg_editor": BG_EDITOR,
    "bg_viewport": BG_VIEWPORT, "bg_panel": BG_PANEL, "bg_panel_sub": BG_PANEL_SUB,
    "bg_input": BG_INPUT, "bg_input_edit": BG_INPUT_EDIT, "bg_box": BG_BOX,
    "bg_button": BG_BUTTON, "bg_button_hover": BG_BUTTON_HOVER,
    "select_blue": SELECT_BLUE, "focus_blue": FOCUS_BLUE,
    "select_blue_hover": SELECT_BLUE_HOVER,
    "select_blue_pressed": SELECT_BLUE_PRESSED,
    "active_orange": ACTIVE_ORANGE, "select_orange": SELECT_ORANGE,
    "warn_orange": WARN_ORANGE, "error_red": ERROR_RED, "success_green": SUCCESS_GREEN,
    "info_blue": INFO_BLUE,
    "text_primary": TEXT_PRIMARY, "text_secondary": TEXT_SECONDARY,
    "text_tertiary": TEXT_TERTIARY, "text_disabled": TEXT_DISABLED,
    "text_selected_fg": TEXT_SELECTED_FG,
    "border_normal": BORDER_NORMAL, "popup_border": POPUP_BORDER,
    "scrollbar_handle": SCROLLBAR_HANDLE,
    "bright_success": BRIGHT_SUCCESS, "bright_warn": BRIGHT_WARN,
    "bright_error": BRIGHT_ERROR, "bright_info": BRIGHT_INFO,
}

# ── 旧 BRAND 别名（历史上 route_animator/runner_panel 等在用）──────────
BRAND = {
    "primary":        FOCUS_BLUE,
    "primary_dim":    SELECT_BLUE,
    "accent_green":   BRIGHT_SUCCESS,
    "accent_amber":   BRIGHT_WARN,
    "accent_red":     BRIGHT_ERROR,
    "bg_root":        BG_ROOT,
    "bg_surface":     BG_EDITOR,
    "bg_elevated":    BG_PANEL,
    "bg_input":       BG_INPUT,
    "border_normal":  BORDER_NORMAL,
    "border_focus":   FOCUS_BLUE,
    "text_primary":   TEXT_PRIMARY,
    "text_secondary": TEXT_SECONDARY,
    "text_dim":       TEXT_DISABLED,
    "scrollbar_bg":        BG_ROOT,
    "scrollbar_handle":    SCROLLBAR_HANDLE,
    "scrollbar_handle_hover": BG_BUTTON_HOVER,
}


# ═══════════════════════════════════════════════════════════════════
#  全局 QSS —— 平直风格，控件级四态 + 焦点环全覆盖
# ═══════════════════════════════════════════════════════════════════

GLOBAL_QSS = f"""
/* ========== 基座 ========== */
/* 字体由 apply_qascade_theme() 以 app.setFont 程序化注入。
   禁止在 QSS 写 * {...} 万能选择器——Qt 上会破坏 QScrollBar 等
   子控件的样式接管，出现原生白把手回退。 */
QWidget {{ color: palette(window-text); }}
QMainWindow, QDialog {{ background-color: {EDITOR_IVORY['root']}; }}

QMainWindow::separator {{
    width: 5px; height: 5px; background-color: {EDITOR_IVORY['border']};
}}
/* R54-D：分隔栏可见可抓——底色灰阶浮出根底，hover 亮 select_blue。
   用户点名"时间线右边没有分隔栏"，4→5px 加实底后可抓取。 */
QMainWindow::separator:hover {{ background-color: {EDITOR_IVORY['selected']}; }}

QSplitter::handle {{ background-color: {EDITOR_IVORY['root']}; margin: 1px; border-radius: 2px; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background-color: {EDITOR_IVORY['selected']}; }}

QToolTip {{
    background-color: {EDITOR_IVORY['panel']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    padding: 3px 6px; font-size: {FS_SMALL}px;
}}

/* ========== 按钮 ========== */
QPushButton {{
    background-color: {EDITOR_IVORY['surface']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    min-height: {MIN_CONTROL_HEIGHT}px; padding: 2px 10px;
}}
QPushButton:hover {{ background-color: {EDITOR_IVORY['hover']}; border-color: {EDITOR_IVORY['border']}; }}
QPushButton:pressed {{ background-color: {EDITOR_IVORY['selected']}; border-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}
QPushButton:focus {{ border-color: {EDITOR_IVORY['focus']}; }}
QPushButton:disabled {{ background-color: {EDITOR_IVORY['panel']}; border-color: {EDITOR_IVORY['border']}; color: {EDITOR_IVORY['disabled']}; }}
QPushButton:checked {{ background-color: {EDITOR_IVORY['selected']}; border-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}
QPushButton:checked:hover {{ background-color: {EDITOR_IVORY['hover']}; }}
QPushButton:flat {{ background-color: transparent; border: none; padding: 2px 4px; }}
QPushButton:flat:hover {{ background-color: {EDITOR_IVORY['hover']}; }}
QPushButton#primary {{ background-color: {EDITOR_IVORY['brand']};
    color: {EDITOR_IVORY['ink']}; border-color: {EDITOR_IVORY['focus']}; }}
QPushButton#primary:hover, QPushButton#primary:pressed {{
    background-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}

QToolButton {{
    background-color: {EDITOR_IVORY['surface']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid transparent; border-radius: {RADIUS}px;
    min-height: {MIN_CONTROL_HEIGHT}px; padding: 2px 6px;
}}
QToolButton:hover {{ background-color: {EDITOR_IVORY['hover']}; }}
QToolButton:pressed, QToolButton:checked {{ background-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}
QToolButton:focus {{ border-color: {EDITOR_IVORY['focus']}; }}
QToolButton:disabled {{ color: {EDITOR_IVORY['disabled']}; }}
QToolButton[popupMode="1"], QToolButton[popupMode="2"] {{ padding-right: 14px; }}

/* ========== 输入 ========== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {EDITOR_IVORY['input']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    min-height: {MIN_CONTROL_HEIGHT}px; padding: 1px 6px;
    selection-background-color: {EDITOR_IVORY['focus']}; selection-color: {EDITOR_IVORY['panel']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    background-color: {EDITOR_IVORY['input']}; border-color: {EDITOR_IVORY['focus']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {EDITOR_IVORY['input']}; color: {EDITOR_IVORY['disabled']};
}}
/* 步进钮隐藏（2026-08-27 用户二轮点名"长方块箭头"根治）：
   数字框范式＝无上下钮，调值靠拖拽/滚轮/方向键/双击键入。
   隐藏后按钮区宽 0，输入框右内缩相应减小。 */
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 0; height: 0;
    subcontrol-position: right center;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0; height: 0; border: none; background: transparent;
}}

QComboBox {{
    background-color: {EDITOR_IVORY['surface']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    min-height: {MIN_CONTROL_HEIGHT}px; padding: 1px 8px;
}}
QComboBox:hover {{ background-color: {EDITOR_IVORY['hover']}; }}
QComboBox:focus {{ border-color: {EDITOR_IVORY['focus']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    /* R53 实测定案（对照实验）：全局 QSS 中 image:url() 对 QScrollBar
       **无**连累（此前白滚动条真凶是 * 万能选择器，已移除）。
       用 tabler chevron-down（crisp 矢量，DPR 锐利）。 */
    image: url("{ICON_CHEVRON_DOWN.replace(chr(92), chr(47))}");
    width: 13px; height: 13px;
}}
QComboBox QAbstractItemView {{
    background-color: {EDITOR_IVORY['panel']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    selection-background-color: {EDITOR_IVORY['selected']}; selection-color: {EDITOR_IVORY['ink']};
    outline: none; padding: 2px;
}}
QComboBox QAbstractItemView::item {{ min-height: 20px; border-radius: 3px; padding: 0 6px; }}

/* ========== Tab ========== */
QTabWidget::pane {{
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    background-color: {EDITOR_IVORY['root']}; top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background-color: {EDITOR_IVORY['root']}; color: {EDITOR_IVORY['muted']};
    border-radius: {RADIUS}px {RADIUS}px 0 0;
    padding: 4px 12px; min-width: 40px; min-height: 18px;
}}
QTabBar::tab:selected {{ background-color: {EDITOR_IVORY['panel']}; color: {EDITOR_IVORY['ink']}; }}
QTabBar::tab:hover:!selected {{ color: {EDITOR_IVORY['ink']}; }}
QTabBar::tab:disabled {{ color: {EDITOR_IVORY['disabled']}; }}

/* ========== 菜单/工具栏 ========== */
QMenuBar {{ background-color: {EDITOR_IVORY['root']}; color: {EDITOR_IVORY['ink']}; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: {RADIUS}px; }}
QMenuBar::item:selected {{ background-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}

QMenu {{
    /* 2026-08-27 用户三轮点名"还是黑"：QSS 圆角菜单在 Windows 会在圆角外
       透出黑色窗口本底。直角 + {EDITOR_IVORY['panel']} 灰底 = 全区域可见的耐看灰。 */
    background-color: {EDITOR_IVORY['panel']}; color: {EDITOR_IVORY['ink']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: 0px; padding: 2px;
}}
QMenu::item {{ padding: 4px 24px 4px 8px; border-radius: 3px; background-color: transparent; }}
QMenu::item:selected {{ background-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']}; }}
QMenu::item:disabled {{ color: {EDITOR_IVORY['disabled']}; }}
QMenu::separator {{ height: 1px; background-color: {EDITOR_IVORY['border']}; margin: 3px 4px; }}

QToolBar {{
    background-color: {EDITOR_IVORY['surface']}; border-bottom: 1px solid {EDITOR_IVORY['border']};
    spacing: 4px; padding: 2px 6px;
}}
QToolBar::separator {{ width: 1px; background-color: {EDITOR_IVORY['border']}; margin: 4px 6px; }}

/* ========== 条目视图 ========== */
QListWidget, QTreeWidget, QTreeView, QTableWidget, QTableView {{
    background-color: {EDITOR_IVORY['panel']}; alternate-background-color: {EDITOR_IVORY['input']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    outline: none; color: {EDITOR_IVORY['ink']};
    selection-background-color: {EDITOR_IVORY['selected']};
}}
QListWidget::item, QTreeWidget::item, QTreeView::item {{
    min-height: 20px; padding: 0 4px; border-radius: 3px; color: {EDITOR_IVORY['ink']};
}}
QListWidget::item:hover, QTreeWidget::item:hover, QTreeView::item:hover {{ background-color: {EDITOR_IVORY['surface']}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {EDITOR_IVORY['selected']}; color: {EDITOR_IVORY['ink']};
}}
QTableWidget, QTableView {{ gridline-color: {EDITOR_IVORY['border']}; selection-background-color: {EDITOR_IVORY['selected']}; }}
QTableCornerButton::section {{ background-color: {EDITOR_IVORY['surface']}; border: none; }}
QHeaderView::section {{
    background-color: {EDITOR_IVORY['surface']}; color: {EDITOR_IVORY['muted']};
    border: none; border-right: 1px solid {EDITOR_IVORY['border']};
    padding: 3px 8px; font-weight: normal;
}}
QHeaderView::section:hover {{ background-color: {EDITOR_IVORY['hover']}; color: {EDITOR_IVORY['ink']}; }}

/* ========== 进度/滑杆 ========== */
QProgressBar {{
    background-color: {EDITOR_IVORY['input']}; border: none; border-radius: {RADIUS}px;
    text-align: center; color: {EDITOR_IVORY['ink']}; min-height: 12px;
}}
QProgressBar::chunk {{ background-color: {EDITOR_IVORY['selected']}; border-radius: {RADIUS}px; }}

QSlider::groove:horizontal {{ background-color: {EDITOR_IVORY['input']}; height: 6px; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background-color: {EDITOR_IVORY['selected']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background-color: {EDITOR_IVORY['accent']}; width: 10px; margin: -4px 0;
    border-radius: 3px;
}}
QSlider::handle:horizontal:hover {{ background-color: {EDITOR_IVORY['ink']}; }}
QSlider::groove:vertical {{ background-color: {EDITOR_IVORY['input']}; width: 6px; border-radius: 3px; }}
QSlider::sub-page:vertical {{ background-color: {EDITOR_IVORY['selected']}; border-radius: 3px; }}
QSlider::handle:vertical {{ background-color: {EDITOR_IVORY['accent']}; height: 10px; margin: 0 -4px; border-radius: 3px; }}
QSlider::handle:vertical:hover {{ background-color: {EDITOR_IVORY['ink']}; }}

QCheckBox, QRadioButton {{ color: {EDITOR_IVORY['ink']}; spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid {EDITOR_IVORY['border']}; background-color: {EDITOR_IVORY['surface']};
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ background-color: {EDITOR_IVORY['hover']}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {EDITOR_IVORY['selected']}; border-color: {EDITOR_IVORY['selected']};
}}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background-color: {EDITOR_IVORY['selected']}; border-color: {EDITOR_IVORY['selected']};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {EDITOR_IVORY['input']}; border-color: {EDITOR_IVORY['border']};
}}

/* ========== 分组框 ========== */
QGroupBox {{
    color: {EDITOR_IVORY['ink']}; background-color: {EDITOR_IVORY['panel']};
    border: 1px solid {EDITOR_IVORY['border']}; border-radius: {RADIUS}px;
    margin-top: 10px; padding: 14px 8px 8px 8px;
    font-weight: normal;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; top: 0px;
    padding: 0 4px; color: {EDITOR_IVORY['muted']}; font-size: {FS_SMALL}px;
}}

/* ========== 滚动条 ========== */
/* 注意：此处背景禁止写 transparent——Qt 6.11 上 transparent 会让整个
   QScrollBar 回退原生渲染（白色把手+箭头），必须用实色/rgba。 */
QScrollBar:vertical {{ background: {EDITOR_IVORY['root']}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {EDITOR_IVORY['border']}; border-radius: 6px; min-height: 24px; margin: 1px 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {EDITOR_IVORY['hover']}; }}
QScrollBar:horizontal {{ background: {EDITOR_IVORY['root']}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {EDITOR_IVORY['border']}; border-radius: 6px; min-width: 24px; margin: 2px 1px;
}}
QScrollBar::handle:horizontal:hover {{ background: {EDITOR_IVORY['hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: none; border: none; height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; }}

QAbstractScrollArea::corner {{ background: transparent; }}

/* ========== Dock/状态栏 ========== */
QDockWidget {{
    color: {EDITOR_IVORY['ink']}; titlebar-close-icon: none; titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background-color: {EDITOR_IVORY['panel']}; padding: 5px 10px;
    border-bottom: 1px solid {EDITOR_IVORY['border']};
}}
QStatusBar {{
    background-color: {EDITOR_IVORY['panel']}; color: {EDITOR_IVORY['muted']};
    border-top: 1px solid {EDITOR_IVORY['border']};
}}
QStatusBar::item {{ border: none; }}
"""
from .editor_materials import apply_editor_palette  # noqa: E402
# Explicit chrome roles above; deep owners keep their inherited palette.

# ═══════════════════════════════════════════════════════════════════
#  绘制字体缓存（R183 横向性能批）
# ═══════════════════════════════════════════════════════════════════
# paintEvent/渲染路径逐帧 `QFont()` 构造（字族解析 + 度量查找）在高频面板
# 每帧可达数十次。照 audio_mod_draw/draw_canvas_paint._ICON_CACHE 先例做
# 模块级缓存，键=(families, pixelSize, bold, pointSize×100)。
# 返回**共享实例**：调用方只允许 `p.setFont(f)`，不得再 set* 变异（会污染缓存）。

UI_FONT_FAMILIES = ("Inter", "Segoe UI", "Microsoft YaHei UI")
MONO_FONT_FAMILIES = ("Consolas", "DejaVu Sans Mono", "monospace")

_FONT_CACHE: dict = {}


def cached_font(px: int = 0, bold: bool = False,
                families: tuple = UI_FONT_FAMILIES,
                point_size: float = 0.0) -> QFont:
    """取缓存的 QFont：px>0 用 setPixelSize，否则用 point_size(setPointSizeF)。

    families 先归一 tuple 再入键：list/tuple 等价入参必须命中同一实例
    （2026-09-30 单元测试实锤：list 直传曾 TypeError: unhashable type）。
    """
    fam = tuple(families)
    key = (fam, int(px), bool(bold), round(float(point_size) * 100))
    f = _FONT_CACHE.get(key)
    if f is None:
        f = QFont()
        f.setFamilies(list(fam))
        if px > 0:
            f.setPixelSize(int(px))
        else:
            f.setPointSizeF(float(point_size))
        f.setBold(bool(bold))
        _FONT_CACHE[key] = f
    return f


# ═══════════════════════════════════════════════════════════════════
#  公开 API
# ═══════════════════════════════════════════════════════════════════

def apply_qascade_theme(app) -> None:
    """Apply warm shared chrome; legacy BG_* remain deep canvas/data roles."""
    apply_editor_palette(app)
    app.setStyleSheet(GLOBAL_QSS)
    # 字体走程序化继承链（Inter→Segoe UI→雅黑），不进 QSS
    from PyQt6.QtGui import QFont
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "Microsoft YaHei UI"])
    f.setPointSize(9)          # ≈12px @96dpi，与 FS_BODY 对应
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(f)


def themed_button_stylesheet(
    bg: str = "",
    fg: str = "",
    hover_bg: str = "",
    border: str = "",
    radius: int = RADIUS,
    font_size: int = FS_BODY,
    padding: str = "2px 12px",
) -> str:
    """生成按钮局部 QSS。空参数用暖 editor 角色，显式业务色保留。"""
    bg = bg or EDITOR_IVORY["surface"]
    fg = fg or EDITOR_IVORY["ink"]
    hover_bg = hover_bg or EDITOR_IVORY["hover"]
    border = border or EDITOR_IVORY["border"]
    return (
        "QPushButton {"
        f" background-color:{bg}; color:{fg};"
        f" border:1px solid {border}; border-radius:{radius}px;"
        f" font-size:{font_size}px; padding:{padding}; min-height:{MIN_CONTROL_HEIGHT}px;"
        "}"
        "QPushButton:hover {"
        f" background-color:{hover_bg}; border-color:{EDITOR_IVORY['border']};"
        "}"
        "QPushButton:pressed {"
        f" background-color:{EDITOR_IVORY['selected']}; color:{EDITOR_IVORY['ink']};"
        "}"
        "QPushButton:disabled {"
        f" color:{EDITOR_IVORY['disabled']}; background-color:{EDITOR_IVORY['panel']};"
        "}"
    )
