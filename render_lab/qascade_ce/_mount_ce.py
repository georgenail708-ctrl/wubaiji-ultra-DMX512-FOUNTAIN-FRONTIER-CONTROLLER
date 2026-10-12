# -*- coding: utf-8 -*-
"""Qascade CE 挂载点 — main.py 一行调用, 失败零副作用。

INTEGRATION_PLAN §2 的 render_lab 隔离区实现（未合入 main.py/src/, 将来挂载
= 把下方 3 行调用放进 main.py）:

  * 开发态: 把本文件所在目录的上一级（render_lab/）放入 sys.path（幂等）。
    打包态（sys.frozen）qascade_ce 落在 _MEIPASS/qascade_ce, _MEIPASS 已在
    sys.path → 不插入。
  * env 策略（INTEGRATION_PLAN §3.1; 换装阶段2 已授权启动即默认开）:
    _mount_ce() 内 setdefault 默认矩阵 —— QASCADE_CT_ENGINE 默认 "1"（主程序
    启动即 C 语义内核）; 子开关见 _ENV_DEFAULT_MATRIX（默认开 = 已验证字节
    等价+正收益的批量项, 默认关 = 等真机验证后逐项 flip）; 用户显式设置优先
    （setdefault 不覆盖已设值）; QASCADE_CT_ENGINE=0 仍是最终逃生口 = L0
    一键回退。
  * try/except 包 install(): 失败零副作用 —— 日志 + 原渲染路径逐字节不变
    （install() 本身整体 try/except + 每 hook 调用点回退, HOOK_AUDIT 22 hook）。
  * 启动日志: backend 探测结果 / 变体 / ce_version / 线程数 / 缺失符号汇总 /
    逐 hook 接入行（含门控状态）。
  * 返回 MountStatus（active/backend/missing/回退计数等）供 main.py 日志、
    「引擎状态」对话框（换装第二阶段）、启动 splash 状态显示。

挂载调用（main.py main() 内, sys.excepthook = _handle_exception 之后、
_create_app(sys.argv) 之前; 3 行, 幂等）:

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, os.path.join(_ROOT, "render_lab"))   # 开发态
    from qascade_ce._mount_ce import _mount_ce
    _ce_status = _mount_ce()                                    # 一行挂载

回滚说明（INTEGRATION_PLAN §4）:
  L0 一键回退: set QASCADE_CT_ENGINE=0（下次启动即原路径）;
  L1 包级回退: 重装旧 exe（不含 qascade_ce data）;
  L2 启动失败: install() 整体 try/except → 本次会话全原版（本文件再包一层）;
  L3 运行中 hook 失败: 每 hook 调用点 try/except → orig（已审计 22 hook）;
  L4 符号缺失: _bind 可选绑定 → 该 hook 恒回退（missing 清单写入启动日志）。
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

# ── env 策略（INTEGRATION_PLAN §3.1; 换装阶段2 已授权启动即默认开）─────
_ENV_KEY = "QASCADE_CT_ENGINE"
# 阶段2: 默认 "1"（主程序启动即 C 语义内核）; =0 仍是 L0 一键回退逃生口。
_CE_ENV_DEFAULT = "1"

# 默认矩阵（启动即生效; setdefault → 用户显式设置优先, 显式值不被覆盖）。
#   默认开 = 已验证字节等价 + 正收益（三扇门 + 活性探针, 见
#   render_lab/bench/ENV_DEFAULT_MATRIX.md）:
#     QASCADE_CE_FLUSH_BATCH  批量 flush（__init__.py :2313 读点）
#     QASCADE_CE_AUDIO_BATCH  音频离线批量预取（__init__.py :3061 读点）
#     QASCADE_CE_AUDIO_MSA    音频结构分析（__init__.py :3379 读点）
#   默认关 = 变更区（Python 仍活跃改动而 C 未同步 → 永不接管, 恒回退
#   原版）或等真机验证后逐项 flip（flip 条件见 ENV_DEFAULT_MATRIX.md §4）:
#     变更区 2026-08-08 暂关（C 重新同步后可 flip 回 1, 记录见各键注释）:
#       QASCADE_CE_T11 交互几何 /
#       QASCADE_CE_T10 同步·编组
#     等真机验证: QASCADE_CE_SEND_RING 发送环 /
#     QASCADE_CE_FLUSH_ASYNC 异步 flush / QASCADE_CE_GPU（backend.py :80 /
#     gpu_bake.py :302 / scheduler.py :136 三处读点）。
#   R178 退役: QASCADE_CE_P6 / QASCADE_CE_P8 / QASCADE_CE_RESOLVE_MANY 只在
#   P4b 替代渲染路径内读取, P4b 按 CLAUDE.md 10.3 明确回退原版后三键无读点
#   → 移出矩阵（见 __init__._hook_frame_path_p4b docstring）。历史: RESOLVE_MANY
#   与 FLUSH_ASYNC 同开 = 竞态非确定（ENV_DEFAULT_MATRIX.md 实证）; 若将来
#   复活 P4b, 该互斥随之复活 —— FLUSH_ASYNC 仍保持 0（真机未验证）。
#   探针专用（代码默认 0, 不进矩阵）: QASCADE_CE_TIMING / QASCADE_CE_PIPELINE_AB
#   / QASCADE_CE_BAKE_THREADS / QASCADE_CE_PROF。
_ENV_DEFAULT_MATRIX = {
    # 默认开（已验证: 门1 哈希 73d9e1c2 零漂移 + 活性探针 46/46 全 PASS）
    "QASCADE_CE_FLUSH_BATCH": "1",
    "QASCADE_CE_AUDIO_BATCH": "1",
    "QASCADE_CE_AUDIO_MSA": "1",
    # 2026-08-09 flip: C 已同步 58da317（镜像 72baed4 三处: xform 结果缓存/
    # 增量脏区重绘/质心增量更新, 212,000 fuzz 样本 0 差异 + 11382 台 A/B
    # translate −45.8% rotate −25.6% 逐位一致）→ 交互几何恢复 C 接管。
    "QASCADE_CE_T11": "1",
    # 2026-08-09 flip: C 已同步 bd80ece（_circle_anchor_cluster_angles 锚点
    # 分簇切线, __init__.py L5243 主池圆页逐圆切线语义同步）→ 同步/编组
    # 恢复 C 接管。
    "QASCADE_CE_T10": "1",
    # 2026-08-10 flip（发送侧专项, 对症「多域输出实时操控丢帧慢帧」——丢帧
    # 在 25ms 循环多域逐个发送侧而非混合计算侧）: SEND_RING 发送环 FIFO 队列
    # （模式 A, push 2µs 即返 + 常驻发送线程; 互斥矩阵全绿——与 RESOLVE_MANY
    # 组合二分实测等价零漂移, 与 FLUSH_ASYNC 代码级互斥 mode_b 优先; 环满
    # 保序丢旧, 绝不回退直发）。真机验证项见 ENV_DEFAULT_MATRIX.md。
    "QASCADE_CE_SEND_RING": "1",
    "QASCADE_CE_FLUSH_ASYNC": "0",
    # 2026-08-10 flip（同一发送侧专项）: PACK 输出路由批量打包（ArtNet 2.8x /
    # sACN 6.5x 内核级已实测; 接线缺陷已修——sink 绑定方法分发 + 池缺域回退
    # 逐包, 2026-08-10 _run_with_pack 修复）。不在 _HOOK_INVENTORY 的既有
    # gate, 现补入矩阵/清单统一门控。
    "QASCADE_CE_PACK": "1",
    # 2026-08-09 flip（离线渲染专项, 用户指令「充分调用算力」）: K3 离线烘焙
    # OpenMP 多核默认开（16 线程, ce_bake_sequence_omp; 5000 轮 fuzz 位级
    # 等价 + HOOK_K3 PASS; 离线路径非演出关键）。接在 EffectEngine.compute
    # → 效果烘焙（自动编曲/声部 clip/时间线预渲染）立即多核。
    "QASCADE_CE_BAKE_THREADS": "16",
    # 2026-08-09 flip（离线渲染专项）: GPU 后端默认 auto（自动识别:
    # nvcuda.dll→CUDA / OpenCL.dll+AMD→OpenCL / 无→CPU 降级）。接入点 =
    # K3 烘焙分派回调（wave 序列 OpenCL 实测 3.65x, 409 万样本 0 失配）。
    # 2026-08-10: mix/wave 任务量阈值 = 启动微基准自适应（首次 GPU 使用时
    # 实测典型任务量网格 GPU vs CPU 耗时定交叉点, <100ms 一次, 见
    # gpu_bake.py calibrate_gpu）; 本机 780M 混合判"永不 GPU"（拷贝带宽
    # 受限 0.51x）, wave 判 GPU（≈64k 样本交叉）; 高带宽独显（4080）同一
    # 逻辑自动判混合 GPU。QASCADE_CE_GPU_MIN_CHANNELS / MIN_WAVE_VALUES /
    # CALIB=0 为人工覆盖与诊断逃生口。实时路径零调用（回调只在离线烘焙
    # 序列触发）。正式机 4080 部署 ce_gpu_cuda.dll 后自动走 CUDA（代码零
    # 差异）。
    "QASCADE_CE_GPU": "auto",
    # 2026-08-10 flip（最后一轮改动, 生产接线完成）: K2 ce_timeline_frame
    # 离线时间线整帧融合（每帧 1 次 ctypes 调用覆盖 fetch+resolve+mix 链,
    # 纯回放 Fast-Path 条件）。生产接线 = _hook_timeline_range: 任务表与
    # _render_position 同源（_indexed_active_clip_items + 结构缓存序）;
    # 帧内任一任务不过 clip_eligible_fast_path（含 K7 方向 fade 未接线守卫）
    # → 整帧回退 _render_position（逐帧 Python 字节同）; ctx 不可用/表构建
    # 失败/内核拒绝/异常 → 整段回退原版。probe_timeline_k2_bytecheck.py
    # 7149 帧逐字节 0 diff + probe_record_bytecheck.py record 子集同构。
    # env=0 恒原版（逃生口保留）。
    "QASCADE_CE_TIMELINE_RANGE": "1",
    # 2026-08-10 flip（最后一轮改动, 生产接线完成）: K5/K6 灯跟踪
    # C 内核（ce_lt_apply + ce_timeline_frame LT 融合段）。生产接线 =
    # _hook_lt: 引擎级状态槽 seed_from/back_write 无损续接, 单任务束
    # start_t=0.0 + pos=local_t（浮点精确）; 内核拒绝/异常 → 委托原版
    # 字节不变。探针 = probe_lt_smoke.py 12 角 + 120 帧双路 0 diff 前置。
    # 灯跟踪仍属「用户实测唯一的验证者」（同步零验证硬规则）: 真机实测
    # 若异常, QASCADE_CE_LT=0 立即回原版（逃生口保留, 无残余状态）。
    "QASCADE_CE_LT": "1",
    # 2026-09-18 flip（用户指令「新功能的计算层应该及时更新到 CE 里面并
    # 通过它完成计算调用, 保证全命中」）: T12 关联器整段 C 计算。
    # 接入点 = TrackLinkerEngine._process_rule（每 25ms tick 的规则遍历 +
    # 逐通道读写; 40 台/120 规则实测 tick 9.07ms 中该段占 82.1%, 余下
    # _get_snapshot 9.3% / 写回 NM 9.3% 属 NM 侧既有 CE 链路）。
    # 逐规则契约守卫（时序分支/未知预设/参数不可 float/地址越界/掩码缺表）
    # → 该规则回退原版, 输出逐字节不变; 内核返回码 != 0 → 零副作用回退。
    # 门禁 = 门1 哈希 73d9e1c2 不漂 + ce_fuzz_linker.py 双路逐字节对拍 +
    # 门0 活性（_LINKER_HIT_CNT > 0）。env=0 一键回退逃生口。
    "QASCADE_CE_LINKER": "1",
    # 2026-09-18 flip（同一用户指令）: T13 矩阵像素引擎 v40 计算族 C 计算。
    # 接入点 = matrix_pixel_engine_ext_v40._compute_pattern/_compute_particle
    # （_EXT_COMPUTE_MAP 的 lambda 调用时按模块名查找 ⇒ 覆盖 964/1254 个 v40
    # 模式）。实测（1356 台, 40ms 主循环）: particle 27.79ms/帧（逐设备×逐粒子
    # exp 核 82.9%）/ pattern 6.46ms/帧; 对照内置 numpy 数组族 0.061ms/帧 ——
    # 后者**不 C 化**（收益 <5%, 见 qascade_ce/matrix_pixel_path.py docstring）。
    # 位级等价难点 = `math.hypot`（CPython vector_norm ≠ ucrtbase hypot）→
    # Dekker double-double 复刻, 实证 20 万×8 域 0 失配
    # （_ui_validation/prevalidate_c_math.py）; 整数哈希 int64 回绕 40 万样本
    # 0 失配。门禁 = 门1 哈希不漂 + ce_fuzz_matrix_pixel.py 逐字节对拍 +
    # 门0 活性（_MTX_HIT_CNT > 0）。env=0 一键回退逃生口。
    "QASCADE_CE_MATRIX_PIXEL": "1",
    # 2026-09-19 flip（同一用户指令）: T14 effect `_tick_play` 脚手架写回层
    # C 计算。接入点 = EffectBrowserWidget._write_direct_channel_vectorized /
    # ._write_rgbw_channel_vectorized（效果预设 compute 早已在 CE T4 全族,
    # 写回层没在）。实测（1356 台, CE 开 × 5 组预设/通道）:
    # _write_channel_vectorized 3.886ms/帧 = 整帧 31.3%（direct 0.641 /
    # rgbw 3.023, dimmer→RGBW 档单档 15.09ms = 62.6%）; 对照
    # _effect_addr_maps_for_fixtures / _collect_channel_structure /
    # _build_positions 命中 R38 静态缓存后 0.00~0.07ms/帧（<1%）→
    # **刻意不 C 化**（收益 ~0, 见 qascade_ce/effect_write_path.py 文档）。
    # 位级等价: C 侧只有乘/除/rint/比较（rint 半偶 = np.rint）; bincount 部分和
    # 按 i 升序 + 四通道按 r→g→b→w 次序相加（浮点结合序逐字复刻）。
    # 门禁 = 门1 哈希不漂 + ce_fuzz_eff_write.py 逐字节对拍 + 门0 活性
    # （_EFF_WRITE_HIT_CNT > 0）。env=0 一键回退逃生口。
    "QASCADE_CE_EFFECT_WRITE": "1",
}

# 本文件 render_lab/qascade_ce/_mount_ce.py → 上一级 = render_lab, 上上级 = 仓库根
_HERE = os.path.dirname(os.path.abspath(__file__))
_RENDER_LAB = os.path.abspath(os.path.join(_HERE, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_RENDER_LAB, ".."))

# 逐 hook 接入清单（install() 注册序; gate = (env 名, 代码默认值) 元组,
# 空元组 = 常开; 门控开 = 任一 env=1）。
_HOOK_INVENTORY = (
    ("K0 _mix_clips 帧合成内核", ()),
    ("indexed_holds 索引保持", ()),
    ("direction_fade 方向渐变", ()),
    ("precharge 泵预充电", ()),
    ("flush_batch 批量 flush", (("QASCADE_CE_FLUSH_BATCH", "1"),)),
    ("flush_timing 计时包装", ()),
    ("heartbeat 空闲心跳", ()),
    ("xy_ref_build xy 参考表", ()),
    ("smoother 输出平滑器", ()),
    ("interlock 安全联锁", ()),
    ("audio 音频特征", (("QASCADE_CE_AUDIO_BATCH", "1"),)),
    ("audio_msa 结构分析", (("QASCADE_CE_AUDIO_MSA", "1"),)),
    ("topview 同步/编组", (("QASCADE_CE_T10", "1"),)),
    ("send_pipeline 发送流水线", (("QASCADE_CE_SEND_RING", "1"),
                                   ("QASCADE_CE_FLUSH_ASYNC", "0"))),
    ("pack_batch 批量打包", (("QASCADE_CE_PACK", "1"),)),
    ("topview_interact 交互几何", (("QASCADE_CE_T11", "1"),)),
    ("timeline_range 离线时间线整帧融合", (("QASCADE_CE_TIMELINE_RANGE", "1"),)),
    ("lt_light_tracking 灯跟踪", (("QASCADE_CE_LT", "1"),)),
    ("linker 关联器整段计算", (("QASCADE_CE_LINKER", "1"),)),
    ("matrix_pixel 矩阵像素引擎整段计算", (("QASCADE_CE_MATRIX_PIXEL", "1"),)),
    ("effect_write effect 写回层", (("QASCADE_CE_EFFECT_WRITE", "1"),)),
)


class MountStatus:
    """_mount_ce() 返回的挂载状态（可 JSON 序列化, 供 main.py/探针消费）。

    active         bool   C 引擎真实生效（engine 已加载且 env=1）
    installed      bool   install() 本会话已执行（含静默回退, 会话内不重试）
    backend        str    "cpu-avx2" / "cpu-sse2" / "python-fallback"
    variant        str    DLL 变体（avx2/sse2; 未加载为空串）
    threads        int    DLL 报告的物理核数（0 = 未知）
    missing        tuple  缺失符号清单（对应 hook 恒回退原版; L4）
    fallback_count int    挂载时永久回退点数量（= len(missing)）
    counters       dict   hook 计数器基线快照（_*_CNT; 后续 diff 可判活性）
    error          str|None 挂载异常描述（None = 无异常; 回退原因也在内）
    env_value      str    QASCADE_CT_ENGINE 生效值
    ce_version     str    DLL ce_version() 返回值（"?" = 不可用）
    """

    __slots__ = ("active", "installed", "backend", "variant", "threads",
                 "missing", "fallback_count", "counters", "error",
                 "env_value", "ce_version")

    def __init__(self, active=False, installed=False, backend="python-fallback",
                 variant="", threads=0, missing=(), fallback_count=0,
                 counters=None, error=None, env_value=None, ce_version=""):
        self.active = active
        self.installed = installed
        self.backend = backend
        self.variant = variant
        self.threads = threads
        self.missing = tuple(missing)
        self.fallback_count = fallback_count
        self.counters = dict(counters) if counters is not None else {}
        self.error = error
        self.env_value = env_value
        self.ce_version = ce_version

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "installed": self.installed,
            "backend": self.backend,
            "variant": self.variant,
            "threads": self.threads,
            "missing": list(self.missing),
            "fallback_count": self.fallback_count,
            "error": self.error,
            "env_value": self.env_value,
            "ce_version": self.ce_version,
            "counters": dict(self.counters),
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def __repr__(self):
        return ("<MountStatus active=%s installed=%s backend=%s variant=%s "
                "threads=%d missing=%d fallback_count=%d error=%r env=%r>"
                % (self.active, self.installed, self.backend, self.variant,
                   self.threads, len(self.missing), self.fallback_count,
                   self.error, self.env_value))


def _ensure_render_lab_on_path() -> bool:
    """开发态把 render_lab/ 放入 sys.path（幂等）。返回 True = 本次插入。"""
    if getattr(sys, "frozen", False):
        return False
    if _RENDER_LAB in sys.path:
        return False
    sys.path.insert(0, _RENDER_LAB)
    return True


def _counter_snapshot(qc) -> dict:
    """聚合 qascade_ce 顶层 *_CNT 计数器（基线快照）。"""
    snap = {}
    for _name in sorted(dir(qc)):
        if not _name.endswith("_CNT"):
            continue
        _v = getattr(qc, _name)
        if isinstance(_v, list) and _v and isinstance(_v[0], int):
            snap[_name] = _v[0]
        elif isinstance(_v, dict):
            snap[_name] = dict(_v)
    return snap


def _hook_inventory(logger) -> None:
    """逐 hook 接入行（含门控状态 — install() 只打印接入, 不打印门控）。"""
    for _name, _gates in _HOOK_INVENTORY:
        if not _gates:
            logger.info("[CE] hook 接入: %s（常开）", _name)
            continue
        _on = any(os.environ.get(_k, _d) == "1" for _k, _d in _gates)
        _keys = "+".join(_k for _k, _d in _gates)
        logger.info("[CE] hook 接入: %s（门控 %s=%s%s）", _name, _keys,
                    "1" if _on else "0",
                    "" if _on else " → 恒回退原版")


def _mount_ce() -> MountStatus:
    """挂载 CE C 计算层（幂等; 失败零副作用 — 原渲染路径逐字节不变）。

    时序（与 main.py 挂载点一致, INTEGRATION_PLAN §2）:
      1. 开发态: render_lab/ 放入 sys.path（打包态 qascade_ce 在 _MEIPASS 根）
      2. env 策略: QASCADE_CT_ENGINE 未设 → 置 _CE_ENV_DEFAULT（阶段2 = "1"）;
         子开关未设 → 置 _ENV_DEFAULT_MATRIX 默认值（用户显式设置优先）
      3. 非 "1" → 直接返回 inactive（不 import 任何 qascade_ce 重模块,
         sys.path 之外零副作用）
      4. try: from qascade_ce import install → install()
      5. 启动日志: backend/变体/ce_version/线程数/缺符号汇总/逐 hook 接入
      6. 返回 MountStatus（active/backend/missing/回退计数）
    """
    _ensure_render_lab_on_path()
    os.environ.setdefault(_ENV_KEY, _CE_ENV_DEFAULT)
    # 默认矩阵落地（setdefault: 用户显式设置优先）——先于门控, 使 install()
    # /_hook_inventory 读到同一视图; 引擎被 =0 关闭时同样统一生效（纯 env
    # 写入, 零 import 零渲染副作用）。
    for _k, _v in _ENV_DEFAULT_MATRIX.items():
        os.environ.setdefault(_k, _v)
    # ── 线程默认值按机器自适应（2026-09-18, 阶段3.1）──────────────────
    # 旧默认 OMP_NUM_THREADS=8 是 8 物理核开发机的调优结论（install() 内
    # setdefault）; 16 物理核/32 线程的 7945HX 上只用了半数物理核。
    # 此处先于 install() 落地（install 的 setdefault("8") 届时不再生效）,
    # 尊重用户显式设置。与 OMP_WAIT_POLICY 同属"线程行为"类, 不经门控矩阵
    # （矩阵 = 功能开关契约, 有单一真源测试; 线程数是机器属性, 随机器变）。
    # 实测依据（bench/ce_probe_omp_threads.py, 8 核机, 相对 OMP=8）:
    #   大任务 mix512:   OMP=16 -20.6%（SMT 有收益）
    #   小任务 full_mix: OMP=16 +30.3%（SMT 有害）
    #   bake 400×11382:  nt=16 比 nt=8 快 12-33%（SMT 有收益）
    # → 实时/通用并行区取物理核数; 离线烘焙取逻辑核数（上限 32）。
    try:
        from qascade_ce.sysinfo import cpu_thread_default
        _phys = cpu_thread_default()
        _logical = os.cpu_count() or 1
        os.environ.setdefault("OMP_NUM_THREADS", str(_phys))
        os.environ.setdefault("QASCADE_CE_BAKE_THREADS",
                              str(min(_logical, 32)))
    except Exception as _te:
        log.debug("[CE] 线程默认值跳过: %s", _te)
    env_value = os.environ.get(_ENV_KEY, _CE_ENV_DEFAULT)
    # 启动资源行（阶段0.2）: 核数/OMP/GPU DLL 门控 —— 目标机零操作可核查
    # 算力是否吃满（对照 bench/ce_probe_resources.py 的审计报告）。
    try:
        from qascade_ce.sysinfo import resource_line
        log.info("[CE] %s", resource_line())
    except Exception as _re:
        log.debug("[CE] 资源行跳过: %s", _re)
    if env_value != "1":
        log.info("[CE] 渲染引擎未启用 (QASCADE_CT_ENGINE=%s): 使用原渲染路径",
                 env_value)
        return MountStatus(env_value=env_value)
    try:
        import qascade_ce as _qc
        from qascade_ce import install as _ce_install
        _ce_install()
    except Exception as _ce:
        log.warning("[CE] 挂载异常, 保持原渲染路径（逐字节不变）: %s", _ce)
        return MountStatus(installed=True, backend="python-fallback",
                           error="%s: %s" % (type(_ce).__name__, _ce),
                           env_value=env_value)
    engine = _qc._engine
    if engine is None:
        log.info("[CE] DLL 不可用, 静默回退原渲染路径（字节不变）")
        return MountStatus(installed=_qc._installed, backend="python-fallback",
                           error="load_or_none → None（DLL 缺失/损坏）",
                           env_value=env_value)
    try:
        _ver = engine.version()
        _ver = (_ver.decode("utf-8", "replace")
                if isinstance(_ver, bytes) else str(_ver))
    except Exception:
        _ver = "?"
    try:
        _threads = max(1, engine.cpu_cores())
    except Exception:
        _threads = 0
    # 缺失符号汇总: engine.missing_symbols 是 _audit_missing 前半段快照,
    # 漏掉其后才绑定的 ce_pulse_effect/ce_stair_comet_effect（ctypes_api
    # 审计顺序既有缺口, 不在本隔离区改动范围内）→ 按 _KNOWN_SYMBOLS 全集
    # 重算, 与 DLL 侧 warning 逐字一致。
    try:
        from qascade_ce import ctypes_api as _ca
        _missing = tuple(sorted(
            n for n in _ca._KNOWN_SYMBOLS
            if getattr(engine, n, None) is None))
    except Exception:
        _missing = tuple(sorted(getattr(engine, "missing_symbols", ()) or ()))
    _backend = "cpu-%s" % engine.variant
    log.info("[CE] 已挂载 C 渲染引擎: backend=%s variant=%s ce_version=%s "
             "threads=%d OMP_WAIT_POLICY=%s OMP_NUM_THREADS=%s",
             _backend, engine.variant, _ver, _threads,
             os.environ.get("OMP_WAIT_POLICY", "?"),
             os.environ.get("OMP_NUM_THREADS", "?"))
    if _missing:
        log.warning("[CE] 缺失符号 %d 个（对应 hook 恒回退原版）: %s",
                    len(_missing), ", ".join(_missing))
    _hook_inventory(log)
    _status = MountStatus(active=True, installed=_qc._installed,
                          backend=_backend, variant=engine.variant,
                          threads=_threads, missing=_missing,
                          fallback_count=len(_missing),
                          counters=_counter_snapshot(_qc),
                          env_value=env_value, ce_version=_ver)
    log.info("[CE] 挂载完成 %s", _status.to_json())
    return _status
