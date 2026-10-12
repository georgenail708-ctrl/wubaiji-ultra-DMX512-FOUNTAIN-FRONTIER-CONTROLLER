/* ═══════════════════════════════════════════════════════════════════════════
 * 矩阵像素引擎（matrix pixel engine）计算内核 —— ce_mtx_pattern / ce_mtx_particle
 *
 * 对齐 Python 原版: src/ui/matrix_slideshow/matrix_pixel_engine_ext_v40.py
 *   · _compute_pattern  :251 （v40 图案族主计算体）
 *   · _metric_family    :113 （96 族度量, 逐族 1:1 转录）
 *   · _coords           :85  （col/row → nx/ny → twist 旋转 → angle/r）
 *   · _particle_sources :289 （16 种粒子轨道）
 *   · _compute_particle :354 （逐设备 × 逐粒子 exp 核 = 全引擎最重段）
 *   · _band :30 / _smooth :19 / _tri :14 / _clamp01 :10 / _dist_phase :26
 *   · _hash01 :40 / _hash01 整数哈希（int64 回绕复刻, 见下）
 *
 * 为什么需要它（2026-09-18 实测归因, render_lab/bench/probe_matrix_pixel_profile.py
 * + _ui_validation/probe_v40_segments.py）:
 *
 *   1356 台设备 / v40 模式族（1254/1615 = 77.6% 的 ext 模式数）:
 *     _compute_pattern   5.69 ms/帧 （coords 49.3% / metric 18.9% / style+dict 31.9%）
 *     _compute_particle 29.87 ms/帧 （逐设备×逐粒子 exp 核 82.9%）
 *   Python 面板 40ms 主循环 tick 实测 matrix_v40_particle_physics 27.8 ms/帧
 *   —— 单帧就把 40ms 预算吃掉 70%。两个函数共覆盖 v40 的 964/1254 个模式
 *   （_compute_pattern 784 + _compute_particle 180）。
 *
 *   ★ 与内置模式族的分工: `_compute_weights_array_fast` 那批（effect_ripple 等）
 *   已是 numpy 向量化, 1356 台实测 0.032ms/帧 — C 化收益 < 5%, 且要复刻 numpy
 *   的浮点批处理顺序（风险高收益低）。判据 = 逐设备 Python 循环 + 标量超越函数。
 *
 * ── 位级等价纪律（与既有 CE 内核同标准, 逐条实证）──────────────────────
 *  1. 全程 f64（Python float = C double）; 本编译单元已 `#pragma
 *     fp_contract(off)`（ce_engine.c:84）→ /O2 不做 FMA 收缩。
 *  2. 超越函数（sin/cos/atan2/exp/floor/sqrt）**运行时从 ucrtbase.dll 按名
 *     解析** —— CPython 在 Windows 链接的就是 ucrtbase。实证见
 *     `_ui_validation/probe_math_ucrt_equiv.py`: 20 万×7 域样本 ×13 函数
 *     0 失配（**唯一例外 hypot**, 见 3）。
 *  3. ★ `math.hypot` **不是** ucrtbase 的 hypot（实测差 1ulp 达 67% 采样）:
 *     CPython 用自研 vector_norm（补偿求和 + 缩放）。实证
 *     `_ui_validation/probe_hypot_cr.py`: 24000 样本 0 失配 ⇒ math.hypot 在
 *     真实域上 = **精确舍入的 sqrt(a²+b²)**。本内核用 Dekker double-double
 *     复刻（`_ce_mtx_hypot`: two_product 求 a²、b² 精确双元 → two_sum 归并 →
 *     精确残差 (hi - sq²) 修正 → sq + rr/(2sq)）。实证
 *     `_ui_validation/prevalidate_c_math.py`: 20 万×8 域 0 失配。
 *     ★ 关键细节: 残差必须用 two_product(sq,sq) 的**精确**结果取差, 直接
 *     `sq*sq` 自带 1ulp 舍入会把修正量放大约 1000 倍 → 实测 57k/160k 失配。
 *     量级守卫: 仅当 a、b 的平方不溢/不欠（|v| ∈ [1e-140, 1e140]）时走 dd 路径,
 *     否则缩放 2^k 后计算（超范围输入在像素域不可能出现, 仅防御）。
 *  4. 整数哈希 `_hash01` 是 Python **任意精度**整数, C 侧用 int64 回绕:
 *     ^ / 算术右移 / 乘法在 Z→Z/2^64 同态下保持低位; 末步 `& 0xFFFFFFFF`
 *     只看低 32 位 ⇒ 回绕复刻成立。实证 prevalidate_c_math.py: 40 万×4 量级域
 *     0 失配（含 ±1e18）。乘法用 unsigned 回绕避免有符号 UB, `>>` 显式符号填充。
 *  5. 取整语义: `int(x)` 向零截断 = C 强转; `math.floor` → ucrtbase floor。
 *  6. Python `%` 语义（float_rem）: `_ce_mtx_py_mod` = fmod + 符号对齐 +
 *     零的 copysign（与 CPython float_rem 逐分支一致, 实测 0 失配）。
 *  7. Python max/min 语义用既有 _ce_py_max/_ce_py_min 三元复刻（平局返回首参）;
 *     `abs()` → ucrtbase fabs; `x ** n` → ucrtbase pow（CPython float_pow 同导出）。
 *  8. 累加次序: 粒子 blob 逐粒子左结合 `+=`（Python sum 序）, 从 0.0 起。
 *
 * ── 契约边界（Python 侧 matrix_pixel_path 事前判定, 任一不满足 → 不调用本内核）
 *  · `engine._grid` 存在且 `cols/rows >= 1`; `col/row` 数组长度 == n
 *  · 全部标量参数 finite（NaN/Inf → 内核返回 -2, 零写入, Python 回退原版）
 *  · `variant/family_offset >= 0`（Python 侧 mode→idx 映射保证）
 *  · n <= CE_MTX_MAXN（超大网格留 Python, 避免栈/临时缓冲无界）
 *  · 输出只写 `out[0..n)`; 内核**不吞异常**（返回码 != 0 → Python 回退）
 * ═══════════════════════════════════════════════════════════════════════════ */

