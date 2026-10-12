# -*- coding: utf-8 -*-
"""R178 时间线后台自动烘焙（极简 autobake）。

用户规格（记忆 r173-day-plan，2026-09-27 拍板）：
  · 顺序从 0 帧烘焙，禁止并行（引擎逐帧渲染存在长程淡入 seam 帧依赖）；
  · 编辑后 500ms 防抖失效重烘，可中断；
  · 已烘焙部分读缓存，未烘焙部分实时兜底（烘焙期渲染互斥见引擎
    `bake_timeline_now`；中断/失效即解锁回退实时，与现状一致）；
  · 时间线缓存条可视化（timeline_widget）；「渲染时间线」按钮移除；
    帧数据导出等待烘焙完成（main_window）；
  · 与手动 `lock_timeline_render_cache` 逐字节 A/B 对拍
    （`_offline_render_work/r178c/ab_bake.py`）。

线程模型：
  · 主线程：QTimer 500ms 防抖（连击合并）+ 信号槽；
  · 单一烘焙 QThread：轮询唤醒事件（250ms 兜底 + 即时唤醒），串行执行
    `bake_timeline_now`（内部序列与手动锁定路径逐字节一致）；
  · 中断 = 编辑代际号 +1，烘焙循环在阶段边界察觉即弃烘；半成品保持解锁。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from contextlib import nullcontext

from PyQt6.QtCore import QObject, QTimer, QThread, pyqtSignal

from .playback_engine import TimelineBakeAbort, bake_timeline_now
from .timeline_cache_notifications import reuse_published_bake
from .timeline_clip_invalidation import clip_invalidation_pending
from .timeline_input_yield import InputYieldBudget

log = logging.getLogger(__name__)

#: 存活烘焙器注册表（清查⑧ 全量回归稳定性）：pytest 不跑 app.exec() →
#: aboutToQuit 永不触发、窗口对象也可能不经 GC 就进入解释器收尾（届时
#: Python 各模块已开始拆卸，__del__ 时序不保证）——此处用 atexit 在收尾
#: 最早点统一停全部线程；注册表只存弱引用包装，避免阻止正常 GC。
import atexit as _atexit
import weakref as _weakref

_LIVE_BAKERS: "weakref.WeakSet" = _weakref.WeakSet()


def _shutdown_all_bakers() -> None:   # pragma: no cover - 收尾路径
    for b in list(_LIVE_BAKERS):
        try:
            b.shutdown()
        except Exception:
            pass


_atexit.register(_shutdown_all_bakers)


def _autobake_disabled() -> bool:
    """环境开关：`QASCADE_TIMELINE_AUTOBAKE` 默认**启用**（R178 审计 P1-5：
    「渲染时间线」按钮已移除，默认关 = 普通用户无任何手段预热缓存，缓存条
    恒空 —— 矛盾）。仅显式 `0/false/off/no` 关闭；基准探针
    （render_lab 测手动渲染路径逐字节哈希）仍用 `=0` 禁用 —— 烘焙与直接
    驱动 `_render_position` 的探针存在 NM 代理换装/渲染锁竞争。
    """
    return str(os.environ.get("QASCADE_TIMELINE_AUTOBAKE", "1")).strip().lower() in (
        "0", "false", "off", "no")


def _warm_audio_mod_features(proj) -> None:
    """R182：烘焙准备段同步预热音频特征（有调制 clips 才有代价，异常不阻断）。

    UI 帧路径的特征解析走 `background_on_miss=True`（miss 返回 None）；烘焙
    线程若在特征未就绪时开烘，逐帧调制会以"特征=1.0"烘进缓存 → 与实时听感
    不一致。这里在烘焙前于**烘焙线程**同步算好（get_features 同步语义），
    之后逐帧解析全部命中。
    """
    try:
        from .audio_mod_driver import warm_features
        if warm_features(proj):
            log.info("音频调制特征已预热（烘焙基准）")
    except Exception:
        log.warning("音频调制特征预热失败（继续烘焙）", exc_info=True)


class _BakeWorker(QThread):
    """单一烘焙线程：严格串行，一次只跑一条 `bake_timeline_now`。"""

    def __init__(self, baker: "TimelineAutoBaker"):
        super().__init__()
        # ★ 清查⑧（全量回归稳定性根因）：**弱引用**持有 baker。强引用会形成
        #   循环（baker._thread ↔ thread._baker）——循环 GC 在解释器收尾段
        #   不保证再跑，于是 baker.__del__（停线程+wait）可能永不执行，而
        #   QThread 仍在 Event.wait 中阻塞 → Python/Qt 双双 teardown 时崩溃
        #   （实测基座 3/10、含改动 6/10 段错误，faulthandler 指向本文件）。
        #   弱引用断开环：窗口一松手，baker 引用计数归零 → __del__ 必达。
        self._baker_ref = weakref.ref(baker)

    def run(self) -> None:
        while True:
            try:
                baker = self._baker_ref()
                # ★ `baker._stop` 必须在 try 内：baker 的包装对象可能已被删除，
                #   属性访问会抛 RuntimeError——若逃出 run()，PyQt6 会 qFatal
                #   终止进程（实测 access violation）。所有访问一个不落。
                if baker is None or baker._stop:
                    return
                baker._wake.wait(baker.POLL_SEC)   # 兜底轮询 + 即时唤醒复用同一事件
                baker._wake.clear()
                if baker._stop:
                    return
                baker._bake_pass()
            except RuntimeError:
                # 对象已析构（teardown 期）→ 唯一安全动作是退出。
                return
            except Exception:
                log.exception("时间线自动烘焙失败（下轮重试）")


def _bake_decision_deferred(baker, project):
    if vars(baker).get("_debounce_pending", False):
        return True
    if time.monotonic() < vars(baker).get("_debounce_until", 0.):
        return True
    from .timeline_ready_range import reuse_ready_bake
    if reuse_ready_bake(baker, project):
        return True
    if reuse_published_bake(baker, project):
        return True
    return baker._engine_is_playing() or baker._failed_gen == baker._gen


class TimelineAutoBaker(QObject):
    """时间线自动烘焙器：接管引擎渲染缓存锁定，编辑失效后自动重烘。

    信号（跨线程 emit，主线程接收）：
      progress(frac, baked_until_frame) — 烘焙进度（缓存条数据源）
      baked_full()                      — 一次烘焙完成且缓存已锁定
      invalidated()                     — 编辑失效（缓存条清零，实时兜底）
    """

    progress = pyqtSignal(float, int)
    baked_full = pyqtSignal()
    invalidated = pyqtSignal()

    DEBOUNCE_MS = 500     # 用户规格：编辑后 500ms 防抖失效重烘
    POLL_SEC = 0.25       # 空闲兜底轮询（捕获绕过 schedule_invalidate 的失效）

    def __init__(self, engine, project_provider, parent=None, *, playing_provider=None):
        super().__init__(parent)
        self._engine = engine
        self._provider = project_provider
        self._playing_provider = playing_provider
        self._gen = 0              # 编辑代际：+1 = 在烘的那次作废
        self._input_revision = 0   # 每次编辑立即作废旧输入；_gen 仍为防抖批次号。
        self._pending_input_check = False
        self._debounce_pending = False
        self._baked_gen = -1       # 已成功发布的代际
        self._failed_gen = -1      # 同一坏工程不在250ms轮询里反复分配/报错。
        self._stop = False
        self._baking = False
        self._baked_until = 0
        self._total_frames = 0
        self._wake = threading.Event()
        self._epoch_lock = threading.RLock()
        self._debounce_until = 0.0
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce_fired)
        self._thread = _BakeWorker(self)
        engine._timeline_auto_baker_ref = weakref.ref(self)
        self._thread.start()
        _LIVE_BAKERS.add(self)   # 清查⑧：解释器收尾兜底（atexit 统一停线程）

    def __del__(self):   # pragma: no cover - GC 时序防御
        # ★ R183 全量回归稳定性：对象被 GC（未走 aboutToQuit/destroyed）时先让
        #   工作线程看到停止位并等它退出，避免 QThread 随父对象析构时线程仍在
        #   运行（"Destroyed while thread is still running" → 进程级崩溃）。
        try:
            _LIVE_BAKERS.discard(self)
            self._stop = True
            self._wake.set()
            th = getattr(self, "_thread", None)
            if th is not None and th.isRunning():
                th.wait(2000)
        except Exception:
            pass

    # ── 主线程 API ──────────────────────────────────────────────────────────

    def schedule_invalidate(self, *, check_only=False) -> None:
        """编辑失效入口（mark_dirty / 时间线内容变化等汇聚点调用）。

        数值编辑 500ms 内多次失效只触发一次重烘（QTimer 重启合并）。
        只复核输入的通知立即交给后台完整检查；跨线程不操作 QTimer。
        """
        on_owner_thread = QThread.currentThread() is self.thread()
        with self._epoch_lock:
            self._input_revision += 1
            self._pending_input_check = bool(check_only)
            self._engine._timeline_render_cache_locked = False
            if check_only or not on_owner_thread:
                self._gen += 1
                self._debounce_until = 0.0
                self._debounce_pending = False
            else:
                self._debounce_pending = True
                self._debounce_until = time.monotonic() + self.DEBOUNCE_MS / 1000.
        if check_only or not on_owner_thread:
            # No numerical rebuild is requested until the worker has read the
            # complete mutable inputs. A busy UI must not delay that check.
            if check_only and on_owner_thread:
                self._debounce.stop()
            self._wake.set()
            return
        self._debounce.start()

    def ensure_running(self) -> None:
        """外部按需触发（缓存过期即开后台烘焙；已最新则空转一次）。"""
        self._wake.set()

    def shutdown(self) -> None:
        """停线程（幂等；aboutToQuit/destroyed/atexit 三路都调）。

        清查⑧：父窗口 destroyed 信号与 C++ 侧析构的先后在同一事件循环轮次里
        不保证——QThread 包裹对象可能已被删除，此时 isRunning() 会抛
        RuntimeError（探针实测）。停线程本身已无对象可停 → 全部吞掉。
        """
        self._stop = True
        self._wake.set()
        th = self._thread
        try:
            running = th is not None and th.isRunning()
        except RuntimeError:
            # The original C++ QThread was already retired by its owner.
            return
        if running:
            if QThread.currentThread() is th:
                raise RuntimeError("Autobake worker cannot wait for its own shutdown")
            if not th.wait():
                raise RuntimeError("Autobake worker did not finish before shutdown")

    def is_baking(self) -> bool:
        return bool(self._baking)

    def baked_until_frame(self) -> int:
        return int(self._baked_until)

    def total_frames(self) -> int:
        return int(self._total_frames)

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _on_debounce_fired(self) -> None:
        with self._epoch_lock:
            # A cross-thread validation cannot stop this owner-thread timer.
            # Ignore its stale timeout instead of creating another generation.
            if not self._debounce_pending:
                return
            self._gen += 1
            self._debounce_until = 0.0
            check_only = self._pending_input_check
            self._debounce_pending = False
        self._wake.set()
        if not check_only:
            self.invalidated.emit()

    def _bake_pass(self) -> None:
        """烘焙线程一次尝试：需要重烘则串行烘完（含中断/新鲜度处理）。"""
        proj = self._resolve_bake_project()
        if proj is None:
            return
        if not self._should_bake_now(proj):
            return
        self._run_bake(proj)

    def _resolve_bake_project(self):
        """取当前工程；不可烘（停转/并发/无工程/空时间线）返回 None。"""
        if self._stop:
            return None
        if self._baking:
            return None
        engine = self._engine
        if engine is None:
            return None
        try:
            proj = self._provider()
        except Exception:
            log.exception("autobake project_provider 失败，本轮跳过")
            return None
        if proj is None:
            return None
        try:
            # 空时间线不烘：空缓存会让 is_timeline_render_cache_current 恒 False
            # → 轮询空转；且无帧可烘，实时渲染本就是兜底。
            if float(engine._project_timeline_end(proj)) <= 0.0:
                return None
        except Exception:
            # R178 审计 P1-5：静默 except 让"工程异常 → 永不烘"无从排查。
            log.warning("autobake 时间线长度检查失败，本轮跳过", exc_info=True)
            return None
        return proj

    def _should_bake_now(self, proj) -> bool:
        """代际有新（编辑失效未消化）或缓存不新鲜 → 需要烘。播放中一律让位。"""
        with vars(self).get("_epoch_lock", nullcontext()):
            if clip_invalidation_pending(self):
                return False
            generation, revision = self._gen, vars(self).get("_input_revision", 0)
        # Qt may deliver its timeout after the wall-clock deadline. Never
        # recertify the old generation before that same edit batch is committed.
        # Validation reads inputs only, yields on the worker, and may recertify
        # an unchanged cache during playback; numerical rendering still defers.
        if _bake_decision_deferred(self, proj):
            # 播放优先：烘焙期渲染互斥会吞掉实时输出帧；停止后 250ms 轮询自动续烘。
            return False
        needed = (self._gen != self._baked_gen
                  or not self._cache_current(self._engine, proj))
        with vars(self).get("_epoch_lock", nullcontext()):
            return bool(needed and not vars(self).get("_stop", False)
                        and self._gen == generation and vars(self).get("_input_revision", 0) == revision
                        and not clip_invalidation_pending(self))

    def _engine_is_playing(self) -> bool:
        try:
            provider = getattr(self, "_playing_provider", None)
            if provider is not None and provider():
                return True
            return bool(self._engine is not None and self._engine.is_playing)
        except Exception:
            return False

    def _run_bake(self, proj) -> None:
        engine = self._engine
        my_gen = self._gen
        my_revision = vars(self).get("_input_revision", 0)
        self._baking = True
        self._baked_until = 0
        try:
            _warm_audio_mod_features(proj)   # R182：特征同步预热（防烘错）
            from .timeline_ready_range import handoff_options
            options = handoff_options(self, proj, my_gen, my_revision)
            options.update({
                "progress_callback": self._make_progress_cb(),
                "input_yield": InputYieldBudget(),
                "should_abort": lambda: bool(
                    self._stop or self._gen != my_gen
                    or vars(self).get("_input_revision", 0) != my_revision
                    or self._engine_is_playing()),
                "publish_lock": vars(self).get("_epoch_lock", nullcontext()),
            })
            status = bake_timeline_now(engine, proj, options)
        except Exception:
            self._failed_gen = my_gen
            log.exception("时间线自动烘焙失败，等待下一次编辑后重试")
            status = "failed"
        finally:
            self._baking = False
        with vars(self).get("_epoch_lock", nullcontext()):
            if vars(self).get("_input_revision", 0) != my_revision:
                status = "stale"
            self._finish_bake(status, my_gen)

    def _make_progress_cb(self):
        """进度边界检查停止、编辑代际及播放，让关闭/换工程及时中断。"""
        my_gen = self._gen
        my_revision = vars(self).get("_input_revision", 0)
        def _cb(info: dict) -> None:
            self._on_progress(info)
            if (self._stop or self._gen != my_gen
                    or vars(self).get("_input_revision", 0) != my_revision
                    or self._engine_is_playing()):
                raise TimelineBakeAbort("停止、编辑或播放，烘焙让位")
        return _cb

    def _finish_bake(self, status: str, my_gen: int) -> None:
        """发布结果：done 且代际仍最新 → 上报完成；否则报失效（下轮重烘）。"""
        if status == "partial" and not self._stop and self._gen == my_gen:
            ready = getattr(self._engine, "_timeline_ready_range", {})
            self._baked_until = int(ready["stop"])
            self._total_frames = int(ready["count"])
            self.progress.emit(self._baked_until / max(1, self._total_frames), self._baked_until)
            return
        if status != "done" or self._stop or self._gen != my_gen:
            # aborted / stale：失效状态保持（引擎缓存解锁惰性态），下一轮重烘
            self.invalidated.emit()
            return
        cache = getattr(self._engine, "_timeline_render_frame_cache", {}) or {}
        self._total_frames = len(cache)
        self._baked_until = self._total_frames
        self._baked_gen = my_gen
        self._pending_input_check = False
        self.progress.emit(1.0, self._baked_until)
        self.baked_full.emit()

    def _cache_current(self, engine, proj) -> bool:
        try:
            return bool(engine.is_timeline_render_cache_current(proj))
        except Exception:
            log.exception("is_timeline_render_cache_current 失败，按需重烘")
            return False

    def _on_progress(self, info: dict) -> None:
        """烘焙线程进度回调 → 主线程信号（烘焙线程内 emit，跨线程 queued）。"""
        phase = str(info.get("phase", "") or "")
        percent = max(0.0, min(1.0, float(info.get("percent", 0.0) or 0.0)))
        if phase == "render":
            # render 段的 current/total 才是帧记账（compile 段是 clip 计数）
            self._baked_until = max(
                self._baked_until, int(info.get("current", 0) or 0))
            self._total_frames = max(
                self._total_frames, int(info.get("total", 1) or 1))
        self.progress.emit(percent, self._baked_until)


def attach_timeline_auto_baker(engine, project_provider, parent=None, *, playing_provider=None):
    """创建并启动自动烘焙器（幂等由调用方保证）；禁用开关时返回 None。"""
    if _autobake_disabled():
        log.info("时间线自动烘焙已由 QASCADE_TIMELINE_AUTOBAKE=0 禁用")
        return None
    baker = TimelineAutoBaker(engine, project_provider, parent=parent,
                              playing_provider=playing_provider)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(baker.shutdown)
    except Exception:
        pass
    # ★ R183 全量回归稳定性：窗口被 GC/close 时（测试连开多窗最常见），
    #   Qt 会把 QThread 作为子对象一起析构；线程仍在跑 = "Destroyed while
    #   thread is still running" → Windows access violation（实测 6 连跑崩 5）。
    #   destroyed 信号在 ~QObject 删子对象**之前**发出 → 在此先停线程，
    #   是唯一来得及的窗口（aboutToQuit 只在真退出时才有）。
    if parent is not None:
        try:
            parent.destroyed.connect(lambda _obj=None, b=baker: b.shutdown())
        except Exception:
            pass
    log.info("时间线自动烘焙器已挂载（防抖 %dms）", TimelineAutoBaker.DEBOUNCE_MS)
    return baker
