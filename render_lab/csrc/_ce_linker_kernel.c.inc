/* ═══════════════════════════════════════════════════════════════════════════
 * 关联器（Track Linker）计算内核 —— ce_linker_preset_eval / ce_linker_rule
 *
 * 对齐 Python 原版: src/processing/track_linker_engine.py
 *   · _process_rule        :2419  （每 25ms tick × 每条规则的热点）
 *   · _write_dst           :2560  （低值覆盖 / 16bit 展开 / 泵无基础值不开）
 *   · _read_src            :2528  （源值读取 + 电机停放归一 + 16bit 合成）
 *   · _is_active           :2511  （活跃掩码 + fine 回退）
 *   · _rule_motors_parked  :2473  （停放掩码 16bit 感知）
 *   · _color_whiten_writes :214  / _light_track_writes :249（两个整组写分支）
 *   · _xy_result_for_refs  :2692  / _write_multi_dst_xy_by_fixture :2705
 *   · preset_def.fn 全族   :380-704（37 个预设数值映射）
 *
 * 为什么需要它（2026-09-18 实测归因, render_lab/bench/probe_linker_tick_profile.py）:
 *   40 台设备 / 120 条规则 / 3 域场景: tick 9.07ms, 其中 _process_rule 占
 *   82.1%（7.45ms）, _get_snapshot 9.3%, 写回 NM 9.3%。_process_rule 内部
 *   886 次/tick 的 np.clip 标量调用（cProfile tottime 首位）+ dict 查找 +
 *   逐通道闭包调用占绝对多数。本内核把「源值数组 + 规则参数 → 绑定决策 +
 *   写入计划」整段搬到 C。
 *
 * ── 位级等价纪律（与既有 CE 内核同标准, 逐条实证）──────────────────────
 *  1. 全程 f64（Python float = C double）; 本编译单元已 `#pragma
 *     fp_contract(off)`（ce_engine.c:84）→ /O2 不做 FMA 收缩。
 *  2. 超越函数（cos/sin/exp/sqrt/pow/fabs）**运行时从 ucrtbase.dll 按名
 *     解析** —— CPython 在 Windows 链接的就是 ucrtbase, 同一函数同一实现
 *     ⇒ 构造上逐位相等。实证: 40 万随机样本 cos/sin/exp/sqrt/fabs/pow 全
 *     0 失配（与既有 _ce_pow_ucrt 同款理由: /MT 静态 CRT 的 pow(x,2.0) 实
 *     为 x*x, 差 1 ulp）。
 *  3. Python max/min 语义用既有 _ce_py_max/_ce_py_min 三元复刻（±0.0 平局
 *     返回第二参）; `abs()` → 动态 fabs; `x ** n` → 动态 pow（CPython
 *     float_pow 同导出）。
 *  4. 取整: `int(np.clip(v,0,255))` = Python int() 向零截断; NaN 一律**拒绝**
 *     （返回 -2 且零写入）→ Python 侧回退原版, 由原版承担 int(nan) 异常语义。
 *  5. `sum(vals)/len(vals)` 用 0.0 起左结合累加（Python sum 语义）。
 *  6. 16bit 读合成 `((coarse<<8)+fine)/256.0`; 写拆分 `int(round(logical*
 *     256.0))`（半偶, rint）逐位对齐 xy_reference._read_axis_logical /
 *     _logical_to_16bit_pair。
 *  7. 低值覆盖决策树次序与 Python 逐分支一致（含"泵无基础值不开启"与
 *     "电机停放不遮罩"两条 2026-08-11 / 09-12 修复语义）。
 *  8. 两遍式: pass1 只读（算决策 + NaN/契约校验）, 只写局部计划数组;
 *     pass2 才提交（pend 就地更新 + 输出）→ 任一非法输入零副作用拒绝。
 *
 * ── 契约边界（Python 侧 _hook_linker 事前判定, 任一不满足 → 不调用本内核）
 *  · `_has_timing == False`（stagger/spray/spacing 全默认; 时序分支有
 *    _rule_activation_state 跨 tick 状态, 留 Python）
 *  · 预设 id 在已知表内且 preset_def 存在
 *  · 全部 src/dst 的 dmx_addr / fine 地址 ∈ [0,512)（出界语义留 Python）
 *  · universe 可 int() 且 round-trip（保证表索引与 dict 键一一对应）
 *  · 同一规则内 dst 的 (universe,addr) 与 (universe,fine) 两两不重复
 *    （重复时同规则先后写有依赖, 留 Python）
 *  · 恰有一张掩码表（snap/motion/act/motact/parked/pend/pend_has）长度
 *    ≥ n_uni, 每个元素为 NULL 或指向 ≥512 字节缓冲
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ── 类型码（Python 侧 _CE_LNK_TYPE_CODE 表同源）──────────────────────── */
enum {
    CE_LT_PLAIN = 0,
    CE_LT_MOTOR_X = 1, CE_LT_MOTOR_Y = 2, CE_LT_MOTOR_Z = 3,
    CE_LT_MOTOR_XF = 4, CE_LT_MOTOR_YF = 5, CE_LT_MOTOR_ZF = 6,
    CE_LT_PUMP = 7,
    CE_LT_DIMMER = 8,
    CE_LT_R = 9, CE_LT_G = 10, CE_LT_B = 11, CE_LT_W = 12,
    CE_LT_MAX = 13
};

/* 模式码（Python 侧 _CE_LNK_MODE_* 同源） */
enum { CE_LM_BROADCAST = 0, CE_LM_PAIRED = 1, CE_LM_XY_MULTI = 2,
       CE_LM_WHITEN = 3, CE_LM_LIGHT = 4 };