#define CE_MTX_MAXN 8192
#define CE_MTX_MAXPARTS 72      /* count = max(3, min(72, ...)) —— _compute_particle */

/* ── 动态解析 ucrtbase（CPython 同一实现 ⇒ 逐位相等）─────────────────── */
typedef double (__cdecl *ce_mtx_math1)(double);
typedef double (__cdecl *ce_mtx_math2)(double, double);

static ce_mtx_math1 volatile _ce_mtx_sin = NULL;
static ce_mtx_math1 volatile _ce_mtx_cos = NULL;
static ce_mtx_math1 volatile _ce_mtx_exp = NULL;
static ce_mtx_math1 volatile _ce_mtx_sqrt = NULL;
static ce_mtx_math1 volatile _ce_mtx_fabs = NULL;
static ce_mtx_math1 volatile _ce_mtx_floor = NULL;
static ce_mtx_math2 volatile _ce_mtx_atan2 = NULL;
static ce_mtx_math2 volatile _ce_mtx_pow = NULL;

static void* _ce_mtx_sym(const char* name)
{
    HMODULE m = GetModuleHandleA("ucrtbase.dll");
    if (m == NULL)
        m = LoadLibraryA("ucrtbase.dll");
    if (m == NULL)
        return NULL;
    return (void*)GetProcAddress(m, name);
}

static double _ce_msin(double x)
{
    if (_ce_mtx_sin == NULL)
        _ce_mtx_sin = (ce_mtx_math1)_ce_mtx_sym("sin");
    return (_ce_mtx_sin != NULL) ? _ce_mtx_sin(x) : sin(x);
}

static double _ce_mcos(double x)
{
    if (_ce_mtx_cos == NULL)
        _ce_mtx_cos = (ce_mtx_math1)_ce_mtx_sym("cos");
    return (_ce_mtx_cos != NULL) ? _ce_mtx_cos(x) : cos(x);
}

static double _ce_mexp(double x)
{
    if (_ce_mtx_exp == NULL)
        _ce_mtx_exp = (ce_mtx_math1)_ce_mtx_sym("exp");
    return (_ce_mtx_exp != NULL) ? _ce_mtx_exp(x) : exp(x);
}

static double _ce_msqrt(double x)
{
    if (_ce_mtx_sqrt == NULL)
        _ce_mtx_sqrt = (ce_mtx_math1)_ce_mtx_sym("sqrt");
    return (_ce_mtx_sqrt != NULL) ? _ce_mtx_sqrt(x) : sqrt(x);
}

static double _ce_mfabs(double x)
{
    if (_ce_mtx_fabs == NULL)
        _ce_mtx_fabs = (ce_mtx_math1)_ce_mtx_sym("fabs");
    return (_ce_mtx_fabs != NULL) ? _ce_mtx_fabs(x) : fabs(x);
}

static double _ce_mfloor(double x)
{
    if (_ce_mtx_floor == NULL)
        _ce_mtx_floor = (ce_mtx_math1)_ce_mtx_sym("floor");
    return (_ce_mtx_floor != NULL) ? _ce_mtx_floor(x) : floor(x);
}

static double _ce_matan2(double y, double x)
{
    if (_ce_mtx_atan2 == NULL)
        _ce_mtx_atan2 = (ce_mtx_math2)_ce_mtx_sym("atan2");
    return (_ce_mtx_atan2 != NULL) ? _ce_mtx_atan2(y, x) : atan2(y, x);
}

static double _ce_mpow(double x, double y)
{
    if (_ce_mtx_pow == NULL)
        _ce_mtx_pow = (ce_mtx_math2)_ce_mtx_sym("pow");
    return (_ce_mtx_pow != NULL) ? _ce_mtx_pow(x, y) : pow(x, y);
}

#define CE_MTX_PI  3.14159265358979323846
#define CE_MTX_TAU 6.28318530717958647692

static int _ce_mtx_is_nan(double x)
{
    unsigned long long u;
    memcpy(&u, &x, 8);
    return ((u & 0x7FF0000000000000ULL) == 0x7FF0000000000000ULL)
        && ((u & 0x000FFFFFFFFFFFFFULL) != 0ULL);
}

static int _ce_mtx_is_finite(double x)
{
    unsigned long long u;
    memcpy(&u, &x, 8);
    return (u & 0x7FF0000000000000ULL) != 0x7FF0000000000000ULL;
}

/* 整数哈希入参必须能安全截断到 int64（`(long long)(double)` 在超出 int64
 * 范围时是 UB）。像素引擎的实参量级 ~1e7（t ≤ 1e5 × 系数 ≤ 10）; 阈值
 * 1e13 留 6 个数量级余量, 命中即返回 -2 让 Python 回退原版（原版 int(x)
 * 是任意精度, 无 UB 可言）。 */
static int _ce_mtx_i64_safe(double x)
{
    return _ce_mtx_is_finite(x) && x > -1e13 && x < 1e13;
}

/* ── Python `%`（float_rem）语义（CPython floatobject.c float_rem）────── */
static double _ce_mtx_copysign0(double y)
{
    /* copysign(0.0, y): y < 0 或 y == -0.0 → -0.0 */
    unsigned long long u;
    memcpy(&u, &y, 8);
    return (u >> 63) ? -0.0 : 0.0;
}

static double _ce_mtx_py_mod(double x, double y)
{
    double m = fmod(x, y);
    if (m != 0.0) {
        if ((y < 0.0) != (m < 0.0))
            m += y;
    } else {
        /* 余数为零: 取除数符号（CPython 注释: fmod 的零符号跨平台不一致）*/
        m = _ce_mtx_copysign0(y);
    }
    return m;
}

