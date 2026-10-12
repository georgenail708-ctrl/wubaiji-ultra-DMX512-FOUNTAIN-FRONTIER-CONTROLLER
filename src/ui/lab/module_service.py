"""ML 实验室模块服务：探测绿色目录 + 管理 serve.py 子进程 + LLM 配置注入。

设计要点（模块化分包的核心）：
  * 实验室是 exe 旁的绿色目录（`ml_stage_lab/`），**不进 onefile 的 PYZ**。
  * 探测顺序 `[exe 旁] → [_MEIPASS] → [工作区根]`（同 tutorial_panel 的三查模式）。
  * 目录缺失 → `available == False`：主界面入口不出现（标准版因此天然无痕迹），
    不报错、不弹窗、不灰按钮——界面上看不出这个功能存在过。
  * 子进程用实验室自己的 .venv（依赖隔离，不污染主程序环境）；venv 缺失给
    可执行的补救指引而非静默失败。

LLM 提供方配置存在 QSettings("Qascade","LabModule")，启动子进程时转成
MLSTAGE_LLM_* 环境变量注入——模块侧 llm_provider 读同一套变量，故"界面配置 =
模块实际使用的配置"，测试按钮测的就是真链路。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from typing import Optional

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QSettings, QTimer, pyqtSignal

_SETTINGS_ORG = "Qascade"
_SETTINGS_APP = "LabModule"

LAB_DIR_NAME = "ml_stage_lab"
DEFAULT_PORT = 8765

# ── LLM 出厂默认：DeepSeek 官方 Responses API ────────────────────────
# ★ 用户 2026-09-13 定案：打包默认接 DeepSeek，**以后只换 API Key 即可**。
#   协议/端点/模型都已固化，界面上留 Key 一个可编辑框（见 lab_window ⑤ 区）。
#   密钥可被 QSettings 覆盖；换 key 只需在实验室窗口改 Key → 保存（自动重启服务）。
DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
# ★★ 2026-09-14 安全修正（CLAUDE.md Part 8「never commit secrets」）：
#   此处原先**硬编码一个真实 API key** —— 一旦提交就永久留在 git 历史里。
#   现改为**从环境变量读取**（默认空串 = 未配置）。用户在实验室窗口 ⑤ 区填一次
#   即存 QSettings（不入库）；运维要预置就 `export MLSTAGE_LLM_KEY=...`，
#   源码中不再出现任何密钥。
DEEPSEEK_DEFAULT_KEY = os.environ.get("MLSTAGE_LLM_KEY", "")
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"

# ── 实验室三工具注册表（模块化：探测 + 拉起 + 承载各自独立进程）─────────
# 每个工具都是独立项目/独立进程；宿主只负责"存在即启用、缺失即隐藏"。
#   ml_stage_lab   ML 自动编曲（HTTP 子进程，宿主 QWebEngineView 承载）
#   drone-forge    DroneForge 无人机编舞（HTTP 子进程，--mode server）
#   _toolbox 工具箱（GUI 独立进程，自建 QApplication）
TOOLS = {
    "arranger": {
        "dir": "ml_stage_lab", "entry": "serve.py", "label": "ML 自动编曲",
        "kind": "http", "default_port": 8765,
    },
    "droneforge": {
        "dir": "drone-forge", "entry": "run.py", "label": "无人机编舞",
        "kind": "http", "default_port": 8000,
        # run.py 支持 --mode gui|browser|server；宿主承载时用 server（不弹自己的窗口）
        "extra_args": ["--mode", "server"],
    },
    "toolbox": {
        "dir": "_toolbox", "entry": "main.py", "label": "工具箱",
        "kind": "gui", "default_port": 0,
    },
}


def find_tool_dir(tool_key: str) -> str:
    """定位某工具的绿色目录（exe 旁优先）。缺失返回空串。"""
    spec = TOOLS.get(tool_key)
    if not spec:
        return ""
    for base in _candidate_roots():
        p = os.path.join(base, spec["dir"])
        if os.path.isfile(os.path.join(p, spec["entry"])):
            return os.path.abspath(p)
    return ""


def find_tool_exe(tool_key: str) -> str:
    """定位打包版 exe（绿色目录优先，其次 exe 旁/发行目录）。"""
    exe_names = {
        "arranger": (),                       # 编曲无独立 exe（纯源码+venv）
        "droneforge": ("DroneForge.exe",),
        "toolbox": ("工具箱.exe",),
    }
    names = exe_names.get(tool_key, ())
    if not names:
        return ""
    d = find_tool_dir(tool_key)
    cands = []
    if d:
        cands += [os.path.join(d, n) for n in names]
        cands += [os.path.join(d, "dist", n) for n in names]
    for base in _candidate_roots():
        cands += [os.path.join(base, n) for n in names]
        cands += [os.path.join(base, TOOLS[tool_key]["dir"], "dist", n) for n in names]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return ""


def tool_available(tool_key: str) -> bool:
    """工具是否可用（源码目录或打包 exe 任一存在即可）。"""
    if tool_key not in TOOLS:
        return False
    return bool(find_tool_dir(tool_key) or find_tool_exe(tool_key))
# 出厂默认 = DeepSeek Responses（用户定案）。这三项固化，通常只需改 key。
LLM_DEFAULTS = {
    "url": DEEPSEEK_RESPONSES_URL,
    "format": "responses",              # OpenAI Responses API 格式
    "key": DEEPSEEK_DEFAULT_KEY,
    "model": DEEPSEEK_DEFAULT_MODEL,
    "timeout": "1800",
    "thinking": "auto",          # 云端思考由厂商模型决定，此处不干预
}
# ── 一键预设（实验室窗口⑤区可切换；用户定案：本地/云端都要能一键切）──
#   云端 = DeepSeek Responses（出厂默认，只换 Key）
#   本地 = llama.cpp llama-server + Qwen3.6-35B-A3B Q4（另一台电脑；无网演出场景）
LLM_LOCAL_PRESET = {
    "url": "http://127.0.0.1:8080/v1/chat/completions",
    "format": "openai",
    "key": "",
    "model": "qwen3.6-35b-a3b",
    "timeout": "1800",
    "thinking": "off",           # ★ 本地务必关思考（实测 4.2s→0.7s，长编排省更多）
}
LLM_PRESETS = {
    "cloud": {
        "label": "云端 DeepSeek（Responses）",
        "cfg": dict(LLM_DEFAULTS),
    },
    "local": {
        "label": "本地 llama.cpp（Qwen3.6-35B-A3B）",
        # 本地预设关思考：Qwen3.6 默认开思考会先烧预算做推理（实测 4.2s→0.7s，
        # 长编排省更多），且能避免"预算被吃光→空响应/截断"。
        "cfg": {**LLM_LOCAL_PRESET, "thinking": "off"},
    },
}


def llm_preset_for_url(url: str) -> str:
    """按 URL 猜当前属于哪个预设（用于界面回显）。"""
    u = (url or "").lower()
    if "deepseek.com" in u:
        return "cloud"
    if any(h in u for h in ("127.0.0.1", "localhost", "192.168.", "10.", "172.")):
        return "local"
    return ""


def _candidate_roots() -> list[str]:
    """实验室绿色目录的候选根（exe 旁优先——分包分发的主形态）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else ""
    meipass = getattr(sys, "_MEIPASS", "")
    ws_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    out: list[str] = []
    for base in (exe_dir, meipass, ws_root):
        if base and base not in out:
            out.append(base)
    return out