/* 预设数值内核编号（Python 侧 _CE_LNK_KIND 表同源） */
enum {
    CE_LK_MOTOR_PUMP_COSINE = 0,
    CE_LK_MOTOR_PUMP_LINEAR = 1,
    CE_LK_MOTOR_PUMP_STEP = 2,
    CE_LK_MOTOR_PUMP_GAUSSIAN = 3,
    CE_LK_MOTOR_PUMP_BIDIR_POS = 4,
    CE_LK_MOTOR_PUMP_BIDIR_NEG = 5,
    CE_LK_MOTOR_XY_PUMP = 6,
    CE_LK_MOTOR_VALVE_LINEAR = 7,
    CE_LK_MOTOR_VALVE_COSINE = 8,
    CE_LK_MOTOR_COLOR_WHITEN = 9,
    CE_LK_PUMP_DIMMER_LINEAR = 10,
    CE_LK_PUMP_DIMMER_SQRT = 11,
    CE_LK_PUMP_DIMMER_POWER = 12,
    CE_LK_MOTOR_COLOR_HUE = 13,
    CE_LK_INTENSITY_PUMP_LINEAR = 14,
    CE_LK_INTENSITY_PUMP_THRESHOLD = 15,
    CE_LK_CHANNEL_LINEAR = 16,
    CE_LK_INVERT = 17,
    CE_LK_CLAMP_RANGE = 18,
    CE_LK_SINE_MODULATE = 19,
    CE_LK_VARIO_PUMP_LINEAR = 20,
    CE_LK_VARIO_PUMP_CURVE = 21,
    CE_LK_CENTER_SCALE_MAP = 22,
    CE_LK_MIRROR_MAP = 23,
    CE_LK_DEVIATION_MAP = 24,
    CE_LK_HOME_DRIVE = 25,
    CE_LK_THRESHOLD_MAP = 26,
    CE_LK_PUMP_LIGHT_TRACK = 27,
    CE_LK_PUMP_LIGHT_TRACK_LINEAR = 28,
    CE_LK_PUMP_LIGHT_TRACK_STEP = 29,
    CE_LK_PUMP_LIGHT_TRACK_PULSE = 30,
    CE_LK_KIND_MAX = 31
};

/* 参数槽（Python 侧 _CE_LNK_PARAM_SLOT 表同源, 逐 key 一个定槽; 缺省由
 * Python 按预设 params_def / fn 内 .get(key, default) 的默认值补齐）。
 * 尾槽补 0.0——**只读本表列出的槽**, 其余槽不参与任何运算。 */
#define CE_LNK_NPARAM 40
#define CE_LP_CENTER      0   /* center / center_x */
#define CE_LP_CENTER_X    1
#define CE_LP_CENTER_Y    2
#define CE_LP_CUTOFF      3
#define CE_LP_MAX_PUMP    4
#define CE_LP_SIGMA       5
#define CE_LP_SCALE       6
#define CE_LP_BASE        7
#define CE_LP_INVERT      8
#define CE_LP_SPAN        9
#define CE_LP_WHITE      10
#define CE_LP_SRC_MIN    11
#define CE_LP_SRC_MAX    12
#define CE_LP_PUMP_MAX   13
#define CE_LP_PUMP_MIN   14
#define CE_LP_GAMMA      15
#define CE_LP_PHASE      16
#define CE_LP_LO         17
#define CE_LP_HI         18
#define CE_LP_FREQ       19
#define CE_LP_DEPTH      20
#define CE_LP_THRESHOLD  21
#define CE_LP_MAX_VAL    22
#define CE_LP_MAXV       23   /* "max"（deviation_map / threshold_map） */
#define CE_LP_SMOOTH     24
#define CE_LP_GAIN       25
#define CE_LP_MAX_SPEED  26
#define CE_LP_WIDTH      27
#define CE_LP_DIMMER     28   /* 灯跟踪目标参数（_LIGHT_TRACK_CH_MAP） */
#define CE_LP_R          29
#define CE_LP_G          30
#define CE_LP_B          31
#define CE_LP_W          32

/* 单规则元素上限（超出则由 Python 侧拒绝并回退原版） */
#define CE_LNK_MAXN 2048

#define CE_LNK_PI 3.14159265358979323846

/* ── 超越函数: 动态解析 ucrtbase（CPython 同一实现 ⇒ 逐位相等）──────── */
typedef double (__cdecl *ce_lnk_math1)(double);
typedef double (__cdecl *ce_lnk_math2)(double, double);

static ce_lnk_math1 volatile _ce_lnk_cos = NULL;
static ce_lnk_math1 volatile _ce_lnk_sin = NULL;
static ce_lnk_math1 volatile _ce_lnk_exp = NULL;
static ce_lnk_math1 volatile _ce_lnk_sqrt = NULL;
static ce_lnk_math1 volatile _ce_lnk_fabs = NULL;
static ce_lnk_math2 volatile _ce_lnk_pow = NULL;

static void* _ce_lnk_sym(const char* name)
{
    HMODULE m = GetModuleHandleA("ucrtbase.dll");
    if (m == NULL)
        m = LoadLibraryA("ucrtbase.dll");
    if (m == NULL)
        return NULL;
    return (void*)GetProcAddress(m, name);
}

static double _ce_lcos(double x)
{
    if (_ce_lnk_cos == NULL)
        _ce_lnk_cos = (ce_lnk_math1)_ce_lnk_sym("cos");
    return (_ce_lnk_cos != NULL) ? _ce_lnk_cos(x) : cos(x);
}

static double _ce_lsin(double x)
{
    if (_ce_lnk_sin == NULL)
        _ce_lnk_sin = (ce_lnk_math1)_ce_lnk_sym("sin");
    return (_ce_lnk_sin != NULL) ? _ce_lnk_sin(x) : sin(x);
}

static double _ce_lexp(double x)
{
    if (_ce_lnk_exp == NULL)
        _ce_lnk_exp = (ce_lnk_math1)_ce_lnk_sym("exp");
    return (_ce_lnk_exp != NULL) ? _ce_lnk_exp(x) : exp(x);
}

static double _ce_lsqrt(double x)
{
    if (_ce_lnk_sqrt == NULL)
        _ce_lnk_sqrt = (ce_lnk_math1)_ce_lnk_sym("sqrt");
    return (_ce_lnk_sqrt != NULL) ? _ce_lnk_sqrt(x) : sqrt(x);
}

static double _ce_lfabs(double x)
{
    if (_ce_lnk_fabs == NULL)
        _ce_lnk_fabs = (ce_lnk_math1)_ce_lnk_sym("fabs");
    return (_ce_lnk_fabs != NULL) ? _ce_lnk_fabs(x) : fabs(x);
}

/* CPython float_pow 同一导出（int 指数也走它: pow(x,2.0) ≠ x*x, 见
 * _ce_pow_ucrt 注释） */
static double _ce_lpow(double x, double y)
{
    if (_ce_lnk_pow == NULL)
        _ce_lnk_pow = (ce_lnk_math2)_ce_lnk_sym("pow");
    return (_ce_lnk_pow != NULL) ? _ce_lnk_pow(x, y) : pow(x, y);
}

/* math.radians: CPython `x * degToRad`, degToRad = Py_MATH_PI/180.0 常量
 * （位型 0x1.1df46a2529d39p-6, 实证与 math.pi/180.0 同） */
