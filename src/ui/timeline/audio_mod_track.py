# -*- coding: utf-8 -*-
"""音频调制共享编辑器（R179 升级 / R183 A7 去控件面 = 纯状态真源持有者）。

R179 架构决策（用户规格 ①②）：调制轨成为**真正的 Track**（`Track.is_audio_mod`，
可添加/可拖动排序），行内渲染/画笔交互走 ClipArea 的轨道行
（`audio_mod_lane.AudioModLaneView`）；本文件的 `AudioModTrack` 降为**共享编辑器/
状态真源持有者**。

R183 A7（用户 2026-09-29 定案："音频调制有残留的前端在音频轨道底下"）：
**时间线底部残留编辑器整体移除**——本类不再构建任何控件面（量/特征/清空/
底部画布 `_ModCanvas` 全部删除），只保留状态与信号（Track/信号链/
`open_editor_requested` 全保留）。参数编辑入口：左双击调制区段 → 调制编辑大页 /
Draw 大页；区段批量清空 → 行内画布右键菜单「清空全部区段」。

旧工程迁移：`set_state` 收到无 `scope_track_id` 的旧档状态（底部固定轨时代）
时发 `legacy_state_needs_track`，由 timeline_widget 自动在顶部补一条调制轨
并回写关联（见 `_is_legacy_payload`）。

状态真源 = `Project.audio_modulation_state`（schema 见 engine/audio_modulation.py）。
播放期调制值由引擎查 `audio_modulation` 纯函数；本模块只做编辑与显示数据源。
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget

from ...engine import audio_modulation as _mod

logger = logging.getLogger(__name__)

MIN_CLIP_S = 0.05


def _is_legacy_payload(state: dict) -> bool:
    """旧档判定（用户规格 ③）：底部固定轨时代的状态（有内容但无 scope_track_id）。"""
    if str(state.get("scope_track_id", "") or ""):
        return False
    return bool(state.get("clips") or state.get("audio_sha")
                or state.get("scope_folder_id"))


def _resplit_points(left: dict, right: dict, t: float, pts, *, span: float) -> None:
    """拆分时断点包络按子区间重参数化（清查①B2 + ②③#3：二元解包崩曲率点
    [t,v,c] 的修复——按 p[0]/p[1] 取值丢曲率，脏数据整体包 try 丢弃 points）。"""
    try:
        from ...engine.audio_mod_envelope import eval_points as _ev, normalize_points
        pts = normalize_points(pts)
        u_cut = min(1.0, max(0.0, float(t) / max(1e-9, float(span))))
        v_cut = float(_ev(pts, u_cut))
        # ★ R183 清查③①C（P2）：左半同样重参数化 t'=p[0]/u_cut——修前只缩放
        #   右半，左半保留原绝对 t（超出新区间 [0,1] 的部分被 eval 端点钳制，
        #   形状漂移最高 0.5）。三元曲率 p[2:] 原样保留。
        lp = [[float(p[0]) / u_cut, float(p[1]), *p[2:]]
              for p in pts if float(p[0]) <= u_cut]
        if not lp or lp[-1][0] < 1.0:
            lp.append([1.0, v_cut])
        # ★ 清查⑤①#3（P2）：右半同保曲率 p[2:]（修前只留二元，带曲率包络
        #   拆分后右半全变直线，与"两侧均保形"语义不对称）。
        rp = [[0.0, v_cut]] + [
            [(float(p[0]) - u_cut) / max(1e-9, 1.0 - u_cut),
             float(p[1]), *p[2:]]
            for p in pts if float(p[0]) > u_cut]
        if len(rp) < 2:
            rp = [[0.0, v_cut], [1.0, v_cut]]
        left["points"] = lp
        right["points"] = rp
    except Exception:
        logger.warning("拆分断点包络重参数化失败，丢弃 points", exc_info=True)
        left.pop("points", None)
        right.pop("points", None)


class AudioModTrack(QWidget):
    """音频调制共享编辑器（状态真源持有者；R183 A7 起无控件面、常隐）。"""

    #: 状态任何变化（编辑/载入/时长/缩放）——行内画布与编辑页消费
    state_changed = pyqtSignal()
    #: 旧档状态（无 scope_track_id 且有内容）→ timeline_widget 自动补调制轨
    legacy_state_needs_track = pyqtSignal()
    #: 左双击（秒位置）→ main_window 打开调制编辑大页（Studio 式 linker 内容）
    open_editor_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_listeners: List[Callable] = []
        self._state: dict = _mod.normalize_state({})
        self._scope_track_id: str = ""      # 所属调制轨 Track.id（R179）
        self._audio_duration: float = 0.0   # >0 = 音频已导入
        self._px_per_sec: float = 100.0
        # 无控件面：显式隐藏，防无布局子件在父窗显示时误现（R183 A7）。
        self.setVisible(False)

    # ── 对外 API ────────────────────────────────────────────────────────
    def set_edit_listener(self, fn: Callable) -> None:
        """注册状态提交回调（main_window 注入：写 project + 置脏 + 烘焙失效）。"""
        self._edit_listeners = [fn] if fn else []

    def set_state(self, state: Optional[dict]) -> None:
        """从工程载入状态（不触发 edit listener；旧档发迁移信号）。"""
        st = state if isinstance(state, dict) else {}
        self._scope_track_id = str(st.get("scope_track_id", "") or "")
        legacy = _is_legacy_payload(st)
        self._state = _mod.normalize_state(st)
        self.state_changed.emit()
        if legacy:
            self.legacy_state_needs_track.emit()

    def state(self) -> dict:
        """状态快照（含 scope_track_id；工程保存口径）。"""
        out = dict(self._state)
        if self._scope_track_id:
            out["scope_track_id"] = self._scope_track_id
        return out

    def absorb_state(self, state: Optional[dict]) -> None:
        """吸收外部编辑（调制编辑大页提交）的状态，**不**回调 edit listener。

        R181：编辑页与调制轨共享工程状态真源；页面提交必须先回灌本 widget，
        否则下一次画笔提交会用陈旧 _state 覆盖页面的映射编辑。
        scope_track_id 缺失时保留现值（页面 payload 可能不带该键）。
        """
        st = state if isinstance(state, dict) else {}
        tid = str(st.get("scope_track_id", "") or "")
        if tid:
            self._scope_track_id = tid
        self._state = _mod.normalize_state(st)
        self.state_changed.emit()

    def set_scope_track(self, track_id: str) -> None:
        """把调制状态绑定到调制轨（scope_track_id；绑定即提交写回工程）。"""
        tid = str(track_id or "")
        if tid and tid == self._scope_track_id:
            return
        self._scope_track_id = tid
        if tid:
            self._commit()

    def scope_track_id(self) -> str:
        return self._scope_track_id

    def set_audio_duration(self, duration: float) -> None:
        self._audio_duration = float(duration or 0.0)
        self.state_changed.emit()

    def set_px_per_sec(self, px: float) -> None:
        self._px_per_sec = max(1.0, float(px or 100.0))
        self.state_changed.emit()

    def mod_paint_state(self, hover_span=None):
        """`paint_mod_surface` 数据源（state/时长/缩放/拖拽预览位/悬停高亮段）。

        R183 A7：本类不再持有拖拽预览（底部画布已移除），drag 槽恒 None；
        行内画布 `AudioModLaneView.mod_paint_state` 用本行拖拽状态合成该槽。
        """
        return (self._state, self._audio_duration, self._px_per_sec, None, hover_span)

    def span_at_sec(self, t: float) -> Optional[tuple]:
        """t 时刻所在 clip → (start, end)（悬停高亮；无 → None，绝不抛）。"""
        try:
            clip = self.clip_at(float(t))
        except (TypeError, ValueError):
            return None
        if clip is None:
            return None
        return (float(clip["start"]), float(clip["end"]))

    # ── 提交 ────────────────────────────────────────────────────────────
    def _commit(self):
        self._state = _mod.normalize_state(self._state)
        for fn in self._edit_listeners:
            try:
                fn(self.state())
            except Exception:
                logger.warning("audio mod edit listener failed", exc_info=True)
        self.state_changed.emit()

    # ── 画笔/区段提交（行内画布与右键菜单共用口径）──────────────────────
    def commit_clip_span(self, start: float, end: float) -> bool:
        """提交一段调制 clip（秒）：≥0.05s、音频时长截断；成功返回 True。

        R181：无音频（时长 0，含删除音频后残留态）一律拒绝——否则能画出
        永远播不到的幽灵区段（审计 B2 实测）。
        """
        if self._audio_duration <= 0:
            return False
        end = min(end, self._audio_duration)
        if end - start < MIN_CLIP_S:
            return False
        clips = list(self._state.get("clips") or [])
        clips.append({"start": round(max(0.0, start), 3), "end": round(end, 3),
                      # ★ R183 清查①B6：右键选的默认波形从未合并进新建 clip
                      #  （死功能）——补上，顺带引擎 normalize 补齐其余缺省。
                      "wave": str(self._state.get("default_wave", "") or "")})
        self._state["clips"] = clips
        self._commit()
        return True

    def clear_clips(self) -> None:
        """清空全部调制区段（行内画布右键菜单「清空全部区段」；空态不提交）。"""
        if not self._state.get("clips"):
            return
        self._state["clips"] = []
        self._commit()

    def clip_at(self, t: float) -> Optional[dict]:
        """t 时刻所在的 clip（右键菜单定位用；★ 清查②①F3 同族：重叠区与
        渲染"排后者胜"同口径——右键定位到真正发声的那条）。"""
        found = None
        for c in self._state.get("clips") or []:
            if c["start"] <= t <= c["end"]:
                found = dict(c)
        return found

    def delete_clip_range(self, clip: dict):
        """删区段（★ R183 清查①B3：同 span 多条只删一条；★ 清查③①F：删
        **末条**命中——与渲染/clip_at 排后者胜同层，右键定位到哪条删哪条）。"""
        kept = []
        drop_index = None
        clips = self._state.get("clips") or []
        for i, c in enumerate(clips):
            if (c["start"] == clip["start"] and c["end"] == clip["end"]):
                drop_index = i         # 循环不 break → 记录最后一条命中
        if drop_index is None:
            # ★ 清查⑤①O-3（P4）：无命中不提交（防白拍撤销快照+清 redo 栈）
            return
        for i, c in enumerate(clips):
            if i != drop_index:
                kept.append(c)
        self._state["clips"] = kept
        self._commit()

    # ── M1 拖动/缩放/per-clip fade 写口（clips 列表定位，与 delete_clip_range
    #    同式；模型运算在引擎纯函数，本层只定位+提交）───────────────────────
    @staticmethod
    def _clip_key(clip: dict) -> tuple:
        """clip → (start,end) 元组键（引擎 round(,4) 定位口径）。"""
        return (round(float((clip or {}).get("start", 0.0)), 4),
                round(float((clip or {}).get("end", 0.0)), 4))

    def move_clip(self, clip: dict, new_start: float) -> bool:
        """拖动平移提交（M1）：保 span 平移 + clamp 音频时长；重叠=排后者胜。

        clips 无实际变化（原位松手）→ 不提交返回 False（防无谓置脏）。
        """
        st = _mod.move_clip(self._state, self._clip_key(clip),
                            float(new_start or 0.0), self._audio_duration)
        return self._adopt_clip_state(st)

    def resize_clip(self, clip: dict, edge_sec: float, edge: str) -> bool:
        """拖边缩放提交（M1）：`edge` 'start'/'end'；最小宽 0.05s、右缘钳音频时长。"""
        st = _mod.resize_clip(self._state, self._clip_key(clip),
                              float(edge_sec or 0.0), str(edge), self._audio_duration)
        return self._adopt_clip_state(st)

    def set_clip_fade(self, clip: dict, side: str, seconds: float) -> bool:
        """per-clip 淡入/淡出提交（M1）：`side` 'in'/'out'，秒 ≥0（0=本 clip 无斜坡）。"""
        st = _mod.normalize_state(self._state)
        i = _mod.clip_index(st["clips"], self._clip_key(clip))
        if i < 0:
            return False
        key = "fade_in" if str(side) in ("in", "fade_in", "attack") else "fade_out"
        st["clips"][i][key] = max(0.0, float(seconds or 0.0))
        return self._adopt_clip_state(st)

    def _adopt_clip_state(self, st: dict) -> bool:
        """吸收引擎产出的新 state：clips 逐位相同则不提交（原位手势零噪声）。"""
        if [dict(c) for c in st.get("clips") or []] == \
                [dict(c) for c in self._state.get("clips") or []]:
            return False
        self._state = st
        self._commit()
        return True

    def split_clip_at(self, clip: dict, t: float) -> bool:
        """于此点拆分（M1 右键条目）：原区段 → [start,t]+[t,end]（两侧 ≥0.05s）。"""
        s, e = self._clip_key(clip)
        if not (s + MIN_CLIP_S <= t <= e - MIN_CLIP_S):
            return False
        st = _mod.normalize_state(self._state)
        i = _mod.clip_index(st["clips"], (s, e))
        if i < 0:
            return False
        left = dict(st["clips"][i])
        right = dict(left)
        left["end"] = round(float(t), 4)
        right["start"] = round(float(t), 4)
        # ★ R183 清查①B2（P1）：断点包络按子区间重参数化——修前整张 0..1
        #   归一化表原样复制进两半（左半在切点冲到末点值、右半从 0 重来，
        #   切点跳变+形状失真）。u_cut 取包络在切点的值，两半各接边界点。
        pts = left.get("points")
        if isinstance(pts, list) and len(pts) >= 2:
            # ★ 清查⑤①#1（P1）：传**相对切点**（t-s）——修前传绝对秒，
            #   s>0 的 clip 拆分包络全部错位（u_cut=t/span 空间错位）。
            _resplit_points(left, right, t - float(s), pts,
                            span=max(1e-9, float(e) - float(s)))
        st["clips"] = st["clips"][:i] + [left, right] + st["clips"][i + 1:]
        return self._adopt_clip_state(st)

    def ask_edit_clip_fade(self, clip: dict, side: str) -> None:
        """per-clip 淡入/淡出时长对话框（右键「此区段淡入淡出」入口；M1）。"""
        from PyQt6.QtWidgets import QInputDialog
        st = _mod.normalize_state(self._state)
        cur = next((c for c in st["clips"]
                    if (c["start"], c["end"]) == self._clip_key(clip)), None)
        if cur is None:
            return
        is_in = str(side) in ("in", "fade_in", "attack")
        sent = float(cur.get("fade_in" if is_in else "fade_out", -1.0))
        glob = float(self._state.get("attack" if is_in else "release", 0.0) or 0.0)
        shown = sent if sent >= 0.0 else glob            # 哨兵 → 展示全局生效值
        title = "此区段淡入时长（秒）" if is_in else "此区段淡出时长（秒）"
        val, ok = QInputDialog.getDouble(self, title, "秒：", shown, 0.0, 30.0, 2,
                                         Qt.WindowType.Sheet)
        if ok:
            self.set_clip_fade(cur, "in" if is_in else "out", float(val))

    def ask_edit_fade(self, key: str):
        """淡入/淡出时长编辑（QInputDialog，右键菜单「淡入淡出」入口）。"""
        from PyQt6.QtWidgets import QInputDialog
        cur = float(self._state.get(key, 0.05 if key == "attack" else 0.2) or 0.0)
        title = "淡入时长（秒）" if key == "attack" else "淡出时长（秒）"
        val, ok = QInputDialog.getDouble(self, title, "秒：", cur, 0.0, 30.0, 2,
                                         Qt.WindowType.Sheet)
        if not ok:
            return
        self._state[key] = float(val)
        self._commit()

    def set_default_wave(self, wave: str):
        """新画 clip 的默认波形（右键「波形」子菜单选择）。"""
        self._state["default_wave"] = wave
        self._commit()

    def set_scope_folder(self, folder_id: str):
        """scope 绑定（文件夹唯一主控；打开编辑页/勾选文件夹时调用）。

        R181：空 folder_id 或与现状同值 → 直接返回（不再无谓提交置脏——
        编辑页每次打开都会调用本入口接当前选中文件夹作用域）。
        """
        fid = str(folder_id or "")
        if not fid or fid == str(self._state.get("scope_folder_id", "") or ""):
            return
        from ...engine.audio_mod_folder_scope import enforce_single_owner
        self._state = enforce_single_owner(self._state, fid)
        self._commit()

    def scope_folder_id(self) -> str:
        """当前 scope 文件夹 id（空=未绑定）。"""
        return str(self._state.get("scope_folder_id", "") or "")

    def scope_summary(self) -> str:
        """状态栏摘要（右键菜单底部信息行）。"""
        n = len(self._state.get("clips") or [])
        fid = str(self._state.get("scope_folder_id", "") or "")
        scope = f"范围:文件夹 {fid}" if fid else "范围:未绑定"
        track = f" · 轨道 {self._scope_track_id[:8]}" if self._scope_track_id else ""
        return (f"调制区段 {n} 段 · {scope} · "
                f"量 {self._state.get('amount', 1.0):.0%}{track}")