/* ── _clamp01（v40:10）──────────────────────────────────────────────── */
static double _ce_mtx_clamp01(double v)
{
    if (v < 0.0)
        return 0.0;
    if (v > 1.0)
        return 1.0;
    return v;
}

/* ── _tri（v40:14）: p = float(x) % 1.0; 1.0 - abs(p*2-1) ───────────── */
static double _ce_mtx_tri(double x)
{
    double p = _ce_mtx_py_mod(x, 1.0);
    return 1.0 - _ce_mfabs(p * 2.0 - 1.0);
}

/* ── _dist_phase（v40:26）: abs(((a-b+0.5) % 1.0) - 0.5) ───────────── */
static double _ce_mtx_dist_phase(double a, double b)
{
    return _ce_mfabs(_ce_mtx_py_mod((a - b) + 0.5, 1.0) - 0.5);
}

/* ── _smooth（v40:19）───────────────────────────────────────────────── */
static double _ce_mtx_smooth(double edge0, double edge1, double x)
{
    double t;
    if (_ce_mfabs(edge1 - edge0) < 1e-9)
        return (x >= edge1) ? 1.0 : 0.0;
    t = _ce_mtx_clamp01((x - edge0) / (edge1 - edge0));
    return t * t * (3.0 - 2.0 * t);
}

/* ── _band（v40:30）────────────────────────────────────────────────── */
static double _ce_mtx_band(double metric, double width, double softness)
{
    double p = _ce_mtx_dist_phase(metric, 0.0);
    double inner;
    width = _ce_py_max(0.001, _ce_py_min(1.5, width));
    softness = _ce_py_max(0.001, _ce_py_min(1.0, softness));
    inner = width * _ce_py_max(0.0, 1.0 - softness);
    if (p <= inner)
        return 1.0;
    return 1.0 - _ce_mtx_smooth(inner, _ce_py_max(inner + 1e-6, width), p);
}

/* ── 整数哈希 _hash01（v40:40）—— Python 任意精度 → int64 回绕 ──────── */
static long long _ce_mtx_ashr(long long v, int k)
{
    /* Python `>>` 对负整数 = 算术右移（floor 除）; MSVC 亦为 sar, 但显式
     * 用无符号逻辑移 + 符号填充实现, 不依赖实现定义行为。 */
    unsigned long long u = (unsigned long long)v;
    unsigned long long s = u >> k;
    if (v < 0 && k > 0)
        s |= ~((~0ULL) >> k);
    return (long long)s;
}

static double _ce_mtx_hash01(long long a, long long b, long long c)
{
    long long n;
    unsigned long long n2;
    n = (long long)((unsigned long long)(a * 374761393LL)
                    + (unsigned long long)(b * 668265263LL)
                    + (unsigned long long)(c * 1442695041LL));
    n = n ^ _ce_mtx_ashr(n, 13);
    n2 = (unsigned long long)n * (unsigned long long)1274126177ULL;
    n = (long long)n2;
    n = n ^ _ce_mtx_ashr(n, 16);
    return (double)(n & 0xFFFFFFFFLL) / 4294967295.0;
}

/* ── math.hypot 逐位复刻（CPython vector_norm = 精确舍入 sqrt(a²+b²)）──
 * 见文件头 §3 的实证与关键细节说明。只用 + - * / 与 ucrtbase sqrt。 */
#define CE_MTX_SPLITTER 134217729.0     /* 2^27 + 1 (Dekker) */

static void _ce_mtx_two_sum(double a, double b, double* s, double* e)
{
    double bb;
    *s = a + b;
    bb = *s - a;
    *e = (a - (*s - bb)) + (b - bb);
}

static void _ce_mtx_split(double a, double* hi, double* lo)
{
    double c = CE_MTX_SPLITTER * a;
    *hi = c - (c - a);
    *lo = a - *hi;
}

static void _ce_mtx_two_product(double a, double b, double* p, double* e)
{
    double ah, al, bh, bl;
    *p = a * b;
    _ce_mtx_split(a, &ah, &al);
    _ce_mtx_split(b, &bh, &bl);
    *e = ((ah * bh - *p) + ah * bl + al * bh) + al * bl;
}

/* 核心: 精确平方和 + 精确舍入平方根（调用方保证量级安全）*/
static double _ce_mtx_hypot_core(double a, double b)
{
    double p1, e1, p2, e2, s, e, hi, lo, sq, ph, pe, rr;
    _ce_mtx_two_product(a, a, &p1, &e1);
    _ce_mtx_two_product(b, b, &p2, &e2);
    _ce_mtx_two_sum(p1, p2, &s, &e);
    e = e + e1 + e2;
    _ce_mtx_two_sum(s, e, &hi, &lo);
    if (hi == 0.0)
        return 0.0;
    sq = _ce_msqrt(hi);
    _ce_mtx_two_product(sq, sq, &ph, &pe);
    rr = ((hi - ph) - pe) + lo;      /* ★ 必须用精确 two_product 取残差 */
    return sq + rr / (2.0 * sq);
}

static double _ce_mtx_hypot(double a, double b)
{
    double aa = _ce_mfabs(a), bb = _ce_mfabs(b);
    if (aa == 0.0 && bb == 0.0)
        return 0.0;
    if (!_ce_mtx_is_finite(aa) || !_ce_mtx_is_finite(bb))
        return aa + bb;              /* 与 CPython vector_norm 同: inf 传播 */
    /* 量级守卫: 平方不溢/不欠的窗口内直接算; 否则缩放 2^k 后算再还原。
     * 像素引擎坐标域恒在窗口内（|v| <= ~1e3）, 缩放分支仅防御。*/
    if (aa >= 1e-140 && aa <= 1e140 && bb >= 1e-140 && bb <= 1e140)
        return _ce_mtx_hypot_core(aa, bb);
    {
        int k = 0;
        double sa = aa, sb = bb, r;
        while (sa > 1e140 || sb > 1e140) { sa *= 0x1p-500; sb *= 0x1p-500; k += 500; }
        while ((sa < 1e-140 && sa > 0.0) || (sb < 1e-140 && sb > 0.0)) {
            sa *= 0x1p500; sb *= 0x1p500; k -= 500;
        }
        if (sa == 0.0 && sb == 0.0)
            return 0.0;
        r = _ce_mtx_hypot_core(sa, sb);
        /* 缩放: a,b *= 2^∓500 ⇒ r_scaled = r_true · 2^-k ⇒ 还原乘 2^k */
        return (k == 0) ? r : ldexp(r, k);
    }
}

