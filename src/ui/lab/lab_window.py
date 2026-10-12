"""实验室独立窗口 —— 三工具（ML 自动编曲 / 无人机编舞 / 工具箱）+ LLM 配置。

设计（照 RouteRunnerPanel 的独立窗口先例）：
  * QWidget(parent, Qt.WindowType.Window) 单例；closed 信号；set_context(...) 注入依赖。
  * 三个工具各占一个子标签；模块未分发时该标签自动隐藏（标准版因此无实验室痕迹）。
  * ML 编曲页：左侧控制区（音频/目标设备/生成/播放/输出开关/LLM 配置），右侧 3D 预览
    （QWebEngineView 承载模块自带 stage3d）。
  * 逐帧通道值由**主进程内** numpy 渲染（mlstage.effects_py 是 numpy-only），
    故预览与真实输出用同一份 field —— "预览即现场"天然成立。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import qascade_theme as _th
from . import module_service as MS
from .lab_output import LabOutput, LabPlayback

ARRANGER_KEY = "arranger"


def _lab_root() -> str:
    """定位实验室模块根（用于 sys.path 注入 mlstage）。"""
    return MS.find_tool_dir(ARRANGER_KEY)


class LabWindow(QWidget):
    """实验室主窗口（独立顶层窗口，非模态单例）。"""

    closed = pyqtSignal()
    _gen_done = pyqtSignal(bool, str, str)        # (成功, 引擎/错误, program JSON)
    _llm_test_done = pyqtSignal(bool, str)        # (已启动, probe JSON)
    _lan_found = pyqtSignal(list)                 # 局域网扫描结果

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("实验室 — ML 自动编曲 · 无人机编舞 · 工具箱")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 660)

        self._pm = None                    # ProjectManager
        self._topview = None               # TopViewPanel
        self._nm = None                    # NetworkManager
        self._lab = MS.LabModule(self)

        self._spec = None                  # LayoutSpec
        self._program = None               # 当前编排 program
        self._render = None                # LabPlayback
        self._out: Optional[LabOutput] = None
        self._audio_path = ""
        self._tabs: dict = {}

        self._build_ui()
        self._gen_done.connect(self._on_gen_done)
        self._llm_test_done.connect(self._on_llm_test_done)
        self._lan_found.connect(self._on_lan_found)
        self._lab.log_line.connect(self._on_log)
        self._lab.ready.connect(self._on_service_ready)

    # ── 上下文注入 ──────────────────────────────────────────────
    def set_context(self, project_manager=None, topview=None, network_manager=None) -> None:
        self._pm = project_manager
        self._topview = topview
        self._nm = network_manager
        if self._out is None:
            self._out = LabOutput(network_manager, self)
            # ★ 幂等连接：`self._out` 若被重建（重复 set_context），UniqueConnection
            #   保证同一 (信号, 槽) 只保留一份，不会累积重复回调。
            U = Qt.ConnectionType.UniqueConnection
            self._out.frame_changed.connect(self._on_frame, U)
            self._out.state_changed.connect(self._on_play_state, U)
        else:
            self._out.set_network_manager(network_manager)
        self._refresh_layout(force=True)

    # ── UI ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs, 1)

        self._build_arranger_tab()
        self._build_droneforge_tab()
        self._build_toolbox_tab()

        # 未分发的工具标签隐藏（标准版 = 三者全无 → 此窗口不会被打开）
        if not MS.tool_available("droneforge"):
            idx = self._tabs.indexOf(self._tabs.widget(1))
            self._tabs.setTabVisible(idx, False)
        if not MS.tool_available("toolbox"):
            idx = self._tabs.indexOf(self._tabs.widget(2))
            self._tabs.setTabVisible(idx, False)

        # 状态行
        bar = QHBoxLayout()
        self._status = QLabel("就绪")
        self._status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        bar.addWidget(self._status, 1)
        root.addLayout(bar)

    # ── ML 编曲页 ────────────────────────────────────────────────
    def _build_arranger_tab(self) -> None:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        split = QSplitter(Qt.Orientation.Horizontal, page)

        # 左：控制区（可滚动）
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(330)
        left_scroll.setMaximumWidth(460)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.setSpacing(8)

        # 1) 音频（独立导入——不与时间线联动）
        gb_audio = QGroupBox("① 音频（独立导入，不与时间线联动）")
        fa = QVBoxLayout(gb_audio)
        row = QHBoxLayout()
        self._audio_lbl = QLineEdit()
        self._audio_lbl.setReadOnly(True)
        self._audio_lbl.setPlaceholderText("未选择音频")
        btn_pick = QPushButton("选择音频…")
        btn_pick.clicked.connect(self._pick_audio)
        row.addWidget(self._audio_lbl, 1)
        row.addWidget(btn_pick)
        fa.addLayout(row)
        self._btn_analyze = QPushButton("② 分析并生成编排")
        self._btn_analyze.clicked.connect(self._on_generate)
        fa.addWidget(self._btn_analyze)
        lv.addWidget(gb_audio)

        # 2) 目标与播放
        gb_play = QGroupBox("③ 目标设备 / 输出")
        fp = QFormLayout(gb_play)
        self._device_cb = QComboBox()
        self._device_cb.addItem("LED 灯阵（RGB+亮度）", "led_matrix")
        self._device_cb.addItem("喷泉跑泉（RGB+泵）", "fountain_matrix")
        fp.addRow("编曲目标：", self._device_cb)

        self._layout_lbl = QLabel("—")
        self._layout_lbl.setWordWrap(True)
        fp.addRow("真实布局：", self._layout_lbl)

        self._chk_output = QCheckBox("输出到真实设备（写入 ml_lab 层）")
        self._chk_output.setChecked(True)
        self._chk_output.toggled.connect(self._on_output_toggle)
        fp.addRow(self._chk_output)

        self._chk_loop = QCheckBox("循环播放")
        fp.addRow(self._chk_loop)
        lv.addWidget(gb_play)

        # 3) 传输
        gb_tr = QGroupBox("④ 播放")
        ft = QVBoxLayout(gb_tr)
        trow = QHBoxLayout()
        self._btn_play = QPushButton("播放")
        self._btn_play.clicked.connect(self._on_play)
        self._btn_stop = QPushButton("■ 停止")
        self._btn_stop.clicked.connect(self._on_stop)
        trow.addWidget(self._btn_play)
        trow.addWidget(self._btn_stop)
        ft.addLayout(trow)
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.sliderReleased.connect(self._on_seek)
        ft.addWidget(self._seek)
        self._time_lbl = QLabel("0.0 / 0.0 s")
        self._time_lbl.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        ft.addWidget(self._time_lbl)
        lv.addWidget(gb_tr)

        # 4) LLM 配置（含测试按钮）
        lv.addWidget(self._build_llm_group())
        lv.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        # 右：3D 预览
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self._preview_host = QWidget()
        self._preview_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_host.setStyleSheet("background:#0a0b10;")
        rv.addWidget(self._preview_host, 1)
        self._preview = None            # 懒建 QWebEngineView
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        lay.addWidget(split)
        self._tabs.addTab(page, "ML 自动编曲")

    def _build_llm_group(self) -> QGroupBox:
        gb = QGroupBox("⑤ LLM 提供方（不配则自动降级 v9 兜底引擎）")
        outer = QVBoxLayout(gb)
        f = QFormLayout()
        cfg = MS.load_llm_settings()

        # ── 一键预设：云端 DeepSeek / 本地 llama.cpp ──────────────
        prow = QHBoxLayout()
        prow.addWidget(QLabel("快速切换："))
        for key in ("cloud", "local"):
            spec = MS.LLM_PRESETS[key]
            b = QPushButton(spec["label"])
            b.setToolTip("一键套用该预设（端点/协议/模型/超时），套用后按【保存】生效")
            b.clicked.connect(lambda _=False, k=key: self._apply_llm_preset(k))
            prow.addWidget(b)
        prow.addStretch(1)
        outer.addLayout(prow)

        # ── 局域网发现：开发机 ↔ 跑模型的机器（WiFi 同一网段）────────
        lrow = QHBoxLayout()
        self._btn_lan = QPushButton("扫描局域网 LLM")
        self._btn_lan.setToolTip("扫同网段常见 LLM 端口（llama.cpp/Ollama 等），"
                                 "自动填入可用端点——省去手查 IP")
        self._btn_lan.clicked.connect(self._on_lan_scan)
        lrow.addWidget(self._btn_lan)
        self._lan_cb = QComboBox()
        self._lan_cb.setMinimumWidth(200)
        self._lan_cb.addItem("（未扫描）", "")
        self._lan_cb.currentIndexChanged.connect(self._on_lan_pick)
        lrow.addWidget(self._lan_cb, 1)
        outer.addLayout(lrow)
        outer.addLayout(f)

        self._llm_url = QLineEdit(cfg["url"])
        self._llm_url.setPlaceholderText("http://127.0.0.1:8080/v1/chat/completions")
        f.addRow("端点 URL：", self._llm_url)

        self._llm_fmt = QComboBox()
        for label, val in (
                ("OpenAI Responses API（DeepSeek 默认）", "responses"),
                ("OpenAI 兼容（本地 llama.cpp 等）", "openai"),
                ("Anthropic Messages", "anthropic"),
                ("按 URL 自动判定", "")):
            self._llm_fmt.addItem(label, val)
        idx = max(0, self._llm_fmt.findData(cfg["format"]))
        self._llm_fmt.setCurrentIndex(idx)
        f.addRow("协议：", self._llm_fmt)

        self._llm_key = QLineEdit(cfg["key"])
        self._llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_key.setPlaceholderText("留空 = 本地 llama.cpp 免鉴权")
        f.addRow("API Key：", self._llm_key)

        self._llm_model = QLineEdit(cfg["model"])
        f.addRow("模型名：", self._llm_model)

        self._llm_timeout = QSpinBox()
        self._llm_timeout.setRange(5, 7200)
        self._llm_timeout.setValue(int(float(cfg["timeout"] or 1800)))
        f.addRow("超时(秒)：", self._llm_timeout)

        # 思考开关：本地 llama 必关（实测 4.2s→0.7s；且避免预算被推理吃光）
        self._llm_think = QComboBox()
        for label, val in (("关（推荐：本地编排）", "off"),
                           ("开（保留推理）", "on"),
                           ("服务端默认", "auto")):
            self._llm_think.addItem(label, val)
        self._llm_think.setCurrentIndex(max(0, self._llm_think.findData(cfg.get("thinking", "off"))))
        self._llm_think.setToolTip(
            "本地 Qwen3.6 默认开思考，会先烧大量输出预算做推理：\n"
            "实测同一 prompt 关思考 0.7s / 开思考 4.2s，长编排差距更大。\n"
            "本地建议关；云端由厂商模型决定（选“服务端默认”即可）。")
        f.addRow("思考模式：", self._llm_think)

        row = QHBoxLayout()
        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_llm_save)
        self._btn_test = QPushButton("测试连接")
        self._btn_test.clicked.connect(self._on_llm_test)
        self._btn_def = QPushButton("恢复出厂默认")
        self._btn_def.clicked.connect(self._on_llm_default)
        row.addWidget(self._btn_save)
        row.addWidget(self._btn_test)
        row.addWidget(self._btn_def)
        f.addRow(row)

        self._llm_status = QLabel("未测试")
        self._llm_status.setWordWrap(True)
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        f.addRow(self._llm_status)
        return gb

    # ── 无人机 / 工具箱页 ───────────────────────────────────────
    def _build_droneforge_tab(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        info = QLabel("DroneForge 无人机动画工坊：制作无人机编舞并导出 .ddsf（4 可直接导入）。\n"
                      "以独立进程运行（后端 127.0.0.1），在下方内嵌窗口中编辑。")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        lay.addWidget(info)
        row = QHBoxLayout()
        btn = QPushButton("启动并打开")
        btn.clicked.connect(self._on_launch_droneforge)
        row.addWidget(btn)
        self._df_status = QLabel("未启动")
        self._df_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        row.addWidget(self._df_status, 1)
        lay.addLayout(row)
        self._df_host = QWidget()
        self._df_host.setStyleSheet("background:#0a0b10;")
        lay.addWidget(self._df_host, 1)
        self._df_view = None
        self._tabs.addTab(page, "无人机编舞 (DroneForge)")

    def _build_toolbox_tab(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        info = QLabel("工具箱：缓存清理(.cfo 死 Scene) / 粒子与设备极限 / 完整性体检 / 日志 / 设置中心 / 防卡助手。\n"
                      "为独立 GUI 程序（自建窗口），点击下方按钮拉起——宿主不承载其界面。")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        lay.addWidget(info)
        lay.addWidget(QLabel("提示：它自带完整窗口，请在其窗口内操作；关闭主程序不会自动关它。"))
        row = QHBoxLayout()
        btn = QPushButton("启动 工具箱")
        btn.clicked.connect(self._on_launch_toolbox)
        row.addWidget(btn)
        self._tb_status = QLabel("未启动")
        self._tb_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        row.addWidget(self._tb_status, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._tabs.addTab(page, "工具箱")

    # ── 布局（真实 TopView 坐标）────────────────────────────────
    def _refresh_layout(self, force: bool = False) -> None:
        try:
            from .topview_layout import read_layout
            proj = getattr(self._pm, "project", None) if self._pm else None
            self._spec = read_layout(topview=self._topview, project=proj)
        except Exception as e:
            self._spec = None
            self._status.setText(f"读取布局失败：{e}")
            return
        if self._spec is None or not self._spec.count:
            self._layout_lbl.setText("未找到可输出的设备（需先配接 LED/喷泉设备）")
        else:
            pumps = sum(1 for f in self._spec.fixtures if f.has_pump)
            self._layout_lbl.setText(
                f"{self._spec.count} 台（泵类 {pumps}）· 来源 {self._spec.source} · "
                f"网格 {self._spec.gx}×{self._spec.gy}")
        if self._preview is not None:
            self._push_layout()

    def _push_layout(self) -> None:
        if self._preview is None or self._spec is None:
            return
        try:
            js = self._bridge_setup_layout(self._spec.to_preview_json())
            self._preview.page().runJavaScript(js)
        except Exception:
            pass

    def _bridge_setup_layout(self, payload: dict) -> str:
        return f"window.__labSetLayout({json.dumps(payload, ensure_ascii=False)});"

    # ── 预览（懒建 WebEngine）───────────────────────────────────
    def _ensure_preview(self) -> bool:
        if self._preview is not None:
            return True
        root = _lab_root()
        if not root:
            self._status.setText("实验室模块未分发，3D 预览不可用")
            return False
        try:
            from PyQt6.QtWebChannel import QWebChannel
            from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except Exception as e:
            self._status.setText(f"QtWebEngine 不可用：{e}")
            return False

        from .lab_bridge import LabPreviewBridge
        view = QWebEngineView(self._preview_host)
        host_lay = QVBoxLayout(self._preview_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.addWidget(view)
        view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        view.settings().setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)

        self._bridge = LabPreviewBridge(self)
        ch = QWebChannel(view.page())
        ch.registerObject("labBridge", self._bridge)
        view.page().setWebChannel(ch)

        # 常驻注入 qwebchannel.js（同 tutorial_panel 的做法）
        try:
            from PyQt6.QtCore import QFile, QIODevice
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "_qwebchannel.js")
            js = ""
            # PyQt 自带 qtwebchannel 资源；用 QFile 从 :/qtwebchannel/qwebchannel.js 读
            f = QFile(":/qtwebchannel/qwebchannel.js")
            if f.open(QIODevice.OpenModeFlag.ReadOnly):
                js = bytes(f.readAll()).decode("utf-8", "replace")
                f.close()
            if js:
                s = QWebEngineScript()
                s.setName("qwebchannel")
                s.setSourceCode(js)
                s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
                s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
                view.page().scripts().insert(s)
        except Exception:
            pass

        view.page().setBackgroundColor(Qt.GlobalColor.black)
        self._preview = view
        # 页面从本地 HTTP 加载（避开 file:// 的 module/wasm 限制）
        url = f"{self._lab.base_url}/js/stage3d.js"   # 仅用于确保服务可达
        preview_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview.html")
        view.load(QUrl.fromLocalFile(preview_file))
        QTimer.singleShot(1500, self._push_layout)
        return True

    # ── 音频 ────────────────────────────────────────────────────
    def _pick_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择音频（独立导入，不影响时间线）", "",
            "音频 (*.mp3 *.wav *.flac *.m4a *.ogg);;所有文件 (*.*)")
        if not path:
            return
        self._audio_path = path
        self._audio_lbl.setText(os.path.basename(path))
        self._status.setText(f"已选择音频：{os.path.basename(path)}")

    # ── 生成编排 ────────────────────────────────────────────────
    def _on_generate(self) -> None:
        if not self._audio_path:
            QMessageBox.information(self, "实验室", "请先选择音频文件。")
            return
        if not MS.tool_available(ARRANGER_KEY):
            QMessageBox.warning(self, "实验室", "ML 编曲模块未随本发行版分发。")
            return
        device = self._device_cb.currentData()
        self._btn_analyze.setEnabled(False)
        self._status.setText("正在启动/连接实验室服务…")
        if not self._lab.running:
            self._lab.start(MS.load_llm_settings())
        self._pending_device = device
        self._prog_thread = threading.Thread(target=self._gen_worker,
                                             args=(self._audio_path, device), daemon=True)
        self._prog_thread.start()

    def _gen_worker(self, audio: str, device: str) -> None:
        """后台：POST /api/analyze → GET /api/program（阻塞，故走线程）。"""
        import urllib.request
        try:
            base = self._lab.base_url
            # 等服务起来（最多 ~20s）
            ok = False
            for _ in range(40):
                try:
                    with urllib.request.urlopen(base + "/index.html", timeout=0.5):
                        ok = True
                        break
                except Exception:
                    import time as _t
                    _t.sleep(0.5)
            if not ok:
                self._gen_done.emit(False, "实验室服务未就绪（看日志）", "")
                return
            # 上传音频
            import mimetypes
            import uuid
            boundary = "----lab" + uuid.uuid4().hex
            with open(audio, "rb") as f:
                data = f.read()
            name = os.path.basename(audio)
            body = b"".join([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                data, b"\r\n", f"--{boundary}--\r\n".encode(),
            ])
            req = urllib.request.Request(base + "/api/analyze", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=3600) as r:
                analysis = json.loads(r.read().decode("utf-8"))
            sha1 = analysis.get("sha1", "")
            # 取编排（无 LLM 时服务端自动降级 v9）
            with urllib.request.urlopen(
                    f"{base}/api/program?sha1={sha1}&device={device}", timeout=3600) as r:
                out = json.loads(r.read().decode("utf-8"))
            self._gen_done.emit(True, out.get("engine", "?"), json.dumps(out, ensure_ascii=False))
        except Exception as e:
            self._gen_done.emit(False, f"{type(e).__name__}: {e}", "")

    def _on_gen_done(self, ok: bool, engine: str, payload: str) -> None:
        self._btn_analyze.setEnabled(True)
        if not ok:
            self._status.setText(f"生成失败：{engine}")
            return
        out = json.loads(payload)
        prog = out.get("program")
        if not prog:
            self._status.setText("生成失败：服务未返回 program")
            return
        self._program = prog
        self._status.setText(f"编排就绪（引擎 {engine}）")
        self._render_program()

    # ── 渲染（主进程内 numpy，预览与输出同一份 field）──────────
    def _render_program(self) -> None:
        if self._program is None:
            return
        root = _lab_root()
        if not root:
            return
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from mlstage import effects_py as EP
            from mlstage import config as C
        except Exception as e:
            self._status.setText(f"加载求值器失败：{e}")
            return
        # 时长：取 program 里最大 t1
        try:
            t1 = max((float(c["t1"]) for tr in self._program.get("tracks", {}).values()
                      for c in tr), default=1.0)
        except Exception:
            t1 = 1.0
        fps = float(getattr(C, "FPS", 30.0))
        T = max(10, int(t1 * fps) + 1)
        cfg = {"field_res": 16, "smooth_alpha": getattr(C, "SMOOTH_ALPHA", 0.6),
               "n_ch": 4, "fps": fps}
        try:
            tl = EP.build_cue_timeline(self._program, T, fps, cfg,
                                       {"beat_lag": 15, "beat_offset": 0})
            field = tl["field"]
        except Exception as e:
            self._status.setText(f"渲染失败：{e}")
            return
        # 泵预充点：program 里 precharge=True 的 pump clip 起点
        pre = [float(c.get("t0", 0.0)) for c in self._program.get("tracks", {}).get("pump", [])
               if c.get("precharge")] if self._program.get("tracks") else []
        self._render = LabPlayback(field=field, spec=self._spec, res=16, ch=4,
                                   fps=fps, audio_path=self._audio_path,
                                   precharge_points=pre, duration=T / fps)
        if self._out is None:                 # 未走 set_context 时兜底建（测试/直接调用）
            self._out = LabOutput(self._nm, self)
            U = Qt.ConnectionType.UniqueConnection   # 幂等：防重复连接累积
            self._out.frame_changed.connect(self._on_frame, U)
            self._out.state_changed.connect(self._on_play_state, U)
        self._out.load(self._render)
        self._out.set_muted(not self._chk_output.isChecked())
        self._seek.setValue(0)
        self._time_lbl.setText(f"0.0 / {self._render.duration:.1f} s")
        self._status.setText(f"渲染 {T} 帧就绪（{self._render.duration:.1f}s）")

    # ── 传输 ────────────────────────────────────────────────────
    def _on_play(self) -> None:
        if not self._ensure_preview():
            pass
        if self._render is None:
            QMessageBox.information(self, "实验室", "请先生成编排。")
            return
        if self._out is None:
            self._out = LabOutput(self._nm, self)
        self._out.play()
        self._btn_play.setText("暂停")

    def _on_stop(self) -> None:
        if self._out is not None:
            self._out.stop()
        self._btn_play.setText("播放")
        self._seek.setValue(0)

    def _on_seek(self) -> None:
        if self._out is None or self._render is None:
            return
        t = self._seek.value() / 1000.0 * self._render.duration
        self._out.seek(t)

    def _on_frame(self, idx: int, total: int) -> None:
        if self._render is None:
            return
        if total > 0:
            self._seek.blockSignals(True)
            self._seek.setValue(int(idx / total * 1000))
            self._seek.blockSignals(False)
        self._time_lbl.setText(f"{idx / max(self._render.fps,1e-6):.1f} / {self._render.duration:.1f} s")
        # 推帧给 3D 预览（用 field 原始行）
        if self._preview is not None and self._render is not None:
            try:
                row = self._render.field[idx]
                js = self._bridge.frame_js(idx, list(row[::max(1, len(row)//256)])[:1024])
                self._preview.page().runJavaScript(js)
            except Exception:
                pass

    def _on_play_state(self, playing: bool) -> None:
        self._btn_play.setText("暂停" if playing else "播放")

    def _on_output_toggle(self, on: bool) -> None:
        if self._out is not None:
            self._out.set_muted(not on)

    # ── 其它工具拉起 ────────────────────────────────────────────
    def _on_launch_droneforge(self) -> None:
        ok, info = self._lab.launch_tool("droneforge")
        if not ok:
            self._df_status.setText(info)
            return
        self._df_status.setText(f"已启动：{info}")
        if info.startswith("http") and self._df_view is None:
            try:
                from PyQt6.QtWebEngineWidgets import QWebEngineView
                v = QWebEngineView(self._df_host)
                lay = QVBoxLayout(self._df_host)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(v)
                v.load(QUrl(info))
                self._df_view = v
            except Exception as e:
                self._df_status.setText(f"已启动（宿主内嵌失败 {e}，请在浏览器打开 {info}）")

    def _on_launch_toolbox(self) -> None:
        ok, info = self._lab.launch_tool("toolbox")
        self._tb_status.setText(info if ok else f"启动失败：{info}")

    # ── LLM 配置 ────────────────────────────────────────────────
    def _llm_form(self) -> dict:
        return {
            "url": self._llm_url.text().strip(),
            "format": self._llm_fmt.currentData() or "",
            "key": self._llm_key.text().strip(),
            "model": self._llm_model.text().strip(),
            "timeout": str(self._llm_timeout.value()),
            "thinking": self._llm_think.currentData() or "off",
        }

    # ── 局域网 LLM 发现（开发机 ↔ 跑模型的机器）────────────────────
    def _on_lan_scan(self) -> None:
        """后台扫描同网段的 LLM 服务，命中后填入下拉框。"""
        self._btn_lan.setEnabled(False)
        self._lan_cb.clear()
        self._lan_cb.addItem("扫描中…（全段约 10-40 秒）", "")
        self._llm_status.setText("正在扫描局域网 LLM 服务…")
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")

        def work():
            try:
                from . import lan_discovery as LD
                found = LD.scan_lan(full=True, timeout=0.5)
                self._lan_found.emit(found)
            except Exception as e:
                self._lan_found.emit([])
                self._log_safe(f"局域网扫描失败：{e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_lan_found(self, found: list) -> None:
        self._btn_lan.setEnabled(True)
        self._lan_cb.clear()
        if not found:
            self._lan_cb.addItem("未发现 LLM 服务", "")
            self._llm_status.setText(
                "未发现局域网 LLM。请确认：①两机同一 WiFi ②对端 llama-server 带 "
                "--host 0.0.0.0 ③防火墙放行该端口")
            self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_WARN};")
            return
        for s in found:
            tag = "端口开(未确认)" if s.get("weak") else s.get("kind", "?")
            hint = f" · {s['model_hint']}" if s.get("model_hint") else ""
            self._lan_cb.addItem(f"{s['host']}:{s['port']} [{tag}]{hint}",
                                 json.dumps(s, ensure_ascii=False))
        self._lan_cb.setCurrentIndex(0)
        self._llm_status.setText(f"发现 {len(found)} 个服务，选中即填入端点")
        self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_SUCCESS};")

    def _on_lan_pick(self, idx: int) -> None:
        if idx < 0:
            return
        raw = self._lan_cb.itemData(idx)
        if not raw:
            return
        try:
            s = json.loads(raw)
        except Exception:
            return
        self._llm_url.setText(s.get("url", ""))
        self._llm_fmt.setCurrentIndex(max(0, self._llm_fmt.findData("openai")))
        self._llm_key.setText("")            # 局域网免鉴权
        if s.get("model_hint"):
            self._llm_model.setText(s["model_hint"])
        self._llm_status.setText(
            f"已填入 {s['host']}:{s['port']}（模型 {s.get('model_hint') or '未识别'}）"
            f" —— 按【测试连接】验证，再按【保存】生效")
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")

    def _log_safe(self, msg: str) -> None:
        try:
            self._status.setText(msg[:160])
        except Exception:
            pass

    def _apply_llm_preset(self, key: str) -> None:
        """一键套用预设（云端 DeepSeek / 本地 llama.cpp）。套用后需【保存】生效。"""
        spec = MS.LLM_PRESETS.get(key)
        if not spec:
            return
        d = spec["cfg"]
        self._llm_url.setText(d["url"])
        self._llm_fmt.setCurrentIndex(max(0, self._llm_fmt.findData(d["format"])))
        self._llm_key.setText(d["key"])
        self._llm_model.setText(d["model"])
        self._llm_timeout.setValue(int(float(d["timeout"])))
        if "thinking" in d:
            self._llm_think.setCurrentIndex(max(0, self._llm_think.findData(d["thinking"])))
        # 本地预设附带部署提示（这决定了它能否真的跑起来）
        hint = ""
        if key == "local":
            hint = ("　本地需先起 llama-server（OpenAI 兼容端口），并在启动参数里"
                    "给足上下文（-c ≥ 32768）与关闭/收敛思考")
        self._llm_status.setText(f"已套用「{spec['label']}」预设（未保存）{hint}")
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")

    def _on_llm_save(self) -> None:
        cfg = self._llm_form()
        MS.save_llm_settings(cfg)
        restarted = False
        if self._lab.running:
            self._lab.restart(cfg)
            restarted = True
        self._llm_status.setText("已保存" + ("，服务已重启以生效" if restarted else ""))
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")

    def _on_llm_default(self) -> None:
        """恢复出厂默认 = DeepSeek Responses（端点/协议/模型固化，通常只需改 Key）。"""
        self._apply_llm_preset("cloud")
        self._llm_status.setText("已恢复出厂默认（云端 DeepSeek Responses，未保存）")
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")

    def _on_llm_test(self) -> None:
        """用**当前界面的值**（无需先保存/重启）跑实验室真实调用路径。"""
        root = _lab_root()
        if not root:
            self._llm_status.setText("实验室模块未分发，无法测试")
            return
        py, note = MS.lab_python(root)
        probe = os.path.join(root, "tools", "llm_probe.py")
        if not os.path.isfile(probe):
            self._llm_status.setText("未找到 llm_probe.py（模块版本过旧）")
            return
        cfg = self._llm_form()
        env = os.environ.copy()
        env.update(MS.llm_env(cfg))
        self._llm_status.setText("测试中…")
        self._llm_status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};")
        self._btn_test.setEnabled(False)

        def work():
            import subprocess
            try:
                r = subprocess.run([py, "-u", probe, "--json"], cwd=root, env=env,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=120)
                line = (r.stdout or "").strip().splitlines()
                self._llm_test_done.emit(True, line[-1] if line else (r.stderr or "")[-200:])
            except Exception as e:
                self._llm_test_done.emit(False, f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _on_llm_test_done(self, launched: bool, payload: str) -> None:
        self._btn_test.setEnabled(True)
        if not launched:
            self._llm_status.setText(f"测试失败：{payload}")
            self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_ERROR};")
            return
        try:
            res = json.loads(payload)
        except Exception:
            self._llm_status.setText(f"测试输出异常：{payload[-160:]}")
            self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_ERROR};")
            return
        if res.get("ok"):
            self._llm_status.setText(f"通（{res.get('protocol')} · {res.get('latency_ms')}ms）"
                                     f" 回复：{res.get('reply_head','')}")
            self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_SUCCESS};")
        else:
            self._llm_status.setText(f"✘ 不通：{res.get('error','')[:200]}")
            self._llm_status.setStyleSheet(f"color:{_th.BRIGHT_ERROR};")

    # ── 服务/日志 ───────────────────────────────────────────────
    def _on_service_ready(self, ok: bool) -> None:
        self._status.setText("实验室服务就绪" if ok else "实验室服务未就绪")

    def _on_log(self, line: str) -> None:
        self._status.setText(line[:160])

    # ── 关闭 ────────────────────────────────────────────────────
    def closeEvent(self, ev) -> None:
        try:
            if self._out is not None:
                self._out.stop()               # 清 ml_lab 层，绝不留残值
        except Exception:
            pass
        try:
            self._lab.stop_all_tools()
        except Exception:
            pass
        self.closed.emit()
        super().closeEvent(ev)
