# -*- coding: utf-8 -*-
"""help_dialogs.py — 通用帮助对话框和按钮工具

为复杂面板提供统一的帮助文档显示机制。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox
from PyQt6.QtCore import Qt
from .effect_workspace_style import recolor_qss
from .qascade_theme import (
    EDITOR_IVORY, STATUS_TEXT, LEGACY_HELP_COLOR_ROLES, LEGACY_HELP_SUCCESS_COLOR,
)


def show_help_dialog(parent, title: str, content: str) -> None:
    """显示帮助对话框（HTML 格式内容）。

    Args:
        parent: 父窗口
        title: 对话框标题
        content: HTML 格式的帮助内容
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumSize(600, 450)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)

    browser = QTextBrowser()
    browser.setHtml(_editor_help_html(content))
    browser.setOpenExternalLinks(False)
    layout.addWidget(browser, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)

    dlg.exec()


def _editor_help_html(content):
    """Local help CSS uses the same light roles; document words/links stay intact."""
    content = recolor_qss(content)
    colors = {old: EDITOR_IVORY[role] for old, role in LEGACY_HELP_COLOR_ROLES.items()}
    colors[LEGACY_HELP_SUCCESS_COLOR] = STATUS_TEXT["success"]
    for old, value in colors.items():
        content = content.replace(old, value)
    return content


# ── 各面板帮助内容定义 ──────────────────────────────────────────────


STUDIO_HELP_HTML = """
<html>
<head>
<style>
body { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
       line-height: 1.6; color: #E6E6E6; background: #1D1D1D; }
h2 { color: #71A8FF; margin-top: 20px; }
h3 { color: #999999; margin-top: 16px; }
code { background: #2D2D2D; padding: 2px 6px; border-radius: 3px;
       color: #56B65B; font-family: Consolas, monospace; }
.warning { background: #3D2D1D; border-left: 4px solid #E36855;
           padding: 12px; margin: 12px 0; }
.tip { background: #1D3D2D; border-left: 4px solid #56B65B;
       padding: 12px; margin: 12px 0; }
ul { margin-left: 20px; }
</style>
</head>
<body>

<h2>Clip Studio 面板帮助</h2>

<h3>应用防抖机制 (300ms)</h3>
<p>
Studio 面板在编辑图形后采用 <code>300ms</code> 防抖延迟应用机制，
避免频繁编辑时产生大量撤销记录或引擎计算负担。
</p>

<div class="tip">
<strong>工作原理：</strong>
<ul>
<li>每次修改图形（拖动卡片、调整连线）触发 300ms 倒计时</li>
<li>300ms 内再次编辑会重置计时器</li>
<li>倒计时结束后才将修改应用到 Clip 并记录撤销历史</li>
<li>切换标签页、切换工程、或执行撤销时立即 flush 挂起的编辑</li>
</ul>
</div>

<h3>何时立即应用</h3>
<p>以下操作会立即 flush 挂起的编辑，无需等待 300ms：</p>
<ul>
<li>切换到其他 Sheet 标签页</li>
<li>切换单页/四宫格视图并聚焦不同画布</li>
<li>增删 Sheet 页</li>
<li>执行撤销/重做操作（由 main_window 触发）</li>
<li>切换到其他工程</li>
</ul>

<div class="warning">
<strong>注意：</strong>
换工程或撤销重灌时会<strong>丢弃</strong>挂起的编辑（防止写错对象），
建议重要编辑后手动切换标签或等待 300ms 确保已应用。
</div>

<h3>状态标签说明</h3>
<ul>
<li><strong>未应用</strong> — 当前 Sheet 无挂起编辑</li>
<li><strong>应用中...</strong> — 300ms 倒计时进行中</li>
<li><strong>N 项未生效（如 平滑）</strong> — 部分字段应用失败（会在下次编辑时重试）</li>
<li><strong>已应用</strong> — 最近一次编辑成功下发到引擎</li>
</ul>

<h3>差量应用机制</h3>
<p>
Studio 采用按 Sheet 的差量应用（ApplyLedger 账本）：
</p>
<ul>
<li>只下发与上次应用<strong>不同</strong>的字段</li>
<li>移动卡片位置 = 零应用（仅改图形，不改 Clip 字段）</li>
<li>删除卡片 = 下发字段复位为默认值</li>
<li>开关灯 = 全局会话级开关，不记入账本</li>
</ul>

</body>
</html>
"""