/* ── _coords（v40:85）—— col/row → nx/ny/x/y/rx/ry/angle/r ─────────── */
typedef struct {
    int col, row;
    double nx, ny, x, y, angle, r;
} ce_mtx_coord;

static void _ce_mtx_coords(const int32_t* col, const int32_t* row, int i,
                           int cols, int rows, double aspect, double twist,
                           double phase, ce_mtx_coord* c)
{
    double rot, cr, sr;
    c->col = (int)col[i];
    c->row = (int)row[i];
    c->nx = (double)c->col / (double)((cols > 1) ? (cols - 1) : 1);
    c->ny = (double)c->row / (double)((rows > 1) ? (rows - 1) : 1);
    c->x = (c->nx - 0.5) * _ce_py_max(1.0, aspect);
    c->y = (c->ny - 0.5) * _ce_py_max(1.0, 1.0 / _ce_py_max(0.001, aspect));
    rot = twist * 0.22 + phase * 0.05;
    cr = _ce_mcos(rot);
    sr = _ce_msin(rot);
    {
        double rx = c->x * cr - c->y * sr;
        double ry = c->x * sr + c->y * cr;
        c->x = rx;                      /* 返回 rx/ry（Python 的 x,y 位）*/
        c->y = ry;
    }
    c->angle = _ce_matan2(c->y, c->x);
    c->r = _ce_mtx_hypot(c->x, c->y);
}

