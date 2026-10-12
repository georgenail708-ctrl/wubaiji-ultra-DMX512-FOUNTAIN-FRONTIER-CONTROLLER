/* ═══════════════════════════════════════════════════════════════════════════
 * effect `_tick_play` 脚手架写回内核 —— ce_eff_write_direct / ce_eff_write_rgbw
 *
 * 对齐 Python 原版: src/ui/effect_browser.py
 *   · _write_direct_channel_vectorized  :7139（逐域地址散写 + HTP max / 电机覆盖）
 *   · _write_rgbw_channel_vectorized    :7160（dimmer/level 无物理 dimmer
 *     时的 RGBW 亮度模拟: bincount 聚合 → 全零设备默认 255 → 缩放 → max 写）
 *
 * 为什么需要它（2026-09-19 实测归因, render_lab/bench/probe_effect_tick_profile.py）:
 *   1356 台设备 × 5 组「预设 × 通道」（CE 开, 效果 compute 已走 T4 内核）:
 *     _write_channel_vectorized  3.886 ms/帧  = 整帧 31.3%   ← 本内核接管
 *       其中 _write_direct_channel_vectorized  0.641 ms/帧（0.6~1.29）
 *            _write_rgbw_channel_vectorized    3.023 ms/帧（dimmer→RGBW 档
 *               单档 15.09 ms/帧 = 该档整帧 61.9%）
 *     对照: _effect_addr_maps_for_fixtures / _collect_channel_structure /
 *     _build_positions 命中 R38 静态缓存后 0.00~0.07 ms/帧（<1%）→ **刻意不
 *     C 化**（见 qascade_ce/effect_write_path.py 文档的数据）。
 *   rgbw 内部实测: compute_universe_now（NM 底色调色, 已走 CE T3）占 53%,
 *   余下 ~7.2ms/帧是 numpy 小数组调用风暴（bincount/where/rint/maximum
 *   × 4 通道 × 逐域, 单帧数百次 1.5µs 级 numpy 调用）→ 本内核把
 *   「聚合 + 默认值 + 缩放 + max 写」整段搬 C; `current`（基色）仍由 Python
 *   侧 NM 算好后按域指针传入（C 无法回调 Python）。
 *
 * ── 批式接口（2026-09-19 修正: 零副作用纪律）────────────────────────────
 * 初版按「每域一次调用」设计 → **部分写入泄漏**: 第 1 域已写、第 2 域被拒绝
 * 时, 调用方回退原版会在同一批缓冲上二次写入（HTP max 下旧值残留）→ 与纯
 * 原版字节不一致（fuzz 实测 11/4425 失配, 全是拒绝路径）。现改为**批式 +
 * 两遍**: 形态表（逐域一行 int64）一次性传入, pass1 校验全部域, pass2 才写
 * → 任一域非法 = 整批零副作用拒绝（与 T12/T13 内核同纪律）。
 *
 * ── 形态表布局（Python 侧 effect_write_path.build_*_plan 同源）──────────
 * direct: 每域 5 个 int64: [n, addrs, fxs, buf, act]
 * rgbw  : 每域 21 个 int64: [n_dev, has_rgb, base,
 *                            n_r, r_a, r_fx, r_dev,
 *                            n_g, g_a, g_fx, g_dev,
 *                            n_b, b_a, b_fx, b_dev,
 *                            n_w, w_a, w_fx, w_dev,
 *                            buf, act]
 * 指针一律以 int64 值携带（`arr.__array_interface__["data"][0]`）—— 热路径
 * 不构造 ctypes 对象。**形态表由调用方持有**, 内核只读。
 *
 * ── 位级等价纪律（与既有 CE 内核同标准, 逐条实证）──────────────────────
 *  1. 全程 f64（Python float = C double）; 本编译单元已 `#pragma
 *     fp_contract(off)`（ce_engine.c:84）→ /O2 不做 FMA 收缩（numpy 逐元素
 *     乘也不融合, 二者构造上同结果）。
 *  2. `values_arr / 255.0` 是 IEEE 真除法逐元素 —— C 侧同样写 `v / 255.0`
 *     （/fp:precise 下编译器不替换为乘倒数）。
 *  3. `np.rint` = 半偶舍入 = C `rint()`（默认 FE_TONEAREST）⇒ 同值同结果。
 *  4. `astype(np.uint8)` 对 [0,255] 内取整值 = 向零截断 = C `(uint8_t)x`
 *     （实证: np.array([101.7, 88.999, 0.5]).astype(np.uint8) → [101,88,0]）。
 *     越界/NaN **拒绝**（返回 -1, 零写入）→ Python 侧回退原版, 由原版承担
 *     numpy 的模 256 回绕语义（C 的越界 double→uint8 是 UB, 不复刻）。
 *  5. `np.bincount(idx, weights=w, minlength=n)` 的累加语义 = **按 i 升序**
 *     `acc[idx[i]] += w[i]` ⇒ C 侧同序循环。四通道合成次序
 *     `sums = 0; sums += part_r; sums += part_g; ...` 逐字复刻 —— 交错累加
 *     会改变浮点结合序 = 位差（fuzz 覆盖每设备多地址的聚合路径）。
 *  6. 写回语义（**含重复下标的 numpy 精确复刻**）:
 *     · 电机: `arr[a] = v` —— numpy 顺序赋值、**后者胜**; C 的逐元素顺序赋值
 *       与之同构（实测 `arr[[3,3]] = [101,88]` → 88）。
 *     · 非电机 HTP: `arr[a] = np.maximum(arr[a], v)` —— numpy 先把 **RHS 整体**
 *       算好（全部读自**原始** arr）, 再按 idx 顺序赋值、后者胜。重复下标下
 *       这是「max 于原始值 + 后者胜」而**不是** max 全量归约（实测
 *       `arr[[3,3,3]] = maximum(arr[[3,3,3]], [101,88,50])` → 50, 非 101）,
 *       **也不能**与逐元素 `buf[a]=max(buf[a],v)` 等价。故 C 侧同构实现:
 *       先算全部 rhs（读原始 buf）到 scratch, 再顺序赋值。
 *     · rgbw 四通道: 同款两阶段（各通道是独立 numpy 语句, 通道间按
 *       r→g→b→w 串行, 通道内的读仍全在该通道写之前）。
 *     这套复刻让**重复地址（重叠配接）也逐字节等价**, 无需拒绝 → 命中率满。
 *
 * ── 契约边界（Python 侧 effect_write_path 事前判定, 任一不满足 → 回退原版）
 *  · 全部 values 有限且 ∈ [0,255]（nan/inf/越界 → 原版模 256 语义）
 *  · addrs ∈ [0,512)、fxs ∈ [0,n_val)、dev ∈ [0,n_dev)
 *  · rgbw: n_dev ≤ CE_EFF_MAXDEV 且 sum(n_dev) ≤ CE_EFF_MAXSUM
 *  · buf/act 各 ≥512 字节且 dtype 逐位对齐（u8/bool）; base 为 ≥512 字节 u8
 *  · n_uni ≤ CE_EFF_MAXUNI; 单域单通道地址数 ≤ 512（= DMX 域宽, 天然满足）
 * 注: **重复地址（重叠配接）在契约内** —— numpy 的「RHS 先算 + 后者胜」语义
 *     已按上面第 6 条精确复刻（fuzz 专门覆盖该形态）。
 * ═══════════════════════════════════════════════════════════════════════════ */

