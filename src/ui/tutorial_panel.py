# -*- coding: utf-8 -*-
"""内置教程/说明书浏览器（扁平风格皮肤）。

内容两类（都**随包内置**，断网可读）：
  - 本软件说明书：`用户手册.html`（+ 可选 `assets/tutorial/manual/` 资源）

技术要点：
  - 用 **QWebEngineView**（PyQt6-WebEngine）：本项目说明书含大量 SVG/JS 动画与杂志版式，
    只有真正的浏览器引擎能 100% 还原；Qt 其余控件族做不到。
  - **超链接模式**：说明页里的 `data-feature="<id>"` 元素（或 `#feature:<id>` 锚点）
    被注入脚本拦截 → 经 QWebChannel 转到 Qt 信号 `featureTriggered(str)`，
    由 MainWindow 打开对应面板 + 画引出标注（见 feature_callout / tutorial_features）。
  - **离线健壮**：内容目录按 [冻结包 _MEIPASS] → [exe 旁] → [工作区根] 三处查找，
    与 `qascade_theme.app_icon_path()` 同款策略；缺内容时给可读的提示页而不是白屏。

顶栏（工具栏语汇）：目录下拉（软件说明）· 后退 · 前进 · 重新载入 ·
在主窗口打开 · 全部关闭 · 状态文本。语言与皮肤一律取 `qascade_theme` 令牌。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from . import qascade_theme as _th

# 内容根候选（与 app_icon_path 同款三处查找）
_CONTENT_SUBDIR = os.path.join("assets", "tutorial")
_MANUAL_NAME = "用户手册.html"


def _candidate_roots() -> List[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else ""
    meipass = getattr(sys, "_MEIPASS", "")
    ws_root = os.path.abspath(os.path.join(here, "..", ".."))
    out = []
    for base in (meipass, exe_dir, ws_root):
        if base:
            out.append(base)
    return out


def content_path(*parts: str) -> str:
    """在候选根中定位内置内容文件；都不存在返回空串（调用方给提示页）。"""
    for base in _candidate_roots():
        p = os.path.join(base, *parts)
        try:
            if os.path.isfile(p):
                return os.path.abspath(p)
        except Exception:
            continue
    return ""


def _dir_path(*parts: str) -> str:
    for base in _candidate_roots():
        p = os.path.join(base, *parts)
        try:
            if os.path.isdir(p):
                return os.path.abspath(p)
        except Exception:
            continue
    return ""


class _Bridge(QObject):
    """JS → Python 桥：教程页里的功能链接点击 + **阅读进度（章节）上报**。"""

    featureTriggered = pyqtSignal(str)
    chapterSeen = pyqtSignal(str)          # 读者当前浏览到的章节 id（data-feature）

    @pyqtSlot(str)
    def featureClicked(self, feature_id: str) -> None:
        self.featureTriggered.emit(str(feature_id or ""))

    @pyqtSlot(str)
    def chapterChanged(self, chapter_id: str) -> None:
        """页面滚动到新的章节 → 上报（零基础模式据此「随浏览」更新标注）。"""
        self.chapterSeen.emit(str(chapter_id or ""))


# ★ 2026-09-12 关键修复：QtWebChannel 的 JS 侧库（qwebchannel.js）**必须显式加载**，
#   否则 `new QWebChannel(...)` 未定义 → 桥永远不建立 → 说明书里点功能链接**全部静默失效**
#   （此前只在探针里手写过 <script src="qrc:///qtwebchannel/qwebchannel.js">，主程序遗漏）。
#   这里改为读取 Qt 资源 `:/qtwebchannel/qwebchannel.js` 作为常驻 QWebEngineScript 注入。
_QWC_JS_CACHE: Optional[str] = None


def _qwebchannel_js() -> str:
    """读取 Qt 内建 qwebchannel.js 源（失败返回空串，调用方退化为无桥）。"""
    global _QWC_JS_CACHE
    if _QWC_JS_CACHE is None:
        try:
            from PyQt6.QtCore import QFile, QIODevice
            f = QFile(":/qtwebchannel/qwebchannel.js")
            if f.open(QIODevice.OpenModeFlag.ReadOnly):
                _QWC_JS_CACHE = bytes(f.readAll()).decode("utf-8")
                f.close()
            else:
                _QWC_JS_CACHE = ""
        except Exception:
            _QWC_JS_CACHE = ""
    return _QWC_JS_CACHE


# 页面脚本（DocumentReady 注入）：先定义 QWebChannel（qwebchannel.js），再建桥 + 装点击拦截。
_BOOTSTRAP_JS = r"""
(function () {
  function wire() {
    if (typeof QWebChannel === 'undefined') return false;
    if (!window.qt || !window.qt.webChannelTransport) return false;
    if (window.__tutorial_wired) return true;
    window.__tutorial_wired = true;
    new QWebChannel(qt.webChannelTransport, function (ch) {
      var bridge = ch.objects.bridge;
      if (!bridge) return;
      window.__tbridge = bridge;
      document.addEventListener('click', function (ev) {
        var el = ev.target.closest('[data-feature]');
        if (!el) {
          var a = ev.target.closest('a[href^="#feature:"]');
          if (a) el = a;
        }
        if (el) {
          var fid = el.getAttribute('data-feature') ||
                    (el.getAttribute('href') || '').replace('#feature:', '');
          if (fid) { ev.preventDefault(); try { bridge.featureClicked(fid); } catch (e) {} }
        }
      }, true);
      // 标记已注入，便于宿主检查
      document.documentElement.setAttribute('data-tutorial-bridge', '1');
      // ── 阅读进度上报：读者滚到哪个章节 → 通知宿主（零基础模式「随浏览」更新标注）──
      try {
        if (typeof IntersectionObserver !== 'undefined') {
          var io = new IntersectionObserver(function (entries) {
            var best = null, bestTop = 1e9;
            entries.forEach(function (en) {
              if (!en.isIntersecting) return;
              var r = en.boundingClientRect;
              // 取「最靠近视口顶部」的可见章节 = 读者正在读的那章
              var t = Math.abs(r.top);
              if (t < bestTop) { bestTop = t; best = en.target; }
            });
            if (best) {
              var id = best.getAttribute('id') || best.getAttribute('data-feature') || '';
              // 只在「章节真的变了」时上报 —— 支持往回滚（旧实现用 Set 去重，
              // 同一章只报一次，往回滚就不更新标注了，与「随浏览」相悖）。
              if (id && id !== window.__lastChapter) {
                window.__lastChapter = id;
                try { bridge.chapterChanged(id); } catch (e) {}
              }
            }
          }, { rootMargin: '-10% 0px -70% 0px', threshold: [0, 0.2, 0.6] });
          var targets = document.querySelectorAll('section.chapter[id], [data-feature]');
          for (var i = 0; i < targets.length; i++) io.observe(targets[i]);
          window.__tutIO = io;
        }
      } catch (e) {}
    });
  }
  var _n = 0;
  (function retry() {
    if (wire()) return;
    if (_n++ < 40) setTimeout(retry, 100);   // 等 qt.webChannelTransport / QWebChannel 就绪
  })();
})();
"""


class TutorialPanel(QWidget):
    """教程面板（放入 dock）。"""

    featureTriggered = pyqtSignal(str)
    openExternallyRequested = pyqtSignal(str)   # 传给主窗（用系统浏览器打开）
    beginnerTourRequested = pyqtSignal()        # 顶栏「零基础模式」：请求开始巡览
    beginnerTourStopRequested = pyqtSignal()    # 巡览进行中再次点击：请求退出
    chapterSeen = pyqtSignal(str)               # 读者浏览到的章节 id（随浏览标注）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bridge = _Bridge(self)
        self._bridge.featureTriggered.connect(self.featureTriggered)
        self._bridge.chapterSeen.connect(self.chapterSeen)
        self._tour_running = False
        self._build_ui()
        # loadFinished 只在此连一次（见 _load 注释：避免 UniqueConnection 重复连接崩溃）
        try:
            self._view.loadFinished.connect(self._after_load)
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────
    def _btn(self, text: str, tip: str = "", accent: bool = False,
             icon_key: str = "") -> QPushButton:
        """顶栏按钮（工具条语汇：扁平、方角、悬浮提亮）。

        accent=True 用于「零基础模式」这个主动作：用 SELECT_BLUE 底 + FOCUS 边，
        与普通 chrome 按钮拉开层级（主按钮=蓝底，次按钮=灰底）。
        icon_key：tabler 图标名（导航箭头等；禁 ◀▶ 字形）。
        """
        b = QPushButton(text, self)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip or text)
        if icon_key:
            try:
                from .main_icon_library import load_tabler_icon
                _ic = load_tabler_icon(icon_key, 13, _th.TEXT_PRIMARY)
                if not _ic.isNull():
                    b.setIcon(_ic)
            except Exception:
                pass
        if accent:
            b.setObjectName("tutorialAccentBtn")
            b.setStyleSheet(
                f"#tutorialAccentBtn{{background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};"
                f"border:1px solid {_th.FOCUS_BLUE};border-radius:4px;padding:2px 10px;"
                "font-size:11px;font-weight:600;}"
                f"#tutorialAccentBtn:hover{{background:{_th.FOCUS_BLUE};color:{_th.BG_DEEP};}}"
                f"#tutorialAccentBtn:disabled{{color:{_th.TEXT_DISABLED};}}")
        else:
            b.setObjectName("tutorialChromeBtn")
            b.setStyleSheet(
                f"#tutorialChromeBtn{{background:{_th.BG_BUTTON};color:{_th.TEXT_PRIMARY};"
                f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:2px 8px;"
                "font-size:11px;}"
                f"#tutorialChromeBtn:hover{{background:{_th.BG_BUTTON_HOVER};}}"
                f"#tutorialChromeBtn:disabled{{color:{_th.TEXT_DISABLED};}}")
        return b

    def _bar_sep(self, parent: QWidget) -> QWidget:
        """工具条分组竖分线（1px SEP_LINE，工具条分组感）。"""
        s = QWidget(parent)
        s.setFixedWidth(1)
        s.setStyleSheet(f"background:{_th.SEP_LINE};")
        return s

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶栏（工具栏语汇：紧凑、BG_EDITOR 底、1px 分线）
        # ★ 用 id 选择器而非裸声明：裸 `background:` 会**级联到所有子控件**
        #   （同 wingDirectionToolbar 那个 bug），这里必须限定在 bar 自身。
        self._bar = bar = QWidget(self)
        bar.setObjectName("tutorialBar")
        bar.setStyleSheet(
            f"#tutorialBar{{background:{_th.BG_EDITOR};"
            f"border-bottom:1px solid {_th.SEP_LINE};}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 5, 8, 5); h.setSpacing(5)
        self._combo = QComboBox(bar)
        self._combo.setStyleSheet(
            f"QComboBox{{background:{_th.BG_INPUT};color:{_th.TEXT_PRIMARY};"
            f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;padding:2px 8px;"
            "font-size:11px;min-width:160px;}"
            f"QComboBox:hover{{border-color:{_th.FOCUS_BLUE};}}"
            f"QComboBox::drop-down{{border:none;width:18px;}}"
            f"QComboBox QAbstractItemView{{background:{_th.BG_BOX};"
            f"color:{_th.TEXT_PRIMARY};border:1px solid {_th.BORDER_NORMAL};"
            f"selection-background-color:{_th.SELECT_BLUE};outline:none;}}")
        self._combo.currentIndexChanged.connect(self._on_source_changed)
        h.addWidget(self._combo)
        h.addWidget(self._bar_sep(bar))
        self._back = self._btn("后退", "后退", icon_key="arrow-left")
        self._back.clicked.connect(self._go_back)
        self._fwd = self._btn("前进", "前进", icon_key="arrow-right")
        self._fwd.clicked.connect(self._go_forward)
        self._reload = self._btn("重载", "重新载入", icon_key="refresh")
        self._reload.clicked.connect(self._reload_page)
        for b in (self._back, self._fwd, self._reload):
            h.addWidget(b)
        h.addWidget(self._bar_sep(bar))
        self._hint = QLabel("", bar)
        self._hint.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:10px;background:transparent;border:none;")
        h.addWidget(self._hint, 1)
        # 零基础模式：阅读时随浏览在界面上引出注释（主动作 → accent 蓝底）
        self._tour = self._btn(
            "零基础模式",
            "零基础模式：边读说明书边在你现在的界面上引出注释（读到哪标注到哪）；"
            "不打开任何面板，点注释框可打开该功能；随时可退出",
            accent=True)
        self._tour.clicked.connect(self._on_tour_clicked)
        h.addWidget(self._tour)
        self._ext = self._btn("在系统浏览器打开", "用系统默认浏览器打开当前页")
        self._ext.clicked.connect(self._open_external)
        h.addWidget(self._ext)
        lay.addWidget(bar)

        # WebEngine 视图
        self._view = QWebEngineView(self)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view.setStyleSheet(f"background:{_th.BG_ROOT};")
        try:
            s = self._view.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        except Exception:
            pass
        ch = QWebChannel(self._view.page())
        ch.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(ch)
        # ★ 常驻注入：先 qwebchannel.js（定义 QWebChannel），再桥脚本（建桥 + 点击拦截）。
        #   用 QWebEngineScript 保证“每次页面载入都注入”，比在 loadFinished 里
        #   runJavaScript 更可靠（后者在含 CSP/异步渲染的页面上可能错过时机）。
        try:
            script = QWebEngineScript()
            script.setName("tutorial_bridge")
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(_qwebchannel_js() + "\n" + _BOOTSTRAP_JS)
            self._view.page().scripts().insert(script)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("教程桥脚本安装失败", exc_info=True)
        lay.addWidget(self._view, 1)

        self._populate_sources()

    def _sources(self) -> List[Tuple[str, str]]:
        """可选内容源：[(显示名, 本地文件路径)]；缺失的源不列出（避免点开白屏）。"""
        out: List[Tuple[str, str]] = []
        manual = content_path(_MANUAL_NAME)
        if manual:
            out.append(("软件说明书", manual))
        return out

    def _populate_sources(self) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        for name, path in self._sources():
            self._combo.addItem(name, path)
        self._combo.blockSignals(False)
        if self._combo.count() == 0:
            self._hint.setText("未找到内置教程内容")
            self._view.setHtml(self._missing_html())
        else:
            self._combo.setCurrentIndex(0)
            self._load(self._combo.itemData(0))

    # ── 载入 ──────────────────────────────────────────────────────────────
    def _load(self, path: str) -> None:
        if not path:
            self._view.setHtml(self._missing_html()); return
        self._hint.setText(os.path.basename(path))
        # 用 file:// URL 载入（相对资源/图片按内容目录解析）
        # ★ loadFinished 在 __init__ 里只连一次；此处绝不再连——原实现每次 _load 都用
        #   UniqueConnection 连一次，第二次（下拉框切换内容源）会抛
        #   "TypeError: connection is not unique" 导致切换源崩溃。
        self._view.load(QUrl.fromLocalFile(path))

    def _after_load(self, ok: bool) -> None:
        if ok:
            # 兜底：若常驻注入未生效（极少数页面），补一次；有 __tutorial_wired 守卫不会重复建桥。
            try:
                self._view.page().runJavaScript(_qwebchannel_js() + "\n" + _BOOTSTRAP_JS)
            except Exception:
                pass
            self._hint.setText(self._view.title() or self._hint.text())

    def _on_source_changed(self, idx: int) -> None:
        if idx >= 0:
            self._load(self._combo.itemData(idx))

    def _go_back(self) -> None:
        self._view.back()

    def _go_forward(self) -> None:
        self._view.forward()

    def _reload_page(self) -> None:
        self._view.reload()

    def _open_external(self) -> None:
        u = self._view.url().toString()
        if u:
            self.openExternallyRequested.emit(u)

    def _missing_html(self) -> str:
        return (f"<html><body style='background:{_th.BG_ROOT};color:{_th.TEXT_PRIMARY};"
                f"font-family:{_th.FONT_MAIN};padding:24px;'>"
                f"<h3 style='color:{_th.SELECT_ORANGE};'>未找到内置教程内容</h3>"
                f"<p style='color:{_th.TEXT_SECONDARY};'>打包时请确认已把 "
                f"<code>{_MANUAL_NAME}</code> 与 <code>assets/tutorial/</code> "
                f"随包打入（见 tools/build_obfuscated.py 的 datas）。</p></body></html>")

    # ── 对外 ──────────────────────────────────────────────────────────────
    def set_tour_running(self, running: bool) -> None:
        """由 MainWindow 同步「零基础模式」按钮状态（巡览开始/结束）。"""
        self._tour_running = bool(running)
        self._tour.setText("退出零基础模式" if self._tour_running else "零基础模式")

    def _on_tour_clicked(self) -> None:
        if self._tour_running:
            self.beginnerTourStopRequested.emit()
        else:
            self.beginnerTourRequested.emit()

    def scroll_to_feature(self, feature_id: str, spec=None) -> None:
        """可选：把 Web 视图滚到该功能的说明卡片（按 data-feature 锚点）。

        锦上添花，全程 try/except；失败绝不影响巡览主流程。
        """
        try:
            fid = str(feature_id or "").replace("\\", "\\\\").replace("'", "\\'")
            if not fid:
                return
            js = ("(function(){try{"
                  "var e=document.querySelector('[data-feature=\"__FID__\"]');"
                  "if(e){e.scrollIntoView({behavior:'smooth',block:'center'});}"
                  "}catch(err){}})();").replace("__FID__", fid)
            self._view.page().runJavaScript(js)
        except Exception:
            pass

    def open__docs(self, index: int = 1) -> None:
        """切到 R4 文档（若有）。"""
        if 0 <= index < self._combo.count():
            self._combo.setCurrentIndex(index)

    def open_manual(self) -> None:
        self._combo.setCurrentIndex(0)

    def show_remote(self, url: str) -> None:
        """兜底：本地无文档时直接载入官方在线文档。"""
        self._hint.setText(url)
        self._view.load(QUrl(url))


class BeginnerTour(QObject):
    """「零基础模式」——**全注释铺开**（用户定稿：像论文全部脚注当场标明）。

    用户明确否掉"上一步/下一步逐个巡览"。现在点一次「零基础模式」即：
      - 扫描当前界面上**所有可标注功能**（`tutorial_features.resolve_anchor`，
        只锚可见控件、绝不打开面板），把它们的引出标注**一次性全部铺开**；
      - 每条标注带**编号徽标**，与目标旁的同号徽标一对一（论文上标 + 脚注的对应关系）；
      - 每条可单独 ✕ 关掉；顶部控制条只有一个「退出」（不是上一步/下一步）；
      - 点某条框体 = 打开对应功能面板（在标注层内即可跳去操作）；
      - 右侧常驻说明书（教程窗口本身），`scroll_to_feature` 仍可用。

    健壮性：任何一步异常都被吞掉并转为「清空/退出」，绝不抛给主流程。
    """

    finished = pyqtSignal()                       # 结束/退出（含空集合）
    progressed = pyqtSignal(int, int, str)        # (已铺开数, 总数, 说明文案)

    def __init__(self, main_window, overlay, panel=None, parent=None) -> None:
        super().__init__(parent)
        self._mw = main_window
        self._ov = overlay
        self._panel = panel
        self._active = False
        import contextlib
        with contextlib.suppress(Exception):
            self._ov.closed.connect(self._on_overlay_closed)

    @property
    def active(self) -> bool:
        return self._active

    # ── 顺序 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _ordered_specs():
        """按 groups() 分类顺序 + 组内声明顺序返回 FeatureSpec 列表。"""
        from . import tutorial_features as tf
        order = tf.groups()
        buckets = {}
        for s in tf.all_features():
            buckets.setdefault(s.group, []).append(s)
        out = []
        for g in order:
            out.extend(buckets.pop(g, []))
        for lst in buckets.values():        # 兜底：未在 groups() 里出现的分组
            out.extend(lst)
        return out

    def _try_anchor(self, fid: str):
        """单条 resolve_anchor（只解析不打开面板）；异常→None。"""
        try:
            from . import tutorial_features as tf
            return tf.resolve_anchor(self._mw, fid)
        except Exception:
            return None

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def start(self) -> None:
        """**一次性铺开全部注释**（不打开任何面板）。"""
        try:
            entries = []
            for spec in self._ordered_specs():
                got = self._try_anchor(spec.id)
                if got is None:
                    continue
                target, spec_i, _opened = got
                body = spec_i.body
                if not _opened:
                    body = body + "（该面板未打开；点此框可打开）"
                entries.append((target, spec_i.title, body, spec_i.id))
            if not entries:
                self._active = False
                self.finished.emit()
                return
            self._active = True

            def _mk_cb(fid):
                def _cb(_fid=fid):
                    try:
                        from . import tutorial_features as tf
                        tf.resolve(self._mw, _fid)     # 用户主动点 → 打开面板+标注
                    except Exception:
                        pass
                return _cb

            n = self._ov.show_all(
                entries,
                on_click_factory=_mk_cb,
                status=f"零基础模式：已在界面上铺开 {len(entries)} 条注释（逐条 ✕ 或用下方退出）",
                on_exit=self.stop,
                keyword_of=lambda ent: str(ent[3] if len(ent) > 3 else ""),
            )
            if self._panel is not None:
                import contextlib
                with contextlib.suppress(Exception):
                    self._panel.set_tour_running(True)
            self.progressed.emit(int(n), len(entries), f"铺开 {n} 条注释")
        except Exception:
            self.stop()

    def refresh(self) -> None:
        """界面可见性变化后重铺（保持"全注释"语义）。"""
        if self._active:
            self.start()

    def stop(self) -> None:
        """退出：清空全部标注并复位。可重复调用。"""
        was = self._active
        self._active = False
        try:
            if self._ov is not None:
                self._ov.dismiss()
        except Exception:
            pass
        if self._panel is not None:
            import contextlib
            with contextlib.suppress(Exception):
                self._panel.set_tour_running(False)
        if was:
            self.finished.emit()

    def _on_overlay_closed(self) -> None:
        if self._active:
            self._active = False
            self.finished.emit()
