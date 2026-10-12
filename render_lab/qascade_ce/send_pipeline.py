"""P9: 发送流水线 — 常驻发送线程 + C 侧发送环 + 异步 flush（stage C 流水线重叠）。

蓝图 COMPUTE_UTILIZATION_PLAN.md §3.3/§3.4/§5:
  - 网络发送线程常驻化（进程存活即存在, 零动态创建）;
  - 双/三缓冲环 C 侧预分配（3×512 记录 × 512B ≈ 0.8MB, 流式轮转, 零重分配）;
  - 发送与渲染并行: 发送线程读上一帧成品, 渲染写本帧另一缓冲。

两种形态（互斥, FLUSH_ASYNC 优先; 新路径默认关, 显式 env 开启）:

模式 A — QASCADE_CE_SEND_RING=1: 输出边界解耦
  NetworkManager._send_to_hw（类级替换）→ C 环 push（memcpy 512B, ~2µs）
  → 立即返回; 常驻发送线程 pop（事件等待, 禁 spin）→ 按 FIFO 逐记录调用
  真实边界。字节序 = 入队序 = 原调用序, 输出字节不变。
  反压（环满, M1 修复 AUDIT_BUGS_MEM）: C 侧覆盖环内最新旧记录（保序丢旧）
  —— 出队序 = 入队序的子序列, 最新记录必进环, 接收端状态单调前进收敛到
  最新; 绝不回退直发（原 100ms 超时直发让环内旧帧与直发新帧乱序交错）。
  探针拦截（实例属性 _send_to_hw = collect_hw）优先: 直接调用拦截器, 不绕环。

模式 B — QASCADE_CE_FLUSH_ASYNC=1: flush 整链异步（mix+xy+联锁+快照+发送）
  flush_universes_now（类级替换）在 CE 渲染路径武装窗口内捕获帧输入
  （层快照引用 + 引用集; update_layer 替换不原地改 → 引用安全）→
  3 槽预分配任务环（零分配轮转）→ 常驻线程按 FIFO 逐帧执行原版 flush
  语义（_mix_one_universe → _apply_xy_reference → 联锁 → 快照 → _send_to_hw）。
  字节序 = 帧序 + 每帧 universe 序 = 串行序 → 门1 哈希 73d9e1c2 恒过。
  反压: 任务环满 → tick 等待（环满 = 等信号, 蓝图 §5）。

位级等价保证（门1/门4）:
  - 输出边界 _send_to_hw 永不破: 所有记录最终经 _send_to_hw（或探针拦截器）
    原样发出, 顺序 FIFO 严格保持（反压环满时按 M1 保序丢旧 = 入队序子序列,
    丢失仅发生在消费者追不上生产者的持续过载, 接收端状态单调前进不回退）;
  - 输入捕获发生在 tick 线程（flush 调用的同一时刻）, 层条目为替换式引用,
    捕获后不再变化; LTP/默认值/锚点为引用（探针场景空/稳定 → 字节不变;
    真实应用 LTP 在途更新 → 该形态为实验态, 合入前需 LTP 快照硬化）;
  - AB 探针（bench/ce_probe_send_async_ab.py）逐帧对拍守护。

线程纪律: 常驻线程 + 事件等待（禁 spin）; 停机置位 + 事件唤醒, join 后销毁。
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

_U8 = ctypes.POINTER(ctypes.c_uint8)
_U16 = ctypes.POINTER(ctypes.c_uint16)

# ── 任务槽（3 槽预分配, 零分配轮转）────────────────────────────────────
_JOB_SLOTS = 3


class _Job:
    """一帧 flush 的输入捕获（引用持有; reset() 复用）."""

    __slots__ = ("frame_no", "universes", "snapshots")

    def __init__(self, layer_names):
        self.frame_no = -1
        self.universes = []
        self.snapshots = {name: {} for name in layer_names}
        self.reset()

    def reset(self):
        self.frame_no = -1
        self.universes.clear()
        for d in self.snapshots.values():
            d.clear()


class _JobRing:
    """3 槽任务环: 单生产者（tick）+ 单消费者（发送线程）; 环满反压.

    g14 修复（2026-08-04, RESOLVE_MANY×FLUSH_ASYNC 竞态根因）: 旧版
    prepare() 直接返回 _head 槽, 无「消费者处理中」预留 —— 环满回绕时
    生产者撞上消费者正在处理的槽（快照 dict 被清/重填 → 混合帧）;
    且消费者 release() 的 reset() 会清掉生产者刚发布的新任务（整帧
    丢失, 探针实测 3 实 3 空交替）。修复 = 槽生命周期双缓冲: 槽只在
    生产者认领（prepare）时重置, 消费者处理中槽标记 _busy, 生产者
    wait 到 busy 清除才可认领; release 只清 busy 不重置槽 → 帧 n 的
    槽数据在帧 n+1 渲染完成前绝不回收。
    """

    def __init__(self, layer_names, n=_JOB_SLOTS):
        self._n = n
        self._slots = [_Job(layer_names) for _ in range(n)]
        self._tail = 0
        self._filled = 0
        self._busy = None        # 消费者处理中的槽下标（None=无）
        self._cond = threading.Condition()

    def prepare(self):
        """tick 线程: 认领一槽（无空闲槽 → 阻塞等待 = 反压）; 认领即重置,
        消费者绝不触碰（_busy 槽不可认领, 已发布槽不可认领）."""
        with self._cond:
            while self._filled >= self._n - (1 if self._busy is not None else 0):
                self._cond.wait()
            i = (self._tail + self._filled) % self._n
            s = self._slots[i]
            s.reset()
            return s

    def publish(self):
        """tick 线程: 填充完成 → 发布 + 唤醒消费者."""
        with self._cond:
            self._filled += 1
            self._cond.notify_all()

    def acquire(self, timeout=0.25):
        """发送线程: 取一已填充槽（空则事件等待, 禁 spin）; 取走即标记
        busy（生产者绝不回写该槽）."""
        with self._cond:
            if self._filled == 0:
                self._cond.wait(timeout)
                if self._filled == 0:
                    return None
            i = self._tail
            s = self._slots[i]
            self._tail = (self._tail + 1) % self._n
            self._filled -= 1
            self._busy = i
            # 释放槽位 → 唤醒可能在 prepare() 反压等待的生产者（环满死锁修复）
            self._cond.notify_all()
            return s

    def release(self, s):
        """发送线程: 处理完毕 → 清除 busy（槽回池, 由生产者下次认领时重置）."""
        with self._cond:
            if self._busy is not None:
                self._busy = None
            self._cond.notify_all()

    def pending(self):
        with self._cond:
            return self._filled + (1 if self._busy is not None else 0)


class _Pipeline:
    """常驻发送流水线（会话级单例; 进程存活即存在, 零动态创建）."""

    def __init__(self, engine, nm_mod, layer_names):
        self.engine = engine
        self.nm_mod = nm_mod
        self._layer_names = tuple(layer_names)
        self._mode_b = os.environ.get("QASCADE_CE_FLUSH_ASYNC", "0") == "1"
        self._mode_a = (not self._mode_b
                        and os.environ.get("QASCADE_CE_SEND_RING", "0") == "1")
        self._ring = None
        self._jobs = _JobRing(self._layer_names)
        self._stop = threading.Event()
        self._thread = None
        # 在途计数（发送线程写, drain 读; GIL 下 int 读写安全）
        self._inflight = [0]
        self._cur_frame = [-1]
        self._n_pushed = [0]
        self._n_sent = [0]
        self._n_jobs = [0]
        self._n_fallback = [0]
        self._sent_bytes = [0]
        # Mode A 预分配消费缓冲（零分配）
        self._buf = np.empty(512, np.uint8)
        self._uni_c = ctypes.c_uint16()
        self._orig_send = None
        self._orig_flush = None
        self._flush_wrap = None

    # ── 生命周期 ────────────────────────────────────────────────────
    def start(self):
        if self._mode_b:
            self._wrap_flush()
        if self._mode_a:
            self._wrap_send()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ce-send-pipeline")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ring is not None:
            try:
                self.engine.ce_send_ring_stop(self._ring)
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                # M5 修复（AUDIT_BUGS_MEM.md）: 发送线程未退出（如 sendto 阻塞）
                # 时 destroy 环 → 线程访问已释放内存 UAF。泄漏优于崩溃:
                # 环不销毁, 交由进程退出回收; 下次 start 重建新环。
                import logging
                logging.getLogger(__name__).warning(
                    "[CE-P9] 发送线程 2s 未退出, 环保留（防 UAF）")
                self._thread = None
                return
        if self._ring is not None:
            try:
                self.engine.ce_send_ring_destroy(self._ring)
            except Exception:
                pass
            self._ring = None
        self._restore()

    def _restore(self):
        """还原类级替换点（幂等; stop 时恢复原版语义）."""
        if self._orig_flush is not None:
            try:
                self.nm_mod.NetworkManager.flush_universes_now = self._orig_flush
            except Exception:
                pass
            self._orig_flush = None
        if self._orig_send is not None:
            try:
                self.nm_mod.NetworkManager._send_to_hw = self._orig_send
            except Exception:
                pass
            self._orig_send = None

    # ── 模式 B: flush 整链异步 ──────────────────────────────────────
    def _wrap_flush(self):
        nm_mod = self.nm_mod
        self._orig_flush = nm_mod.NetworkManager.flush_universes_now

        def _ce_flush_wrap(self_, universes=None):
            from src.network.output_preview_control import final_output_preview_enabled
            if final_output_preview_enabled(self_):
                return self._orig_flush(self_, universes)
            if (os.environ.get("QASCADE_CE_FLUSH_ASYNC", "0") == "1"
                    and getattr(self_, "_ce_flush_async_armed", False)):
                return self._enqueue_flush(self_, universes)
            return self._orig_flush(self_, universes)

        self._flush_wrap = _ce_flush_wrap
        nm_mod.NetworkManager.flush_universes_now = _ce_flush_wrap

    def _enqueue_flush(self, nm, universes):
        """tick 线程: 捕获帧输入 → 入环（环满 = 反压等信号）."""
        if universes is None:
            universe_set = set()
            for layer in nm._layers.values():
                universe_set.update(layer.keys())
            universes = sorted(universe_set)
        else:
            universes = sorted({int(u) for u in universes})
        job = self._jobs.prepare()
        _fn = getattr(nm, "_ce_flush_frame_no", -1)
        job.frame_no = int(_fn) if _fn is not None else -1
        job.universes = universes
        for name in job.snapshots.keys():
            d = job.snapshots[name]
            d.clear()
            layer = nm._layers.get(name, {})
            for uni in universes:
                entry = layer.get(uni)
                if entry is not None:
                    d[uni] = entry
        self._jobs.publish()
        return None

    def _flush_job(self, nm, job):
        """发送线程: 原版 flush_universes_now 语义复刻（输入=捕获帧快照）.

        顺序与串行一致（帧 FIFO + 每帧 universe 排序）; 快照写入与
        _send_to_hw 与串行逐字节相同。锁纪律: mix 阶段无锁（输入已捕获）,
        仅 dirty 更新持锁。
        mix 采用 nm._mix_universes_batch（direct 模式, 应用默认批量路径,
        门2/harness 已验证与标量路径逐字节一致）— 一次批量调用替代 73×
        逐域 ctypes 包装, 流水线墙钟从 ~6.5ms 降至 ~2ms → 无反压, tick
        永不等待（GIL 竞争面同步收窄）。
        """
        universes = job.universes
        batch_mixed = None
        try:
            if (getattr(nm, "_shape_blend_mode", "") == "direct"
                    and hasattr(nm, "_mix_universes_batch")):
                batch_mixed = nm._mix_universes_batch(job.snapshots, universes)
        except Exception:
            batch_mixed = None
        for uni in universes:
            try:
                if batch_mixed is not None:
                    virtual_out = batch_mixed.get(int(uni))
                    if virtual_out is None:
                        continue
                else:
                    virtual_out = nm._mix_one_universe(job.snapshots, int(uni),
                                                       update_anchor=False)
                physical_out = nm._apply_xy_reference(int(uni), virtual_out)
                try:
                    # 与原版 flush_universes_now 同款联锁（绝对导入 —
                    # qascade_ce 顶层包无上级, 相对导入会 ImportError 静默吞掉联锁）
                    from src.engine.safety_interlock import get_interlock as _gi
                    _ilock = _gi()
                    if _ilock.is_armed:
                        physical_out = _ilock.process(int(uni), physical_out)
                except Exception:
                    pass
                if physical_out is virtual_out:
                    nm._universe_snapshot[int(uni)] = physical_out.copy()
                    nm._universe_virtual_snapshot[int(uni)] = nm._universe_snapshot[int(uni)]
                else:
                    nm._universe_virtual_snapshot[int(uni)] = virtual_out.copy()
                    nm._universe_snapshot[int(uni)] = physical_out.copy()
                if nm._running:
                    nm._send_to_hw(int(uni), physical_out)
                    self._n_sent[0] += 1
            except Exception:
                log.exception("CE async flush failed for universe %s", uni)
        try:
            with nm._state_lock:
                nm._dirty_universes.difference_update(set(universes))
        except Exception:
            pass

    # ── 模式 A: 输出边界解耦（C 环）─────────────────────────────────
    def _wrap_send(self):
        nm_mod = self.nm_mod
        engine = self.engine
        self._orig_send = nm_mod.NetworkManager._send_to_hw
        self._ring = engine.ce_send_ring_create()
        if self._ring is None:
            log.warning("[CE-P9] ce_send_ring_create 失败 → 发送环未启用")
            self._orig_send = None
            return

        def _ce_ring_send(self_, universe_1based, data):
            inst = self_.__dict__.get("_send_to_hw")
            if inst is not None:
                # 探针拦截（collect_hw）或实例替换 → 直接调用（哈希边界原样）
                return inst(universe_1based, data)
            from src.network.output_preview_control import final_output_preview_enabled
            if final_output_preview_enabled(self_):
                return self._orig_send(self_, universe_1based, data)
            try:
                d = np.ascontiguousarray(np.asarray(data, dtype=np.uint8),
                                         dtype=np.uint8)
                if d.shape == (512,):
                    # 多生产者串行化: 真实应用 _send_to_hw 从 mixer（Qt 主线程）/
                    # tick/UI 三处调用, 全部持 _state_lock（RLock 重入安全）;
                    # C 环是 SPSC — 统一在 _state_lock 上串行化全部生产者,
                    # 环契约保持（_send_to_hw 永不破）。环 push ≈ 2µs memcpy,
                    # 发送 I/O 仍在消费者线程 → 解耦目的不受影响。
                    with self_._state_lock:
                        rc = self.ce_push(int(universe_1based), d)
                        if rc == 1:
                            return None
            except Exception:
                pass
            # 停机(-2) / 异常 → 回退直发（边界永不破; 环满已由 C 侧保序丢旧
            # 处理, push 不再有超时回退路径 — M1 修复, 绝不直发绕过环）
            self._n_fallback[0] += 1
            return self._orig_send(self_, universe_1based, data)

        nm_mod.NetworkManager._send_to_hw = _ce_ring_send

    def ce_push(self, uni, data):
        if self._ring is None:
            return -1
        try:
            rc = self.engine.ce_send_ring_push(
                self._ring, ctypes.c_uint16(int(uni)),
                data.ctypes.data_as(_U8))
            if rc == 1:
                self._n_pushed[0] += 1
                self._sent_bytes[0] += 512
            return int(rc)
        except Exception:
            return -1

    def _send_one(self, nm, uni, data):
        inst = nm.__dict__.get("_send_to_hw")
        if inst is not None:
            inst(uni, data)
        elif self._orig_send is not None:
            self._orig_send(nm, uni, data)
        else:
            nm._send_to_hw(uni, data)
        self._n_sent[0] += 1

    # ── 发送线程主循环（事件等待, 禁 spin）──────────────────────────
    @staticmethod
    def _resolve_nm():
        """惰性解析 NetworkManager 单例 — 仅在首个任务到达时（探针/应用创建
        实例之后）; 严禁在 install 时创建单例（env 配置在实例化前生效）."""
        try:
            from src.network.network_manager import NetworkManager as _NM
            return _NM.instance()
        except Exception:
            return None

    def _run(self):
        nm = None
        while not self._stop.is_set():
            if self._mode_b:
                job = self._jobs.acquire()
                if job is None:
                    continue
                self._inflight[0] += 1
                try:
                    if nm is None:
                        nm = self._resolve_nm()
                    if nm is not None:
                        self._cur_frame[0] = job.frame_no
                        self._flush_job(nm, job)
                except Exception:
                    log.exception("CE 异步 flush 异常")
                finally:
                    self._jobs.release(job)
                    self._inflight[0] -= 1
            elif self._mode_a:
                if self._ring is None:
                    self._stop.wait(0.25)
                    continue
                try:
                    rc = self.engine.ce_send_ring_pop(
                        self._ring, ctypes.byref(self._uni_c),
                        self._buf.ctypes.data_as(_U8))
                except Exception:
                    rc = 0
                if rc == 0:
                    continue        # 停机或异常 → 主循环查 stop
                self._inflight[0] += 1
                try:
                    if nm is None:
                        nm = self._resolve_nm()
                    if nm is not None:
                        self._send_one(nm, int(self._uni_c.value), self._buf)
                finally:
                    self._inflight[0] -= 1
            else:
                self._stop.wait(0.25)

    # ── 诊断 ────────────────────────────────────────────────────────
    def pending(self):
        """未完成工作量（任务环 + C 环 + 在途）."""
        p = self._jobs.pending()
        if self._ring is not None:
            try:
                p += int(self.engine.ce_send_ring_pending(self._ring))
            except Exception:
                pass
        return p + self._inflight[0]

    def drain(self, timeout=30.0):
        """等待全部已入队记录送达边界（探针哈希前必须）."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.pending() == 0:
                return 0
            time.sleep(0.02)
        return self.pending()

    def stats(self):
        return {
            "mode_b": self._mode_b, "mode_a": self._mode_a,
            "pushed": self._n_pushed[0], "sent": self._n_sent[0],
            "fallback": self._n_fallback[0],
            "sent_bytes": self._sent_bytes[0],
            "pending": self.pending(),
            "cur_frame": self._cur_frame[0],
        }