#define CE_EFF_MAXDEV 2048      /* 单域设备数上限 */
#define CE_EFF_MAXSUM 8192      /* 一批内设备数总和上限（sums 工作区） */
#define CE_EFF_MAXUNI 512       /* 一批内域数上限 */

/* ── 单通道部分和（np.bincount 语义: 按 i 升序 acc[idx[i]] += w[i]）──────
 * first=1 → acc 先清零再累加（对应 `sums = np.zeros(...); sums += part`）;
 * first=0 → 直接累加进已有累加器（对应后续通道的 `+=`）。
 * base=NULL → 权重恒 0.0（原版 `current is None` 时 base 是全零数组）。 */
static void _ce_eff_bincount(double* acc, int n_acc, int first,
                             int n, const int64_t* addr, const int64_t* dev,
                             const uint8_t* base)
{
    int i;
    if (first) {
        for (i = 0; i < n_acc; i++)
            acc[i] = 0.0;
    }
    if (n <= 0 || addr == NULL || dev == NULL || base == NULL)
        return;                 /* base 缺失 = 权重全 0.0, 加 0.0 不改值 */
    for (i = 0; i < n; i++)
        acc[(int)dev[i]] += (double)base[(int)addr[i]];
}

/* 校验 values[0..n_val): 有限且 ∈ [0,255]。返回 0 = 通过, -1 = 拒绝。 */
static int _ce_eff_check_values(int n_val, const double* values)
{
    int i;
    if (n_val < 0 || (n_val > 0 && values == NULL))
        return -1;
    for (i = 0; i < n_val; i++) {
        double v = values[i];
        if (v != v || v < 0.0 || v > 255.0)
            return -1;
    }
    return 0;
}

/* 校验一组 int64 下标向量: 可容纳进 int 且值 ∈ [0, bound)。 */
static int _ce_eff_check_idx(int n, const int64_t* arr, int bound)
{
    int i;
    if (n <= 0)
        return 0;
    if (arr == NULL)
        return -1;
    for (i = 0; i < n; i++) {
        int v = (int)arr[i];
        if (arr[i] != (int64_t)v || v < 0 || v >= bound)
            return -1;
    }
    return 0;
}