/* ── _metric_family（v40:113）—— 96 族度量, 逐族转录 ───────────────── */
static double _ce_mtx_metric(int family, double nx, double ny, double x, double y,
                             double angle, double r, double t, double phase,
                             double mix, double bass, double mid, double high,
                             int variant)
{
    double diag = nx + ny;
    double anti = nx - ny;
    double diamond = _ce_mfabs(x) + _ce_mfabs(y);
    double square = _ce_py_max(_ce_mfabs(x), _ce_mfabs(y));
    int fam = family % 96;

    switch (fam) {
    case 0:
        return nx * (2.0 + mix * 3.0) + 0.18 * _ce_msin(ny * CE_MTX_PI * 4.0 + phase);
    case 1:
        return ny * (2.0 + mix * 3.0) + 0.18 * _ce_mcos(nx * CE_MTX_PI * 4.0 - phase);
    case 2:
        return diag + 0.22 * _ce_msin(anti * CE_MTX_PI * (3.0 + bass * 3.0));
    case 3:
        return anti + 0.22 * _ce_mcos(diag * CE_MTX_PI * (3.0 + mid * 3.0));
    case 4:
        return r * (2.4 + bass * 3.6) + angle / CE_MTX_TAU * (1.0 + mix);
    case 5:
        return angle / CE_MTX_TAU * (2.0 + mix * 5.0) + r * (1.4 + high * 2.6);
    case 6:
        return diamond * (3.0 + bass * 3.0) + 0.18 * _ce_msin(angle * 4.0 + t);
    case 7:
        return square * (3.0 + mid * 3.0) + 0.15 * _ce_mcos(angle * 6.0 - t);
    case 8:
        return _ce_mfabs(_ce_msin((nx + phase * 0.03) * CE_MTX_PI * (4 + variant % 7))) + ny * mix;
    case 9:
        return _ce_mfabs(_ce_msin((ny - phase * 0.03) * CE_MTX_PI * (4 + variant % 7))) + nx * mix;
    case 10:
        return (_ce_mfloor(nx * (4 + variant % 8)) + _ce_mfloor(ny * (3 + variant % 7))) * 0.17;
    case 11:
        return _ce_mtx_py_mod(nx * (3 + variant % 7), 1.0)
             + _ce_mtx_py_mod(ny * (5 + variant % 5), 1.0) * 0.35;
    case 12:
        return _ce_py_min(_ce_mfabs(x), _ce_mfabs(y)) * (5.0 + bass * 5.0) + r * mix;
    case 13:
        return _ce_py_max(_ce_mfabs(x), _ce_mfabs(y)) * (5.0 + mid * 4.0)
             - _ce_py_min(_ce_mfabs(x), _ce_mfabs(y)) * mix;
    case 14:
        return _ce_mfabs(x * _ce_mcos(phase) + y * _ce_msin(phase)) * (5.0 + high * 4.0);
    case 15:
        return _ce_mfabs(-x * _ce_msin(phase) + y * _ce_mcos(phase)) * (5.0 + bass * 4.0);
    case 16:
        return r + 0.28 * _ce_msin(angle * (3 + variant % 9) + t * 0.7);
    case 17:
        return diamond + 0.24 * _ce_mcos((nx * 2.0 - ny) * CE_MTX_PI + phase);
    case 18:
        return square + 0.22 * _ce_msin((ny * 2.0 + nx) * CE_MTX_PI - phase);
    case 19:
        return _ce_mfabs(_ce_msin((diag + phase * 0.02) * CE_MTX_PI * (2 + variant % 8))) * 0.55 + r;
    case 20:
        return _ce_mfabs(_ce_mcos((anti - phase * 0.02) * CE_MTX_PI * (2 + variant % 8))) * 0.55 + r;
    case 21:
        return _ce_py_min(_ce_py_min(_ce_mtx_hypot(nx - 0.18, ny - 0.22),
                                     _ce_mtx_hypot(nx - 0.78, ny - 0.36)),
                          _ce_mtx_hypot(nx - 0.43, ny - 0.82)) * (4.0 + bass * 3.0);
    case 22:
        return (_ce_mpow(_ce_mfabs(x), 1.0 + mix) + _ce_mpow(_ce_mfabs(y), 1.4 + high))
             * (2.6 + bass * 2.0);
    case 23:
        return (_ce_msin(nx * CE_MTX_PI * (3 + variant % 6) + phase)
              + _ce_mcos(ny * CE_MTX_PI * (4 + variant % 5) - phase)) * 0.5;
    case 24:
        return _ce_mtx_py_mod(_ce_mfloor((nx + ny + t * 0.03) * (4 + variant % 7)), 2.0) + r * mix;
    case 25:
        return _ce_mtx_py_mod(_ce_mfloor((nx - ny - t * 0.02) * (5 + variant % 6)), 2.0) + diamond * mix;
    case 26:
        return _ce_mfabs(_ce_msin((r * (4 + variant % 8) + angle / CE_MTX_TAU) * CE_MTX_PI));
    case 27:
        return _ce_mfabs(_ce_mcos((diamond * (3 + variant % 7) - angle / CE_MTX_TAU) * CE_MTX_PI));
    case 28:
        return _ce_mtx_hash01((long long)(nx * 12), (long long)(ny * 12),
                              (long long)variant) * 0.6 + r * 0.8;
    case 29:
        return _ce_mtx_hash01((long long)((nx + t * 0.02) * 16),
                              (long long)((ny - t * 0.02) * 16),
                              (long long)variant) * 0.8 + diamond * 0.5;
    case 30:
        return _ce_mfabs(nx - _ce_mtx_tri(t * 0.13 + ny * 0.5)) * 2.0;
    case 31:
        return _ce_mfabs(ny - _ce_mtx_tri(t * 0.11 + nx * 0.6)) * 2.0;
    case 32:
        return _ce_py_min(_ce_mfabs(nx - 0.25), _ce_mfabs(nx - 0.75)) * 3.0 + ny * mix;
    case 33:
        return _ce_py_min(_ce_mfabs(ny - 0.25), _ce_mfabs(ny - 0.75)) * 3.0 + nx * mix;
    case 34:
        return _ce_mfabs(_ce_msin((nx * ny + t * 0.03) * CE_MTX_PI * (8 + variant % 11)));
    case 35:
        return _ce_mfabs(_ce_mcos(((nx - 0.5) * (ny - 0.5) - t * 0.02) * CE_MTX_PI * (10 + variant % 9)));
    case 36:
        return (_ce_mfabs(x + y) * 1.6 + _ce_mfabs(x - y) * 0.4) * (2.0 + mix);
    case 37:
        return (_ce_mfabs(x - y) * 1.6 + _ce_mfabs(x + y) * 0.4) * (2.0 + mix);
    case 38:
        return _ce_mfabs(_ce_msin((angle * (2 + variant % 7) + r * (5 + bass * 4.0)) * CE_MTX_PI));
    case 39:
        return _ce_mfabs(_ce_mcos((angle * (3 + variant % 8) - r * (4 + mid * 4.0)) * CE_MTX_PI));
    case 40:
        return _ce_py_min(_ce_mfabs(nx - 0.5), _ce_mfabs(ny - 0.5)) * (5 + variant % 8);
    case 41:
        return _ce_py_max(_ce_mfabs(nx - 0.5), _ce_mfabs(ny - 0.5)) * (5 + variant % 8);
    case 42:
        return _ce_mfabs(_ce_msin((_ce_mfloor(nx * 8) + ny * 8 + t * 0.2) * CE_MTX_PI * 0.5));
    case 43:
        return _ce_mfabs(_ce_mcos((_ce_mfloor(ny * 8) + nx * 8 - t * 0.2) * CE_MTX_PI * 0.5));
    case 44:
        return _ce_mfabs(_ce_msin(nx * CE_MTX_PI * 6 + t)) * 0.35
             + _ce_mfabs(_ce_msin(ny * CE_MTX_PI * 4 - t * 0.7)) * 0.65;
    case 45:
        return _ce_mfabs(_ce_mcos(nx * CE_MTX_PI * 5 - t * 0.8)) * 0.65
             + _ce_mfabs(_ce_mcos(ny * CE_MTX_PI * 7 + t)) * 0.35;
    case 46:
        return _ce_mfabs(_ce_msin((r + diamond * 0.35) * CE_MTX_PI * (4 + variant % 7) - t));
    case 47:
        return _ce_mfabs(_ce_mcos((square + r * 0.25) * CE_MTX_PI * (5 + variant % 8) + t));
    case 48:
        return _ce_py_min(_ce_mtx_hypot(nx - 0.15 - 0.2 * _ce_msin(t), ny - 0.2),
                          _ce_mtx_hypot(nx - 0.85, ny - 0.8 + 0.2 * _ce_mcos(t))) * 4.0;
    case 49:
        return _ce_py_min(_ce_py_min(_ce_mtx_hypot(nx - 0.25, ny - 0.75),
                                     _ce_mtx_hypot(nx - 0.75, ny - 0.25)),
                          _ce_mtx_hypot(nx - 0.5, ny - 0.5)) * 4.5;
    case 50:
        return (_ce_mfabs(_ce_msin(diag * CE_MTX_PI * 4.0 + phase))
              + _ce_mtx_hash01((long long)(nx * 8), (long long)(ny * 8),
                               (long long)variant) * mix) * 0.7;
    case 51:
        return (_ce_mfabs(_ce_mcos(anti * CE_MTX_PI * 4.0 - phase))
              + _ce_mtx_hash01((long long)(nx * 9), (long long)(ny * 7),
                               (long long)variant) * mix) * 0.7;
    case 52:
        return _ce_mfabs(_ce_msin((diamond + _ce_mtx_hash01((long long)(nx * 5),
                                                            (long long)(ny * 5),
                                                            (long long)variant) * 0.2)
                                  * CE_MTX_PI * 5.0 - t));
    case 53:
        return _ce_mfabs(_ce_mcos((square + _ce_mtx_hash01((long long)(nx * 6),
                                                           (long long)(ny * 4),
                                                           (long long)variant) * 0.2)
                                  * CE_MTX_PI * 5.0 + t));
    case 54:
        return _ce_mfabs(_ce_msin((nx * (2 + variant % 6) + ny * (3 + variant % 7))
                                  * CE_MTX_PI + t));
    case 55:
        return _ce_mfabs(_ce_mcos((nx * (3 + variant % 7) - ny * (2 + variant % 6))
                                  * CE_MTX_PI - t));
    case 56:
        return _ce_mtx_tri((r + angle / CE_MTX_TAU * 0.4) * (3 + variant % 7) - t * 0.2);
    case 57:
        return _ce_mtx_tri((diamond - angle / CE_MTX_TAU * 0.3) * (4 + variant % 6) + t * 0.2);
    case 58:
        return _ce_mtx_hash01((long long)((nx + _ce_msin(t) * 0.03) * 18),
                              (long long)((ny + _ce_mcos(t) * 0.03) * 18),
                              (long long)variant);
    case 59:
        return _ce_mtx_hash01((long long)(nx * (10 + variant % 9)),
                              (long long)(ny * (10 + variant % 7)),
                              (long long)((int)(t * 4) + variant));
    default:
        break;
    }
    if (fam < 72) {                     /* 60..71: 多臂环 */
        double arms = (double)(2 + fam % 10);
        return _ce_mfabs(_ce_msin(angle * arms + r * (3.0 + mix * 5.0) - phase));
    }
    if (fam < 84) {                     /* 72..83: 抖动格 */
        double cell = (double)(3 + fam % 9);
        return _ce_mtx_hash01((long long)((nx + phase * 0.03) * cell),
                              (long long)((ny - phase * 0.02) * (cell + 2.0)),
                              (long long)variant) * 0.7 + r * 0.5;
    }
    {                                   /* 84..95: 花瓣干涉 */
        double arms = (double)(3 + fam % 8);
        return _ce_mfabs(_ce_mcos((angle * arms - diamond * (2.0 + mix * 3.0) + phase)
                                  * CE_MTX_PI));
    }
}