#define CE_LNK_DEG2RAD 0x1.1df46a2529d39p-6

static double _ce_labs(double x) { return (x < 0.0) ? -x : x; }

/* Python round(x): 银行家舍入; MSVC rint 默认 FE_TONEAREST（半偶）同 */
static double _ce_lround(double x) { return rint(x); }

static int _ce_lnk_is_nan(double x)
{
    unsigned long long u;
    memcpy(&u, &x, 8);
    return ((u & 0x7FF0000000000000ULL) == 0x7FF0000000000000ULL)
        && ((u & 0x000FFFFFFFFFFFFFULL) != 0ULL);
}

/* ── 类型谓词（与 src/core/constants.py 同源）──────────────────────── */
static int _ce_lt_motor_src(int t)
{
    return (t >= CE_LT_MOTOR_X && t <= CE_LT_MOTOR_ZF);
}

/* MOTOR_FINE_CHANNELS: 粗电机 → 有 fine 通道（16bit 合成读 + 展开写） */
static int _ce_lt_has_fine(int t)
{
    return (t >= CE_LT_MOTOR_X && t <= CE_LT_MOTOR_Z);
}

/* MOTOR_FINE_TO_COARSE 命中（dst 本身是 fine 通道）→ 只写粗地址 */
static int _ce_lt_is_fine_dst(int t)
{
    return (t >= CE_LT_MOTOR_XF && t <= CE_LT_MOTOR_ZF);
}

/* _LIGHT_TRACK_CH_MAP → 参数槽 (dimmer,R,G,B,W) = 0..4（未命中 → dimmer） */
static int _ce_lt_light_slot(int t)
{
    switch (t) {
    case CE_LT_R: return 1;
    case CE_LT_G: return 2;
    case CE_LT_B: return 3;
    case CE_LT_W: return 4;
    default:      return 0;
    }
}

/* MotorParkedReadEps = 1.0（_normalize_parked_motor_read :74） */
static double _ce_lnk_norm_parked(double val)
{
    return (val >= 0.0 && val < 1.0) ? 128.0 : val;
}

/* ── 16bit 读合成（xy_reference._read_axis_logical :356）──────────── */
static double _ce_lnk_read_axis(const uint8_t* data, int coarse, int fine)
{
    int c = (int)data[coarse];
    if (fine < 0)
        return (double)c;
    return (double)((c << 8) + (int)data[fine]) / 256.0;
}

/* ── 16bit 写拆分（xy_reference._logical_to_16bit_pair :364）──────── */
static void _ce_lnk_split16(double value, int* out_coarse, int* out_fine)
{
    double logical = _ce_py_max(0.0, _ce_py_min(255.0, value));
    double v16;
    if (logical >= 255.0)
        v16 = 65535.0;
    else
        v16 = _ce_lround(logical * 256.0);
    v16 = _ce_py_max(0.0, _ce_py_min(65535.0, v16));
    *out_coarse = (int)(((long long)v16 >> 8) & 0xFF);
    *out_fine = (int)((long long)v16 & 0xFF);
}

/* int(np.clip(v,0,255)): Python int() 向零截断 */
static int _ce_lnk_to_u8(double v)
{
    double c = _ce_py_max(0.0, _ce_py_min(255.0, v));
    return (int)c;
}

/* ── 活跃判定（track_linker_engine._is_active :2511）──────────────── */
static int _ce_lnk_is_active(int t, const uint8_t* const* tab, int u,
                             int a, int f)
{
    const uint8_t* mask;
    if (tab == NULL || u < 0)
        return 0;
    mask = tab[u];
    if (mask == NULL)
        return 0;
    if (a >= 0 && a < 512 && mask[a])
        return 1;
    if (_ce_lt_has_fine(t) && f >= 0 && f < 512)
        return mask[f] != 0;
    return 0;
}

/* ── 停放掩码（_motor_ref_parked :96 / _ref_parked_in_mask :85）────── */
static int _ce_lnk_parked1(const uint8_t* mask, int addr)
{
    if (mask == NULL || addr < 0 || addr >= 512)
        return 0;
    return mask[addr] != 0;
}

static int _ce_lnk_ref_parked(const uint8_t* mask, int coarse, int fine)
{
    if (_ce_lnk_parked1(mask, coarse))
        return 1;
    return _ce_lnk_parked1(mask, fine);
}

/* ── 二维偏离（_xy_deviation :308）: !has_aux → 一维 |src−center_x| ── */
static double _ce_lnk_xy_dev(int has_aux, double src, double aux,
                             double cx, double cy)
{
    double dx, dy;
    if (!has_aux)
        return _ce_labs(src - cx);
    dx = src - cx;
    dy = aux - cy;
    return _ce_lsqrt(dx * dx + dy * dy);
}

/* ═══════════════════════════════════════════════════════════════════════
 * 预设数值内核（preset_def.fn 全族逐位复刻; 37 个入口 → 31 个不同函数）
 * 参数槽约定见 CE_LNK_NPARAM 注释; 返回**未截断**原始值（同 Python fn）。
 * ═══════════════════════════════════════════════════════════════════════ */
