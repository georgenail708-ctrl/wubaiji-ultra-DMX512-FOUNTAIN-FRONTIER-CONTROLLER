"""
AudioRefTrack — 时间线音频参考轨
功能：
  1. 导入 MP3/WAV/FLAC/OGG 音频文件
  2. 在时间线上显示波形（与 ClipArea 同步缩放/滚动）
  3. 同步播放（随时间线播放头一起播放）
  4. 支持静音/取消静音
  5. ★ 新增：支持删除已导入的音频参考轨
"""
from __future__ import annotations
import os
import threading
import time
from typing import Optional

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRect, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont,
    QPixmap, QLinearGradient, QPolygonF,
)

from .. import qascade_theme as _th   # R183 横向性能批：cached_font（paint 字体缓存）
from .waveform_loader import WaveformLoader
from .waveform_load_session import WaveformLoadSession

try:
    from ..main_icon_library import make_main_icon as _make_icon
except ImportError:
    _make_icon = None

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QFileDialog,
    QSizePolicy, QFrame, QMessageBox,
)

# ──────────────────────────────────────────────────────────────────────────────
# 波形绘制视图
# ──────────────────────────────────────────────────────────────────────────────
class WaveformView(QWidget):
    """绘制音频波形，支持缩放和滚动。

    ★ 音频 clip 化（2026-08-04）：
      - 导入的音频在底部音频参考轨上呈现为一个可编辑 clip（波形 + 边框 + 淡入淡出帘）。
      - 拖拽 clip 主体 → 调整位置（start_time）；拖拽两端菱形手柄 → 淡入/淡出时长。
      - clip 只属于音频参考轨：不进入 session.tracks，不存在跨轨移动路径（锁定轨道）。
      - 波形渲染缓存：峰值数据由 WaveformLoader 一次性计算；绘制时把整段波形预渲染
        成 QPixmap（按 音频时长/峰值数/视图高度 缓存），缩放/滚动/重绘只做源矩形映射，
        不重算波形。仅音频文件更换或视图高度变化才重渲染。
    """
    clip_edit_committed = pyqtSignal(float, float, float)  # start_time, fade_in, fade_out

    FADE_HANDLE_W = 8
    _PIXMAP_BASE_PPS = 100.0   # 波形缓存位图的基础密度（像素/秒）
    _PIXMAP_MAX_W = 24000      # 缓存位图最大宽度（上限防爆内存）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peaks: Optional[np.ndarray] = None
        self._duration: float = 0.0
        self._px_per_sec: float = 100.0
        self._scroll_x: float = 0.0
        self._playhead: float = 0.0
        self._loading = False
        self._label = ""
        # ── 音频 clip 参数（位置/淡入/淡出，只存在于本视图 = 音频参考轨）──
        self._clip_start: float = 0.0
        self._clip_fade_in: float = 0.0
        self._clip_fade_out: float = 0.0
        # ── 波形渲染缓存：仅音频/高度变化时重渲染，缩放/重绘不重算 ──
        self._waveform_pixmap: Optional[QPixmap] = None
        self._waveform_cache_key: Optional[tuple] = None
        # ── 拖拽状态 ──
        self._drag_mode: Optional[str] = None
        self._drag_origin_x: int = 0
        self._drag_start_start: float = 0.0
        self._drag_start_fade_in: float = 0.0
        self._drag_start_fade_out: float = 0.0
        self._is_dragging = False

        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background:#181818;")  # bg_deep（原藏青 #1a1a2e，对齐全站色温）
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def set_peaks(self, peaks: np.ndarray, duration: float):
        self._peaks = peaks
        self._duration = duration
        self._loading = False
        self._invalidate_waveform_cache()
        self.update()

    def set_label(self, text: str):
        self._label = text or ""
        self.update()

    def clear(self):
        """清除波形数据"""
        self._peaks = None
        self._duration = 0.0
        self._loading = False
        self._invalidate_waveform_cache()
        self.update()

    def set_loading(self, v: bool):
        self._loading = v
        self.update()

    def set_px_per_sec(self, v: float):
        self._px_per_sec = v
        self.update()

    def set_scroll_x(self, v: float):
        self._scroll_x = v
        self.update()

    def set_playhead(self, pos: float):
        old_px = int(self._playhead * self._px_per_sec - self._scroll_x) if self._px_per_sec > 0 else -1
        self._playhead = pos
        new_px = int(self._playhead * self._px_per_sec - self._scroll_x) if self._px_per_sec > 0 else -1
        if old_px == new_px:
            return
        margin = 5
        if old_px >= 0:
            self.update(max(0, old_px - margin), 0, margin * 2 + 2, self.height())
        if new_px >= 0:
            self.update(max(0, new_px - margin), 0, margin * 2 + 2, self.height())

    # ── 音频 clip 参数 ──────────────────────────────────────────────────────
    def set_clip_state(self, start: float = 0.0, fade_in: float = 0.0, fade_out: float = 0.0):
        """设置音频 clip 位置/淡入/淡出（视图内按当前音频时长钳制）。"""
        dur = max(0.0, float(self._duration or 0.0))
        self._clip_start = max(0.0, float(start or 0.0))
        self._clip_fade_in = max(0.0, min(float(fade_in or 0.0), dur))
        self._clip_fade_out = max(0.0, min(float(fade_out or 0.0), dur))
        self.update()

    # ── 波形渲染缓存（性能：缩放/滚动/重绘不重算波形）────────────────────
    def _invalidate_waveform_cache(self):
        self._waveform_pixmap = None
        self._waveform_cache_key = None

    def _ensure_waveform_pixmap(self, view_h: int) -> Optional[QPixmap]:
        """Bounded complete-source raster; zoom/scroll reuse its DPR-aware cache."""
        from .waveform_raster import ensure_waveform_pixmap
        return ensure_waveform_pixmap(self, view_h)

    # ── 绘制 ────────────────────────────────────────────────────────────────
    def _clip_geometry(self) -> tuple:
        pps = max(0.0001, float(self._px_per_sec or 0.0))
        start = max(0.0, float(self._clip_start or 0.0))
        dur = max(0.0, float(self._duration or 0.0))
        fade_in = max(0.0, min(float(self._clip_fade_in or 0.0), dur))
        fade_out = max(0.0, min(float(self._clip_fade_out or 0.0), dur))
        x0 = int(start * pps - self._scroll_x)
        x1 = int((start + dur) * pps - self._scroll_x)
        return x0, x1, fade_in, fade_out, pps, dur

    def paintEvent(self, event):
        from .waveform_clip_paint import paint_view
        paint_view(self)

    def _draw_fade_curtains(self, p: QPainter, w: int, h: int, x0: int, x1: int,
                            fade_in: float, fade_out: float, pps: float):
        from .waveform_clip_paint import paint_fades
        paint_fades(p, self, (x0, x1, fade_in, fade_out, pps, self._duration))

    def _draw_clip_label(self, p: QPainter, w: int, h: int, body_l: int, body_r: int):
        from .waveform_clip_paint import paint_label
        paint_label(p, self, body_l, body_r)

    # ── 鼠标交互（与普通 clip 同交互：拖主体移位置，拖菱形手柄设淡入淡出）──
    def _handle_hit(self, x: int) -> Optional[str]:
        if self._peaks is None or self._duration <= 0:
            return None
        x0, x1, fade_in, fade_out, pps, _dur = self._clip_geometry()
        fi_x = x0 + int(fade_in * pps)
        fo_x = x1 - int(fade_out * pps)
        if abs(x - fi_x) <= self.FADE_HANDLE_W:
            return "fade_in"
        if abs(x - fo_x) <= self.FADE_HANDLE_W:
            return "fade_out"
        if x0 <= x <= x1:
            return "move"
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        hit = self._handle_hit(e.pos().x())
        if hit is None:
            e.ignore()
            return
        self._drag_mode = hit
        self._drag_origin_x = e.pos().x()
        self._drag_start_start = self._clip_start
        self._drag_start_fade_in = self._clip_fade_in
        self._drag_start_fade_out = self._clip_fade_out
        self._is_dragging = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()
        self.grabMouse()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._is_dragging and (e.buttons() & Qt.MouseButton.LeftButton):
            pps = max(1.0, self._px_per_sec)
            dx = e.pos().x() - self._drag_origin_x
            dur = max(0.0, self._duration)
            if self._drag_mode == "move":
                self._clip_start = max(0.0, self._drag_start_start + dx / pps)
            elif self._drag_mode == "fade_in":
                self._clip_fade_in = max(0.0, min(dur, self._drag_start_fade_in + dx / pps))
            elif self._drag_mode == "fade_out":
                self._clip_fade_out = max(0.0, min(dur, self._drag_start_fade_out - dx / pps))
            self.update()
            e.accept()
            return
        if self._handle_hit(e.pos().x()) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self.releaseMouse()
            self._is_dragging = False
            mode = self._drag_mode
            self._drag_mode = None
            if mode is not None:
                self.clip_edit_committed.emit(
                    self._clip_start, self._clip_fade_in, self._clip_fade_out)
            self.update()
        e.accept()