def find_lab_dir() -> str:
    """定位 ml_stage_lab 目录；都找不到返回空串。"""
    for base in _candidate_roots():
        p = os.path.join(base, LAB_DIR_NAME)
        if os.path.isfile(os.path.join(p, "serve.py")):
            return os.path.abspath(p)
    return ""


def lab_python(lab_dir: str) -> tuple[str, str]:
    """返回 (解释器路径, 说明)。优先级从"最可移植"到"最兜底"：

      1) `<lab>/python/python.exe`  —— 随包**嵌入式 Python + site-packages**
         （推荐形态：自包含、无绝对路径依赖，换机直接可用）
      2) `<lab>/.venv/Scripts/python.exe` —— 开发态 venv
         （★ 注意：venv 的 pyvenv.cfg 硬编码基础解释器绝对路径，
           换机后若该路径不存在会直接报 "did not find executable" ——
           故仅作开发态用途，不适合分发）
      3) 主程序解释器 —— 兜底（多半缺 torch，会在日志里提示）
    """
    if not lab_dir:
        return "", "实验室目录缺失"
    portable = os.path.join(lab_dir, "python", "python.exe")
    if os.path.isfile(portable):
        return portable, "实验室随包运行时（自包含）"
    venv_py = os.path.join(lab_dir, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_py):
        return venv_py, "实验室独立 venv（开发态）"
    venv_py = os.path.join(lab_dir, ".venv", "bin", "python")   # 非 Windows 兜底
    if os.path.isfile(venv_py):
        return venv_py, "实验室独立 venv（开发态）"
    return sys.executable, "回退主程序解释器（可能缺 torch/依赖）"


