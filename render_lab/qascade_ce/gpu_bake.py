"""GPU 离线烘焙混合接入 — ce_gpu_mix_batch（K0）路由 + 门控 + 同帧回退。

蓝图（render_lab/COMPUTE_UTILIZATION_PLAN.md）§4.1 路由规则 + §6.5 回退 +
§8.5 环境变量约定的离线侧实现：

- 门控: QASCADE_CE_GPU ∈ (1, auto, opencl, cuda) 才加载 GPU 后端; 未设/0
  = 纯 CPU 路径（门1 哈希 73d9e1c2 不漂, 代码默认 "0"）; exe 内经
  _mount_ce 挂载为 auto（自动识别, §8.5: auto 同 1）。
- 任务量阈值（2026-08-10 起为启动微基准自适应）: 本帧总通道数 < 阈值 → 走
  CPU 混合。默认阈值由首次使用 GPU 时对典型任务量网格实测 GPU vs CPU 耗时
  动态校准（见 _DEF_MIN_CHANNELS 下方校准块）; QASCADE_CE_GPU_MIN_CHANNELS
  设值 = 人工覆盖（语义保留）; 校准关闭/失败回落静态默认。
- 同帧回退（§6.5）: ce_gpu_mix_batch 返回非 0 → 本帧立即改路由 CPU 批量
  混合重算, 输出逐字节不变; 遥测计数 gpu_fail > 0 = 真实发生过（非静默）。
- 位级（§6.2）: 同一份打包字节同一份算术; K0 探针
  bench/ce_probe_gpu_equiv.py 9/9 PASS 实锤与 ce_mix_clips 逐位一致。
- 实测（2026-08-04 本机 780M, AMD 驱动 3661.0）: 混合内核转移受限
  （~2GB/s 驱动拷贝）, 311×2 域 CPU AVX2 0.45ms vs GPU 2.7-3.9ms →
  自适应校准在本机判"永不 GPU"（混合恒 CPU）; 高带宽工程机（NVIDIA PCIe）
  同逻辑按实测比值自动判 GPU——判决依据实测时间比而非机型。

线程安全: 单 dev 句柄不支持并发 run（ce_gpu_api.h）→ 全部 GPU 调用在
调用方锁（threading.Lock）内串行; 打包缓冲每线程独立。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

P_F32 = ctypes.POINTER(ctypes.c_float)
P_F64 = ctypes.POINTER(ctypes.c_double)
P_U8 = ctypes.POINTER(ctypes.c_uint8)
P_I32 = ctypes.POINTER(ctypes.c_int32)

# ce_gpu_api.h 错误码（§8: 0 成功 / 负值错误）
_CE_GPU_BACKEND_OPENCL = 2

# GPU 后端 DLL 探测顺序（同 backend.py _GPU_DLLS）: NVIDIA→CUDA, AMD→OpenCL
_GPU_DLLS = (("ce_gpu_cuda.dll", 1), ("ce_gpu_opencl.dll", _CE_GPU_BACKEND_OPENCL))


def _gpu_dll_dirs() -> tuple[str, ...]:
    """GPU DLL 候选目录（按序探测, 去重后返回; 与 backend.py 同构）。

    ① 包目录（__file__ 所在, 源码/onedir 部署）;
    ② QASCADE_CE_GPU_DIR（外部 DLL 目录——onefile exe 部署, _MEIPASS
       只读不可写, 4080 方案 A: 正式包 + 外部目录放 ce_gpu_cuda.dll）;
    ③ frozen: exe 同目录（sys.executable 的 dirname）; 开发态: cwd。
    CUDA/OpenCL 优先级由 _GPU_DLLS 顺序决定（外部目录的 CUDA 同样先于
    包内 OpenCL, 只要 cuda 名在先即优先）。
    """
    dirs = [os.path.dirname(os.path.abspath(__file__))]
    env_dir = os.environ.get("QASCADE_CE_GPU_DIR")
    if env_dir:
        dirs.append(env_dir)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        dirs.append(os.getcwd())
    seen = set()
    out = []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return tuple(out)

# 任务量阈值默认: 本帧总通道数 (n_univ×512) 低于此值 → 不走 GPU。
# 默认值 = 蓝图 §4.1 路由规则 "n_tasks < 512 → CPU" 的通道数等价
# （512 域 × 512 通道）; 本机 780M 实测 GPU 混合转移受限（AMD 驱动拷贝
# ~2GB/s, 每调用 2.7-3.9ms vs AVX2 0.1-0.3ms）——即使超阈值 GPU 也不如
# CPU, 故阈值保持蓝图保守值; 工程机（NVIDIA PCIe 32GB/s）按门4 实测调低。
# 2026-08-10 起该默认值仅作"校准失败/关闭"时的静态回落——真实阈值由
# 启动微基准自适应校准（见 _MIX_CALIB_DOMAINS 下方 calibrate_gpu）。
_DEF_MIN_CHANNELS = 262144

# ── 启动微基准自适应阈值（2026-08-10）────────────────────────────────────
# 替代写死阈值: GPU 后端首次使用时, 对典型任务量网格实测 GPU vs CPU 耗时,
# 取首个 GPU 严格反超的网格点作自适应阈值（决策依据实测时间比而非机型）:
#   780M 本机: 混合网格全输 → 永不 GPU; wave 网格在 64k-1M 反超 → 阈值≈64k
#   高带宽独显（4080）: 混合 8 域即反超 → 阈值=4096 通道; wave 同样反超
# 覆盖两类: 混合(mix, 域数网格 ×512 通道) 与 wave 烘焙(样本数网格)。
# 开销: 网格 3 次中位 × 两类, 本机实测合计 <100ms; 结果模块级缓存
# （_CALIB, 进程内只跑一次; 加锁防并发重复校准）。
# 人工覆盖: QASCADE_CE_GPU_MIN_CHANNELS（mix 通道数, 语义不变）/
#   QASCADE_CE_GPU_MIN_WAVE_VALUES（wave 样本数）/ QASCADE_CE_GPU_MIN_WAVE_FRAMES;
#   QASCADE_CE_GPU_CALIB=0 关闭校准（回落静态默认, 探针/诊断用）。
_MIX_CALIB_DOMAINS = (8, 32, 128, 512)          # 域数 → 通道数 = ×512
_WAVE_CALIB_SAMPLES = (4096, 65536, 1048576)    # n_frames × n_fixtures
_CALIB_LOCK = threading.Lock()
_CALIB = None                                   # 模块级缓存（进程内只跑一次）


def _env_int(key: str, default: int | None) -> int | None:
    """env 整型读取; 未设/非法 → default。"""
    raw = os.environ.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _median_ms(run, reps: int) -> float:
    """中位耗时（ms）; 异常向上抛 → 校准整体回落静态默认。"""
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        run()
        ts.append((time.perf_counter() - t0) * 1000.0)
    ts.sort()
    return ts[len(ts) // 2]


def _bench_mix_gpu(gpu, n_univ: int):
    """混合 GPU 计时任务: [U][2][512] 打包布局, nlays=2 全有效（与生产同构）。"""
    lmax = 2
    r = np.random.default_rng(n_univ * 131 + 7)
    layers = n_univ * lmax * 512
    vals = r.random(layers, dtype=np.float32) * 255.0
    acts = r.integers(0, 2, layers, dtype=np.uint8)
    anchors = r.random(layers, dtype=np.float32) * 255.0
    nlays = np.full(n_univ, lmax, np.int32)
    out = np.empty(n_univ * 512, np.uint8)
    any_ = np.empty(n_univ * 512, np.uint8)

    def run():
        rc = gpu.mix_batch(vals, acts, anchors, nlays, n_univ, lmax, 0.5,
                           out, any_)
        if rc != 0:
            raise RuntimeError(f"校准 mix rc={rc}: {gpu.last_error()}")
    return run


def _bench_mix_cpu(engine, n_univ: int):
    """混合 CPU 计时任务: 生产路径 ce_mix_clips_u8_batch（AVX2 批量）。"""
    lmax = 2
    r = np.random.default_rng(n_univ * 131 + 11)
    layers = n_univ * lmax * 512
    de = r.integers(0, 256, layers, dtype=np.uint8)
    eff = r.integers(0, 2, layers, dtype=np.uint8)
    anchors = r.random(layers, dtype=np.float32) * 255.0
    nlays = np.full(n_univ, lmax, np.int32)
    out = np.empty(n_univ * 512, np.uint8)
    any_ = np.empty(n_univ * 512, np.uint8)
    pv = (ctypes.POINTER(ctypes.c_uint8) * n_univ)()
    pa = (ctypes.POINTER(ctypes.c_uint8) * n_univ)()
    pn = (ctypes.POINTER(ctypes.c_float) * n_univ)()
    for u in range(n_univ):
        base = u * lmax * 512
        pv[u] = de[base:].ctypes.data_as(P_U8)
        pa[u] = eff[base:].ctypes.data_as(P_U8)
        pn[u] = anchors[base:].ctypes.data_as(P_F32)
    f = engine.ce_mix_clips_u8_batch

    def run():
        _ = (de, eff, anchors)  # 持有数据缓冲: 指针数组只存裸地址, 不保活数组
        f(pv, pa, pn, nlays.ctypes.data_as(P_I32), n_univ, 0.5,
          out.ctypes.data_as(P_U8), any_.ctypes.data_as(P_U8))
    return run


def _bench_wave_gpu(gpu, n_samples: int):
    """wave 烘焙 GPU 计时任务: 每帧一任务（与 K3 回调同构, 一次 launch 全帧）。"""
    n_frames = max(32, int(np.sqrt(n_samples)))
    n_fixtures = max(1, n_samples // n_frames)
    t = np.arange(n_frames, dtype=np.float64) / 40.0
    speed = np.full(n_frames, 1.0)
    phase = np.full(n_frames, 0.1)
    lo = np.zeros(n_frames)
    hi = np.full(n_frames, 255.0)
    fns = np.zeros(n_frames, np.int32)
    n_arr = np.full(n_frames, n_fixtures, np.int32)
    offsets = np.arange(n_frames + 1, dtype=np.int32) * n_fixtures
    out = np.empty(n_frames * n_fixtures, np.uint8)

    def run():
        rc = gpu.wave_batch(t, speed, phase, lo, hi, fns, n_arr, offsets,
                            n_frames, out.ctypes.data_as(P_U8))
        if rc != 0:
            raise RuntimeError(f"校准 wave rc={rc}: {gpu.last_error()}")
    return run


def _bench_wave_cpu(engine, n_samples: int):
    """wave 烘焙 CPU 计时任务: 串行参考 ce_compute_sequence（K3 验收 A 模式）。"""
    n_frames = max(32, int(np.sqrt(n_samples)))
    n_fixtures = max(1, n_samples // n_frames)
    out = np.empty(n_frames * n_fixtures, np.uint8)
    f = engine.ce_compute_sequence

    def run():
        f(40.0, 1.0, 0.1, 0.0, 255.0, 0, n_frames, n_fixtures,
          out.ctypes.data_as(P_U8))
    return run


def _decide_crossover(gpu_ms, cpu_ms, sizes):
    """首个 GPU 快于 CPU 且至少快 10%（裕量）的网格点 → 该点规模为阈值;
    全输 → None（永不 GPU）。

    GPU 耗时 = 固定启动开销 + 线性搬移（拷贝受限）; CPU ≈ 线性 → 单交叉点。
    10% 裕量吸收 iGPU 逐次抖动（780M 实测 64k 点 GPU 0.7~2.3ms 双峰）,
    避免阈值在相邻网格点间来回翻转; 决策仍完全由实测比值决定。
    """
    for gm, cm, size in zip(gpu_ms, cpu_ms, sizes, strict=True):
        if gm < cm * 0.9:
            return int(size)
    return None


def calibrate_gpu(gpu, engine) -> dict | None:
    """启动微基准自适应阈值（模块级缓存, 进程内只跑一次）。

    返回 dict: {mix_min_channels, wave_min_values, data{...}, device}。
    mix_min_channels 为 None = 混合永不走 GPU; 校准失败/关闭 → {"error": ...}
    或 {"disabled": True}（调用方回落静态默认）。GPU 调用在 gpu.lock 内串行。
    """
    global _CALIB
    if _CALIB is not None:
        return _CALIB
    with _CALIB_LOCK:
        if _CALIB is not None:
            return _CALIB
        if _env_int("QASCADE_CE_GPU_CALIB", 1) == 0:
            _CALIB = {"disabled": True}
            return _CALIB
        try:
            # 预热: iGPU DVFS 时钟爬升约 2 次调用才达稳态（实测 64k wave:
            # 前 2 次 3.2ms → 之后 6 次稳定 0.5ms）; CPU 首调同样偏慢
            # （首次 ctypes 调用 1.5ms vs 稳态 0.05ms）。64k 预热 2 次即可
            # 拉满时钟（1M 冷调 5ms/次, 收益与开销不成比例——1M 决策裕量
            # 3-4x, 首调略高仍过 0.9 裕量）。
            with gpu.lock:
                _bench_wave_gpu(gpu, _WAVE_CALIB_SAMPLES[-2])()
                _bench_wave_gpu(gpu, _WAVE_CALIB_SAMPLES[-2])()
                _bench_mix_gpu(gpu, _MIX_CALIB_DOMAINS[0])()
            _bench_mix_cpu(engine, _MIX_CALIB_DOMAINS[0])()
            _bench_wave_cpu(engine, _WAVE_CALIB_SAMPLES[0])()
            # 先全 GPU 后全 CPU: iGPU 与 CPU 共享内存带宽, 交错测量会
            # 互相扰动（GPU 测值被紧邻的 CPU 负载抬高 → 决策抖动）。
            # reps: mix 全 1（本机 CPU 快 20-40x, 1 次足够判定）; wave GPU
            # 4k/64k/1M = 1/4/1（64k 交叉邻域 4 次压噪）; wave CPU 4k/64k
            # = 2/2（CPU 无双态, 中位即稳）, 1M 1 次（参考 20ms+, 裕量 3x+）。
            mix_gpu = []
            for dom in _MIX_CALIB_DOMAINS:
                with gpu.lock:
                    mix_gpu.append(_median_ms(_bench_mix_gpu(gpu, dom), 1))
            wave_gpu = []
            for n in _WAVE_CALIB_SAMPLES:
                # 64k 为本机交叉邻域（1.0~1.8ms 双态, 与 CPU ~1.3ms 互咬）,
                # 4 次取中位压噪; 4k 决定性输（GPU 10x 慢）1 次足够; 1M 决策
                # 裕量 3x+, 1 次足够。
                reps = 1 if n >= _WAVE_CALIB_SAMPLES[-1] else (
                    4 if n == _WAVE_CALIB_SAMPLES[-2] else 1)
                with gpu.lock:
                    wave_gpu.append(_median_ms(_bench_wave_gpu(gpu, n), reps))
            mix_cpu = []
            for dom in _MIX_CALIB_DOMAINS:
                mix_cpu.append(_median_ms(_bench_mix_cpu(engine, dom), 1))
            wave_cpu = []
            for n in _WAVE_CALIB_SAMPLES:
                # CPU 无 DVFS 双态（稳定 1.29~1.35ms@64k）→ 2 次中位足够
                reps = 1 if n >= _WAVE_CALIB_SAMPLES[-1] else 2
                wave_cpu.append(_median_ms(_bench_wave_cpu(engine, n), reps))
            _CALIB = {
                "mix_min_channels": _decide_crossover(
                    mix_gpu, mix_cpu, [d * 512 for d in _MIX_CALIB_DOMAINS]),
                "wave_min_values": _decide_crossover(
                    wave_gpu, wave_cpu, list(_WAVE_CALIB_SAMPLES)),
                "data": {
                    "mix_domains": list(_MIX_CALIB_DOMAINS),
                    "mix_gpu_ms": [round(x, 4) for x in mix_gpu],
                    "mix_cpu_ms": [round(x, 4) for x in mix_cpu],
                    "wave_samples": list(_WAVE_CALIB_SAMPLES),
                    "wave_gpu_ms": [round(x, 4) for x in wave_gpu],
                    "wave_cpu_ms": [round(x, 4) for x in wave_cpu],
                },
                "device": gpu.name,
            }
            log.info(
                "[CE-GPU] 自适应阈值校准: mix=%s 通道 wave=%s 样本 "
                "(mix GPU %s / CPU %s ms; wave GPU %s / CPU %s ms)",
                _CALIB["mix_min_channels"], _CALIB["wave_min_values"],
                _CALIB["data"]["mix_gpu_ms"], _CALIB["data"]["mix_cpu_ms"],
                _CALIB["data"]["wave_gpu_ms"], _CALIB["data"]["wave_cpu_ms"])
        except Exception as e:  # noqa: BLE001 校准失败 → 调用方回落静态默认
            log.warning("[CE-GPU] 自适应阈值校准失败, 回落静态默认: %s", e)
            _CALIB = {"error": repr(e)}
        return _CALIB


def resolve_mix_min_channels(gpu, engine) -> int | None:
    """mix 自适应阈值: env 人工覆盖 > 校准 > 静态默认。None = 永不 GPU。"""
    if gpu is None or not gpu.available:
        return _DEF_MIN_CHANNELS
    v = _env_int("QASCADE_CE_GPU_MIN_CHANNELS", None)
    if v is not None:
        return v
    cal = calibrate_gpu(gpu, engine)
    if cal is None or "mix_min_channels" not in cal:
        return _DEF_MIN_CHANNELS
    return cal["mix_min_channels"]  # 可为 None = 本机混合永不 GPU


def resolve_wave_min_values(gpu, engine) -> int:
    """wave 烘焙自适应阈值（样本数）: env 人工覆盖 > 校准 > 静态默认。"""
    if gpu is None or not gpu.available:
        return _DEF_MIN_WAVE_VALUES
    v = _env_int("QASCADE_CE_GPU_MIN_WAVE_VALUES", None)
    if v is not None:
        return v
    cal = calibrate_gpu(gpu, engine)
    if (cal is None or "wave_min_values" not in cal
            or cal["wave_min_values"] is None):
        return _DEF_MIN_WAVE_VALUES
    return int(cal["wave_min_values"])


def resolve_wave_min_frames() -> int:
    """wave 烘焙帧数下限（结构约束, 非性能项）; env 可覆盖。"""
    return int(_env_int("QASCADE_CE_GPU_MIN_WAVE_FRAMES",
                        _DEF_MIN_WAVE_FRAMES) or _DEF_MIN_WAVE_FRAMES)


class GPUMix:
    """ce_gpu_mix_batch 后端句柄（OpenCL/CUDA 按 DLL 探测; 失败 = None 语义）。

    lock: 单 dev 句柄不支持并发 run（ce_gpu_api.h）→ 所有 GPU 调用经此锁串行;
    锁挂在句柄上（而非调用方）, 多线程多路由共享同一句柄时天然互斥。
    """

    def __init__(self, dll: ctypes.CDLL, backend_id: int):
        self.dll = dll
        self.backend_id = backend_id
        self.lock = threading.Lock()
        self._dev = None
        dll.ce_gpu_probe.restype = ctypes.c_int
        dll.ce_gpu_init.restype = ctypes.c_void_p
        dll.ce_gpu_init.argtypes = [ctypes.c_int, ctypes.c_char_p]
        dll.ce_gpu_destroy.argtypes = [ctypes.c_void_p]
        dll.ce_gpu_name.restype = ctypes.c_char_p
        dll.ce_gpu_name.argtypes = [ctypes.c_void_p]
        dll.ce_gpu_caps.restype = ctypes.c_uint32
        dll.ce_gpu_caps.argtypes = [ctypes.c_void_p]
        dll.ce_gpu_build.restype = ctypes.c_int
        dll.ce_gpu_build.argtypes = [ctypes.c_void_p]
        dll.ce_gpu_last_error.restype = ctypes.c_char_p
        dll.ce_gpu_last_error.argtypes = [ctypes.c_void_p]
        dll.ce_gpu_mix_batch.restype = ctypes.c_int
        dll.ce_gpu_mix_batch.argtypes = [
            ctypes.c_void_p, P_F32, P_U8, P_F32, P_I32,
            ctypes.c_int, ctypes.c_int, ctypes.c_float, P_U8, P_U8]
        if dll.ce_gpu_probe() == 0:
            return  # 本 DLL 后端不可用
        self._dev = dll.ce_gpu_init(backend_id, None)
        if not self._dev:
            return
        if not (dll.ce_gpu_caps(self._dev) & (1 << 0)):  # CE_GPU_CAP_EXACT_MIX
            dll.ce_gpu_destroy(self._dev)
            self._dev = None
            return

    @property
    def available(self) -> bool:
        return self._dev is not None

    @property
    def name(self) -> str:
        if self._dev is None:
            return ""
        n = self.dll.ce_gpu_name(self._dev)
        return n.decode("utf-8", "replace") if n else ""

    def build(self) -> bool:
        if self._dev is None:
            return False
        return self.dll.ce_gpu_build(self._dev) == 1

    def last_error(self) -> str:
        if self._dev is None:
            return "no device"
        e = self.dll.ce_gpu_last_error(self._dev)
        return e.decode("utf-8", "replace") if e else ""

    def mix_batch(self, vals: np.ndarray, acts: np.ndarray,
                  anchors: np.ndarray, nlays: np.ndarray,
                  n_univ: int, lmax: int, mod_gain: float,
                  out: np.ndarray, act_any: np.ndarray) -> int:
        """打包 [U][lmax][512] 一次调用（K0）。返回 0 成功 / -1 失败。"""
        if self._dev is None:
            return -1
        return int(self.dll.ce_gpu_mix_batch(
            self._dev,
            vals.ctypes.data_as(P_F32), acts.ctypes.data_as(P_U8),
            anchors.ctypes.data_as(P_F32), nlays.ctypes.data_as(P_I32),
            int(n_univ), int(lmax), float(mod_gain),
            out.ctypes.data_as(P_U8), act_any.ctypes.data_as(P_U8)))

    def destroy(self):
        if self._dev is not None:
            self.dll.ce_gpu_destroy(self._dev)
            self._dev = None

    # ── K3-GPU: wave 序列批量烘焙（2026-08-09 接入）────────────────────
    # ce_gpu_wave_batch 语义: ntask 个任务, 每任务独立 (t, speed, phase_step,
    # lo, hi, wave_fn, n[帧数]); offsets[task] 为输出行偏移(累加), 总输出
    # total = offsets[ntask] 行 × n_fixtures 列。与 ce_compute_sequence 位级
    # 对拍映射: 每帧一任务(ntask=n_frames), t[task]=task/fps, n[task]=
    # n_fixtures → 内核 phase = (g-offset)*phase_step = i*phase_step(逐台),
    # t*speed 同 C 侧逐帧公式(实测 409 万样本 0 失配, ce_gpu_opencl_result)。
    # out 由调用方提供(外部指针直接落盘, 零拷贝)。
    def wave_batch(self, t: np.ndarray, speed: np.ndarray,
                   phase_step: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                   wave_fn: np.ndarray, n: np.ndarray, offsets: np.ndarray,
                   ntask: int, out_ptr) -> int:
        """批量 wave 序列烘焙。返回 0 成功 / -1 失败（调用方回退 CPU）。"""
        if self._dev is None:
            return -1
        fn = getattr(self, "_wave_batch_fn", None)
        if fn is None:
            self.dll.ce_gpu_wave_batch.restype = ctypes.c_int
            self.dll.ce_gpu_wave_batch.argtypes = [
                ctypes.c_void_p,
                P_F64, P_F64, P_F64, P_F64, P_F64,   # t, speed, phase_step, lo, hi
                P_I32, P_I32, P_I32,                 # wave_fn, n, offsets
                ctypes.c_int,                        # ntask
                P_U8]                                # out
            fn = self.dll.ce_gpu_wave_batch
            self._wave_batch_fn = fn
        rc = int(fn(
            self._dev,
            np.ascontiguousarray(t, np.float64).ctypes.data_as(P_F64),
            np.ascontiguousarray(speed, np.float64).ctypes.data_as(P_F64),
            np.ascontiguousarray(phase_step, np.float64).ctypes.data_as(P_F64),
            np.ascontiguousarray(lo, np.float64).ctypes.data_as(P_F64),
            np.ascontiguousarray(hi, np.float64).ctypes.data_as(P_F64),
            np.ascontiguousarray(wave_fn, np.int32).ctypes.data_as(P_I32),
            np.ascontiguousarray(n, np.int32).ctypes.data_as(P_I32),
            np.ascontiguousarray(offsets, np.int32).ctypes.data_as(P_I32),
            int(ntask), out_ptr))
        return rc


# 模块级句柄缓存（2026-09-18）: 每次 load_gpu_mix() 都新建 CDLL + GPU 上下文
# 会重复占显存与初始化时间（K3 烘焙 hook 与 MixRouter 各调一次 → 2 份上下文）。
# 缓存单例; 显式 destroy 或探测失败时清除。reset_gpu_mix_cache() 供探针用。
_GPU_MIX_CACHE: dict = {"gm": None, "tried": False}


def load_gpu_mix(use_cache: bool = True) -> GPUMix | None:
    """探测链加载（CUDA→OpenCL; 任一失败返回 None, 调用方纯 CPU）。

    每个 DLL 名依次在 _gpu_dll_dirs() 各目录中尝试——包内 DLL 仍最先命中
    （① 优先）; onefile exe（_MEIPASS 只读）从外部目录加载 DLL。
    命中后过「首次使用自动对拍自检」（见 _selfcheck_gpu）: mix 位级 0 差异
    才启用, 否则本后端禁用并继续探测链（回退 CPU/下一后端, 字节不变）。
    use_cache=True（默认）: 进程内单例句柄（避免重复 GPU 上下文）;
    探针/测试要新句柄时传 False 或先 reset_gpu_mix_cache()。
    """
    if use_cache:
        if _GPU_MIX_CACHE["gm"] is not None:
            return _GPU_MIX_CACHE["gm"]
        if _GPU_MIX_CACHE["tried"]:
            return None
    for dll_name, backend_id in _GPU_DLLS:
        for dll_dir in _gpu_dll_dirs():
            path = os.path.join(dll_dir, dll_name)
            if not os.path.exists(path):
                continue
            try:
                dll = ctypes.CDLL(path)
                gm = GPUMix(dll, backend_id)
                if gm.available:
                    if not _selfcheck_gpu(gm):
                        log.warning("[CE-GPU] %s 自检未通过 → 本后端禁用, "
                                    "回退 CPU（输出逐字节不变）", path)
                        gm.destroy()
                        continue
                    log.info("[CE-GPU] 烘焙混合后端: %s（%s）", gm.name, path)
                    if use_cache:
                        _GPU_MIX_CACHE["gm"] = gm
                        _GPU_MIX_CACHE["tried"] = True
                    return gm
            except Exception as e:  # noqa: BLE001 探测失败 = 该后端不可用
                log.warning("[CE-GPU] %s@%s 探测异常: %s", dll_name, dll_dir, e)
    if use_cache:
        _GPU_MIX_CACHE["tried"] = True
    return None


def reset_gpu_mix_cache() -> None:
    """清缓存（探针/测试用）; 已取出的句柄由调用方自行 destroy。"""
    _GPU_MIX_CACHE["gm"] = None
    _GPU_MIX_CACHE["tried"] = False


# ── 首次使用自动对拍自检（2026-09-18, 阶段1.6）─────────────────────────
# 目的: 目标机零操作的安全网——CUDA DLL 由开发机交叉编译随包交付, 目标机
# 首次真正要用 GPU 前, 用少量随机数据把 GPU 结果与 CPU-C 权威路径逐字节
# 对拍（K0 mix, CE_GPU_CAP_EXACT_MIX 契约）。失败 → 本进程禁用该后端并
# 告警, 自动回退 CPU（演出/烘焙不中断, 门1 哈希不漂）。
# 门控 QASCADE_CE_GPU_SELFCHECK: "0" 关闭（诊断逃生口）; 默认开。
# 每会话一次（结果缓存）; 开销 ~10ms 级（24 轮小批量）。
_SELFCHECK = {"done": False, "ok": True}
_SELFCHECK_LOCK = threading.Lock()


def _cpu_engine():
    """CPU 参考引擎（自检对拍用）; 不可用返回 None（则跳过对拍放行）。"""
    try:
        from .ctypes_api import load_or_none
        return load_or_none()
    except Exception:
        return None


def _selfcheck_gpu(gm: GPUMix) -> bool:
    """GPU vs CPU-C 严格对拍（mix）; True=通过/跳过, False=禁用该后端。"""
    if (os.environ.get("QASCADE_CE_GPU_SELFCHECK", "1") or "1").strip() == "0":
        return True
    with _SELFCHECK_LOCK:
        if _SELFCHECK["done"]:
            return bool(_SELFCHECK["ok"])
        _SELFCHECK["done"] = True
        eng = _cpu_engine()
        if eng is None or eng.ce_mix_clips is None:
            log.info("[CE-GPU] 自检跳过（无 CPU 参考引擎）, GPU 直接启用")
            _SELFCHECK["ok"] = True
            return True
        try:
            r = np.random.RandomState(0x5EED2026)
            mism = 0
            total = 0
            for rd in range(24):
                n_univ = 16
                lmax = int(r.randint(1, 5))
                nlays = r.randint(0, lmax + 1, size=n_univ).astype(np.int32)
                if rd % 5 == 0:
                    nlays[0] = 0                      # nlay=0 边界
                layers = n_univ * lmax * 512
                bits = r.randint(0, 1 << 32, size=layers,
                                 dtype=np.uint64).astype(np.uint32)
                vals = np.ascontiguousarray(bits.view(np.float32))
                bits = r.randint(0, 1 << 32, size=layers,
                                 dtype=np.uint64).astype(np.uint32)
                anchors = np.ascontiguousarray(bits.view(np.float32))
                acts = r.randint(0, 2, size=layers, dtype=np.uint8)
                mod_gain = float(np.float32(r.random_sample()))
                out = np.zeros(n_univ * 512, np.uint8)
                any_ = np.zeros(n_univ * 512, np.uint8)
                rc = gm.mix_batch(vals, acts, anchors, nlays, n_univ, lmax,
                                  mod_gain, out, any_)
                if rc != 0:
                    log.warning("[CE-GPU] 自检 GPU 调用失败 rc=%d: %s",
                                rc, gm.last_error())
                    _SELFCHECK["ok"] = False
                    return False
                ref = np.zeros(n_univ * 512, np.uint8)
                ref_any = np.zeros(n_univ * 512, np.uint8)
                for u in range(n_univ):
                    off = u * lmax * 512
                    eng.ce_mix_clips(
                        vals[off:].ctypes.data_as(P_F32),
                        acts[off:].ctypes.data_as(P_U8),
                        anchors[off:].ctypes.data_as(P_F32),
                        int(nlays[u]), mod_gain,
                        ref[u * 512:(u + 1) * 512].ctypes.data_as(P_U8),
                        ref_any[u * 512:(u + 1) * 512].ctypes.data_as(P_U8))
                mism += int(np.count_nonzero(out != ref))
                mism += int(np.count_nonzero(any_ != ref_any))
                total += 2 * n_univ * 512
            if mism == 0:
                log.info("[CE-GPU] 自检 PASS: mix 位级对拍 %d 通道 0 差异（%s）",
                         total, gm.name)
                _SELFCHECK["ok"] = True
                return True
            log.warning("[CE-GPU] 自检 FAIL: mix 位级差异 %d/%d —— 禁用 GPU, "
                        "回退 CPU（输出逐字节不变; 检查 DLL 架构/驱动）",
                        mism, total)
            _SELFCHECK["ok"] = False
            return False
        except Exception as e:  # noqa: BLE001 自检异常 = 不信任该后端
            log.warning("[CE-GPU] 自检异常 → 禁用 GPU, 回退 CPU: %s", e)
            _SELFCHECK["ok"] = False
            return False


class MixRouter:
    """烘焙混合路由（连续池布局）: 每帧选 GPU 或 CPU, 失败同帧回退 CPU,
    输出逐字节不变。

    输入布局（烘焙 worker 的池布局, 见 ce_bench_bake.BakePhase）:
      每帧全部域按任务行序连续排布; phase 提供:
        n_univ / lmax / counts[u]（每域候选层数, 含零有效层——零层 d=0.0
        与逐层同序累加, 位级等价于剔除, 见 ce_engine.c ce_mix_clips 头注释）
        row 偏移 = phase.starts（每域起始行）× 512
      de_pool/eff_pool: u8 [n*512]（resolve 输出: 层数据 + 有效掩码）
      vals_f32: f32 [n*512]（u8→f32 逐帧转换, 与引擎 astype 同值）
      anchors:  f32 [n*512]（每任务行锚点, phase 缓存）
    out_data/out_any: (n_univ×512) u8 输出（域序 = phase.univs）
    """

    def __init__(self, engine, gpu: GPUMix | None,
                 min_channels: int | None = None):
        """min_channels=None = 自适应（env 覆盖 > 启动微基准校准 > 静态默认）;
        None 结果 = 校准判本机混合永不走 GPU。"""
        self.engine = engine
        self.gpu = gpu
        if min_channels is None:
            self.min_channels = resolve_mix_min_channels(gpu, engine)
        else:
            self.min_channels = max(1024, int(min_channels))
        self.cnt_gpu_calls = 0
        self.cnt_cpu_calls = 0
        self.cnt_gpu_fail = 0        # 遥测: >0 = GPU 真实失败回退过
        self.cnt_fallback_frames = 0
        self._cap_u = 0
        self._cap_l = 0
        self._pack_v = np.empty(0, np.float32)
        self._pack_a = np.empty(0, np.uint8)
        self._pack_n = np.empty(0, np.float32)
        self._pack_l = np.empty(0, np.int32)
        # GPU 打包索引映射（phase 缓存: (dst_row, src_row) 对）
        self._gpu_idx_v = None

    def _ensure_gpu_pack(self, n_univ: int, lmax: int):
        if n_univ <= self._cap_u and lmax <= self._cap_l:
            return
        u = max(n_univ, self._cap_u)
        l = max(lmax, self._cap_l)
        n = u * l * 512
        self._pack_v = np.empty(n, np.float32)
        self._pack_a = np.empty(n, np.uint8)
        self._pack_n = np.empty(n, np.float32)
        self._pack_l = np.empty(u, np.int32)
        self._cap_u = u
        self._cap_l = l

    def cache_gpu_index(self, phase) -> None:
        """按 phase 缓存 [U][lmax][512] 打包索引（一次; 帧间复用零打包开销）。"""
        n_univ = phase.n_univ
        lmax = phase.lmax
        self._ensure_gpu_pack(n_univ, lmax)
        dst = np.empty(n_univ * lmax, np.int32)
        src = np.empty(n_univ * lmax, np.int32)
        k = 0
        for u in range(n_univ):
            n = int(phase.counts[u])
            base_src = int(phase.starts[u])
            for l in range(n):
                dst[k] = u * lmax + l
                src[k] = base_src + l
                k += 1
        self._gpu_idx_v = (dst[:k], src[:k], n_univ, lmax)

    def mix_contiguous(self, phase, de_pool: np.ndarray, eff_pool: np.ndarray,
                       anchors: np.ndarray, mod_gain: float,
                       out_data: np.ndarray, out_any: np.ndarray,
                       force_gpu: bool = False) -> None:
        """混合一帧（池布局, vals 为 u8 直读）。out_data/out_any: (n_univ×512),
        域序=phase.univs。CPU 路径走 ce_mix_clips_u8_batch（v=(float)u8, 位级
        = astype 路径）; GPU 路径打包 [U][lmax][512] f32（u8→f32 在打包内做,
        与 CPU 路径同值）。force_gpu: 门4 运行期抽查用——无视阈值强制 GPU。"""
        n_univ = phase.n_univ
        if n_univ == 0:
            return
        total = n_univ * 512
        if (self.gpu is not None and self.gpu.available
                and (force_gpu
                     or (self.min_channels is not None
                         and total >= self.min_channels))):
            try:
                # 打包 [U][lmax][512]（索引映射 phase 缓存; 只填有效层行,
                # 内核按 nlays=counts 只读这些行 → 位级与池布局一致）
                if (self._gpu_idx_v is None
                        or self._gpu_idx_v[2] != n_univ
                        or self._gpu_idx_v[3] != phase.lmax):
                    self.cache_gpu_index(phase)
                dst, src, _, _ = self._gpu_idx_v
                v2 = self._pack_v.reshape(-1, 512)
                a2 = self._pack_a.reshape(-1, 512)
                n2 = self._pack_n.reshape(-1, 512)
                v2[dst] = de_pool.reshape(-1, 512)[src].astype(np.float32)
                a2[dst] = eff_pool.reshape(-1, 512)[src]
                n2[dst] = anchors.reshape(-1, 512)[src]
                with self.gpu.lock:  # 共享锁: 多路由共句柄时互斥（非线程安全句柄）
                    rc = self.gpu.mix_batch(
                        self._pack_v, self._pack_a, self._pack_n,
                        phase.counts, n_univ, phase.lmax, mod_gain,
                        out_data[:total], out_any[:total])
                if rc == 0:
                    self.cnt_gpu_calls += 1
                    return
                # §6.5: GPU 失败 → 同帧回退 CPU, 输出逐字节不变
                self.cnt_gpu_fail += 1
                self.cnt_fallback_frames += 1
                log.warning("[CE-GPU] 混合失败回退 CPU: %s",
                            self.gpu.last_error())
            except Exception as e:  # noqa: BLE001 回退 CPU（同帧, 字节不变）
                self.cnt_gpu_fail += 1
                self.cnt_fallback_frames += 1
                log.warning("[CE-GPU] 混合异常回退 CPU: %s", e)
        self._mix_cpu(phase, de_pool, eff_pool, anchors,
                      mod_gain, out_data, out_any)
        self.cnt_cpu_calls += 1

    def _mix_cpu(self, phase, de_pool: np.ndarray, eff_pool: np.ndarray,
                 anchors: np.ndarray, mod_gain: float,
                 out_data: np.ndarray, out_any: np.ndarray) -> None:
        """CPU 批量混合（ce_mix_clips_u8_batch, vals u8 直读; 指针 phase 缓存）。"""
        engine = self.engine
        f = engine.ce_mix_clips_u8_batch
        if phase.ptrs_cpu is None:
            n_univ = phase.n_univ
            pv = (ctypes.POINTER(ctypes.c_uint8) * n_univ)()
            pa = (ctypes.POINTER(ctypes.c_uint8) * n_univ)()
            pn = (ctypes.POINTER(ctypes.c_float) * n_univ)()
            for u in range(n_univ):
                base = int(phase.starts[u]) * 512
                pv[u] = de_pool[base:].ctypes.data_as(P_U8)
                pa[u] = eff_pool[base:].ctypes.data_as(P_U8)
                pn[u] = anchors[base:].ctypes.data_as(P_F32)
            phase.ptrs_cpu = (pv, pa, pn)
        pv, pa, pn = phase.ptrs_cpu
        f(pv, pa, pn, phase.counts.ctypes.data_as(P_I32),
          phase.n_univ, float(mod_gain),
          out_data[:phase.n_univ * 512].ctypes.data_as(P_U8),
          out_any[:phase.n_univ * 512].ctypes.data_as(P_U8))

    def stats(self) -> dict:
        return {
            "gpu_calls": self.cnt_gpu_calls,
            "cpu_calls": self.cnt_cpu_calls,
            "gpu_fail": self.cnt_gpu_fail,
            "fallback_frames": self.cnt_fallback_frames,
        }


class GPUBAKE:
    """烘焙 GPU 总闸门: QASCADE_CE_GPU=1 才加载; 否则纯 CPU（哈希不漂）。"""

    def __init__(self):
        self.gpu: GPUMix | None = None
        self.enabled = False
        self.why = ""

    def init(self) -> bool:
        gate = os.environ.get("QASCADE_CE_GPU", "0")
        if gate not in ("1", "auto", "opencl", "cuda"):
            self.why = "QASCADE_CE_GPU 未启用（=纯 CPU 路径）"
            return False
        self.gpu = load_gpu_mix()
        if self.gpu is None:
            self.why = "GPU 后端不可用（探测失败）"
            return False
        if not self.gpu.build():
            self.why = "GPU 内核编译失败: " + self.gpu.last_error()
            self.gpu.destroy()
            self.gpu = None
            return False
        self.enabled = True
        return True

    def make_router(self, engine, min_channels: int | None = None) -> MixRouter:
        """min_channels=None → MixRouter 自适应（env 覆盖 > 校准 > 静态默认）。"""
        if min_channels is None and os.environ.get("QASCADE_CE_GPU_MIN_CHANNELS"):
            min_channels = _env_int("QASCADE_CE_GPU_MIN_CHANNELS", None)
        return MixRouter(engine, self.gpu if self.enabled else None,
                         min_channels=min_channels)

    def destroy(self):
        if self.gpu is not None:
            self.gpu.destroy()
            self.gpu = None
        self.enabled = False


_BAKE = GPUBAKE()


def gpu_bake_init() -> GPUBAKE:
    """模块级单例初始化（幂等）。"""
    if not _BAKE.enabled and not _BAKE.why:
        _BAKE.init()
    return _BAKE


# ── K3-GPU: 离线烘焙 GPU 分派注册（2026-08-09 用户指令: 离线渲染充分
#    调用算力, OpenCL/CUDA 自动识别最佳方案）──────────────────────────
# 接入点 = C 侧 ce_bake_sequence_omp 的预留分派点（ce_engine.c: 分派序
# GPU 钩子 → OpenMP → 串行）; 回调返回 1=已烘焙(out 与 ce_compute_sequence
# 逐字节相等) / 0=未处理 → 调用方回退 OpenMP 并行（逐字节不变）。
# 自动识别: DLL 探测链 CUDA→OpenCL（load_gpu_mix）; 开发机 AMD 780M 走
# OpenCL（wave 烘焙实测 3.65x）, 正式机 4080 部署 ce_gpu_cuda.dll 后自动
# 走 CUDA, 零代码差异。
# 阈值: 任务量不足 GPU 启动开销 > 收益 → 直接回退（与 mix 同策略）;
# GPU 任一失败/异常 → 回退 OpenMP, 正确性零风险（回调语义保证）。
# 2026-08-10 起为启动微基准自适应（resolve_wave_min_values）: 此静态值仅作
# 校准失败/关闭/env 未覆盖时的回落（780M 校准判 wave 阈值≈64k 样本）。
_DEF_MIN_WAVE_VALUES = 262144   # n_frames × n_fixtures 低于此值不走 GPU（静态回落）
_DEF_MIN_WAVE_FRAMES = 32       # 帧数低于此值不摊销 GPU 启动/拷贝（静态回落）

# K3 回调签名（ce_gpu_api.h / ce_engine.c 分派契约）:
#   int cb(double fps, double speed, double phase_step, double lo, double hi,
#          int wave_fn_idx, int n_frames, int n_fixtures, uint8_t* out)
_K3_BAKE_CB_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_double, ctypes.c_double, ctypes.c_double,
    ctypes.c_double, ctypes.c_double,
    ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint8))

_bake_cb_registered = False


def install_bake_gpu(engine) -> bool:
    """注册 K3 GPU 烘焙分派回调（幂等）。返回 True=已注册/False=跳过。

    QASCADE_CE_GPU ∈ (1, auto, opencl, cuda) 且 DLL 存在才注册; 否则
    零改动（纯 CPU/OpenMP 路径, 门1 哈希不漂）。GPU 探测懒加载（首次
    烘焙回调才 init, 启动零开销）。
    """
    global _bake_cb_registered
    if _bake_cb_registered:
        return True
    if not hasattr(engine, "ce_gpu_bake_sequence_register"):
        return False
    gate = os.environ.get("QASCADE_CE_GPU", "0")
    if gate not in ("1", "auto", "opencl", "cuda"):
        return False

    def _cb(fps, speed, phase_step, lo, hi, wave_idx,
            n_frames, n_fixtures, out_ptr):
        if n_frames < resolve_wave_min_frames() or n_fixtures < 1:
            return 0
        # GPU 探测懒加载（首次 ≥帧门 的烘焙回调才 init; 校准在 init 后首次
        # 执行并缓存 <100ms, 本任务与后续任务均按自适应阈值路由——替代写死
        # _DEF_MIN_WAVE_VALUES, 高带宽机器自动放宽、低带宽机器自动收紧）
        gpu = gpu_bake_init().gpu
        if gpu is None or not gpu.available:
            return 0
        if n_frames * n_fixtures < resolve_wave_min_values(gpu, engine):
            return 0
        try:
            # 每帧一任务: t[task]=task/fps, n[task]=n_fixtures（位级对拍映射）
            t = np.arange(n_frames, dtype=np.float64) / float(fps)
            n_arr = np.full(n_frames, n_fixtures, np.int32)
            offsets = np.arange(n_frames + 1, dtype=np.int32) * n_fixtures
            with gpu.lock:  # 单 dev 句柄不支持并发 run
                rc = gpu.wave_batch(
                    t, np.full(n_frames, speed, np.float64),
                    np.full(n_frames, phase_step, np.float64),
                    np.full(n_frames, lo, np.float64),
                    np.full(n_frames, hi, np.float64),
                    np.full(n_frames, wave_idx, np.int32),
                    n_arr, offsets, n_frames, out_ptr)
            if rc == 0:
                _BAKE.cnt_gpu_bake_calls += 1
                return 1
            _BAKE.cnt_gpu_bake_fail += 1
            log.warning("[CE-K3] GPU wave 烘焙失败(%s)回退 OpenMP: %s",
                        gpu.name, gpu.last_error())
            return 0
        except Exception as e:  # noqa: BLE001 任何异常 → 回退 OpenMP（字节不变）
            _BAKE.cnt_gpu_bake_fail += 1
            log.warning("[CE-K3] GPU wave 烘焙异常回退 OpenMP: %s", e)
            return 0

    _BAKE.cnt_gpu_bake_calls = 0
    _BAKE.cnt_gpu_bake_fail = 0
    _k3_cb_holder[0] = _K3_BAKE_CB_TYPE(_cb)  # 模块级持有, 防 ctypes 回调被 GC
    try:
        engine.ce_gpu_bake_sequence_register(_k3_cb_holder[0])
    except Exception as e:  # noqa: BLE001 注册失败 = 该引擎无分派点
        _k3_cb_holder[0] = None
        log.warning("[CE-K3] GPU 烘焙分派注册失败: %s", e)
        return False
    _bake_cb_registered = True
    log.info("[CE-K3] GPU 烘焙分派已注册（gate=%s, 探测懒加载）", gate)
    return True


_k3_cb_holder = [None]  # 模块级引用（install_bake_gpu 写入, 防 GC）