#define CE_EFF_DP(n, i) ((const int64_t*)(intptr_t)shape[(n) * 5 + (i)])
#define CE_EFF_DPU8(n, i) ((uint8_t*)(intptr_t)shape[(n) * 5 + (i)])

/* ── 直接通道批量写入（_write_direct_channel_vectorized 全语义）──────────
 * 返回写入地址总数（>=0）或 -1（拒绝, **整批零写入**）。 */
CE_API int ce_eff_write_direct(
    int n_uni, const int64_t* shape,
    int n_val, const double* values, int is_motor)
{
    uint8_t scratch[512];
    int k, i;
    long total = 0;

    if (n_uni < 0 || n_uni > CE_EFF_MAXUNI)
        return -1;
    if (n_uni > 0 && shape == NULL)
        return -1;
    if (_ce_eff_check_values(n_val, values) != 0)
        return -1;

    /* ── pass1: 全部域校验（任一非法 → 整批拒绝, 零写入）── */
    for (k = 0; k < n_uni; k++) {
        int n = (int)shape[k * 5 + 0];
        const int64_t* addrs = CE_EFF_DP(k, 1);
        const int64_t* fxs = CE_EFF_DP(k, 2);
        if (n < 0 || n > 512)
            return -1;
        if (shape[k * 5 + 3] == 0 || shape[k * 5 + 4] == 0)
            return -1;                       /* buf/act 必给 */
        if (n == 0)
            continue;
        if (_ce_eff_check_idx(n, addrs, 512) != 0)
            return -1;
        if (_ce_eff_check_idx(n, fxs, n_val) != 0)
            return -1;
    }

    /* ── pass2: 提交（逐域; 每个向量内「读全部 → 顺序赋值」= numpy 语义）── */
    for (k = 0; k < n_uni; k++) {
        int n = (int)shape[k * 5 + 0];
        const int64_t* addrs = CE_EFF_DP(k, 1);
        const int64_t* fxs = CE_EFF_DP(k, 2);
        uint8_t* buf = CE_EFF_DPU8(k, 3);
        uint8_t* act = CE_EFF_DPU8(k, 4);
        if (is_motor) {
            /* `arr[a] = v`: 顺序赋值, 重复下标后者胜 —— C 同构 */
            for (i = 0; i < n; i++) {
                int a = (int)addrs[i];
                buf[a] = (uint8_t)((int)values[(int)fxs[i]]);
                act[a] = 1;
            }
        } else {
            /* `arr[a] = maximum(arr[a], v)`: RHS 全读原始 buf, 再后者胜赋值 */
            for (i = 0; i < n; i++) {
                int a = (int)addrs[i];
                uint8_t v = (uint8_t)((int)values[(int)fxs[i]]);
                uint8_t cur = buf[a];        /* 原始值（尚未写入本向量）*/
                scratch[i] = (v > cur) ? v : cur;
            }
            for (i = 0; i < n; i++) {
                int a = (int)addrs[i];
                buf[a] = scratch[i];
                act[a] = 1;
            }
        }
        total += n;
    }
    return (int)total;
}

#define CE_EFF_RP(n, i) ((const int64_t*)(intptr_t)shape[(n) * 21 + (i)])
#define CE_EFF_RPU8(n, i) ((uint8_t*)(intptr_t)shape[(n) * 21 + (i)])

/* ── RGBW 亮度模拟批量写入（_write_rgbw_channel_vectorized 全语义）───────
 * 返回写入地址总数（>=0）或 -1（拒绝, **整批零写入**）。 */