static double _ce_lnk_eval1(int kind, const double* p, double src,
                            int has_aux, double aux)
{
    switch (kind) {
    case CE_LK_MOTOR_PUMP_COSINE: {
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = _ce_lnk_xy_dev(has_aux, src, aux,
                                    p[CE_LP_CENTER_X], p[CE_LP_CENTER_Y]);
        if (dev >= cutoff)
            return 0.0;
        return p[CE_LP_MAX_PUMP]
             * _ce_lpow(_ce_lcos(dev / cutoff * CE_LNK_PI / 2.0), 2.0);
    }
    case CE_LK_MOTOR_PUMP_LINEAR: {
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = _ce_lnk_xy_dev(has_aux, src, aux,
                                    p[CE_LP_CENTER_X], p[CE_LP_CENTER_Y]);
        if (dev >= cutoff)
            return 0.0;
        return p[CE_LP_MAX_PUMP] * (1.0 - dev / cutoff);
    }
    case CE_LK_MOTOR_PUMP_STEP: {
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = _ce_lnk_xy_dev(has_aux, src, aux,
                                    p[CE_LP_CENTER_X], p[CE_LP_CENTER_Y]);
        return (dev < cutoff) ? p[CE_LP_MAX_PUMP] : 0.0;
    }
    case CE_LK_MOTOR_PUMP_GAUSSIAN: {
        double sigma = _ce_py_max(1.0, p[CE_LP_SIGMA]);
        double dev = _ce_lnk_xy_dev(has_aux, src, aux,
                                    p[CE_LP_CENTER_X], p[CE_LP_CENTER_Y]);
        return p[CE_LP_MAX_PUMP]
             * _ce_lexp(-_ce_lpow(dev, 2.0) / (2.0 * _ce_lpow(sigma, 2.0)));
    }
    case CE_LK_MOTOR_PUMP_BIDIR_POS: {
        double center = p[CE_LP_CENTER];
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = src - center;
        if (dev <= 0.0)
            return 0.0;
        if (dev >= cutoff)
            return p[CE_LP_MAX_PUMP];
        return p[CE_LP_MAX_PUMP]
             * _ce_lpow(_ce_lcos((1.0 - dev / cutoff) * CE_LNK_PI / 2.0), 2.0);
    }
    case CE_LK_MOTOR_PUMP_BIDIR_NEG: {
        double center = p[CE_LP_CENTER];
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = center - src;
        if (dev <= 0.0)
            return 0.0;
        if (dev >= cutoff)
            return p[CE_LP_MAX_PUMP];
        return p[CE_LP_MAX_PUMP]
             * _ce_lpow(_ce_lcos((1.0 - dev / cutoff) * CE_LNK_PI / 2.0), 2.0);
    }
    case CE_LK_MOTOR_XY_PUMP: {
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        double dev = _ce_lnk_xy_dev(has_aux, src, aux,
                                    p[CE_LP_CENTER_X], p[CE_LP_CENTER_Y]);
        if (dev >= cutoff)
            return 0.0;
        return p[CE_LP_MAX_PUMP] * (1.0 - dev / cutoff);
    }
    case CE_LK_MOTOR_VALVE_LINEAR: {
        double result = p[CE_LP_BASE] + (src - p[CE_LP_CENTER]) * p[CE_LP_SCALE];
        if (p[CE_LP_INVERT] > 0.5)
            result = 255.0 - result;
        return result;
    }
    case CE_LK_MOTOR_VALVE_COSINE: {
        double dev = _ce_labs(src - p[CE_LP_CENTER]);
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        if (dev >= cutoff)
            return 0.0;
        return p[CE_LP_MAX_VAL]
             * _ce_lpow(_ce_lcos(dev / cutoff * CE_LNK_PI / 2.0), 2.0);
    }
    case CE_LK_MOTOR_COLOR_WHITEN: {
        double span = _ce_py_max(1.0, p[CE_LP_SPAN]);
        double t = _ce_py_max(0.0, _ce_py_min(
            1.0, _ce_labs(src - p[CE_LP_CENTER]) / span));
        return (p[CE_LP_INVERT] >= 0.5) ? (1.0 - t) : t;
    }
    case CE_LK_PUMP_DIMMER_LINEAR:
    case CE_LK_CHANNEL_LINEAR:
    case CE_LK_INTENSITY_PUMP_LINEAR:
        return src * p[CE_LP_SCALE] + p[CE_LP_BASE];
    case CE_LK_PUMP_DIMMER_SQRT:
        return _ce_lsqrt(_ce_py_max(0.0, src / 255.0)) * 255.0 * p[CE_LP_SCALE];
    case CE_LK_PUMP_DIMMER_POWER:
        return _ce_lpow(src / 255.0, p[CE_LP_GAMMA]) * 255.0;
    case CE_LK_MOTOR_COLOR_HUE: {
        double angle = (src - p[CE_LP_CENTER]) * p[CE_LP_SCALE] + p[CE_LP_PHASE];
        return (_ce_lsin(angle * CE_LNK_DEG2RAD) + 1.0) / 2.0 * 255.0;
    }
    case CE_LK_INTENSITY_PUMP_THRESHOLD: {
        double threshold = p[CE_LP_THRESHOLD];
        if (src < threshold)
            return 0.0;
        return (src - threshold) / _ce_py_max(1.0, 255.0 - threshold)
             * p[CE_LP_MAX_PUMP];
    }
    case CE_LK_INVERT:
        return 255.0 - src;
    case CE_LK_CLAMP_RANGE:
        return _ce_py_max(p[CE_LP_LO], _ce_py_min(p[CE_LP_HI], src));
    case CE_LK_SINE_MODULATE:
        return p[CE_LP_CENTER] + p[CE_LP_DEPTH]
             * _ce_lsin(src / 255.0 * CE_LNK_PI * 2.0 * p[CE_LP_FREQ]);
    case CE_LK_VARIO_PUMP_LINEAR:
    case CE_LK_VARIO_PUMP_CURVE: {
        double span = p[CE_LP_SRC_MAX] - p[CE_LP_SRC_MIN];
        double t, u, g;
        if (_ce_labs(span) < 1e-6)
            span = 1.0;
        t = _ce_py_max(0.0, _ce_py_min(1.0,
                        (src - p[CE_LP_SRC_MIN]) / span));
        u = (p[CE_LP_INVERT] >= 0.5) ? t : 1.0 - t;
        g = (kind == CE_LK_VARIO_PUMP_LINEAR) ? 1.0 : p[CE_LP_GAMMA];
        if (g > 0.0 && _ce_labs(g - 1.0) > 1e-9)
            u = _ce_lpow(u, g);
        return p[CE_LP_PUMP_MIN]
             + (p[CE_LP_PUMP_MAX] - p[CE_LP_PUMP_MIN]) * u;
    }
    case CE_LK_CENTER_SCALE_MAP:
        return p[CE_LP_CENTER] + (src - p[CE_LP_CENTER]) * p[CE_LP_SCALE];
    case CE_LK_MIRROR_MAP:
        return (p[CE_LP_CENTER] != 128.0)
             ? (255.0 - src) * p[CE_LP_CENTER] / 128.0
             : 255.0 - src;
    case CE_LK_DEVIATION_MAP: {
        double dev = _ce_labs(src - p[CE_LP_CENTER]);
        double cutoff = _ce_py_max(1.0, p[CE_LP_CUTOFF]);
        if (dev >= cutoff)
            return 0.0;
        return p[CE_LP_MAXV] * (1.0 - dev / cutoff);
    }
    case CE_LK_HOME_DRIVE: {
        if (src <= p[CE_LP_THRESHOLD])
            return p[CE_LP_CENTER];
        return p[CE_LP_CENTER] + p[CE_LP_MAX_SPEED] * _ce_py_min(
            1.0, (src - p[CE_LP_THRESHOLD])
                 / _ce_py_max(1.0, 255.0 - p[CE_LP_THRESHOLD]));
    }
    case CE_LK_THRESHOLD_MAP: {
        if (src <= p[CE_LP_THRESHOLD])
            return 0.0;
        return p[CE_LP_MAXV] * _ce_py_min(
            1.0, (src - p[CE_LP_THRESHOLD])
                 / _ce_py_max(1.0, 255.0 - p[CE_LP_THRESHOLD]));
    }
    case CE_LK_PUMP_LIGHT_TRACK: {
        double t;
        if (src <= p[CE_LP_THRESHOLD])
            return 0.0;
        if (p[CE_LP_SMOOTH] <= 0.0)
            return _ce_py_min(1.0, p[CE_LP_GAIN]);
        t = _ce_py_min(1.0, (src - p[CE_LP_THRESHOLD]) / p[CE_LP_SMOOTH]);
        t = t * t * (3.0 - 2.0 * t);
        return _ce_py_min(1.0, t * p[CE_LP_GAIN]);
    }
    case CE_LK_PUMP_LIGHT_TRACK_LINEAR: {
        double t;
        if (src <= p[CE_LP_THRESHOLD])
            return 0.0;
        t = _ce_py_min(1.0, (src - p[CE_LP_THRESHOLD])
                       / _ce_py_max(1.0, p[CE_LP_MAX_PUMP] - p[CE_LP_THRESHOLD]));
        return _ce_py_min(1.0, t * p[CE_LP_GAIN]);
    }
    case CE_LK_PUMP_LIGHT_TRACK_STEP:
        return (src > p[CE_LP_THRESHOLD]) ? _ce_py_min(1.0, p[CE_LP_GAIN]) : 0.0;
    case CE_LK_PUMP_LIGHT_TRACK_PULSE: {
        double dev = _ce_labs(src - p[CE_LP_CENTER]);
        double t;
        if (dev >= p[CE_LP_WIDTH])
            return 0.0;
        t = 1.0 - dev / p[CE_LP_WIDTH];
        t = t * t * (3.0 - 2.0 * t);
        return _ce_py_min(1.0, t * p[CE_LP_GAIN]);
    }
    default:
        return 0.0;
    }
}