/* ── _compute_pattern 造型分支（v40:261-285）───────────────────────── */
static double _ce_mtx_style_val(int style, double metric, double freq, double phase,
                                double width, double soft, double mix, double bass,
                                double mid, double high, double rms, double t,
                                double nx, double ny, int col, int row, int variant)
{
    double mf = metric * freq - phase;
    if (style == 0)
        return _ce_mtx_band(mf, width, soft);
    if (style == 1)
        return _ce_mpow(_ce_mtx_tri(mf), 1.0 + high * 2.0);
    if (style == 2)
        return (_ce_mtx_py_mod(mf, 1.0)
                < _ce_py_max(0.02, _ce_py_min(0.85, width * (1.4 + mix)))) ? 1.0 : 0.0;
    if (style == 3) {
        double cell = _ce_mtx_hash01((long long)(nx * (6 + variant % 11)),
                                     (long long)(ny * (5 + variant % 9)),
                                     (long long)((int)(phase * 8) + variant));
        return _ce_mtx_clamp01(_ce_mtx_band(mf, width, soft) * 0.72
                               + cell * (0.15 + high * 0.28));
    }
    if (style == 4) {
        double primary = _ce_mtx_band(mf, width, soft);
        double secondary = _ce_mtx_band(metric * (freq * 0.55 + 0.3) + phase * 0.7,
                                        width * 1.55, soft * 1.3);
        return _ce_mtx_clamp01(primary * (0.70 + bass * 0.25)
                               + secondary * (0.18 + mid * 0.35));
    }
    if (style == 5) {
        double dots = _ce_mtx_hash01((long long)(nx * (8 + variant % 10)),
                                     (long long)(ny * (7 + variant % 8)),
                                     (long long)((int)(t * (3 + variant % 5)) + variant));
        return _ce_py_max(_ce_mtx_band(mf, width, soft) * 0.65,
                          (dots > 0.78 - mix * 0.18) ? dots : 0.0);
    }
    if (style == 6)
        return _ce_mtx_clamp01((_ce_msin(mf * CE_MTX_TAU)
                                + _ce_mcos((metric * freq * 0.37 + phase) * CE_MTX_TAU)
                                + 2.0) * 0.25);
    if (style == 7) {
        double stripe = _ce_mfabs(_ce_msin(((double)(col + row) + phase * 4.0)
                                           * CE_MTX_PI
                                           / (double)_ce_py_max(1.0, (double)(2 + variant % 7))));
        return _ce_py_max(_ce_mtx_band(mf, width, soft), stripe * (0.25 + mix * 0.45));
    }
    return _ce_mtx_smooth(0.08, 0.95, _ce_mtx_tri(mf)) * (0.7 + rms * 0.3);
}

