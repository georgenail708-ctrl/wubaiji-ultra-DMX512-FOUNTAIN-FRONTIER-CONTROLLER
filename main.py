"""
Qascade — Entry Point
专业 DMX 视觉控制系统
启动策略：只初始化引擎 + 网络 + 主窗口，面板由主窗口功能栏按需懒加载
"""

from __future__ import annotations

import sys
import os
import traceback
import logging
# R53 排查：frozen 态全程 faulthandler（原生崩溃也留栈）
try:
    if getattr(sys, "frozen", False):
        import faulthandler as _fh
        _fh_log = open(os.path.join(os.path.dirname(sys.executable), "boot_fault.log"), "w", encoding="utf-8")
        _fh.enable(file=_fh_log)
except Exception:
    pass

# ============================================================
# 关键修复：PyInstaller 打包后路径处理
# ============================================================
if getattr(sys, 'frozen', False):
    _ROOT = sys._MEIPASS
else:
    _ROOT = os.path.dirname(os.path.abspath(__file__))

_SRC = os.path.join(_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ============================================================
# 日志配置
# ============================================================
# ★ 默认 INFO（2026-09-13 修）：原为 DEBUG，而渲染热路径有 15 处 log.debug
#   （playback_engine 的 [DIR_FADE] 等）逐帧同步写 stderr —— 实测峰值
#   410~458 行/秒（tick_probe3.log 共 22223 行），在渲染/UI 线程持 GIL，
#   是"整机卡顿"的直接来源之一。调试时用 QASCADE_LOG_DEBUG=1 恢复 DEBUG。
_log_level = (logging.DEBUG if os.environ.get("QASCADE_LOG_DEBUG", "0") == "1"
              else logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("Qascade")

# ============================================================
# PyQt6 导入
# ============================================================
try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
    from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal, QRectF
    from PyQt6.QtGui     import QColor, QFont, QPixmap, QPainter, QLinearGradient, QBrush, QIcon, QPen, QRadialGradient, QPainterPath
except ImportError as e:
    print(f"[FATAL] PyQt6 not installed: {e}")
    sys.exit(1)


# ============================================================
# 工具函数
# ============================================================
def _try_import(module_path: str, label: str):
    """安全导入模块"""
    try:
        parts = module_path.rsplit(".", 1)
        mod   = __import__(parts[0], fromlist=[parts[1]] if len(parts) > 1 else [])
        return getattr(mod, parts[1]) if len(parts) > 1 else mod
    except Exception as exc:
        log.warning("Could not import %s (%s): %s", label, module_path, exc)
        return None


from src.ui.startup_theme import SPLASH_PALETTE, build_splash_background


class _AnimatedSplash(QWidget):
    """雾白纪启动画面 v3 —— 正宗 Blender splash 布局。

    全幅暖米色 artwork（对角双色调 + 极细网格纹理），居中 200px 官方图标
    + 聚光呼吸 + 对角光带周期扫掠；底部 64px 状态条：左「V1.0」大版本
    白字（Blender 式）+ 中部进度线/状态文字 + 右「雾白纪」字标。
    文案随时间错峰淡入；退出 240ms 淡出。静态层离屏缓存。
    """

    def __init__(self):
        super().__init__(None,
            Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("雾白纪 ULTRA")
        w, h = 760, 460
        self._w, self._h = w, h
        self.resize(w, h)
        self._t = 0
        self._msg = "正在启动控制核心…"
        self._exiting = False
        self._fade_started = False
        from PyQt6.QtCore import QElapsedTimer
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self.MIN_SHOW_MS = 3000
        self._icon_pix = self._load_icon()

        self._base = build_splash_background((w, h), self.devicePixelRatioF())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _load_icon(self) -> QPixmap:
        try:
            from src.ui.qascade_theme import app_icon_path
            path = app_icon_path()
        except Exception:
            path = ""
        if path and os.path.exists(path):
            pm = QIcon(path).pixmap(200, 200)
            if not pm.isNull():
                return pm
        for name in ("雾白纪ULTRA图标.ico", "雾白纪新版图标.ico", "至冬软件新版图标.ico", "app.ico"):
            path = os.path.join(_ROOT, name)
            if os.path.exists(path):
                pm = QIcon(path).pixmap(200, 200)
                if not pm.isNull():
                    return pm
        return QPixmap()

    def _tick(self):
        self._t += 1
        if self._exiting:
            self.setWindowOpacity(max(0.0, self.windowOpacity() - 0.09))
            if self.windowOpacity() <= 0.02:
                self._timer.stop(); self.close()
        else:
            self.update()

    def paintEvent(self, ev):
        import math as _math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self._w, self._h
        t = self._t
        p.drawPixmap(0, 0, self._base)

        def fade(t0, speed=15):
            v = (t - t0) * speed / 100.0
            return 0.0 if v < 0 else (1.0 if v > 1 else v)

        isz = 200
        ix = (w - isz) // 2
        iy = int(h * 0.34) - isz // 2
        bob = int(3 * _math.sin(t * 0.032))
        self._draw_backdrop_fx(p, t, iy, isz, bob)
        sc = 0.95 + 0.05 * fade(2, 18)
        if not self._icon_pix.isNull():
            p.setOpacity(fade(2, 18))
            target = QRectF(ix + isz * (1 - sc) / 2, iy + isz * (1 - sc) / 2 + bob,
                            isz * sc, isz * sc)
            p.drawPixmap(target, self._icon_pix,
                         QRectF(self._icon_pix.rect()))
            p.setOpacity(1.0)
        p.setOpacity(fade(50))
        p.setPen(QColor(SPLASH_PALETTE["muted"]))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(QRectF(18, 12, 200, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "雾白纪 ULTRA · WU BAI JI")
        sy = h - 64
        p.setOpacity(fade(40))
        p.setPen(QColor(SPLASH_PALETTE["ink"]))
        p.setFont(QFont("Segoe UI", 22, QFont.Weight.DemiBold))
        p.drawText(QRectF(24, sy + 6, 110, 40),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "ULTRA")
        px0, px1 = 150, w - 190
        span = px1 - px0
        p.setPen(QPen(QColor(SPLASH_PALETTE["border"]), 3))
        p.drawLine(px0, sy + 24, px1, sy + 24)
        seg, echo = 170.0, 100.0
        period = span + seg + echo
        ph = (t * 3.4) % period
        sx = px0 + ph - seg - echo
        tail = QLinearGradient(sx - echo, sy + 24, sx, sy + 24)
        tail.setColorAt(0.0, QColor(255, 170, 80, 0))      # 琥珀扫尾（品牌色）
        tail.setColorAt(1.0, QColor(255, 190, 110, 95))
        p.setPen(QPen(QBrush(tail), 3))
        a0, a1 = int(max(px0, sx - echo)), int(min(px1, sx))
        if a1 > a0: p.drawLine(a0, sy + 24, a1, sy + 24)
        line = QLinearGradient(sx, sy + 24, sx + seg, sy + 24)
        line.setColorAt(0.0, QColor(113, 168, 255, 0))
        line.setColorAt(0.5, QColor(150, 195, 255, 240))
        line.setColorAt(1.0, QColor(113, 168, 255, 0))
        p.setPen(QPen(QBrush(line), 3))
        b0, b1 = int(max(px0, sx)), int(min(px1, sx + seg))
        if b1 > b0: p.drawLine(b0, sy + 24, b1, sy + 24)
        p.setOpacity(max(0.3, fade(20)))
        p.setPen(QColor(SPLASH_PALETTE["muted"]))
        p.setFont(QFont("Microsoft YaHei UI", 9))
        p.drawText(QRectF(px0, sy + 34, span, 20),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   self._msg)
        p.setOpacity(fade(56))
        p.setPen(QColor(SPLASH_PALETTE["ink"]))
        p.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        p.drawText(QRectF(w - 210, sy + 8, 190, 30),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "雾白纪")
        p.setPen(QColor(SPLASH_PALETTE["muted"]))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(w - 210, sy + 36, 190, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "Professional DMX Suite")
        p.end()

    def _draw_backdrop_fx(self, p, t, iy, isz, bob):
        """呼吸光晕 + 斜向扫光带（paintEvent 抽出的动态背景层）。

        R180 视觉验收修复两处：
          · 光晕硬边圆盘 —— 渐变半径 230 > 椭圆半径 170，在 alpha≈17 处
            被裁断出可见圆边；改为渐变半径=绘制半径（边缘 alpha 恰 0 全软边），
            且品牌蓝环 alpha>0 真正可见（旧 1.0 stop alpha=0 → 双色退化成单色）；
          · 扫光带垂直硬缝 —— 旧"竖直裁剪窗 × 对角渐变"在裁剪窗边切出贯穿
            整窗的硬边；改为剪切空间画竖直光带（渐变与带边垂直 → 两侧全软），
            外加圆角卡裁剪。
        """
        import math as _math
        w, h = self._w, self._h
        # ★ R178 ULTRA：呼吸光晕品牌双色（琥珀内环 + 品牌蓝外散，呼应新图标）
        spot_a = 34 + int(22 * (1 + _math.sin(t * 0.038)))
        glow_r = (isz + 140) / 2.0
        spot = QRadialGradient(w / 2, iy + isz / 2 + bob, glow_r)
        spot.setColorAt(0.00, QColor(255, 250, 240, spot_a))
        spot.setColorAt(0.52, QColor(255, 160, 70, 26))       # 琥珀内环（图标扫光同色）
        spot.setColorAt(0.85, QColor(113, 168, 255, 12))      # 品牌蓝外散
        spot.setColorAt(1.00, QColor(113, 168, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(spot))
        p.drawEllipse(QRectF(w / 2 - glow_r, iy + isz / 2 + bob - glow_r, glow_r * 2, glow_r * 2))
        band_x = ((t * 4.6) % (w + 520)) - 420
        band = QLinearGradient(0, 0, 340, 0)
        band.setColorAt(0.0, QColor(255, 170, 80, 0))     # 琥珀扫光（ULTRA 图标呼应）
        band.setColorAt(0.5, QColor(255, 200, 130, 16))
        band.setColorAt(1.0, QColor(255, 170, 80, 0))
        p.setBrush(QBrush(band)); p.setPen(Qt.PenStyle.NoPen)
        p.save()
        _clip = QPainterPath(); _clip.addRoundedRect(QRectF(1, 1, w - 2, h - 2), 11, 11)
        p.setClipPath(_clip)
        p.translate(band_x, 0); p.shear(0.25, 0)
        p.drawRect(QRectF(0, -2, 340, h + 4))
        p.restore()

    def showMessage(self, msg, alignment=None, color=None):
        self._msg = str(msg); self.update()

    def begin_exit(self):
        if self._fade_started:
            return
        remain = self.MIN_SHOW_MS - self._elapsed.elapsed()
        if remain > 0:
            self._fade_started = True
            QTimer.singleShot(int(remain), self._start_fade)
        else:
            self._start_fade()

    def _start_fade(self):
        self._exiting = True


def _build_splash() -> "_AnimatedSplash":
    """构建雾白纪高级启动动画。"""
    return _AnimatedSplash()

# ============================================================
# Stub 类（降级备用）
# ============================================================
class _StubEngine:
    """最小化 stub，UI 可独立运行"""
    is_playing = False

    class _Project:
        tracks: list = []

    project = _Project()

    def play(self):     self.is_playing = True
    def pause(self):    self.is_playing = False
    def stop(self):     self.is_playing = False
    def seek(self, t):  pass
    def shutdown(self): pass

    def new_project(self):
        self.project = self._Project()
        self.project.tracks = []

    def add_track(self):
        from types import SimpleNamespace
        import uuid
        t = SimpleNamespace(
            id=str(uuid.uuid4()),
            name=f"Track {len(self.project.tracks) + 1}",
            color="#4a90d9",
            clips=[],
            muted=False,
            solo=False,
            filter_label=lambda: "ALL",
        )
        self.project.tracks.append(t)
        return t

    def remove_track(self, track_id: str):
        self.project.tracks = [t for t in self.project.tracks if t.id != track_id]


class _StubNetworkManager(QObject):
    """最小化 stub，包含所有必需的信号和方法"""
    is_running    = False
    protocol_name = ""
    frame_in      = pyqtSignal(object)
    frame_raw_in  = pyqtSignal(int, object)
    status_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    def start(self):          self.is_running = True
    def stop(self):           self.is_running = False
    def configure(self, cfg): pass
    def instance(self):       return self

    def update_layer(self, layer_name: str, universe: int, data):
        pass

    def clear_layer(self, layer_name: str, universe=None):
        pass

    def get_universe_data(self, universe: int):
        import numpy as np
        return np.zeros(512, dtype=np.uint8)

    def get_last_dmx(self, universe: int):
        import numpy as np
        return np.zeros(512, dtype=np.uint8)


# ============================================================
# 核心模块加载
# ============================================================
def _load_network_manager():
    """加载 NetworkManager 单例"""
    NM = _try_import("src.network.network_manager.NetworkManager", "NetworkManager")
    if NM:
        try:
            nm = NM.instance()
            log.info("Real NetworkManager loaded via .instance() id=%s", id(nm))
            return nm
        except Exception as e:
            log.warning("NetworkManager.instance() failed: %s", e)
    log.info("Using stub network manager.")
    return _StubNetworkManager()


def _load_project_manager():
    """加载 ProjectManager 单例"""
    PM = _try_import("src.core.project_manager.ProjectManager", "ProjectManager")
    if PM:
        try:
            pm = PM.instance()
            log.info("Real ProjectManager loaded via .instance() id=%s", id(pm))
            return pm
        except Exception as e:
            log.warning("ProjectManager.instance() failed: %s", e)
    return None


def _load_engine(nm, pm):
    """加载播放引擎"""
    Engine = _try_import("src.engine.playback_engine.PlaybackEngine", "PlaybackEngine")
    if Engine:
        try:
            engine = Engine(nm, pm) if pm else Engine(nm, None)
            log.info("Real PlaybackEngine instantiated. id=%s", id(engine))
            return engine
        except Exception as e:
            log.warning("PlaybackEngine instantiation failed: %s — using stub", e)
    log.info("Using stub engine.")
    return _StubEngine()


def _build_ui(engine, nm):
    """创建主窗口"""
    from src.ui.main_window import MainWindow
    win = MainWindow(engine=engine, network_manager=nm)
    return win


# ============================================================
# 应用初始化
# ============================================================
def _create_app(argv: list[str]) -> QApplication:
    """创建 QApplication 并设置现代暗色主题（PyQtDarkTheme 引擎）"""
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    # ★ 教程面板用 QtWebEngine 渲染（本项目说明书含 SVG/JS 动画，Qt 其余控件族
    #   无法还原）。QtWebEngine 有硬性约束：**必须在 QApplication 之前**导入
    #   QtWebEngineWidgets，或先置 AA_ShareOpenGLContexts —— 否则运行期再 import
    #   会抛 "must be imported before a QCoreApplication instance is created"，
    #   导致教程面板打不开。此处提前置位并预导入（失败不影响主功能）。
    try:
        from PyQt6.QtCore import Qt as _Qt, QCoreApplication as _QCA
        _QCA.setAttribute(_Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        import PyQt6.QtWebEngineWidgets  # noqa: F401  (预热，满足导入时序约束)
    except Exception:
        pass

    app = QApplication(argv)
    # Windows 任务栏图标归组（用户点名侧边栏图标不对）：不设 AID 时任务栏
    # 用 python/pyi 引导器身份，图标呈默认态。
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WuBaiJi.FogWhite.1")
    except Exception:
        pass
    app.setApplicationName("Qascade")
    app.setOrganizationName("Qascade")
    app.setApplicationVersion("ULTRA")
    app.setStyle("Fusion")

    # 最新版应用图标（雾白纪 2026-08-27）：启动期只挂"路径型 QIcon"，
    # 零解码（解码推迟到 GUI 线程首次绘制）——frozen 态启动期解码会 C abort。
    # R56 补强：路径落空（如图标漏随包）时改挂程序化品牌标，绝不空 QIcon
    # （空 QIcon = Windows 标题栏/任务栏系统默认图标，即 08-28 前的顽疾）。
    try:
        from src.ui.qascade_theme import app_icon_path
        _ip = app_icon_path()
        if _ip:
            app.setWindowIcon(QIcon(_ip))
        else:
            from src.ui.qascade_theme import app_icon_pixmap
            _ipm = app_icon_pixmap(64)
            if _ipm is not None and not _ipm.isNull():
                app.setWindowIcon(QIcon(_ipm))
    except Exception:
        pass

    # ── Qascade Blender 风格主题 ────────────────────────────────────
    # QPalette 只做基座（Fusion 原生部件/缺省色兜底），样式统一由
    # src/ui/qascade_theme.py 的全局 QSS 提供（单一样式来源）。
    from src.ui.qascade_theme import apply_editor_palette
    apply_editor_palette(app)

    # ── Qascade 全局 QSS 样式（覆盖+增强 PyQtDarkTheme）───
    _apply_qascade_theme(app)

    return app


def _apply_qascade_theme(app: QApplication) -> None:
    """应用共享编辑器主题 QSS（来自 src/ui/qascade_theme.py）。"""
    try:
        from src.ui.qascade_theme import apply_qascade_theme
        apply_qascade_theme(app)
        log.info("Qascade QSS theme applied")
    except ImportError:
        log.debug("qascade_theme not available, skipping QSS")


def _handle_exception(exc_type, exc_value, exc_tb):
    """全局异常处理器"""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical("Uncaught exception:\n%s", msg)
    # R53：noconsole 打包态无处显示——落盘 exe 旁 crash.log 供排查
    try:
        if getattr(sys, "frozen", False):
            import datetime as _dt
            with open(os.path.join(_ROOT, "crash.log"), "a", encoding="utf-8") as f:
                f.write("[" + _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] " + msg)
                f.write(os.linesep)
    except Exception:
        pass
    if QApplication.instance():
        QMessageBox.critical(
            None, "未处理的异常",
            f"<pre style='font-family:Consolas;font-size:11px'>{msg[:2000]}</pre>",
        )


def _acquire_single_instance_guard() -> bool:
    """★ R193/IO-01：单实例守卫（模块级：main 行数基线）。

    接收 socket 双方均设 SO_REUSEADDR 时 Windows 允许二次 bind 成功（R182 的
    端口占用保护对自双开永不触发）——双实例会同时向同一广播域发
    ArtNet/sACN（现场灯收到两路交错指令）。QASCADE_ALLOW_MULTI=1 显式豁免。
    """
    if os.environ.get("QASCADE_ALLOW_MULTI", "") == "1":
        return True
    try:
        from PyQt6.QtCore import QLockFile, QStandardPaths
        _lock_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation)
        _lock = QLockFile(os.path.join(_lock_dir, "qascade-ultra.single-instance.lock"))
        # ★ R194/NB-02：不设 staleLockTime(0)——实测 Qt/Windows 陈旧判定按锁
        #   文件年龄，置 0 会让崩溃/断电残留锁永不回收（控制台永久无法启动）；
        #   Qt 默认 30s 足以拦截活双开（活持有者 tryLock 恒 False）。
        if _lock.tryLock(0):
            globals()["_single_instance_lock"] = _lock  # 进程生命周期内持有
            return True
        log.error("检测到雾白纪 ULTRA 已在运行（单实例守卫），本次启动退出。")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "雾白纪 ULTRA 已在运行。\n请使用已打开的窗口（双开会导致 DMX 输出冲突）。",
                "雾白纪 ULTRA", 0x00000010)
        except Exception:
            pass
        return False
    except Exception as _lock_err:
        log.warning("单实例守卫不可用（照常启动）: %s", _lock_err)
        return True


# ============================================================
# 主函数
# ============================================================
def main():
    """程序入口"""
    sys.excepthook = _handle_exception

    # ── 启动诊断持久化（2026-08-28 移到最前 + frozen 写 exe 目录）────────
    #   此前写 _ROOT：frozen 态 = _MEIPASS 临时解包目录，进程退出即清空，
    #   排障信息全丢。exe 目录才是可回收的落点（与 boot_fault.log 同目录）。
    def _trace(stage):
        try:
            import datetime as _dt
            _d = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else _ROOT
            with open(os.path.join(_d, "boot_trace.log"), "a", encoding="utf-8") as f:
                f.write("[" + _dt.datetime.now().strftime("%H:%M:%S") + "] " + stage + "\n")
        except Exception:
            pass
    _trace("enter-main")

    # ★ R193/IO-01：单实例守卫（_trace 定义之后才能调用）——双开=DMX 双写
    if not _acquire_single_instance_guard():
        _trace("single-instance: already running, exit")
        return

    # ══ Qascade CE 挂载（render_lab 隔离包; 失败零副作用; =0 一键回退）══
    _ce_status = None
    try:
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, os.path.join(_ROOT, "render_lab"))   # 开发态
        os.environ.setdefault("QASCADE_CT_ENGINE", "1")             # 正式版: 启动即 C 语义内核; =0 强制关回退
        from qascade_ce._mount_ce import _mount_ce
        _ce_status = _mount_ce()                                    # 一行挂载调用
    except Exception as _ce_mount_err:                              # 打包态未随包 qascade_ce 等
        log.warning("Qascade CE 挂载失败, 程序照常启动（原渲染路径）: %s", _ce_mount_err)
        _trace("ce-mount FAILED: %r" % (_ce_mount_err,))
    if _ce_status is not None:
        log.info("Qascade CE 挂载状态: %s", _ce_status.to_json())   # 启动日志一行 JSON
        try:
            _trace("ce-mount: active=%s backend=%s missing=%d fallback=%d"
                   % (getattr(_ce_status, "active", "?"),
                      getattr(_ce_status, "backend", "?"),
                      len(getattr(_ce_status, "missing", []) or []),
                      getattr(_ce_status, "fallback_count", -1)))
        except Exception:
            pass
        # ★ CE 接线契约摘要（2026-09-13 加）：把"哪些 hook 真被替换 / 有无目标
        #   缺失"写进 boot_trace。目标缺失 = src/ 改名导致脱靶（旧行为是静默
        #   回退、性能悄悄变慢）。落盘后任何一次启动都能一眼看出接线是否完整。
        try:
            import qascade_ce as _qce_mod
            _rep = getattr(_qce_mod, "_CE_CONTRACT_REPORT", {}) or {}
            _att = len(_rep.get("attached") or [])
            _miss = _rep.get("missing_target") or []
            _na = _rep.get("not_attached") or []
            _trace("ce-contract: attached=%d missing_target=%d not_attached=%d"
                   % (_att, len(_miss), len(_na)))
            if _miss:
                _trace("ce-contract!! 目标缺失(疑 src/ 改名): %s"
                       % [m.get("hook") for m in _miss[:8]])
        except Exception:
            pass
    else:
        _trace("ce-mount: None")

    # ── R183-J 启动环境行（backend / 物理核 / GPU 摘要）──────────────
    #   boot_trace 是 exe 旁可回收的持久证据: 目标机零操作核查 CE/GPU
    #   后端与算力档位。physical_cores() 走 psutil→Win32→逻辑//2 退化链;
    #   取不到 qascade_ce 时输出已加载 DLL/模块摘要（打包态排查用）。
    #   常规日志的完整资源行由 _mount_ce 打印, 此处不重复 log.*。
    try:
        _be = getattr(_ce_status, "backend", None) or "python-fallback"
        _phys, _gpu = 0, ""
        try:
            from qascade_ce.sysinfo import (physical_cores as _phys_fn,
                                            gpu_dll_status as _gpu_fn)
            _phys = _phys_fn()
            _gpu = _gpu_fn()
        except Exception:
            _ce_base = os.path.join(getattr(sys, "_MEIPASS", _ROOT), "qascade_ce")
            _dlls = [f for f in ("ce_engine_avx2.dll", "ce_engine_sse2.dll")
                     if os.path.exists(os.path.join(_ce_base, f))]
            _gpu = "dll=%s mods=%d" % (",".join(_dlls) or "无",
                                       sum(1 for m in sys.modules if "qascade_ce" in m))
        _trace("boot-env: backend=%s cores=%sP/%sL gpu=%s"
               % (_be, _phys or "?", os.cpu_count() or "?",
                  str(_gpu).replace(" ", "")))
    except Exception:
        pass

    app = _create_app(sys.argv)

    # The CE send-ring owns a native worker and must be stopped before Qt/Python
    # teardown releases its C buffers.  Window close reaches aboutToQuit after
    # the UI has stopped the NetworkManager; making the shutdown explicit keeps
    # the ring thread from touching freed native state during normal exit.
    def _shutdown_ce_pipeline():
        try:
            from qascade_ce import send_pipeline as _ce_send_pipeline
            _ce_send_pipeline.stop()
        except Exception:
            pass

    app.aboutToQuit.connect(_shutdown_ce_pipeline)

    _trace("splash-building")
    splash = _build_splash()
    _trace("splash-built")
    splash.show()
    _trace("splash-shown")
    app.processEvents()
    
    splash.showMessage("正在连接控制网络与设备通道…",
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                       QColor("#999999"))
    app.processEvents()
    nm = _load_network_manager()
    
    splash.showMessage("正在准备雾白纪控制核心…",
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                       QColor("#999999"))
    app.processEvents()
    pm = _load_project_manager()
    
    engine = _load_engine(nm, pm)
    
    splash.showMessage("正在加载专业工作界面…",
                       Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                       QColor("#999999"))
    app.processEvents()
    
    try:
        win = _build_ui(engine, nm)
    except Exception as e:
        log.critical("Failed to build UI:\n%s", traceback.format_exc())
        QMessageBox.critical(None, "启动失败", f"无法构建主界面:\n{e}")
        sys.exit(1)
    
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        try:
            win._load_project(sys.argv[1])
            log.info("Project loaded from CLI: %s", sys.argv[1])
        except Exception as e:
            log.warning("Could not load project from CLI: %s", e)
    
    win.show()
    _trace("win-shown")
    splash.begin_exit()
    log.info("Qascade (雾白纪 ULTRA) started successfully.")
    
    if (os.environ.get("QASCADE_R214_PASSIVE_CACHE_PROBE") == "1"
            and os.environ.get("QASCADE_R203_ACCEPTANCE") == "1"
            and os.environ.get("QASCADE_R203_SUITE") == "cache"):
        from tools._r214_cache_passive_probe import install as install_cache_probe
        install_cache_probe(app, win)

    if os.environ.get("QASCADE_R214_NATIVE_NETWORK") == "1":
        from tools._r214_native_network_smoke import install
        install(app, win)

    sys.exit(app.exec())


if __name__ == "__main__":
    # PyInstaller frozen + Windows spawn 并行渲染（时间线离线渲染多进程）:
    # 无 freeze_support 时子进程会重跑 main() 弹出整个 GUI, 并行路径挂起
    # 静默回退串行。必须最先执行（spawn 子进程在此被拦截为 worker 入口）。
    import multiprocessing
    multiprocessing.freeze_support()
    main()
