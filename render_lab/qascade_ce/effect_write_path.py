# -*- coding: utf-8 -*-
"""effect `_tick_play` 脚手架写回层 CE 计算 —— 逐域地址散写 / RGBW 亮度模拟。

背景（2026-09-19 实测归因, `render_lab/bench/probe_effect_tick_profile.py`
+ `probe_effect_line_profile.py`）:

  1356 台设备 × 5 组「预设 × 通道」（**CE 开**, 效果 compute 已走 T4 内核）:

    _compute_channel_frames（效果 compute, 已有 CE T4）  7.077 ms/帧  57.1%
    _write_channel_vectorized（本模块接管）              3.886 ms/帧  31.3%
      ├ _write_direct_channel_vectorized                  0.641 ms/帧   5.2%
      └ _write_rgbw_channel_vectorized                    3.023 ms/帧  24.4%
           （dimmer→RGBW 单档 15.09 ms/帧 = 该档整帧 61.9%）
    _effect_addr_maps_for_fixtures / _collect_channel_structure /
    _build_positions（均有缓存）                          0.00~0.07 ms/帧  <1%
    nm.update_layer('effect') 写回                        0.5~0.7 ms/帧  ~5%

  ★ 为什么只做写回层（数据说话）:
    · 地址映射 / 通道结构 / positions 命中缓存后 **<1%** —— 静态结构缓存
      （R38 冻结修复）已经把 Python 循环消掉了, C 化收益接近 0 → **不做**。
    · `_write_rgbw_channel_vectorized` 内部 15.09ms 中 `compute_universe_now`
      （NM 底色调色, 已走 CE T3 全链混合）占 ~7.9ms —— 那是 NM 侧既有 CE
      链路, 不在本模块; 余下 ~7.2ms 是**逐域 numpy 小数组调用风暴**
      （bincount/where/rint/maximum × 4 通道 × 逐域, 单帧数百次 1.5µs 级
      numpy 调用）→ 正是 C 化收益最大的形态。
    · `_compute_channel_frames` 的 57.1% 是效果引擎侧（sort/空间分组/方向 +
      T4 C 内核）, 属另一条链, 不在本模块范围。

接入点（用户指令 2026-09-18「新功能的计算层应该及时更新到 CE 里面并通过它
完成计算调用, 保证全命中」）:
  `src/ui/effect_browser.py` 的 `EffectBrowserWidget._write_direct_channel_vectorized`
  (:7139) / `._write_rgbw_channel_vectorized` (:7160)。二者是**方法**且各占
  一整段语义 → 逐点替换（与 T12/T13 同纪律）。

## 位级等价（与既有 CE 内核同标准, 见 `csrc/_ce_eff_kernel.c.inc` 顶部）

* 本层的 **C 侧数值只有乘法/除法/rint/比较** —— 没有超越函数, 故无 ucrtbase
  动态解析需求; `rint` 半偶舍入与 `np.rint` 同语义（默认 FE_TONEAREST）。
* `np.bincount(idx, weights=w, minlength=n)` 的累加次序 = 按 i 升序
  `acc[idx[i]] += w[i]` ⇒ C 侧同序循环; 四通道合成次序（先各通道独立部分和,
  再按 r→g→b→w 相加）逐字复刻 —— 交错累加会改变浮点结合序 = 位差。
* 值域外的 `values`（nan/inf/<0/>255）**内核拒绝**（返回 -1, 零写入）→ 本层
  回退原版, 由原版承担 numpy 的 `astype(np.uint8)` 语义（C 的越界
  double→uint8 转换是 UB, 不复刻）。
* 地址/下标越界、dtype 非 int64、非连续、电机路径地址重复 → 契约拒绝回退。
* 两遍式: 内核先校验全部输入再写 → 任一非法输入零副作用。

## 与 Python 原版的结构差异（有意为之, 语义等价）

* 每次 `_collect_channel_structure` 产出的静态结构（地址/设备/配对数组）
  只做**一次**扫描转成「内核计划」（原始指针 + 计数 + 查重结果）, 之后每帧
  零转换成本; 计划以 `id(结构)` 为键并**持有结构引用**（防 id 复用误命中,
  与 `_effect_addr_cache` 同款）, 随 `_invalidate_tick_caches` 一并清空。
* `bufs`/`acts` 的懒创建语义逐字保留（`if uni not in bufs` 才建 512 零缓冲;
  空结构的域**不建** —— 否则 `current_unis` 会多出空域, 改变 NM 写入集合）。
* rgbw 的 `current`（底色调色）仍在 Python 侧经 `compute_universe_now` 取得
  （含 `compute_cache` 逐帧复用与 TypeError/Exception 两级回退, 逐字保留）,
  只把「聚合 → 默认值 → 缩放 → max 写」交给内核。
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("qascade_ce.effect_write")

# 与 csrc/_ce_eff_kernel.c.inc 的 CE_EFF_MAXDEV 同源
MAXDEV = 2048

#: `ce_eff_write_rgbw` 的跨域工作区（`sums`）与「单域部分和 scratch」同址起点。
#: C 侧: `double* part = sums + CE_EFF_MAXSUM - CE_EFF_MAXDEV;`（8192-2048=6144），
#: 而 `_ce_eff_bincount(part, n_dev, first=1, ...)` **先清零 part[0..n_dev)**
#: —— 当某域的 base_off 落在 6144 附近（`|base_off - 6144| < n_dev`）时，
#: 清零/累加会与 `sums_k = sums + base_off` 同址 → 该域通道0 的部分和被打掉、
#: 结果静默错值（R93 实测: 12×512 设备后第 13 域, C=223 vs Python=87）。
#: 该形态需要「一批内设备数逼近 6144」的真实规模工程（≥6000 台无物理 dimmer
#: 的设备走 RGBW 模拟）才触发; 内核修好前由本层拒绝 → 回退 Python 原版
#: （正确性优先; 不重编 DLL 的约束下唯一安全做法）。
_SCRATCH_OFF = 6144          # CE_EFF_MAXSUM(8192) - CE_EFF_MAXDEV(2048)

MAXSUM = 8192

# 内联缓存上限（结构计划按 id 键 + 持有引用; 与 _CH_STRUCT_CACHE_MAX 同量级）
_PLAN_CACHE_MAX = 64


def rgbw_scratch_alias_unsafe(table, n_uni) -> bool:
    """本批是否落进 `ce_eff_write_rgbw` 的 scratch 别名区（见 _SCRATCH_OFF）。

    `table` = `build_rgbw_table` 的产出（每域一行 21 列 int64，第 0 列 = n_dev）。
    代价 O(n_uni)（≤512），相对逐域工作可忽略。
    """
    off = 0
    for k in range(int(n_uni)):
        try:
            n = int(table[k * 21 + 0])
        except Exception:
            return True                       # 形态异常 → 交给原版
        if n < 0:
            return True
        if abs(off - _SCRATCH_OFF) < n:
            return True
        off += n
    return False


def _data_ptr(arr) -> int:
    """ndarray 数据指针（Python int, 不做 ctypes 对象构造 —— 热路径省 1.5µs/次）。"""
    return int(arr.__array_interface__["data"][0])


def _i64_ok(arr) -> bool:
    """intp/int64 连续一维数组（内核按下标读 int64）。"""
    if arr is None or not isinstance(arr, np.ndarray):
        return False
    if arr.dtype != np.intp and arr.dtype != np.int64:
        return False
    return arr.ndim == 1 and bool(arr.flags["C_CONTIGUOUS"])


def _u8_ok(arr) -> bool:
    return (isinstance(arr, np.ndarray) and arr.dtype == np.uint8
            and arr.shape == (512,) and bool(arr.flags["C_CONTIGUOUS"]))


def _bool_ok(arr) -> bool:
    return (isinstance(arr, np.ndarray) and arr.dtype == bool
            and arr.shape == (512,) and bool(arr.flags["C_CONTIGUOUS"]))


def _f64_ok(arr) -> bool:
    return (isinstance(arr, np.ndarray) and arr.dtype == np.float64
            and arr.ndim == 1 and bool(arr.flags["C_CONTIGUOUS"]))


def build_direct_table(direct_uni) -> np.ndarray | None:
    """`direct_uni`（{uni: ([addr...] intp, [fx...] intp)}）→ 形态表（5×n_uni int64）。

    每域一行: [n, addrs, fxs, buf, act]（后四项由**调用方**在写入前填缓冲指针;
    本函数只填 n/addrs/fxs 与前两列, buf/act 占位 0）。

    ★ 只收录「原版会创建 bufs 条目」的域（addrs.size > 0）—— 原版对空向量
      `continue`, 连 bufs 都不建; 若这里也收录空向量, 回退路径的
      `current_unis` 集合会变 → NM 写入集合变化 = 字节漂移。

    返回 None = 契约不满足（调用方回退原版）。
    """
    if not isinstance(direct_uni, dict):
        return None
    rows = []
    for uni, entry in direct_uni.items():
        try:
            addrs, fxs = entry[0], entry[1]
        except Exception:
            return None
        if (not _i64_ok(addrs) or not _i64_ok(fxs)
                or int(addrs.size) != int(fxs.size)):
            return None
        if addrs.size == 0:
            continue                      # 原版 `continue`（不建 bufs）
        rows.append((uni, int(addrs.size), _data_ptr(addrs), _data_ptr(fxs)))
    shape = np.zeros((len(rows) or 1) * 5, np.int64)
    for i, (_uni, n, p_a, p_f) in enumerate(rows):
        shape[i * 5 + 0] = n
        shape[i * 5 + 1] = p_a
        shape[i * 5 + 2] = p_f
    return shape


def build_rgbw_table(rgbw_uni) -> np.ndarray | None:
    """`rgbw_uni`（逐域 bucket）→ 形态表（21×n_uni int64）。

    每域一行: [n_dev, has_rgb, base, n_r, r_a, r_fx, r_dev, ..., buf, act]
    （base/buf/act 由调用方填; has_rgb 指针此处填好）。
    同 direct: 只收录「原版会建 bufs 条目」的域（四通道全空 → `continue`）。
    """
    if not isinstance(rgbw_uni, dict):
        return None
    rows = []
    for uni, bucket in rgbw_uni.items():
        try:
            has_rgb = bucket["has_rgb"]
        except Exception:
            return None
        if (not isinstance(has_rgb, np.ndarray) or has_rgb.dtype != bool
                or has_rgb.ndim != 1 or not has_rgb.flags["C_CONTIGUOUS"]):
            return None
        row = [int(has_rgb.size), _data_ptr(has_rgb), 0]
        any_ch = False
        for ch in ("r", "g", "b", "w"):
            try:
                a = bucket[ch]
                fx = bucket[ch + "_fx"]
                dev = bucket[ch + "_dev"]
            except Exception:
                return None
            if (not _i64_ok(a) or not _i64_ok(fx) or not _i64_ok(dev)
                    or int(a.size) != int(fx.size)
                    or int(a.size) != int(dev.size)):
                return None
            if a.size:
                any_ch = True
            row += [int(a.size), _data_ptr(a), _data_ptr(fx), _data_ptr(dev)]
        if not any_ch:
            continue                      # 原版 `continue`（不建 bufs）
        row += [0, 0]                     # buf, act（调用方填）
        rows.append(row)
    shape = np.zeros((len(rows) or 1) * 21, np.int64)
    for i, row in enumerate(rows):
        shape[i * 21:(i + 1) * 21] = row
    return shape


_CHUNK = 64          # 批量分配的域行数（64×512B = 32KB/块）


class _BufArena:
    """按 (bufs, acts) 容器身份构建的域缓冲分配器（**消除逐域 ctypes 开销**）。

    原版每个域 `bufs[uni] = np.zeros(512)`; 本 hook 若再为每个域取
    `__array_interface__` 指针（实测 ~1.7µs × 2/域）, 在「85 域 / 单通道类型」
    场景下反而**比原版更慢**（实测 direct(pump) −0.22ms/帧）。改为: 整块
    `(rows,512)` 数组一次分配, 逐域只取**行视图**（~0.3µs）并把指针按
    `base + row*512` **算术推出**（0 成本）—— 逐域总成本 ~1µs, 低于原版的
    逐域 numpy 工作量（~2.5µs）, 于是单通道类型与多通道类型**都**是净收益。

    语义上与「逐域 np.zeros(512)」逐字等价: 每个域至多从池里取一行, 取时该行
    必然全零（块分配即清零, 且一行不重复发放）; 同帧内已被其它实现建过的域
    直接复用原对象（原版语义）。
    """

    __slots__ = ("bufs", "acts", "p8", "pb", "row8", "rowb", "n8", "nb",
                 "p8_base", "pb_base", "pmap")

    def __init__(self, bufs, acts):
        self.bufs = bufs
        self.acts = acts
        self.p8 = None
        self.pb = None
        self.row8 = 0
        self.rowb = 0
        self.n8 = 0
        self.nb = 0
        self.p8_base = 0
        self.pb_base = 0
        self.pmap = {}

    def slot(self, uni):
        """(buf, p_buf, act, p_act) 或 None（形状/dtype 不符 → 调用方回退）。"""
        pmap = self.pmap
        ent = pmap.get(uni)
        if ent is not None:
            if ent[0] is self.bufs.get(uni) and ent[2] is self.acts.get(uni):
                return ent
        buf = self.bufs.get(uni)
        act = self.acts.get(uni)
        if buf is not None or act is not None:
            # 已有条目（同帧前一通道类型建的 / 被其它实现建过）→ 复用原对象
            if buf is None:
                buf = np.zeros(512, dtype=np.uint8)
                self.bufs[uni] = buf
            if act is None:
                act = np.zeros(512, dtype=bool)
                self.acts[uni] = act
            if not _u8_ok(buf) or not _bool_ok(act):
                return None
            ent = (buf, _data_ptr(buf), act, _data_ptr(act))
            pmap[uni] = ent
            return ent
        if self.row8 >= self.n8:
            self.n8 = _CHUNK
            self.row8 = 0
            a = np.zeros((_CHUNK, 512), dtype=np.uint8)
            self.p8_base = _data_ptr(a)
            self.p8 = a
        if self.rowb >= self.nb:
            self.nb = _CHUNK
            self.rowb = 0
            b = np.zeros((_CHUNK, 512), dtype=bool)
            self.pb_base = _data_ptr(b)
            self.pb = b
        row8, rowb = self.row8, self.rowb
        self.row8 += 1
        self.rowb += 1
        buf = self.p8[row8]
        act = self.pb[rowb]
        self.bufs[uni] = buf
        self.acts[uni] = act
        ent = (buf, self.p8_base + row8 * 512, act, self.pb_base + rowb * 512)
        pmap[uni] = ent
        return ent


def ctx_for(widget) -> dict:
    """逐 widget 的 T14 运行期上下文（形态表缓存 + 帧内缓冲分配器）。

    挂在 widget 的 `__dict__` 上, 随 `_invalidate_tick_caches` 一并清空
    （`_ce_eff_plan_cache` 键名沿用, 见 `_hook_effect_write`）。
    """
    c = widget.__dict__.get("_ce_eff_plan_cache")
    if c is None:
        c = {"direct": {}, "rgbw": {}, "arena": None, "base_ptrs": {}}
        widget.__dict__["_ce_eff_plan_cache"] = c
    return c


def arena_for(ctx, bufs, acts) -> "_BufArena":
    """取本帧的缓冲分配器（按 (bufs, acts) 容器身份判定帧边界）。

    `_tick_play` 每帧新建 `uni_bufs`/`uni_acts`, 同帧内多个通道类型共用同一
    对 dict → 身份不变则复用（含已建的域条目与指针）; 身份变化 = 新一帧 →
    新分配器（旧池随引用释放, 无跨帧残留）。
    """
    a = ctx["arena"]
    if a is None or a.bufs is not bufs or a.acts is not acts:
        a = _BufArena(bufs, acts)
        ctx["arena"] = a
    return a


def plan_for(cache: dict, key_obj, builder, *args):
    """id 键 + 持有引用的有界计划缓存（仿 `_cache_put_bounded` 的防 id 复用）。

    缓存值 = (形态表 ndarray, 域键列表) —— 域键保留**原 universe 对象**,
    保证回退路径的 dict 键与 Python 原版恒同。

    形态表随 `_collect_channel_structure` 的静态结构走: 结构缓存命中 →
    同一对象 → 同一表（地址指针稳定, 逐帧零构建成本）; buf/act 指针每帧由
    调用方填（缓冲可被重建）。
    """
    key = id(key_obj)
    ent = cache.get(key)
    if ent is not None and ent[0] is key_obj:
        return ent[1]
    built = builder(*args)
    if built is None:
        return None
    if key not in cache and len(cache) >= _PLAN_CACHE_MAX:
        oldest = next(iter(cache), None)
        if oldest is not None:
            del cache[oldest]
    cache[key] = (key_obj, built)
    return built


def direct_plan(direct_uni):
    """(形态表, [uni...]) 或 None。"""
    table = build_direct_table(direct_uni)
    if table is None:
        return None
    unis = [u for u, e in direct_uni.items()
            if e is not None and int(e[0].size) > 0]
    return table, unis


def rgbw_plan(rgbw_uni):
    """(形态表, [uni...]) 或 None（只含"原版会建 bufs 条目"的域）。"""
    table = build_rgbw_table(rgbw_uni)
    if table is None:
        return None
    unis = []
    for u, b in rgbw_uni.items():
        try:
            if (int(b["r"].size) or int(b["g"].size) or int(b["b"].size)
                    or int(b["w"].size)):
                unis.append(u)
        except Exception:
            return None
    return table, unis



def zeros512() -> np.ndarray:
    """rgbw 当前无 `current` 时的全零基色缓冲（该域全部走 default=255 分支）。"""
    return np.zeros(512, dtype=np.uint8)


def current_for(nm, uni, compute_cache):
    """rgbw 的底色调色缓冲（`compute_universe_now` 逐字复刻 :7176-7200）。

    返回 (ok, buf)：ok=False = 契约拒绝（NM 接口形态不符 → 调用方回退原版）；
    buf = None 表示原版语境下的 `current is None`（基色按全零处理）。

    逐字保留原版的两级回退: 带 `exclude_ltp_editor_layers` 的实现 →
    TypeError 时退到 4 参版 → 其它异常一律 current=None; compute_cache 按
    `int(uni)` 键跨通道类型复用（同 `_write_to_bufs` 的单帧缓存）。
    """
    if nm is None or not hasattr(nm, "compute_universe_now"):
        return True, None
    key = int(uni)
    if key in compute_cache:
        cur = compute_cache[key]
        if cur is None:
            return True, None
        return _base_ok(cur), cur if _base_ok(cur) else None
    cur = None
    try:
        cur = nm.compute_universe_now(
            int(uni),
            apply_output_smoothing=False,
            exclude_layers=["effect", "clip_playback", "clip_preview", "timeline"],
            exclude_ltp_editor_layers={"effect"},
            apply_xy_reference=False,
        )
    except TypeError:
        try:
            cur = nm.compute_universe_now(
                int(uni),
                apply_output_smoothing=False,
                exclude_layers=["effect", "clip_playback", "clip_preview", "timeline"],
            )
        except Exception:
            cur = None
    except Exception:
        cur = None
    compute_cache[key] = cur
    if cur is None:
        return True, None
    ok = _base_ok(cur)
    return ok, cur if ok else None


def _base_ok(arr) -> bool:
    """`compute_universe_now` 结果必须能当 512 字节 uint8 基色缓冲读。"""
    return (isinstance(arr, np.ndarray) and arr.dtype == np.uint8
            and arr.ndim == 1 and int(arr.size) >= 512
            and bool(arr.flags["C_CONTIGUOUS"]))


def current_ptr(nm, uni, compute_cache, ctx):
    """`current_for` 的指针版: (ok, cur, ptr)。

    指针按「compute_cache 里那个数组对象的身份」缓存 —— 同一 `current` 数组
    在多个通道类型间复用时避免重复取 `__array_interface__`（每域 ~1.7µs）。
    """
    ok, cur = current_for(nm, uni, compute_cache)
    if not ok or cur is None:
        return ok, cur, 0
    pc = ctx.get("base_ptrs")
    if pc is None:
        pc = {}
        ctx["base_ptrs"] = pc
    ent = pc.get(id(cur))
    if ent is not None and ent[0] is cur:
        return True, cur, ent[1]
    if len(pc) > 128:
        pc.clear()
    p = _data_ptr(cur)
    pc[id(cur)] = (cur, p)
    return True, cur, p