/* 读 _aux_val 的内核（其余忽略 aux —— 与 Python 一致） */
static int _ce_lnk_uses_aux(int kind)
{
    switch (kind) {
    case CE_LK_MOTOR_PUMP_COSINE:  case CE_LK_MOTOR_PUMP_LINEAR:
    case CE_LK_MOTOR_PUMP_STEP:    case CE_LK_MOTOR_PUMP_GAUSSIAN:
    case CE_LK_MOTOR_XY_PUMP:
        return 1;
    default:
        return 0;
    }
}

/* is_motor_pump_rule = _is_motor_to_pump_preset(preset_id) && dst 含 pump */
static int _ce_lnk_is_motor_pump_kind(int kind)
{
    return (kind >= CE_LK_MOTOR_PUMP_COSINE && kind <= CE_LK_MOTOR_XY_PUMP);
}

CE_API int ce_linker_preset_eval(int kind, const double* params,
                                 int n, const double* src, const double* aux,
                                 double* out)
{
    int i;
    if (kind < 0 || kind >= CE_LK_KIND_MAX)
        return -1;
    if (params == NULL || src == NULL || out == NULL || n < 0)
        return -1;
    for (i = 0; i < n; i++) {
        int has_aux = 0;
        double a = 0.0;
        if (aux != NULL && _ce_lnk_uses_aux(kind)) {
            a = aux[i];
            has_aux = !_ce_lnk_is_nan(a);
        }
        out[i] = _ce_lnk_eval1(kind, params, src[i], has_aux, a);
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════════
 * ce_linker_rule —— 单条规则整段计算（读源 → 映射 → 低值覆盖 → 写入计划）
 *
 * 入参: kind/mode/params（CE_LNK_NPARAM 槽）; pair（仅 CE_LM_XY_MULTI, 每 dst
 *       一个 (x_src_idx<<16)|y_src_idx, -1 = 无配对）
 *       src_desc/dst_desc: 每项 4 个 int32 = {u, addr, type, fine}
 *       （u = 域在 snap/act/... 表中的索引, 由 Python 侧建立）
 * 出参: out_u/out_a/out_v/out_n —— 写入计划（表索引/地址/值）, 由 Python
 *       侧翻译回 universe 并累积进 writes 字典
 *
 * 返回: 0 成功; -1 契约/参数非法; -2 数值非法（NaN, 零副作用）;
 *       -3 容量不足（零副作用）。非 0 → Python 回退原版（逐字节不变）。
 * ═══════════════════════════════════════════════════════════════════════ */

typedef struct {
    int uni;
    int addr;
    int val;
    int fine_addr;   /* -1 = 无 */
    int fine_val;
} ce_lnk_plan;

/* 计划累积（同 (uni,addr) 后写覆盖前写 —— Python dict 语义） */
static int _ce_lnk_emit(ce_lnk_plan* plans, int* n_plan, int cap,
                        int u, int addr, int val, int fine_addr, int fine_val)
{
    int i;
    for (i = 0; i < *n_plan; i++) {
        if (plans[i].uni != u)
            continue;
        if (plans[i].addr == addr) {
            /* 同址后写覆盖前写（Python dict 语义）; 若本条带 fine, fine 一并
             * 接管（原计划无 fine 时新增, 有则覆盖）——Python 侧已保证同一
             * 规则内 (universe,addr) 与 (universe,fine) 两两不重叠, 故此处
             * 不存在交叉覆盖二义。 */
            plans[i].val = val;
            if (fine_addr >= 0) {
                plans[i].fine_addr = fine_addr;
                plans[i].fine_val = fine_val;
            }
            return 0;
        }
        if (plans[i].fine_addr >= 0 && plans[i].fine_addr == addr) {
            plans[i].fine_val = val;
            return 0;
        }
    }
    if (*n_plan >= cap)
        return -1;
    plans[*n_plan].uni = u;
    plans[*n_plan].addr = addr;
    plans[*n_plan].val = val;
    plans[*n_plan].fine_addr = fine_addr;
    plans[*n_plan].fine_val = fine_val;
    (*n_plan)++;
    return 0;
}

/* 待写值读取（_pending_dst_value :2567）: 返回 1 = 已有本 tick 写入 */
static int _ce_lnk_pending(const uint8_t* const* pend,
                           const uint8_t* const* pend_has,
                           int u, int addr, int fine_ok, int fine_addr,
                           double* out)
{
    const uint8_t* buf;
    const uint8_t* has;
    if (u < 0)
        return 0;
    buf = pend[u];
    has = pend_has[u];
    if (buf == NULL || has == NULL)
        return 0;
    if (addr >= 0 && addr < 512 && has[addr]) {
        *out = (double)buf[addr];
        return 1;
    }
    if (fine_ok && fine_addr >= 0 && fine_addr < 512 && has[fine_addr]) {
        *out = _ce_lnk_read_axis(buf, addr, fine_addr);
        return 1;
    }
    return 0;
}

/* 当前目标值（_current_dst_value :2587） */
static double _ce_lnk_current(const uint8_t* const* snap,
                              const uint8_t* const* pend,
                              const uint8_t* const* pend_has,
                              int u, int a, int t, int fine_addr, double dflt)
{
    double pending;
    const uint8_t* uni_data;
    if (_ce_lnk_pending(pend, pend_has, u, a, _ce_lt_has_fine(t),
                        fine_addr, &pending))
        return pending;
    uni_data = (u >= 0) ? snap[u] : NULL;
    if (uni_data == NULL)
        return dflt;
    if (_ce_lt_has_fine(t))
        return _ce_lnk_read_axis(uni_data, a, fine_addr);
    return (double)uni_data[a];
}

/* 停放判定（_rule_motors_parked :2473）: 全部电机源停放 → 1 */
static int _ce_lnk_motors_parked(int is_motor_pump, int n_src,
                                 const int32_t* src_desc,
                                 const uint8_t* const* parked)
{
    int i, found = 0;
    if (!is_motor_pump || parked == NULL)
        return 0;
    for (i = 0; i < n_src; i++) {
        int t = (int)src_desc[i * 4 + 2];
        int u, a, f;
        if (!_ce_lt_motor_src(t))
            continue;
        found = 1;
        u = (int)src_desc[i * 4 + 0];
        a = (int)src_desc[i * 4 + 1];
        f = (int)src_desc[i * 4 + 3];
        if (!_ce_lnk_ref_parked((u >= 0) ? parked[u] : NULL, a, f))
            return 0;
    }
    return found;
}

/* 源值读取（_read_src :2528）: 返回 1 = 有值 */
static int _ce_lnk_read_src(int t, int u, int a, int f,
                            const uint8_t* const* snap,
                            const uint8_t* const* motion,
                            const uint8_t* const* act,
                            const uint8_t* const* motact,
                            double* out)
{
    const uint8_t* uni_data = (u >= 0) ? snap[u] : NULL;
    double val;
    if (_ce_lt_motor_src(t)) {
        if (_ce_lnk_is_active(t, motact, u, a, f)) {
            if (u >= 0 && motion != NULL && motion[u] != NULL)
                uni_data = motion[u];
        } else if (!_ce_lnk_is_active(t, act, u, a, f)) {
            return 0;
        }
    } else if (!_ce_lnk_is_active(t, act, u, a, f)) {
        return 0;
    }
    if (uni_data == NULL || a < 0 || a >= 512)
        return 0;
    val = _ce_lt_has_fine(t) ? _ce_lnk_read_axis(uni_data, a, f)
                             : (double)uni_data[a];
    if (_ce_lt_motor_src(t))
        val = _ce_lnk_norm_parked(val);
    *out = val;
    return 1;
}

CE_API int ce_linker_rule(
    int kind, int mode, const double* params, const int32_t* pair,
    int n_src, const int32_t* src_desc,
    int n_dst, const int32_t* dst_desc,
    int n_uni,
    const uint8_t* const* snap, const uint8_t* const* motion,
    const uint8_t* const* act, const uint8_t* const* motact,
    const uint8_t* const* parked,
    uint8_t* const* pend, uint8_t* const* pend_has,
    int32_t* out_u, int32_t* out_a, int32_t* out_v, int* out_n, int out_cap)
{
    ce_lnk_plan plans[CE_LNK_MAXN];
    int n_plan = 0;
    int i;
    int is_motor_pump = 0;
    double src_vals[CE_LNK_MAXN];
    int src_ok[CE_LNK_MAXN];
    double res[CE_LNK_MAXN];
    int res_valid[CE_LNK_MAXN];   /* 1 = 本 dst 参与写入（PAIRED/XY_MULTI 的
                                   * `continue` 语义: 源无值/无配对 → 整条
                                   * dst 不写, 且不产生任何决策副作用） */
    int any_src = 0;

    if (kind < 0 || kind >= CE_LK_KIND_MAX)
        return -1;
    if (mode < 0 || mode > CE_LM_LIGHT)
        return -1;
    if (params == NULL || snap == NULL || act == NULL || motact == NULL
        || pend == NULL || pend_has == NULL || out_u == NULL || out_a == NULL
        || out_v == NULL || out_n == NULL)
        return -1;
    if (n_uni <= 0 || out_cap < 0 || n_src < 0 || n_dst < 0)
        return -1;
    if (n_src > CE_LNK_MAXN || n_dst > CE_LNK_MAXN)
        return -1;
    if ((n_src > 0 && src_desc == NULL) || (n_dst > 0 && dst_desc == NULL))
        return -1;
    if (mode == CE_LM_XY_MULTI && pair == NULL && n_dst > 0)
        return -1;
    *out_n = 0;
    for (i = 0; i < n_dst; i++)
        res_valid[i] = 0;
    if (n_dst == 0 || n_src == 0)
        return 0;

    /* 极性/类型合法性（越界即拒绝, 避免 C 侧 UB） */
    for (i = 0; i < n_src; i++) {
        int t = (int)src_desc[i * 4 + 2];
        int u = (int)src_desc[i * 4 + 0];
        if (t < 0 || t >= CE_LT_MAX || u < 0 || u >= n_uni)
            return -1;
    }
    for (i = 0; i < n_dst; i++) {
        int t = (int)dst_desc[i * 4 + 2];
        int u = (int)dst_desc[i * 4 + 0];
        if (t < 0 || t >= CE_LT_MAX || u < 0 || u >= n_uni)
            return -1;
    }

    if (_ce_lnk_is_motor_pump_kind(kind)) {
        for (i = 0; i < n_dst; i++)
            if ((int)dst_desc[i * 4 + 2] == CE_LT_PUMP)
                is_motor_pump = 1;
    }

    /* ── pass 1: 只读决策 ─────────────────────────────────────────── */
    if (mode == CE_LM_WHITEN) {
        /* _color_whiten_writes :214 —— 绝对写入（不经低值覆盖） */
        double cur_by_type[CE_LT_MAX];
        double sum = 0.0, prog, white;
        int nvals = 0;
        for (i = 0; i < CE_LT_MAX; i++)
            cur_by_type[i] = 0.0;
        for (i = 0; i < n_src; i++) {
            double v;
            if (_ce_lnk_read_src((int)src_desc[i * 4 + 2],
                                 (int)src_desc[i * 4 + 0],
                                 (int)src_desc[i * 4 + 1],
                                 (int)src_desc[i * 4 + 3],
                                 snap, motion, act, motact, &v)) {
                sum += v;
                nvals++;
            }
        }
        if (nvals == 0)
            return 0;
        prog = _ce_lnk_eval1(kind, params, sum / (double)nvals, 0, 0.0);
        if (_ce_lnk_is_nan(prog))
            return -2;
        prog = _ce_py_max(0.0, _ce_py_min(1.0, prog));
        /* ★ 2026-09-19 修（第三轮回访查出的 C 侧缺陷）：white 的参数槽是
         *   CE_LP_WHITE(=10)；旧代码写 params[8] 而 8 是 CE_LP_INVERT →
         *   用户设 white=255 时 C 侧读到的是 invert 值（0）→ 输出恒 0
         *   （实测：直调 DLL，white=255 → C 输出全 0 / Python 输出 255）。
         *   Python 侧此前已加守卫整批回退；本行修好后可摘守卫恢复满命中。 */
        white = _ce_py_max(0.0, _ce_py_min(255.0, params[CE_LP_WHITE]));
        /* cur: 按 dst 顺序取快照原值（同类型后写覆盖 —— Python dict 语义） */
        for (i = 0; i < n_dst; i++) {
            int u = (int)dst_desc[i * 4 + 0];
            int a = (int)dst_desc[i * 4 + 1];
            int t = (int)dst_desc[i * 4 + 2];
            const uint8_t* ud = (u >= 0) ? snap[u] : NULL;
            cur_by_type[t] = (ud != NULL && a >= 0 && a < 512)
                           ? (double)ud[a] : 0.0;
        }
        for (i = 0; i < n_dst; i++) {
            int u = (int)dst_desc[i * 4 + 0];
            int a = (int)dst_desc[i * 4 + 1];
            int t = (int)dst_desc[i * 4 + 2];
            double out_val = (t == CE_LT_DIMMER)
                           ? cur_by_type[t]
                           : cur_by_type[t] + (white - cur_by_type[t]) * prog;
            if (_ce_lnk_is_nan(out_val))
                return -2;
            if (_ce_lnk_emit(plans, &n_plan, CE_LNK_MAXN, u, a,
                             _ce_lnk_to_u8(out_val), -1, 0) != 0)
                return -3;
        }
        goto commit;
    }

    /* ── 源值收集 ────────────────────────────────────────────────── */
    for (i = 0; i < n_src; i++) {
        src_ok[i] = _ce_lnk_read_src((int)src_desc[i * 4 + 2],
                                     (int)src_desc[i * 4 + 0],
                                     (int)src_desc[i * 4 + 1],
                                     (int)src_desc[i * 4 + 3],
                                     snap, motion, act, motact, &src_vals[i]);
        if (src_ok[i])
            any_src = 1;
    }

    /* ── 目标结果 res[i]（Python: 各分支 result → 逐 dst 写）─────────
     * 分支次序严格对齐 :2742-2790: XY_MULTI(use_xy_vector) → PAIRED →
     * WHITEN → LIGHT → BROADCAST。注意 XY_MULTI「一条都没写」时 Python
     * 落到广播路径（use_xy_vector 为真 ⇒ 目标预设必为电机→泵 ⇒ 非
     * whiten/light）, 故此处 mode 改判后由下方独立的 `if` 链继续。 */
    if (mode == CE_LM_XY_MULTI) {
        /* _write_multi_dst_xy_by_fixture :2705 */
        int wrote_any = 0;
        for (i = 0; i < n_dst; i++)
            res[i] = 0.0;
        for (i = 0; i < n_dst; i++) {
            int code = (int)pair[i];
            int xi, yi, have_x = 0, have_y = 0;
            double x_val = 0.0, y_val = 0.0;
            if (code < 0)
                continue;
            xi = (code >> 16) & 0xFFFF;
            yi = code & 0xFFFF;
            /* 0xFFFF = 该轴缺失（Python pair.get("motor_x") → None） */
            if (xi != 0xFFFF) {
                if (xi >= n_src)
                    return -1;
                have_x = _ce_lnk_read_src((int)src_desc[xi * 4 + 2],
                                          (int)src_desc[xi * 4 + 0],
                                          (int)src_desc[xi * 4 + 1],
                                          (int)src_desc[xi * 4 + 3],
                                          snap, motion, act, motact, &x_val);
            }
            if (yi != 0xFFFF) {
                if (yi >= n_src)
                    return -1;
                have_y = _ce_lnk_read_src((int)src_desc[yi * 4 + 2],
                                          (int)src_desc[yi * 4 + 0],
                                          (int)src_desc[yi * 4 + 1],
                                          (int)src_desc[yi * 4 + 3],
                                          snap, motion, act, motact, &y_val);
            }
            if (!have_x && !have_y)
                continue;
            {
                double r = _ce_lnk_eval1(kind, params,
                                         have_x ? x_val : params[CE_LP_CENTER_X],
                                         1,
                                         have_y ? y_val : params[CE_LP_CENTER_Y]);
                if (_ce_lnk_is_nan(r))
                    return -2;
                res[i] = _ce_py_max(0.0, _ce_py_min(255.0, r));
            }
            res_valid[i] = 1;
            wrote_any = 1;
        }
        if (!wrote_any) {
            /* 一条都没写 → 落回广播路径（Python 原版行为） */
            mode = CE_LM_BROADCAST;
        }
    }
    if (mode == CE_LM_PAIRED) {
        /* 配对模式: N src == N dst, 逐对独立（无 _aux_val） */
        for (i = 0; i < n_dst; i++) {
            double r;
            if (!src_ok[i])
                continue;
            r = _ce_lnk_eval1(kind, params, src_vals[i], 0, 0.0);
            if (_ce_lnk_is_nan(r))
                return -2;
            res[i] = _ce_py_max(0.0, _ce_py_min(255.0, r));
            res_valid[i] = 1;
        }
    } else if (mode == CE_LM_LIGHT) {
        /* _light_track_writes :249 */
        double sum = 0.0, gain;
        int nvals = 0;
        for (i = 0; i < n_src; i++) {
            if (src_ok[i]) {
                sum += src_vals[i];
                nvals++;
            }
        }
        if (nvals == 0)
            return 0;
        gain = _ce_lnk_eval1(kind, params, sum / (double)nvals, 0, 0.0);
        if (_ce_lnk_is_nan(gain))
            return -2;
        gain = _ce_py_max(0.0, _ce_py_min(1.0, gain));
        for (i = 0; i < n_dst; i++) {
            int slot = _ce_lt_light_slot((int)dst_desc[i * 4 + 2]);
            double target = params[CE_LP_DIMMER + slot];
            double v = target * gain;
            res[i] = _ce_py_max(0.0, _ce_py_min(255.0, v));
            res_valid[i] = 1;
        }
    } else if (mode == CE_LM_BROADCAST) {
        /* 广播 / （XY 矢量）: 多源取平均 或 src[0]+aux=src[1] */
        int is_xy_vec = 0;
        double result;
        if (is_motor_pump && n_src >= 2) {
            int has_x = 0, has_y = 0;
            for (i = 0; i < n_src; i++) {
                int t = (int)src_desc[i * 4 + 2];
                if (t == CE_LT_MOTOR_X)
                    has_x = 1;
                if (t == CE_LT_MOTOR_Y)
                    has_y = 1;
            }
            is_xy_vec = (has_x && has_y);
        }
        if (!is_xy_vec) {
            int nv = 0, aux_seen = 0;
            double sum = 0.0, aux_val = 0.0;
            for (i = 0; i < n_src; i++) {
                if (!src_ok[i])
                    continue;
                sum += src_vals[i];
                nv++;
                if (nv == 2)
                    aux_val = src_vals[i];
            }
            if (nv == 0)
                return 0;
            result = sum / (double)nv;
            aux_seen = (nv >= 2);
            result = _ce_lnk_eval1(kind, params, result, aux_seen, aux_val);
        } else {
            double src_val, aux_val;
            int has_aux;
            if (!any_src)
                return 0;
            src_val = src_ok[0] ? src_vals[0] : params[CE_LP_CENTER_X];
            has_aux = (n_src >= 2);
            aux_val = (n_src >= 2 && src_ok[1])
                    ? src_vals[1] : params[CE_LP_CENTER_Y];
            result = _ce_lnk_eval1(kind, params, src_val, has_aux, aux_val);
        }
        if (_ce_lnk_is_nan(result))
            return -2;
        result = _ce_py_max(0.0, _ce_py_min(255.0, result));
        for (i = 0; i < n_dst; i++) {
            res[i] = result;
            res_valid[i] = 1;
        }
    }

    /* ── 低值覆盖写入决策（_write_dst :2560, 非时序分支）───────────── */
    for (i = 0; i < n_dst; i++) {
        int u = (int)dst_desc[i * 4 + 0];
        int a = (int)dst_desc[i * 4 + 1];
        int t = (int)dst_desc[i * 4 + 2];
        int f = (int)dst_desc[i * 4 + 3];
        double val;
        int has_pending;
        double pending;

        if (!res_valid[i])
            continue;               /* Python `continue`: 无源值/无配对 */
        if (_ce_lt_is_fine_dst(t)) {
            /* dst 本身是 fine 通道 → 只写粗地址, 直接截断 */
            if (_ce_lnk_emit(plans, &n_plan, CE_LNK_MAXN, u, a,
                             _ce_lnk_to_u8(res[i]), -1, 0) != 0)
                return -3;
            continue;
        }
        has_pending = _ce_lnk_pending(pend, pend_has, u, a,
                                      _ce_lt_has_fine(t), f, &pending);
        if (!has_pending && !_ce_lnk_is_active(t, act, u, a, f)) {
            /* 目标无基础值: 泵不开启; 其它直接写结果 */
            if (t == CE_LT_PUMP)
                continue;
            val = _ce_py_max(0.0, _ce_py_min(255.0, res[i]));
        } else if (t == CE_LT_PUMP
                   && _ce_lnk_motors_parked(is_motor_pump, n_src,
                                            src_desc, parked)) {
            /* 电机全停放 → 写当前值（等价"不遮罩", 保留 linker 层占用） */
            val = _ce_lnk_current(snap, pend, pend_has, u, a, t, f, res[i]);
            val = _ce_py_max(0.0, _ce_py_min(255.0, val));
        } else {
            double cur = _ce_lnk_current(snap, pend, pend_has, u, a, t, f,
                                         255.0);
            val = _ce_py_min(cur, res[i]);
            val = _ce_py_max(0.0, _ce_py_min(255.0, val));
        }
        if (_ce_lnk_is_nan(val))
            return -2;
        if (_ce_lt_has_fine(t) && f >= 0 && f < 512) {
            int c8, f8;
            _ce_lnk_split16(val, &c8, &f8);
            if (_ce_lnk_emit(plans, &n_plan, CE_LNK_MAXN, u, a, c8, f, f8)
                    != 0)
                return -3;
        } else {
            if (_ce_lnk_emit(plans, &n_plan, CE_LNK_MAXN, u, a,
                             _ce_lnk_to_u8(val), -1, 0) != 0)
                return -3;
        }
    }

commit:
    /* ── pass 2: 提交（先整体校验容量 → 零副作用, 再就地更新 pend +
     * 输出计划; 与 Python dict `writes` 语义一致）────────────────── */
    {
        int need = n_plan;
        for (i = 0; i < n_plan; i++)
            if (plans[i].fine_addr >= 0)
                need++;
        if (need > out_cap)
            return -3;
    }
    for (i = 0; i < n_plan; i++) {
        int u = plans[i].uni;
        if (u >= 0 && u < n_uni && pend[u] != NULL && pend_has[u] != NULL) {
            pend[u][plans[i].addr] = (uint8_t)plans[i].val;
            pend_has[u][plans[i].addr] = 1;
            if (plans[i].fine_addr >= 0) {
                pend[u][plans[i].fine_addr] = (uint8_t)plans[i].fine_val;
                pend_has[u][plans[i].fine_addr] = 1;
            }
        }
        out_u[*out_n] = (int32_t)u;
        out_a[*out_n] = (int32_t)plans[i].addr;
        out_v[*out_n] = (int32_t)plans[i].val;
        (*out_n)++;
        if (plans[i].fine_addr >= 0) {
            out_u[*out_n] = (int32_t)u;
            out_a[*out_n] = (int32_t)plans[i].fine_addr;
            out_v[*out_n] = (int32_t)plans[i].fine_val;
            (*out_n)++;
        }
    }
    return 0;
}
