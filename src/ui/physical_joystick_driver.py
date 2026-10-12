"""
physical_joystick_driver.py
────────────────────────────────────────────────────────────────────────────
实体游戏摇杆驱动模块（pygame 后端）

功能：
  - 枚举所有已连接的游戏摇杆设备（支持 T16000M 等多轴摇杆）
  - 通过 QTimer 轮询轴值（25ms，40fps）
  - 发射 axis_changed(x, y) 信号供 BridgeJoystickPanel / ProgrammerPanel 接收
  - 支持轴绑定（哪个轴号对应 X，哪个轴号对应 Y）
  - 支持启用/停用
  - 支持死区（deadzone）过滤
  - 鼠标模拟模式：
      * 启用时将鼠标移至框选区域中心并模拟左键按下
      * 摇杆偏移方向控制鼠标在区域内的移动方向和速度
      * 摇杆回中时停止移动但保持左键按住
      * 停用时模拟左键松开
      * 鼠标本身不被禁用，随时可接管
  - 全局单例 get_physical_joystick_driver()

依赖：pygame（pip install pygame），Windows 下用 ctypes Win32 API 模拟鼠标
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import sys
import threading
from typing import Optional, List, Dict, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QSettings, QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────
_SETTINGS_ORG  = "Qascade"
_SETTINGS_APP  = "PhysicalJoystick"
_SETTINGS_KEY  = "joystick_config_v2"
_MOUSE_REG_KEY = "joystick_mouse_region_v1"

_POLL_INTERVAL_MS  = 25    # 摇杆轮询间隔（毫秒）
_MOUSE_INTERVAL_MS = 16    # 鼠标移动定时器间隔（约 60fps）
_DEFAULT_DEADZONE  = 0.05  # 默认死区（±5%）
_DEFAULT_SPEED     = 8.0   # 默认最大鼠标移动速度（像素/帧）


# ─────────────────────────────────────────────────────────────────────────────
#  Windows 鼠标模拟（ctypes Win32 SendInput）
# ─────────────────────────────────────────────────────────────────────────────
_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

    # INPUT 结构体常量
    _INPUT_MOUSE    = 0
    _MOUSEEVENTF_MOVE        = 0x0001
    _MOUSEEVENTF_LEFTDOWN    = 0x0002
    _MOUSEEVENTF_LEFTUP      = 0x0004
    _MOUSEEVENTF_ABSOLUTE    = 0x8000

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx",          ctypes.c_long),
            ("dy",          ctypes.c_long),
            ("mouseData",   ctypes.c_ulong),
            ("dwFlags",     ctypes.c_ulong),
            ("time",        ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]

    def _win_send_mouse_event(flags: int, dx: int = 0, dy: int = 0):
        """通过 SendInput 发送鼠标事件（相对移动或按键）。"""
        try:
            inp = _INPUT()
            inp.type = _INPUT_MOUSE
            inp._input.mi.dx = dx
            inp._input.mi.dy = dy
            inp._input.mi.mouseData = 0
            inp._input.mi.dwFlags = flags
            inp._input.mi.time = 0
            inp._input.mi.dwExtraInfo = None
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        except Exception as e:
            print(f"[PhysicalJoystick] SendInput 失败: {e}")

    def _mouse_left_down():
        _win_send_mouse_event(_MOUSEEVENTF_LEFTDOWN)

    def _mouse_left_up():
        _win_send_mouse_event(_MOUSEEVENTF_LEFTUP)

    def _mouse_move_relative(dx: int, dy: int):
        if dx != 0 or dy != 0:
            _win_send_mouse_event(_MOUSEEVENTF_MOVE, dx, dy)

else:
    # 非 Windows：用 QCursor 移动，左键按下/松开暂不支持（仅移动）
    def _mouse_left_down():
        pass

    def _mouse_left_up():
        pass

    def _mouse_move_relative(dx: int, dy: int):
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  pygame 安全初始化
# ─────────────────────────────────────────────────────────────────────────────
_pygame_ok   = False
_pygame_lock = threading.Lock()


def _init_pygame_joystick() -> bool:
    """线程安全地初始化 pygame joystick 子系统（不创建窗口）。"""
    global _pygame_ok
    with _pygame_lock:
        if _pygame_ok:
            return True
        try:
            import pygame
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            _pygame_ok = True
            return True
        except Exception as e:
            print(f"[PhysicalJoystick] pygame 初始化失败: {e}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  设备信息
# ─────────────────────────────────────────────────────────────────────────────
class JoystickDeviceInfo:
    """描述一个已连接的摇杆设备。"""
    def __init__(self, index: int, name: str, num_axes: int, num_buttons: int):
        self.index       = index
        self.name        = name
        self.num_axes    = num_axes
        self.num_buttons = num_buttons

    def __repr__(self) -> str:
        return (f"JoystickDeviceInfo(index={self.index}, name={self.name!r}, "
                f"axes={self.num_axes}, buttons={self.num_buttons})")


def enumerate_joysticks() -> List[JoystickDeviceInfo]:
    """枚举当前连接的所有摇杆设备，返回 JoystickDeviceInfo 列表。

    ★ 非破坏性扫描 — 不调用 quit()/init() 销毁活跃句柄。
    用幂等 init() 确保子系统已初始化，直接读取 get_count()。
    避免每 3 秒扫描导致活跃摇杆短暂"断联"。
    """
    if not _init_pygame_joystick():
        return []
    try:
        import pygame
        # 幂等初始化（不销毁已有句柄）
        if not pygame.joystick.get_init():
            pygame.joystick.init()
        count = pygame.joystick.get_count()
        result = []
        for i in range(count):
            try:
                joy = pygame.joystick.Joystick(i)
                joy.init()
                result.append(JoystickDeviceInfo(
                    index=i,
                    name=joy.get_name(),
                    num_axes=joy.get_numaxes(),
                    num_buttons=joy.get_numbuttons(),
                ))
            except Exception:
                pass
        return result
    except Exception as e:
        print(f"[PhysicalJoystick] 枚举设备失败: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  PhysicalJoystickDriver
# ─────────────────────────────────────────────────────────────────────────────
class PhysicalJoystickDriver(QObject):
    """
    实体摇杆驱动器（全局单例）。

    信号：
      axis_changed(x: float, y: float)
          — x/y 均为 [0.0, 1.0]，中心为 0.5，经死区处理后发射。
      device_list_changed(devices: list)
          — 设备列表刷新时发射（list of JoystickDeviceInfo）。
      connection_status_changed(connected: bool, name: str)
          — 设备连接/断开时发射。
      mouse_mode_changed(enabled: bool)
          — 鼠标模拟模式切换时发射。
    """
    axis_changed              = pyqtSignal(float, float)   # (x, y) in [0,1]
    axis3d_changed           = pyqtSignal(float, float, float, float)  # (x, y, z, w) in [0,1]
    device_list_changed       = pyqtSignal(list)            # List[JoystickDeviceInfo]
    connection_status_changed = pyqtSignal(bool, str)       # (connected, device_name)
    mouse_mode_changed        = pyqtSignal(bool)            # (enabled)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled:       bool  = False
        self._device_index:  int   = -1
        self._axis_x:        int   = 0
        self._axis_y:        int   = 1
        self._axis_z:        int   = 2
        self._axis_w:        int   = 3
        self._deadzone:      float = _DEFAULT_DEADZONE
        self._invert_x:      bool  = False
        self._invert_y:      bool  = False
        self._invert_z:      bool  = False
        self._invert_w:      bool  = False
        self._last_x:        float = 0.5
        self._last_y:        float = 0.5
        self._last_z:        float = 0.5
        self._last_w:        float = 0.5
        self._joystick             = None  # pygame.joystick.Joystick
        self._devices:       List[JoystickDeviceInfo] = []

        # ── 鼠标模拟相关 ──────────────────────────────────────────────────────
        self._mouse_mode:    bool  = False   # 是否启用鼠标模拟模式
        self._mouse_pressed: bool  = False   # 当前是否已模拟按下左键
        self._mouse_speed:   float = _DEFAULT_SPEED  # 最大移动速度（像素/帧）
        # 框选区域 {x, y, w, h}（屏幕坐标）
        self._mouse_region:  Optional[Dict[str, int]] = None
        # 当前鼠标在区域内的浮点坐标（用于亚像素累积）
        self._cur_fx: float = 0.0
        self._cur_fy: float = 0.0

        # 摇杆轮询定时器
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        # 鼠标移动定时器（独立于摇杆轮询，更高频率）
        self._mouse_timer = QTimer(self)
        self._mouse_timer.setInterval(_MOUSE_INTERVAL_MS)
        self._mouse_timer.timeout.connect(self._mouse_tick)

        # 设备扫描定时器（每 3 秒热插拔检测）
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(3000)
        self._scan_timer.timeout.connect(self.refresh_devices)
        self._scan_timer.start()

        # 初始化：首次设备枚举含 pygame.init(~0.4s)+硬件扫描，会阻塞面板打开。
        # 延到事件循环下一拍（singleShot 0）执行——面板瞬开，扫描随后完成；
        # 摇杆是可选功能，无需在构造时同步等硬件。settings 轻量，仍同步。
        self._load_settings()
        QTimer.singleShot(0, self.refresh_devices)

    # ── 设备管理 ─────────────────────────────────────────────────────────────

    def refresh_devices(self):
        """扫描设备列表。非破坏性扫描 + 智能重连。

        ★ 修复: enumerate_joysticks 不再销毁活跃句柄, 因此如果设备索引未变,
        完全跳过重连, 保持轮询不中断。仅在设备真正变化时才重建连接。
        """
        saved_idx = self._device_index
        was_enabled = self._enabled

        new_devices = enumerate_joysticks()
        old_names = sorted(d.name for d in self._devices)
        new_names = sorted(d.name for d in new_devices)
        self._devices = new_devices
        if old_names != new_names:
            self.device_list_changed.emit(list(self._devices))

        if saved_idx < 0 or self._joystick is None:
            return

        # 设备仍在 → 检查是否需要重连
        still_present = any(d.index == saved_idx for d in self._devices)
        if not still_present:
            self._disconnect_device()
            return

        # ★ 修复: 句柄未被销毁（非破坏性扫描），且同一设备仍在线 → 无需重连
        # 仅当 joystick 句柄异常时才重建（通过检查 get_init 状态）
        try:
            if self._joystick.get_init():
                return  # 句柄有效, 无需任何操作
        except Exception:
            pass  # 句柄异常, 需要重建

        # 句柄异常, 执行重建
        self._timer.stop()
        try:
            import pygame
            self._joystick = pygame.joystick.Joystick(saved_idx)
            self._joystick.init()
            self._device_index = saved_idx
            # ★ 修复3: 重连后跳过前3帧, 等pygame稳定再发射信号
            self._skip_frames = 3
            if was_enabled:
                self._timer.start()
            self.connection_status_changed.emit(
                True, self._joystick.get_name())
        except Exception:
            self._disconnect_device()

    def get_devices(self) -> List[JoystickDeviceInfo]:
        return list(self._devices)

    def get_device_info(self, index: int) -> Optional[JoystickDeviceInfo]:
        for d in self._devices:
            if d.index == index:
                return d
        return None

    def select_device(self, device_index: int):
        if device_index == self._device_index:
            return
        self._disconnect_device()
        # Reset last values so mouse tick / first poll use neutral position
        self._last_x = self._last_y = self._last_z = self._last_w = 0.5
        if device_index < 0:
            self._device_index = -1
            return
        try:
            import pygame
            joy = pygame.joystick.Joystick(device_index)
            joy.init()
            self._joystick     = joy
            self._device_index = device_index
            num_axes = joy.get_numaxes()
            self._axis_x = 0 if num_axes > 0 else -1
            self._axis_y = 1 if num_axes > 1 else -1
            self._axis_z = 2 if num_axes > 2 else -1
            self._axis_w = 3 if num_axes > 3 else -1
            self.connection_status_changed.emit(True, joy.get_name())
            if self._enabled:
                self._timer.start()
        except Exception as e:
            print(f"[PhysicalJoystick] 连接设备 {device_index} 失败: {e}")
            self._device_index = -1
        self._save_settings()

    def _disconnect_device(self):
        if self._joystick is not None:
            try:
                self._joystick.quit()
            except Exception:
                pass
            self._joystick = None
        old_index = self._device_index
        self._device_index = -1
        self._timer.stop()
        # 如果鼠标模式激活，停用时松开左键
        if self._mouse_pressed:
            self._do_mouse_up()
        if old_index >= 0:
            self.connection_status_changed.emit(False, "")

    # ── 轴绑定 ───────────────────────────────────────────────────────────────

    def set_axis_x(self, axis_num: int):
        self._axis_x = axis_num
        self._save_settings()

    def set_axis_y(self, axis_num: int):
        self._axis_y = axis_num
        self._save_settings()

    def set_invert_x(self, invert: bool):
        self._invert_x = invert
        self._save_settings()

    def set_invert_y(self, invert: bool):
        self._invert_y = invert
        self._save_settings()

    def set_axis_z(self, axis_num: int):
        self._axis_z = axis_num
        self._save_settings()

    def get_axis_z(self) -> int:
        return self._axis_z

    def set_invert_z(self, invert: bool):
        self._invert_z = invert
        self._save_settings()

    def get_invert_z(self) -> bool:
        return self._invert_z

    def set_axis_w(self, axis_num: int):
        self._axis_w = axis_num
        self._save_settings()

    def get_axis_w(self) -> int:
        return self._axis_w

    def set_invert_w(self, invert: bool):
        self._invert_w = invert
        self._save_settings()

    def get_invert_w(self) -> bool:
        return self._invert_w

    def set_deadzone(self, deadzone: float):
        self._deadzone = max(0.0, min(0.5, deadzone))
        self._save_settings()

    def get_axis_x(self) -> int:
        return self._axis_x

    def get_axis_y(self) -> int:
        return self._axis_y

    def get_deadzone(self) -> float:
        return self._deadzone

    # ── 鼠标模拟 ─────────────────────────────────────────────────────────────

    def set_mouse_mode(self, enabled: bool):
        """
        启用/停用鼠标模拟模式。
        启用时：将鼠标移至框选区域中心，模拟左键按下。
        停用时：模拟左键松开。
        """
        if self._mouse_mode == enabled:
            return
        self._mouse_mode = enabled
        if enabled:
            # 将鼠标移至区域中心
            region = self._get_region()
            cx = region["x"] + region["w"] // 2
            cy = region["y"] + region["h"] // 2
            QCursor.setPos(cx, cy)
            # 初始化浮点坐标为区域中心
            self._cur_fx = float(cx)
            self._cur_fy = float(cy)
            # 模拟左键按下
            self._do_mouse_down()
            # 启动鼠标移动定时器
            self._mouse_timer.start()
        else:
            # 模拟左键松开
            self._do_mouse_up()
            self._mouse_timer.stop()
        self._save_settings()
        self.mouse_mode_changed.emit(enabled)

    def is_mouse_mode(self) -> bool:
        return self._mouse_mode

    def set_mouse_speed(self, speed: float):
        """设置最大鼠标移动速度（像素/帧，建议 2~30）。"""
        self._mouse_speed = max(1.0, min(100.0, speed))
        self._save_settings()

    def get_mouse_speed(self) -> float:
        return self._mouse_speed

    def set_mouse_region(self, region: Dict[str, int]):
        """设置框选区域 {x, y, w, h}（屏幕坐标）。"""
        self._mouse_region = {
            "x": int(region.get("x", 0)),
            "y": int(region.get("y", 0)),
            "w": max(8, int(region.get("w", 8))),
            "h": max(8, int(region.get("h", 8))),
        }
        self._save_settings()

    def get_mouse_region(self) -> Optional[Dict[str, int]]:
        return dict(self._mouse_region) if self._mouse_region else None

    def _get_region(self) -> Dict[str, int]:
        """获取当前框选区域，若未设置则返回全屏。"""
        if self._mouse_region:
            return dict(self._mouse_region)
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.geometry()
            return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}

    def _do_mouse_down(self):
        """模拟鼠标左键按下。"""
        if not self._mouse_pressed:
            _mouse_left_down()
            self._mouse_pressed = True

    def _do_mouse_up(self):
        """模拟鼠标左键松开。"""
        if self._mouse_pressed:
            _mouse_left_up()
            self._mouse_pressed = False

    def _mouse_tick(self):
        """
        鼠标移动定时器回调（约 60fps）。
        根据当前摇杆偏移量（_last_x, _last_y）计算移动量，
        在框选区域内移动鼠标。
        摇杆值 0.5 = 中心 = 不移动；偏移越大移动越快。
        """
        if not self._mouse_mode:
            return

        # 摇杆偏移量（-0.5 ~ +0.5，中心为 0）
        ox = self._last_x - 0.5   # 负=左，正=右
        oy = self._last_y - 0.5   # 负=上，正=下

        # 死区已在 _poll 中处理，这里直接使用
        # 速度 = 偏移量 * 最大速度（像素/帧）
        vx = ox * 2.0 * self._mouse_speed   # ox 最大 ±0.5，乘以 2 归一化到 ±1
        vy = oy * 2.0 * self._mouse_speed

        if abs(vx) < 0.01 and abs(vy) < 0.01:
            return  # 摇杆在死区内，不移动

        # 获取区域边界
        region = self._get_region()
        rx, ry = region["x"], region["y"]
        rw, rh = region["w"], region["h"]
        x_min, x_max = float(rx), float(rx + rw - 1)
        y_min, y_max = float(ry), float(ry + rh - 1)

        # 读取当前鼠标位置（用于初始化 _cur_fx/_cur_fy）
        # 若 _cur_fx 超出区域（可能是外部鼠标移动导致），重新同步
        cur_pos = QCursor.pos()
        actual_x = float(cur_pos.x())
        actual_y = float(cur_pos.y())

        # 如果实际鼠标位置与记录偏差超过 3px（用户移动了鼠标），同步记录
        if abs(actual_x - self._cur_fx) > 3 or abs(actual_y - self._cur_fy) > 3:
            self._cur_fx = actual_x
            self._cur_fy = actual_y

        # 计算新位置（亚像素累积）
        new_fx = self._cur_fx + vx
        new_fy = self._cur_fy + vy

        # 限制在区域内
        new_fx = max(x_min, min(x_max, new_fx))
        new_fy = max(y_min, min(y_max, new_fy))

        # 计算实际像素移动量（整数）
        dx = int(new_fx) - int(self._cur_fx)
        dy = int(new_fy) - int(self._cur_fy)

        # 更新记录
        self._cur_fx = new_fx
        self._cur_fy = new_fy

        # 移动鼠标（相对移动，保持左键按住状态）
        if dx != 0 or dy != 0:
            _mouse_move_relative(dx, dy)

    # ── 启用/停用 ─────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        """启用或停用摇杆驱动。"""
        self._enabled = enabled
        if enabled and self._device_index >= 0 and self._joystick is not None:
            self._timer.start()
        else:
            self._timer.stop()
            if not enabled:
                self.axis_changed.emit(0.5, 0.5)
                self.axis3d_changed.emit(0.5, 0.5, 0.5, 0.5)
                # 若鼠标模式激活，停用摇杆时也停用鼠标模式
                if self._mouse_mode:
                    self.set_mouse_mode(False)
        self._save_settings()

    def is_enabled(self) -> bool:
        return self._enabled

    def is_connected(self) -> bool:
        return self._device_index >= 0 and self._joystick is not None

    # ── 轮询 ─────────────────────────────────────────────────────────────────

    def _poll(self):
        """每 25ms 轮询一次摇杆轴值。"""
        if not self._enabled or self._joystick is None:
            return
        # ★ 修复3: 重连后跳过静默帧, 避免新句柄首次读返回中位导致断联
        skip = getattr(self, '_skip_frames', 0)
        try:
            import pygame
            pygame.event.pump()
            x_raw = 0.0
            if 0 <= self._axis_x < self._joystick.get_numaxes():
                x_raw = float(self._joystick.get_axis(self._axis_x))
            y_raw = 0.0
            if 0 <= self._axis_y < self._joystick.get_numaxes():
                y_raw = float(self._joystick.get_axis(self._axis_y))
            z_raw = 0.0
            if 0 <= self._axis_z < self._joystick.get_numaxes():
                z_raw = float(self._joystick.get_axis(self._axis_z))
            w_raw = 0.0
            if 0 <= self._axis_w < self._joystick.get_numaxes():
                w_raw = float(self._joystick.get_axis(self._axis_w))
            x_raw = self._apply_deadzone(x_raw)
            y_raw = self._apply_deadzone(y_raw)
            z_raw = self._apply_deadzone(z_raw)
            w_raw = self._apply_deadzone(w_raw)
            if self._invert_x:
                x_raw = -x_raw
            if self._invert_y:
                y_raw = -y_raw
            if self._invert_z:
                z_raw = -z_raw
            if self._invert_w:
                w_raw = -w_raw
            x = max(0.0, min(1.0, (x_raw + 1.0) / 2.0))
            y = max(0.0, min(1.0, (y_raw + 1.0) / 2.0))
            z = max(0.0, min(1.0, (z_raw + 1.0) / 2.0))
            w = max(0.0, min(1.0, (w_raw + 1.0) / 2.0))
            # ★ 修复3: 静默帧 — 重连后前几帧只更新 _last 值, 不发射信号
            if skip > 0:
                self._skip_frames = skip - 1
                self._last_x, self._last_y = x, y
                self._last_z, self._last_w = z, w
                return
            # 发射信号（只在值变化时）
            xy_changed = abs(x - self._last_x) > 0.001 or abs(y - self._last_y) > 0.001
            z_changed = abs(z - self._last_z) > 0.001
            w_changed = abs(w - self._last_w) > 0.001
            if xy_changed:
                self.axis_changed.emit(x, y)
            if xy_changed or z_changed or w_changed:
                self.axis3d_changed.emit(x, y, z, w)
            # 更新最新轴值（供鼠标定时器使用，必须在发射信号后更新）
            self._last_x = x
            self._last_y = y
            self._last_z = z
            self._last_w = w
        except Exception as e:
            print(f"[PhysicalJoystick] 轮询失败: {e}")
            self._disconnect_device()

    def _apply_deadzone(self, value: float) -> float:
        dz = self._deadzone
        if abs(value) < dz:
            return 0.0
        sign = 1.0 if value > 0 else -1.0
        return sign * (abs(value) - dz) / (1.0 - dz)

    # ── 持久化 ───────────────────────────────────────────────────────────────

    def _save_settings(self):
        try:
            import json
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            data = {
                "enabled":       self._enabled,
                "device_index":  self._device_index,
                "axis_x":        self._axis_x,
                "axis_y":        self._axis_y,
                "axis_z":        self._axis_z,
                "axis_w":        self._axis_w,
                "invert_x":      self._invert_x,
                "invert_y":      self._invert_y,
                "invert_z":      self._invert_z,
                "invert_w":      self._invert_w,
                "deadzone":      self._deadzone,
                "mouse_mode":    self._mouse_mode,
                "mouse_speed":   self._mouse_speed,
                "mouse_region":  self._mouse_region,
            }
            s.setValue(_SETTINGS_KEY, json.dumps(data))
        except Exception:
            pass

    def _load_settings(self):
        try:
            import json
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            raw = s.value(_SETTINGS_KEY, "", type=str)
            if not raw:
                return
            data = json.loads(raw)
            self._axis_x       = int(data.get("axis_x", 0))
            self._axis_y       = int(data.get("axis_y", 1))
            self._axis_z       = int(data.get("axis_z", 2))
            self._axis_w       = int(data.get("axis_w", 3))
            self._invert_x     = bool(data.get("invert_x", False))
            self._invert_y     = bool(data.get("invert_y", False))
            self._invert_z     = bool(data.get("invert_z", False))
            self._invert_w     = bool(data.get("invert_w", False))
            self._deadzone     = float(data.get("deadzone", _DEFAULT_DEADZONE))
            self._mouse_speed  = float(data.get("mouse_speed", _DEFAULT_SPEED))
            region = data.get("mouse_region", None)
            if isinstance(region, dict):
                self._mouse_region = {
                    "x": int(region.get("x", 0)),
                    "y": int(region.get("y", 0)),
                    "w": max(8, int(region.get("w", 8))),
                    "h": max(8, int(region.get("h", 8))),
                }
            # 不自动恢复 enabled、device_index、mouse_mode（需要用户手动选择）
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
_global_driver: Optional[PhysicalJoystickDriver] = None


def get_physical_joystick_driver() -> PhysicalJoystickDriver:
    """获取全局实体摇杆驱动器单例（**自愈**：C++ 侧已销毁则重建）。

    ★ 2026-09-20：与 `get_joystick_bind_manager` 同因 —— 无父单例可能被宿主
      销毁连带删除，模块全局仍指着悬空包装；检测并重建，避免调用方拿到
      已销毁对象（实测会静默打断面板构造）。
    """
    global _global_driver
    if _global_driver is not None:
        try:
            from PyQt6 import sip
            if not sip.isdeleted(_global_driver):
                return _global_driver
        except Exception:
            return _global_driver
    _global_driver = PhysicalJoystickDriver()
    return _global_driver