LIGHT_TRACKING_HELP_HTML = """
<html>
<head>
<style>
body { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
       line-height: 1.6; color: #E6E6E6; background: #1D1D1D; }
h2 { color: #71A8FF; margin-top: 20px; }
h3 { color: #999999; margin-top: 16px; }
code { background: #2D2D2D; padding: 2px 6px; border-radius: 3px;
       color: #56B65B; font-family: Consolas, monospace; }
.warning { background: #3D2D1D; border-left: 4px solid #E36855;
           padding: 12px; margin: 12px 0; }
.tip { background: #1D3D2D; border-left: 4px solid #56B65B;
       padding: 12px; margin: 12px 0; }
ul { margin-left: 20px; }
</style>
</head>
<body>

<h2>灯光跟踪 (Light Tracking) 帮助</h2>

<h3>功能概述</h3>
<p>
灯光跟踪功能让补光灯自动响应泵或阀门的开合状态，
实现喷泉设备与灯光的自动联动效果。
</p>

<h3>配套映射机制 (Companion Mapping)</h3>
<p>
系统复用 TopView 的配套设备映射逻辑：
</p>
<ul>
<li><strong>总设备包</strong> — TopView 中的设备组（页）视为一个整体包</li>
<li><strong>泵/阀 ↔ 灯</strong> — 同一包内的泵/阀与灯建立配对映射</li>
<li><strong>锚点容差</strong> — 使用最近邻算法（容差 25 单位）匹配物理位置</li>
<li><strong>遮罩机制</strong> — 对该设备包内的所有灯对象生成遮罩</li>
</ul>

<div class="tip">
<strong>工作流程：</strong>
<ol>
<li>在 TopView 中将喷泉泵/阀与补光灯放置在同一页</li>
<li>确保灯与对应的泵/阀物理位置接近（≤25 单位）</li>
<li>右键 Clip → 启用灯跟踪，选择跟踪来源（泵数值 / 阀门开合）</li>
<li>播放时系统自动计算泵/阀状态，驱动配对的灯光亮度</li>
</ol>
</div>

<h3>跟踪参数说明</h3>
<ul>
<li><strong>跟踪来源</strong> — 跟踪泵数值（0-255）或阀门开合（开/关）</li>
<li><strong>触发阈值</strong> — 泵值或阀门信号超过此值时触发灯亮（0-255，默认 128）</li>
<li><strong>淡入速度 (Attack)</strong> — 灯从灭到亮的响应时间（秒，默认 0.1s）</li>
<li><strong>淡出速度 (Release)</strong> — 灯从亮到灭的释放时间（秒，默认 0.3s）</li>
<li><strong>固定颜色</strong> — 灯亮起时的颜色（RGB）</li>
<li><strong>最大亮度</strong> — 灯的最大亮度上限（0-255，默认 255）</li>
</ul>

<h3>优先级规则</h3>
<p>泵值的优先级（从高到低）：</p>
<ol>
<li>本 Clip 的帧数据（录制或手动设置的泵值）</li>
<li>其他正在播放的 Clip 的泵帧数据</li>
<li>Programmer 实时控制台的泵值</li>
</ol>

<div class="warning">
<strong>限制：</strong>
<ul>
<li>灯跟踪需要 TopView 中已建立正确的设备配对关系</li>
<li>散台页（设备后缀）和配套页的配对逻辑可能存在断裂</li>
<li>跨 Universe 域的泵与灯需要正确的遮罩和帧查找机制</li>
</ul>
</div>

</body>
</html>
"""