/* ── _particle_sources（v40:289）—— 16 种轨道, 逐种转录 ─────────────── */
static int _ce_mtx_particle_sources(int variant, double t, int count,
                                    double bass, double mid, double high,
                                    double* px, double* py, double* pb)
{
    double speed = 0.08 + 0.018 * (variant % 11) + bass * 0.10;
    int mode = variant % 16;
    int k;
    for (k = 0; k < count; k++) {
        long long seed = (long long)variant * 101LL + (long long)k * 37LL;
        double bx = _ce_mtx_hash01(seed, 1LL, 0LL);
        double by = _ce_mtx_hash01(seed, 2LL, 0LL);
        double x = 0.0, y = 0.0;
        if (mode == 0) {
            x = _ce_mtx_py_mod(bx + t * speed * (0.5 + _ce_mtx_hash01(seed, 3LL, 0LL)), 1.0);
            y = _ce_mtx_py_mod(by + 0.08 * _ce_msin(t * 0.7 + k), 1.0);
        } else if (mode == 1) {
            x = _ce_mtx_py_mod(bx + 0.08 * _ce_msin(t + k), 1.0);
            y = _ce_mtx_py_mod(by + t * speed * (0.5 + _ce_mtx_hash01(seed, 4LL, 0LL)), 1.0);
        } else if (mode == 2) {
            double ang = t * speed * 2.0 + (double)k * CE_MTX_TAU / (double)((count > 1) ? count : 1);
            double rad = 0.12 + 0.36 * _ce_mtx_hash01(seed, 5LL, 0LL) + mid * 0.08;
            x = 0.5 + _ce_mcos(ang) * rad;
            y = 0.5 + _ce_msin(ang * (0.8 + high * 0.3)) * rad;
        } else if (mode == 3) {
            x = 0.5 + (bx - 0.5) * _ce_mtx_tri(t * speed + k * 0.13);
            y = 0.5 + (by - 0.5) * _ce_mtx_tri(t * speed * 0.7 + k * 0.17);
        } else if (mode == 4) {
            x = _ce_mtx_tri(t * speed + bx + k * 0.07);
            y = _ce_mtx_tri(t * speed * 1.3 + by + k * 0.11);
        } else if (mode == 5) {
            long long step = (long long)(t * (3.0 + bass * 7.0) + k);
            x = _ce_mtx_hash01(seed, step, 0LL);
            y = _ce_mtx_hash01(seed + 1LL, step, 0LL);
        } else if (mode == 6) {
            x = _ce_mtx_py_mod(bx + _ce_msin(t * 0.5 + k) * 0.25, 1.0);
            y = _ce_mtx_py_mod(by + _ce_mcos(t * 0.4 + k * 0.7) * 0.25, 1.0);
        } else if (mode == 7) {
            x = 0.5 + _ce_mcos(t * speed + k) * (0.10 + 0.35 * _ce_mtx_tri(t * 0.2 + bx));
            y = 0.5 + _ce_msin(t * speed + k) * (0.10 + 0.35 * _ce_mtx_tri(t * 0.2 + by));
        } else if (mode == 8) {
            x = _ce_mtx_py_mod(bx + t * speed, 1.0);
            y = _ce_mtx_py_mod(by + t * speed * 0.73, 1.0);
        } else if (mode == 9) {
            x = _ce_mtx_py_mod(bx + _ce_msin(t * speed + by * CE_MTX_TAU) * 0.35, 1.0);
            y = _ce_mtx_py_mod(by + _ce_msin(t * speed * 1.4 + bx * CE_MTX_TAU) * 0.35, 1.0);
        } else if (mode == 10) {
            x = 0.5 + _ce_mcos(t * speed + bx * CE_MTX_TAU) * (0.2 + high * 0.2);
            y = by;
        } else if (mode == 11) {
            x = bx;
            y = 0.5 + _ce_msin(t * speed + by * CE_MTX_TAU) * (0.2 + bass * 0.2);
        } else if (mode == 12) {
            x = _ce_mtx_tri(t * speed + bx);
            y = _ce_mtx_py_mod(by + _ce_msin(x * CE_MTX_TAU + t) * 0.12, 1.0);
        } else if (mode == 13) {
            y = _ce_mtx_tri(t * speed + by);
            x = _ce_mtx_py_mod(bx + _ce_mcos(y * CE_MTX_TAU - t) * 0.12, 1.0);
        } else if (mode == 14) {
            double ang = (double)k * CE_MTX_TAU / (double)((count > 1) ? count : 1) + t * speed;
            double rad = _ce_mtx_tri(t * speed * 0.7 + bx) * 0.48;
            x = 0.5 + _ce_mcos(ang) * rad;
            y = 0.5 + _ce_msin(ang) * rad;
        } else {
            x = _ce_mtx_py_mod(bx + _ce_msin(t * speed + k) * 0.2 + t * speed * 0.3, 1.0);
            y = _ce_mtx_py_mod(by + _ce_mcos(t * speed * 0.8 + k) * 0.2 + t * speed * 0.2, 1.0);
        }
        px[k] = _ce_mtx_clamp01(x);
        py[k] = _ce_mtx_clamp01(y);
        pb[k] = 0.55 + 0.45 * _ce_mtx_hash01(seed, 9LL, 0LL);
    }
    return 0;
}

/* ═══ 导出: ce_mtx_pattern ═══════════════════════════════════════════════
 * `_compute_pattern(engine, _mode_id, variant, family_offset)` 的整段计算体
 * （v40:251-286）。返回 0 = 成功写入 out[0..n); 非 0 = 契约拒绝（零写入,
 * Python 侧回退原版 `_compute_pattern`）。
 * 契约: n ∈ [0, CE_MTX_MAXN]; cols/rows >= 1; 全部标量 finite;
 *       variant/family_offset >= 0; col/row 为 n 元素 int32。 */
