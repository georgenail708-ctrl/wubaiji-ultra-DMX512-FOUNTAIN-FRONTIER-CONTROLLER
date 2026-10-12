"""
midi_joystick_driver.py
MIDI 摇杆驱动模块（pygame.midi 后端）

功能：
  - 枚举所有已连接的 MIDI 输入设备
  - 通过 QTimer 轮询 CC 消息（25ms，40fps）
  - 配置 CC 号到 X/Y/Z 轴的映射
  - 发射 axis3d_changed(x, y, z) 信号供绑定引擎接收
  - 支持死区过滤、反转、MIDI 通道选择
  - 支持启用/停用
  - 全局单例 get_midi_joystick_driver()

依赖：pygame.midi（pygame 2.6.1 内置，无需额外安装）
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QSettings

_SETTINGS_ORG = "Qascade"
_SETTINGS_APP = "MIDIJoystick"
_SETTINGS_KEY = "midi_joystick_config_v1"
_POLL_INTERVAL_MS = 25
_DEFAULT_DEADZONE = 0.05


# -- pygame.midi 安全初始化 ------------------------------------------------
_pygame_midi_ok = False
_pygame_midi_lock = threading.Lock()


def _init_midi() -> bool:
    """线程安全初始化 pygame.midi（不创建窗口）。"""
    global _pygame_midi_ok
    with _pygame_midi_lock:
        if _pygame_midi_ok:
            return True
        try:
            import pygame.midi  # noqa: F811 — must import submodule explicitly
            if not pygame.midi.get_init():
                pygame.midi.init()
            _pygame_midi_ok = True
            return True
        except Exception as e:
            print(f"[MIDIJoystick] pygame.midi 初始化失败: {e}")
            return False


# -- 设备信息 ---------------------------------------------------------------


@dataclass
class MIDIDeviceInfo:
    """描述一个 MIDI 输入设备。"""
    device_id: int
    name: str
    interface: str
    is_input: bool

    def __repr__(self) -> str:
        return (f"MIDIDeviceInfo(id={self.device_id}, name={self.name!r}, "
                f"input={self.is_input})")


def enumerate_midi_inputs() -> List[MIDIDeviceInfo]:
    """枚举所有 MIDI 输入设备。

    ★ 非破坏性扫描 — 不调用 quit()/init() 销毁活跃句柄。
    幂等 init() 确保子系统已初始化后直接读取 get_count()，
    避免每 3 秒扫描导致活跃 MIDI 连接周期性拆建。
    """
    if not _init_midi():
        return []
    try:
        import pygame.midi
        # 幂等初始化（不销毁已有句柄）
        if not pygame.midi.get_init():
            pygame.midi.init()
        count = pygame.midi.get_count()
        result = []
        for i in range(count):
            info = pygame.midi.get_device_info(i)
            if info is None:
                continue
            interf, name, is_input, is_output, opened = info
            if is_input:
                result.append(MIDIDeviceInfo(
                    device_id=i,
                    name=name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name),
                    interface=interf.decode("utf-8", errors="replace") if isinstance(interf, bytes) else str(interf),
                    is_input=bool(is_input),
                ))
        return result
    except Exception as e:
        print(f"[MIDIJoystick] 枚举设备失败: {e}")
        return []


# -- MIDIJoystickDriver -----------------------------------------------------


class MIDIJoystickDriver(QObject):
    """
    MIDI 摇杆驱动器（全局单例）。

    信号：
      axis3d_changed(x: float, y: float, z: float)
          — x/y/z 均为 [0.0, 1.0]，中心为 0.5，经死区处理后发射。
      device_list_changed(devices: list)
          — 设备列表刷新时发射（list of MIDIDeviceInfo）。
      connection_status_changed(connected: bool, name: str)
          — 设备连接/断开时发射。
    """
    axis3d_changed = pyqtSignal(float, float, float)
    device_list_changed = pyqtSignal(list)
    connection_status_changed = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled: bool = False
        self._device_id: int = -1
        self._midi_input = None  # pygame.midi.Input
        self._devices: List[MIDIDeviceInfo] = []

        # CC-to-axis mapping (default: CC1=X, CC2=Y, CC3=Z)
        self._cc_map: Dict[str, int] = {"x": 1, "y": 2, "z": 3}
        self._midi_channel: int = 0  # 0-15, matches MIDI channel 1-16

        # Auto-learn: detect device channel and CC mapping automatically
        self._auto_learn: bool = True
        self._learn_samples: int = 0
        self._learn_channel_counts: Dict[int, int] = {}  # channel → count
        self._learn_cc_counts: Dict[int, int] = {}       # cc_num → count
        self._LEARN_TARGET = 60  # samples before auto-configuring
        self._invert_x: bool = False
        self._invert_y: bool = False
        self._invert_z: bool = False
        self._deadzone: float = _DEFAULT_DEADZONE
        self._last_x: float = 0.5
        self._last_y: float = 0.5
        self._last_z: float = 0.5

        # Return-to-center: if no update for an axis within this time, snap to 0.5
        self._axis_last_update: Dict[str, float] = {}  # axis → monotonic time
        self._RETURN_TO_CENTER_MS = 200  # ms before idle axis returns to center

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(3000)
        self._scan_timer.timeout.connect(self.refresh_devices)
        self._scan_timer.start()

        self.refresh_devices()
        self._load_settings()

    # -- 设备管理 -----------------------------------------------------------

    def refresh_devices(self):
        """扫描 MIDI 设备列表。非破坏性扫描，仅设备拓扑变化才重建连接。"""
        saved_id = self._device_id

        new_devices = enumerate_midi_inputs()
        old_ids = sorted(d.device_id for d in self._devices)
        new_ids = sorted(d.device_id for d in new_devices)
        topology_changed = old_ids != new_ids
        self._devices = new_devices
        if topology_changed:
            self.device_list_changed.emit(list(self._devices))

        # 无活动输入 → 无需重建
        if saved_id < 0 or self._midi_input is None:
            return

        # 有活动输入且拓扑未变化 → 保持连接不拆不建（消除每 3s 周期性拆建）
        if not topology_changed:
            return

        # 拓扑变化但所选设备仍在 → 非破坏性扫描未销毁句柄，活动输入仍有效，不拆
        if any(d.device_id == saved_id for d in self._devices):
            return

        # 所选设备已消失 → 断开
        self._disconnect_device()

    def get_devices(self) -> List[MIDIDeviceInfo]:
        return list(self._devices)

    def get_device_info(self, device_id: int) -> Optional[MIDIDeviceInfo]:
        for d in self._devices:
            if d.device_id == device_id:
                return d
        return None

    def select_device(self, device_id: int):
        if device_id == self._device_id and self._midi_input is not None:
            return
        self._disconnect_device()
        if device_id < 0:
            return
        try:
            import pygame.midi
            if not pygame.midi.get_init():
                pygame.midi.init()
            self._midi_input = pygame.midi.Input(device_id)
            self._device_id = device_id
            self._reset_auto_learn()
            info = self.get_device_info(device_id)
            name = info.name if info else str(device_id)
            self.connection_status_changed.emit(True, name)
            if self._enabled:
                self._timer.start()
        except Exception as e:
            print(f"[MIDIJoystick] 连接设备 {device_id} 失败: {e}")
            self._disconnect_device()

    def _disconnect_device(self):
        if self._midi_input is not None:
            try:
                self._midi_input.close()
            except Exception:
                pass
            self._midi_input = None
        old_id = self._device_id
        self._device_id = -1
        self._timer.stop()
        self._reset_auto_learn()
        if old_id >= 0:
            self.connection_status_changed.emit(False, "")

    def _reset_auto_learn(self):
        """Reset auto-learn state (called on new connection)."""
        self._auto_learn = True
        self._learn_samples = 0
        self._learn_channel_counts = {}
        self._learn_cc_counts = {}
        self._axis_last_update = {}  # reset return-to-center timers

    def _apply_auto_learn(self):
        """Auto-detect channel and CC mapping from observed messages."""
        self._auto_learn = False
        # Pick most common channel
        if self._learn_channel_counts:
            best_ch = max(self._learn_channel_counts, key=self._learn_channel_counts.get)
            self._midi_channel = best_ch
        # Pick top 3 most active CC numbers → X, Y, Z
        if self._learn_cc_counts:
            sorted_ccs = sorted(self._learn_cc_counts.items(), key=lambda x: -x[1])
            axes = ["x", "y", "z"]
            for i, (cc, _) in enumerate(sorted_ccs[:3]):
                if i < len(axes):
                    self._cc_map[axes[i]] = cc
        self._save_settings()
        print(f"[MIDIJoystick] Auto-learned: ch={self._midi_channel+1} "
              f"CC map={self._cc_map}")

    # -- CC 映射 ------------------------------------------------------------

    def set_cc_mapping(self, axis: str, cc_num: int):
        if axis in self._cc_map:
            self._cc_map[axis] = max(0, min(127, cc_num))
            self._save_settings()

    def get_cc_mapping(self, axis: str) -> int:
        return self._cc_map.get(axis, -1)

    def set_midi_channel(self, channel: int):
        self._midi_channel = max(0, min(15, channel))
        self._save_settings()

    def get_midi_channel(self) -> int:
        return self._midi_channel

    def set_invert_x(self, invert: bool):
        self._invert_x = invert
        self._save_settings()

    def set_invert_y(self, invert: bool):
        self._invert_y = invert
        self._save_settings()

    def set_invert_z(self, invert: bool):
        self._invert_z = invert
        self._save_settings()

    def get_invert_x(self) -> bool:
        return self._invert_x

    def get_invert_y(self) -> bool:
        return self._invert_y

    def get_invert_z(self) -> bool:
        return self._invert_z

    def set_deadzone(self, deadzone: float):
        self._deadzone = max(0.0, min(0.5, deadzone))
        self._save_settings()

    def get_deadzone(self) -> float:
        return self._deadzone

    # -- 启用/停用 ----------------------------------------------------------

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if enabled and self._device_id >= 0 and self._midi_input is not None:
            self._timer.start()
        else:
            self._timer.stop()
            if not enabled:
                self.axis3d_changed.emit(0.5, 0.5, 0.5)
        self._save_settings()

    def is_enabled(self) -> bool:
        return self._enabled

    def is_connected(self) -> bool:
        return self._device_id >= 0 and self._midi_input is not None

    # -- 轮询 ---------------------------------------------------------------

    def _poll(self):
        """每 25ms 轮询一次 MIDI 输入。"""
        if not self._enabled or self._midi_input is None:
            return
        try:
            import pygame.midi
            if not pygame.midi.get_init():
                pygame.midi.init()

            if not self._midi_input.poll():
                return

            events = self._midi_input.read(1024)
            axis_values: Dict[str, float] = {"x": self._last_x, "y": self._last_y, "z": self._last_z}
            changed = False

            for event in events:
                data = event[0]  # [status, data1, data2, data3]
                status = data[0] & 0xF0
                channel = data[0] & 0x0F
                cc_num = data[1]
                cc_val = data[2]

                # Auto-learn mode: collect statistics to detect device config
                if self._auto_learn and status == 0xB0:
                    self._learn_channel_counts[channel] = \
                        self._learn_channel_counts.get(channel, 0) + 1
                    self._learn_cc_counts[cc_num] = \
                        self._learn_cc_counts.get(cc_num, 0) + 1
                    self._learn_samples += 1
                    if self._learn_samples >= self._LEARN_TARGET:
                        self._apply_auto_learn()
                    continue  # don't route during learning

                if status != 0xB0 or channel != self._midi_channel:
                    continue  # not a CC message on our channel
                normalized = max(0.0, min(1.0, cc_val / 127.0))

                for axis_name, mapped_cc in self._cc_map.items():
                    if cc_num != mapped_cc:
                        continue
                    # Apply deadzone
                    dz = self._deadzone
                    if abs(normalized - 0.5) < dz / 2.0:
                        normalized = 0.5
                    # Apply inversion
                    invert = getattr(self, f"_invert_{axis_name}", False)
                    if invert:
                        normalized = 1.0 - normalized
                    axis_values[axis_name] = normalized
                    self._axis_last_update[axis_name] = time.monotonic()
                    changed = True

            # Return-to-center: snap idle axes back to 0.5
            now = time.monotonic()
            for ax in ("x", "y", "z"):
                last = self._axis_last_update.get(ax, 0)
                if now - last > self._RETURN_TO_CENTER_MS / 1000.0:
                    if abs(axis_values[ax] - 0.5) > 0.01:
                        axis_values[ax] = 0.5
                        changed = True

            if changed:
                self._last_x = axis_values["x"]
                self._last_y = axis_values["y"]
                self._last_z = axis_values["z"]
                self.axis3d_changed.emit(self._last_x, self._last_y, self._last_z)

        except Exception as e:
            print(f"[MIDIJoystick] 轮询失败: {e}")
            self._disconnect_device()

    # -- 持久化 -------------------------------------------------------------

    def _save_settings(self):
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            data = {
                "enabled": self._enabled,
                "device_id": self._device_id,
                "cc_map": dict(self._cc_map),
                "midi_channel": self._midi_channel,
                "invert_x": self._invert_x,
                "invert_y": self._invert_y,
                "invert_z": self._invert_z,
                "deadzone": self._deadzone,
            }
            s.setValue(_SETTINGS_KEY, json.dumps(data))
        except Exception:
            pass

    def _load_settings(self):
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            raw = s.value(_SETTINGS_KEY, "", type=str)
            if not raw:
                return
            data = json.loads(raw)
            self._cc_map = {k: int(v) for k, v in data.get("cc_map", {"x": 1, "y": 2, "z": 3}).items()}
            self._midi_channel = int(data.get("midi_channel", 0))
            self._invert_x = bool(data.get("invert_x", False))
            self._invert_y = bool(data.get("invert_y", False))
            self._invert_z = bool(data.get("invert_z", False))
            self._deadzone = float(data.get("deadzone", _DEFAULT_DEADZONE))
        except Exception:
            pass


# -- 全局单例 ---------------------------------------------------------------
_global_midi_driver: Optional[MIDIJoystickDriver] = None


def get_midi_joystick_driver() -> MIDIJoystickDriver:
    global _global_midi_driver
    if _global_midi_driver is None:
        _global_midi_driver = MIDIJoystickDriver()
    return _global_midi_driver