CE_API int ce_eff_write_rgbw(
    int n_uni, const int64_t* shape,
    int n_val, const double* values)
{
    double sums[CE_EFF_MAXSUM];
    /* ★ 2026-09-19 修（第三轮回访查出的 C 侧缺陷）：`part` 此前是
     *   `sums + CE_EFF_MAXSUM - CE_EFF_MAXDEV` —— 当某个批的 base_off 落进
     *   [MAXSUM-MAXDEV, MAXSUM) 区间时，`part` 与 `sums_k` **物理重叠** →
     *   第 2..4 通道的 bincount 结果被 sums_k 的累加**自己清零**
     *   （实测：12×512+4 设备批，C=223 vs Python=87；仅当 base_off≈6144 命中）。
     *   改为独立缓冲，语义与 Python 的"先写 part 再累加进 sums"逐字一致。 */
    double part_buf[CE_EFF_MAXDEV];
    uint8_t scratch[512];
    int base_off[CE_EFF_MAXUNI + 1];
    int k, i;
    long total = 0;

    if (n_uni < 0 || n_uni > CE_EFF_MAXUNI)
        return -1;
    if (n_uni > 0 && shape == NULL)
        return -1;
    if (_ce_eff_check_values(n_val, values) != 0)
        return -1;

    /* ── pass1: 全部域校验 + 设备数前缀和 ── */
    base_off[0] = 0;
    for (k = 0; k < n_uni; k++) {
        int n_dev = (int)shape[k * 21 + 0];
        const int64_t* devs[4];
        const int64_t* addrs[4];
        const int64_t* fxs[4];
        int ns[4];
        int ch;
        if (n_dev < 0 || n_dev > CE_EFF_MAXDEV)
            return -1;
        if (base_off[k] + n_dev > CE_EFF_MAXSUM)
            return -1;
        base_off[k + 1] = base_off[k] + n_dev;
        if (shape[k * 21 + 1] == 0 || shape[k * 21 + 2] == 0
            || shape[k * 21 + 19] == 0 || shape[k * 21 + 20] == 0)
            return -1;                       /* has_rgb/base/buf/act 必给 */
        for (ch = 0; ch < 4; ch++) {
            int off = 3 + ch * 4;
            ns[ch] = (int)shape[k * 21 + off];
            addrs[ch] = CE_EFF_RP(k, off + 1);
            fxs[ch] = CE_EFF_RP(k, off + 2);
            devs[ch] = CE_EFF_RP(k, off + 3);
            if (ns[ch] < 0 || ns[ch] > 512)
                return -1;
            if (_ce_eff_check_idx(ns[ch], addrs[ch], 512) != 0)
                return -1;
            if (_ce_eff_check_idx(ns[ch], fxs[ch], n_val) != 0)
                return -1;
            if (_ce_eff_check_idx(ns[ch], devs[ch], n_dev) != 0)
                return -1;
        }
    }

    /* ── pass2: 逐域（先聚合 sums 再四通道改写; 次序 r→g→b→w）── */
    for (k = 0; k < n_uni; k++) {
        int n_dev = (int)shape[k * 21 + 0];
        const uint8_t* has_rgb = CE_EFF_RPU8(k, 1);
        const uint8_t* base = CE_EFF_RPU8(k, 2);
        uint8_t* buf = CE_EFF_RPU8(k, 19);
        uint8_t* act = CE_EFF_RPU8(k, 20);
        double* sums_k = sums + base_off[k];
        double* part = part_buf;   /* 独立缓冲（见函数头说明，修同址别名） */
        int ch;
        int off;
        /* 通道视图（与 pass1 同址重取） */
        const int64_t* ca[4];
        const int64_t* cf[4];
        const int64_t* cd[4];
        int cn[4];
        for (ch = 0; ch < 4; ch++) {
            off = 3 + ch * 4;
            cn[ch] = (int)shape[k * 21 + off];
            ca[ch] = CE_EFF_RP(k, off + 1);
            cf[ch] = CE_EFF_RP(k, off + 2);
            cd[ch] = CE_EFF_RP(k, off + 3);
        }
        /* sums = Σ_c bincount_c（Python 的 `sums = 0; sums += ...` 次序） */
        _ce_eff_bincount(sums_k, n_dev, 1, cn[0], ca[0], cd[0], base);
        for (ch = 1; ch < 4; ch++) {
            int d;
            _ce_eff_bincount(part, n_dev, 1, cn[ch], ca[ch], cd[ch], base);
            for (d = 0; d < n_dev; d++)
                sums_k[d] += part[d];
        }
        /* 四通道改写（r→g→b→w; 每通道「先算完 rhs 再后者胜赋值」——
         * 对应 numpy `arr[a] = maximum(arr[a], out)` 的读-写两阶段,
         * 通道之间保持 r→g→b→w 串行 = 原版语句次序） */
        for (ch = 0; ch < 4; ch++) {
            int is_w = (ch == 3);
            for (i = 0; i < cn[ch]; i++) {
                int a = (int)ca[ch][i];
                int dv = (int)cd[ch][i];
                uint8_t defv;
                double out;
                uint8_t o;
                uint8_t cur;
                if (sums_k[dv] == 0.0) {
                    if (is_w)
                        defv = has_rgb[dv] ? (uint8_t)0 : (uint8_t)255;
                    else
                        defv = (uint8_t)255;
                } else {
                    defv = base[a];
                }
                out = rint((double)defv * (values[(int)cf[ch][i]] / 255.0));
                o = (uint8_t)out;
                cur = buf[a];            /* 该通道写之前的值（本通道内尚未落写）*/
                scratch[i] = (o > cur) ? o : cur;
            }
            for (i = 0; i < cn[ch]; i++) {
                int a = (int)ca[ch][i];
                buf[a] = scratch[i];
                act[a] = 1;
            }
            total += cn[ch];
        }
    }
    return (int)total;
}