def load_llm_settings() -> dict:
    s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    return {
        "url": str(s.value("llm/url", LLM_DEFAULTS["url"]) or LLM_DEFAULTS["url"]),
        "format": str(s.value("llm/format", LLM_DEFAULTS["format"]) or LLM_DEFAULTS["format"]),
        "key": str(s.value("llm/key", LLM_DEFAULTS["key"]) or ""),
        "model": str(s.value("llm/model", LLM_DEFAULTS["model"]) or LLM_DEFAULTS["model"]),
        "timeout": str(s.value("llm/timeout", LLM_DEFAULTS["timeout"]) or LLM_DEFAULTS["timeout"]),
        "thinking": str(s.value("llm/thinking", "off") or "off"),
    }


def save_llm_settings(cfg: dict) -> None:
    s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    for k in ("url", "format", "key", "model", "timeout"):
        s.setValue(f"llm/{k}", str(cfg.get(k, LLM_DEFAULTS[k])))
    s.setValue("llm/thinking", str(cfg.get("thinking", "off") or "off"))
    s.sync()


def llm_env(cfg: dict) -> dict:
    """把界面配置转成模块侧环境变量（唯一契约，见 mlstage/llm_provider.py）。"""
    return {
        "MLSTAGE_LLM_URL": str(cfg.get("url") or LLM_DEFAULTS["url"]),
        "MLSTAGE_LLM_FORMAT": str(cfg.get("format") or LLM_DEFAULTS["format"]),
        "MLSTAGE_LLM_KEY": str(cfg.get("key") or ""),
        "MLSTAGE_LLM_MODEL": str(cfg.get("model") or LLM_DEFAULTS["model"]),
        "MLSTAGE_LLM_TIMEOUT": str(cfg.get("timeout") or LLM_DEFAULTS["timeout"]),
        "MLSTAGE_LLM_THINKING": str(cfg.get("thinking", "off") or "off"),
    }


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class LabModule(QObject):
    """实验室模块生命周期服务（单例持有于 MainWindow）。

    信号：
      log_line(str)     子进程输出/状态文本
      state_changed()   available / running / port 变化
      ready(bool)       服务健康检查结果
    """
    log_line = pyqtSignal(str)
    state_changed = pyqtSignal()
    ready = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._tool_procs: dict = {}          # tool_key -> QProcess
        self._lab_dir: str = ""
        self._python: str = ""
        self._python_note: str = ""
        self._port: int = DEFAULT_PORT
        self._starting = False
        self._healthy = False
        self._refresh_paths()

    # ── 探测 ──────────────────────────────────────────────────────
    def _refresh_paths(self) -> None:
        self._lab_dir = find_lab_dir()
        if self._lab_dir:
            self._python, self._python_note = lab_python(self._lab_dir)
        else:
            self._python, self._python_note = "", "实验室目录缺失"

    @property
    def available(self) -> bool:
        """模块是否可用（决定主界面入口是否出现）。"""
        if not self._lab_dir:
            self._refresh_paths()
        return bool(self._lab_dir and os.path.isfile(os.path.join(self._lab_dir, "serve.py")))

    @property
    def lab_dir(self) -> str:
        return self._lab_dir

    @property
    def python_note(self) -> str:
        return self._python_note

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def port(self) -> int:
        return self._port

    # ── 子进程 ────────────────────────────────────────────────────
    def start(self, llm_cfg: Optional[dict] = None) -> bool:
        """拉起 serve.py。已在跑则直接返回 True。"""
        if self.running:
            return True
        self._refresh_paths()
        if not self.available:
            self.log_line.emit("[lab] 实验室目录不存在——该功能未随本发行版分发")
            return False
        if self._starting:
            return False
        # 端口自适应：默认 8765，占用则往上找
        port = DEFAULT_PORT
        for _ in range(20):
            if _port_free(port):
                break
            port += 1
        else:
            self.log_line.emit("[lab] 8765~8784 端口均被占用，无法启动")
            return False
        self._port = port

        cfg = llm_cfg if llm_cfg is not None else load_llm_settings()
        env = QProcessEnvironment.systemEnvironment()
        for k, v in llm_env(cfg).items():
            env.insert(k, v)
        env.insert("MSTAGE_DEVICE", os.environ.get("MSTAGE_DEVICE", "auto"))
        env.insert("PYTHONIOENCODING", "utf-8")

        proc = QProcess(self)
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(self._lab_dir)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)

        self._starting = True
        self._proc = proc
        self.log_line.emit(f"[lab] 启动 {self._python_note}: {self._python} -u serve.py")
        # 绑 127.0.0.1：集成场景下实验室服务只应本机可达（不放 LAN）
        proc.start(self._python,
                   ["-u", "serve.py", "--host", "127.0.0.1", "--port", str(self._port)])
        self.state_changed.emit()
        # 健康轮询
        QTimer.singleShot(1200, lambda: self._poll_health(0))
        return True

    def stop(self) -> None:
        p = self._proc
        self._proc = None
        self._healthy = False
        if p is not None:
            try:
                p.readyReadStandardOutput.disconnect()
            except Exception:
                pass
            try:
                p.terminate()
                if not p.waitForFinished(3000):
                    p.kill()
                    p.waitForFinished(1500)
            except Exception:
                pass
            self.log_line.emit("[lab] serve.py 已停止")
        self.state_changed.emit()

    def restart(self, llm_cfg: Optional[dict] = None) -> bool:
        self.stop()
        return self.start(llm_cfg)

    # ── 内部 ──────────────────────────────────────────────────────
    def _on_output(self) -> None:
        p = self._proc
        if p is None:
            return
        try:
            raw = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
        except Exception:
            return
        for line in raw.splitlines():
            if line.strip():
                self.log_line.emit(line.rstrip())

    def _on_finished(self, code, _status) -> None:
        self._starting = False
        self._healthy = False
        self.log_line.emit(f"[lab] serve.py 退出（code={code}）")
        self.state_changed.emit()

    def _on_error(self, err) -> None:
        self._starting = False
        self.log_line.emit(f"[lab] 子进程错误：{err}")
        self.state_changed.emit()

    def _probe_once(self) -> bool:
        """一次轻量 TCP 探活（不触发分析/模型加载）。"""
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=0.4):
                return True
        except OSError:
            return False

    def _poll_health(self, attempt: int) -> None:
        if not self.running:
            self.ready.emit(False)
            return
        if self._probe_once():
            self._healthy = True
            self._starting = False
            self.log_line.emit(f"[lab] 服务就绪 {self.base_url}")
            self.state_changed.emit()
            self.ready.emit(True)
            return
        if attempt >= 40:          # ~12s 上限（首次启动要 import torch 等）
            self._starting = False
            self.state_changed.emit()
            self.log_line.emit("[lab] 服务未在预期时间内就绪（看上方日志）")
            self.ready.emit(False)
            return
        QTimer.singleShot(300, lambda: self._poll_health(attempt + 1))

    # ── 通用工具拉起（drone-forge / _toolbox）────────────────
    def launch_tool(self, tool_key: str, llm_cfg: Optional[dict] = None) -> tuple[bool, str]:
        """拉起非编曲工具。返回 (成功, 说明或URL/进程句柄描述)。

        http 类（drone-forge）：源码模式用其 venv 跑 run.py --mode server；
          exe 模式直接跑 exe --mode server。
        gui 类（_toolbox）：源码模式用其 venv 跑 main.py；exe 模式跑 exe。
          纯独立窗口，宿主不承载 UI。
        """
        spec = TOOLS.get(tool_key)
        if not spec:
            return False, f"未知工具 {tool_key}"
        exe = find_tool_exe(tool_key)
        d = find_tool_dir(tool_key)
        if not exe and not d:
            return False, f"{spec['label']} 未随本发行版分发"

        if spec["kind"] == "http":
            port = spec["default_port"]
            for _ in range(20):
                if _port_free(port):
                    break
                port += 1
            if exe:
                program, args = exe, list(spec.get("extra_args", [])) + ["--port", str(port)]
            else:
                py, note = lab_python(d)
                program = py
                args = [os.path.join(d, spec["entry"])] + list(spec.get("extra_args", []))
                args += ["--port", str(port)]
            if spec["kind"] == "http" and tool_key != "arranger":
                # drone-forge 的 autosave/snapshot 需要可写目录（frozen 下会落临时目录）
                pass
            proc = QProcess(self)
            proc.setWorkingDirectory(d or os.path.dirname(exe))
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda p=proc: self._relay(p))
            proc.finished.connect(
                lambda c, s, k=tool_key: self.log_line.emit(f"[lab] {k} 进程退出 code={c}"))
            self._tool_procs[tool_key] = proc
            self.log_line.emit(f"[lab] 启动 {spec['label']}: {program} {' '.join(args)}")
            proc.start(program, args)
            return True, f"http://127.0.0.1:{port}/"

        # gui：独立进程，宿主不承载
        if exe:
            program, args = exe, []
        else:
            py, _note = lab_python(d)
            program, args = py, [os.path.join(d, spec["entry"])]
        proc = QProcess(self)
        proc.setWorkingDirectory(d or os.path.dirname(exe))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._relay(p))
        self._tool_procs[tool_key] = proc
        self.log_line.emit(f"[lab] 启动 {spec['label']}: {program}")
        proc.start(program, args)
        return True, "独立窗口"

    def _relay(self, proc) -> None:
        try:
            raw = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        except Exception:
            return
        for line in raw.splitlines():
            if line.strip():
                self.log_line.emit(line.rstrip())

    def tool_running(self, tool_key: str) -> bool:
        p = self._tool_procs.get(tool_key)
        return p is not None and p.state() != QProcess.ProcessState.NotRunning

    def stop_tool(self, tool_key: str) -> None:
        p = self._tool_procs.pop(tool_key, None)
        if p is not None:
            try:
                p.terminate()
                if not p.waitForFinished(3000):
                    p.kill()
            except Exception:
                pass

    def stop_all_tools(self) -> None:
        for k in list(self._tool_procs):
            self.stop_tool(k)

    # ── 对外小工具（供窗口调用）───────────────────────────────────
    def api_get(self, path: str, timeout_ms: int = 120000) -> Optional[dict]:
        """同步 GET JSON（窗口在非 UI 线程用；简易实现，够本模块用）。"""
        import urllib.request
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=timeout_ms / 1000.0) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            self.log_line.emit(f"[lab] GET {path} 失败：{e}")
            return None