MATRIX_SLIDESHOW_HELP_HTML = """
<html>
<head>
<style>
body { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
       line-height: 1.6; color: #E6E6E6; background: #1D1D1D; }
h2 { color: #71A8FF; margin-top: 20px; }
h3 { color: #999999; margin-top: 16px; }
code { background: #2D2D2D; padding: 2px 6px; border-radius: 3px;
       color: #56B65B; font-family: Consolas, monospace; }
.warning { background: #3D2D1D; border-left: 4px solid #E36855;
           padding: 12px; margin: 12px 0; }
.tip { background: #1D3D2D; border-left: 4px solid #56B65B;
       padding: 12px; margin: 12px 0; }
ul { margin-left: 20px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #444; padding: 8px; text-align: left; }
th { background: #2D2D2D; color: #71A8FF; }
</style>
</head>
<body>

<h2>矩阵幻灯片 (Matrix Slideshow) 帮助</h2>

<h3>工作模式</h3>
<p>面板支持两种工作模式：</p>
<ul>
<li><strong>PPT 模式</strong> — 手动编辑 ON/OFF 帧序列，幻灯片播放</li>
<li><strong>像素效果模式</strong> — 实时像素效果引擎驱动（音频 FFT / 屏幕采样 / 动态效果）</li>
</ul>

<h3>过渡模式详解</h3>
<p>
矩阵幻灯片的过渡逻辑决定了从一帧到下一帧的切换方式：
</p>

<table>
<tr>
<th>过渡模式</th>
<th>说明</th>
<th>适用场景</th>
</tr>
<tr>
<td><code>TRANS_CUT</code></td>
<td>瞬间切换，无过渡效果</td>
<td>节奏感强的音乐编排</td>
</tr>
<tr>
<td><code>TRANS_FADE</code></td>
<td>整体淡入淡出</td>
<td>柔和的颜色渐变</td>
</tr>
<tr>
<td><code>TRANS_SEQ_ON</code></td>
<td>按序列逐个打开设备</td>
<td>波浪式启动效果</td>
</tr>
<tr>
<td><code>TRANS_SEQ_OFF</code></td>
<td>按序列逐个关闭设备</td>
<td>追逐式熄灭效果</td>
</tr>
<tr>
<td><code>TRANS_SEQ_BOTH</code></td>
<td>同时执行 ON 和 OFF 序列</td>
<td>复杂的交织效果</td>
</tr>
<tr>
<td><code>TRANS_WIPE_*</code></td>
<td>擦除式过渡（左右/上下/内外）</td>
<td>空间方向性切换</td>
</tr>
</table>

<div class="tip">
<strong>序列过渡计算：</strong>
<ul>
<li>系统根据设备在 TopView 中的位置和设定的排序方向计算序列</li>
<li><code>SEQ_ON</code> 模式：前一帧的 OFF 设备按序列渐次变 ON</li>
<li><code>SEQ_OFF</code> 模式：前一帧的 ON 设备按序列渐次变 OFF</li>
<li>过渡时间均匀分配到序列中的每个设备</li>
</ul>
</div>

<h3>内置序列</h3>
<p>
面板提供丰富的内置序列模式（BUILTIN_SEQUENCES），包括：
</p>
<ul>
<li><strong>空间类</strong> — 从左到右、从下到上、由内向外、螺旋...</li>
<li><strong>节奏类</strong> — 奇偶交替、随机闪烁、心跳节奏...</li>
<li><strong>形状类</strong> — 圆形扩散、矩形环绕、菱形对角...</li>
<li><strong>物理类</strong> — 粒子扩散、波纹涟漪、引力汇聚...</li>
</ul>

<h3>播放模式</h3>
<ul>
<li><code>PLAY_LOOP</code> — 循环播放（1 → 2 → 3 → 1...）</li>
<li><code>PLAY_ONCE</code> — 播放一次后停止</li>
<li><code>PLAY_PINGPONG</code> — 来回摆动（1 → 2 → 3 → 2 → 1...）</li>
<li><code>PLAY_RANDOM</code> — 随机跳转到任意帧</li>
</ul>

<div class="warning">
<strong>性能提示：</strong>
<ul>
<li>大量设备（>200）的序列过渡可能产生较高计算负担</li>
<li>像素效果模式的音频 FFT 分析需要活跃的音频输入</li>
<li>屏幕采样模式会持续截屏，注意 CPU/GPU 占用</li>
</ul>
</div>

<h3>帧参数说明</h3>
<ul>
<li><strong>停留时间</strong> — 本帧保持的时长（秒）</li>
<li><strong>过渡时间</strong> — 切换到下一帧的过渡时长（秒）</li>
<li><strong>ON 值</strong> — 设备开启时的 DMX 电平（0-255）</li>
<li><strong>OFF 值</strong> — 设备关闭时的 DMX 电平（0-255）</li>
</ul>

</body>
</html>
"""