# ── 模块级单例与公共 API（探针用）─────────────────────────────────────
_PIPE: _Pipeline | None = None


def install(engine) -> bool:
    """门控安装: QASCADE_CE_FLUSH_ASYNC=1 或 QASCADE_CE_SEND_RING=1 时启用."""
    global _PIPE
    if _PIPE is not None:
        return True
    if (os.environ.get("QASCADE_CE_FLUSH_ASYNC", "0") != "1"
            and os.environ.get("QASCADE_CE_SEND_RING", "0") != "1"):
        return False
    mode_b = os.environ.get("QASCADE_CE_FLUSH_ASYNC", "0") == "1"
    mode_a = (not mode_b and os.environ.get("QASCADE_CE_SEND_RING", "0") == "1")
    if mode_a and getattr(engine, "ce_send_ring_create", None) is None:
        # M3: DLL 无 P9 环符号 → 模式 A 不启用（不假死）; 模式 B 不依赖环符号
        return False
    try:
        from src.network import network_manager as nm_mod
        from src.network.network_manager import ALL_LAYERS as _LAYERS
    except Exception:
        return False
    _PIPE = _Pipeline(engine, nm_mod, _LAYERS)
    _PIPE.start()
    # GIL 竞争抑制: 流水线线程与 tick 线程同抢 GIL, 默认 5ms 切换间隔会让
    # tick 的 Python 段最多等 5ms（实测消费循环 1.7→6.5ms）。降至 1ms →
    # 等待上界 1ms; 段内字节码 < 1ms 时无额外切换开销。
    try:
        import sys as _sys
        if _sys.getswitchinterval() > 0.001:
            _sys.setswitchinterval(0.001)
    except Exception:
        pass
    log.info("[CE-P9] 发送流水线已启动: mode_a=%s mode_b=%s",
             _PIPE._mode_a, _PIPE._mode_b)
    return True


def is_active() -> bool:
    return _PIPE is not None


def drain(timeout=30.0) -> int:
    if _PIPE is None:
        return 0
    return _PIPE.drain(timeout=timeout)


def pending() -> int:
    if _PIPE is None:
        return 0
    return _PIPE.pending()


def cur_frame() -> int:
    if _PIPE is None:
        return -1
    return _PIPE._cur_frame[0]


def pipeline_stats() -> dict:
    if _PIPE is None:
        return {"active": False}
    return {"active": True, **_PIPE.stats()}


def stop() -> None:
    global _PIPE
    if _PIPE is not None:
        _PIPE.stop()
        _PIPE = None
