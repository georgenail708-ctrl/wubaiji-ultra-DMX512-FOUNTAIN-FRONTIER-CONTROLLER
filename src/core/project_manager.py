"""
Qascade V1.0 — Project Manager

修复说明：
  [1] 彻底移除 __new__ 单例拦截 —— PyQt6 的 QObject 禁止拆分 __new__/__init__
  [2] 改用类变量 + classmethod 工厂方法实现单例，QObject 正常走完整构造链
  [3] instance() 是唯一入口，直接调用 ProjectManager() 也安全（会触发警告）
  [4] reset_singleton() 保留，便于测试 / 热重载
  [5] 新增 save() / load() 方法
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import zlib
from typing import Optional
from .clip_smoothing_source import attach_frame_ref, capture_frame_ref

# 工程 JSON 解析统一入口（orjson 优先，退路纯 json）定义于 models.py（最底层，
# 供 ProjectLoader/Project.load/撤销恢复共用）；此处 re-export 保持单一定义。
from .models import (  # noqa: F401 - parse_project_json 为本模块公开入口
    Project,
    Track,
    Clip,
    FilterRule,
    parse_project_json,
)

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal

log = logging.getLogger(__name__)

# 大工程异步加载阈值（文件大小，字节）
_ASYNC_LOAD_THRESHOLD = 50 * 1024 * 1024  # 50 MB


class ProjectLoader(QThread):
    """后台 QThread：解析大工程 JSON 并构建轻量 Project（不解码 DMX 帧数据）。

    信号：
      progress(stage, current, total)
          stage: str 描述当前阶段
          current/total: 进度（0=不确定模式）
      finished(project_dict)
          将原始 dict 发出供调用方连接
      error(message)
    """

    progress = pyqtSignal(str, int, int)  # stage_name, current, total
    finished = pyqtSignal(object)         # Project 实例
    error = pyqtSignal(str)

    def __init__(self, filepath: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._path = filepath

    def run(self):
        try:
            # Stage 1: 解析 JSON（I/O 密集，但单次分配；orjson 优先，见文件头）
            self.progress.emit("解析 JSON...", 0, 0)
            with open(self._path, "rb") as f:
                raw = parse_project_json(f.read())
            if not isinstance(raw, dict):
                self.error.emit("工程文件格式错误：顶层数据不是字典")
                return

            # Stage 2: 构建轻量 Project（帧数据用 _LazyDMXFrame，不解码 base64）
            self.progress.emit("构建设备模型...", 0, 0)
            project = Project._from_dict(raw, _lazy=True)
            self.progress.emit("加载完成", 1, 1)
            self.finished.emit(project)
        except Exception as e:
            log.exception("ProjectLoader 加载异常")
            self.error.emit(f"加载失败：{e}")


# ★ R193/P2-11（SV-08）：core 层死代码 ProjectSaver 已删除——全仓唯一在用的
# 同名类在 src/ui/project_save_async.py；旧类遮蔽 QThread.finished，误用即炸。


# ═══════════════════════════════════════════════════════════════════════════
#  撤销域（"上一步究竟精确改了什么"）—— 2026-09-19 用户实报修复
# ═══════════════════════════════════════════════════════════════════════════
# 用户原话::
#
#     "点击编辑-撤销 会影响错误判断上一步究竟做了什么，例如我上一步其实是对 clip
#      的切分，它撤销的时候会同时把例如关联器的设置也撤销了。不应该按照每个页面
#      来撤销。而是真正纪录上一步究竟精确到干了什么操作"
#
# 根因：撤销条目一直是**整工程快照**（改前把 Project 全量序列化），撤销即整体替换
#   → 快照**之后**在别的面板做的改动一并被回滚。
#
# 修法：条目附带它**实际改动**的工程顶层字段集合（domain）。撤销只把 domain 内的
#   字段还原成快照值，其余字段保持现状。调用方声明自己的域（如切 clip →
#   DOMAIN_TIMELINE）—— 这才是"记录上一步干了什么"。domain=None 保留旧全量语义。
DOMAIN_TIMELINE = frozenset({"tracks", "programs", "current_program_id",
                             "audio_path", "audio_clip_state",
                             "audio_modulation_state"})   # M1：调制 clip 拖动可撤销
#: ★ R183 T2（时间线批）：标记轨 / 循环区**独立域**。
#: 为什么独立而不并进 DOMAIN_TIMELINE（审计⑦B P1-5 实锤）：并进去会让"拖 clip 后
#: 按 Ctrl+Z"连带把拖之后添加的标记一起删掉（markers 回到那次 clip 快照的时刻）——
#: 用户看到的是"撤销一次剪切，标记没了"。标记编辑各自压自己的域快照，语义才是
#: "撤销只回滚这一步改了什么"（2026-09-19 定案）。
DOMAIN_MARKERS = frozenset({"markers", "loop"})
DOMAIN_LINKER = frozenset({"processor_state"})
DOMAIN_TOPVIEW = frozenset({"topview_state", "splines"})
DOMAIN_EFFECT = frozenset({"effect_browser_state"})
DOMAIN_MATRIX = frozenset({"matrix_slideshow_state"})
DOMAIN_PROGRAMMER = frozenset({"programmer_state", "programmer_cues_data",
                               "param_envelopes_data", "trajectory_curves_data"})
DOMAIN_PATCH = frozenset({"fixtures", "fixture_defs", "fixture_groups",
                          "logical_devices"})
DOMAIN_DISPATCH = frozenset({"dispatch_state"})
DOMAIN_ROUTE = frozenset({"route_order_state", "route_animator_state"})
DOMAIN_AUDIO = frozenset({"audio_bridge_state", "audio_driver_state"})
DOMAIN_CLIP_STUDIO = frozenset({"clip_studio_state"})   # R172 Clip Studio 卡片画布

#: 面板状态类字段（`main_window._collect_project_state_for_save` 写的那批）。
#: 域内不含它们时，快照无需先收集面板状态（省一次全量收集，也避免快照副作用）。
PANEL_STATE_KEYS = (DOMAIN_LINKER | DOMAIN_TOPVIEW | DOMAIN_EFFECT | DOMAIN_MATRIX
                    | DOMAIN_PROGRAMMER | DOMAIN_DISPATCH | DOMAIN_ROUTE
                    | DOMAIN_AUDIO | DOMAIN_CLIP_STUDIO)


def entry_kind(entry):
    """栈条目种类：scoped / full；旧格式（裸 Project/bytes）返回 None。"""
    if isinstance(entry, tuple) and entry and entry[0] in ("scoped", "full"):
        return entry[0]
    return None


def entry_domain(entry):
    """该条目记录的域（None = 全量语义）。兼容旧的 3 元 scoped 条目。"""
    kind = entry_kind(entry)
    if kind == "scoped" and len(entry) >= 4:
        return entry[3]
    if kind == "full" and len(entry) >= 3:
        return entry[2]
    return None


def entry_payload(entry):
    """条目里的快照载荷（去掉种类/refs/domain 外壳）。"""
    kind = entry_kind(entry)
    return entry[1] if kind else entry


def entry_frames_refs(entry):
    """scoped 条目的旁路帧引用；其它形态返回 None。"""
    return entry[2] if entry_kind(entry) == "scoped" else None


def entry_dict(entry):
    """条目 → 工程 dict（不重建 Project，供域合并读取）；解不开返回 None。"""
    payload = entry_payload(entry)
    if isinstance(payload, Project):
        return payload.to_dict()
    if isinstance(payload, bytes):
        payload = zlib.decompress(payload).decode("utf-8")
    if isinstance(payload, str):
        try:
            return parse_project_json(payload)
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None



class ProjectManager(QObject):
    # ── 信号 ──────────────────────────────────────────────────────────────────
    project_changed = pyqtSignal()
    track_added     = pyqtSignal(str)
    track_removed   = pyqtSignal(str)
    clip_removed    = pyqtSignal(str, str)
    dirty_changed   = pyqtSignal(bool)

    # ── 单例状态（类变量）────────────────────────────────────────────────────
    _instance: Optional["ProjectManager"] = None

    # =========================================================================
    #  构造
    # =========================================================================

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

        self._project: Project            = Project()
        self._undo_stack: list            = []
        self._redo_stack: list            = []
        self._dirty: bool                 = False
        self._current_path: Optional[str] = None
        # 撤销快照策略：
        #   "deepcopy" = 现状逐字节不变（copy.deepcopy 整个 Project）。默认。
        #   "dict"     = 序列化快照（project.to_dict() -> JSON 串），还原时反序列化重建。
        # 红线：默认必须走原 deepcopy 路径，dict 模式纯 opt-in。
        self._undo_mode: str              = "deepcopy"

        log.info("ProjectManager 实例已创建 (id=%s)", id(self))

    # ── 单例工厂 ──────────────────────────────────────────────────────────────

    @classmethod
    def instance(cls, parent: Optional[QObject] = None) -> "ProjectManager":
        if cls._instance is None:
            try:
                cls._instance = cls(parent)
            except Exception:
                cls._instance = None
                log.exception("ProjectManager 创建失败")
                raise
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        if cls._instance is not None:
            try:
                cls._instance.deleteLater()
            except Exception:
                log.debug("reset_singleton: deleteLater failed, ignoring")
                pass
            cls._instance = None
        log.info("ProjectManager 单例已重置")

    # =========================================================================
    #  工程属性
    # =========================================================================

    @property
    def project(self) -> Project:
        return self._project

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self):
        self._edit_revision = getattr(self, "_edit_revision", 0) + 1
        if not self._dirty:
            self._dirty = True
            self.dirty_changed.emit(True)

    # =========================================================================
    #  文件 I/O
    # =========================================================================

    def save(self, path: str, mark_clean: bool = True,
             set_current_path: bool = True) -> bool:
        """保存工程到文件。

        ★ R193/SV-02：autosave 须传 ``mark_clean=False, set_current_path=False``
        ——用户从未保存真路径，不能清 dirty，也不能把"当前路径"改绑到
        autosave 时间戳文件（历史埋雷：后续相对路径解析/标题全部跟着错）。
        """
        try:
            self._project.save(path)
            if mark_clean:
                self._dirty = False
                self.dirty_changed.emit(False)
            if set_current_path:
                self._current_path = path
            log.info("工程已保存: %s", path)
            return True
        except Exception as e:
            log.exception("保存失败: %s", e)
            return False

    def load(self, path: str) -> bool:
        """从文件加载工程。

        小工程（<50MB）同步加载，行为不变。
        大工程异步加载，显示进度对话框，UI 不卡死。
        """
        try:
            # 检查文件大小，决定同步/异步路径
            self._last_load_error = ""
            file_size = os.path.getsize(path)
            if file_size < _ASYNC_LOAD_THRESHOLD:
                # 小工程：同步路径（行为不变）
                self._project = Project.load(path)
                self._finalize_load(path)
                return True

            # 大工程：异步加载 + 进度对话框
            return self._load_large_async(path)
        except Exception as e:
            log.exception("打开失败: %s", e)
            self._last_load_error = str(e)
            return False

    def _load_large_async(self, path: str) -> bool:
        """大工程异步加载（QThread + QProgressDialog）。"""
        # 在 core 模块内按需导入 QtWidgets（避免模块级 UI 依赖）
        from PyQt6.QtWidgets import QProgressDialog, QApplication

        loader = ProjectLoader(path); self._active_loader = loader  # ★ R193/P2-10：防局部引用归零
        dialog = QProgressDialog("正在加载项目...", None, 0, 0, None)
        dialog.setWindowTitle("雾白纪 ULTRA")  # 2026-08-27 品牌：加载进度框用户可见
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # P2-24：无父时 WindowModal 不拦任何窗口，改全局拦截
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        dialog.setCancelButton(None)  # 不允许取消
        # 确保 dialog 在事件循环中实际显示
        QApplication.processEvents()

        error_message: Optional[str] = None
        loaded_project: Optional[Project] = None

        def _on_progress(stage: str, current: int, total: int):
            dialog.setLabelText(stage)
            if total > 0 and current <= total:
                dialog.setMaximum(total)
                dialog.setValue(current)
            QApplication.processEvents()

        def _on_finished(proj: object):
            nonlocal loaded_project
            loaded_project = proj
            dialog.close()

        def _on_error(msg: str):
            nonlocal error_message
            error_message = msg
            dialog.close()

        loader.progress.connect(_on_progress)
        loader.finished.connect(_on_finished)
        loader.error.connect(_on_error)

        loader.start()
        dialog.exec()  # 模态事件循环：UI 保持响应，直到 dialog.close()

        # 检查结果
        if error_message is not None:
            log.error("异步加载失败: %s", error_message)
            self._last_load_error = str(error_message)
            # 确保 loader 终止
            if loader.isRunning():
                loader.quit()
                loader.wait(2000)
            return False

        if loaded_project is None:
            log.error("异步加载返回空工程")
            self._last_load_error = "异步加载返回空工程"
            return False

        # 应用加载结果
        self._project = loaded_project
        self._finalize_load(path)
        # ★ R193/P2-10（SV-07）：收线程防 C++ 析构竞态（run() emit 后即返回，
        # 实际等待毫秒级）；引用挂 self 防局部归零后 "Destroyed while running"。
        self._active_loader = loader; loader.wait(2000); self._active_loader = None
        return True

    def _finalize_load(self, path: str):
        """加载完成后的公共清理（同步/异步路径共用）。"""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._dirty = False
        self.dirty_changed.emit(False)
        self._current_path = path
        self.project_changed.emit()
        log.info("工程已打开: %s", path)

    # =========================================================================
    #  撤销 / 重做
    # =========================================================================

    _MAX_UNDO = 50

    # ── 快照策略开关 ───────────────────────────────────────────────────────────

    @property
    def undo_mode(self) -> str:
        """当前撤销快照策略："deepcopy"(默认/现状) 或 "dict"(序列化快照)。"""
        return self._undo_mode

    @undo_mode.setter
    def undo_mode(self, value: str) -> None:
        v = str(value or "deepcopy").strip().lower()
        if v not in ("deepcopy", "dict"):
            v = "deepcopy"
        if v == self._undo_mode:
            return
        # 切换模式时清空历史，避免两种异构快照混在同一栈里。
        self._undo_mode = v
        self._undo_stack.clear()
        self._redo_stack.clear()
        log.info("ProjectManager 撤销策略切换为 %s", v)

    # ── 捕获 / 还原（按模式分派）─────────────────────────────────────────────

    def _capture(self, project: "Project"):
        """把一个 Project 捕获成栈里存放的快照对象。

        - deepcopy 模式：尝试 copy.deepcopy；Project 是 QObject 必抛 TypeError，
                          故与 dict 模式一样回退为 zlib 压缩 bytes。
        - dict     模式：返回 zlib 压缩后的 bytes（project.to_dict() 序列化为
                         JSON 串再压缩）。存压缩 bytes 而非裸 dict —— to_dict 内部
                         对 fixture_groups / logical_devices / *_state 等是浅引用，
                         存裸 dict 会与实时工程共享可变对象、被后续编辑污染；压缩
                         冻结语义，且高冗余 DMX 帧数据压缩比高、驻留体积显著缩小。
        """
        if self._undo_mode == "dict":
            return zlib.compress(json.dumps(project.to_dict(), ensure_ascii=False).encode("utf-8"))
        try:
            return copy.deepcopy(project)
        except TypeError:
            # PyQt6 的 QObject 子类不支持 pickle → copy.deepcopy(Project) 必抛
            # TypeError("cannot pickle ...")；回退序列化快照（与 dict 模式同款，
            # _restore 对两种快照都支持）。2026-08-11: TopView 保存实时组报错根因。
            return zlib.compress(json.dumps(project.to_dict(), ensure_ascii=False).encode("utf-8"))

    def _restore(self, snap) -> "Project":
        """把栈里的快照对象还原成一个独立的 Project。

        - Project 实例（deepcopy 模式）：直接返回，本就独立。
        - 压缩 bytes（序列化快照）：zlib 解压 -> JSON 串 -> 反序列化重建。Project
          没有 from_dict，复用既有 Project.load(path)（经临时文件），零 models.py 改动。
        """
        if isinstance(snap, Project):
            return snap
        # 序列化模式：压缩 bytes 解压（或原样 str/dict）-> 临时文件 -> Project.load 重建。
        frames_refs = None
        prog_live_refs = None
        if isinstance(snap, tuple) and snap and snap[0] == "scoped":
            snap, frames_refs = snap[1], snap[2] if len(snap) >= 3 else None
            # R57 新格式：refs = (prog_refs, live_refs) 分容器回挂；
            # R49 旧格式（flat list）保持原行为。
            if isinstance(frames_refs, tuple) and len(frames_refs) == 2 \
                    and isinstance(frames_refs[0], list) and isinstance(frames_refs[1], list):
                prog_live_refs = frames_refs
                frames_refs = None
        if isinstance(snap, bytes):
            data = zlib.decompress(snap).decode("utf-8")
        else:
            data = snap if isinstance(snap, str) else json.dumps(snap, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(suffix=".qcz.json", prefix="undo_snap_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            proj_loaded = Project.load(tmp_path)
            if prog_live_refs is not None:
                # R57：program 副本与 live tracks 各自回挂，同 id 互不串帧
                self._reattach_frames(prog_live_refs[0],
                                      [p for p in (getattr(proj_loaded, "programs", None) or [])])
                self._reattach_frames(prog_live_refs[1], [proj_loaded])
            elif frames_refs:
                # scoped 快照：按 clip id 回挂旁路的帧列表（浅拷贝共享帧对象）
                by_id = {}
                for _prog in (getattr(proj_loaded, "programs", None) or []):
                    for _t in (getattr(_prog, "tracks", None) or []):
                        for _c in (getattr(_t, "clips", None) or []):
                            by_id[str(getattr(_c, "id", ""))] = _c
                for _t in (getattr(proj_loaded, "tracks", None) or []):
                    for _c in (getattr(_t, "clips", None) or []):
                        by_id[str(getattr(_c, "id", ""))] = _c
                for ref in frames_refs:
                    _c = by_id.get(ref[0])
                    if _c is not None:
                        attach_frame_ref(_c, ref)
            return proj_loaded
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                log.debug("ProjectManager._restore: failed to remove temp file, ignoring")
                pass

    def _reattach_frames(self, refs, containers):
        """R57：把 (clip_id, frames_list) refs 回挂到指定容器集合内的 clip。"""
        if not refs:
            return
        by_id = {}
        for cont in containers:
            for _t in (getattr(cont, "tracks", None) or []):
                for _c in (getattr(_t, "clips", None) or []):
                    by_id[str(getattr(_c, "id", ""))] = _c
        for ref in refs:
            _c = by_id.get(ref[0])
            if _c is not None:
                attach_frame_ref(_c, ref)

    def _snapshot_scoped(self, domain=None):
        """交互级轻快照（R49 性能·R57 再提速）。

        与全量 _snapshot 同构的状态，仅剥离各 clip 的 frames 大负重：
        - R49：帧列表以浅拷贝 list(frames) 旁路保存，恢复时按 clip id 回挂；
          帧对象本尊共享安全——时间线参数编辑不改动帧内容（record 只追加新帧）。
        - R57：① to_dict(include_frames=False) 直接跳过帧→base64 dict 转换
          （10 万帧真实工程 to_dict ~1.1s 是旧快照的大头，且转换结果随剥即弃）；
          ② refs 直接从 clip 对象收集（旧实现先全量转换再从 dict 摘除）；
          ③ refs 分 (program_refs, live_refs) 两路回挂——旧实现 program 副本与
          live tracks 的同 id clip 共用一个 by_id 槽，恢复时 live clip 可能被
          塞入 program 副本的陈旧帧（undo 后录制内容错乱）；
          ④ zlib 压缩延迟到当前编辑突发之后（QTimer 250ms）——手势路径只付
             json.dumps，压缩不再插入拖动后的下一拍；_restore 本就兼容 str 载荷。
        栈条目形如 ("scoped", json_str|zlib_bytes, (prog_refs, live_refs))。
        """
        snap = self._make_scoped_entry(domain)
        self._undo_stack.append(snap)
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        # 压缩延迟：同栈条目原位替换为 zlib bytes（str→bytes 对 _restore 等价）。
        try:
            from PyQt6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(250, lambda: self._compress_scoped_entry(snap))
        except Exception:
            self._compress_scoped_entry(snap)

    def _make_scoped_entry(self, domain=None):
        """构造 scoped 栈条目（**不压栈**）—— 撤销/重做压反向条目时复用。"""
        from .dispatch_snapshot import snapshot_entry
        dispatch = snapshot_entry(self._project, domain)
        if dispatch is not None:
            return dispatch
        from .timeline_snapshot import timeline_snapshot_dict
        d = timeline_snapshot_dict(self._project, domain)
        payload = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        return ("scoped", payload, self._collect_frames_refs(), domain)

    def _collect_frames_refs(self):
        """各 clip 的帧列表旁路引用（scoped 快照剥离 frames，恢复时回挂）。"""
        prog_refs: list = []
        live_refs: list = []

        def _collect(tracks, out):
            for t in tracks or []:
                for c in (getattr(t, "clips", None) or []):
                    ref = capture_frame_ref(c)
                    if ref is not None:
                        out.append(ref)

        for prog in (getattr(self._project, "programs", None) or []):
            _collect(getattr(prog, "tracks", None), prog_refs)
        _collect(getattr(self._project, "tracks", None), live_refs)
        return (prog_refs, live_refs)

    def _compress_scoped_entry(self, snap):
        """把栈内指定 scoped 条目的 str 载荷原位压缩（R57：移出交互热路径）。"""
        try:
            # 身份比较定位（== 会深度比较 4.5MB JSON 与 10 万元素 refs，得不偿失）
            idx = -1
            for i, entry in enumerate(self._undo_stack):
                if entry is snap:
                    idx = i
                    break
            if idx < 0:
                return  # 已被 undo/redo 消费或被挤出栈
            _, payload, refs, domain = snap
            if isinstance(payload, str):
                compressed = zlib.compress(payload.encode("utf-8"), 1)
                self._undo_stack[idx] = ("scoped", compressed, refs, domain)
        except Exception:
            pass  # 压缩失败时条目保持 str 载荷，_restore 兼容

    def _snapshot(self, domain=None):
        """全量快照（`domain=None`）或**限域**快照（只回滚 domain 内字段）。

        ★ R131：`fixtures`/`fixture_groups`/`logical_devices`/`fixture_defs`/
          `light_control_mode` 五个属性 setter **不再自行 `_snapshot()`** ——
          面板的既有写法是「先 `pm._snapshot(domain=DOMAIN_X)`（记"上一步真正干了
          什么"），再经 setter 写值」，setter 再压一条 `domain=None` 会让每步产生两条
          重叠条目：第一次 Ctrl+Z 消费的是那条同态条目 → 用户看到"按了没反应"。
          写入方负责快照是 R100 既定语义（见 `_snapshot` 的 domain 说明），故此处只
          保留写值 + 标脏 + 广播。
        """
        snap = self._capture(self._project)
        self._undo_stack.append(("full", snap, domain))
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        self._apply_step(self._undo_stack.pop(), self._redo_stack)
        self.project_changed.emit()
        self._mark_dirty()

    def redo(self):
        if not self._redo_stack:
            return
        self._apply_step(self._redo_stack.pop(), self._undo_stack)
        self.project_changed.emit()
        self._mark_dirty()

    def _apply_step(self, entry, push_to) -> None:
        """消费一条条目：回滚它记的域，并把**反向条目**压入 push_to。

        `domain is None` → 旧的全量语义（整机替换），零回归；否则只还原该域内的
        字段、其余保持现状 —— 即"撤销只影响上一步真正做过的那件事"。
        """
        domain = entry_domain(entry)
        if domain is None:
            push_to.append(("full", self._capture(self._project), None))
            self._project = self._restore(entry_payload(entry))
            return
        push_to.append(self._make_scoped_entry(domain))
        self._project = self._merged_project(entry, domain)

    def _merged_project(self, entry, domain):
        """只回滚 `domain` 内字段（其余保持现状）→ 新 Project。"""
        before = entry_dict(entry)
        if before is None:                      # 载荷解不开 → 退回整机替换
            return self._restore(entry_payload(entry))
        from .dispatch_snapshot import restore_domain
        dispatch = restore_domain(self._project, before, domain)
        if dispatch is not None:
            return dispatch
        from .scoped_frame_restore import merged_frame_refs
        refs = merged_frame_refs(domain, self._collect_frames_refs(), entry_frames_refs(entry))
        from .timeline_restore import restore_timeline_domain
        merged = restore_timeline_domain(self._project, before, domain)
        if merged is not None:
            self._reattach_frame_refs(refs, merged)
            return merged
        cur = self._project.to_dict(include_frames=False)
        for k in domain:
            if k in before:
                cur[k] = before[k]
            else:
                cur.pop(k, None)                # 快照里没有 = 该字段当时不存在
        merged = self._load_from_dict(cur)
        self._reattach_frame_refs(refs, merged)
        return merged

    def _reattach_frame_refs(self, refs, proj) -> None:
        """按 (prog_refs, live_refs) 双容器回挂帧（与 `_restore` 同口径）。"""
        if isinstance(refs, tuple) and len(refs) == 2:
            self._reattach_frames(refs[0], list(getattr(proj, "programs", None) or []))
            self._reattach_frames(refs[1], [proj])
        else:
            self._reattach_frames(refs, [proj])

    def _load_from_dict(self, d) -> "Project":
        """复用原 JSON 规范化和 Project.load 工厂，避免撤回的临时文件往返。"""
        payload = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        return Project._from_dict(parse_project_json(payload))

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    # =========================================================================
    #  设备 / 设备组
    # =========================================================================

    @property
    def fixtures(self):
        return self._project.fixtures

    @fixtures.setter
    def fixtures(self, value: list) -> None:
        
        self._project.fixtures = value
        self._mark_dirty()
        self.project_changed.emit()

    @property
    def fixture_groups(self):
        return getattr(self._project, "fixture_groups", [])

    @fixture_groups.setter
    def fixture_groups(self, value: list):
        
        self._project.fixture_groups = value
        self._mark_dirty()
        self.project_changed.emit()

    @property
    def logical_devices(self):
        return getattr(self._project, "logical_devices", [])

    @logical_devices.setter
    def logical_devices(self, value: list):
        
        self._project.logical_devices = value
        self._mark_dirty()
        self.project_changed.emit()

    @property
    def light_control_mode(self) -> str:
        return getattr(self._project, "light_control_mode", "whole")

    @light_control_mode.setter
    def light_control_mode(self, value: str):
        v = str(value or "whole").strip().lower()
        self._project.light_control_mode = v if v in ("single", "whole") else "whole"
        self._mark_dirty()
        self.project_changed.emit()

    def add_fixture(self, fi) -> None:
        self._snapshot()
        self._project.fixtures.append(fi)
        self._mark_dirty()
        self.project_changed.emit()

    def remove_fixture(self, fixture_id: str) -> None:
        self._snapshot()
        self._project.fixtures = [
            f for f in self._project.fixtures if f.id != fixture_id]
        self._mark_dirty()
        self.project_changed.emit()

    def get_fixture_def(self, def_id: str):
        return self._project.get_fixture_def(def_id)

    def add_fixture_definition(self, fdef) -> None:
        self._snapshot()
        if not hasattr(self._project, "fixture_defs"):
            self._project.fixture_defs = []
        self._project.fixture_defs.append(fdef)
        self._mark_dirty()
        self.project_changed.emit()

    @property
    def fixture_defs(self):
        if not hasattr(self._project, "fixture_defs"):
            self._project.fixture_defs = []
        return self._project.fixture_defs

    @fixture_defs.setter
    def fixture_defs(self, value: list) -> None:
        
        self._project.fixture_defs = value
        self._mark_dirty()
        self.project_changed.emit()

    # =========================================================================
    #  轨道
    # =========================================================================

    def add_track(self, track: Track) -> Track:
        self._snapshot()
        self._project.add_track(track)
        self.track_added.emit(track.id)
        self.project_changed.emit()
        self._mark_dirty()
        return track

    def remove_track(self, track_id: str):
        self._snapshot()
        self._project.remove_track(track_id)
        self.track_removed.emit(track_id)
        self.project_changed.emit()
        self._mark_dirty()

    def update_track(self, track_id: str, **kwargs):
        self._snapshot()
        track = self._project.get_track(track_id)
        if track is None:
            log.warning("update_track: 找不到轨道 %s", track_id)
            return
        for k, v in kwargs.items():
            setattr(track, k, v)
        self.project_changed.emit()
        self._mark_dirty()

    def get_track(self, track_id: str) -> Optional[Track]:
        return self._project.get_track(track_id)

    # =========================================================================
    #  Clip
    # =========================================================================

    def add_clip(self, track_id: str, clip: Clip):
        track = self._project.get_track(track_id)
        if track is None:
            log.warning("add_clip: 找不到轨道 %s", track_id)
            return
        self._snapshot()
        track.add_clip(clip)
        self.project_changed.emit()
        self._mark_dirty()

    def remove_clip(self, track_id: str, clip_id: str):
        track = self._project.get_track(track_id)
        if track is None:
            log.warning("remove_clip: 找不到轨道 %s", track_id)
            return
        self._snapshot()
        track.remove_clip(clip_id)
        self.clip_removed.emit(track_id, clip_id)
        self.project_changed.emit()
        self._mark_dirty()

    def move_clip(self, clip_id: str, new_start: float):
        for track in self._project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    self._snapshot()
                    clip.start_time = max(0.0, new_start)
                    track.clips.sort(key=lambda c: c.start_time)
                    self.project_changed.emit()
                    self._mark_dirty()
                    return
        log.warning("move_clip: 找不到 clip %s", clip_id)

    def copy_clip(self, clip_id: str) -> Optional[Clip]:
        for track in self._project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip.copy()
        log.warning("copy_clip: 找不到 clip %s", clip_id)
        return None

    # =========================================================================
    #  过滤规则
    # =========================================================================

    def set_input_filter(self, rule: FilterRule):
        self._snapshot()
        self._project.input_filter = rule
        self._mark_dirty()

    def set_output_filter(self, rule: FilterRule):
        self._snapshot()
        self._project.output_filter = rule
        self._mark_dirty()