# ──────────────────────────────────────────────────────────────────────────────
# 音频参考轨主组件
# ──────────────────────────────────────────────────────────────────────────────
class AudioRefTrack(QWidget):
    """
    时间线底部的音频参考轨。
    包含：
      - 左侧控制区（导入按钮、删除按钮、文件名、静音按钮）
      - 右侧波形显示区

    ★ 新增：audio_removed 信号，当用户删除参考轨时发出
    ★ 2026-08-04：音频 clip 化——导入后在最底部呈现为可编辑音频 clip
      （波形/淡入淡出/位置调整，只属于音频参考轨，锁定不能移走）。
    """
    audio_loaded   = pyqtSignal(str, float)   # (path, duration)
    audio_removed  = pyqtSignal()             # ★ 新增：参考轨被删除
    audio_changed  = pyqtSignal()             # user-visible project state changed
    audio_clip_edited = pyqtSignal()          # ★ 音频 clip 参数（位置/淡入/淡出）被编辑提交

    CTRL_WIDTH = 204   # 与 TRACK_LIST_WIDTH 对齐

    def __init__(self, parent=None):
        super().__init__(parent)
        self._audio_path: Optional[str] = None
        self._duration: float = 0.0
        self._muted: bool = False
        self._player = None          # QMediaPlayer（懒加载）
        self._audio_output = None
        self._loader: Optional[WaveformLoader] = None
        self._waveform_load = WaveformLoadSession(self)
        self._play_started_at: float = 0.0
        self._last_sync_seek_at: float = 0.0
        self._last_requested_pos_ms: int = -1
        # ── 音频 clip 状态（位置/淡入/淡出，持久化到工程）──
        self._clip_start: float = 0.0
        self._clip_fade_in: float = 0.0
        self._clip_fade_out: float = 0.0
        self._transport_playing: bool = False
        self.setFixedHeight(56)
        self.setStyleSheet("background:#181818; border-top:1px solid rgba(255,255,255,21);")  # bg_deep（原藏青 #141428）
        self._build_ui()

    # ── UI 构建 ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧控制区
        ctrl = QWidget()
        ctrl.setFixedWidth(self.CTRL_WIDTH)
        ctrl.setStyleSheet("background:#2D2D2D; border-right:1px solid rgba(255,255,255,21);")  # bg_box
        ctrl_lay = QHBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(6, 4, 6, 4)
        ctrl_lay.setSpacing(4)

        self._import_btn = QPushButton()
        if _make_icon: self._import_btn.setIcon(_make_icon("project_open", 16))
        else: self._import_btn.setText("载入")
        self._import_btn.setToolTip("导入音频参考文件 (MP3/WAV/FLAC/OGG)")
        self._import_btn.setFixedSize(26, 26)
        self._import_btn.setCursor(Qt.CursorShape.PointingHandCursor)   # §9
        # D 波任务 E：主操作 select_blue 填充 + 白字，hover 提亮 / pressed 压暗
        self._import_btn.setStyleSheet(
            "QPushButton{background:#4772B3;border:1px solid #4772B3;"
            "color:#FFFFFF;border-radius:4px;}"                       # select_blue 主操作
            "QPushButton:hover{background:#5480C5;}"
            "QPushButton:pressed{background:#35507C;}")
        self._import_btn.clicked.connect(self._import_audio)
        ctrl_lay.addWidget(self._import_btn)

        # ★ 新增：删除按钮
        self._delete_btn = QPushButton()
        if _make_icon: self._delete_btn.setIcon(_make_icon("track_delete", 16))
        else: self._delete_btn.setText("删除")
        self._delete_btn.setToolTip("删除音频参考轨")
        self._delete_btn.setFixedSize(26, 26)
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)   # §9
        self._delete_btn.setEnabled(False)   # 无音频时禁用
        self._delete_btn.setStyleSheet(
            # error_red/bright_error 危险操作（§4 语义保 hue）
            "QPushButton{background:rgba(153,22,22,90);border:1px solid rgba(227,104,85,110);"
            "color:#E36855;border-radius:4px;}"
            "QPushButton:hover{background:rgba(153,22,22,150);border-color:#E36855;}"
            "QPushButton:pressed{background:rgba(153,22,22,200);}"
            "QPushButton:disabled{background:#303030;border-color:#262626;color:#8B8B8B;}")
        self._delete_btn.clicked.connect(self._remove_audio)
        ctrl_lay.addWidget(self._delete_btn)

        self._mute_btn = QPushButton()
        if _make_icon: self._mute_btn.setIcon(_make_icon("audio_sim", 16))
        else: self._mute_btn.setText("静音")
        self._mute_btn.setToolTip("静音/取消静音")
        self._mute_btn.setFixedSize(26, 26)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)     # §9
        self._mute_btn.setCheckable(True)
        self._mute_btn.setStyleSheet(
            "QPushButton{background:#545454;border:1px solid #3D3D3D;"
            "color:#CCCCCC;border-radius:4px;}"
            "QPushButton:hover{background:#656565;}"
            "QPushButton:pressed{background:#4772B3;color:#FFFFFF;}"
            "QPushButton:checked{background:#991616;color:#FFFFFF;border-color:#991616;}"  # 静音=error_red 填充
            "QPushButton:checked:hover{background:#B01C1C;}")
        self._mute_btn.toggled.connect(self._on_mute_toggled)
        ctrl_lay.addWidget(self._mute_btn)

        self._name_lbl = QLabel("音频参考轨")
        self._name_lbl.setStyleSheet("color:#8B8B8B; font-size:10px;")
        self._name_lbl.setWordWrap(False)
        self._name_lbl.setMaximumWidth(100)
        ctrl_lay.addWidget(self._name_lbl, 1)

        root.addWidget(ctrl)

        # 右侧波形区（音频 clip 画布）
        self._waveform = WaveformView(self)
        self._waveform.clip_edit_committed.connect(self._on_clip_edit_committed)
        root.addWidget(self._waveform, 1)

    # ── 公开接口 ───────────────────────────────────────────────────────────────
    def set_px_per_sec(self, v: float):
        self._waveform.set_px_per_sec(v)

    def set_scroll_x(self, v: float):
        self._waveform.set_scroll_x(v)

    def _audio_time_at(self, pos: float) -> Optional[float]:
        """把时间线播放头位置映射到音频 clip 内的位置；clip 范围外返回 None。"""
        try:
            pos = float(pos or 0.0)
        except Exception:
            return None
        start = max(0.0, float(self._clip_start or 0.0))
        end = start + max(0.0, float(self._duration or 0.0))
        if pos < start or pos > end:
            return None
        return pos - start

    def _ensure_player(self) -> bool:
        """懒创建 QMediaPlayer；PyQt6.QtMultimedia 不可用时返回 False。"""
        if not self._audio_path:
            return False
        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtCore import QUrl
            if self._player is None:
                self._player = QMediaPlayer()
                self._audio_output = QAudioOutput()
                self._player.setAudioOutput(self._audio_output)
                self._player.setSource(QUrl.fromLocalFile(self._audio_path))
            return True
        except ImportError:
            return False

    def set_playhead(self, pos: float):
        from .sntc_reference_audio import reference_selected
        if reference_selected(self):
            self._waveform.set_playhead(pos)
            return
        self._set_playhead_local(pos)

    def _set_playhead_local(self, pos: float):
        self._waveform.set_playhead(pos)
        if self._muted or not self._audio_path:
            return
        audio_pos = self._audio_time_at(pos)
        try:
            from PyQt6.QtMultimedia import QMediaPlayer
            if audio_pos is None:
                # 播放头在音频 clip 范围外：暂停（不 seek，避免跳回开头）
                if (self._player is not None
                        and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
                    self._player.pause()
                return
            if (self._player is not None
                    and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
                now = time.monotonic()
                if now - float(getattr(self, "_play_started_at", 0.0) or 0.0) < 0.75:
                    return
                if now - float(getattr(self, "_last_sync_seek_at", 0.0) or 0.0) < 0.50:
                    return
                cur_ms = int(self._player.position())
                target_ms = int(audio_pos * 1000)
                if abs(cur_ms - target_ms) > 350:
                    self._player.setPosition(target_ms)
                    self._last_sync_seek_at = now
                    self._last_requested_pos_ms = target_ms
            elif self._transport_playing:
                # 播放头进入 clip 范围但音频未在播：补启动（播放中途进入 clip）
                if self._ensure_player():
                    self._player.setPosition(int(audio_pos * 1000))
                    self._player.play()
                    self._play_started_at = time.monotonic()
                    self._last_sync_seek_at = 0.0
                    self._last_requested_pos_ms = int(audio_pos * 1000)
        except Exception:
            pass

    def play(self, pos: float = None):
        from .sntc_reference_audio import reference_selected
        if not reference_selected(self):
            self._play_local(pos)

    def _play_local(self, pos: float = None):
        """开始播放音频（按音频 clip 位置映射；clip 范围外由 set_playhead 补启动）"""
        self._transport_playing = True
        if self._muted or not self._audio_path:
            return
        if pos is not None and self._audio_time_at(pos) is None:
            return  # clip 尚未开始：进入 clip 范围时自动补启动
        if not self._ensure_player():
            return
        try:
            if pos is not None:
                audio_pos = self._audio_time_at(pos)
                if audio_pos is None:
                    return
                self._last_requested_pos_ms = int(audio_pos * 1000)
                self._player.setPosition(self._last_requested_pos_ms)
            self._play_started_at = time.monotonic()
            self._last_sync_seek_at = 0.0
            self._player.play()
        except Exception:
            pass

    def pause(self):
        from .sntc_reference_audio import reference_selected
        if not reference_selected(self):
            self._pause_local()

    def _pause_local(self):
        """暂停播放"""
        self._transport_playing = False
        if self._player:
            try:
                self._player.pause()
            except Exception:
                pass
        self._play_started_at = 0.0

    def stop(self):
        from .sntc_reference_audio import reference_selected
        if not reference_selected(self):
            self._stop_local()

    def _stop_local(self):
        """停止播放"""
        self._transport_playing = False
        if self._player:
            try:
                self._player.stop()
            except Exception:
                pass
        self._play_started_at = 0.0

    def seek(self, pos: float):
        from .sntc_reference_audio import reference_selected
        if not reference_selected(self):
            self._seek_local(pos)

    def _seek_local(self, pos: float):
        """跳转到指定时间（映射到音频 clip 内位置，范围外不跳）"""
        if self._player:
            audio_pos = self._audio_time_at(pos)
            if audio_pos is None:
                return
            try:
                self._last_requested_pos_ms = int(audio_pos * 1000)
                self._last_sync_seek_at = time.monotonic()
                self._player.setPosition(self._last_requested_pos_ms)
            except Exception:
                pass

    @property
    def has_audio(self) -> bool:
        return self._audio_path is not None

    @property
    def audio_path(self) -> str:
        return self._audio_path or ""

    def load_audio_file(self, path: str) -> bool:
        """Programmatic load entry used when restoring a saved project."""
        if not path:
            return False
        norm_path = os.path.abspath(os.fspath(path))
        if not os.path.isfile(norm_path):
            return False
        self._load_audio(norm_path)
        return True

    def clear_audio(self):
        self._clear_audio_state(notify_change=False)

    # ── 音频 clip 状态（位置/淡入/淡出，随工程保存/加载）───────────────────
    @property
    def clip_end_time(self) -> float:
        """音频 clip 结束时间 = 位置 + 音频时长（供时间线总宽度计算）。"""
        return max(0.0, float(self._clip_start or 0.0)) + max(0.0, float(self._duration or 0.0))

    @property
    def audio_clip_state(self) -> dict:
        """返回可持久化的音频 clip 参数（start_time/fade_in/fade_out）。"""
        return {
            "start_time": round(max(0.0, float(self._clip_start or 0.0)), 3),
            "fade_in": round(max(0.0, float(self._clip_fade_in or 0.0)), 3),
            "fade_out": round(max(0.0, float(self._clip_fade_out or 0.0)), 3),
        }

    def set_audio_clip_state(self, state: dict):
        """恢复保存的音频 clip 参数（打开工程时调用；fade 在波形加载后钳制到时长）。"""
        try:
            state = dict(state or {})
            start = max(0.0, float(state.get("start_time", 0.0) or 0.0))
            fade_in = max(0.0, float(state.get("fade_in", 0.0) or 0.0))
            fade_out = max(0.0, float(state.get("fade_out", 0.0) or 0.0))
        except Exception:
            start, fade_in, fade_out = 0.0, 0.0, 0.0
        self._clip_start = start
        self._clip_fade_in = fade_in
        self._clip_fade_out = fade_out
        self._waveform.set_clip_state(start, fade_in, fade_out)

    def _on_clip_edit_committed(self, start: float, fade_in: float, fade_out: float):
        """音频 clip 编辑提交（拖拽结束）：同步状态并通知保存/时长刷新。"""
        self.set_audio_clip_state(
            {"start_time": start, "fade_in": fade_in, "fade_out": fade_out})
        self.audio_clip_edited.emit()
        self.audio_changed.emit()

    # ── 槽函数 ────────────────────────────────────────────────────────────────
    def _import_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入音频参考文件", "",
            "音频文件 (*.mp3 *.wav *.flac *.ogg *.aac *.m4a);所有文件 (*)"
        )
        if not path:
            return
        self._load_audio(path)
        self.audio_changed.emit()

    def _load_audio(self, path: str):
        """加载指定路径的音频文件"""
        path = os.path.abspath(os.fspath(path))

        self._waveform_load.cancel()

        self._audio_path = path
        fname = os.path.basename(path)
        self._name_lbl.setText(fname)
        self._name_lbl.setToolTip(path)
        self._name_lbl.setStyleSheet("color:#71A8FF; font-size:10px;")  # focus_blue 文件名高亮
        self._waveform.set_label(fname)
        self._transport_playing = False

        # 启用删除按钮
        self._delete_btn.setEnabled(True)

        # 重置播放器
        if self._player:
            try:
                self._player.stop()
                from PyQt6.QtCore import QUrl
                self._player.setSource(QUrl.fromLocalFile(path))
            except Exception:
                pass
            self._player = None
            self._audio_output = None
        self._play_started_at = 0.0
        self._last_sync_seek_at = 0.0
        self._last_requested_pos_ms = -1

        # 后台加载波形
        self._duration = 0.0
        self._waveform.clear()
        self._waveform.set_loading(True)
        self._waveform_load.request(path)

        try:
            from ...core.project_manager import ProjectManager
            pm = ProjectManager.instance()
            if pm and pm.project:
                pm.project.audio_path = path
        except Exception:
            pass

    def _remove_audio(self):
        self._clear_audio_state(notify_change=True)

    def _clear_audio_state(self, notify_change: bool):
        """
        ★ 新增：删除当前音频参考轨。
        停止播放，清空数据，恢复初始状态。
        """
        # 停止加载线程
        self._waveform_load.cancel()
        if not self._audio_path:
            return

        # 停止播放
        # Explicit source retirement still owns its player's stop, even while
        # external transport is selected. It does not stop the DMX timeline.
        self._stop_local()
        if self._player:
            try:
                from PyQt6.QtCore import QUrl
                self._player.setSource(QUrl())  # 清空媒体源
            except Exception:
                pass
            self._player = None
            self._audio_output = None
        self._play_started_at = 0.0
        self._last_sync_seek_at = 0.0
        self._last_requested_pos_ms = -1

        # 清空数据
        self._audio_path = None
        self._duration = 0.0
        self._clip_start = 0.0
        self._clip_fade_in = 0.0
        self._clip_fade_out = 0.0
        self._transport_playing = False

        # 重置 UI
        self._waveform.clear()
        self._waveform.set_label("")
        self._name_lbl.setText("音频参考轨")
        self._name_lbl.setToolTip("")
        self._name_lbl.setStyleSheet("color:#8B8B8B; font-size:10px;")
        self._delete_btn.setEnabled(False)

        # 通知 ProjectManager 清空 audio_path
        try:
            from ...core.project_manager import ProjectManager
            pm = ProjectManager.instance()
            if pm and pm.project:
                pm.project.audio_path = ""
        except Exception:
            pass
        # 发出信号，通知外部（如 ProgrammerPanel 清除音频链接）
        self.audio_removed.emit()
        if notify_change:
            self.audio_changed.emit()

    def _on_waveform_loaded(self, peaks, duration):
        self._duration = duration
        self._waveform.set_peaks(peaks, duration)
        # 波形就绪后把 clip 参数推给视图（fade 按真实时长钳制）
        self._waveform.set_clip_state(self._clip_start, self._clip_fade_in, self._clip_fade_out)
        self.audio_loaded.emit(self._audio_path, duration)

    def _on_waveform_error(self, msg: str):
        self._waveform.set_loading(False)
        self._name_lbl.setText(str(msg))
        self._name_lbl.setStyleSheet("color:#E36855; font-size:10px;")  # bright_error（9→10px，§5 下限）

    def _on_mute_toggled(self, checked: bool):
        self._muted = checked
        if not _make_icon:
            self._mute_btn.setText("取消静音" if checked else "静音")
        if self._player:
            try:
                if checked:
                    self._player.pause()
            except Exception:
                pass