CE_API int ce_mtx_pattern(
    int n, const int32_t* col, const int32_t* row, int cols, int rows,
    double width, double twist, double mix, double t, double phase, double freq,
    double soft, double bass, double mid, double high, double rms,
    int variant, int family_offset, double* out)
{
    int i, style;
    double aspect;
    if (n < 0 || n > CE_MTX_MAXN)
        return -1;
    if (cols < 1 || rows < 1)
        return -1;
    if (variant < 0 || family_offset < 0)
        return -1;
    if (col == NULL || row == NULL || (out == NULL && n > 0))
        return -1;
    if (!_ce_mtx_is_finite(width) || !_ce_mtx_is_finite(twist)
            || !_ce_mtx_is_finite(mix) || !_ce_mtx_is_finite(t)
            || !_ce_mtx_is_finite(phase) || !_ce_mtx_is_finite(freq)
            || !_ce_mtx_is_finite(soft) || !_ce_mtx_is_finite(bass)
            || !_ce_mtx_is_finite(mid) || !_ce_mtx_is_finite(high)
            || !_ce_mtx_is_finite(rms))
        return -2;
    if (!_ce_mtx_i64_safe(t * 4.0) || !_ce_mtx_i64_safe(t * 8.0)
            || !_ce_mtx_i64_safe(phase * 8.0))
        return -2;      /* 哈希入参可安全截断（防御性; 超范围留 Python 原版）*/
    aspect = (double)cols / (double)rows;
    style = variant % 9;
    for (i = 0; i < n; i++) {
        ce_mtx_coord c;
        double metric, val;
        _ce_mtx_coords(col, row, i, cols, rows, aspect, twist, phase, &c);
        metric = _ce_mtx_metric(variant + family_offset, c.nx, c.ny, c.x, c.y,
                                c.angle, c.r, t, phase, mix, bass, mid, high,
                                variant);
        val = _ce_mtx_style_val(style, metric, freq, phase, width, soft, mix,
                                bass, mid, high, rms, t, c.nx, c.ny,
                                c.col, c.row, variant);
        /* 先算再查再写 —— NaN 一律拒绝（零写入, Python 回退原版承担异常语义）*/
        if (_ce_mtx_is_nan(val))
            return -2;
        out[i] = _ce_mtx_clamp01(val);
    }
    return 0;
}

/* ═══ 导出: ce_mtx_particle ═════════════════════════════════════════════=
 * `_compute_particle(engine, _mode_id, variant)` 的整段计算体（v40:354-384）
 * —— 全引擎最重段（1356 台/180 模式实测 29.9ms/帧, 逐设备×逐粒子 exp 核）。
 * 契约同上。 */
CE_API int ce_mtx_particle(
    int n, const int32_t* col, const int32_t* row, int cols, int rows,
    double density, double width, double twist, double mix, double t, double phase,
    double soft, double bass, double mid, double high, double rms,
    int beat, int variant, double* out)
{
    double aspect, count_d, gravity, sigma, denom;
    double px[CE_MTX_MAXPARTS], py[CE_MTX_MAXPARTS], pb[CE_MTX_MAXPARTS];
    int i, pidx, cnt;
    if (n < 0 || n > CE_MTX_MAXN)
        return -1;
    if (cols < 1 || rows < 1)
        return -1;
    if (variant < 0)
        return -1;
    if (col == NULL || row == NULL || (out == NULL && n > 0))
        return -1;
    if (!_ce_mtx_is_finite(density) || !_ce_mtx_is_finite(width)
            || !_ce_mtx_is_finite(twist) || !_ce_mtx_is_finite(mix)
            || !_ce_mtx_is_finite(t) || !_ce_mtx_is_finite(phase)
            || !_ce_mtx_is_finite(soft) || !_ce_mtx_is_finite(bass)
            || !_ce_mtx_is_finite(mid) || !_ce_mtx_is_finite(high)
            || !_ce_mtx_is_finite(rms))
        return -2;
    if (!_ce_mtx_i64_safe(t * 8.0))
        return -2;      /* 哈希入参可安全截断（防御性）*/
    /* count = max(3, min(72, int(5 + density*(4+variant%15) + rms*16))) */
    count_d = 5.0 + density * (double)(4 + variant % 15) + rms * 16.0;
    cnt = (int)count_d;
    if (cnt > 72)
        cnt = 72;
    if (cnt < 3)
        cnt = 3;
    aspect = (double)cols / (double)rows;
    _ce_mtx_particle_sources(variant, t + phase * 0.2, cnt, bass, mid, high,
                             px, py, pb);
    gravity = 0.12 * _ce_msin(t * 0.6 + variant);
    sigma = _ce_py_max(0.012, width * (0.16 + (double)(variant % 9) * 0.025
                                       + high * 0.08));
    denom = _ce_py_max(1.0, (double)cnt * 0.36);
    for (i = 0; i < n; i++) {
        ce_mtx_coord c;
        double val = 0.0;
        int absdx = (variant % 6 == 1 || variant % 6 == 4);
        int absdy = (variant % 7 == 2 || variant % 7 == 5);
        _ce_mtx_coords(col, row, i, cols, rows, aspect, twist, phase, &c);
        for (pidx = 0; pidx < cnt; pidx++) {
            double dx = c.nx - px[pidx];
            double dy = c.ny - py[pidx] - gravity * mix;
            double dist2, blob;
            if (absdx)
                dx = _ce_mfabs(dx);
            if (absdy)
                dy = _ce_mfabs(dy);
            dist2 = dx * dx + dy * dy;
            blob = _ce_mexp(-dist2 / _ce_py_max(1e-6, sigma * sigma)) * pb[pidx];
            if (variant % 5 == 0)
                blob *= 0.65 + 0.35 * _ce_mtx_tri(c.angle / CE_MTX_TAU
                                                  * (3 + variant % 7)
                                                  + pidx * 0.13);
            val += blob;
        }
        /* `val / max(1.0, count*0.36)` —— Python 是除法; 用乘倒数会差 ulp,
         * 必须真除（count*0.36 与 max 结果同式的 double）。 */
        val = val / denom;
        if (beat)
            val = _ce_py_max(val, _ce_mtx_band(c.r * (2.0 + mix * 3.0) - phase,
                                               width * 1.4, soft) * 0.55);
        val = _ce_mtx_clamp01(val * (1.35 + bass * 0.45));
        if (_ce_mtx_is_nan(val))
            return -2;
        out[i] = val;
    }
    return 0;
}
