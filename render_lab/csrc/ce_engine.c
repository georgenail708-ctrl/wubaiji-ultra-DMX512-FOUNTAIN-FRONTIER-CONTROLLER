/*
 * Qascade CE (C Engine) — 隔离渲染引擎测试版
 * P0: 最小骨架 — 版本/CPU 能力探测（验证构建链与 ctypes 桥）
 * 后续: K0 帧合成 / K1 波形求值 / K2 全链路混合 / K3 GPU 离线烘焙
 *
 * 构建: render_lab/qascade_ce/build.py (MSVC cl.exe 双变体)
 * 位级等价纪律: SIMD 只按通道向量化, 绝不向量化单通道内循环
 */
#include <windows.h>
#include <intrin.h>
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#ifndef _OPENMP
/* 无 OpenMP 构建（MSVC 必带 /openmp, 本文件仅防误用）: 恒回退串行 */
static int omp_get_max_threads(void) { return 1; }
#endif

/* 阶段B: condvar 常驻线程池契约（实现在 ce_pool.c, §4.3 冻结签名） */
#include "ce_pool.h"

#define CE_API __declspec(dllexport)

/* ── 版本 ───────────────────────────────────────────────────────────── */

CE_API const char* ce_version(void)
{
    return "0.1.0-p0";
}

/* ── CPU 能力探测 ───────────────────────────────────────────────────── */

/* AVX2 支持: CPUID.7.0.EBX bit5 + OSXSAVE(bit27 of ECX leaf1) + XGETBV
 * 两个变体 DLL 均编译本函数（__cpuid 是普通指令, SSE2 变体安全）。 */
CE_API int ce_cpu_has_avx2(void)
{
    int regs[4] = {0};
    __cpuid(regs, 0);
    if (regs[0] < 7)
        return 0;
    __cpuid(regs, 1);
    if (!(regs[2] & (1 << 27)))            /* OSXSAVE */
        return 0;
    __cpuid(regs, 7);
    if (!(regs[1] & (1 << 5)))             /* AVX2 */
        return 0;
    /* XGETBV: 确认 OS 已保存 AVX 状态 (XCR0.1|2) */
    unsigned long long xcr0 = _xgetbv(0);
    return (xcr0 & 0x6) == 0x6;
}

CE_API int ce_cpu_cores(void)
{
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return (int)si.dwNumberOfProcessors;
}

/* ── K0: 帧合成（对齐 src/engine/native/_render_kernels.pyx 的 _mix_channel）──
 *
 * 输入契约（与 Cython mix_clips_kernel 一致）:
 *   vals   f32 row-major (nlay,512)   — 各层已求值数据
 *   acts   u8  row-major (nlay,512)   — 0/1 层激活掩码
 *   anchors f32 row-major (nlay,512)  — 各层锚点
 *   mod_gain f32                      — 调制增益
 * 输出:
 *   out      u8[512]   — 混合结果
 *   act_any  u8[512]   — 任意层激活位
 *
 * 位级等价纪律（与 Cython 语义逐行一致）:
 *   1. 每通道内 nlay 累加严格同序（float32 串行），通道间独立
 *   2. 非 active 层 d=0.0 也参与 sum_delta 与 primary 竞争（严格 > 比较）
 *   3. primary 取 |d| 最大层（NaN 永不竞争成功——fabs(NaN)>x 恒假，同 Cython）
 *   4. clamp[0,255] 之后 isnan→0，最后截断 cast
 *   5. /fp:precise 禁 FMA 融合（与 Cython 同编译器同选项）
 */
#pragma fp_contract(off)

CE_API void ce_mix_clips(const float* vals, const uint8_t* acts,
                         const float* anchors, int nlay, float mod_gain,
                         uint8_t* out, uint8_t* act_any)
{
    for (int c = 0; c < 512; c++) {
        int pidx = -1, any_a = 0;
        float best_abs = -1.0f, sum_delta = 0.0f, primary_delta, res;
        float sum_active = 0.0f, cnt_active = 0.0f;
        for (int l = 0; l < nlay; l++) {
            const float v = vals[(size_t)l * 512 + c];
            float d;
            if (acts[(size_t)l * 512 + c]) {
                any_a = 1;
                d = v - anchors[(size_t)l * 512 + c];
                sum_active += v;
                cnt_active += 1.0f;
            } else {
                d = 0.0f;
            }
            sum_delta += d;
            if (fabsf(d) > best_abs) {
                best_abs = fabsf(d);
                pidx = l;
            }
        }
        act_any[c] = (uint8_t)any_a;
        if (pidx >= 0 && acts[(size_t)pidx * 512 + c]) {
            primary_delta = vals[(size_t)pidx * 512 + c]
                          - anchors[(size_t)pidx * 512 + c];
            res = vals[(size_t)pidx * 512 + c]
                + (sum_delta - primary_delta) * mod_gain;
        } else if (any_a) {
            res = (cnt_active > 0.0f) ? (sum_active / cnt_active) : 0.0f;
        } else {
            res = 0.0f;
        }
        if (res < 0.0f)
            res = 0.0f;
        else if (res > 255.0f)
            res = 255.0f;
        if (isnan(res))
            res = 0.0f;
        out[c] = (uint8_t)res;
    }
}

/* ── P2: AVX2 8-lane SIMD 变体（仅在 avx2 DLL 内编译, build.py -DCE_AVX2）
 *
 * 位级等价关键: 每个 SIMD lane 负责一个独立通道, 通道内 nlay 累加保持串行同序
 * （与标量版/Cython 完全一致）；8 通道并行仅消除通道间的串行, 不改变任何
 * 单通道的浮点运算顺序 → 输出逐位相同。
 * fabs 用清符号位（NaN 保持 NaN, 比较恒假——与 Cython fabs 语义一致）。
 * 注: 内核内不并行（OpenMP 在每 universe 一次调用的小粒度下为负优化——
 *     实测 311 域逐次调用 17-20ms vs 标量 8.3ms），并行在 ce_mix_clips_batch
 *     按 universe 粒度做（K2 融合接口）。
 */
#ifdef CE_AVX2
#include <immintrin.h>

CE_API void ce_mix_clips_avx2(const float* vals, const uint8_t* acts,
                              const float* anchors, int nlay, float mod_gain,
                              uint8_t* out, uint8_t* act_any)
{
    const __m256 ones = _mm256_set1_ps(1.0f);
    const __m256 zeros = _mm256_setzero_ps();
    const __m256 mone = _mm256_set1_ps(-1.0f);
    const __m256 lo = _mm256_setzero_ps();
    const __m256 hi = _mm256_set1_ps(255.0f);
    const __m256 signmask = _mm256_set1_ps(-0.0f);
    const __m256i neg_one = _mm256_set1_epi32(-1);

    for (int c0 = 0; c0 < 512; c0 += 8) {
        __m256 sum_delta = zeros, best_abs = mone;
        __m256 sum_active = zeros, cnt_active = zeros;
        __m256i pidx = neg_one, any_a = _mm256_setzero_si256();

        for (int l = 0; l < nlay; l++) {
            const float* vp = vals + (size_t)l * 512 + c0;
            const uint8_t* ap = acts + (size_t)l * 512 + c0;
            const float* np = anchors + (size_t)l * 512 + c0;

            __m256 v = _mm256_loadu_ps(vp);
            __m256 d = _mm256_sub_ps(v, _mm256_loadu_ps(np));
            /* 8 字节 acts → 8×uint32 mask（0xFFFFFFFF/0） */
            __m128i b8 = _mm_loadl_epi64((const __m128i*)ap);
            __m256i act32 = _mm256_cvtepu8_epi32(b8);
            __m256i maski = _mm256_andnot_si256(_mm256_cmpeq_epi32(act32, _mm256_set1_epi32(0)), _mm256_set1_epi32(-1));
            __m256 mask = _mm256_castsi256_ps(maski);

            any_a = _mm256_or_si256(any_a, maski);
            d = _mm256_and_ps(d, mask);               /* 非 active 层 d=0（同标量） */
            sum_delta = _mm256_add_ps(sum_delta, d);
            sum_active = _mm256_add_ps(sum_active, _mm256_and_ps(v, mask));
            cnt_active = _mm256_add_ps(cnt_active, _mm256_and_ps(ones, mask));

            /* |d| = 清符号位；严格 > 比较（NaN 恒假，同 Cython） */
            __m256 ad = _mm256_andnot_ps(signmask, d);
            __m256 gt = _mm256_cmp_ps(ad, best_abs, _CMP_GT_OQ);
            best_abs = _mm256_blendv_ps(best_abs, ad, gt);
            pidx = _mm256_castps_si256(
                _mm256_blendv_ps(_mm256_castsi256_ps(pidx),
                                 _mm256_castsi256_ps(_mm256_set1_epi32(l)), gt));
        }

        /* 每通道标量收尾（提取值 = 同序累加结果, 与标量版逐位相同） */
        float sdl[8], sa[8], cnt[8];
        int pidx_l[8], any_l[8];
        _mm256_storeu_ps(sdl, sum_delta);
        _mm256_storeu_ps(sa, sum_active);
        _mm256_storeu_ps(cnt, cnt_active);
        _mm256_storeu_si256((__m256i*)pidx_l, pidx);
        _mm256_storeu_si256((__m256i*)any_l, any_a);

        for (int j = 0; j < 8; j++) {
            int c = c0 + j;
            float res;
            if (pidx_l[j] >= 0 && acts[(size_t)pidx_l[j] * 512 + c]) {
                float pd = vals[(size_t)pidx_l[j] * 512 + c]
                         - anchors[(size_t)pidx_l[j] * 512 + c];
                res = vals[(size_t)pidx_l[j] * 512 + c]
                    + (sdl[j] - pd) * mod_gain;
            } else if (any_l[j]) {
                res = (cnt[j] > 0.0f) ? (sa[j] / cnt[j]) : 0.0f;
            } else {
                res = 0.0f;
            }
            if (res < 0.0f)
                res = 0.0f;
            else if (res > 255.0f)
                res = 255.0f;
            if (isnan(res))
                res = 0.0f;
            out[c] = (uint8_t)res;
            act_any[c] = (uint8_t)(any_l[j] != 0);   /* 0xFFFFFFFF → 1 归一化 */
        }
    }
}
#endif /* CE_AVX2 */

/* ── P2: 批量帧合成（K2 融合核心接口）───────────────────────────────────
 *
 * 一次调用处理 N 个 universe（各自独立 nlay），OpenMP 按 universe 并行——
 * 粒度大（311 域），线程开销摊销；每 universe 语义与 ce_mix_clips 完全一致
 * （逐位等价）。两个变体 DLL 均导出（内部自动选标量/AVX2）。
 *
 * 输入: vals/acts/anchors 为指针数组（每 universe 一个 (nlay,512) 连续块），
 *       nlays 为每 universe 层数数组。
 * 输出: out/act_any 为 n_univ×512 连续块（row-major）。
 */
/* ── P4b: 帧准备大融合（无方向 fade 简路径, 对齐 _render_position）──────
 *
 * ce_frame_index（本文件内部辅助, 等价 _compiled_clip_frame_index(:4601)）:
 *   - count<=0 → -1; count==1 → 0; source_t=max(0,·)
 *   - lower_bound（numpy searchsorted side=left）→ 最近帧（含边界取 idx）
 * P4a（ce_frame_index/ce_change_scores 独立 Python 绑定）已删除: 负收益实证
 * （ctypes 10µs×292 次/帧 > C 收益, README iter#4）, 从未安装; ce_change_scores
 * 内核随之删除（无内部调用者）。
 * 位级等价: 全程 double 比较/运算（与 numpy float64 语义一致）
 */
#include <math.h>

CE_API int ce_frame_index(const double* times, int count, double source_t)
{
    if (count <= 0)
        return -1;
    if (count == 1)
        return 0;
    if (source_t < 0.0)
        source_t = 0.0;
    /* numpy searchsorted(times, source_t, side="left") = lower_bound */
    int lo = 0, hi = count;
    while (lo < hi) {
        int mid = lo + (hi - lo) / 2;
        if (times[mid] < source_t)
            lo = mid + 1;
        else
            hi = mid;
    }
    int idx = lo;
    if (idx <= 0)
        return 0;
    if (idx >= count)
        return count - 1;
    double prev_t = times[idx - 1], next_t = times[idx];
    return (source_t - prev_t < next_t - source_t) ? idx - 1 : idx;
}

/* ── P4b/d: 帧准备大融合（无方向 fade 简路径, 对齐 _render_position）──────
 *
 * 一次调用完成原 Python 链的 8+ 次 numpy 操作（ctypes 粒度教训）:
 *   frame_index 二分 → raw 取帧 → 合成（fade_mode 分支）→ clamp[0,255] → u8
 *   → active 掩码（base_active）→ LTP scores（last_change/rate + hold）
 * fade_mode: 0=直出 raw*gain; 1=fade_in prev+gain*(raw-prev); 2=fade_out
 *   target+gain*(raw-target)（与 playback_engine.py:3572-3574/:3568/:3571 同序
 *   float32 运算）。hold_extension 时 fade 被原版跳过 → mode 恒 0。
 * 位级等价: 全程 float32 同序（numpy NEP50: f32 数组×python float 保持 f32）；
 * scores 全程 double。返回 idx（<0 表示无帧）。
 */
CE_API int ce_render_clip_frame(
    const double* frame_times, int n_frames,
    const uint8_t* frame_data, const float* last_change,
    double source_t, double clip_start, double rate, double timeline_now,
    int hold_extension, float gain, const uint8_t* base_active,
    int fade_mode, const float* prev, const float* target,
    uint8_t* out, uint8_t* active_out, double* scores)
{
    int idx = ce_frame_index(frame_times, n_frames, source_t);
    if (idx < 0) {
        for (int c = 0; c < 512; c++) {
            scores[c] = -INFINITY;
            if (base_active[c])
                scores[c] = clip_start;
            active_out[c] = 0;
        }
        return -1;
    }
    const uint8_t* raw = frame_data + (size_t)idx * 512;
    const float* lc = (last_change != NULL) ? last_change + (size_t)idx * 512 : NULL;
    for (int c = 0; c < 512; c++) {
        float r = (float)raw[c];
        float v;
        /* M5: fade_mode=1/2 但对应 anchor 为 NULL（调用方 anchor 缺失）时
           按 fade_mode=0 处理（v = r*gain）——与 Python 侧 _render_one
           守卫一致（prev/target None → fade_mode 置 0）。 */
        if (fade_mode == 1 && prev != NULL) {
            v = prev[c] + gain * (r - prev[c]);
        } else if (fade_mode == 2 && target != NULL) {
            v = target[c] + gain * (r - target[c]);
        } else {
            v = r * gain;
        }
        if (v < 0.0f)
            v = 0.0f;
        else if (v > 255.0f)
            v = 255.0f;
        out[c] = (uint8_t)v;
        uint8_t a = base_active[c];
        active_out[c] = a;
        double s = (lc != NULL)
            ? clip_start + (double)lc[c] / rate
            : clip_start;
        if (hold_extension && s > timeline_now)
            s = timeline_now;
        scores[c] = a ? s : -INFINITY;
    }
    return idx;
}

/* ── R56e: K2 专用精化渲染（= ce_render_clip_frame + 精化 active 行）────
 * refined_row: 该任务本帧的精化 active 512B 行（调用方按 source_t 所在
 *   区间半场从 2n 交错表选行, 语义 = _clip_current_channel_active_mask 的
 *   base&((cur>0)|邻帧变化), 邻帧按掩码函数自身 last-≤ 索引）; NULL = 用
 *   base_active（旧行为; hold 行传 NULL, 同 Python hold_extension=True）。
 * 仅 K2 离线路径使用; 实时 P4b/batch 的 ce_render_clip_frame 保持原签名。 */
static int _ce_render_clip_frame_x(
    const uint8_t* frame_data, const float* last_change, int idx,
    double clip_start, double rate, double timeline_now,
    float gain, const uint8_t* base_active,
    int fade_mode, const float* prev, const float* target,
    const uint8_t* refined_row,
    uint8_t* out, uint8_t* active_out, double* scores)
{
    if (idx < 0) {
        for (int c = 0; c < 512; c++) {
            scores[c] = -INFINITY;
            if (base_active[c])
                scores[c] = clip_start;
            active_out[c] = 0;
        }
        return -1;
    }
    const uint8_t* raw = frame_data + (size_t)idx * 512;
    const float* lc = (last_change != NULL) ? last_change + (size_t)idx * 512 : NULL;
    const uint8_t* act_src = (refined_row != NULL) ? refined_row : base_active;
    for (int c = 0; c < 512; c++) {
        float r = (float)raw[c];
        float v;
        if (fade_mode == 1 && prev != NULL) {
            v = prev[c] + gain * (r - prev[c]);
        } else if (fade_mode == 2 && target != NULL) {
            v = target[c] + gain * (r - target[c]);
        } else {
            v = r * gain;
        }
        if (v < 0.0f)
            v = 0.0f;
        else if (v > 255.0f)
            v = 255.0f;
        out[c] = (uint8_t)v;
        uint8_t a = act_src[c];
        active_out[c] = a;
        /* K2 路径 hold_extension 恒 0（hold 编码为 fade 全零行）→ 无
           timeline_now 钳制分支（实时变体 ce_render_clip_frame 才有）。 */
        double s = (lc != NULL)
            ? clip_start + (double)lc[c] / rate
            : clip_start;
        scores[c] = a ? s : -INFINITY;
    }
    return idx;
}

/* ── P5a-1: 过渡曲线（对齐 _clip_transition_gains :4098, 纯数学）────────
 * curve: 0=linear 1=easein(tt²) 2=easeout(1-(1-tt)²) 3=easeinout(smoothstep)
 *        4=bezier(0.3i²t+3it²+t³)
 * strategy: 0=none 1=immediate(1) 2=decelerate(sqrt,!fade_in)
 *           3=hold_decay(tt>=0.6→1 否则 tt/0.6, !fade_in)
 *           4=step_down(floor/ceil(4g)/4) 5=smooth_ease(六次)
 *           6=delay_then_cut(1, !fade_in)
 * 位级等价: 严格按 Python 运算顺序（左结合）做 f32 运算; sqrtf/floorf/ceilf
 * 为 IEEE 正确舍入（与 numpy 的 C 实现一致）。
 */
#include <math.h>

/* Library implementations live in the shared source-motion include below. */
static float _ce_motion_curve32(float t, int fade_in, int code, int strategy);
static double _ce_motion_curve64(double t, int fade_in, int code, int strategy);

/* 单通道过渡增益（ce_transition_gains 与 ce_parallel_fixture_gains 共用） */
static float _ce_transition_gain(float t, int fade_in, int curve, int strategy)
{
    if (curve >= 100 && curve < 140)
        return _ce_motion_curve32(t, fade_in, curve, strategy);
    float g;
    switch (curve) {
        case 1:  /* easein */
            g = t * t;
            break;
        case 2:  /* easeout */
            g = 1.0f - (1.0f - t) * (1.0f - t);
            break;
        case 3:  /* easeinout smoothstep */
            g = t * t * (3.0f - 2.0f * t);
            break;
        case 4: { /* bezier: 0.3*inv*inv*tt + 3.0*inv*tt*tt + tt*tt*tt */
            float inv = 1.0f - t;
            g = 0.3f * inv * inv * t + 3.0f * inv * t * t + t * t * t;
            break;
        }
        default:
            g = t;
        }
        switch (strategy) {
        case 1:
            g = 1.0f;
            break;
        case 2:
            if (!fade_in)
                g = sqrtf(g);
            break;
        case 3: { /* hold_decay: tt>=0.6→1 else clip(tt/0.6,0,1) */
            if (!fade_in) {
                if (t >= 0.6f) {
                    g = 1.0f;
                } else {
                    float q = t / 0.6f;
                    g = (q < 0.0f) ? 0.0f : (q > 1.0f ? 1.0f : q);
                }
            }
            break;
        }
        case 4: {
            float scaled = g * 4.0f;
            g = fade_in ? (floorf(scaled) / 4.0f) : (ceilf(scaled) / 4.0f);
            break;
        }
        case 5: {
            /* smooth_ease: g*g*g*(g*(g*6-15)+10) */
            float g2 = g * g * g;
            float inner = g * (g * 6.0f - 15.0f) + 10.0f;
            g = g2 * inner;
            break;
        }
        case 6:
            if (!fade_in)
                g = 1.0f;
            break;
        default:
            break;
        }
    if (g < 0.0f) g = 0.0f;
    else if (g > 1.0f) g = 1.0f;
    return g;
}

/* ── K7a: 标量 f64 过渡增益 — 对齐 Python _clip_transition_gain(scalar) ──
 * 注意: 这是 f64 标量路径(playback_engine.py:5133, 供 _clip_fade_gain 用),
 * 与 numpy f32 的 _clip_transition_gains(:4098) 是两条不同路径! 上述
 * _ce_transition_gain 只等价后者(f32); K7a 的 fade 走标量路径, 必须全 f64。
 * Python: progress 先 clamp [0,1]; 未启用过渡 → smoothstep; 否则 curve×strategy;
 * 终值 clamp [0,1]。curve/strategy 索引与 f32 版同表(0=linear/none)。
 */
static double _ce_transition_gain64(double t, int fade_in, int curve, int strategy)
{
    if (curve >= 100 && curve < 140)
        return _ce_motion_curve64(t, fade_in, curve, strategy);
    double g;
    switch (curve) {
        case 1:  /* easein */
            g = t * t;
            break;
        case 2:  /* easeout */
            g = 1.0 - (1.0 - t) * (1.0 - t);
            break;
        case 3:  /* easeinout smoothstep */
            g = t * t * (3.0 - 2.0 * t);
            break;
        case 4: { /* bezier: 0.3*inv*inv*tt + 3.0*inv*tt*tt; += tt*tt*tt */
            double inv = 1.0 - t;
            g = 0.3 * inv * inv * t + 3.0 * inv * t * t;
            g += t * t * t;
            break;
        }
        default:
            g = t;
        }
        switch (strategy) {
        case 1:
            g = 1.0;
            break;
        case 2:
            if (!fade_in)
                g = sqrt(g);
            break;
        case 3: /* hold_decay: tt>=0.6→1 否则 tt/0.6(用 t 非 gain; 终值 clamp) */
            if (!fade_in)
                g = (t >= 0.6) ? 1.0 : t / 0.6;
            break;
        case 4: {
            double scaled = g * 4.0;
            g = fade_in ? (floor(scaled) / 4.0) : (ceil(scaled) / 4.0);
            break;
        }
        case 5: { /* smooth_ease: g*g*g*(g*(g*6-15)+10) */
            double g2 = g * g * g;
            double inner = g * (g * 6.0 - 15.0) + 10.0;
            g = g2 * inner;
            break;
        }
        case 6:
            if (!fade_in)
                g = 1.0;
            break;
        default:
            break;
        }
    if (g < 0.0) g = 0.0;
    else if (g > 1.0) g = 1.0;
    return g;
}

/* ── K7a: 标量 fade 包络 — 对齐 _clip_fade_gain :5225(f64) ──────────────
 * dur/fi/fo 为 Python 侧预算值: dur=max(0, clip.duration), fi/fo=max(0, 原值);
 * 内部 clamp 到 duration(1e-6 阈值), 窗口阈值 1e-4 在调用方(与 :3756 同)。
 * te=0(transitions_optimized 空) → smoothstep(与 _clip_transition_gain 首行一致)。
 */
static double _ce_clip_fade_gain64(double dur, double fi, double fo,
                                   double local_t, int te,
                                   int curve_fi, int curve_fo,
                                   int strategy_fi, int strategy_fo)
{
    if (dur <= 0.0)
        return 1.0;
    double fi_c = (fi > dur) ? dur : fi;   /* clamp(0, dur); fi 已 ≥0 */
    double fo_c = (fo > dur) ? dur : fo;
    double gain = 1.0;
    if (fi_c > 1e-6 && local_t < fi_c) {
        double progress = local_t / fi_c;
        if (progress < 0.0) progress = 0.0;
        else if (progress > 1.0) progress = 1.0;
        gain = te ? _ce_transition_gain64(progress, 1, curve_fi, strategy_fi)
                  : progress * progress * (3.0 - 2.0 * progress);
    }
    if (fo_c > 1e-6 && local_t > dur - fo_c) {
        double progress = (dur - local_t) / fo_c;
        if (progress < 0.0) progress = 0.0;
        else if (progress > 1.0) progress = 1.0;
        double fog = te ? _ce_transition_gain64(progress, 0, curve_fo, strategy_fo)
                        : progress * progress * (3.0 - 2.0 * progress);
        if (fog < gain)
            gain = fog;
    }
    return gain;
}

CE_API void ce_transition_gains(const float* tt, int n, int fade_in,
                                int curve, int strategy, float* out)
{
    for (int i = 0; i < n; i++) {
        float t = tt[i];
        if (t < 0.0f) t = 0.0f;
        else if (t > 1.0f) t = 1.0f;
        out[i] = _ce_transition_gain(t, fade_in, curve, strategy);
    }
}

/* R166 device wing budget: call-local doubles, after channel-type fade scaling.
 * Keep the original delay-enabled branch and its f32 ramp intermediates. */
static void _ce_parallel_wing_fit(double duration, double* delay, double* fade)
{
    double duration_c = duration > 0.0 ? duration : 0.0;
    double total = *delay + *fade;
    if (duration_c <= 1e-4 || total <= duration_c + 1e-6)
        return;
    double ratio = duration_c / (total < 1e-9 ? 1e-9 : total);
    *delay = *delay * ratio;
    *fade = *fade * ratio;
}

/* ── P5a-2: 平行分组逐设备增益（对齐 _compute_parallel_fixture_gains :6515）──
 * 输入 pos_in/sizes_in[K]（fade_in 方向分组, 可 NULL; 成对传递）+ pos_out/
 * sizes_out[K]（fade_out 方向分组, 可 NULL）; 输出 gains[K]。
 * 标量全 f64（同原版 Python float）; 数组参与运算处按 NEP50 转 f32 同序,
 * 与 numpy 向量化逐位一致（M6 fuzz 对拍 ce_fuzz_gains.py, 含 1e-4 阈值边界）。
 * 守卫: pos 非 NULL 而 sizes NULL → 视为该方向未启用（防越界读, 同 hook 侧
 * _ce_gain_arrays_ok 回退语义）。
 */
CE_API void ce_parallel_fixture_gains(
    const float* pos_in, const float* sizes_in,
    const float* pos_out, const float* sizes_out, int K,
    double fade_in, double fade_out, double duration, double local_t,
    int is_fading_in, int is_fading_out,
    double seq_delay_in, double seq_delay_out,
    double fade_in_scale, double fade_out_scale,
    int curve, int strategy, float* gains)
{
    for (int i = 0; i < K; i++)
        gains[i] = 1.0f;
    double eff_in = fade_in * fade_in_scale;
    if (eff_in < 0.0) eff_in = 0.0;
    double eff_out = fade_out * fade_out_scale;
    if (eff_out < 0.0) eff_out = 0.0;
    double delay_in = seq_delay_in, delay_out = seq_delay_out;
    if (seq_delay_in > 1e-4)
        _ce_parallel_wing_fit(duration, &delay_in, &eff_in);
    if (seq_delay_out > 1e-4)
        _ce_parallel_wing_fit(duration, &delay_out, &eff_out);

    if (is_fading_in && pos_in != NULL && sizes_in != NULL
            && (eff_in > 1e-4 || seq_delay_in > 1e-4)) {
        for (int i = 0; i < K; i++) {
            float gpos = pos_in[i];
            float gsize = sizes_in[i];
            float t_start, win;
            double win64 = 0.0;
            if (seq_delay_in > 1e-4) {
                t_start = gpos * (float)delay_in;   /* NEP50: f64 标量转 f32 */
                /* win 为 Python float: np.maximum(win,1e-6)=np.float64 → tt 除法
                   在 f64 进行, 后转 f32（双舍入, 与原版逐位一致, M6 fuzz 实测） */
                win64 = (eff_in > 1e-6) ? eff_in : 1e-6;
                win = (float)win64;
            } else {
                win = (float)eff_in / (gsize > 1.0f ? gsize : 1.0f);
                float d = (float)eff_in - win;
                if (d < 0.0f) d = 0.0f;
                t_start = gpos * d;
            }
            float t_end = t_start + win;
            float tt;
            if (seq_delay_in > 1e-4) {
                double tt64 = (double)((float)local_t - t_start) / win64;
                if (tt64 < 0.0) tt64 = 0.0;
                else if (tt64 > 1.0) tt64 = 1.0;
                tt = (float)tt64;
            } else {
                float w = win;
                if (w < 1e-6f) w = 1e-6f;
                tt = ((float)local_t - t_start) / w;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
            }
            float g = _ce_transition_gain(tt, 1, curve, strategy);
            if ((float)local_t < t_start)
                g = 0.0f;
            else if ((float)local_t >= t_end)
                g = 1.0f;
            if (g < gains[i])
                gains[i] = g;
        }
    }

    if (is_fading_out && pos_out != NULL && sizes_out != NULL
            && (eff_out > 1e-4 || seq_delay_out > 1e-4)) {
        for (int i = 0; i < K; i++) {
            float gpos = pos_out[i];
            float gsize = sizes_out[i];
            float t_start, win;
            double win64 = 0.0;
            if (seq_delay_out > 1e-4) {
                double region = delay_out + eff_out;
                t_start = (float)(duration - region) + gpos * (float)delay_out;
                win64 = (eff_out > 1e-6) ? eff_out : 1e-6;
                win = (float)win64;
            } else {
                win = (float)eff_out / (gsize > 1.0f ? gsize : 1.0f);
                float d = (float)eff_out - win;
                if (d < 0.0f) d = 0.0f;
                t_start = (float)(duration - eff_out) + gpos * d;
            }
            float t_end = t_start + win;
            float tt;
            if (seq_delay_out > 1e-4) {
                double tt64 = (double)(t_end - (float)local_t) / win64;
                if (tt64 < 0.0) tt64 = 0.0;
                else if (tt64 > 1.0) tt64 = 1.0;
                tt = (float)tt64;
            } else {
                float w = win;
                if (w < 1e-6f) w = 1e-6f;
                tt = (t_end - (float)local_t) / w;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
            }
            float g = _ce_transition_gain(tt, 0, curve, strategy);
            if ((float)local_t >= t_end)
                g = 0.0f;
            else if ((float)local_t < t_start)
                g = 1.0f;
            if (g < gains[i])
                gains[i] = g;
        }
    }
}

/* ── P6: C 持久上下文 — 数据所有权在 C, Python 每帧只传 ID ────────────
 * 颠覆点: clip 渲染数据（frame_times/frame_data/last_change/base_active/
 * rule_active）注册进 C 持久缓冲; 每帧一次 ce_render_frame 调用,
 * 传 146×int ID + 标量数组（~1KB）, C 内 OpenMP 按任务并行——彻底消除
 * P5d 的逐任务指针数组打包开销与 Python 侧 numpy 分配。
 */
#include <stdlib.h>
#include <string.h>

typedef struct {
    int n_frames;
    double* frame_times;    /* 持久 */
    uint8_t* frame_data;    /* n*512 持久 */
    float* last_change;     /* n*512 持久, 可 NULL */
    uint8_t* base_active;   /* 512 持久 */
    int has_rule;
    uint8_t* rule_active;   /* 512 持久, has_rule 时有效 */
    float* prev;            /* 512 持久, fade_mode=1 anchor (P6b), 可 NULL */
    float* target;          /* 512 持久, fade_mode=2 anchor (P6b), 可 NULL */
    float* anchor;          /* 512 持久, A3: 混合锚点 —— 注册期由 frame_data +
                               base_active&rule 计算（= _ce_get_clip_anchor
                               同输入同输出, 内容跨帧稳定） */
    int in_use;             /* M2: 槽位复用 — 1=已注册, 0=已释放可回收 */
} ce_clip_entry;

/* A3: 全融合内核会话级 scratch（单次 malloc 整块, 成长复用; 单提交者 =
 * 渲染主线程持锁调用; 与 P8 池独立不共用） */
typedef struct {
    uint8_t* out;      /* n*512   任务序渲染输出（ce_render_clip_frame 行） */
    uint8_t* act;      /* n*512   任务序激活 */
    double*  sc;       /* n*512   任务序 scores */
    uint8_t* eff;      /* n*512   组序 effective（全行, 含 count==0） */
    uint8_t* de;       /* n*512   组序 data_eff */
    uint8_t* lde;      /* n*512   压实层数据（count>0 行, 组内序） */
    uint8_t* lef;      /* n*512   压实层掩码 */
    float*   lan;      /* n*512   压实层锚点 */
    uint8_t* skip;     /* n       任务跳过标记（渲染 idx<0 / 槽无效） */
    int* cnts;         /* n       组序每行有效计数 */
    int* flat;         /* n       组序 flat_idx（任务行号） */
    int* gcnt;         /* n       每组行数 */
    int* gpos;         /* n       组填充游标 */
    int* guniv;        /* n       组 → universe */
    int* goff;         /* n+1     组前缀和 */
    double* ties;      /* n       组序 tie */
    const uint8_t** rules;  /* n   组序规则指针（NULL = 无过滤）——const 首层,
                             * 直传 ce_resolve_many_group_* 的
                             * `const uint8_t* const*`（仅加第二层 const,
                             * clang -Wincompatible-pointer-types 零告警） */
    uint8_t* corner;   /* n       任务序 rule-else 角标志（K2: base_active
                        * 全零且 has_rule → active 改写为 rule） */
    float* ca;         /* n*512   任务序角锚点现算表（K2: 角任务锚点按
                        * rule_active 现算, 与 Python post-merge active 一致） */
    uint8_t* ltscr;    /* n*512   K6: LT 任务中间缓冲（raw→LT 改写后 fade） */
    float*  dgains;    /* n*512   K7: 方向任务每通道增益（_clip_channel_fade_gains） */
    float*  dgtmp;     /* n*512   K7: 方向任务路径B通道级临时（K<=512） */
    uint8_t* dscr;     /* n*512   K7: 方向任务 raw 物化行（重映射就地） */
    uint8_t* dcmask;   /* n*512   K7: 方向任务完成批次掩码（:8040） */
    int cap;
} ce_fused_scratch;

typedef struct {
    ce_clip_entry* clips;
    int clip_cap, clip_count;
    uint8_t* scr_block;         /* A3: 融合内核 scratch 整块（NULL=未分配） */
    ce_fused_scratch scr;
    uint8_t** consumed;     /* R56g: 每槽接管消费掩码(512B), 可 NULL */
    uint8_t* consumed_any;  /* R56g: 每槽 0/1 快速跳过 */       /* A3: 融合内核 scratch 视图（成长复用） */
} ce_ctx;

CE_API void* ce_ctx_create(int max_clips)
{
    ce_ctx* ctx = (ce_ctx*)calloc(1, sizeof(ce_ctx));
    if (!ctx) return NULL;
    ctx->clip_cap = max_clips > 0 ? max_clips : 512;
    ctx->clips = (ce_clip_entry*)calloc((size_t)ctx->clip_cap, sizeof(ce_clip_entry));
    if (!ctx->clips) { free(ctx); return NULL; }
    ctx->consumed = (uint8_t**)calloc((size_t)ctx->clip_cap, sizeof(uint8_t*));
    ctx->consumed_any = (uint8_t*)calloc((size_t)ctx->clip_cap, 1);
    if (!ctx->consumed || !ctx->consumed_any) {
        free(ctx->consumed); free(ctx->consumed_any);
        free(ctx->clips); free(ctx); return NULL;
    }
    return ctx;
}

CE_API void ce_ctx_destroy(void* vctx)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx) return;
    for (int i = 0; i < ctx->clip_count; i++) {
        free(ctx->clips[i].frame_times);
        free(ctx->clips[i].frame_data);
        free(ctx->clips[i].last_change);
        free(ctx->clips[i].base_active);
        free(ctx->clips[i].rule_active);
        free(ctx->clips[i].prev);
        free(ctx->clips[i].target);
        free(ctx->clips[i].anchor);
    }
    if (ctx->consumed) {
        for (int i = 0; i < ctx->clip_count; i++) free(ctx->consumed[i]);
        free(ctx->consumed);
    }
    free(ctx->consumed_any);
    free(ctx->scr_block);           /* A3: 融合内核 scratch 整块 */
    free(ctx->clips);
    free(ctx);
}

/* A3: 混合锚点内核前置声明（定义见文件尾）——ce_clip_register 注册期调用 */
CE_API void ce_clip_anchor(const uint8_t* frames, int n_frames,
                           const uint8_t* ch_active, float* anchor);

/* K6: LT 融合内核类型与前置声明（定义见文件尾）——ce_timeline_frame 调用 */
typedef struct {
    int n;                /* 帧数 */
    const double* times;  /* 升序时间戳 */
    const uint8_t* const* rows;  /* 每帧原始长度数据 */
    const int* lens;      /* 每帧原始长度 */
} ce_lt_pump_tbl;

static int ce_render_clip_frame_lt(
    const double* frame_times, int n_frames,
    const uint8_t* frame_data, const float* last_change,
    double source_t, double clip_start, double rate, double timeline_now,
    int hold_extension, float gain, const uint8_t* base_active,
    int fade_mode, const float* prev, const float* target,
    const void* scope, double local_t,
    const uint8_t* nm_rgb, const int* fx_live,
    const void* const* cross_tbl, const double* cross_t0, const int* cross_live,
    double* state, double* state_t, uint8_t* state_exists,
    uint8_t* color, uint8_t* color_valid,
    uint8_t* touched, uint8_t* touched_color,
    int n_state, int n_color,
    uint8_t* ltscr,
    uint8_t* out, uint8_t* active_out, double* scores);

/* 注册（幂等: 相同内容覆盖）; 数据复制进 ctx 持久持有。返回 clip_idx。 */
CE_API int ce_clip_register(void* vctx, int idx,
                            const double* frame_times, int n_frames,
                            const uint8_t* frame_data, const float* last_change,
                            const uint8_t* base_active, int has_rule,
                            const uint8_t* rule_active)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx || idx < 0 || idx >= ctx->clip_cap) return -1;
    ce_clip_entry* e = &ctx->clips[idx];
    if (e->n_frames != n_frames) {
        free(e->frame_times); free(e->frame_data); free(e->last_change);
        e->frame_times = NULL; e->frame_data = NULL; e->last_change = NULL;
        e->n_frames = n_frames;
    }
    if (e->frame_times == NULL && n_frames > 0) {
        e->frame_times = (double*)malloc((size_t)n_frames * sizeof(double));
        e->frame_data = (uint8_t*)malloc((size_t)n_frames * 512);
        if (!e->frame_times || !e->frame_data) return -1;
    }
    if (n_frames > 0) {
        memcpy(e->frame_times, frame_times, (size_t)n_frames * sizeof(double));
        memcpy(e->frame_data, frame_data, (size_t)n_frames * 512);
        if (last_change != NULL) {
            /* M4: last_change 为 NULL（调用方无 last_change_time）时跳过
               memcpy——旧实现无条件 memcpy(NULL) 空指针崩溃。NULL 语义与
               直调路径一致: ce_render_clip_frame 内 lc=NULL → score=clip_start。 */
            if (e->last_change == NULL)
                e->last_change = (float*)malloc((size_t)n_frames * 512 * sizeof(float));
            if (!e->last_change) return -1;
            memcpy(e->last_change, last_change, (size_t)n_frames * 512 * sizeof(float));
        } else {
            /* 曾注册过 last_change 现在缺 → 释放置 NULL, 渲染按无 last_change
               处理（避免陈旧数据污染 score）。 */
            free(e->last_change);
            e->last_change = NULL;
        }
    }
    if (e->base_active == NULL)
        e->base_active = (uint8_t*)malloc(512);
    if (e->base_active) memcpy(e->base_active, base_active, 512);
    e->has_rule = has_rule ? 1 : 0;
    if (e->has_rule) {
        if (e->rule_active == NULL)
            e->rule_active = (uint8_t*)malloc(512);
        if (e->rule_active) memcpy(e->rule_active, rule_active, 512);
    }
    if (idx >= ctx->clip_count) ctx->clip_count = idx + 1;
    e->in_use = 1;
    /* A3: 混合锚点注册期一次计算。act' = base_active & rule（非零即真,
     * 同 _ce_consume_resolve_many 物化锚点输入的 np.asarray(rule,bool)&act）;
     * 输入与 _ce_get_clip_anchor 同源（frame_data/base_active/rule 均注册
     * 自 compiled）→ ce_clip_anchor 同输入同输出 → 融合路径锚点逐字节一致。
     * 内容跨帧稳定, 注册一次; 槽复用覆盖时随注册重算。 */
    if (e->anchor == NULL)
        e->anchor = (float*)malloc(512 * sizeof(float));
    if (e->anchor != NULL && e->base_active != NULL) {
        uint8_t actp[512];
        for (int c = 0; c < 512; c++)
            actp[c] = (uint8_t)(e->base_active[c] != 0
                                && (!e->has_rule || e->rule_active[c] != 0));
        ce_clip_anchor(e->frame_data, e->n_frames, actp, e->anchor);
    }
    return idx;
}

/* ── R56g: 接管消费掩码（Python 侧按帧标记后回推）──────────────────
 * mask=NULL 清除该槽。应用点 = ce_timeline_frame 任务渲染后（act/sc 置
 * 零/-inf），与 Python `_takeover_consumed_active_for_item` 同语义
 * （active & ~consumed，scores 同失效域）。 */
CE_API int ce_clip_set_consumed(void* vctx, int idx, const uint8_t* mask)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx || idx < 0 || idx >= ctx->clip_cap) return -1;
    if (ctx->consumed[idx] == NULL) {
        ctx->consumed[idx] = (uint8_t*)calloc(1, 512);
        if (!ctx->consumed[idx]) return -1;
    }
    uint8_t any = 0;
    if (mask != NULL) {
        memcpy(ctx->consumed[idx], mask, 512);
        for (int c = 0; c < 512; c++) { if (mask[c]) { any = 1; break; } }
    } else {
        memset(ctx->consumed[idx], 0, 512);
    }
    ctx->consumed_any[idx] = any;
    return idx;
}

/* ── M2: 槽位释放（Python 侧淘汰旧键后调用）──────────────────────
 * 释放该槽全部持久数据并清零（calloc 语义 = 未注册态）; 若释放的是
 * 顶部槽则收缩 clip_count（ce_render_frame 的 ci>=clip_count 守卫
 * 使被释放槽恒不可达; 中部槽由 frame_times==NULL 守卫兜底跳过）。
 * 幂等: 未注册槽调用无副作用。 */
CE_API int ce_clip_unregister(void* vctx, int idx)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx || idx < 0 || idx >= ctx->clip_cap) return -1;
    ce_clip_entry* e = &ctx->clips[idx];
    free(e->frame_times);
    free(e->frame_data);
    free(e->last_change);
    free(e->base_active);
    free(e->rule_active);
    free(e->prev);
    free(e->target);
    free(e->anchor);
    memset(e, 0, sizeof(ce_clip_entry));
    while (ctx->clip_count > 0 && !ctx->clips[ctx->clip_count - 1].in_use)
        ctx->clip_count--;
    return 0;
}

/* ── M2: 槽容量查询（Python 侧注册槽分配上限, 与 ce_ctx_create 的
 * max_clips 一致）── */
CE_API int ce_ctx_cap(void* vctx)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    return ctx ? ctx->clip_cap : 0;
}

/* ── P6b: fade anchor 注册（幂等覆盖; prev/target 可单独或同时更新）──────
 * 与 _get_fade_in_anchor/_get_fade_out_target 缓存值逐字节一致（f32）;
 * fade_mode=1 用 prev, fade_mode=2 用 target; 内容跨帧稳定故注册一次,
 * fade_out target 每帧新对象 → Python 侧按 id() 判等, 每帧重注册无害。
 * A3 修复: NULL 语义 = 清除对应 anchor（释放置 NULL）。旧实现 NULL 时
 * 保留旧值 —— 槽内容变化后陈旧 anchor 被后续渲染读到（fuzz 实锤）;
 * 真实管线中 fm 恒配对应 anchor（fm=1 任务 prev 恒非 NULL）,
 * 此修改不改变真实管线可观察行为。 */
CE_API int ce_clip_register_fade(void* vctx, int idx,
                                 const float* prev, const float* target)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx || idx < 0 || idx >= ctx->clip_cap) return -1;
    ce_clip_entry* e = &ctx->clips[idx];
    if (prev != NULL) {
        if (e->prev == NULL)
            e->prev = (float*)malloc(512 * sizeof(float));
        if (e->prev) memcpy(e->prev, prev, 512 * sizeof(float));
    } else {
        free(e->prev);
        e->prev = NULL;
    }
    if (target != NULL) {
        if (e->target == NULL)
            e->target = (float*)malloc(512 * sizeof(float));
        if (e->target) memcpy(e->target, target, 512 * sizeof(float));
    } else {
        free(e->target);
        e->target = NULL;
    }
    return idx;
}

/* 每帧一次调用: n_tasks 任务批量渲染（数据在 C, OpenMP 按任务并行）。
 * 第7次并行失败实证: MSVC vcomp ACTIVE 策略区域间自旋抢核（idle500ms
 * 进程CPU +281ms, 系统CPU 100%, 帧 17→32ms）——根因是等待策略非并行本身;
 * PASSIVE 策略（install() 里 OMP_WAIT_POLICY=PASSIVE）实测零空闲燃烧
 * （idle500ms CPU +0.0ms）+ 区域进入 <1ms, 并行区收益转正（146×~3-30µs
 * 任务: 微基准 1→8 线程 0.60→0.26ms/帧）。任务相互独立（每任务独立输出
 * 缓冲, 只读 ctx）→ 位级等价保持。
 * 输出: out_idx[t]（<0 跳过）; out/act/scores 为 [n_tasks*512] 连续块。
 */
CE_API void ce_render_frame(void* vctx,
                            const int* clip_idx, const int* universe,
                            const double* source_t, const double* start_t,
                            const double* rate, const double* timeline_now,
                            const int* hold, const float* gain, const int* fade_mode,
                            int n_tasks,
                            int* out_idx, uint8_t* out, uint8_t* act, double* scores)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (!ctx || n_tasks <= 0) return;
    int t;
    /* P6c: OpenMP 按任务并行（等待策略 PASSIVE 由 install() 设置, 零自旋） */
    #pragma omp parallel for schedule(static)
    for (t = 0; t < n_tasks; t++) {
        int ci = clip_idx[t];
        if (ci < 0 || ci >= ctx->clip_count || ctx->clips[ci].frame_times == NULL) {
            out_idx[t] = -1; continue;
        }
        ce_clip_entry* e = &ctx->clips[ci];
        out_idx[t] = ce_render_clip_frame(
            e->frame_times, e->n_frames, e->frame_data, e->last_change,
            source_t[t], start_t[t], rate[t], timeline_now[t],
            hold[t], gain[t], e->base_active, fade_mode[t],
            e->prev, e->target,
            out + (size_t)t * 512, act + (size_t)t * 512, scores + (size_t)t * 512);
    }
}

/* ── P5d: 帧准备批量（一次调用 N 任务, C 内 OpenMP 按任务并行）──────────
 * 输入为指针数组（每任务独立缓冲）; 输出 out/active_out/scores 指针数组;
 * out_idx[t] 记录每任务帧 idx（<0 跳过）。与逐任务 ce_render_clip_frame
 * 完全一致（纯 C 内并行, 无 GIL）——批量负优化教训: Python 侧必须用
 * 预分配+copyto 填充（np.stack 在 GIL 内是主因）。
 */
CE_API void ce_render_clip_frame_batch(
    const double* const* frame_times, const int* n_frames,
    const uint8_t* const* frame_data, const float* const* last_change,
    const double* source_t, const double* clip_start, const double* rate,
    const double* timeline_now, const int* hold_ext, const float* gain,
    const uint8_t* const* base_active, const int* fade_mode,
    const float* const* prev, const float* const* target,
    int n_tasks,
    uint8_t* const* out, uint8_t* const* active_out, double* const* scores,
    int* out_idx)
{
    int t;
    #pragma omp parallel for schedule(static)
    for (t = 0; t < n_tasks; t++) {
        out_idx[t] = ce_render_clip_frame(
            frame_times[t], n_frames[t], frame_data[t], last_change[t],
            source_t[t], clip_start[t], rate[t], timeline_now[t],
            hold_ext[t], gain[t], base_active[t], fade_mode[t],
            prev[t], target[t], out[t], active_out[t], scores[t]);
    }
}

/* ── P5b: 顺序分组逐设备增益（对齐 _compute_sequential_fixture_gains :6444）
 * 输入: 两组 (group_idx, group_pos, group_sizes)[K]（in/out 方向, 可 NULL）;
 * batch_offsets[batch_count]（Python 预计算: 顺序偏移）; 标量参数。
 * 输出 gains[K]（含 global_gain 乘）。f32 同序, 与 numpy 逐位一致。
 */
CE_API void ce_sequential_fixture_gains(
    const float* gi_in, const float* gp_in, const float* gs_in,
    const float* gi_out, const float* gp_out, const float* gs_out,
    const float* batch_offsets, int batch_count, int K,
    double per_group, double global_gain, double out_span,
    double fade_in, double fade_out, double duration, double local_t,
    int is_fading_in, int is_fading_out,
    double seq_delay_in, double seq_delay_out,
    double fade_in_scale, double fade_out_scale,
    int curve, int strategy, float* gains)
{
    /* numpy 标量语义: Python float(f64) 标量参与 f32 数组运算时转 f32;
       标量×标量保持 f64。逐运算对齐。 */
    for (int i = 0; i < K; i++)
        gains[i] = 1.0f;

    if (is_fading_in && gi_in != NULL) {
        float pg_in = (float)(per_group * fade_in_scale);   /* f64 乘 → f32 */
        for (int i = 0; i < K; i++) {
            int bidx = ((int)gi_in[i]) % (batch_count > 0 ? batch_count : 1);
            if (bidx < 0) bidx += (batch_count > 0 ? batch_count : 1);
            float gsize = gs_in[i];
            float win = pg_in / (gsize > 1.0f ? gsize : 1.0f);
            float d = (float)per_group - win;               /* NEP50: 标量转 f32 减 */
            if (d < 0.0f) d = 0.0f;
            float t_start = batch_offsets[bidx] + gp_in[i] * d;
            float t_end = t_start + win;
            float w = win;
            if (w < 1e-6f) w = 1e-6f;
            float tt = ((float)local_t - t_start) / w;      /* f64 标量转 f32 */
            if (tt < 0.0f) tt = 0.0f;
            else if (tt > 1.0f) tt = 1.0f;
            float g = _ce_transition_gain(tt, 1, curve, strategy);
            if ((float)local_t < t_start) g = 0.0f;
            else if ((float)local_t >= t_end) g = 1.0f;
            if (g < gains[i]) gains[i] = g;
        }
    }

    if (is_fading_out && gi_out != NULL && (fade_out > 1e-4 || seq_delay_out > 1e-4)) {
        double out_target = (seq_delay_out > 1e-4) ? seq_delay_out : fade_out;
        double os = out_span;
        if (os < 1e-6) os = 1e-6;
        double out_scale = out_target / os;                 /* f64 除 */
        if (out_scale < 1e-6) out_scale = 1e-6;
        double out_per_group = per_group * out_scale;       /* f64 乘 */
        if (out_per_group < 1e-6) out_per_group = 1e-6;
        float pg_out = (float)(out_per_group * fade_out_scale);  /* f64 乘 → f32 */
        float out_scale_f = (float)out_scale;
        float t_start_base = (float)(duration - out_target);     /* f64 减 → f32 */
        for (int i = 0; i < K; i++) {
            int bidx = ((int)gi_out[i]) % (batch_count > 0 ? batch_count : 1);
            if (bidx < 0) bidx += (batch_count > 0 ? batch_count : 1);
            float gsize = gs_out[i];
            float win = pg_out / (gsize > 1.0f ? gsize : 1.0f);
            float d = (float)out_per_group - win;          /* NEP50: 标量转 f32 减 */
            if (d < 0.0f) d = 0.0f;
            float rel_start = batch_offsets[bidx] * out_scale_f + gp_out[i] * d;
            float t_start = t_start_base + rel_start;
            float t_end = t_start + win;
            float w = win;
            if (w < 1e-6f) w = 1e-6f;
            float tt = (t_end - (float)local_t) / w;
            if (tt < 0.0f) tt = 0.0f;
            else if (tt > 1.0f) tt = 1.0f;
            float g = _ce_transition_gain(tt, 0, curve, strategy);
            if ((float)local_t >= t_end) g = 0.0f;
            else if ((float)local_t < t_start) g = 1.0f;
            if (g < gains[i]) gains[i] = g;
        }
    }

    float gg = (float)global_gain;
    for (int i = 0; i < K; i++)
        gains[i] = gains[i] * gg;
}

/* np.maximum 精确语义（f64 版, 同 ce_np_maxf :2363 的位级约定）: NaN 双侧
 * 传播（a 为 NaN 返回 a 原位型, 否则 b 为 NaN 返回 b）; 数值平局返回第二参
 * （max(-0.0,+0.0)=+0.0）。NaN 判定走整数位检查——MSVC /fp:precise 会把
 * sNaN 折叠成 qNaN 常量（见 :2350 注释）。 */
static int ce_f64_is_nan(double x)
{
    uint64_t u;
    memcpy(&u, &x, 8);
    return ((u & 0x7FF0000000000000ULL) == 0x7FF0000000000000ULL)
        && ((u & 0x000FFFFFFFFFFFFFULL) != 0ULL);
}

static double ce_np_max_dbl(double a, double b)
{
    if (ce_f64_is_nan(a)) return a;
    if (ce_f64_is_nan(b)) return b;
    return (a > b) ? a : b;
}

/* ── T3: K2 全链路混合（对齐 _mix_one_universe :1722, direct 模式）───────
 * 输入:
 *   base_data/base_active[4][512]  (fader/programmer/tester/linker, 可 NULL)
 *   shape_data/shape_active[6][512] (effect/effect_auto/matrix/matrix_audio/audio/joystick)
 *   clip_data/clip_active[3][512]   (timeline/clip_playback/clip_preview)
 *   ltp_time[layer][512] f64 或 NULL（base 4 层 + shape 6 层的时间戳）
 *   defaults[512] u8（channel defaults）; has_defaults
 *   shape_gain f32; direct_anchor[512] f32（或 NULL → 128）
 *   clip_t/editor_t[512] f64 或 NULL（CLIP LTP 时间戳）
 * 输出: out[512] u8（阶段1-4 全语义, clamp 后截断）
 * 语义对齐: BASE LTP 覆盖→default_suppressed→SHAPE 偏移(absolute/audio_htp)/
 * 平均→合成(only/both+linker/absolute 时间比较)→CLIP LTP 覆盖
 */
CE_API void ce_full_mix(
    const uint8_t* const* base_data, const uint8_t* const* base_active,
    const uint8_t* const* shape_data, const uint8_t* const* shape_active,
    const uint8_t* const* clip_data, const uint8_t* const* clip_active,
    const double* const* ltp_time, int n_ltp_layers,
    const uint8_t* defaults, const uint8_t* default_active, int has_defaults,
    float shape_gain, const float* direct_anchor,
    const double* clip_t, const double* editor_t,
    uint8_t* out)
{
    float base_val[512];
    uint8_t base_act[512], base_layer_act[512], default_mask[512];
    double base_time[512];
    float shape_offset[512], shape_raw[512];
    uint8_t shape_act[512];
    int n_layers_per_ch[512];
    uint8_t direct_abs[512];
    float direct_abs_val[512];
    double direct_abs_time[512];
    uint8_t audio_htp_act[512];
    float audio_htp_val[512];
    double audio_htp_time[512];
    uint8_t linker_act[512];
    float out_f[512];

    for (int c = 0; c < 512; c++) {
        base_val[c] = (has_defaults && default_active[c]) ? (float)defaults[c] : 0.0f;
        base_act[c] = (has_defaults && default_active[c]) ? 1 : 0;
        base_layer_act[c] = 0;
        default_mask[c] = (has_defaults && default_active[c]) ? 1 : 0;
        base_time[c] = -1.0;
        shape_offset[c] = 0.0f;
        shape_raw[c] = 0.0f;
        shape_act[c] = 0;
        n_layers_per_ch[c] = 0;
        direct_abs[c] = 0;
        direct_abs_val[c] = 0.0f;
        direct_abs_time[c] = -1.0;
        audio_htp_act[c] = 0;
        audio_htp_val[c] = 0.0f;
        audio_htp_time[c] = -1.0;
        linker_act[c] = 0;
    }

    /* ── 阶段 1: BASE LTP ───────────────────────────────────────── */
    for (int l = 0; l < 4; l++) {
        if (base_data[l] == NULL || base_active[l] == NULL)
            continue;
        const double* lt = (ltp_time != NULL && l < n_ltp_layers) ? ltp_time[l] : NULL;
        for (int c = 0; c < 512; c++) {
            if (!base_active[l][c])
                continue;
            base_val[c] = (float)base_data[l][c];
            base_act[c] = 1;
            base_layer_act[c] = 1;
            double t = (lt != NULL) ? lt[c] : 0.0;
            /* Python: base_time[a] = np.maximum(base_time[a], layer_t[a]) */
            base_time[c] = ce_np_max_dbl(base_time[c], t);
        }
    }
    /* default_suppressed: default_mask & non_default_active & ~base_layer_active */
    for (int c = 0; c < 512; c++) {
        if (!default_mask[c] || base_layer_act[c])
            continue;
        int non_default = 0;
        for (int l = 0; l < 6 && !non_default; l++)
            if (shape_active[l] != NULL && shape_active[l][c])
                non_default = 1;
        for (int l = 0; l < 3 && !non_default; l++)
            if (clip_active[l] != NULL && clip_active[l][c])
                non_default = 1;
        if (non_default)
            base_act[c] = 0;
    }
    /* linker 掩码 */
    if (base_active[3] != NULL)
        for (int c = 0; c < 512; c++)
            linker_act[c] = base_active[3][c];

    /* ── 阶段 2: SHAPE 偏移 ─────────────────────────────────────── */
    for (int l = 0; l < 6; l++) {
        if (shape_data[l] == NULL || shape_active[l] == NULL)
            continue;
        int is_abs = (l <= 4);            /* effect..audio: ABSOLUTE_DIRECT */
        int is_audio_htp = (l == 3 || l == 4);  /* matrix_audio, audio */
        const double* lt = (ltp_time != NULL && (4 + l) < n_ltp_layers) ? ltp_time[4 + l] : NULL;
        for (int c = 0; c < 512; c++) {
            if (!shape_active[l][c])
                continue;
            float sf = (float)shape_data[l][c];
            float a = (direct_anchor != NULL) ? direct_anchor[c] : 128.0f;
            if (is_audio_htp) {
                audio_htp_act[c] = 1;
                if (sf >= audio_htp_val[c]) {
                    audio_htp_val[c] = sf;
                    double t = (lt != NULL) ? lt[c] : 0.0;
                    /* Python: audio_htp_time = np.maximum(audio_htp_time, t) */
                    audio_htp_time[c] = ce_np_max_dbl(audio_htp_time[c], t);
                }
            } else if (is_abs) {
                /* Python 仅 ABSOLUTE_DIRECT_SHAPE_LAYERS 进 direct_abs 路径
                 * （direct_absolute |= a; :1872）——joystick(相对层) 不进。 */
                direct_abs[c] = 1;
                double t = (lt != NULL) ? lt[c] : 0.0;
                if (t >= direct_abs_time[c]) {
                    direct_abs_time[c] = t;
                    direct_abs_val[c] = sf;
                }
            }
            float offset = (sf - a) * shape_gain;
            if (!is_abs)
                shape_offset[c] += offset;
            n_layers_per_ch[c] += 1;
            shape_act[c] = 1;
            if (sf > shape_raw[c])
                shape_raw[c] = sf;
        }
    }
    /* audio_htp 合并入 direct_abs */
    for (int c = 0; c < 512; c++) {
        if (audio_htp_act[c]) {
            direct_abs[c] = 1;
            if (audio_htp_time[c] >= direct_abs_time[c]) {
                direct_abs_time[c] = audio_htp_time[c];
                direct_abs_val[c] = audio_htp_val[c];
            }
            if (audio_htp_val[c] > shape_raw[c])
                shape_raw[c] = audio_htp_val[c];
        }
    }
    /* multi_mask: offset /= n_layers */
    for (int c = 0; c < 512; c++)
        if (n_layers_per_ch[c] > 1)
            shape_offset[c] = shape_offset[c] / (float)n_layers_per_ch[c];

    /* ── 阶段 3: 合成 ───────────────────────────────────────────── */
#ifdef CE_AVX2
    /* 8-lane SIMD（每 lane 一通道）: 分支用 mask/blendv（ob/os/both 互斥,
     * 与标量分支同构）; 单通道内浮点运算顺序不变——base_val+offset 与
     * direct_abs_val+offset 各算一次, 未选中 lane 的结果由 blendv 丢弃;
     * double 时间比较保持标量（见下, 本机 vcmppd 数据通路不可靠）。 */
    {
        const __m256i one_i = _mm256_set1_epi32(1);
        const __m256 zero_f = _mm256_setzero_ps();
        for (int c0 = 0; c0 < 512; c0 += 8) {
            __m256i ba_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(base_act + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));
            __m256i sa_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(shape_act + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));
            __m256i li_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(linker_act + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));
            __m256i da_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(direct_abs + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));

            __m256 ba_f = _mm256_castsi256_ps(ba_m);
            __m256 sa_f = _mm256_castsi256_ps(sa_m);
            __m256 li_f = _mm256_castsi256_ps(li_m);
            __m256 da_f = _mm256_castsi256_ps(da_m);
            /* ob = base & ~shape; os = shape & ~base; both = base & shape */
            __m256 ob_f = _mm256_andnot_ps(sa_f, ba_f);
            __m256 os_f = _mm256_andnot_ps(ba_f, sa_f);
            __m256 both_f = _mm256_and_ps(ba_f, sa_f);

            __m256 bv = _mm256_loadu_ps(base_val + c0);
            __m256 sr = _mm256_loadu_ps(shape_raw + c0);
            __m256 off = _mm256_loadu_ps(shape_offset + c0);
            __m256 dv = _mm256_loadu_ps(direct_abs_val + c0);

            __m256 bv_off = _mm256_add_ps(bv, off);
            __m256 dv_off = _mm256_add_ps(dv, off);

            /* os: shape_raw, direct_abs 时 direct_abs_val + offset */
            __m256 r_os = _mm256_blendv_ps(sr, dv_off, da_f);

            /* both: linker → base_val; 否则 base_val+offset, 且 direct_abs
             * 时 direct_abs_time>=base_time → direct_abs_val+offset。
             * 时间比较保持标量（本机 vcmppd 结果经 store/permute/insert 读取
             * 出现 lane 错位, 双 DLL fuzz 实证; 逐指令探针证明标量比较 +
             * cvtepu8+cmpeq 重建掩码路径可靠）——逐 lane 比较 → u8 数组 →
             * 与 ce_mix_clips_avx2 同款 u8 掩码路径。 */
            uint8_t ge_bits[8];
            for (int j = 0; j < 8; j++)
                ge_bits[j] = (direct_abs_time[c0 + j] >= base_time[c0 + j]) ? 1 : 0;
            __m256i tge_m = _mm256_cmpeq_epi32(
                _mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)ge_bits)), one_i);
            __m256 tge_f = _mm256_castsi256_ps(tge_m);
            __m256 r_both_inner = _mm256_blendv_ps(bv_off, dv_off,
                                                   _mm256_and_ps(da_f, tge_f));
            __m256 r_both = _mm256_blendv_ps(r_both_inner, bv, li_f);

            /* ob → base_val; os → r_os; both → r_both; else → 0 */
            __m256 res = _mm256_blendv_ps(_mm256_blendv_ps(
                _mm256_blendv_ps(zero_f, r_both, both_f), r_os, os_f), bv, ob_f);
            _mm256_storeu_ps(out_f + c0, res);
        }
    }
#else
    for (int c = 0; c < 512; c++) {
        int ob = base_act[c] && !shape_act[c];
        int os = shape_act[c] && !base_act[c];
        int both = base_act[c] && shape_act[c];
        if (ob) {
            out_f[c] = base_val[c];
        } else if (os) {
            out_f[c] = shape_raw[c];
            if (direct_abs[c])
                out_f[c] = direct_abs_val[c] + shape_offset[c];
        } else if (both) {
            if (linker_act[c]) {
                out_f[c] = base_val[c];
            } else {
                out_f[c] = base_val[c] + shape_offset[c];
                if (direct_abs[c]) {
                    if (direct_abs_time[c] >= base_time[c])
                        out_f[c] = direct_abs_val[c] + shape_offset[c];
                    else
                        out_f[c] = base_val[c] + shape_offset[c];
                }
            }
        } else {
            out_f[c] = 0.0f;
        }
    }
#endif

    /* ── 阶段 4: CLIP LTP 覆盖 ──────────────────────────────────── */
#ifdef CE_AVX2
    {
        const __m256i one_i = _mm256_set1_epi32(1);
        const int have_t = (clip_t != NULL && editor_t != NULL);
        for (int l = 0; l < 3; l++) {
            if (clip_data[l] == NULL || clip_active[l] == NULL)
                continue;
            for (int c0 = 0; c0 < 512; c0 += 8) {
                /* 掩码 = clip_active & ~linker（& t>=editor, 双指针齐备时） */
                __m256i act_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(clip_active[l] + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));
                __m256i li_m = _mm256_andnot_si256(_mm256_cmpeq_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(linker_act + c0))), _mm256_setzero_si256()), _mm256_set1_epi32(-1));
                __m256 mask = _mm256_castsi256_ps(_mm256_andnot_si256(li_m, act_m));
                if (have_t) {
                    /* clip_t>=editor_t 标量比较 → u8 → cvtepu8+cmpeq（同上） */
                    uint8_t ge_bits[8];
                    for (int j = 0; j < 8; j++)
                        ge_bits[j] = (clip_t[c0 + j] >= editor_t[c0 + j]) ? 1 : 0;
                    __m256i tge_m = _mm256_cmpeq_epi32(
                        _mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)ge_bits)), one_i);
                    mask = _mm256_and_ps(mask, _mm256_castsi256_ps(tge_m));
                }
                __m256 cd = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(
                    _mm_loadl_epi64((const __m128i*)(clip_data[l] + c0))));
                __m256 res = _mm256_blendv_ps(_mm256_loadu_ps(out_f + c0), cd, mask);
                _mm256_storeu_ps(out_f + c0, res);
            }
        }
    }
#else
    for (int l = 0; l < 3; l++) {
        if (clip_data[l] == NULL || clip_active[l] == NULL)
            continue;
        for (int c = 0; c < 512; c++) {
            if (!clip_active[l][c])
                continue;
            if (linker_act[c])
                continue;
            if (clip_t == NULL || editor_t == NULL) {
                out_f[c] = (float)clip_data[l][c];
            } else if (clip_t[c] >= editor_t[c]) {
                out_f[c] = (float)clip_data[l][c];
            }
        }
    }
#endif

#ifdef CE_AVX2
    /* clamp 按通道向量化: min/max + NaN→0（同标量 (uint8_t)NaN 的 MSVC 行为;
     * min/max 遇 NaN 返回哪个操作数依硬件, 显式清零保证一致）。截断 cast 保持
     * 标量——本机 cvttps2dq 按最近舍入执行（非整数 float 双 DLL fuzz + 逐指令
     * 探针实证 209.99999→210）, 每 lane 用 C (uint8_t) 截断与 SSE2 标量一致。 */
    {
        const __m256 zero_f = _mm256_setzero_ps();
        const __m256 hi_f = _mm256_set1_ps(255.0f);
        for (int c0 = 0; c0 < 512; c0 += 8) {
            __m256 v = _mm256_loadu_ps(out_f + c0);
            v = _mm256_min_ps(_mm256_max_ps(v, zero_f), hi_f);
            __m256 nan = _mm256_cmp_ps(v, v, _CMP_UNORD_Q);
            v = _mm256_andnot_ps(nan, v);
            float vv[8];
            _mm256_storeu_ps(vv, v);
            for (int j = 0; j < 8; j++)
                out[c0 + j] = (uint8_t)vv[j];
        }
    }
#else
    for (int c = 0; c < 512; c++) {
        float v = out_f[c];
        if (v < 0.0f) v = 0.0f;
        else if (v > 255.0f) v = 255.0f;
        out[c] = (uint8_t)v;
    }
#endif
}

/* ── T4: K1 波形求值（对齐 _wave_generic :1444 + WAVE_FUNCS :67）────────
 * 全程 double（math.sin/cos/exp 是 f64; t/speed/phase_step 是 Python float）;
 * val = lo + v*(hi-lo) f64 → int() 截断 → clamp[0,255]。
 * wave_fn_idx: 0=Sine 1=Square 2=Sawtooth 3=Triangle 4=Random(splitmix64)
 *              5=Cosine 6=Bounce 7=Expo
 * Random 逐位复刻 _det_rand: round 用 rint(银行家舍入, 同 Python round)。
 */
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#ifndef M_E
#define M_E 2.7182818284590452354
#endif

/* Python t % 1.0 语义（负数返回非负） */
static double _ce_mod1(double x)
{
    double m = fmod(x, 1.0);
    if (m < 0.0) m += 1.0;
    return m;
}

/* Python x % y 逐位复刻（CPython floatobject.c float_rem）:
 * fmod 后按除数符号校正; 精确 0 时返回 copysign(0.0, y)（含 -0.0 归一）。
 * 比 _ce_mod1 通用: 除数可为负（负 bpm → 负 beat_period）或无穷。 */
static double _ce_mod_div(double x, double y)
{
    double m = fmod(x, y);
    if (m != 0.0) {
        if ((y < 0.0) != (m < 0.0))
            m += y;
    } else {
        m = copysign(0.0, y);
    }
    return m;
}

static double _ce_wave(double x, int idx)
{
    switch (idx) {
    case 0: return (sin(x * 2.0 * M_PI) + 1.0) / 2.0;
    case 1: return (_ce_mod1(x) < 0.5) ? 1.0 : 0.0;
    case 2: return _ce_mod1(x);
    case 3: return 1.0 - fabs(2.0 * _ce_mod1(x) - 1.0);
    case 4: {
        /* _det_rand: splitmix64 逐位复刻（rint = 银行家舍入, 同 Python round） */
        unsigned long long k = (unsigned long long)rint(x * 100000.0);
        k = (k ^ (k >> 30)) * 0xBF58476D1CE4E5B9ULL;
        k = (k ^ (k >> 27)) * 0x94D049BB133111EBULL;
        k = (k ^ (k >> 31));
        return (double)(k & 0xFFFFFFFFULL) / 4294967296.0;
    }
    case 5: return (cos(x * 2.0 * M_PI) + 1.0) / 2.0;
    case 6: return fabs(sin(x * M_PI));
    case 7: return (exp(_ce_mod1(x)) - 1.0) / (M_E - 1.0);
    default: return (sin(x * 2.0 * M_PI) + 1.0) / 2.0;
    }
}

CE_API void ce_wave_effect(double t, double speed, double phase_step,
                           double lo, double hi, int wave_fn_idx, int n,
                           uint8_t* out)
{
    for (int i = 0; i < n; i++) {
        double phase = (double)i * phase_step;   /* seq_i * phase_step */
        double v = _ce_wave(t * speed + phase, wave_fn_idx);
        double val = lo + v * (hi - lo);
        int iv;

        if (val <= 0.0) iv = 0;        /* B2-2 值域先判断 */

        else if (val >= 255.0) iv = 255;

        else iv = (int)val;
        out[i] = (uint8_t)iv;
    }
}

/* ── T4 续: Audio 反应波形（对齐 _audio_bpm_wave :2649 / _audio_energy :2685 /
 *    _audio_beat_fader :3449 三种节拍包络预设）────────────────────────────
 * 统一入口, kind 0-2:
 *   kind 0 = bpm_wave   (对齐 _audio_bpm_wave :2649): 每节拍一次指数波从头部
 *            向尾部传播, v = exp(-((beat_phase - seq_i*phase_step) % 1.0) * 5)
 *   kind 1 = energy     (对齐 _audio_energy :2685): 全体设备同一节拍能量包络,
 *            v = exp(-beat_phase * 4)
 *   kind 2 = beat_fader (对齐 _audio_beat_fader :3449): 逐设备错相指数衰减,
 *            v = exp(-beat_phase_i * decay), beat_phase_i 按 seq_i*offset_per 偏移
 * 参数映射: t/lo/hi 通用; bpm → beat_period = 60.0/bpm; phase_step 仅 kind 0;
 *   offset_per/decay 仅 kind 2。
 * 位级纪律: 全程 double; t % beat_period 与 (..) % 1.0 用 _ce_mod_div 逐位复刻
 *   CPython float_rem（负数 t/负 bpm/±0.0 语义一致）; exp 参数左结合对齐
 *   Python（-x % 1.0 * 5 → (-mod)*5.0）; int()+clamp 先值域早退再截断
 *   （同 ce_fader_effect, 避免超 int32 的 C (int) UB）; 参数域内无 NaN。 */
CE_API void ce_audio_wave_effect(
    double t, double bpm, int kind,
    double phase_step, double offset_per, double decay,
    double lo, double hi,
    int n, uint8_t* out)
{
    double beat_period = 60.0 / bpm;
    double beat_phase = _ce_mod_div(t, beat_period) / beat_period;
    for (int i = 0; i < n; i++) {
        double v, val;
        switch (kind) {
        case 0: {  /* audio_bpm_wave: 节拍触发波浪从头部向尾部传播 */
            double phase = (double)i * phase_step;
            v = exp(-_ce_mod_div(beat_phase - phase, 1.0) * 5.0);
            break;
        }
        case 1:    /* audio_energy: 全体设备同一节拍能量包络 */
            v = exp(-beat_phase * 4.0);
            break;
        case 2: {  /* audio_beat_fader: 每设备错相指数衰减 */
            double bp = _ce_mod_div(t + (double)i * offset_per, beat_period)
                        / beat_period;
            v = exp(-bp * decay);
            break;
        }
        default:   /* 未知 kind: v=0 → 输出 lo */
            v = 0.0;
            break;
        }
        val = lo + v * (hi - lo);
        if (val <= 0.0) {
            out[i] = 0;
        } else if (val >= 255.0) {
            out[i] = 255;
        } else {
            out[i] = (uint8_t)(int)val;   /* 0 < val < 255: int() 截断 */
        }
    }
}

/* T5 前向声明（_ce_pow_ucrt 定义在 :4954）: curve 的 `progress ** 2` 必须
 * 走动态 ucrtbase pow——CPython float_pow(int 指数) 逐位等于 ucrtbase pow,
 * 而本 DLL 默认 /MT 静态 CRT 的 pow(x,2.0) 内部实为 x*x（探针实测 266/50万
 * 差 1 ulp）。 */
static double _ce_pow_ucrt(double x, double y);

/* ── T5: Fader 预设逐设备求值（对齐 _register_fader_presets :2243-2385）───
 * 四种预设统一入口, kind 0-3:
 *   kind 0 = linear      (对齐 _fader_linear      :2246)
 *   kind 1 = sequential  (对齐 _fader_sequential  :2276)
 *   kind 2 = breath      (对齐 _fader_breath      :2310)
 *   kind 3 = curve       (对齐 _fader_curve       :2340)
 * 参数映射: val_a/val_b = 起始/目标值（breath 中对应 min/max 最小值/最大值,
 *   val_a=lo, val_b=hi）; duration 仅 linear/curve; delay_per/fade_time 仅
 *   sequential（每设备 start = seq_i*delay_per）; speed 仅 breath;
 *   curve_idx 仅 curve（0=Linear(else) 1=EaseIn 2=EaseOut 3=EaseInOut
 *   4=Bounce 5=Elastic）。
 * 位级纪律: 全程 double（同 Python float）; min/max 用三元复刻（相等与 NaN
 *   语义同 Python, 参数域内无 NaN）; `progress ** 2` 必须用 _ce_pow_ucrt
 *   （动态 ucrtbase pow）——CPython float_pow(int 指数) 逐位等于 ucrtbase pow,
 *   而 /MT 静态 CRT 的 pow(x,2.0) 内部实为 x*x（差 1 ulp）; sin 参数逐项
 *   左结合对齐 Python（t*speed*2*pi 与
 *   progress*pi*3/5）; int()+clamp 先值域早退再截断（C (int) 超 int32 范围
 *   是 UB, Python int() 任意精度——必须早退避免 INT_MIN 哨兵）。
 * 输出: out[i] 对应 sorted_fx[i]（seq_i 序）; ch 通道名与 orig_i 索引映射
 *   由调用方负责（本内核只算值, 不碰 ef.values 结构）。
 */
CE_API void ce_fader_effect(
    double t, int kind, int curve_idx,
    double val_a, double val_b,
    double duration, double delay_per, double fade_time, double speed,
    int n, uint8_t* out)
{
    double denom_dur = duration > 0.001 ? duration : 0.001;  /* max(0.001, duration) */
    double denom_fade = fade_time > 0.001 ? fade_time : 0.001;
    for (int i = 0; i < n; i++) {
        double prog, p, val, v;
        switch (kind) {
        case 0:  /* linear: 全部设备同一值 */
            prog = t / denom_dur;
            if (prog > 1.0) prog = 1.0;        /* min(1.0, ...) */
            val = val_a + (val_b - val_a) * prog;
            break;
        case 1:  /* sequential: 每设备 start = seq_i * delay_per */
            {
                double start = (double)i * delay_per;
                prog = (t - start) / denom_fade;
                if (prog < 0.0) prog = 0.0;    /* max(0.0, min(1.0, ...)) */
                else if (prog > 1.0) prog = 1.0;
                val = val_a + (val_b - val_a) * prog;
            }
            break;
        case 2:  /* breath: v = (sin(t*speed*2*pi)+1)/2, 全部设备同一值 */
            v = (sin(t * speed * 2.0 * M_PI) + 1.0) / 2.0;
            val = val_a + v * (val_b - val_a);
            break;
        case 3:  /* curve: 曲线映射后全部设备同一值 */
            prog = t / denom_dur;
            if (prog > 1.0) prog = 1.0;        /* min(1.0, ...) */
            switch (curve_idx) {
            case 1:  p = _ce_pow_ucrt(prog, 2.0); break;                      /* EaseIn    */
            case 2:  p = 1.0 - _ce_pow_ucrt(1.0 - prog, 2.0); break;          /* EaseOut   */
            case 3:  p = _ce_pow_ucrt(prog, 2.0) * (3.0 - 2.0 * prog); break; /* EaseInOut */
            case 4:  p = fabs(sin((prog * M_PI) * 3.0)) * (1.0 - prog); break; /* Bounce  */
            case 5:  p = sin((prog * M_PI) * 5.0) * (1.0 - prog) + prog; break; /* Elastic */
            default: p = prog; break;                                 /* Linear   */
            }
            val = val_a + (val_b - val_a) * p;
            break;
        default:
            prog = t / denom_dur;
            if (prog > 1.0) prog = 1.0;
            val = val_a + (val_b - val_a) * prog;
            break;
        }
        /* Python int()+clamp 精确语义: 先按值域早退——C (int) 对超出
         * int32 范围的 double 是 UB（MSVC cvttsd2si 返回 INT_MIN 哨兵,
         * 而 Python int() 是任意精度）。val≤0 → 0, val≥255 → 255,
         * 区间内 (int) 截断与 Python int() 逐位一致。 */
        if (val <= 0.0) {
            out[i] = 0;
        } else if (val >= 255.0) {
            out[i] = 255;
        } else {
            out[i] = (uint8_t)(int)val;   /* 0 < val < 255: int() 截断 */
}}}
/* ── T4续: chase 效果族（对齐 effect_engine.py _register_chase_presets :1560 ──
 * 复刻 11 个追逐类 preset 的 compute 函数体（纯数学部分，逐位等价纪律）:
 *   int(x)          → (long long)x 截断（向零，同 Python int()）
 *   x % n (int)     → _ce_py_mod_ll（Python 非负余数，负 x 修正）
 *   x % m (float)   → _ce_fmod_pos（fmod + 符号修正 + -0.0 归一，同 CPython
 *                      float_rem；m>0 时结果 ∈ [0,m)）
 *   random.Random(int seed) → MT19937 init_by_array，逐位复刻 CPython 3.13:
 *                      key = |seed| 按 32 位小端分块 → init_by_array
 *                      （参考 Modules/_randommodule.c random_seed :318）
 *   random()        → genrand_res53 = (g>>5)*2^26+(g>>6) 再乘 (1/2^53)
 * mode: 0=single 1=triple 2=dual 3=color 4=strobe_random 5=strobe_global
 *       6=alternate 7=fade_tail 8=bpm_chase 9=valve_sequential 10=motor
 * 输出按 seq 序（i=0..n-1，seq_i 即 sorted_fx 下标）; scatter 到 orig_i 由上层做:
 *   mode 0..9  → out（单通道）;  mode 3  → out=R out2=G out3=B;
 *   mode 10    → out=motor_x out2=motor_y
 */

/* Python x % m（m>0，含负 x）— 结果 ∈ [0,m) */
static long long _ce_py_mod_ll(long long x, long long m)
{
    long long r = x % m;
    if (r < 0) r += m;
    return r;
}

/* Python 浮点 x % m（m>0）— fmod 后符号修正; -0.0 归一为 +0.0（同 float_rem
 * 的 copysign(0.0, wx) 分支，避免 -0.0 进入后续算术） */
static double _ce_fmod_pos(double x, double m)
{
    double r = fmod(x, m);
    if (r < 0.0) r += m;
    if (r == 0.0) r = 0.0;
    return r;
}

/* ── MT19937（CPython 3.13 _randommodule.c 逐位复刻）─────────────────── */

#define _CE_MT_N 624
#define _CE_MT_M 397

typedef struct {
    uint32_t mt[_CE_MT_N];
    int index;
} ce_mt_state_t;

static void _ce_mt_init_genrand(ce_mt_state_t* s, uint32_t seed)
{
    s->mt[0] = seed;
    for (int i = 1; i < _CE_MT_N; i++) {
        s->mt[i] = 1812433253U * (s->mt[i - 1] ^ (s->mt[i - 1] >> 30)) + (uint32_t)i;
    }
    s->index = _CE_MT_N;
}

static uint32_t _ce_mt_next(ce_mt_state_t* s)
{
    uint32_t y;
    static const uint32_t mag01[2] = {0x0U, 0x9908b0dfU};
    if (s->index >= _CE_MT_N) {
        int kk;
        for (kk = 0; kk < _CE_MT_N - _CE_MT_M; kk++) {
            y = (s->mt[kk] & 0x80000000U) | (s->mt[kk + 1] & 0x7fffffffU);
            s->mt[kk] = s->mt[kk + _CE_MT_M] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        for (; kk < _CE_MT_N - 1; kk++) {
            y = (s->mt[kk] & 0x80000000U) | (s->mt[kk + 1] & 0x7fffffffU);
            s->mt[kk] = s->mt[kk + (_CE_MT_M - _CE_MT_N)] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        y = (s->mt[_CE_MT_N - 1] & 0x80000000U) | (s->mt[0] & 0x7fffffffU);
        s->mt[_CE_MT_N - 1] = s->mt[_CE_MT_M - 1] ^ (y >> 1) ^ mag01[y & 0x1U];
        s->index = 0;
    }
    y = s->mt[s->index++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680U;
    y ^= (y << 15) & 0xefc60000U;
    y ^= (y >> 18);
    return y;
}

/* CPython random(): (a*67108864.0 + b) * (1.0/9007199254740992.0) */
static double _ce_mt_res53(ce_mt_state_t* s)
{
    uint32_t a = _ce_mt_next(s) >> 5;
    uint32_t b = _ce_mt_next(s) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

/* CPython random_seed(int): key = |seed| 的 32 位小端分块 → init_by_array */
static void _ce_mt_seed(ce_mt_state_t* s, long long seed)
{
    uint32_t key[2];
    int keyused = 0;
    long long v = seed;
    if (v < 0) v = -v;
    do {
        key[keyused++] = (uint32_t)(v & 0xFFFFFFFFLL);
        v >>= 32;
    } while (v != 0 && keyused < 2);
    /* init_by_array（标准 mt19937ar 算法） */
    _ce_mt_init_genrand(s, 19650218U);
    size_t i = 1, j = 0;
    size_t k = (_CE_MT_N > (size_t)keyused ? _CE_MT_N : (size_t)keyused);
    for (; k; k--) {
        s->mt[i] = (s->mt[i] ^ ((s->mt[i - 1] ^ (s->mt[i - 1] >> 30)) * 1664525U))
                   + key[j] + (uint32_t)j;
        i++; j++;
        if (i >= _CE_MT_N) { s->mt[0] = s->mt[_CE_MT_N - 1]; i = 1; }
        if (j >= (size_t)keyused) j = 0;
    }
    for (k = _CE_MT_N - 1; k; k--) {
        s->mt[i] = (s->mt[i] ^ ((s->mt[i - 1] ^ (s->mt[i - 1] >> 30)) * 1566083941U))
                   - (uint32_t)i;
        i++;
        if (i >= _CE_MT_N) { s->mt[0] = s->mt[_CE_MT_N - 1]; i = 1; }
    }
    s->mt[0] = 0x80000000U;
    s->index = _CE_MT_N;
}

/* 每个 mode 一个独立静态函数（≤100 行/函数纪律），由 ce_chase_effect 分派 */
static void _ce_chase_single(double t, double speed, int n, int lo, int hi,
                             uint8_t* out)
{
    long long k = (long long)(t * speed * (double)n);
    int idx = (int)_ce_py_mod_ll(k, (long long)n);
    for (int i = 0; i < n; i++) out[i] = (i == idx) ? (uint8_t)hi : (uint8_t)lo;
}

static void _ce_chase_triple(double t, double speed, int n, int lo, int hi,
                             int width, uint8_t* out)
{
    long long k = (long long)(t * speed * (double)n);
    int head = (int)_ce_py_mod_ll(k, (long long)n);
    if (width < 1) width = 1;   /* ★ R126(4085321) 对齐: max(1, width) 截断 0 整条熄灭 */
    memset(out, lo, (size_t)n);
    for (int i = 0; i < width; i++) out[(head + i) % n] = (uint8_t)hi;
}

static void _ce_chase_dual(double t, double speed, int n, int lo, int hi,
                           int width, uint8_t* out)
{
    long long k = (long long)(t * speed * (double)n);
    int head1 = (int)_ce_py_mod_ll(k, (long long)n);
    long long k2 = (long long)((double)n - t * speed * (double)n);
    int head2 = (int)_ce_py_mod_ll(k2, (long long)n);
    if (width < 1) width = 1;   /* ★ R126(4085321) 对齐: max(1, width) 同 _triple_chase 修复 */
    memset(out, lo, (size_t)n);
    for (int i = 0; i < width; i++) {
        out[(head1 + i) % n] = (uint8_t)hi;
        out[(head2 + i) % n] = (uint8_t)hi;
    }
}

static void _ce_chase_color(double t, double speed, int n, int n_colors,
                            uint8_t* out, uint8_t* out2, uint8_t* out3)
{
    static const uint8_t colors[6][3] = {
        {255, 0, 0}, {0, 255, 0}, {0, 0, 255},
        {255, 255, 0}, {0, 255, 255}, {255, 0, 255}};
    int nc = n_colors;
    if (nc < 1) nc = 1;
    if (nc > 6) nc = 6;
    int seg = n / nc;
    if (seg < 1) seg = 1;
    long long k = (long long)(t * speed * (double)n);
    int head = (int)_ce_py_mod_ll(k, (long long)n);
    for (int i = 0; i < n; i++) {
        int rel = (int)_ce_py_mod_ll((long long)(i - head), (long long)n);
        int ci = (rel / seg) % nc;
        out[i] = colors[ci][0];
        out2[i] = colors[ci][1];
        out3[i] = colors[ci][2];
    }
}

static void _ce_chase_strobe_random(double t, double rate, int n, int lo, int hi,
                                    double density, uint8_t* out)
{
    ce_mt_state_t st;
    _ce_mt_seed(&st, (long long)(t * rate * 1000.0));
    for (int i = 0; i < n; i++) {
        out[i] = (_ce_mt_res53(&st) < density) ? (uint8_t)hi : (uint8_t)lo;
    }
}

static void _ce_chase_strobe_global(double t, double rate, int n, int lo, int hi,
                                    double duty, uint8_t* out)
{
    double phase = _ce_mod1(t * rate);
    uint8_t val = (phase < duty) ? (uint8_t)hi : (uint8_t)lo;
    memset(out, val, (size_t)n);
}

static void _ce_chase_alternate(double t, double rate, int n, int lo, int hi,
                                uint8_t* out)
{
    long long k = (long long)(t * rate);
    int phase = (int)_ce_py_mod_ll(k, 2);
    for (int i = 0; i < n; i++) {
        out[i] = ((i % 2) == phase) ? (uint8_t)hi : (uint8_t)lo;
    }
}

static void _ce_chase_fade(double t, double speed, int n, int lo, int hi,
                           int tail, uint8_t* out)
{
    double head = _ce_fmod_pos(t * speed * (double)n, (double)n);
    for (int i = 0; i < n; i++) {
        double dist = _ce_fmod_pos(head - (double)i, (double)n);
        if (dist < (double)tail) {
            double v = (double)hi * (1.0 - dist / (double)tail);
            int iv = (int)v;              /* Python int() 截断 */
            if (iv < lo) iv = lo;         /* max(lo, int(v)) */
            out[i] = (uint8_t)iv;
        } else {
            out[i] = (uint8_t)lo;
        }
    }
}

static void _ce_chase_bpm(double t, int n, int lo, int hi, double bpm,
                          uint8_t* out)
{
    double bp = 60.0 / bpm;
    long long k = (long long)(t / bp);
    int cur = (int)_ce_py_mod_ll(k, (long long)n);
    for (int i = 0; i < n; i++) out[i] = (i == cur) ? (uint8_t)hi : (uint8_t)lo;
}

static void _ce_chase_valve(double t, double rate, int n, int lo, int hi,
                            int overlap, uint8_t* out)
{
    double period = 1.0 / rate;
    double t_in_p = _ce_fmod_pos(t, period);
    int active = (int)((t_in_p / period) * (double)n);
    for (int i = 0; i < n; i++) {
        int is_on = (i == active) || (overlap != 0 && i == (active + 1) % n);
        out[i] = is_on ? (uint8_t)hi : (uint8_t)lo;
    }
}

static void _ce_chase_motor(double t, double speed, double delay, int n,
                            uint8_t* out, uint8_t* out2)
{
    for (int i = 0; i < n; i++) {
        double t_d = t - (double)i * delay;
        if (t_d < 0.0) t_d = 0.0;         /* max(0.0, t - seq_i*delay) */
        double x = 0.5 + 0.4 * cos(t_d * speed * 2.0 * M_PI);
        double y = 0.5 + 0.4 * sin(t_d * speed * 2.0 * M_PI);
        out[i] = (uint8_t)(int)(x * 255.0);
        out2[i] = (uint8_t)(int)(y * 255.0);
    }
}

CE_API void ce_chase_effect(
    double t, double speed, double rate, double delay,
    int mode, int n,
    int lo, int hi, int width, int tail, int n_colors,
    double density, double duty, double bpm,
    int overlap,
    uint8_t* out, uint8_t* out2, uint8_t* out3)
{
    if (n <= 0) return;
    switch (mode) {
    case 0:  _ce_chase_single(t, speed, n, lo, hi, out); break;
    case 1:  _ce_chase_triple(t, speed, n, lo, hi, width, out); break;
    case 2:  _ce_chase_dual(t, speed, n, lo, hi, width, out); break;
    case 3:  _ce_chase_color(t, speed, n, n_colors, out, out2, out3); break;
    case 4:  _ce_chase_strobe_random(t, rate, n, lo, hi, density, out); break;
    case 5:  _ce_chase_strobe_global(t, rate, n, lo, hi, duty, out); break;
    case 6:  _ce_chase_alternate(t, rate, n, lo, hi, out); break;
    case 7:  _ce_chase_fade(t, speed, n, lo, hi, tail, out); break;
    case 8:  _ce_chase_bpm(t, n, lo, hi, bpm, out); break;
    case 9:  _ce_chase_valve(t, rate, n, lo, hi, overlap, out); break;
    case 10: _ce_chase_motor(t, speed, delay, n, out, out2); break;
    }
}

/* ── T3b: K2 批量并行（73 域一次调用 + OpenMP 按域并行）────────────────
 * 统一连续布局 [n][layer][512]（缺席层 = active 全 0 → 语义等同 NULL）;
 * ltp_time 缺席 = 0.0（Python None→0.0 语义）; clip_t/editor_t 缺席用
 * has_clip_t/has_editor_t 标志（缺席 → 直接覆盖）。
 * 工作量 ~75 万浮点/帧——OpenMP 并行真正有效（此前并行失败根因=工作量太小）。
 */
CE_API void ce_full_mix_batch(
    const uint8_t* base_data, const uint8_t* base_active,
    const uint8_t* shape_data, const uint8_t* shape_active,
    const uint8_t* clip_data, const uint8_t* clip_active,
    const double* ltp_time, int n,
    float shape_gain, const float* direct_anchor,
    const uint8_t* defaults, const uint8_t* default_active,
    const double* clip_t, const double* editor_t,
    int has_clip_t, int has_editor_t,
    uint8_t* out)
{
    int u;
    #pragma omp parallel for schedule(static)
    for (u = 0; u < n; u++) {
        const uint8_t* bd[4]; const uint8_t* ba[4];
        const uint8_t* sd[6]; const uint8_t* sa[6];
        const uint8_t* cd[3]; const uint8_t* ca[3];
        const double* lt[13];
        for (int l = 0; l < 4; l++) {
            bd[l] = base_data + ((size_t)u * 4 + l) * 512;
            ba[l] = base_active + ((size_t)u * 4 + l) * 512;
        }
        for (int l = 0; l < 6; l++) {
            sd[l] = shape_data + ((size_t)u * 6 + l) * 512;
            sa[l] = shape_active + ((size_t)u * 6 + l) * 512;
        }
        for (int l = 0; l < 3; l++) {
            cd[l] = clip_data + ((size_t)u * 3 + l) * 512;
            ca[l] = clip_active + ((size_t)u * 3 + l) * 512;
        }
        for (int l = 0; l < 13; l++)
            lt[l] = ltp_time + ((size_t)u * 13 + l) * 512;
        ce_full_mix(bd, ba, sd, sa, cd, ca, lt, 13,
                    (defaults != NULL) ? defaults + (size_t)u * 512 : NULL,
                    (default_active != NULL) ? default_active + (size_t)u * 512 : NULL,
                    (defaults != NULL) ? 1 : 0,
                    shape_gain,
                    (direct_anchor != NULL) ? direct_anchor + (size_t)u * 512 : NULL,
                    (has_clip_t && clip_t != NULL) ? clip_t + (size_t)u * 512 : NULL,
                    (has_editor_t && editor_t != NULL) ? editor_t + (size_t)u * 512 : NULL,
                    out + (size_t)u * 512);
    }
}

/* ── T5b: flush 73 域批量混合并行（OpenMP 按域并行）────────────────────
 * 背景: flush_universes_now 每帧 73 域串行调用 ce_full_mix
 * （C 内核 ~68µs + Python 包装 ~35µs ≈ 8-11ms/帧）; 域间零共享
 * （每域独立输出 [512], 输入指针只读）→ 天然可并行。
 * T3b ce_full_mix_batch 负优化根因 = Python 组装 13 个连续大缓冲
 * （~2-3ms）> OpenMP 收益——本内核改收指针数组（Python 端层指针缓存
 * 填充 ~1.5-3.5ms, 与串行包装同成本）; 一次 C 调用替代 73 次
 * （省 73 次 Python 包装 ~2.5ms）+ C 内按域并行。
 * 布局: 全部指针数组域行主序 [u*L + l]（缺席 = NULL 元素）:
 *   base_data/base_active [4n]  shape_data/shape_active [6n]
 *   clip_data/clip_active [3n]  ltp_time [13n]（可 NULL）
 *   defaults/default_active [n]  direct_anchor [n]  clip_t [n]  editor_t [n]
 * 每域语义 = ce_full_mix 单域调用（同一内核, 同一指针内容 → 逐位一致）;
 * n<=0 → 无操作。out [n*512] 每域 512 字节全量写出。
 */
CE_API void ce_full_mix_many(
    const uint8_t* const* base_data, const uint8_t* const* base_active,
    const uint8_t* const* shape_data, const uint8_t* const* shape_active,
    const uint8_t* const* clip_data, const uint8_t* const* clip_active,
    const double* const* ltp_time, int n,
    float shape_gain,
    const uint8_t* const* defaults, const uint8_t* const* default_active,
    const float* const* direct_anchor,
    const double* const* clip_t, const double* const* editor_t,
    uint8_t* out)
{
    int u;
    #pragma omp parallel for schedule(static)
    for (u = 0; u < n; u++) {
        const uint8_t* bd[4]; const uint8_t* ba[4];
        const uint8_t* sd[6]; const uint8_t* sa[6];
        const uint8_t* cd[3]; const uint8_t* ca[3];
        const double* lt[13];
        int l;
        for (l = 0; l < 4; l++) {
            bd[l] = base_data[u * 4 + l];
            ba[l] = base_active[u * 4 + l];
        }
        for (l = 0; l < 6; l++) {
            sd[l] = shape_data[u * 6 + l];
            sa[l] = shape_active[u * 6 + l];
        }
        for (l = 0; l < 3; l++) {
            cd[l] = clip_data[u * 3 + l];
            ca[l] = clip_active[u * 3 + l];
        }
        for (l = 0; l < 13; l++)
            lt[l] = (ltp_time != NULL) ? ltp_time[u * 13 + l] : NULL;
        {
            const uint8_t* d = (defaults != NULL) ? defaults[u] : NULL;
            const uint8_t* da = (default_active != NULL) ? default_active[u] : NULL;
            ce_full_mix(bd, ba, sd, sa, cd, ca, lt, 13,
                        d, da, (d != NULL && da != NULL) ? 1 : 0,
                        shape_gain,
                        (direct_anchor != NULL) ? direct_anchor[u] : NULL,
                        (clip_t != NULL) ? clip_t[u] : NULL,
                        (editor_t != NULL) ? editor_t[u] : NULL,
                        out + (size_t)u * 512);
        }
    }
}
CE_API void ce_mix_clips_batch(const float* const* vals,
                               const uint8_t* const* acts,
                               const float* const* anchors,
                               const int* nlays, int n_univ, float mod_gain,
                               uint8_t* out, uint8_t* act_any)
{
#ifdef CE_AVX2
    int u;
    #pragma omp parallel for schedule(static)
    for (u = 0; u < n_univ; u++) {
        ce_mix_clips_avx2(vals[u], acts[u], anchors[u], nlays[u], mod_gain,
                          out + (size_t)u * 512, act_any + (size_t)u * 512);
    }
#else
    int u;
    #pragma omp parallel for schedule(static)
    for (u = 0; u < n_univ; u++) {
        ce_mix_clips(vals[u], acts[u], anchors[u], nlays[u], mod_gain,
                     out + (size_t)u * 512, act_any + (size_t)u * 512);
    }
#endif
}

/* ── 烘焙 u8 层数据混合（vals 直读 u8, 位级 = ce_mix_clips 的 astype 路径）
 * 离线烘焙热路径: 层数据（clip 帧求值输出）为 u8, 现路径 numpy astype→f32
 * 每帧 ~40µs GIL; 本变体 v = (float)raw[c]（0-255 整数在 f32 精确表示,
 * 与 numpy astype 同值）→ 全部下游算术逐位一致。AVX2 变体用 u8 行直接
 * 进 lane（float 转换在寄存器内完成, 与标量同序）。 */
CE_API void ce_mix_clips_u8(const uint8_t* vals, const uint8_t* acts,
                            const float* anchors, int nlay, float mod_gain,
                            uint8_t* out, uint8_t* act_any)
{
    for (int c = 0; c < 512; c++) {
        int pidx = -1, any_a = 0;
        float best_abs = -1.0f, sum_delta = 0.0f, primary_delta, res;
        float sum_active = 0.0f, cnt_active = 0.0f;
        for (int l = 0; l < nlay; l++) {
            const float v = (float)vals[(size_t)l * 512 + c];
            float d;
            if (acts[(size_t)l * 512 + c]) {
                any_a = 1;
                d = v - anchors[(size_t)l * 512 + c];
                sum_active += v;
                cnt_active += 1.0f;
            } else {
                d = 0.0f;
            }
            sum_delta += d;
            if (fabsf(d) > best_abs) {
                best_abs = fabsf(d);
                pidx = l;
            }
        }
        act_any[c] = (uint8_t)any_a;
        if (pidx >= 0 && acts[(size_t)pidx * 512 + c]) {
            primary_delta = (float)vals[(size_t)pidx * 512 + c]
                          - anchors[(size_t)pidx * 512 + c];
            res = (float)vals[(size_t)pidx * 512 + c]
                + (sum_delta - primary_delta) * mod_gain;
        } else if (any_a) {
            res = (cnt_active > 0.0f) ? (sum_active / cnt_active) : 0.0f;
        } else {
            res = 0.0f;
        }
        if (res < 0.0f)
            res = 0.0f;
        else if (res > 255.0f)
            res = 255.0f;
        if (isnan(res))
            res = 0.0f;
        out[c] = (uint8_t)res;
    }
}

/* AVX2 u8 变体: 每 lane 一通道, v = (float)raw[c]（u8→f32 精确）;
 * 与 ce_mix_clips_avx2 同结构: 通道内累加同序, 8 lane 并行仅消通道间串行 →
 * 与标量 u8 版逐位相同。 */
#ifdef CE_AVX2
static void ce_mix_clips_u8_avx2(const uint8_t* vals, const uint8_t* acts,
                                 const float* anchors, int nlay,
                                 float mod_gain, uint8_t* out,
                                 uint8_t* act_any)
{
    const __m256 ones = _mm256_set1_ps(1.0f);
    const __m256 zeros = _mm256_setzero_ps();
    const __m256 mone = _mm256_set1_ps(-1.0f);
    const __m256 hi = _mm256_set1_ps(255.0f);
    const __m256 signmask = _mm256_set1_ps(-0.0f);
    const __m256i neg_one = _mm256_set1_epi32(-1);

    for (int c0 = 0; c0 < 512; c0 += 8) {
        __m256 sum_delta = zeros, best_abs = mone;
        __m256 sum_active = zeros, cnt_active = zeros;
        __m256i pidx = neg_one, any_a = _mm256_setzero_si256();

        for (int l = 0; l < nlay; l++) {
            const uint8_t* vp = vals + (size_t)l * 512 + c0;
            const uint8_t* ap = acts + (size_t)l * 512 + c0;
            const float* np = anchors + (size_t)l * 512 + c0;

            /* u8 8 字节 → 8×f32（精确转换, = (float)raw[c]） */
            __m256 v = _mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(
                _mm_loadl_epi64((const __m128i*)vp)));
            __m256 d = _mm256_sub_ps(v, _mm256_loadu_ps(np));
            __m128i b8 = _mm_loadl_epi64((const __m128i*)ap);
            __m256i act32 = _mm256_cvtepu8_epi32(b8);
            __m256i maski = _mm256_andnot_si256(_mm256_cmpeq_epi32(act32, _mm256_set1_epi32(0)), _mm256_set1_epi32(-1));
            __m256 mask = _mm256_castsi256_ps(maski);

            any_a = _mm256_or_si256(any_a, maski);
            d = _mm256_and_ps(d, mask);
            sum_delta = _mm256_add_ps(sum_delta, d);
            sum_active = _mm256_add_ps(sum_active, _mm256_and_ps(v, mask));
            cnt_active = _mm256_add_ps(cnt_active, _mm256_and_ps(ones, mask));

            __m256 ad = _mm256_andnot_ps(signmask, d);
            __m256 gt = _mm256_cmp_ps(ad, best_abs, _CMP_GT_OQ);
            best_abs = _mm256_blendv_ps(best_abs, ad, gt);
            pidx = _mm256_castps_si256(
                _mm256_blendv_ps(_mm256_castsi256_ps(pidx),
                                 _mm256_castsi256_ps(_mm256_set1_epi32(l)), gt));
        }

        float sdl[8], sa[8], cnt[8];
        int pidx_l[8], any_l[8];
        _mm256_storeu_ps(sdl, sum_delta);
        _mm256_storeu_ps(sa, sum_active);
        _mm256_storeu_ps(cnt, cnt_active);
        _mm256_storeu_si256((__m256i*)pidx_l, pidx);
        _mm256_storeu_si256((__m256i*)any_l, any_a);

        for (int j = 0; j < 8; j++) {
            int c = c0 + j;
            float res;
            if (pidx_l[j] >= 0 && acts[(size_t)pidx_l[j] * 512 + c]) {
                float pd = (float)vals[(size_t)pidx_l[j] * 512 + c]
                         - anchors[(size_t)pidx_l[j] * 512 + c];
                res = (float)vals[(size_t)pidx_l[j] * 512 + c]
                    + (sdl[j] - pd) * mod_gain;
            } else if (any_l[j]) {
                res = (cnt[j] > 0.0f) ? (sa[j] / cnt[j]) : 0.0f;
            } else {
                res = 0.0f;
            }
            if (res < 0.0f)
                res = 0.0f;
            else if (res > 255.0f)
                res = 255.0f;
            if (isnan(res))
                res = 0.0f;
            out[c] = (uint8_t)res;
            act_any[c] = (uint8_t)(any_l[j] != 0);
        }
    }
}
#endif /* CE_AVX2 */

/* 批量版（同 ce_mix_clips_batch 布局: 指针数组逐域; AVX2 DLL 走 u8 AVX2
 * 内核, SSE2 DLL 走标量） */
CE_API void ce_mix_clips_u8_batch(const uint8_t* const* vals,
                                  const uint8_t* const* acts,
                                  const float* const* anchors,
                                  const int* nlays, int n_univ,
                                  float mod_gain,
                                  uint8_t* out, uint8_t* act_any)
{
    int u;
    #pragma omp parallel for schedule(static)
    for (u = 0; u < n_univ; u++) {
#ifdef CE_AVX2
        ce_mix_clips_u8_avx2(vals[u], acts[u], anchors[u], nlays[u],
                             mod_gain, out + (size_t)u * 512,
                             act_any + (size_t)u * 512);
#else
        ce_mix_clips_u8(vals[u], acts[u], anchors[u], nlays[u], mod_gain,
                        out + (size_t)u * 512, act_any + (size_t)u * 512);
#endif
    }
}

/* ── T5: 候选解析（逐位对齐 PlaybackEngine._resolve_candidate_layers,
 *       render_path.py 候选解析段 :434-455 的 isfinite 缓存版）
 *
 * 一域 n 层候选（每层 512 通道）→ 逐通道最佳分 + 有效掩码 + 有效数据:
 *   1. best[c] 初值 -inf；仅 active & isfinite 通道参与取大
 *      （等价 np.maximum(best, scores, out=best, where=mask) —
 *       相等值时保持 best 原值，含 ±0.0 位型一致）
 *   2. effective = active & isfinite(scores) & (scores >= best - 1e-12)
 *      （f64 逐字计算：best=-inf 时 best-1e-12=-inf，与 numpy 广播一致）
 *   3. data_eff = data 逐字节拷贝后非有效通道清零
 *      （Python 层对无有效通道的层直接 continue——对应 effective_counts[l]==0，
 *        此时本内核写出全 0 掩码/数据，与“全清零结果未物化”一致）
 *
 * 布局: 全部 row-major [n][512]（层为主，与 ce_full_mix_batch 同款）;
 * n<=0 → 直接返回不写任何输出。NaN/±inf 由 isfinite 排除，永不竞争、永不影响 best。
 *
 * 输入:
 *   data    u8  [n*512]  各层原始数据
 *   active  u8  [n*512]  0/1 激活（非零视为激活，同 numpy bool 转换）
 *   scores  f64 [n*512]  候选分数（含 -inf/+inf/NaN 由语义排除）
 * 输出:
 *   effective     u8  [n*512]  0/1 有效掩码
 *   data_eff      u8  [n*512]  有效数据（非有效通道清零）
 *   effective_counts int [n]   每层有效通道数（0 → 调用方跳过该层）
 *   best_out      f64 [512] 或 NULL  逐通道最佳分（未参与通道保持 -inf）
 */
/* 单域解析内核体（ce_resolve_candidates 与 ce_resolve_batch 共用同一实现
 * → 批量版逐域与标量版逐位一致; 变更需两侧同构） */
static void ce_resolve_one(int n,
                           const uint8_t* data,
                           const uint8_t* active,
                           const double* scores,
                           uint8_t* effective,
                           uint8_t* data_eff,
                           int* effective_counts,
                           double* best_out)
{
    double best[512];
    int l, c;
    if (n <= 0)
        return;
    for (c = 0; c < 512; c++)
        best[c] = -INFINITY;
    /* 第 1 遍: 逐层 np.maximum(best, scores, where=active & isfinite) */
    for (l = 0; l < n; l++) {
        const uint8_t* act = active + (size_t)l * 512;
        const double* sc = scores + (size_t)l * 512;
        for (c = 0; c < 512; c++) {
            if (act[c] && isfinite(sc[c]) && sc[c] > best[c])
                best[c] = sc[c];
        }
    }
    /* 第 2 遍: effective 掩码 + data_eff 拷贝清零 + 有效计数 */
    for (l = 0; l < n; l++) {
        const uint8_t* dt = data + (size_t)l * 512;
        const uint8_t* act = active + (size_t)l * 512;
        const double* sc = scores + (size_t)l * 512;
        uint8_t* eff = effective + (size_t)l * 512;
        uint8_t* de = data_eff + (size_t)l * 512;
        int cnt = 0;
        for (c = 0; c < 512; c++) {
            uint8_t e;
            if (act[c] && isfinite(sc[c]) && sc[c] >= best[c] - 1e-12)
                e = 1;
            else
                e = 0;
            eff[c] = e;
            de[c] = e ? dt[c] : 0;
            cnt += e;
        }
        effective_counts[l] = cnt;
    }
    if (best_out != NULL) {
        for (c = 0; c < 512; c++)
            best_out[c] = best[c];
    }
}

CE_API void ce_resolve_candidates(
    int n,
    const uint8_t* data,
    const uint8_t* active,
    const double* scores,
    uint8_t* effective,
    uint8_t* data_eff,
    int* effective_counts,
    double* best_out)
{
    ce_resolve_one(n, data, active, scores, effective, data_eff,
                   effective_counts, best_out);
}

/* ── 烘焙批量候选解析: 一帧全部域一次调用（离线烘焙热路径, §3.2 帧维分区;
 * Python 侧每帧 73 次标量 ctypes 调用 (~0.2ms GIL) → 本内核 1 次调用。
 * 逐域 = ce_resolve_candidates 同实现（ce_resolve_one）→ 逐位一致。
 *
 * ties: double [total] 每行 priority tie（引擎 scores[act] += tie 语义:
 * 仅 active & 参与比较的行内值加 tie; f64 加法与 numpy 逐位一致）。
 *
 * 布局（烘焙 worker 的池布局）: 全部域按任务行序连续排布——
 *   data/active/scores  [total*512], total = Σ counts[u];
 *   域 u 的候选行 = [starts[u] + l]（l ∈ [0, counts[u])）;
 * effective/data_eff/effective_counts 同布局同偏移写出。
 * starts: int [n_univ] 每域起始行（前缀和）; counts: int [n_univ] 每域层数。 */
CE_API void ce_resolve_batch(
    const int* starts, const int* counts, int n_univ,
    const double* ties,
    const uint8_t* data, const uint8_t* active, const double* scores,
    uint8_t* effective, uint8_t* data_eff, int* effective_counts)
{
    int u;
    for (u = 0; u < n_univ; u++) {
        int s = starts[u];
        int n = counts[u];
        if (n <= 0)
            continue;
        /* 带 tie 的逐域解析: best/effective 全部基于 (scores[c] + tie[row]);
         * 非 active 行不加 tie（同引擎 scores[act] += tie）; 语义 =
         * ce_resolve_one(ties 逐行加后) 逐位一致。 */
        double best[512];
        int l, c;
        for (c = 0; c < 512; c++)
            best[c] = -INFINITY;
        for (l = 0; l < n; l++) {
            const uint8_t* act = active + (size_t)(s + l) * 512;
            const double* sc = scores + (size_t)(s + l) * 512;
            double t = ties[s + l];
            for (c = 0; c < 512; c++) {
                double v;
                if (!act[c])
                    continue;
                v = sc[c] + t;
                if (isfinite(v) && v > best[c])
                    best[c] = v;
            }
        }
        for (l = 0; l < n; l++) {
            const uint8_t* dt = data + (size_t)(s + l) * 512;
            const uint8_t* act = active + (size_t)(s + l) * 512;
            const double* sc = scores + (size_t)(s + l) * 512;
            uint8_t* eff = effective + (size_t)(s + l) * 512;
            uint8_t* de = data_eff + (size_t)(s + l) * 512;
            double t = ties[s + l];
            int cnt = 0;
            for (c = 0; c < 512; c++) {
                uint8_t e;
                double v = sc[c] + t;
                if (act[c] && isfinite(v) && v >= best[c] - 1e-12)
                    e = 1;
                else
                    e = 0;
                eff[c] = e;
                de[c] = e ? dt[c] : 0;
                cnt += e;
            }
            effective_counts[s + l] = cnt;
        }
    }
}

/* ── T5-9: 帧级融合候选解析（P6 批量路径「消费循环+候选解析」一次 C 调用）
 *
 * 现状（11382 台 × 146 候选层实测）: 消费循环 4.6ms + 逐 universe 解析 9.4ms
 * （73 次 ctypes + 每次 np.stack×3 共 3.5ms + C 内核 73×45-68µs ≈ 4.5ms）。
 * 本内核一次调用完成全部域的:
 *   1. act'[c] = act[c] && rule[c] != 0        （规则字节非零即真, 同
 *      np.asarray(rule, dtype=bool) & act —— 非 a&r 位与, 防 rule 非 0/1）
 *   2. sc[c]   = scores[c] + tie               （f64 同序加法; 仅 act' 通道参与）
 *   3. best[c] = max(act' & isfinite(sc))      —— 严格 >, 等值保持旧值
 *      （np.maximum(best, scores, out=best, where=mask) 同语义; -inf 初值）
 *   4. eff[c]  = act' & isfinite(sc) & (sc >= best[c] - 1e-12)
 *      （f64 逐字: best=-inf → -inf-1e-12=-inf, 与 numpy 广播一致）
 *   5. de[c]   = eff[c] ? data[c] : 0;  count += eff[c]
 * 与旧链（_ce_consume_batch_rows + ce_resolve_candidates）逐位一致:
 *   旧链候选构建 scores[act'] += tie（fancy index 同序 f64 加法）后
 *   ce_resolve_one 的 best/eff/de 语义 —— 本内核把「tie 叠加 + 规则过滤 +
 *   best + eff + de + 计数」全部合入两遍扫描, 数学顺序不变。
 *
 * 布局: data/act/scores 为 P6 批量池 [n_batch*512] 行主序（ce_render_frame
 *   原样直读, 零中间复制）; flat_idx [n_total] 组序任务 → 池行号; 组 = universe
 *   （首现序, 组内 = batch 扫描序 —— 与旧链 candidate_clip_layers 插入序一致）;
 *   ties [n_total] 每行 priority tie（0.0 语义同不加）;
 *   rules [n_total] 每行规则指针（整体 NULL 或元素 NULL = 无规则过滤）;
 *   effective/data_eff [n_total*512] 组序（行 = group_off[g]+j, 与 flat_idx
 *   平行）; counts [n_total]。
 * 边界: n_groups<=0 → 无操作; 组大小 0 → 跳过; flat_idx 越界为调用方契约;
 *   NaN/±inf 由 isfinite 排除, 永不竞争、永不影响 best。
 * 层跳过: count==0 的层调用方不 append（Python 端 continue 语义）——本内核
 *   照写全 0 掩码/数据（与"全清零结果未物化"一致）。
 * 变体: AVX2 DLL（-DCE_AVX2）内 8-lane 通道并行——每 lane 独立通道, 通道内
 *   浮点运算顺序与标量同 → 逐位一致（ce_mix_clips_avx2 同纪律）。 */
static void ce_resolve_many_group_scalar(
    const int* idxs, int k,
    const uint8_t* data, const uint8_t* act, const double* scores,
    const double* ties, const uint8_t* const* rules,
    uint8_t* effective, uint8_t* data_eff, int* counts)
{
    double best[512];
    int j, c;
    if (k <= 0)
        return;
    for (c = 0; c < 512; c++)
        best[c] = -INFINITY;
    /* 遍 1: best —— sc = scores + tie（仅 act' 通道）。 */
    for (j = 0; j < k; j++) {
        const size_t bi = (size_t)idxs[j];
        const uint8_t* a = act + bi * 512;
        const uint8_t* r = (rules != NULL) ? rules[j] : NULL;
        const double* sc0 = scores + bi * 512;
        const double tie = (ties != NULL) ? ties[j] : 0.0;
        for (c = 0; c < 512; c++) {
            double v;
            if ((r != NULL) ? (a[c] && r[c] != 0) : a[c]) {
                v = sc0[c] + tie;
                if (isfinite(v) && v > best[c])
                    best[c] = v;
            }
        }
    }
    /* 遍 2: eff + de + count */
    for (j = 0; j < k; j++) {
        const size_t bi = (size_t)idxs[j];
        const uint8_t* dt = data + bi * 512;
        const uint8_t* a = act + bi * 512;
        const uint8_t* r = (rules != NULL) ? rules[j] : NULL;
        const double* sc0 = scores + bi * 512;
        const double tie = (ties != NULL) ? ties[j] : 0.0;
        uint8_t* eff = effective + (size_t)j * 512;
        uint8_t* de = data_eff + (size_t)j * 512;
        int cnt = 0;
        for (c = 0; c < 512; c++) {
            uint8_t e = 0;
            if ((r != NULL) ? (a[c] && r[c] != 0) : a[c]) {
                const double v = sc0[c] + tie;
                if (isfinite(v) && v >= best[c] - 1e-12)
                    e = 1;
            }
            eff[c] = e;
            de[c] = e ? dt[c] : 0;
            cnt += e;
        }
        counts[j] = cnt;
    }
}

#ifdef CE_AVX2
/* 8-lane AVX2 变体（每 lane 一通道; 通道内运算顺序与标量逐位一致）。
 * 掩码纪律: act/rule 掩码先扩展为 64-bit lane（cvtepu8→i32→epi64 零扩展 +
 *   64-bit cmpeq → 全 1/全 0 掩码）, 与 infm/比较掩码同宽后再 OR——
 *   blendv/movemask 按符号位取掩码, 混宽会产生半 1 掩码（错误）。
 * 结构: 每迭代 8 通道 = 2 个 4-lane __m256d（sv0=ch0-3, sv1=ch4-7）;
 *   movemask_pd 每向量取 4 位（bad 掩码）→ 拼 8 位 eff → 字节写出 + 查表计数。
 * 计数查表 _ce_rpop8（不依赖 POPCNT 指令, 与标量 cnt 逐位一致）。 */
static const unsigned char _ce_rpop8[256] = {
    0,1,1,2,1,2,2,3,1,2,2,3,2,3,3,4,1,2,2,3,2,3,3,4,2,3,3,4,3,4,4,5,
    1,2,2,3,2,3,3,4,2,3,3,4,3,4,4,5,2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,
    1,2,2,3,2,3,3,4,2,3,3,4,3,4,4,5,2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,3,4,4,5,4,5,5,6,4,5,5,6,5,6,6,7,
    1,2,2,3,2,3,3,4,2,3,3,4,3,4,4,5,2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,3,4,4,5,4,5,5,6,4,5,5,6,5,6,6,7,
    2,3,3,4,3,4,4,5,3,4,4,5,4,5,5,6,3,4,4,5,4,5,5,6,4,5,5,6,5,6,6,7,
    3,4,4,5,4,5,5,6,4,5,5,6,5,6,6,7,4,5,5,6,5,6,6,7,5,6,6,7,6,7,7,8,
};

static void ce_resolve_many_group_avx2(
    const int* idxs, int k,
    const uint8_t* data, const uint8_t* act, const double* scores,
    const double* ties, const uint8_t* const* rules,
    uint8_t* effective, uint8_t* data_eff, int* counts)
{
    const __m256i expmask = _mm256_set1_epi64x(0x7FF0000000000000LL);
    const __m256d m12 = _mm256_set1_pd(-1e-12);
    const __m256d ninf = _mm256_set1_pd(-INFINITY);
    const __m256i ones64 = _mm256_set1_epi64x(-1LL);
    const __m256i zeroi = _mm256_setzero_si256();
    int j, c0;
    if (k <= 0)
        return;
    for (j = 0; j < k; j++)
        counts[j] = 0;      /* 遍 2 按 c0 段累加, 先清零 */
    for (c0 = 0; c0 < 512; c0 += 8) {
        __m256d best0 = ninf, best1 = ninf;
        for (j = 0; j < k; j++) {
            const size_t bi = (size_t)idxs[j];
            const uint8_t* a = act + bi * 512 + c0;
            const uint8_t* r = (rules != NULL && rules[j] != NULL) ? rules[j] + c0 : NULL;
            const double* sc0 = scores + bi * 512 + c0;
            const __m256d tiev = (ties != NULL) ? _mm256_set1_pd(ties[j])
                                                 : _mm256_setzero_pd();
            /* act' 非激活掩码: 通道值 == 0 → 全 1（rule 非零即真并入） */
            __m128i a8 = _mm_loadl_epi64((const __m128i*)a);
            __m256i am0 = _mm256_cmpeq_epi64(
                _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(a8)), zeroi);
            __m256i am1 = _mm256_cmpeq_epi64(
                _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(_mm_srli_si128(a8, 4))),
                zeroi);
            if (r != NULL) {
                __m128i r8 = _mm_loadl_epi64((const __m128i*)r);
                __m256i rm0 = _mm256_cmpeq_epi64(
                    _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(r8)), zeroi);
                __m256i rm1 = _mm256_cmpeq_epi64(
                    _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(_mm_srli_si128(r8, 4))),
                    zeroi);
                am0 = _mm256_or_si256(am0, rm0);
                am1 = _mm256_or_si256(am1, rm1);
            }
            __m256d sv0 = _mm256_add_pd(_mm256_loadu_pd(sc0), tiev);
            __m256d sv1 = _mm256_add_pd(_mm256_loadu_pd(sc0 + 4), tiev);
            /* isfinite: 指数位全 1 = NaN/±inf → 并入非激活 */
            __m256i in0 = _mm256_cmpeq_epi64(
                _mm256_and_si256(_mm256_castpd_si256(sv0), expmask), expmask);
            __m256i in1 = _mm256_cmpeq_epi64(
                _mm256_and_si256(_mm256_castpd_si256(sv1), expmask), expmask);
            /* take = act'==1 & 有限 & (sc > best) — blendv 按符号位 */
            __m256i take0 = _mm256_andnot_si256(
                _mm256_or_si256(am0, in0),
                _mm256_castpd_si256(_mm256_cmp_pd(sv0, best0, _CMP_GT_OQ)));
            __m256i take1 = _mm256_andnot_si256(
                _mm256_or_si256(am1, in1),
                _mm256_castpd_si256(_mm256_cmp_pd(sv1, best1, _CMP_GT_OQ)));
            best0 = _mm256_blendv_pd(best0, sv0, _mm256_castsi256_pd(take0));
            best1 = _mm256_blendv_pd(best1, sv1, _mm256_castsi256_pd(take1));
        }
        const __m256d bm0 = _mm256_add_pd(best0, m12);
        const __m256d bm1 = _mm256_add_pd(best1, m12);
        /* 遍 2: 8 通道 bad 掩码 → eff 位 → 字节写出 + 数据拷贝 + 计数 */
        for (j = 0; j < k; j++) {
            const size_t bi = (size_t)idxs[j];
            const uint8_t* dt = data + bi * 512 + c0;
            const uint8_t* a = act + bi * 512 + c0;
            const uint8_t* r = (rules != NULL && rules[j] != NULL) ? rules[j] + c0 : NULL;
            const double* sc0 = scores + bi * 512 + c0;
            const __m256d tiev = (ties != NULL) ? _mm256_set1_pd(ties[j])
                                                 : _mm256_setzero_pd();
            uint8_t* eff = effective + (size_t)j * 512 + c0;
            uint8_t* de = data_eff + (size_t)j * 512 + c0;
            __m128i a8 = _mm_loadl_epi64((const __m128i*)a);
            __m256i am0 = _mm256_cmpeq_epi64(
                _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(a8)), zeroi);
            __m256i am1 = _mm256_cmpeq_epi64(
                _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(_mm_srli_si128(a8, 4))),
                zeroi);
            if (r != NULL) {
                __m128i r8 = _mm_loadl_epi64((const __m128i*)r);
                __m256i rm0 = _mm256_cmpeq_epi64(
                    _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(r8)), zeroi);
                __m256i rm1 = _mm256_cmpeq_epi64(
                    _mm256_cvtepi32_epi64(_mm_cvtepu8_epi32(_mm_srli_si128(r8, 4))),
                    zeroi);
                am0 = _mm256_or_si256(am0, rm0);
                am1 = _mm256_or_si256(am1, rm1);
            }
            __m256d sv0 = _mm256_add_pd(_mm256_loadu_pd(sc0), tiev);
            __m256d sv1 = _mm256_add_pd(_mm256_loadu_pd(sc0 + 4), tiev);
            __m256i in0 = _mm256_cmpeq_epi64(
                _mm256_and_si256(_mm256_castpd_si256(sv0), expmask), expmask);
            __m256i in1 = _mm256_cmpeq_epi64(
                _mm256_and_si256(_mm256_castpd_si256(sv1), expmask), expmask);
            /* bad = 非激活 | 非有限 | (sc < best-1e-12) — 64-bit 全 1 掩码 */
            __m256i bad0 = _mm256_or_si256(
                _mm256_or_si256(am0, in0),
                _mm256_andnot_si256(
                    _mm256_castpd_si256(_mm256_cmp_pd(sv0, bm0, _CMP_GE_OQ)),
                    ones64));
            __m256i bad1 = _mm256_or_si256(
                _mm256_or_si256(am1, in1),
                _mm256_andnot_si256(
                    _mm256_castpd_si256(_mm256_cmp_pd(sv1, bm1, _CMP_GE_OQ)),
                    ones64));
            /* 注意: ~ 必须先取反再 & 0xF —— ~(m&0xF) 的符号扩展会污染低字节
             * 高半位（~0xF = 0xFFFFFFF0, 低字节 0xF0 的 4-7 位为 1 → 高半
             * 通道误判有效）。正确顺序: (~m & 0xF)。 */
            int bits = ((~(int)_mm256_movemask_pd(_mm256_castsi256_pd(bad0)) & 0xF)
                     | ((~(int)_mm256_movemask_pd(_mm256_castsi256_pd(bad1)) & 0xF) << 4));
            bits &= 0xFF;
            /* 8 位掩码 → 8 字节 0/1; 数据拷贝与标量 de = e ? dt : 0 逐字节一致 */
            for (int k2 = 0; k2 < 8; k2++) {
                const uint8_t e = (uint8_t)((bits >> k2) & 1);
                eff[k2] = e;
                de[k2] = (e != 0) ? dt[k2] : 0;
            }
            counts[j] += (int)_ce_rpop8[(unsigned)bits];
        }
    }
}
#endif /* CE_AVX2 */

CE_API void ce_resolve_candidates_many(
    int n_groups,
    const int* group_off,
    const int* flat_idx,
    const uint8_t* data,
    const uint8_t* act,
    const double* scores,
    const double* ties,
    const uint8_t* const* rules,
    uint8_t* effective,
    uint8_t* data_eff,
    int* counts)
{
    int g;
    if (n_groups <= 0)
        return;
    for (g = 0; g < n_groups; g++) {
        const int k = group_off[g + 1] - group_off[g];
        if (k <= 0)
            continue;
        const int* idxs = flat_idx + group_off[g];
#ifdef CE_AVX2
        ce_resolve_many_group_avx2(
            idxs, k, data, act, scores,
            (ties != NULL) ? ties + group_off[g] : NULL,
            (rules != NULL) ? rules + group_off[g] : NULL,
            effective + (size_t)group_off[g] * 512,
            data_eff + (size_t)group_off[g] * 512,
            counts + group_off[g]);
#else
        ce_resolve_many_group_scalar(
            idxs, k, data, act, scores,
            (ties != NULL) ? ties + group_off[g] : NULL,
            (rules != NULL) ? rules + group_off[g] : NULL,
            effective + (size_t)group_off[g] * 512,
            data_eff + (size_t)group_off[g] * 512,
            counts + group_off[g]);
#endif
    }
}

/* ── A3: 全融合帧内核 — 消费+解析+混合+打包 一次 C 调用 ────────────────
 * 现状（11382 台实测）: P6 批量渲染 → Python 消费循环 → 融合解析 →
 * 逐域 _mix_clips, 每帧 ~300 次 numpy 中间调用 + 73 次 Cython 内核 +
 * 多段 ctypes 调用。本内核 1 次 ctypes 调用完成全链:
 *   1. 渲染: ce_render_clip_frame 每任务（数据从 ctx 槽取; OpenMP 按任务
 *      并行, 同 ce_render_frame —— 每任务独立输出行, 只读 ctx → 位级等价）
 *   2. 解析: 组 = universe 首现序, 组内 = 任务扫描序（同旧链候选插入序）;
 *      ce_resolve_candidates_many 逻辑（ce_resolve_many_group_* 原样复用,
 *      rule 从 ctx 槽取, tie 逐任务传入 —— 与 _ce_consume_resolve_many
 *      的 ties_l 同值, 逐位一致）
 *   3. 锚点: 注册期已算入 ctx 槽（ce_clip_register 内, act' = base_active &
 *      rule, 与 _ce_get_clip_anchor 同输入 → 同输出）
 *   4. 混合: 组内 count>0 层压实（组内序 = 旧链候选序）→ ce_mix_clips_u8
 *      （u8 直读 = astype(f32) 位级等价）; mod_gain 传参（同 _mix_clips）
 *   5. 打包: 域主序连续缓冲 univ_out/univ_act [n_ok*1024]（每域
 *      out[512]+act[512] 相邻, 组序）; univ_off[u] = 域 u 字节偏移或 -1。
 * 契约:
 *   - univ_off 长度 ≥ max(universe)+1, 调用方必须预初始化为 -1 —— 内核
 *     执行期复用作 universe→组映射（-1 哨兵 = 未出现）, 结束时改写为
 *     字节偏移（组序 ×1024）; universe<0 的任务跳过。
 *   - ties [n_tasks] 任务序 priority tie（NULL/0.0 = 不加）;
 *   - rule 从 ctx 槽取（无注册规则 = 无过滤）;
 *   - n_tasks<=0 / ctx 缺失 → 返回 0; univ_off NULL / scratch 分配失败
 *     → 返回 -1（调用方回退旧链, H1 计数）。
 * 返回: 有输出域数（univ_off[u] >= 0 的 u 数）。
 * 位级等价: 渲染/解析/混合全部复用既有内核（各自已 10 万次 fuzz 对拍）,
 * 层序/域序与旧链一致; 本内核只做调度与拷贝, 无任何重新求值。 */
static int _ce_fused_ensure(ce_ctx* ctx, int n)
{
    int cap = ctx->scr.cap;
    if (n <= cap)
        return 0;
    cap = n + 16;
    size_t bytes =
        (size_t)cap * 512 * 6          /* out act eff de lde lef: 6×u8×512 */
      + (size_t)cap * 512 * 8          /* sc: f64 */
      + (size_t)cap * 512 * 4          /* lan: f32 */
      + (size_t)cap * 1                /* skip */
      + (size_t)cap * 1                /* corner: u8 (K2) */
      + (size_t)cap * 512 * 4          /* ca: f32 (K2 角锚点表) */
      + (size_t)cap * 4 * 5            /* cnts flat gcnt gpos guniv */
      + (size_t)(cap + 1) * 4          /* goff */
      + (size_t)cap * 8                /* ties f64 */
      + (size_t)cap * sizeof(uint8_t*) /* rules */
      + (size_t)cap * 512              /* ltscr (K6 LT 中间帧) */
      + (size_t)cap * 512 * 4          /* dgains (K7 方向任务增益) */
      + (size_t)cap * 512 * 4          /* dgtmp (K7 路径B临时) */
      + (size_t)cap * 512              /* dscr (K7 raw 物化行) */
      + (size_t)cap * 512;             /* dcmask (K7 完成掩码) */
    uint8_t* blk = (uint8_t*)malloc(bytes);
    if (blk == NULL)
        return -1;
    free(ctx->scr_block);              /* 旧块释放（无引用在途: 单提交者） */
    ctx->scr_block = blk;
    ce_fused_scratch* s = &ctx->scr;
    size_t off = 0;
    s->out   = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->act   = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->eff   = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->de    = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->lde   = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->lef   = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->lan   = (float*)(blk + off);    off += (size_t)cap * 512 * 4;
    s->sc    = (double*)(blk + off);   off += (size_t)cap * 512 * 8;
    s->skip  = (uint8_t*)(blk + off);  off += (size_t)cap * 1;
    s->corner = (uint8_t*)(blk + off); off += (size_t)cap * 1;
    s->ca    = (float*)(blk + off);    off += (size_t)cap * 512 * 4;
    s->cnts  = (int*)(blk + off);      off += (size_t)cap * 4;
    s->flat  = (int*)(blk + off);      off += (size_t)cap * 4;
    s->gcnt  = (int*)(blk + off);      off += (size_t)cap * 4;
    s->gpos  = (int*)(blk + off);      off += (size_t)cap * 4;
    s->guniv = (int*)(blk + off);      off += (size_t)cap * 4;
    s->goff  = (int*)(blk + off);      off += (size_t)(cap + 1) * 4;
    s->ties  = (double*)(blk + off);   off += (size_t)cap * 8;
    s->rules = (const uint8_t**)(blk + off);
    off += (size_t)cap * sizeof(uint8_t*);
    s->ltscr = (uint8_t*)(blk + off);  off += (size_t)cap * 512;
    s->dgains = (float*)(blk + off);   off += (size_t)cap * 512 * 4;
    s->dgtmp  = (float*)(blk + off);   off += (size_t)cap * 512 * 4;
    s->dscr   = (uint8_t*)(blk + off); off += (size_t)cap * 512;
    s->dcmask = (uint8_t*)(blk + off); off += (size_t)cap * 512;
    s->cap = cap;
    return 0;
}

CE_API int ce_render_frame_fused(
    void* vctx,
    const int* clip_idx, const int* universe,
    const double* source_t, const double* start_t, const double* rate,
    const double* timeline_now, const int* hold, const float* gain,
    const int* fade_mode, int n_tasks,
    const double* ties,
    double mod_gain,
    uint8_t* univ_out, uint8_t* univ_act, int* univ_off)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    int t, g, ok_univ = 0;
    if (ctx == NULL || n_tasks <= 0)
        return 0;
    if (univ_off == NULL)
        return -1;
    if (_ce_fused_ensure(ctx, n_tasks) < 0)
        return -1;
    ce_fused_scratch* s = &ctx->scr;
    const float mg = (float)mod_gain;

    /* ── 1) 渲染: ce_render_clip_frame 每任务（OpenMP 按任务并行, 同
     * ce_render_frame; 每任务独立输出行 + 只读 ctx → 位级等价）────── */
    #pragma omp parallel for schedule(static)
    for (t = 0; t < n_tasks; t++) {
        int ci = clip_idx[t];
        s->skip[t] = 1;
        if (ci < 0 || ci >= ctx->clip_count || ctx->clips[ci].frame_times == NULL)
            continue;
        ce_clip_entry* e = &ctx->clips[ci];
        int idx = ce_render_clip_frame(
            e->frame_times, e->n_frames, e->frame_data, e->last_change,
            source_t[t], start_t[t], rate[t], timeline_now[t],
            hold[t], gain[t], e->base_active, fade_mode[t],
            e->prev, e->target,
            s->out + (size_t)t * 512, s->act + (size_t)t * 512,
            s->sc + (size_t)t * 512);
        if (idx >= 0)
            s->skip[t] = 0;
    }

    /* ── 2) 组构建: universe 首现序, 组内 = 任务扫描序 ─────────────
     * univ_off[u] 执行期 = u→组下标（初值 -1 由调用方预置, -1 哨兵
     * 与组下标 0..n-1 不冲突）; 结束时改写为字节偏移。 */
    int n_groups = 0;
    for (t = 0; t < n_tasks; t++) {
        if (s->skip[t])
            continue;
        int u = universe[t];
        if (u < 0)
            continue;
        int g2 = univ_off[u];
        if (g2 < 0) {
            g2 = n_groups++;
            univ_off[u] = g2;
            s->guniv[g2] = u;
            s->gcnt[g2] = 0;
        }
        s->gcnt[g2]++;
    }
    if (n_groups <= 0)
        return 0;
    s->goff[0] = 0;
    for (g = 0; g < n_groups; g++)
        s->goff[g + 1] = s->goff[g] + s->gcnt[g];
    for (g = 0; g < n_groups; g++)
        s->gpos[g] = s->goff[g];
    /* 填 flat_idx + 组序 tie / rule 指针 */
    for (t = 0; t < n_tasks; t++) {
        if (s->skip[t])
            continue;
        int u = universe[t];
        if (u < 0)
            continue;
        int g2 = univ_off[u];
        int pos = s->gpos[g2]++;
        s->flat[pos] = t;
        s->ties[pos] = (ties != NULL) ? ties[t] : 0.0;
        int ci = clip_idx[t];
        ce_clip_entry* e = &ctx->clips[ci];
        s->rules[pos] = (e->has_rule && e->rule_active != NULL)
            ? e->rule_active : NULL;
    }

    /* ── 3) 每域: 解析（复用 many 内核）→ 压实 → 混合 → 打包 ────── */
    int layer_pos = 0;   /* 全组累计压实层数（混合层缓冲连续区） */
    for (g = 0; g < n_groups; g++) {
        int k = s->goff[g + 1] - s->goff[g];
        const int* idxs = s->flat + s->goff[g];
        const int u = s->guniv[g];
        if (k > 0) {
#ifdef CE_AVX2
            ce_resolve_many_group_avx2(
                idxs, k, s->out, s->act, s->sc,
                s->ties + s->goff[g], s->rules + s->goff[g],
                s->eff + (size_t)s->goff[g] * 512,
                s->de + (size_t)s->goff[g] * 512,
                s->cnts + s->goff[g]);
#else
            ce_resolve_many_group_scalar(
                idxs, k, s->out, s->act, s->sc,
                s->ties + s->goff[g], s->rules + s->goff[g],
                s->eff + (size_t)s->goff[g] * 512,
                s->de + (size_t)s->goff[g] * 512,
                s->cnts + s->goff[g]);
#endif
        }
        /* 压实 count>0 层（组内序 = 旧链候选序）+ 锚点 + 混合 */
        int nlay = 0;
        for (int j = 0; j < k; j++) {
            if (s->cnts[s->goff[g] + j] <= 0)
                continue;
            int tt = idxs[j];
            ce_clip_entry* e = &ctx->clips[clip_idx[tt]];
            memcpy(s->lde + (size_t)(layer_pos + nlay) * 512,
                   s->de + (size_t)(s->goff[g] + j) * 512, 512);
            memcpy(s->lef + (size_t)(layer_pos + nlay) * 512,
                   s->eff + (size_t)(s->goff[g] + j) * 512, 512);
            if (e->anchor != NULL) {
                memcpy(s->lan + (size_t)(layer_pos + nlay) * 512,
                       e->anchor, 512 * sizeof(float));
            } else {
                /* 防御: 槽锚点缺失（注册期 malloc 失败等）→ 同输入现算
                 * （与注册期计算逐字节一致） */
                uint8_t actp[512];
                float tmp[512];
                for (int c = 0; c < 512; c++)
                    actp[c] = (uint8_t)(e->base_active != NULL
                                        && e->base_active[c] != 0
                                        && (!e->has_rule
                                            || e->rule_active[c] != 0));
                ce_clip_anchor(e->frame_data, e->n_frames, actp, tmp);
                memcpy(s->lan + (size_t)(layer_pos + nlay) * 512, tmp,
                       512 * sizeof(float));
            }
            nlay++;
        }
        if (nlay > 0) {
#ifdef CE_AVX2
            ce_mix_clips_u8_avx2(
                s->lde + (size_t)layer_pos * 512,
                s->lef + (size_t)layer_pos * 512,
                s->lan + (size_t)layer_pos * 512,
                nlay, mg,
                univ_out + (size_t)g * 1024, univ_act + (size_t)g * 1024);
#else
            ce_mix_clips_u8(
                s->lde + (size_t)layer_pos * 512,
                s->lef + (size_t)layer_pos * 512,
                s->lan + (size_t)layer_pos * 512,
                nlay, mg,
                univ_out + (size_t)g * 1024, univ_act + (size_t)g * 1024);
#endif
            univ_off[u] = g * 1024;
            ok_univ++;
        } else {
            univ_off[u] = -1;
        }
        layer_pos += nlay;
    }
    return ok_univ;
}

/* ── K7a: 单任务 fade 判定（窗口 1e-4 + 标量增益 f64; :3756/:3859-3876 对齐）
 * fade_dur == NULL → 旧行为(fmode=0, fgain=1.0)。窗口与增益阈值分开
 * (窗口 1e-4 用原值, 增益 1e-6 用 clamp 值 — 两处 Python 语义不同)。
 * local_t = pos - start_t(:1836 同式, f64)。 */
static void _ce_task_fade(
    const double* fade_dur, const double* fade_in, const double* fade_out,
    const int* fade_te, const int* fade_curve_fi, const int* fade_curve_fo,
    const int* fade_strategy_fi, const int* fade_strategy_fo,
    const float* const* fade_anchor, const float* const* fade_target,
    int t, double pos, double start_t,
    int* fmode, float* fgain, const float** fprev, const float** ftarget)
{
    *fmode = 0;
    *fgain = 1.0f;
    *fprev = NULL;
    *ftarget = NULL;
    if (fade_dur == NULL)
        return;
    /* fade 数组为全表 [n_total]（与 clip_slot/start_t 同约定, 按全局任务下标
     * t 索引; 非 scratch 的帧内局部行 ti —— LT 数组才按 ti） */
    double local_t = pos - start_t;
    double dur = fade_dur[t];
    double fi = fade_in[t];
    double fo = fade_out[t];
    if (fi > 1e-4 && local_t < fi) {
        *fmode = 1;
        *fprev = (fade_anchor != NULL) ? fade_anchor[t] : NULL;
    } else if (dur > 0.0 && fo > 1e-4 && local_t > dur - fo) {
        *fmode = 2;
        *ftarget = (fade_target != NULL) ? fade_target[t] : NULL;
    }
    *fgain = (float)_ce_clip_fade_gain64(
        dur, fi, fo, local_t,
        (fade_te != NULL) ? fade_te[t] : 0,
        (fade_curve_fi != NULL) ? fade_curve_fi[t] : 0,
        (fade_curve_fo != NULL) ? fade_curve_fo[t] : 0,
        (fade_strategy_fi != NULL) ? fade_strategy_fi[t] : 0,
        (fade_strategy_fo != NULL) ? fade_strategy_fo[t] : 0);
}

/* ══════════════════════════════════════════════════════════════════════
 * K7: 方向性淡入淡出内核（_clip_channel_fade_gains :8144 全语义）
 * 对齐探针: probe_fade_dir_bytecheck.py（变异矩阵 7 子类全命中, 逐字节 0 diff）
 * 子类:
 *   A1 路径A 平行分组逐设备（_compute_parallel_fixture_gains :7657）
 *   A2 路径A 顺序分组批次（_compute_sequential_fixture_gains :7586,
 *      batch_offsets/global_gain/out 缩放全语义）
 *   A3 类型比例逐列覆盖（fade_type_ratio_in/out, :8263-8285）
 *   B1 路径B 无设备几何回退（_compute_group_gains_vectorized :7840,
 *      平行 f64 除法 + _sequential_times f32 除法, 全方向位置源）
 *   A4/B2 seq_delay_in/out 解耦模型（扫掠 span=seq_delay, 窗口=fade）
 *   C1 completed_mask 末帧覆盖（:3843-3862, 仅 fmode=1）
 *   C2 源帧按组重映射（_clip_sequential_remapped_source_frame :2920,
 *      copy_map 行 + 跨槽采样 + phase 插值）
 * 位级等价纪律（NEP50 实测, 见 probe_fade_k7a_bytecheck.py 注释）:
 *   - path A parallel 非 seq_delay: win f32 除法; seq_delay: win f64 除法
 *   - path A sequential: win/t_start/t_end 全 f32; (gains*global_gain) f32
 *   - path B parallel: win f64 除法, t_start f64 差后转 f32
 *   - path B sequential: win f32 除法（_sequential_times :7937）
 *   - _clip_transition_gains :5166: tt f32 clamp[0,1] → te=0 走 LUT
 *     (idx=(int)(tt*255), :5109); te=1 走 curve×strategy f32
 *   - 末帧混合 = prev + g*(end-prev) f32; clip[0,255] → u8 截断（:3876-3877）
 * 结构布局与 timeline_ctx.py CeDirBlock (ctypes) 严格一一对应。
 * ══════════════════════════════════════════════════════════════════════ */

typedef struct {
    /* ── f64 标量（对齐 _clip_channel_fade_gains :8157-8169 钳制 + 渲染窗口原值）── */
    double dur;             /* max(0, clip.duration) :8157 */
    double fi_raw;          /* max(0, fade_in) 渲染窗口 :3768 */
    double fo_raw;          /* max(0, fade_out) :3769 */
    double fi;              /* min(fi_raw, dur) 增益钳制 :8161 */
    double fo;              /* min(fo_raw, dur) :8162 */
    double sdi;             /* min(seq_delay_in, dur) :8168 */
    double sdo;             /* min(seq_delay_out, dur) :8169 */
    double sdi_raw;         /* max(0, seq_delay_in) 渲染窗口 :3644 */
    double sdo_raw;         /* max(0, seq_delay_out) :3645 */
    double single;          /* timing_detail 缩放单组时长 :2780 */
    double single_raw;      /* max(0, fade_group_single_duration) :8167 */
    double pg_b_in;         /* 路径B _sequential_times per_group in: :7941
                             * (single_raw>1e-4 ? single_raw : max(1e-6,fi)) */
    double pg_b_out;        /* 路径B out: (single_raw>1e-4 ? single_raw : max(1e-6,fo)) */
    double pause_sum;       /* Σ缩放 pauses (group_timing) :2787 */
    double seq_span;        /* 渲染窗口 span :3647-3650 */
    double sequence_span;   /* 增益窗口 span :8187-8195 */
    double per_group;       /* 路径A顺序 per_group :7602-7608 */
    double out_target;      /* :7637 */
    double out_span;        /* :7638 */
    double out_scale;       /* :7639 */
    double out_per_group;   /* :7640 */
    double phase_amount;    /* motor_phase_amount :2947/:3052 */
    double src_dur;         /* _clip_source_duration(clip, duration) */
    /* ── int 标量 ── */
    int dir_in_active;      /* fade_in_direction != "none" */
    int dir_out_active;     /* fade_out_direction != "none" */
    int groups;             /* max(1, fade_groups) :8163 */
    int batch_count;        /* _clip_sequential_batch_count(clip, groups) :2771 */
    int te;                 /* transitions_optimized 非空=1 :5174 */
    int curve_fi;
    int curve_fo;
    int strategy_fi;
    int strategy_fo;
    int path;               /* 0=设备(A) 1=通道(B) :8223/:8294 */
    int K;                  /* 设备数/通道数 */
    int seq_gains;          /* mode==sequential && groups>1 :8242/:7968 */
    int seq_enabled;        /* timing_detail :2773 */
    int ntypes;             /* 比例≠1 类型数 :8171-8180 */
    int nentries;           /* entries 行数（仅比例类型） */
    int max_step_in;        /* 路径B 平行步数 :7871 */
    int max_step_out;
    int batch_count_b;      /* 路径B _sequential_times 用（钳制组数）:7939-7940 */
    int n_copy_rows;        /* copy_map 行数 */
    int remap_enabled;      /* copy_map 非空 :3396 */
    int remapped_source;    /* sequential_source_remapped :3388 */
    int seed_fallback;      /* :3389 (bool: out=fallback.copy() else zeros) */
    int remap_universe_ok;  /* :2960 帧域含 u 或帧域空（构建期判定） */
    int motor_phase_enabled;/* bool(clip.motor_transition_phase_enabled) :2944-2946 */
    int parallel_remap;     /* 非 seq 的 motor phase 平行重映射 :2949-2956 */
    /* ── 指针 ── */
    const float* ease_lut;        /* [256] f32 或 NULL :8170 */
    const float* anchor;          /* [512] f32 淡入锚(prev) :3837-3842 */
    const float* target;          /* [512] f32 淡出目标 :3833 */
    const int32_t* spans;         /* [2K] 路径A (start,end) */
    const float* gi_in;           /* [K] 路径A in 组 idx(f32) :7621 */
    const float* gp_in;           /* [K] */
    const float* gs_in;           /* [K] */
    const float* gi_out;          /* [K] 路径A out :7635 */
    const float* gp_out;
    const float* gs_out;
    const double* ratio_in;       /* [ntypes] :8171-8175 */
    const double* ratio_out;      /* [ntypes] :8176-8180 */
    const int32_t* entries;       /* [3*nentries] (fixture, type, off) :8278-8285 */
    const int32_t* channels;      /* [K] 1-based 路径B :8319 */
    const float* pos_in;          /* [K] _compute_direction_positions(in_dir) :7959 */
    const float* pos_out;         /* [K] :7986 */
    const float* gppos_in;        /* [K] 路径B 平行组位置(in) :7871-7897 */
    const float* gppos_out;       /* [K] (out) */
    const float* gidx_in;         /* [K] 路径B 顺序组 idx(f32) :7906-7935 */
    const float* gpos_in;         /* [K] */
    const float* gsize_in;        /* [K] */
    const float* gidx_out;        /* [K] 路径B out */
    const float* gpos_out;
    const float* gsize_out;
    const float* bo_b_in;         /* [batch_count_b] f32 路径B in offsets :7948 */
    const float* bo_b_out;        /* [batch_count_b] f32 路径B out offsets */
    const int32_t* gidx_c;        /* [K] completed 组 idx(int32) :8074/:8090 */
    const int32_t* copy_rows;     /* [6*n_copy_rows] (tgt,src,count,slot,group,phase) */
    const float* copy_f;          /* [2*n_copy_rows] (rel_pos, group_size) */
    const uint8_t* end_raw;       /* [512] u8 末帧 :3853-3856 或 NULL */
    const uint8_t* base_order_mask; /* [512] u8 或 NULL :2870 */
    const double* offsets;        /* [batch_count] f64 时序 offsets :2744 */
    const float* bo_a;            /* [batch_count] f32 路径A in offsets :7611 */
    const float* bo_a_out;        /* [batch_count] f32 路径A out offsets :7641 */
} ce_dir_block;

/* 过渡增益（方向路径）: te=0 → _ease_apply(LUT 或 smoothstep) :5175-5176;
 * te=1 → _ce_transition_gain(f32 curve×strategy) :5177-5203。tt 已 f32 clamp。 */
static float _ce_dir_transition_gain(float tt, int fade_in, int curve, int strategy,
                                     int te, const float* ease_lut)
{
    if (!te) {
        if (ease_lut != NULL) {
            int idx = (int)(tt * 255.0f);
            if (idx < 0) idx = 0;
            else if (idx > 255) idx = 255;
            return ease_lut[idx];
        }
        return tt * tt * (3.0f - 2.0f * tt);
    }
    return _ce_transition_gain(tt, fade_in, curve, strategy);
}

/* searchsorted(times, v, side="left") = lower_bound（重映射插值 :3010） */
static int _ce_searchsorted_left(const double* times, int count, double v)
{
    int lo = 0, hi = count;
    while (lo < hi) {
        int mid = lo + (hi - lo) / 2;
        if (times[mid] < v) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

/* 活跃批次: 对齐 _clip_sequential_active_batch :2734-2753（offsets 预算,
 * f64; local_t 钳 ≥0）。 */
static void _ce_dir_seq_active_batch(const ce_dir_block* b, double local_t,
                                     int* out_batch, double* out_group_t)
{
    int bc = b->batch_count;
    double lt = (local_t < 0.0) ? 0.0 : local_t;
    int batch;
    if (b->parallel_remap) {
        /* :2949-2956 parallel_motor_phase: single=duration, pauses=[], bc=1 */
        *out_batch = 0;
        double gt = lt - 0.0;
        if (gt < 0.0) gt = 0.0;
        else if (gt > b->dur) gt = b->dur;
        *out_group_t = gt;
        return;
    }
    for (batch = 0; batch < bc; batch++) {
        double start = (b->offsets != NULL) ? b->offsets[batch] : 0.0;
        double active_end = start + b->single;
        if (lt <= active_end + 1e-9) {
            double gt = lt - start;
            if (gt < 0.0) gt = 0.0;
            else if (gt > b->single) gt = b->single;
            *out_batch = batch;
            *out_group_t = gt;
            return;
        }
        if (batch < bc - 1) {
            double next_start = (b->offsets != NULL) ? b->offsets[batch + 1] : 0.0;
            if (lt < next_start - 1e-9) {
                *out_batch = batch;
                *out_group_t = b->single;
                return;
            }
        }
    }
    *out_batch = bc - 1;
    *out_group_t = b->single;
}

/* 已完成批次: 对齐 _clip_completed_sequential_batch_index :8015-8023 */
static int _ce_dir_completed_batch(const ce_dir_block* b, double local_t)
{
    if (!b->seq_enabled)
        return -1;
    int batch;
    double group_t;
    _ce_dir_seq_active_batch(b, local_t, &batch, &group_t);
    if (group_t >= b->single - 1e-6)
        return batch;
    return batch - 1;
}

/* 完成批次通道掩码: 对齐 _clip_completed_sequential_channel_mask :8040-8095。
 * completed_batch < 0 → 全零（调用方行为, 同 Python）。 */
static void _ce_dir_completed_mask(const ce_dir_block* b, int completed_batch,
                                   uint8_t* mask /* [512] */)
{
    int i;
    memset(mask, 0, 512);
    if (completed_batch < 0)
        return;
    if (b->path == 0) {
        /* :8074-8082 设备 span 覆盖, group_idx % batch_count */
        for (i = 0; i < b->K; i++) {
            if (b->gidx_c == NULL)
                break;
            if ((int)b->gidx_c[i] % b->batch_count > completed_batch)
                continue;
            int32_t st = b->spans[2 * i];
            int32_t en = b->spans[2 * i + 1];
            if (!(st >= 0 && st < en && en <= 512))
                continue;
            for (int32_t c = st; c < en; c++)
                mask[c] = 1;
        }
    } else {
        /* :8089-8094 通道级（1-based idx-1, 越界跳过） */
        for (i = 0; i < b->K; i++) {
            if (b->gidx_c == NULL)
                break;
            if ((int)b->gidx_c[i] % b->batch_count > completed_batch)
                continue;
            int idx = b->channels[i] - 1;
            if (idx >= 0 && idx < 512)
                mask[idx] = 1;
        }
    }
}

/* ── 路径A: 平行分组单设备增益（对齐 _compute_parallel_fixture_gains :7657-7713
 * 单元素; 与既有 ce_parallel_fixture_gains 同式, 曲线/策略/te/LUT 按边取）。
 * NEP50: 非 seq_delay win 为 f32 除法, seq_delay win 为 f64 除法（双舍入）。 */
static float _ce_dir_par_fixture_one(const ce_dir_block* b, int i, double local_t,
                                     int is_fading_in, int is_fading_out,
                                     double scale_in, double scale_out)
{
    float g = 1.0f;
    double eff_in = b->fi * scale_in;
    if (eff_in < 0.0) eff_in = 0.0;
    double eff_out = b->fo * scale_out;
    if (eff_out < 0.0) eff_out = 0.0;
    double delay_in = b->sdi, delay_out = b->sdo;
    if (b->sdi > 1e-4)
        _ce_parallel_wing_fit(b->dur, &delay_in, &eff_in);
    if (b->sdo > 1e-4)
        _ce_parallel_wing_fit(b->dur, &delay_out, &eff_out);
    if (is_fading_in && b->gi_in != NULL && b->gp_in != NULL && b->gs_in != NULL
            && (eff_in > 1e-4 || b->sdi > 1e-4)) {
        float gpos = b->gp_in[i];
        float gsize = b->gs_in[i];
        float t_start, win;
        double win64 = 0.0;
        if (b->sdi > 1e-4) {
            t_start = gpos * (float)delay_in;
            win64 = (eff_in > 1e-6) ? eff_in : 1e-6;
            win = (float)win64;
        } else {
            win = (float)eff_in / (gsize > 1.0f ? gsize : 1.0f);
            float d = (float)eff_in - win;
            if (d < 0.0f) d = 0.0f;
            t_start = gpos * d;
        }
        float t_end = t_start + win;
        float tt;
        if (b->sdi > 1e-4) {
            double tt64 = (double)((float)local_t - t_start) / win64;
            if (tt64 < 0.0) tt64 = 0.0;
            else if (tt64 > 1.0) tt64 = 1.0;
            tt = (float)tt64;
        } else {
            float w = win;
            if (w < 1e-6f) w = 1e-6f;
            tt = ((float)local_t - t_start) / w;
            if (tt < 0.0f) tt = 0.0f;
            else if (tt > 1.0f) tt = 1.0f;
        }
        float g1 = _ce_dir_transition_gain(tt, 1, b->curve_fi, b->strategy_fi,
                                           b->te, b->ease_lut);
        if ((float)local_t < t_start) g1 = 0.0f;
        else if ((float)local_t >= t_end) g1 = 1.0f;
        if (g1 < g) g = g1;
    }
    if (is_fading_out && b->gi_out != NULL && b->gp_out != NULL && b->gs_out != NULL
            && (eff_out > 1e-4 || b->sdo > 1e-4)) {
        float gpos = b->gp_out[i];
        float gsize = b->gs_out[i];
        float t_start, win;
        double win64 = 0.0;
        if (b->sdo > 1e-4) {
            double region = delay_out + eff_out;
            t_start = (float)(b->dur - region) + gpos * (float)delay_out;
            win64 = (eff_out > 1e-6) ? eff_out : 1e-6;
            win = (float)win64;
        } else {
            win = (float)eff_out / (gsize > 1.0f ? gsize : 1.0f);
            float d = (float)eff_out - win;
            if (d < 0.0f) d = 0.0f;
            t_start = (float)(b->dur - eff_out) + gpos * d;
        }
        float t_end = t_start + win;
        float tt;
        if (b->sdo > 1e-4) {
            double tt64 = (double)(t_end - (float)local_t) / win64;
            if (tt64 < 0.0) tt64 = 0.0;
            else if (tt64 > 1.0) tt64 = 1.0;
            tt = (float)tt64;
        } else {
            float w = win;
            if (w < 1e-6f) w = 1e-6f;
            tt = (t_end - (float)local_t) / w;
            if (tt < 0.0f) tt = 0.0f;
            else if (tt > 1.0f) tt = 1.0f;
        }
        float g1 = _ce_dir_transition_gain(tt, 0, b->curve_fo, b->strategy_fo,
                                           b->te, b->ease_lut);
        if ((float)local_t >= t_end) g1 = 0.0f;
        else if ((float)local_t < t_start) g1 = 1.0f;
        if (g1 < g) g = g1;
    }
    return g;
}

/* ── 路径A: 顺序分组单设备增益（对齐 _compute_sequential_fixture_gains
 * :7586-7655 单元素; 全 f32 窗口运算; :7616-7617 延迟启用时整体增益=1） */
static float _ce_dir_seq_global(const ce_dir_block* b, double local_t)
{
    if (b->sdi > 1e-6 || b->sdo > 1e-6)
        return 1.0f;
    return (float)_ce_clip_fade_gain64(b->dur, b->fi_raw, b->fo_raw, local_t,
                                       b->te, b->curve_fi, b->curve_fo,
                                       b->strategy_fi, b->strategy_fo);
}

static float _ce_dir_seq_fixture_one(const ce_dir_block* b, int i, double local_t,
                                     int is_fading_in, int is_fading_out,
                                     double scale_in, double scale_out)
{
    float g = 1.0f;
    if (is_fading_in) {
        /* :7619-7632 — in 无 eff 门槛（is_fading_in 已含方向）, 按单组窗口 */
        int bi = (int)b->gi_in[i] % b->batch_count;
        float bo = (b->bo_a != NULL) ? b->bo_a[bi] : 0.0f;
        float gsize = b->gs_in[i];
        float win = (float)(b->per_group * scale_in) / (gsize > 1.0f ? gsize : 1.0f);
        float d = (float)b->per_group - win;
        if (d < 0.0f) d = 0.0f;
        float t_start = bo + b->gp_in[i] * d;
        float t_end = t_start + win;
        float w = win;
        if (w < 1e-6f) w = 1e-6f;
        float tt = ((float)local_t - t_start) / w;
        if (tt < 0.0f) tt = 0.0f;
        else if (tt > 1.0f) tt = 1.0f;
        float g1 = _ce_dir_transition_gain(tt, 1, b->curve_fi, b->strategy_fi,
                                           b->te, b->ease_lut);
        if ((float)local_t < t_start) g1 = 0.0f;
        else if ((float)local_t >= t_end) g1 = 1.0f;
        if (g1 < g) g = g1;
    }
    if (is_fading_out && (b->fo > 1e-4 || b->sdo > 1e-4)) {
        /* :7634-7653 — out: out_per_group/out_offsets 缩放 */
        int bi = (int)b->gi_out[i] % b->batch_count;
        float bo = (b->bo_a_out != NULL) ? b->bo_a_out[bi] : 0.0f;
        float gsize = b->gs_out[i];
        float win = (float)(b->out_per_group * scale_out) / (gsize > 1.0f ? gsize : 1.0f);
        float d = (float)b->out_per_group - win;
        if (d < 0.0f) d = 0.0f;
        float rel_start = bo + b->gp_out[i] * d;
        float t_start = (float)(b->dur - b->out_target) + rel_start;
        float t_end = t_start + win;
        float w = win;
        if (w < 1e-6f) w = 1e-6f;
        float tt = (t_end - (float)local_t) / w;
        if (tt < 0.0f) tt = 0.0f;
        else if (tt > 1.0f) tt = 1.0f;
        float g1 = _ce_dir_transition_gain(tt, 0, b->curve_fo, b->strategy_fo,
                                           b->te, b->ease_lut);
        if ((float)local_t >= t_end) g1 = 0.0f;
        else if ((float)local_t < t_start) g1 = 1.0f;
        if (g1 < g) g = g1;
    }
    return g;
}

/* ── 路径A 驱动: 设备 span 铺入 512 通道（:8223-8289）。
 * K<=0 → 整片标量（:8234-8237）; 无比例 → 默认增益直铺（:8256-8262）;
 * 有比例 → 默认先铺 + 逐类型列覆盖（:8263-8285, 仅比例≠1 类型,
 * 比例=1 类型写入与默认同值 = 字节等价可跳过）。 */
static void _ce_dir_fixture_gains(const ce_dir_block* b, double local_t,
                                  int is_fading_in, int is_fading_out,
                                  float* gains /* [512] */)
{
    int i, c;
    if (b->K <= 0) {
        float g = (float)_ce_clip_fade_gain64(
            b->dur, b->fi_raw, b->fo_raw, local_t, b->te,
            b->curve_fi, b->curve_fo, b->strategy_fi, b->strategy_fo);
        for (c = 0; c < 512; c++) gains[c] = g;
        return;
    }
    for (c = 0; c < 512; c++) gains[c] = 1.0f;
    /* Original public fixture route returns its initial ones when any
     * nondefault type ratio is present; ntypes marks that metadata branch. */
    if (b->ntypes != 0) return;
    for (i = 0; i < b->K; i++) {
        float g;
        if (b->seq_gains)
            g = _ce_dir_seq_fixture_one(b, i, local_t, is_fading_in, is_fading_out,
                                        1.0, 1.0) * _ce_dir_seq_global(b, local_t);
        else
            g = _ce_dir_par_fixture_one(b, i, local_t, is_fading_in, is_fading_out,
                                        1.0, 1.0);
        int32_t st = b->spans[2 * i];
        int32_t en = b->spans[2 * i + 1];
        if (!(st >= 0 && st < en && en <= 512))
            continue;
        for (c = (int)st; c < (int)en; c++)
            gains[c] = g;
    }
    for (int ti = 0; ti < b->ntypes; ti++) {
        double ri = (b->ratio_in != NULL) ? b->ratio_in[ti] : 1.0;
        double ro = (b->ratio_out != NULL) ? b->ratio_out[ti] : 1.0;
        for (int e = 0; e < b->nentries; e++) {
            const int32_t* ent = b->entries + 3 * e;
            if (ent[1] != ti)
                continue;
            int fi = ent[0];
            if (fi < 0 || fi >= b->K)
                continue;
            float g;
            if (b->seq_gains)
                g = _ce_dir_seq_fixture_one(b, fi, local_t, is_fading_in,
                                            is_fading_out, ri, ro)
                    * _ce_dir_seq_global(b, local_t);
            else
                g = _ce_dir_par_fixture_one(b, fi, local_t, is_fading_in,
                                            is_fading_out, ri, ro);
            int32_t st = b->spans[2 * fi];
            int32_t en = b->spans[2 * fi + 1];
            if (!(st >= 0 && st < en && en <= 512))
                continue;
            int32_t col = st + ent[2];
            if (col >= st && col < en)
                gains[col] = g;
        }
    }
}

/* ── 路径B: 通道级增益驱动（对齐 :8294-8323 + _compute_group_gains_vectorized
 * :7840-8013）。位置源选择: 主 positions = in_dir if is_fading_in else out_dir
 * :8308; in/out 分支位置 = dir_active ? 对应 : 主位置（:7959-7960/:7986-7987）。
 * 平行分支 win f64 除法; _sequential_times 分支 win f32 除法（per_edge
 * batch_offsets 预算）。K<=0 → 整片标量（:8300-8304, 调用方等价）。 */
static void _ce_dir_channel_gains(const ce_dir_block* b, double local_t,
                                  int is_fading_in, int is_fading_out,
                                  float* gains /* [512] */, float* tmp /* [512] K 临时 */)
{
    int i, c;
    if (b->K <= 0) {
        float g = (float)_ce_clip_fade_gain64(
            b->dur, b->fi_raw, b->fo_raw, local_t, b->te,
            b->curve_fi, b->curve_fo, b->strategy_fi, b->strategy_fo);
        for (c = 0; c < 512; c++) gains[c] = g;
        return;
    }
    int K = b->K;
    /* 位置源选择（:8308-8316; 不变量: !dir_in_active → !is_fading_in） */
    const float* pos_base = is_fading_in ? b->pos_in : b->pos_out;
    const float* in_pos = b->dir_in_active ? b->pos_in : pos_base;
    const float* out_pos = b->dir_out_active ? b->pos_out : pos_base;
    /* in/out 分支数据源: dir_active ? 本方向 : (is_fading_in ? in : out) */
    int in_src = b->dir_in_active ? 1 : (is_fading_in ? 1 : 0);
    int out_src = b->dir_out_active ? 1 : (is_fading_in ? 0 : 1);
    const float* in_gp = in_src ? b->gppos_in : b->gppos_out;
    int in_step = in_src ? b->max_step_in : b->max_step_out;
    const float* in_gidx = in_src ? b->gidx_in : b->gidx_out;
    const float* in_gpos = in_src ? b->gpos_in : b->gpos_out;
    const float* in_gsize = in_src ? b->gsize_in : b->gsize_out;
    const float* out_gp = out_src ? b->gppos_out : b->gppos_in;
    int out_step = out_src ? b->max_step_out : b->max_step_in;
    const float* out_gidx = out_src ? b->gidx_out : b->gidx_in;
    const float* out_gpos = out_src ? b->gpos_out : b->gpos_in;
    const float* out_gsize = out_src ? b->gsize_out : b->gsize_in;
    for (i = 0; i < K; i++)
        tmp[i] = 1.0f;
    /* ── 淡入（:7958-7982, 窗口无方向项）── */
    if ((b->fi > 1e-4 && local_t < b->fi) || (b->sdi > 1e-4 && local_t < b->sdi + b->fi)) {
        if (b->sdi > 1e-4) {
            /* 解耦模型: 平行组位置 × sdi, win = max(1e-6, fi) f64 :7962-7967 */
            double win64 = (b->fi > 1e-6) ? b->fi : 1e-6;
            float winf = (float)win64;
            for (i = 0; i < K; i++) {
                float t_start = in_gp[i] * (float)b->sdi;
                float t_end = t_start + winf;
                double tt64 = (double)((float)local_t - t_start) / win64;
                float tt = (float)(tt64 < 0.0 ? 0.0 : (tt64 > 1.0 ? 1.0 : tt64));
                float g1 = _ce_dir_transition_gain(tt, 1, b->curve_fi,
                                                   b->strategy_fi, b->te, b->ease_lut);
                if ((float)local_t < t_start) g1 = 0.0f;
                else if ((float)local_t >= t_end) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        } else if (b->seq_gains) {
            /* _sequential_times(in_pos, fade_in) :7937-7955, f32 除法 */
            for (i = 0; i < K; i++) {
                int bi = (int)in_gidx[i] % b->batch_count_b;
                float bo = (b->bo_b_in != NULL) ? b->bo_b_in[bi] : 0.0f;
                float gsize = in_gsize[i];
                float win = (float)b->pg_b_in / (gsize > 1.0f ? gsize : 1.0f);
                float d = (float)b->pg_b_in - win;
                if (d < 0.0f) d = 0.0f;
                float t_start = bo + in_gpos[i] * d;
                float t_end = t_start + win;
                float w = win;
                if (w < 1e-6f) w = 1e-6f;
                float tt = ((float)local_t - t_start) / w;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
                float g1 = _ce_dir_transition_gain(tt, 1, b->curve_fi,
                                                   b->strategy_fi, b->te, b->ease_lut);
                if ((float)local_t < t_start) g1 = 0.0f;
                else if ((float)local_t >= t_end) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        } else {
            /* 平行: :7970-7974。win64/d64/winc 为 Python 标量链
             * (fade_in/float(step) 纯 Python f64, np.maximum(win,1e-6)→np.float64);
             * per-element t_start/t_end/tt/比较 = weak 标量 → f32（NEP50,
             * 审查 H2 2026-08-10 修正: 原全 f64 与引擎 f32 位级不等价） */
            double win64 = b->fi / (double)(in_step > 1 ? in_step : 1);
            double d64 = b->fi - win64;
            if (d64 < 0.0) d64 = 0.0;
            double winc = (win64 > 1e-6) ? win64 : 1e-6;
            float winf = (float)win64;
            float df = (float)d64;
            float wf = (float)winc;
            for (i = 0; i < K; i++) {
                float t_start = in_gp[i] * df;
                float t_end = t_start + winf;
                float tt = ((float)local_t - t_start) / wf;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
                float g1 = _ce_dir_transition_gain(tt, 1, b->curve_fi,
                                                   b->strategy_fi, b->te, b->ease_lut);
                if ((float)local_t < t_start) g1 = 0.0f;
                else if ((float)local_t >= t_end) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        }
    }
    /* ── 淡出（:7984-8011, 窗口无方向项）── */
    if ((b->fo > 1e-4 && local_t > b->dur - b->fo)
            || (b->sdo > 1e-4 && local_t > b->dur - (b->sdo + b->fo))) {
        if (b->sdo > 1e-4) {
            /* 解耦: :7989-7994 */
            double region = b->sdo + b->fo;
            double win64 = (b->fo > 1e-6) ? b->fo : 1e-6;
            float winf = (float)win64;
            float base = (float)(b->dur - region);
            for (i = 0; i < K; i++) {
                float t_start = base + out_gp[i] * (float)b->sdo;
                float t_end = t_start + winf;
                double tt64 = (double)(t_end - (float)local_t) / win64;
                float tt = (float)(tt64 < 0.0 ? 0.0 : (tt64 > 1.0 ? 1.0 : tt64));
                float g1 = _ce_dir_transition_gain(tt, 0, b->curve_fo,
                                                   b->strategy_fo, b->te, b->ease_lut);
                if ((float)local_t >= t_end) g1 = 0.0f;
                else if ((float)local_t < t_start) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        } else if (b->seq_gains) {
            /* _sequential_times(out_pos, fade_out) :7995-7998 */
            for (i = 0; i < K; i++) {
                int bi = (int)out_gidx[i] % b->batch_count_b;
                float bo = (b->bo_b_out != NULL) ? b->bo_b_out[bi] : 0.0f;
                float gsize = out_gsize[i];
                float win = (float)b->pg_b_out / (gsize > 1.0f ? gsize : 1.0f);
                float d = (float)b->pg_b_out - win;
                if (d < 0.0f) d = 0.0f;
                float rel_start = bo + out_gpos[i] * d;
                float t_start = (float)(b->dur - b->fo) + rel_start;
                float t_end = t_start + win;
                float w = win;
                if (w < 1e-6f) w = 1e-6f;
                float tt = (t_end - (float)local_t) / w;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
                float g1 = _ce_dir_transition_gain(tt, 0, b->curve_fo,
                                                   b->strategy_fo, b->te, b->ease_lut);
                if ((float)local_t >= t_end) g1 = 0.0f;
                else if ((float)local_t < t_start) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        } else {
            /* 平行: :8000-8003。标量链 f64, per-element f32(同淡入 H2 修正) */
            double win64 = b->fo / (double)(out_step > 1 ? out_step : 1);
            double d64 = b->fo - win64;
            if (d64 < 0.0) d64 = 0.0;
            double winc = (win64 > 1e-6) ? win64 : 1e-6;
            double base64 = b->dur - b->fo;
            float basef = (float)base64;
            float df = (float)d64;
            float winf = (float)win64;
            float wf = (float)winc;
            for (i = 0; i < K; i++) {
                float t_start = basef + out_gp[i] * df;
                float t_end = t_start + winf;
                float tt = (t_end - (float)local_t) / wf;
                if (tt < 0.0f) tt = 0.0f;
                else if (tt > 1.0f) tt = 1.0f;
                float g1 = _ce_dir_transition_gain(tt, 0, b->curve_fo,
                                                   b->strategy_fo, b->te, b->ease_lut);
                if ((float)local_t >= t_end) g1 = 0.0f;
                else if ((float)local_t < t_start) g1 = 1.0f;
                if (g1 < tmp[i]) tmp[i] = g1;
            }
        }
    }
    /* :8319-8321 向量化映射到 512 通道（1-based, 越界丢弃） */
    for (c = 0; c < 512; c++)
        gains[c] = 1.0f;
    for (i = 0; i < K; i++) {
        int idx = b->channels[i] - 1;
        if (idx >= 0 && idx < 512)
            gains[idx] = tmp[i];
    }
}

/* ── 源帧按组重映射（对齐 _clip_sequential_remapped_source_frame → _render_copy_map
 * :2987-3073; motor_phase_only 恒 False, motor force profile None → 恒等整形）。
 * drow 就地改写: seed_fallback → 保留 raw 副本, 否则先置零（:3033）。
 * 行语义: target_batch = target_group % batch_count（parallel_remap 时
 * batch_count=1/single=duration）; >active → 跳过; <active 或 group_t>=single
 * → 末帧; 否则 source_t + rel_pos*phase_span*phase_scale（并行 phase_scale=1,
 * 顺序 1-gp）; interpolate=phase_source && amount>1e-6 → searchsorted_left
 * 双帧 f32 混合 + rintf（round-half-even, :3024-3026）。 */
static void _ce_dir_remap_raw(const ce_dir_block* b, ce_ctx* ctx,
                              double local_t, double source_t,
                              uint8_t* drow /* [512] */)
{
    double local_now = (local_t < 0.0) ? 0.0 : local_t;
    int active_batch;
    double group_t;
    _ce_dir_seq_active_batch(b, local_now, &active_batch, &group_t);
    double end_source_t = b->src_dur - 1e-4;
    if (end_source_t < 0.0) end_source_t = 0.0;
    if (!b->seed_fallback)
        memset(drow, 0, 512);
    double amount = (b->phase_amount > 0.0) ? b->phase_amount : 0.0;
    double phase_fraction = (amount > 0.0) ? amount / (1.0 + amount) : 0.0;
    double phase_span = b->src_dur * phase_fraction;
    if (phase_span < 0.0) phase_span = 0.0;
    int par = b->parallel_remap;
    for (int r = 0; r < b->n_copy_rows; r++) {
        const int32_t* row = b->copy_rows + 6 * r;
        int32_t target_start = row[0];
        int32_t source_start = row[1];
        int32_t count = row[2];
        int32_t sample_slot = row[3];
        int32_t target_group = row[4];
        int32_t phase_source = row[5];
        if (target_start < 0 || source_start < 0 || count <= 0
                || target_start + count > 512 || source_start + count > 512)
            continue;
        int target_batch = par ? 0 : (target_group % b->batch_count);
        if (target_batch > active_batch)
            continue;
        double sample_t;
        if (target_batch < active_batch || group_t >= (par ? b->dur : b->single)) {
            sample_t = end_source_t;
        } else {
            sample_t = (source_t > 0.0) ? source_t : 0.0;
            if (phase_source && (par ? b->dur : b->single) > 1e-6 && b->src_dur > 1e-6) {
                double denom = (par ? b->dur : b->single);
                double gp = group_t / (denom > 1e-6 ? denom : 1e-6);
                if (gp < 0.0) gp = 0.0;
                else if (gp > 1.0) gp = 1.0;
                double phase_scale = par ? 1.0 : (1.0 - gp);
                double phase_offset = (double)b->copy_f[2 * r] * phase_span * phase_scale;
                sample_t = sample_t + phase_offset;
                if (sample_t < 0.0) sample_t = 0.0;
            }
        }
        if (sample_t < 0.0) sample_t = 0.0;
        else if (sample_t > end_source_t) sample_t = end_source_t;
        int interpolate = phase_source && b->phase_amount > 1e-6;
        if (sample_slot < 0 || sample_slot >= ctx->clip_count
                || ctx->clips[sample_slot].frame_times == NULL
                || ctx->clips[sample_slot].n_frames <= 0)
            continue;                     /* source_raw None → 行跳过 :3070-3071 */
        ce_clip_entry* e = &ctx->clips[sample_slot];
        if (!interpolate) {
            /* :3027-3028 _compiled_clip_frame_data(compiled, sample_t) 最近帧 */
            int idx = ce_frame_index(e->frame_times, e->n_frames, sample_t);
            if (idx < 0)
                continue;
            const uint8_t* src = e->frame_data + (size_t)idx * 512;
            for (int32_t c2 = 0; c2 < count; c2++)
                drow[target_start + c2] = src[source_start + c2];
        } else {
            /* :3001-3026 双帧插值（searchsorted left + f32 混合 + rint） */
            int idx = _ce_searchsorted_left(e->frame_times, e->n_frames, sample_t);
            if (idx <= 0) {
                const uint8_t* src = e->frame_data;
                for (int32_t c2 = 0; c2 < count; c2++)
                    drow[target_start + c2] = src[source_start + c2];
            } else if (idx >= e->n_frames) {
                const uint8_t* src = e->frame_data + (size_t)(e->n_frames - 1) * 512;
                for (int32_t c2 = 0; c2 < count; c2++)
                    drow[target_start + c2] = src[source_start + c2];
            } else {
                double t0 = e->frame_times[idx - 1];
                double t1 = e->frame_times[idx];
                if (t1 <= t0 + 1e-9) {
                    const uint8_t* src = e->frame_data + (size_t)idx * 512;
                    for (int32_t c2 = 0; c2 < count; c2++)
                        drow[target_start + c2] = src[source_start + c2];
                    continue;
                }
                double ratio = (sample_t - t0) / (t1 - t0);
                if (ratio < 0.0) ratio = 0.0;
                else if (ratio > 1.0) ratio = 1.0;
                const uint8_t* a = e->frame_data + (size_t)(idx - 1) * 512;
                const uint8_t* bb = e->frame_data + (size_t)idx * 512;
                float rf = (float)ratio;
                for (int32_t c2 = 0; c2 < count; c2++) {
                    float m = (float)a[source_start + c2]
                            + ((float)bb[source_start + c2] - (float)a[source_start + c2]) * rf;
                    if (m < 0.0f) m = 0.0f;
                    else if (m > 255.0f) m = 255.0f;
                    drow[target_start + c2] = (uint8_t)rintf(m);
                }
            }
        }
    }
}

/* ── 方向任务单任务判定: 增益(512) + 渲染窗口 fmode（对齐 :3751-3805）:
 * 增益窗口用钳制值(:8197-8209), 渲染窗口用未钳制值(:3768-3805)。
 * 增益与窗口独立: 窗口外 → 整片标量增益, fmode 仍按原值窗口。
 * 重映射在 fmode=1 的 seq/motor-phase 窗口内就地执行（drow 非 NULL）。 */
static void _ce_task_fade_dir(const ce_dir_block* b, ce_ctx* ctx,
                              double local_t, double source_t,
                              uint8_t* drow /* [512] 或 NULL */,
                              float* tmp /* [512] 路径B临时(dgtmp行) */,
                              int* out_remap_ran,
                              int* fmode, const float** fprev, const float** ftarget,
                              float* gains /* [512] */)
{
    int i;
    /* 增益窗口（钳制值; seq 项用 sequence_span = raw single 基） */
    int is_fading_in = b->dir_in_active && (
        (b->fi > 1e-4 && local_t < b->fi)
        || (b->sdi > 1e-4 && local_t < b->sdi + b->fi)
        || (b->seq_enabled && local_t < fmax(1e-4, b->sequence_span)));
    int is_fading_out = b->dir_out_active && (
        (b->fo > 1e-4 && local_t > b->dur - b->fo)
        || (b->sdo > 1e-4 && local_t > b->dur - (b->sdo + b->fo)));
    if (!is_fading_in && !is_fading_out) {
        /* :8213-8216 窗口外 → 整片标量 */
        float g = (float)_ce_clip_fade_gain64(
            b->dur, b->fi_raw, b->fo_raw, local_t, b->te,
            b->curve_fi, b->curve_fo, b->strategy_fi, b->strategy_fo);
        for (i = 0; i < 512; i++) gains[i] = g;
    } else if (b->path == 0) {
        _ce_dir_fixture_gains(b, local_t, is_fading_in, is_fading_out, gains);
    } else {
        _ce_dir_channel_gains(b, local_t, is_fading_in, is_fading_out,
                              gains, tmp);
    }
    /* 渲染窗口（未钳制值; :3768-3805, hold_extension 已排除） */
    int in_fade_out = b->dur > 0.0 && (
        (b->fo_raw > 1e-4 && local_t > b->dur - b->fo_raw)
        || (b->sdo_raw > 1e-4 && b->dir_out_active
            && local_t > b->dur - (b->fo_raw + b->sdo_raw)));
    int in_seq_in = b->seq_enabled && b->dir_in_active
        && local_t < fmax(1e-4, b->seq_span);
    int in_motor_phase = (!b->seq_enabled) && b->motor_phase_enabled
        && b->dir_in_active && b->phase_amount > 1e-6;
    int in_dir_in = (b->fi_raw > 1e-4 && local_t < b->fi_raw)
        || (b->sdi_raw > 1e-4 && b->dir_in_active
            && local_t < b->fi_raw + b->sdi_raw)
        || in_seq_in;
    *fmode = 0;
    *fprev = NULL;
    *ftarget = NULL;
    if (in_fade_out) {
        *fmode = 2;
        *ftarget = b->target;
    } else if (in_dir_in) {
        *fmode = 1;
        *fprev = b->anchor;
    }
    /* 源帧重映射 :3807-3820（is_in_sequential_in / is_motor_phase_active;
     * 帧域守卫 :2960; 失败 → 返回 fallback → blend 用原 raw） */
    *out_remap_ran = (in_seq_in || in_motor_phase) && b->remap_universe_ok;
    if (*out_remap_ran && drow != NULL && b->remap_enabled)
        _ce_dir_remap_raw(b, ctx, local_t, source_t, drow);
}

/* ── 方向任务渲染: 每通道增益混合 + 完成掩码末帧覆盖 + seq 活跃掩码
 * （对齐 :3824-3877 混合 + :2846-2877 活跃掩码 + :3899-3902 scores）。
 * 返回 idx（调用方已算）; 完成掩码仅 fmode=1 且 !remapped_source 生效。 */
static int _ce_render_clip_frame_dir(
    const uint8_t* raw, int idx, const float* last_change,
    double clip_start, double rate,
    int fade_mode, const float* prev, const float* target,
    const float* gains, const uint8_t* dcmask, const uint8_t* end_raw,
    const ce_dir_block* b, const uint8_t* base_active,
    uint8_t* out, uint8_t* active_out, double* scores,
    const uint8_t* refined)
{
    const float* lc = (last_change != NULL) ? last_change + (size_t)idx * 512 : NULL;
    int c;
    for (c = 0; c < 512; c++) {
        float r = (float)raw[c];
        float g = gains[c];
        float v;
        if (fade_mode == 1 && prev != NULL) {
            v = prev[c] + g * (r - prev[c]);
        } else if (fade_mode == 2 && target != NULL) {
            v = target[c] + g * (r - target[c]);
        } else {
            v = r * g;
        }
        /* 完成批次覆盖: data[mask] = prev + gains*(end_raw - prev) :3859-3862 */
        if (dcmask != NULL && dcmask[c] && prev != NULL && end_raw != NULL) {
            float er = (float)end_raw[c];
            v = prev[c] + g * (er - prev[c]);
        }
        if (v < 0.0f) v = 0.0f;
        else if (v > 255.0f) v = 255.0f;
        out[c] = (uint8_t)v;
    }
    /* 活跃掩码: seq 启用 → (base & bom) | (out>0 & bom) :2853-2876
     * R56e: 非 seq 分支支持逐帧精化 active（refined = 注册期
     * base&((cur>0)|changed) 表, 同 _clip_current_channel_active_mask;
     * NULL = 旧行为）。seq 分支公式不同 → 由调用方对含 seq 语义的 clip
     * 传 NULL（该类 clip 已整帧回退 Python）。 */
    const uint8_t* bom = (b != NULL && b->seq_enabled) ? b->base_order_mask : NULL;
    const uint8_t* act_src = (refined != NULL)
        ? refined + (size_t)idx * 512 : base_active;
    for (c = 0; c < 512; c++) {
        uint8_t a;
        if (bom != NULL) {
            uint8_t bo = bom[c];
            a = (uint8_t)((base_active[c] & bo) | ((out[c] > 0 ? 1u : 0u) & bo));
        } else if (b != NULL && b->seq_enabled) {
            /* :2870-2876 bom=None → return mask | raw_mask（base | out>0） */
            a = (uint8_t)(base_active[c] | ((out[c] > 0) ? 1u : 0u));
        } else {
            a = act_src[c];
        }
        active_out[c] = a;
        double s = (lc != NULL) ? clip_start + (double)lc[c] / rate : clip_start;
        scores[c] = a ? s : -INFINITY;
    }
    return idx;
}

/* ── K2: 离线时间线整帧融合（每帧 1 次 ctypes 调用; 纯回放 Fast-Path 条件）
 * 对齐 _render_timeline_frame_range 步骤 c-e 子集（seq/fade/motor/LT 关闭）:
 *   1. 任务区间 = task_off[frame_idx .. frame_idx+1]（CSR）; 每任务现算
 *      source_t（Python 序, playback_engine.py:1800）:
 *        source_t = fmin(fmax(0.0, pos - start_t) * rate, fmax(0.0, src_dur - 1e-4))
 *      → ce_render_clip_frame(fade_mode=0, gain=1.0, hold=0) 取帧/active/scores。
 *      rate 守卫（:3907 rate<=1e-9 → 1.0）只用于 scores 通道, source_t 用 raw。
 *      frame_idx < 0 → 任务丢弃（:3702 continue）。
 *   2. ★ rule else 角（语义铁律, :3890-3893）: 槽 has_rule 且 base_active 全零
 *      → active 改写为 rule_active; scores 在 rule 通道上重算
 *      （= _compiled_clip_channel_change_scores, 槽 last_change 行 + guarded rate）;
 *      锚点按 rule_active 现算进 s->ca（Python post-merge active 输入,
 *      槽锚点 base&rule 此时为全零, 不可用）。
 *   3. 组构建（universe 首现序）+ ce_resolve_many_group_* + 压实 + 混合打包:
 *      结构复刻 ce_render_frame_fused 步骤 2-5; rule 指针取自槽;
 *      tie 取自任务表（调用方已预算 priority*1e9+(track_order+1)*1e-3+start_t*1e-4）;
 *      角任务锚点用 s->corner/s->ca, 非角用槽锚点（缺失同 fused 防御现算）。
 * 契约: 同 ce_render_frame_fused——univ_off 调用方预置 -1（执行期 = 组下标,
 * 返回 = 字节偏移或 -1）; universe 值域 < max_u 由调用方保证; 返回有输出
 * 域数; <0 = 拒绝 → 调用方回退原路径。
 */
CE_API int ce_timeline_frame(
    void* vctx,
    const int* task_off, int frame_idx, double pos,
    const int* clip_slot, const int* universe,
    const double* start_t, const double* rate, const double* src_dur,
    const double* tie, double mod_gain,
    uint8_t* univ_out, uint8_t* univ_act, int* univ_off,
    /* ── K6: 灯跟踪任务参数(lt_scope_off == NULL → 全部非 LT, 行为同旧)── */
    const int* lt_scope_off,          /* [n_tasks] -1 = 无 LT, ≥0 = scope 句柄 */
    void* const* lt_scope,            /* [n_scopes] scope 扁平块指针 */
    const uint8_t* const* lt_nm_rgb,  /* [n_tasks] 每任务 [3*(n_fx+n_pumps)] u8 */
    const int* const* lt_fx_live,     /* [n_tasks] 每任务 [n_fx+n_pumps] int */
    const void* const* const* lt_cross_tbl, /* [n_tasks] 每任务 [n_cross] 泵帧表 */
    const double* const* lt_cross_t0, /* [n_tasks] 每任务 [n_cross] f64 */
    const int* const* lt_cross_live,  /* [n_tasks] 每任务 [n_cross] int */
    double* lt_state, double* lt_state_t, uint8_t* lt_state_exists,
    uint8_t* lt_color, uint8_t* lt_color_valid,
    uint8_t* lt_touched, uint8_t* lt_touched_color,
    int n_lt_state, int n_lt_color,
    /* ── K7a: fade 任务参数(fade_dur == NULL → 无 fade, 行为同旧)──
     * dur/fi/fo 为 f64(Python f64 语义, 非 f32! 边界比较须逐位同);
     * te/curve/strategy 索引表同 _ce_transition_gain64; anchor/target
     * 为每任务 512×f32(调用方注册期 prefetch, 与 Python :3859-3876 同源)。 */
    const double* fade_dur,          /* [n_tasks] max(0, clip.duration) */
    const double* fade_in,           /* [n_tasks] max(0, fade_in 原值) */
    const double* fade_out,          /* [n_tasks] max(0, fade_out 原值) */
    const int* fade_te,              /* [n_tasks] transitions_optimized 非空=1 */
    const int* fade_curve_fi,        /* [n_tasks] 淡入曲线索引 */
    const int* fade_curve_fo,        /* [n_tasks] 淡出曲线索引 */
    const int* fade_strategy_fi,     /* [n_tasks] 淡入策略索引 */
    const int* fade_strategy_fo,     /* [n_tasks] 淡出策略索引 */
    const float* const* fade_anchor, /* [n_tasks] 淡入锚(prev) 512×f32 */
    const float* const* fade_target, /* [n_tasks] 淡出目标(target) 512×f32 */
    void* const* dir_blocks,         /* [n_tasks] 每任务 ce_dir_block* 或 NULL */
    /* ── R56e: 逐帧精化 active（录制稀疏掩码语义）──
     * refined_pool == NULL（或 task_refined == NULL）→ 全部用槽 base_active
     *   （旧行为, 字节不变）; 否则 refined_pool[code] = 本任务本帧的精化
     *   active 512B 行（code = 建表期按原始时间戳 epsilon 选好的行指针码;
     *   hold/排除行传 -1 → 槽 base_active, 同 Python hold_extension=True）。
     * dir 任务: 精化只用于非 seq 分支; LT 任务: 不取精化（LT clip 已由
     *   调用方整帧回退 Python）。 */
    const int* task_refined,
    const uint8_t* const* refined_pool,
    /* ── R56g: 任务行转储（接管消费标记用, Python 侧逐帧消费）──
     * 三者任一 NULL = 不转储。skip 行 act=0（Python eff 逻辑自然忽略）。 */
    uint8_t* dump_out, uint8_t* dump_act, double* dump_sc)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    int t, g, ok_univ = 0;
    if (ctx == NULL || task_off == NULL || univ_off == NULL || frame_idx < 0)
        return -1;
    int t0 = task_off[frame_idx];
    int t1 = task_off[frame_idx + 1];
    int n_tasks = t1 - t0;
    if (n_tasks <= 0)
        return 0;
    if (_ce_fused_ensure(ctx, n_tasks) < 0)
        return -1;
    ce_fused_scratch* s = &ctx->scr;
    const float mg = (float)mod_gain;

    /* ── 1) 渲染: 每任务（ti = 任务区间内局部行, 全 scratch 行用 ti）────
     * LT 任务走串行阶段(1b): 状态槽跨任务共享, Python 逐任务顺序语义
     * (同键双更新角: F5+跨域同帧两次更新, 第二次 dt=0.001 小步) 在
     * OpenMP 下会竞态 → LT 任务必须串行且按任务序。非 LT 任务保持 OpenMP。 */
    const int has_lt = (lt_scope_off != NULL && lt_scope != NULL);
    #pragma omp parallel for schedule(static)
    for (t = t0; t < t1; t++) {
        int ti = t - t0;
        int ci = clip_slot[t];
        s->skip[ti] = 1;
        s->corner[ti] = 0;
        if (ci < 0 || ci >= ctx->clip_count || ctx->clips[ci].frame_times == NULL)
            continue;
        if (has_lt && lt_scope_off[ti] >= 0)
            continue;               /* LT 任务留到串行阶段(共享状态) */
        ce_clip_entry* e = &ctx->clips[ci];
        double rate_raw = rate[t];
        double rate_s = (rate_raw <= 1e-9) ? 1.0 : rate_raw;
        double src_clamp = fmax(0.0, src_dur[t] - 1e-4);
        double source_t = fmin(fmax(0.0, pos - start_t[t]) * rate_raw, src_clamp);
        uint8_t* out_row = s->out + (size_t)ti * 512;
        uint8_t* act_row = s->act + (size_t)ti * 512;
        double* sc_row = s->sc + (size_t)ti * 512;
        const uint8_t* refined_row = NULL;
        if (task_refined != NULL && refined_pool != NULL
                && task_refined[ti] >= 0)
            refined_row = refined_pool[task_refined[ti]];
        const ce_dir_block* db = (dir_blocks != NULL)
            ? (const ce_dir_block*)dir_blocks[t] : NULL;
        int idx;
        if (db != NULL) {
            /* ── K7: 方向 fade 任务（_clip_channel_fade_gains 全语义）── */
            if (db->seq_enabled) {
                /* _clip_sequential_source_time :2791-2806: seq 组时序源帧
                 * 重映射 — Python 渲染循环 item 的 source_t 经
                 * _clip_sequential_active_batch 组窗口映射(:2795-2796),
                 * 线性式 (local_t*rate) 仅在非 seq 时等价。single>1e-4 由
                 * :2773 enabled 保证; source_duration<=1e-6 走 active_t*rate
                 * 分支(:2804)。offsets/single/batch_count 与 active_batch
                 * 同源(:2744)。 */
                int sb; double sgt;
                _ce_dir_seq_active_batch(db, pos - start_t[t], &sb, &sgt);
                double st = (db->single > 1e-6 && src_dur[t] > 1e-6)
                    ? (sgt / db->single) * src_dur[t]
                    : sgt * rate_raw;
                source_t = fmin(fmax(0.0, st), src_clamp);
            }
            idx = ce_frame_index(e->frame_times, e->n_frames, source_t);
            if (idx < 0)
                continue;           /* :3702 无帧 → 任务丢弃 */
            /* raw 物化到 dscr 行（重映射就地改写; 无重映射时等价 memcpy） */
            memcpy(s->dscr + (size_t)ti * 512,
                   e->frame_data + (size_t)idx * 512, 512);
            /* R56e: 精化行选择——a 半场(source_t < times[idx]-1e-9, 掩码函数
             * last-≤ 索引 = idx-1) vs b 半场(≥, 索引 = idx); 2n 交错表。 */
            /* R56e: refined_pool[code] = 本任务本帧的精化 active 512B 行
             * （半场选择已在 Python 建表期按原始时间戳 epsilon 完成）;
             * code<0（hold/排除）→ 槽 base_active（同 Python hold 语义）。 */
            const uint8_t* refined_row = NULL;
            if (task_refined != NULL && refined_pool != NULL
                    && task_refined[ti] >= 0)
                refined_row = refined_pool[task_refined[ti]];
            int fmode;
            const float* fprev;
            const float* ftarget;
            int remap_ran = 0;
            uint8_t* drow = (db->seq_enabled || db->motor_phase_enabled)
                ? s->dscr + (size_t)ti * 512 : NULL;
            _ce_task_fade_dir(db, ctx, pos - start_t[t], source_t, drow,
                              s->dgtmp + (size_t)ti * 512, &remap_ran,
                              &fmode, &fprev, &ftarget,
                              s->dgains + (size_t)ti * 512);
            /* 完成批次末帧覆盖: 仅 fmode=1 且未应用重映射 :3843-3851
             * （非 seq → completed_batch=-1 → 全零掩码, 等价跳过） */
            uint8_t* dcmask = NULL;
            if (fmode == 1 && db->seq_enabled
                    && !(remap_ran && db->remapped_source)) {
                int cb = _ce_dir_completed_batch(db, pos - start_t[t]);
                dcmask = s->dcmask + (size_t)ti * 512;
                _ce_dir_completed_mask(db, cb, dcmask);
            }
            _ce_render_clip_frame_dir(
                s->dscr + (size_t)ti * 512, idx, e->last_change,
                start_t[t], rate_s, fmode, fprev, ftarget,
                s->dgains + (size_t)ti * 512, dcmask, db->end_raw,
                db, e->base_active, out_row, act_row, sc_row, NULL); /* R56E-DBG */
        } else {
            /* ── 旧路径: 标量 fade + 原渲染（K7a/K2 行为不变）── */
            int fmode;
            float fgain;
            const float* fprev;
            const float* ftarget;
            _ce_task_fade(fade_dur, fade_in, fade_out, fade_te,
                          fade_curve_fi, fade_curve_fo, fade_strategy_fi,
                          fade_strategy_fo, fade_anchor, fade_target,
                          t, pos, start_t[t], &fmode, &fgain, &fprev, &ftarget);
            idx = ce_frame_index(e->frame_times, e->n_frames, source_t);
            const uint8_t* refined_row = NULL;
            if (idx >= 0 && task_refined != NULL && refined_pool != NULL
                    && task_refined[ti] >= 0)
                refined_row = refined_pool[task_refined[ti]];
            idx = _ce_render_clip_frame_x(
                e->frame_data, e->last_change, idx,
                start_t[t], rate_s, pos,
                fgain, e->base_active, fmode, fprev, ftarget,
                refined_row,
                out_row, act_row, sc_row);
        }
        if (idx < 0)
            continue;               /* :3702 frame_index < 0 → 任务丢弃 */
        s->skip[ti] = 0;
        /* ★ rule else 角: 渲染后 act 行全零 + has_rule → active=rule
         * （Python :3890-3893: rule & (clip_active if any else rule);
         *  方向任务 act 行 = 渲染后 clip_active, 非方向与 base_active 恒等） */
        if (e->has_rule && e->rule_active != NULL) {
            int ba_any = 0;
            for (int c = 0; c < 512; c++) {
                if (act_row[c]) { ba_any = 1; break; }
            }
            if (!ba_any) {
                const float* lc = (e->last_change != NULL)
                    ? e->last_change + (size_t)idx * 512 : NULL;
                for (int c = 0; c < 512; c++) {
                    if (e->rule_active[c]) {
                        act_row[c] = 1;
                        sc_row[c] = (lc != NULL)
                            ? start_t[t] + (double)lc[c] / rate_s
                            : start_t[t];
                    } else {
                        act_row[c] = 0;
                        sc_row[c] = -INFINITY;
                    }
                }
                ce_clip_anchor(e->frame_data, e->n_frames,
                               e->rule_active, s->ca + (size_t)ti * 512);
                s->corner[ti] = 1;
            }
        }
        /* R56g: 接管消费扣除（= _takeover_consumed_active_for_item;
         * PY 顺序 = rule fold 之后 → 此处同位）*/
        if (ctx->consumed_any[ci]) {
            const uint8_t* cmk = ctx->consumed[ci];
            for (int c = 0; c < 512; c++) {
                if (cmk[c]) { act_row[c] = 0; sc_row[c] = -INFINITY; }
            }
        }
    }
    /* 1b) LT 任务(串行, 任务序): ce_render_clip_frame_lt 融合变体 — 注册期
     * base_active 已 = base|light_mask(:3885-3889 合并), 故 act/scores/角
     * 检查与 K2 语义一致, rule-else 角对 LT 任务原样适用。 */
    if (has_lt) {
        for (t = t0; t < t1; t++) {
            int ti = t - t0;
            /* LT 数组按帧内局部行 ti 索引（与 scratch 同约定; 每帧 bundle
             * 由 wrapper 按本帧任务构建） */
            if (lt_scope_off[ti] < 0)
                continue;
            int ci = clip_slot[t];
            if (ci < 0 || ci >= ctx->clip_count || ctx->clips[ci].frame_times == NULL)
                continue;
            ce_clip_entry* e = &ctx->clips[ci];
            double rate_raw = rate[t];
            double rate_s = (rate_raw <= 1e-9) ? 1.0 : rate_raw;
            double src_clamp = fmax(0.0, src_dur[t] - 1e-4);
            double source_t = fmin(fmax(0.0, pos - start_t[t]) * rate_raw, src_clamp);
            const void* scope = lt_scope[lt_scope_off[ti]];
            if (scope == NULL)
                continue;
            int fmode;
            float fgain;
            const float* fprev;
            const float* ftarget;
            _ce_task_fade(fade_dur, fade_in, fade_out, fade_te,
                          fade_curve_fi, fade_curve_fo, fade_strategy_fi,
                          fade_strategy_fo, fade_anchor, fade_target,
                          t, pos, start_t[t], &fmode, &fgain, &fprev, &ftarget);
            int idx = ce_render_clip_frame_lt(
                e->frame_times, e->n_frames, e->frame_data, e->last_change,
                source_t, start_t[t], rate_s, pos,
                0, fgain, e->base_active, fmode, fprev, ftarget,
                scope, pos - start_t[t],
                (lt_nm_rgb != NULL) ? lt_nm_rgb[ti] : NULL,
                (lt_fx_live != NULL) ? lt_fx_live[ti] : NULL,
                (lt_cross_tbl != NULL) ? lt_cross_tbl[ti] : NULL,
                (lt_cross_t0 != NULL) ? lt_cross_t0[ti] : NULL,
                (lt_cross_live != NULL) ? lt_cross_live[ti] : NULL,
                lt_state, lt_state_t, lt_state_exists,
                lt_color, lt_color_valid,
                lt_touched, lt_touched_color,
                n_lt_state, n_lt_color,
                s->ltscr + (size_t)ti * 512,
                s->out + (size_t)ti * 512, s->act + (size_t)ti * 512,
                s->sc + (size_t)ti * 512);
            if (idx < 0)
                continue;           /* :3702 无帧 → 丢弃(LT 不执行) */
            s->skip[ti] = 0;
            /* ★ rule else 角(base_active 已含 light_mask) */
            if (e->has_rule && e->rule_active != NULL) {
                int ba_any = 0;
                for (int c = 0; c < 512; c++) {
                    if (e->base_active[c]) { ba_any = 1; break; }
                }
                if (!ba_any) {
                    uint8_t* actr = s->act + (size_t)ti * 512;
                    double* scr = s->sc + (size_t)ti * 512;
                    const float* lc = (e->last_change != NULL)
                        ? e->last_change + (size_t)idx * 512 : NULL;
                    for (int c = 0; c < 512; c++) {
                        if (e->rule_active[c]) {
                            actr[c] = 1;
                            scr[c] = (lc != NULL)
                                ? start_t[t] + (double)lc[c] / rate_s
                                : start_t[t];
                        } else {
                            actr[c] = 0;
                            scr[c] = -INFINITY;
                        }
                    }
                    ce_clip_anchor(e->frame_data, e->n_frames,
                                   e->rule_active, s->ca + (size_t)ti * 512);
                    s->corner[ti] = 1;
                }
            }
            if (ctx->consumed_any[ci]) {
                const uint8_t* cmk = ctx->consumed[ci];
                uint8_t* actr = s->act + (size_t)ti * 512;
                double* scr = s->sc + (size_t)ti * 512;
                for (int c = 0; c < 512; c++) {
                    if (cmk[c]) { actr[c] = 0; scr[c] = -INFINITY; }
                }
            }
        }
    }

    /* ── 2) 组构建: universe 首现序, 组内 = 任务扫描序 ───────────── */
    int n_groups = 0;
    for (t = t0; t < t1; t++) {
        int ti = t - t0;
        if (s->skip[ti])
            continue;
        int u = universe[t];
        if (u < 0)
            continue;
        int g2 = univ_off[u];
        if (g2 < 0) {
            g2 = n_groups++;
            univ_off[u] = g2;
            s->guniv[g2] = u;
            s->gcnt[g2] = 0;
        }
        s->gcnt[g2]++;
    }
    if (n_groups <= 0)
        return 0;
    s->goff[0] = 0;
    for (g = 0; g < n_groups; g++)
        s->goff[g + 1] = s->goff[g] + s->gcnt[g];
    for (g = 0; g < n_groups; g++)
        s->gpos[g] = s->goff[g];
    for (t = t0; t < t1; t++) {
        int ti = t - t0;
        if (s->skip[ti])
            continue;
        int u = universe[t];
        if (u < 0)
            continue;
        int g2 = univ_off[u];
        int pos = s->gpos[g2]++;
        s->flat[pos] = ti;
        s->ties[pos] = (tie != NULL) ? tie[t] : 0.0;
        int ci = clip_slot[t];
        ce_clip_entry* e = &ctx->clips[ci];
        s->rules[pos] = (e->has_rule && e->rule_active != NULL)
            ? e->rule_active : NULL;
    }

    /* ── 3) 每域: 解析 → 压实（角层锚点用现算表）→ 混合 → 打包 ────── */
    int layer_pos = 0;
    for (g = 0; g < n_groups; g++) {
        int k = s->goff[g + 1] - s->goff[g];
        const int* idxs = s->flat + s->goff[g];
        const int u = s->guniv[g];
        if (k > 0) {
#ifdef CE_AVX2
            ce_resolve_many_group_avx2(
                idxs, k, s->out, s->act, s->sc,
                s->ties + s->goff[g], s->rules + s->goff[g],
                s->eff + (size_t)s->goff[g] * 512,
                s->de + (size_t)s->goff[g] * 512,
                s->cnts + s->goff[g]);
#else
            ce_resolve_many_group_scalar(
                idxs, k, s->out, s->act, s->sc,
                s->ties + s->goff[g], s->rules + s->goff[g],
                s->eff + (size_t)s->goff[g] * 512,
                s->de + (size_t)s->goff[g] * 512,
                s->cnts + s->goff[g]);
#endif
        }
        int nlay = 0;
        for (int j = 0; j < k; j++) {
            if (s->cnts[s->goff[g] + j] <= 0)
                continue;
            int tt = idxs[j];
            int task_row = tt + t0;
            ce_clip_entry* e = &ctx->clips[clip_slot[task_row]];
            memcpy(s->lde + (size_t)(layer_pos + nlay) * 512,
                   s->de + (size_t)(s->goff[g] + j) * 512, 512);
            memcpy(s->lef + (size_t)(layer_pos + nlay) * 512,
                   s->eff + (size_t)(s->goff[g] + j) * 512, 512);
            if (s->corner[tt]) {
                memcpy(s->lan + (size_t)(layer_pos + nlay) * 512,
                       s->ca + (size_t)tt * 512, 512 * sizeof(float));
            } else if (e->anchor != NULL) {
                memcpy(s->lan + (size_t)(layer_pos + nlay) * 512,
                       e->anchor, 512 * sizeof(float));
            } else {
                uint8_t actp[512];
                float tmp[512];
                for (int c = 0; c < 512; c++)
                    actp[c] = (uint8_t)(e->base_active != NULL
                                        && e->base_active[c] != 0
                                        && (!e->has_rule
                                            || e->rule_active[c] != 0));
                ce_clip_anchor(e->frame_data, e->n_frames, actp, tmp);
                memcpy(s->lan + (size_t)(layer_pos + nlay) * 512, tmp,
                       512 * sizeof(float));
            }
            nlay++;
        }
        if (nlay > 0) {
#ifdef CE_AVX2
            ce_mix_clips_u8_avx2(
                s->lde + (size_t)layer_pos * 512,
                s->lef + (size_t)layer_pos * 512,
                s->lan + (size_t)layer_pos * 512,
                nlay, mg,
                univ_out + (size_t)g * 1024, univ_act + (size_t)g * 1024);
#else
            ce_mix_clips_u8(
                s->lde + (size_t)layer_pos * 512,
                s->lef + (size_t)layer_pos * 512,
                s->lan + (size_t)layer_pos * 512,
                nlay, mg,
                univ_out + (size_t)g * 1024, univ_act + (size_t)g * 1024);
#endif
            univ_off[u] = g * 1024;
            ok_univ++;
        } else {
            univ_off[u] = -1;
        }
        layer_pos += nlay;
    }
    /* R56g: 任务行转储（skip 行 act=0 → Python 标记逻辑自然忽略）*/
    if (dump_out != NULL && dump_act != NULL && dump_sc != NULL) {
        for (int ti = 0; ti < n_tasks; ti++) {
            uint8_t* do_r = dump_out + (size_t)ti * 512;
            uint8_t* da_r = dump_act + (size_t)ti * 512;
            double* ds_r = dump_sc + (size_t)ti * 512;
            if (s->skip[ti]) {
                memset(da_r, 0, 512);
                for (int c = 0; c < 512; c++) ds_r[c] = -INFINITY;
                memset(do_r, 0, 512);
            } else {
                memcpy(do_r, s->out + (size_t)ti * 512, 512);
                memcpy(da_r, s->act + (size_t)ti * 512, 512);
                memcpy(ds_r, s->sc + (size_t)ti * 512, 512 * sizeof(double));
            }
        }
    }
    return ok_univ;
}

/* ── T5: matrix 效果逐设备求值（对齐 _register_matrix_presets :1847-2237）──
 * kind: 0=static(:1850) 1=scroll_h(:1884) 2=scroll_v(:1928) 3=blink(:1973)
 *       4=gradient(:2014) 5=alternate(:2056) 6=scan_row(:2096) 7=scan_col(:2131)
 *       8=spiral(:2165) 9=explode(:2202)
 * 参数映射: t/speed/rate=f64; cols/lo(off_val|min)/hi(on_val|max)=int;
 *   cells = 图案平铺 (row-major 满网格 pat_rows×pat_cols; 无图案 kind 传 NULL);
 *   mask 类(0/3/4/5) 用 flat_len 做 modulo 掩码(复刻 as_flat_mask :116);
 *   scroll 类(1/2) 用网格索引(复刻 pat.get :95, 行/列模后必在界内);
 *   alternate(5): (int(t*rate)%2==0) 选 cells(flat_len) 否则 cells2(flat_len2);
 *   位级等价纪律: 全程 f64 同 Python float; int() 截断 = (long long) 截断;
 *     Python % 负数返回非负 → _ce_int_mod; 浮点 % 用 _ce_fmod_py(模恒正);
 *     gradient 无 clamp(同 Python, v∈[0,1] 恒在界内); pow(x,2.0) 复刻 x**2;
 *     sin/sqrt/pow/fmod 同 UCRT(与 Python 3.13 同一 CRT, /fp:precise 禁 FMA)。
 * 边界: cells 要求满网格 rows×cols(PatternDef __post_init__/from_dict 保证);
 *   spiral 内部 malloc 序列表(rows*cols≤n+cols-1, 用后 free, 失败=不写);
 *   cols<1 防御置 1(Python 侧 ZeroDivisionError, 正常参数不触发)。
 */

/* Python int % m 语义（负数返回非负; m>0） */
static int _ce_int_mod(long long k, int m)
{
    long long r = k % m;
    if (r < 0)
        r += m;
    return (int)r;
}

/* Python float % b 语义（符号随除数; 本文件调用处 b 恒正） */
static double _ce_fmod_py(double a, double b)
{
    double m = fmod(a, b);
    if (m < 0.0)
        m += b;
    return m;
}

/* 复刻 _spiral_indices :2810（逐行同构）; 返回 rows*cols 长度, 调用方 free */
static int* _ce_spiral_indices(int rows, int cols)
{
    int total = rows * cols;
    int* res = (int*)malloc((size_t)total * sizeof(int));
    int idx = 0, top = 0, bottom = rows - 1, left = 0, right = cols - 1;
    if (res == NULL)
        return NULL;
    while (top <= bottom && left <= right) {
        for (int c = left; c <= right; c++)
            res[idx++] = top * cols + c;
        top++;
        for (int r = top; r <= bottom; r++)
            res[idx++] = r * cols + right;
        right--;
        if (top <= bottom) {
            for (int c = right; c >= left; c--)
                res[idx++] = bottom * cols + c;
            bottom--;
        }
        if (left <= right) {
            for (int r = bottom; r >= top; r--)
                res[idx++] = r * cols + left;
            left++;
        }
    }
    return res;
}

/* 掩码循环: out[i] = flat[i%flat_len] != 0 ? hi : lo（static/alternate 共用） */
static void _ce_mask_apply(int n, const uint8_t* cells, int flat_len,
                           int lo, int hi, uint8_t* out)
{
    for (int i = 0; i < n; i++) {
        int bit = (flat_len > 0) ? (cells[i % flat_len] != 0) : 0;
        out[i] = (uint8_t)(bit ? hi : lo);
    }
}

/* 网格取位: pat.get(row%rows, col%cols)（scroll_h/v 共用; 模后必在界内） */
static int _ce_grid_bit(const uint8_t* cells, int pr, int pc, int row, int col)
{
    if (pr <= 0 || pc <= 0)
        return 0;
    return cells[(row % pr) * pc + (col % pc)] != 0;
}

/* gradient(kind 4): phase=(t*speed)%1.0, sin 波, int() 无 clamp（同 Python） */
static void _ce_matrix_gradient(double t, double speed, int lo, int hi, int n,
                                const uint8_t* cells, int flat_len,
                                uint8_t* out)
{
    double phase = _ce_fmod_py(t * speed, 1.0);
    for (int i = 0; i < n; i++) {
        int bit = (flat_len > 0) ? (cells[i % flat_len] != 0) : 0;
        if (bit) {
            double v = (sin((phase + (double)i * 0.05) * 2.0 * M_PI) + 1.0) / 2.0;
            out[i] = (uint8_t)((int)((double)lo + v * (double)(hi - lo)));
        } else {
            out[i] = (uint8_t)lo;
        }
    }
}

/* spiral(kind 8): cur=int(t*speed*n)%n, on_idx=order[cur]（:2165） */
static void _ce_matrix_spiral(double t, double speed, int cols, int lo, int hi,
                              int n, uint8_t* out)
{
    int rows = (n + cols - 1) / cols;
    int* order = _ce_spiral_indices(rows, cols);
    int cur, on_idx, order_len;
    if (order == NULL)
        return;  /* malloc 失败: 不写输出(调用方保持原缓冲) */
    cur = _ce_int_mod((long long)(t * speed * (double)n), n);
    order_len = rows * cols;
    on_idx = (cur < order_len) ? order[cur] : cur;
    for (int i = 0; i < n; i++)
        out[i] = (uint8_t)((i == on_idx) ? hi : lo);
    free(order);
}

/* explode(kind 9): dist=sqrt(pow(col-cx,2)+pow(row-cy,2)), |dist-radius|<0.8 */
static void _ce_matrix_explode(double t, double speed, int cols, int lo, int hi,
                               int n, uint8_t* out)
{
    int rows = (n + cols - 1) / cols;
    int mx = (rows > cols) ? rows : cols;
    double cx = (double)(cols - 1) / 2.0;
    double cy = (double)(rows - 1) / 2.0;
    double radius = _ce_fmod_py(t * speed, (double)mx / 2.0 + 1.0);
    for (int i = 0; i < n; i++) {
        int row = i / cols, col = i % cols;
        double dist = sqrt(pow((double)col - cx, 2.0)
                         + pow((double)row - cy, 2.0));
        out[i] = (uint8_t)((fabs(dist - radius) < 0.8) ? hi : lo);
    }
}

CE_API void ce_matrix_effect(int kind, double t, double speed, double rate,
                             int cols, int lo, int hi, int n,
                             const uint8_t* cells, int pat_rows, int pat_cols,
                             int flat_len, const uint8_t* cells2, int flat_len2,
                             uint8_t* out)
{
    int rows, i;
    if (n <= 0)
        return;
    if (cols < 1)
        cols = 1;
    if (flat_len > 0 && cells == NULL)
        flat_len = 0;
    if (flat_len2 > 0 && cells2 == NULL)
        flat_len2 = 0;
    rows = (n + cols - 1) / cols;

    switch (kind) {
    case 0:  /* static: mask[seq_i]=flat[i%flat_len], hi/lo */
        _ce_mask_apply(n, cells, flat_len, lo, hi, out);
        break;
    case 1: {  /* scroll_h: offset=int(t*speed*cols)%cols, 网格 get */
        int offset = _ce_int_mod((long long)(t * speed * (double)cols), cols);
        for (i = 0; i < n; i++) {
            int row = i / cols;
            int col = (i % cols + offset) % cols;
            out[i] = (uint8_t)(_ce_grid_bit(cells, pat_rows, pat_cols, row, col)
                               ? hi : lo);
        }
        break;
    }
    case 2: {  /* scroll_v: rows=ceil(n/cols), offset=int(t*speed*rows)%rows */
        int offset = _ce_int_mod((long long)(t * speed * (double)rows), rows);
        for (i = 0; i < n; i++) {
            int row = (i / cols + offset) % rows;
            int col = i % cols;
            out[i] = (uint8_t)(_ce_grid_bit(cells, pat_rows, pat_cols, row, col)
                               ? hi : lo);
        }
        break;
    }
    case 3: {  /* blink: blink_on=(int(t*rate)%2==0), mask 门控 */
        int blink_on = _ce_int_mod((long long)(t * rate), 2) == 0;
        for (i = 0; i < n; i++) {
            int bit = (flat_len > 0) ? (cells[i % flat_len] != 0) : 0;
            out[i] = (uint8_t)(bit ? (blink_on ? hi : lo) : lo);
        }
        break;
    }
    case 4:  /* gradient: sin 波渐变（见 _ce_matrix_gradient） */
        _ce_matrix_gradient(t, speed, lo, hi, n, cells, flat_len, out);
        break;
    case 5: {  /* alternate: (int(t*rate)%2==0) ? 图案1 : 图案2 */
        int use_first = _ce_int_mod((long long)(t * rate), 2) == 0;
        _ce_mask_apply(n, use_first ? cells : cells2,
                       use_first ? flat_len : flat_len2, lo, hi, out);
        break;
    }
    case 6:  /* scan_row: cur_row=int(t*speed)%rows, row==cur_row */
    {
        int cur_row = _ce_int_mod((long long)(t * speed), rows);
        for (i = 0; i < n; i++)
            out[i] = (uint8_t)((i / cols == cur_row) ? hi : lo);
        break;
    }
    case 7:  /* scan_col: cur_col=int(t*speed)%cols, col==cur_col */
    {
        int cur_col = _ce_int_mod((long long)(t * speed), cols);
        for (i = 0; i < n; i++)
            out[i] = (uint8_t)((i % cols == cur_col) ? hi : lo);
        break;
    }
    case 8:  /* spiral: 螺旋点亮（见 _ce_matrix_spiral） */
        _ce_matrix_spiral(t, speed, cols, lo, hi, n, out);
        break;
    case 9:  /* explode: 圆环扩散（见 _ce_matrix_explode） */
        _ce_matrix_explode(t, speed, cols, lo, hi, n, out);
        break;
    default:
        break;
    }
}

/* ── T4b: K1 Joystick 类别求值（对齐 _joystick_circle :2394 /
 *    _joystick_figure8 :2441 / _joystick_scan :2485 / _joystick_wave_scan :2527）──
 * 全程 double: t/speed/phase_step/半径/中心是 Python float, math.sin/cos 是 f64 libm。
 * angle = t*speed*2π + seq_i*phase_step（Python 左结合: (((t*speed)*2)*M_PI)+phase）。
 * mode: 0=circle 1=figure8 2=scan 3=wave_scan; p[8] 参数槽复用:
 *   mode0: p[0]=cx p[1]=cy p[2]=radius_x p[3]=radius_y
 *   mode1: p[0]=cx p[1]=cy p[2]=scale
 *   mode2: p[0]=lo p[1]=hi p[2]=y_fixed
 *   mode3: p[0]=lo_x p[1]=hi_x p[2]=lo_y p[3]=hi_y
 * 输出: out_x[n]/out_y[n] = clamp[0,255](int(轴值*255)) — Python int() 截断逐位复刻。
 * 通道映射（x_ch/y_ch → motor_x/y/z）留在 Python 侧, 本内核只算数值。
 */
CE_API void ce_joystick_effect(double t, double speed, double phase_step,
                               int mode, const double* p, int n,
                               uint8_t* out_x, uint8_t* out_y)
{
    if (n <= 0)
        return;
    for (int i = 0; i < n; i++) {
        double phase = (double)i * phase_step;          /* seq_i * phase_step */
        double angle = t * speed * 2.0 * M_PI + phase;
        double xv, yv;
        switch (mode) {
        case 0:  /* circle: x=cx+rx*cos(angle); y=cy+ry*sin(angle) */
            xv = p[0] + p[2] * cos(angle);
            yv = p[1] + p[3] * sin(angle);
            break;
        case 1:  /* figure8: x=cx+scale*sin(angle); y=cy+scale*sin(2*angle)/2 */
            xv = p[0] + p[2] * sin(angle);
            yv = p[1] + p[2] * sin(2.0 * angle) / 2.0;
            break;
        case 2:  /* scan: x=lo+(hi-lo)*(sin(angle)+1)/2; y=y_fixed */
            xv = p[0] + (p[1] - p[0]) * (sin(angle) + 1.0) / 2.0;
            yv = p[2];
            break;
        case 3:  /* wave_scan: x=lo_x+(hi_x-lo_x)*(sin(angle)+1)/2;
                    y=lo_y+(hi_y-lo_y)*(cos(angle)+1)/2 */
            xv = p[0] + (p[1] - p[0]) * (sin(angle) + 1.0) / 2.0;
            yv = p[2] + (p[3] - p[2]) * (cos(angle) + 1.0) / 2.0;
            break;
        default: /* circle 兜底 */
            xv = p[0] + p[2] * cos(angle);
            yv = p[1] + p[3] * sin(angle);
            break;
        }
        int xi = (int)(xv * 255.0);                     /* Python int() 截断 */
        if (xi < 0) xi = 0; else if (xi > 255) xi = 255;
        out_x[i] = (uint8_t)xi;
        int yi = (int)(yv * 255.0);
        if (yi < 0) yi = 0; else if (yi > 255) yi = 255;
        out_y[i] = (uint8_t)yi;
    }
}

/* ── T4c: K1 Audio 类别求值（对齐 _audio_bpm_flash :2584 / _audio_bpm_chase :2615
 *    / _audio_bpm_wave :2649 / _audio_energy :2685 / _audio_color_beat :2718
 *    / _audio_section :2749）──────────────────────────────────────────────
 * beat_period = 60.0/bpm; beat_phase = (t % beat_period)/beat_period
 * （Python float % = fmod + 补正 → 被除数符号无关, 除数恒正 → 结果非负）。
 * mode: 0=bpm_flash 1=bpm_chase 2=bpm_wave 3=energy 4=color_beat 5=section
 * p[8] 槽位:
 *   mode0/1: p[0]=bpm p[1]=off_val p[2]=on_val
 *   mode2:   p[0]=bpm p[1]=phase_step p[2]=min p[3]=max
 *   mode3:   p[0]=bpm p[1]=min p[2]=max
 *   mode4:   p[0]=bpm（固定 8 色表; fade=1.0-beat_phase*0.5; 无 clamp 逐位复刻）
 *   mode5:   p[0]=bpm p[1]=verse_val p[2]=chorus_val p[3]=bars_per_section
 * 输出: out[n] 单通道; mode4 时 out[3n] = R,G,B 三联（全体设备同值）。
 */
static double _ce_py_mod(double x, double y)
{
    double m = fmod(x, y);
    if (m < 0.0) m += y;
    return m;
}

CE_API void ce_audio_effect(double t, int mode, const double* p, int n,
                            uint8_t* out)
{
    if (n <= 0)
        return;
    double bpm = p[0];
    double beat_period = 60.0 / bpm;
    double beat_phase = _ce_py_mod(t, beat_period) / beat_period;
    switch (mode) {
    case 0: { /* bpm_flash: val = hi if phase<0.1 else lo（全体同值） */
        uint8_t v = (beat_phase < 0.1) ? (uint8_t)p[2] : (uint8_t)p[1];
        for (int i = 0; i < n; i++) out[i] = v;
        break;
    }
    case 1: { /* bpm_chase: cur = int(t/beat_period) % n 单台亮 */
        int beat_idx = (int)(t / beat_period);
        int cur = beat_idx % n;
        if (cur < 0) cur += n;                          /* Python % 非负 */
        uint8_t on = (uint8_t)p[2], off = (uint8_t)p[1];
        for (int i = 0; i < n; i++) out[i] = (i == cur) ? on : off;
        break;
    }
    case 2: { /* bpm_wave: v=exp(-((beat_phase-seq_i*phase_step)%1.0)*5) 逐台 */
        double phase_step = p[1], lo = p[2], hi = p[3];
        for (int i = 0; i < n; i++) {
            double phase = (double)i * phase_step;
            double v = exp(-_ce_py_mod(beat_phase - phase, 1.0) * 5.0);
            double dv = (lo + v * (hi - lo));
            int iv;
            if (dv <= 0.0) iv = 0;
            else if (dv >= 255.0) iv = 255;
            else iv = (int)dv;
            out[i] = (uint8_t)iv;
        }
        break;
    }
    case 3: { /* energy: energy=exp(-beat_phase*4) 全体同值 */
        double lo = p[1], hi = p[2];
        double energy = exp(-beat_phase * 4.0);
        double dv = (lo + energy * (hi - lo));
            int iv;
            if (dv <= 0.0) iv = 0;
            else if (dv >= 255.0) iv = 255;
            else iv = (int)dv;
        for (int i = 0; i < n; i++) out[i] = (uint8_t)iv;
        break;
    }
    case 4: { /* color_beat: colors[beat_idx%8]; R/G/B=int(c*fade) 无 clamp */
        static const int cols[8][3] = {
            {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {255, 255, 0},
            {0, 255, 255}, {255, 0, 255}, {255, 128, 0}, {128, 0, 255},
        };
        int beat_idx = (int)(t / beat_period);
        int ci = beat_idx % 8;
        if (ci < 0) ci += 8;
        double fade = 1.0 - beat_phase * 0.5;
        int r = (int)((double)cols[ci][0] * fade);
        int g = (int)((double)cols[ci][1] * fade);
        int b = (int)((double)cols[ci][2] * fade);
        for (int i = 0; i < n; i++) {
            out[3 * i] = (uint8_t)r;
            out[3 * i + 1] = (uint8_t)g;
            out[3 * i + 2] = (uint8_t)b;
        }
        break;
    }
    case 5: { /* section: 段落交替 hi/lo（bar_period=beat_period*4） */
        double lo = p[1], hi = p[2], bars = p[3];
        double bar_period = beat_period * 4.0;
        double section_period = bar_period * bars;
        int section_idx = (int)(t / section_period);
        int m = section_idx % 2;
        if (m < 0) m += 2;                              /* Python % 非负 */
        uint8_t v = (m == 1) ? (uint8_t)hi : (uint8_t)lo;
        for (int i = 0; i < n; i++) out[i] = v;
        break;
    }
    default: { /* bpm_flash 兜底 */
        uint8_t v = (beat_phase < 0.1) ? (uint8_t)p[2] : (uint8_t)p[1];
        for (int i = 0; i < n; i++) out[i] = v;
        break;
    }
    }
}
/* ── T6: 泵预充电调度纯数学（对齐 src/engine/pump_precharge.py）────────
 * 调度器本体（register/tick/unregister 的事件列表状态机: _pending 列表、
 * fired/cancelled 标记、按触发窗口 [trigger, clip_start) 剪枝）是跨帧
 * 有状态逻辑, 留在 Python 原路径; 此处 C 化其纯数学部分, 供调用方组合:
 *   ce_precharge_lead_s            register: lead_s = max(0, lead_ms/1000)
 *   ce_precharge_trigger_position  register: trigger = max(0, start-lead_s)
 *   ce_precharge_value             register: level = clamp(int(raw), 0, 255)
 *   ce_precharge_channel           register: ch    = clamp(int(ch_1b)-1, 0, 511)
 *   ce_precharge_should_fire       tick: now=max(0,now) 后 trigger<=now<clip_start
 *   ce_precharge_build_frame       _fire_event: 512 帧 = 快照(或零) + 单字节
 * 参数映射: 上游先完成 Python 对象语义（precharge_level 的 `or 0` 与默认 13、
 * precharge_lead_ms 的 `or 1000.0` 与默认 1000.0）——C 接收已解析的数值。
 * 位级纪律（与 ce_wave_effect/ce_fader_effect 同）:
 *   全程 double; IEEE 除法/减法/比较与 Python float 逐位一致（MSVC
 *   /fp:precise 无 FMA 融合）; max(0.0,x) 用 `if (!(v > 0.0)) v = 0.0`
 *   复刻——Python max 对 -0.0/NaN 返回首参 +0.0, 该写法逐位等价;
 *   int()+clamp 先值域早退再 (int) 截断（C (int) 对超 int32 范围 double
 *   是 UB, 早退保证全定义域安全; 输入域排除 NaN——Python int(NaN) 抛
 *   ValueError, 调度器上游属性值不可能出现）。
 */

CE_API int ce_precharge_value(double level_raw)
{
    /* Python: max(0, min(255, int(raw))) — int() 向零截断 */
    if (level_raw <= 0.0)
        return 0;
    if (level_raw >= 255.0)
        return 255;
    return (int)level_raw;
}

CE_API int ce_precharge_channel(double ch_1based)
{
    /* Python: max(0, min(511, int(ch_1based) - 1)) */
    if (ch_1based <= 1.0)
        return 0;
    if (ch_1based >= 512.0)
        return 511;
    return (int)ch_1based - 1;
}

CE_API double ce_precharge_lead_s(double lead_ms)
{
    /* Python: max(0.0, lead_ms / 1000.0) — 已解析的 lead_ms（毫秒） */
    double v = lead_ms / 1000.0;
    if (!(v > 0.0))
        v = 0.0;
    return v;
}

CE_API double ce_precharge_trigger_position(double start_time, double lead_s)
{
    /* Python: max(0.0, start_time - lead_s) */
    double v = start_time - lead_s;
    if (!(v > 0.0))
        v = 0.0;
    return v;
}

CE_API int ce_precharge_should_fire(double now, double trigger_position,
                                    double clip_start)
{
    /* Python tick: now = max(0.0, float(current_position));
     * 触发窗口 = trigger_position <= now < clip_start（左闭右开） */
    if (!(now > 0.0))
        now = 0.0;
    return (trigger_position <= now && now < clip_start) ? 1 : 0;
}

CE_API void ce_precharge_build_frame(const uint8_t* snapshot,
                                     int snapshot_valid, int channel,
                                     int level, uint8_t* out)
{
    /* Python _fire_event: data = np.zeros(512, u8); 若 universe 在
     * _universe_snapshot 中则 data[:] = 快照; data[channel] = level */
    if (snapshot_valid && snapshot != NULL) {
        for (int i = 0; i < 512; i++)
            out[i] = snapshot[i];
    } else {
        for (int i = 0; i < 512; i++)
            out[i] = 0;
    }
    out[channel] = (uint8_t)level;
}
/* ── T6: compute() 批量序列求值（对齐 src/ui/effect_engine.py compute() :1237
 * 帧循环 + _wave_generic :1444 逐设备求值）────────────────────────────────
 * 一次性求值 n_frames × n_fixtures 的 u8 值矩阵, 行主序 out[f*n+i]。
 * 语义逐字对齐 compute() 的波形路径:
 *   t = frame_idx / fps（Python float 除法, 位级同 IEEE f64 相除）
 *   每帧复用 ce_wave_effect（T4 K1 已 10 万次 fuzz 位级一致）:
 *   phase = i*phase_step; v = _ce_wave(t*speed+phase); val = int(lo+v*(hi-lo));
 *   clamp[0,255]。wave_fn_idx 同 ce_wave_effect（0-7 = WAVE_FUNCS 8 波形）。
 * 参数映射: fps=compute() 的 fps; speed/phase_step/min/max/wave 由调用方从
 *   params 取出（min/max 经 int() 截断后传入, 同 _wave_generic）; ch_type 是
 *   通道名与值无关, 帧对象构造（ef.values[orig_i][ch] = 值）留 Python。
 * 覆盖范围: 波形类效果（wave_* 8 预设）; 音频/包络/摇杆等逐帧分支仍走 Python。
 * fps==0/NaN: compute() 中 Python 会 ZeroDivisionError, 此处防御性早退。
 */
CE_API void ce_compute_sequence(
    double fps, double speed, double phase_step,
    double lo, double hi, int wave_fn_idx,
    int n_frames, int n_fixtures,
    uint8_t* out)
{
    int f;
    if (fps == 0.0 || isnan(fps))
        return;
    if (n_frames <= 0 || n_fixtures <= 0 || out == NULL)
        return;
    for (f = 0; f < n_frames; f++) {
        double t = (double)f / fps;
        ce_wave_effect(t, speed, phase_step, lo, hi, wave_fn_idx,
                       n_fixtures, out + (size_t)f * (size_t)n_fixtures);
    }
}

/* ── T6b: compute() 批量序列求值 — OpenMP 帧并行版（离线批量场景）
 *
 * 数学与 ce_compute_sequence 逐字相同: 每帧只依赖 (f, fps) 与标量参数,
 * 帧间无共享状态 → 并行只改执行顺序, 输出逐字节一致（位级等价, 静态度分配）。
 * 适用场景: 大工程离线批量（至冬7.0 = 11382 台 × 160 帧 ≈ 1.8M 次波形求值,
 * 串行 ~18-22ms/段）。单帧任务 ≈ 110-150µs（11382 台）——大于 100µs 并行
 * 阈值（前 7 次并行负优化教训: 小任务 OpenMP fork/spin 抢核）。
 *
 * 门控（三条件全满足才进并行区, 否则走与 ce_compute_sequence 完全相同的
 * 串行循环）:
 *   1. n_frames >= 8                （并行区至少分摊 8 任务）
 *   2. n_frames * n_fixtures >= 200K（≈ 2ms 串行预算, 覆盖 fork 开销）
 *   3. omp_get_max_threads() >= 2   （单线程运行时不付 fork 代价）
 * 调度 schedule(static): 帧间负载均匀（每帧固定 n_fixtures 次求值）。
 * 实时约束: 调用方 = EffectEngine.compute（PyQt 主线程/编曲离线）, 不在
 * DMX tick 热路径; 并行区内无任何 Python/全局状态, 纯 C 线程安全。
 */
CE_API void ce_compute_sequence_omp(
    double fps, double speed, double phase_step,
    double lo, double hi, int wave_fn_idx,
    int n_frames, int n_fixtures,
    uint8_t* out)
{
    int f;
    if (fps == 0.0 || isnan(fps))
        return;
    if (n_frames <= 0 || n_fixtures <= 0 || out == NULL)
        return;
    if (n_frames >= 8 && omp_get_max_threads() >= 2
        && (int64_t)n_frames * (int64_t)n_fixtures >= 200000LL) {
        #pragma omp parallel for schedule(static)
        for (f = 0; f < n_frames; f++) {
            double t = (double)f / fps;
            ce_wave_effect(t, speed, phase_step, lo, hi, wave_fn_idx,
                           n_fixtures, out + (size_t)f * (size_t)n_fixtures);
        }
        return;
    }
    for (f = 0; f < n_frames; f++) {
        double t = (double)f / fps;
        ce_wave_effect(t, speed, phase_step, lo, hi, wave_fn_idx,
                       n_fixtures, out + (size_t)f * (size_t)n_fixtures);
    }
}
/* ── T5: clip 锚点（逐位对齐 PlaybackEngine._get_clip_anchor :7442-7480）──
 *
 * 一 clip 的 n 个有效帧（每帧 512 通道 u8; 调用方已按 Python fixed 逻辑
 * 展平/截断/补零至 512）→ 每通道锚点 = (max+min)/2:
 *   1. min_buf 初值 255 / max_buf 初值 0（int16 中间态, 同 np.full(512,255,int16)）
 *   2. 逐帧逐元素 int16 最小/最大（复刻 np.minimum/maximum; 值域恒 [0,255],
 *      逐元素无累积——numpy int16 模 2^16 语义与 C int16_t 一致, 且永不触界）
 *   3. anchor[c] = ((f32)max[c] + (f32)min[c]) / 2.0f
 *      （同 Python: (max_buf.astype(f32)+min_buf.astype(f32))/2.0;
 *        和 ≤ 510 < 2^24 在 f32 中精确, 除 2 亦精确——位级一致）
 *   4. 非 active 通道清零; n_frames==0 → 全 0
 *      （复刻 seen |= ch_active 语义: ≥1 有效帧时 seen==ch_active,
 *        0 帧时 idx 全 False → anchor 全 0）
 *
 * 输入:
 *   frames     u8  [n_frames*512]  row-major 有效帧数据
 *   n_frames   int                 有效帧数（universe 过滤由调用方完成）
 *   ch_active  u8  [512]  0/1 激活（非零视为激活, 同 numpy bool 转换）
 * 输出:
 *   anchor     f32 [512]  每通道锚点
 */
CE_API void ce_clip_anchor(const uint8_t* frames, int n_frames,
                           const uint8_t* ch_active, float* anchor)
{
    int16_t min_buf[512], max_buf[512];
    int c, f;
    if (n_frames <= 0) {
        for (c = 0; c < 512; c++)
            anchor[c] = 0.0f;
        return;
    }
    for (c = 0; c < 512; c++) {
        min_buf[c] = 255;
        max_buf[c] = 0;
    }
    for (f = 0; f < n_frames; f++) {
        const uint8_t* row = frames + (size_t)f * 512;
        for (c = 0; c < 512; c++) {
            int16_t v = (int16_t)row[c];
            if (v < min_buf[c])
                min_buf[c] = v;
            if (v > max_buf[c])
                max_buf[c] = v;
        }
    }
    for (c = 0; c < 512; c++) {
        if (ch_active[c])
            anchor[c] = ((float)max_buf[c] + (float)min_buf[c]) / 2.0f;
        else
            anchor[c] = 0.0f;
    }
}
/* ── T6: 效果引擎设备排序（对齐 src/ui/effect_engine.py sort_fixtures :358）──
 * 6 种 sort_mode: 0=index 1=linear_x 2=linear_y 3=radial 4=grid_row 5=grid_col
 * 排序键（与 Python sorted(key=...) 相同: 键一次构建, 后按元组逐分量比较）:
 *   index     (base, idx)
 *   linear_x  (base, x, y, idx)
 *   linear_y  (base, y, x, idx)
 *   radial    (base, atan2(y,x), x, y, idx)
 *   grid_row  (base, round(y/50), x, idx)
 *   grid_col  (base, round(x/50), y, idx)
 * base = base_rank[i]（调用方按 base_order_ids 去重 rank 传入, -1=未收录）;
 *   未收录 → fallback_rank + 原索引, fallback_rank = n_base_rank + n + 1000
 *   （= Python len(base_rank) + len(indexed) + 1000）。
 * 比较语义 = CPython 元组逐分量 < / >（NaN 双假 → 视为相等继续下一分量）;
 * 末分量 idx 唯一 → 有限坐标下键全序唯一, 稳定性自动满足（仍用稳定归并排序
 * 兜底）。round() = 银行家舍入（CPython round 对二进制浮点精确取整, <2^53
 * 时 floor+frac 判 .5 同义）; atan2/floor 用 CRT（CPython 同源, 逐位一致）。
 * 坐标契约: 有限 double; 缺失设备坐标由调用方补 0.0（同 positions.get(·,{})）。
 * 不做: __route_animator__ 与 base_order_ids 整序快路径（纯 id 字典重排,
 *   非数值排序, 留在 Python——实测当前 src 该路径才是真正生效的排序）。
 * 注: 当前 src 的 sort 路径里 base_rank 恒全未收录（收录即走 by_id 早退）,
 *   故 base_key ≡ fallback_rank+idx, 排序结果恒=原序; 内核仍完整实现
 *   base_rank+位置分量语义, src 侧一旦解除遮蔽即保持逐位一致。
 */

typedef struct {
    int32_t idx;       /* 原索引（= enumerate 序） */
    int64_t base;      /* 已解析 base rank（fallback 已并入） */
    double x, y;       /* 坐标（缺失 → 0.0） */
    double ang;        /* radial: atan2(y, x) */
    int64_t gy, gx;    /* grid_row 用 gy=round(y/50); grid_col 用 gx=round(x/50) */
} ce_sort_item;

/* CPython 元组分量比较: a<b → -1, a>b → +1, 否则（含 NaN 双假）→ 0 继续 */
static int ce_cmp_dbl(double a, double b)
{
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
}

static int ce_cmp_i64(int64_t a, int64_t b)
{
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
}

/* Python round() 对 double 的银行家舍入（半距取偶; |v|<2^53 与 CPython 一致） */
static int64_t ce_round_half_even(double v)
{
    double f = floor(v);
    int64_t r = (int64_t)f;
    double frac = v - f;
    if (frac > 0.5) {
        r += 1;
    } else if (frac == 0.5 && (r & 1)) {
        r += 1;
    }
    return r;
}
/* ── T6: 安全联锁（对齐 src/engine/safety_interlock.py SafetyInterlock.process）──
 *
 * 插入于 output_smoother 与 _send_to_hw 之间；每 universe 每 tick 调用。
 * 逐帧状态机（状态/计数/上帧值由调用方持有，本函数就地更新）:
 *   违规检测（min/max 越界 + 变化率越界）→ 计数/状态迁移（六态: DISARMED/
 *   ARMED/WARNING/SOFT_LIMIT/HARD_STOP，全局 BLACKOUT 由本函数产出标志）→
 *   动作（HARD_STOP 写 safe_default / WARNING+ 钳位 min-max）→ 存储上帧值。
 *
 * 输入契约:
 *   result    f32[frame_len] 就地修改（= Python result 缓冲，调用方须拷贝输入）
 *   universe  本帧 universe
 *   cu/cs/cn  [n] 各约束 universe / start_channel / num_channels（start ≥ 0）
 *   cmin/cmax/crate/cdef  [n][cn[i]] 各约束数组（调用方按行分配）
 *   states    [n] in/out 0=DISARMED 1=ARMED 2=WARNING 3=SOFT_LIMIT 4=HARD_STOP
 *   counts    [n] in/out 违规计数（= _violation_count，get 默认 0）
 *   has_prev  [n] in/out 1=有上帧值（= idx in _last_values）
 *   prev_len  [n] in/out 上帧值长度（帧长变化时 Python 以 len(prev)>=length 判断）
 *   prevs     [n][cap] in/out 上帧值（调用方分配，cap ≥ cn[i]；cn=0 时可为 NULL）
 *   blackout_ratio  c_double（Python float 原值直传，double 除法与 int/int 一致）
 * 输出:
 *   blackout  out 1=本次触发全局 BLACKOUT（此时 out 填全 0）
 *   out       u8[frame_len] 最终帧 round→clip(0,255)→uint8（无 blackout 时）
 *
 * 位级等价纪律（numpy 2.x 实测钉死）:
 *   1. np.clip(v,mn,mx) = minimum(maximum(v,mn),mx)；NaN 传播:
 *      a 为 NaN → 返回 a 原位型，否则 b 为 NaN → 返回 b，否则数值比较
 *      （-0.0 平局返回第二参: max(-0,0)=+0, max(0,-0)=-0）
 *   2. 变化率 |x-prev| > rate 用 float32 fabsf；NaN 参与比较恒 False（同 numpy）
 *   3. 每约束严格顺序: 检测 → 计数/迁移 → 动作 → 存上帧（同列表顺序，重叠约束
 *      后写可见前写，与 Python 顺序循环一致）
 *   4. 最终 np.round 银行家舍入（半偶，-0.5→-0.0 保号）；clip 后 NaN→0 转 uint8
 *   5. blackout 比例 (double)hs/(double)n 与 Python int/int 除法逐位一致
 * 契约外: start_channel < 0（Python 侧广播异常，属崩溃路径，不承载）
 */
/* NaN 判定走整数位检查: MSVC 优化器（/fp:precise）把 NaN 值域抽象为
 * 「任意 NaN 等价」，常量折叠时会把 sNaN 位型替换为静默化（quiet）位型
 * （最小复现实测: memcpy 注入的 sNaN 在折叠路径上被换成 qNaN 常量）。
 * 整数位检查保证选择逻辑作用于原始位型，杜绝任何值域改写; 排除 NaN 后
 * `(a > b) ? a : b` 与 SSE maxss 逐位一致（含 ±0.0 平局返回第二参），
 * 即使折叠也安全。 */
static int ce_f32_is_nan(float x)
{
    uint32_t u;
    memcpy(&u, &x, 4);
    return ((u & 0x7F800000u) == 0x7F800000u) && ((u & 0x007FFFFFu) != 0u);
}

static float ce_np_maxf(float a, float b)
{
    if (ce_f32_is_nan(a)) return a;
    if (ce_f32_is_nan(b)) return b;
    return (a > b) ? a : b;
}

static float ce_np_minf(float a, float b)
{
    if (ce_f32_is_nan(a)) return a;
    if (ce_f32_is_nan(b)) return b;
    return (a < b) ? a : b;
}

static int ce_sort_item_cmp(const ce_sort_item* a, const ce_sort_item* b, int mode)
{
    int c = ce_cmp_i64(a->base, b->base);
    if (c != 0) return c;
    switch (mode) {
    case 1:  /* linear_x: (base, x, y, idx) */
        c = ce_cmp_dbl(a->x, b->x);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->y, b->y);
        if (c != 0) return c;
        break;
    case 2:  /* linear_y: (base, y, x, idx) */
        c = ce_cmp_dbl(a->y, b->y);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->x, b->x);
        if (c != 0) return c;
        break;
    case 3:  /* radial: (base, atan2(y,x), x, y, idx) */
        c = ce_cmp_dbl(a->ang, b->ang);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->x, b->x);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->y, b->y);
        if (c != 0) return c;
        break;
    case 4:  /* grid_row: (base, round(y/50), x, idx) */
        c = ce_cmp_i64(a->gy, b->gy);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->x, b->x);
        if (c != 0) return c;
        break;
    case 5:  /* grid_col: (base, round(x/50), y, idx) */
        c = ce_cmp_i64(a->gx, b->gx);
        if (c != 0) return c;
        c = ce_cmp_dbl(a->y, b->y);
        if (c != 0) return c;
        break;
    default: /* index: (base, idx) */
        break;
    }
    return ce_cmp_i64(a->idx, b->idx);
}

/* 稳定自底向上归并排序（左先出 <= 保稳定, 与 Python list.sort 一致） */
static void ce_sort_items(ce_sort_item* items, ce_sort_item* tmp, int n, int mode)
{
    for (int width = 1; width < n; width *= 2) {
        for (int lo = 0; lo + width < n; lo += 2 * width) {
            int mid = lo + width;
            int hi = lo + 2 * width;
            if (hi > n) hi = n;
            int i = lo, j = mid, k = lo;
            while (i < mid && j < hi) {
                if (ce_sort_item_cmp(&items[i], &items[j], mode) <= 0)
                    tmp[k++] = items[i++];
                else
                    tmp[k++] = items[j++];
            }
            while (i < mid) tmp[k++] = items[i++];
            while (j < hi) tmp[k++] = items[j++];
            for (k = lo; k < hi; k++) items[k] = tmp[k];
        }
    }
}
/* ── T5: phase_spread 相位铺开 — O(N) 时间相位层（对齐 effect_engine.py
 *       _compute_unit_values :821 虚拟单元路径 + _wave_generic :1444）
 *
 * 背景: phase_spread>0 时 Python 为每个虚拟单元 k 单独跑一次 fn（每次 fn 对
 * 全部 N 单元求值）→ O(N²)（11382 台大项目卡顿源之一）。本内核取对角直接版:
 *   第 k 个单元: t_k = effect_t + phase_spread * (k / max(1, N-1))  [k/denom 真除法 f64]
 *   out[k] = fn(t_k)[k] —— 各单元值 (t_k, i) 是独立纯函数、无跨单元累积
 *   （_ce_wave 无状态）→ O(N²) 嵌套循环只写对角 i==k，折叠为 O(N) 直接求对角，
 *   每个 i==k 迭代与原嵌套循环逐表达式同序（t_k → phase → v → val → 截断），
 *   输出逐位一致（fuzz 对拍 100% 位级等价, ce_fuzz_phasespread.py）。
 * 波形内层与 ce_wave_effect 共用 _ce_wave（8 波形, wave_fn_idx 同 T4）。
 * 位级等价: 全程 double 同 Python; int()+clamp 先值域早退再截断（同
 * ce_fader_effect, C (int) 超 int32 范围是 UB）。
 * B2-4 加固: 入口对 spread 复刻 Python 侧 max(0.0, min(1.0, x)) clamp
 * （NaN → 1.0, ±inf 有界, -0.0 → +0.0）; val NaN → 0（CE 纪律, 同
 * ce_mix_clips）。契约内 [0,1] 恒等 → 门1 哈希零漂移。
 */
CE_API void ce_phase_spread(
    double effect_t, double phase_spread,
    double speed, double phase_step, double lo, double hi,
    int wave_fn_idx, int n, uint8_t* out)
{
    int denom, k;
    if (n <= 0)
        return;
    /* B2-4: 契约外 NaN/±inf spread 防御。Python 侧 hook 先
     * max(0.0, min(1.0, phase_spread))（effect_engine.py:1142）: min/max
     * 与 NaN 比较恒 False → 第一参存活 → NaN → 1.0（满铺开, 实测复刻）;
     * +inf → 1.0, -inf/-0.0 → 0.0（max(0.0, x) 同）。正常域 [0,1] 恒等 →
     * 位级零漂移。 */
    if (isnan(phase_spread))
        phase_spread = 1.0;
    else if (phase_spread <= 0.0)
        phase_spread = 0.0;          /* 含 -0.0 → +0.0, 同 Python max(0.0, ...) */
    else if (phase_spread > 1.0)
        phase_spread = 1.0;
    denom = (n > 1) ? (n - 1) : 1;   /* max(1, n-1) */
    for (k = 0; k < n; k++) {
        double t_k = effect_t + phase_spread * ((double)k / (double)denom);
        double phase = (double)k * phase_step;   /* seq_i * phase_step, i==k */
        double v = _ce_wave(t_k * speed + phase, wave_fn_idx);
        double val = lo + v * (hi - lo);
        if (isnan(val)) {
            out[k] = 0;   /* B2-4: CE 纪律 clamp 后 isnan → 0（同 ce_mix_clips）;
                             NaN 比较恒假会落入 else 的 (int)NaN UB, 必须先早退 */
        } else if (val <= 0.0) {
            out[k] = 0;
        } else if (val >= 255.0) {
            out[k] = 255;
        } else {
            out[k] = (uint8_t)(int)val;   /* int() 截断 */
        }
    }
}

/* ── T5b: phase_spread 逐块（per_shape 时钟同步）— 每块独立 O(m) 对角折叠
 *
 * per_shape 分支（effect_engine.py _run_per_shape_units :1214-1234 +
 * _compute_unit_values :1145-1175）: 每单元块 m 个虚拟单元, 块内 seq 从 0
 * 重启（k=0..m-1）, denom = max(1, m-1), t_k = effect_t + spread*(k/denom),
 * phase = k*phase_step（per_shape 无矩阵投影 → spatial_phase_index = seq_i）;
 * 数学与 ce_phase_spread 逐块完全同构（_ce_wave 无状态纯函数）→ 按块折叠
 * 对角 O(Σm) = O(N)。输入:
 *   within[i] — 链序位置 i 在其块内的单元局部序号 (0..m_i-1)
 *   m_i[i]    — 位置 i 所属块的单元数（m=1 单件尾块同 flat n=1）
 * 输出 out[i] = 该块 k=within[i] 的对角值（散写 values[orig_i] 的位级一致源）。
 * 位级纪律全同 ce_phase_spread（B2-4 入口 clamp / B2-2 值域早退 / NaN→0）。
 */
CE_API void ce_phase_spread_blocks(
    double effect_t, double phase_spread,
    double speed, double phase_step, double lo, double hi,
    int wave_fn_idx, int n, const int32_t* within, const int32_t* m_i,
    uint8_t* out)
{
    int i;
    if (n <= 0 || within == NULL || m_i == NULL || out == NULL)
        return;
    if (isnan(phase_spread))
        phase_spread = 1.0;
    else if (phase_spread <= 0.0)
        phase_spread = 0.0;
    else if (phase_spread > 1.0)
        phase_spread = 1.0;
    for (i = 0; i < n; i++) {
        int k = (int)within[i];
        int m = (int)m_i[i];
        int denom = (m > 1) ? (m - 1) : 1;   /* max(1, m-1) */
        double t_k = effect_t + phase_spread * ((double)k / (double)denom);
        double phase = (double)k * phase_step;
        double v = _ce_wave(t_k * speed + phase, wave_fn_idx);
        double val = lo + v * (hi - lo);
        if (isnan(val)) {
            out[i] = 0;
        } else if (val <= 0.0) {
            out[i] = 0;
        } else if (val >= 255.0) {
            out[i] = 255;
        } else {
            out[i] = (uint8_t)(int)val;
        }
    }
}

CE_API void ce_sort_fixtures(const double* xs, const double* ys,
                             const int64_t* base_rank, int n_base_rank, int n,
                             int mode, int32_t* out)
{
    if (n <= 0 || xs == NULL || ys == NULL || out == NULL)
        return;
    if (mode < 0 || mode > 5)
        mode = 0;
    int64_t fallback_rank = (int64_t)n_base_rank + (int64_t)n + 1000;
    ce_sort_item* items = (ce_sort_item*)malloc((size_t)n * sizeof(ce_sort_item));
    ce_sort_item* tmp = (ce_sort_item*)malloc((size_t)n * sizeof(ce_sort_item));
    if (items == NULL || tmp == NULL) {
        free(items);
        free(tmp);
        return;   /* 分配失败: 输出不变, 调用方回退原路径 */
    }
    for (int i = 0; i < n; i++) {
        items[i].idx = (int32_t)i;
        items[i].base = (base_rank != NULL && base_rank[i] >= 0)
            ? base_rank[i] : fallback_rank + i;
        items[i].x = xs[i];
        items[i].y = ys[i];
        items[i].ang = atan2(ys[i], xs[i]);
        items[i].gy = ce_round_half_even(ys[i] / 50.0);
        items[i].gx = ce_round_half_even(xs[i] / 50.0);
    }
    ce_sort_items(items, tmp, n, mode);
    for (int i = 0; i < n; i++)
        out[i] = items[i].idx;
    free(items);
    free(tmp);
}
/* ── T5: dragon 空间相位家族（对齐 effect_presets_v26.py
 *       _motor_reverse_dragon :364 / _motor_gradient_dragon :396）
 *
 * 逐设备空间相位: progress = seq_i / max(1, n-1)，reverse → 1.0 - 该值
 *   （先算除法再减，同 Python）; phase = t*speed + progress*phase_spread;
 *   val = 128 + amp*127*sin(2π*phase)，gradient → amp = 0.3 + 0.7*progress
 *   否则 amp = amplitude; 输出 _clamp = max(0, min(255, int(v)))。
 * 位级等价: 全程 f64 同 Python; sin 参数 (2.0*M_PI)*phase 左结合
 *   （2*math.pi 同值）; int()+clamp 先值域早退再截断。
 * B2-4 加固（同 ce_phase_spread）: val NaN → 0 早退（CE 纪律, 同 ce_mix_clips）
 *   ——NaN 比较恒假会落入 else 的 (int)NaN UB, 必须先早退。注意: 与
 *   ce_phase_spread 不同, 入口不加 spread clamp——Python dragon 路径
 *   （effect_presets_v26.py :364/:396）不 clamp phase_spread（ParamSpec 上限
 *   3.0）, 加 clamp 会破坏契约内 [0.1,3.0] 的位级等价（fuzz 对拍直传）。
 */
CE_API void ce_dragon_phase(
    double t, double speed, double phase_spread, double amplitude,
    int reverse, int gradient, int n, uint8_t* out)
{
    int denom, i;
    if (n <= 0)
        return;
    denom = (n > 1) ? (n - 1) : 1;   /* max(1, n-1) */
    for (i = 0; i < n; i++) {
        double progress = (double)i / (double)denom;
        double amp, phase, val;
        if (reverse)
            progress = 1.0 - progress;
        amp = gradient ? (0.3 + 0.7 * progress) : amplitude;
        phase = t * speed + progress * phase_spread;
        val = 128.0 + amp * 127.0 * sin(2.0 * M_PI * phase);
        if (isnan(val)) {
            out[i] = 0;   /* B2-4: NaN 参数（t/speed/spread/amplitude）→ 0,
                             确定性无 UB（同 ce_phase_spread 纪律） */
        } else if (val <= 0.0) {
            out[i] = 0;
        } else if (val >= 255.0) {
            out[i] = 255;
        } else {
            out[i] = (uint8_t)(int)val;   /* int() 截断 */
        }
    }
}
/* ── T5: 音频分析层（对齐 src/ui/audio_driver.py + src/audio/music_structure_analyzer.py）─
 *
 * 目标函数（每 25ms 帧频调用, 纯数学, 无 FFT）:
 *   1. AudioAnalyzer._analyze_file_at_position / _analyze_mic_buffer :705-707/:739-741
 *        buf = buf * gain                     # f32 逐元素 × (float32)gain
 *        rms  = float(np.sqrt(np.mean(buf**2)))   # f32 平方 → 均值 → sqrtf
 *        peak = float(np.max(np.abs(buf)))        # f32 fabs + max(NaN 传播)
 *   2. music_structure_analyzer._spectral_flatness :96-104（全程 f64 纯数学）
 *        arr = [max(b, 1e-10) for b in bands]
 *        geo_mean  = math.exp(sum(math.log(x) for x in arr) / n)
 *        arith_mean = sum(arr) / n
 *        return geo_mean / max(arith_mean, 1e-10)
 *
 * 位级等价纪律（numpy 2.4.4 源码 loops_utils.h.src FLOAT_pairwise_sum 逐行复刻,
 * 实测 4000/4000 对拍确认）:
 *   1. f32 求和 = numpy add.reduce 逐位一致: 0.0f + pairwise_sum
 *      - n<8: 累加器从 -0.0f 起（保留 -0 语义）
 *      - 8<=n<=128: 8 累加器 r[k] += a[i+k]（i=8 起步, 步长 8）,
 *        树合并 ((r0+r1)+(r2+r3))+((r4+r5)+(r6+r7)), 尾部 n%8 个元素串行追加
 *      - n>128: 递归二分, n2 = n/2 - (n/2)%8, 左半 + 右半（f32 加法）
 *   2. mean = sum / (float)n（f32 除法; NEP50 弱标量 python int → f32）
 *   3. sqrtf/fabsf/乘法/加法 = IEEE 正确舍入（/fp:precise 禁 FMA/重关联）
 *   4. max 的 NaN 传播同 numpy maximum.reduce（scalar_max: A>=B || isnan(A)）;
 *      +0.0 胜 -0.0; 最后一个 NaN 的位模式胜出（与标量序 reduce 同）
 *   5. f64 路径（flatness）: log/exp 用 CRT（CPython math 模块同源 ucrt, 300k
 *      对拍 0 差异）; max(b, 1e-10) = (b < 1e-10) ? 1e-10 : b（NaN → b, 同 Python max）
 *   6. ★ Python 3.13 内建 sum() 对浮点 = Neumaier 补偿求和（CPython 3.12+
 *      gh-100425, 非串行!）: f_result = 0.0 + a[0], 余项 Neumaier, 收尾
 *      c!=0 且有限才补偿。两处 sum() 均按此复刻（200k 对拍 0 差异）
 *   7. 空输入: numpy np.max 会抛异常, 本内核防御性返回 0.0（仅此点与
 *      原逻辑行为不同, 原调用路径 buf 恒为 chunk 长度不会触发）
 */

/* numpy FLOAT_pairwise_sum 逐位复刻（f32 累加, 与输入数组同序访问） */
static float _ce_pairwise_sum_f32(const float* a, int n)
{
    if (n < 8) {
        float res = -0.0f;
        for (int i = 0; i < n; i++)
            res += a[i];
        return res;
    }
    else if (n <= 128) {
        float r[8], res;
        r[0] = a[0]; r[1] = a[1]; r[2] = a[2]; r[3] = a[3];
        r[4] = a[4]; r[5] = a[5]; r[6] = a[6]; r[7] = a[7];
        int i = 8;
        for (; i < n - (n % 8); i += 8) {
            r[0] += a[i + 0]; r[1] += a[i + 1];
            r[2] += a[i + 2]; r[3] += a[i + 3];
            r[4] += a[i + 4]; r[5] += a[i + 5];
            r[6] += a[i + 6]; r[7] += a[i + 7];
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) +
              ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++)
            res += a[i];
        return res;
    }
    else {
        int n2 = n / 2;
        n2 -= n2 % 8;
        return _ce_pairwise_sum_f32(a, n2) + _ce_pairwise_sum_f32(a + n2, n - n2);
    }
}

/* 帧能量: rms/peak（buf 为 float32 采样, gain 为 Python float → f32 弱标量） */
CE_API void ce_audio_frame_energy(const float* buf, int n, double gain,
                                  double* out_rms, double* out_peak)
{
    if (n <= 0) {
        *out_rms = 0.0;
        *out_peak = 0.0;
        return;
    }
    float g = (float)gain;
    float* sq = (float*)malloc((size_t)n * sizeof(float));
    if (sq == NULL) {
        *out_rms = 0.0;
        *out_peak = 0.0;
        return;
    }
    float peak = 0.0f;
    int has_peak = 0;
    for (int i = 0; i < n; i++) {
        float v = buf[i] * g;          /* buf * gain, f32 */
        float a = fabsf(v);            /* np.abs, f32 */
        if (isnan(a)) {
            peak = a;                  /* NaN 传播（maximum.reduce: 后见者胜）*/
            has_peak = 1;              /* ★ 首个元素即 NaN 时也占用位, 防后续
                                          有限值经 !has_peak 路径覆盖 NaN */
        } else if (!has_peak || a > peak) {
            peak = a;
            has_peak = 1;
        }
        sq[i] = v * v;                 /* buf ** 2, f32 */
    }
    float sum = 0.0f + _ce_pairwise_sum_f32(sq, n);  /* np.sum: 恒等 0.0f 起 */
    free(sq);
    float mean = sum / (float)n;       /* np.mean: f32 除法 */
    *out_rms = (double)sqrtf(mean);    /* np.sqrt → float() 加宽 */
    *out_peak = (double)peak;
}

/* Python 3.13 内建 sum() 浮点路径逐位复刻（CPython 3.12+ gh-100425:
 * start=0 首元素经 PyNumber_Add(0, x0) 得 f_result, 之后 Neumaier 补偿求和,
 * 收尾 c!=0 且有限才补偿——补 +0.0 初值（0.0 + -0.0 = +0.0 保号）） */
static double _ce_py_sum_f64(const double* a, int n)
{
    if (n <= 0)
        return 0.0;
    double f_result = 0.0 + a[0];
    double c = 0.0;
    for (int i = 1; i < n; i++) {
        double x = a[i];
        double t = f_result + x;
        if (fabs(f_result) >= fabs(x))
            c += (f_result - t) + x;
        else
            c += (x - t) + f_result;
        f_result = t;
    }
    if (c != 0.0 && isfinite(c))
        f_result += c;
    return f_result;
}

/* ── T6: 空间聚块 / 方向变换排序内核（对齐 src/ui/effect_engine.py
 *       apply_spatial_scope :500 / apply_effect_direction :451）─────────────
 *
 * 背景: 两者均为「效果默认排序之上的一次性重排」（compute 时调用, 不进每帧
 * 热路径）; 输出 = 原索引排列（int32 数组）, 调用方按序映射 fixture。
 *
 * ce_effect_direction(n, mode, out): 方向变换纯排列内核。方向串 → mode 映射
 * 由调用方完成（字符串比较是 Python 语义, 不进 C）:
 *   mode 0 = 恒等: "default"/"left_to_right"/"front_to_back"/"diag_tl_br"/
 *                  "diag_bl_tr"/未知串（Python 的 return items 分支）
 *   mode 1 = 反向: "reverse"/"right_to_left"/"back_to_front"/"diag_br_tl"/
 *                  "diag_tr_bl"（list(reversed(items))）
 *   mode 2 = center_out / mode 3 = edge_in
 * center_out/edge_in 的 Python 键 = (abs(i - center), i), center = (n-1)/2.0;
 * 整数等价键 |2i-(n-1)|（n<2^53 时该浮点比较与整数比较逐位一致, fuzz 实证）;
 * 同键稳定排序保 i 升序（= 元组第二分量）; edge_in = 整体反序（reverse=True
 * 作用于完整排序结果, 非逐键反序）。n<=1 或未知 mode → 恒等。
 *
 * ce_spatial_scope(item_fids, n, pos_fids, pos_x, pos_y, m,
 *                  group_fids, group_lens, g, out): per_shape 聚块内核。
 * scope 归一化（normalize_spatial_scope :480）留在 Python 侧 —— 仅当结果为
 * per_shape 时才调用本内核, 其余分支调用方直接返回恒等。算法逐字对齐
 * apply_spatial_scope :500:
 *   1. 组过滤: 组内成员（已去空串, group_lens 即过滤后长度）<2 的组整体
 *      跳过, 不参与 fid 映射（len(fids) < 2 → continue）
 *   2. 映射: fid → 首个含它的保留组（setdefault 首胜, 组序+组内序扫描;
 *      跨组重复 fid 取最先）
 *   3. 质心: 用组的完整成员表求 cx=sum(xs)/len, cy=sum(ys)/len —— 含不在
 *      items 的幽灵 fid 与组内重复 fid; Python 3.13 内建 sum() 为 Neumaier
 *      补偿求和（CPython 3.12+ gh-100425）→ _ce_scope_sum_f64（与主分支
 *      后加的 _ce_py_sum_f64 同算法, 独立命名防合并冲突）; 位置缺失 → 0.0
 *   4. 成员: 映射到该组的 items（原顺序）; ≥2 个时按极角 atan2(py-cy,
 *      px-cx) 稳定升序（同键保原序 = Python sorted 稳定性）; <2 个时组不
 *      重排（成员原位散落）
 *   5. 发射: 按 items 原序; 非组成员原位; 组首次出现 → 整块发射并标记
 *      emitted, 再次出现 → 跳过
 * 边界（fid 必须整数化; 非整数条目由调用方留给原路径）:
 *   - item id 在 Python 侧经 str(id or "") 过滤, 假值（0/""/None）→ "" 永
 *     不匹配任何组; 调用方把假值条目映射为哨兵 -1, 并保证组 fid 集合不含
 *     -1（组内 0 合法保留: str(0)="0" 为真值——fuzz 含此不对称用例）
 *   - 组内空串成员 Python 已丢弃（if str(f)）, group_lens 为过滤后长度
 *   - 非有限坐标（NaN/±inf）: 质心/极角可能出现 NaN, 对 NaN 键排序无全序
 *     （Python 与 C 均未定义序）; 真实 TopView 坐标恒有限, 内核按有限
 *     输入设计（fuzz 只用有限坐标）
 */

/* Python 3.13 内建 sum() 浮点路径逐位复刻（CPython 3.12+ gh-100425:
 * start=0 首元素经 PyNumber_Add(0, x0) 得 f_result, 之后 Neumaier 补偿求和,
 * 收尾 c!=0 且有限才补偿——0.0 + -0.0 = +0.0 保号语义一致） */
static double _ce_scope_sum_f64(const double* a, int n)
{
    double f_result, c;
    int i;
    if (n <= 0)
        return 0.0;
    f_result = 0.0 + a[0];
    c = 0.0;
    for (i = 1; i < n; i++) {
        double x = a[i];
        double t = f_result + x;
        if (fabs(f_result) >= fabs(x))
            c += (f_result - t) + x;
        else
            c += (x - t) + f_result;
        f_result = t;
    }
    if (c != 0.0 && isfinite(c))
        f_result += c;
    return f_result;
}

/* 频谱平坦度: geo_mean / arith_mean（全程 f64, 同 _spectral_flatness）
 * 两处 sum() 均为 Python 3.13 内建 sum → Neumaier 补偿求和（非串行）;
 * arith 也是对 clamp 后的 arr 求和（max(b, 1e-10) 先行） */
CE_API double ce_audio_spectral_flatness(const double* bands, int n)
{
    if (n <= 0)
        return 0.0;
    double* xa = (double*)malloc((size_t)n * sizeof(double));
    double* la = (double*)malloc((size_t)n * sizeof(double));
    if (xa == NULL || la == NULL) {
        free(xa);
        free(la);
        return 0.0;
    }
    for (int i = 0; i < n; i++) {
        double b = bands[i];
        double x = (b < 1e-10) ? 1e-10 : b;   /* max(b, 1e-10): NaN → b */
        xa[i] = x;
        la[i] = log(x);
    }
    double log_sum = _ce_py_sum_f64(la, n);          /* sum(math.log(x) ...) */
    double arith = _ce_py_sum_f64(xa, n) / (double)n; /* sum(arr) / n */
    double geo = exp(log_sum / (double)n);
    free(la);
    free(xa);
    double denom = (arith < 1e-10) ? 1e-10 : arith;   /* max(arith_mean, 1e-10) */
    return geo / denom;
}
/* ── T-recon: 8 频段均值（对齐 src/ui/audio_driver.py AudioAnalyzer
 *    _analyze_file_at_position :713-715 / _analyze_mic_buffer :741-743 的
 *    频段循环; fft 为 f64, 全程 f64; 实时 25ms 帧频 + 离线整曲烘焙共用）
 *
 * Python 原逻辑（FFT 输出之后）:
 *     freqs = np.fft.rfftfreq(n, 1.0 / sr)      # f64: k * (1.0/(n*d)), d=1.0/sr
 *     for lo, hi in FREQ_RANGES:                # audio_driver.py:78-85 固定 8 段
 *         mask = (freqs >= lo) & (freqs < hi)
 *         energy = float(np.mean(fft[mask])) if mask.any() else 0.0
 *         bands.append(min(1.0, energy * gain * 0.01))
 *
 * 位级等价纪律（numpy 2.4.4 loops_utils.h.src DOUBLE_pairwise_sum 同模板,
 * Python 侧穷举 n 1..4096 × 幅值域逐位对拍 0 差异）:
 *   1. fft 为 f64（buf f32 × hanning f64 提升; np.abs(rfft) ≥ 0 但内核不假设）
 *   2. 选段求和 = numpy add.reduce f64（pairwise）→ _ce_pairwise_sum_f64:
 *      - n<8: 累加器从 +0.0 起串行（0.0 + -0.0 = +0.0 保号, 同 numpy n8）
 *      - 8<=n<=128: 8 累加器 r[k] += a[i+k]（i=8 起步, 步长 8）,
 *        树合并 ((r0+r1)+(r2+r3))+((r4+r5)+(r6+r7)), 尾部 n%8 串行追加
 *      - n>128: 递归二分, n2 = n/2 - (n/2)%8, 左半 + 右半（f64 加法）
 *   3. mean = sum / (double)count（numpy true_divide f64 / int, count ≤ 2^24 精确）
 *   4. energy = float(mean) 即 f64 加宽; (energy * gain) * 0.01 全程 f64
 *      （Python 左结合）; gain 契约 = float/int/np.float64（f64 数学）——
 *      np.float32 强标量会让原版降为 f32 数学, 由调用方回退原版
 *   5. min(1.0, x) = (x < 1.0) ? x : 1.0 —— NaN → 1.0, 同 Python min(1.0, x)
 *   6. 空选段（count==0, 同 mask.any() 为假）→ energy=0.0 → 0.0 链 → 0.0
 *   7. freqs 逐 bin 复算: inv = 1.0/((double)n * d) 先算（numpy fftfreq:
 *      results = 1.0/(n*d) 标量, 再 arange*results）, d = 1.0/sr;
 *      每 bin freq = (double)k * inv —— 与 rfftfreq 逐位一致
 *   8. 并行性: 8 频段互相独立 → 批量版按帧分块 OpenMP（帧内 8 段串行,
 *      位级与单帧版逐帧一致——每帧输出与其它帧无耦合）
 *   9. ★ /fp:precise 会把 0.0 + x 折叠成 x（丢 -0.0 保号, 实测复现）——
 *      累加器初始零经 volatile 读（numpy 逐 lane 从 +0.0 起步: -0.0 → +0.0）
 */
static volatile double _ce_f64_zero_v = 0.0;  /* 防折叠屏障（-0.0 保号） */

static double _ce_pairwise_sum_f64(const double* a, int n)
{
    if (n < 8) {
        double res = _ce_f64_zero_v;
        for (int i = 0; i < n; i++)
            res += a[i];
        return res;
    }
    else if (n <= 128) {
        double r[8], res;
        /* 累加器从 +0.0 起（volatile: -0.0 → +0.0, 同 numpy 逐 lane 0 起步） */
        r[0] = _ce_f64_zero_v + a[0]; r[1] = _ce_f64_zero_v + a[1];
        r[2] = _ce_f64_zero_v + a[2]; r[3] = _ce_f64_zero_v + a[3];
        r[4] = _ce_f64_zero_v + a[4]; r[5] = _ce_f64_zero_v + a[5];
        r[6] = _ce_f64_zero_v + a[6]; r[7] = _ce_f64_zero_v + a[7];
        int i = 8;
        for (; i < n - (n % 8); i += 8) {
            r[0] += a[i + 0]; r[1] += a[i + 1];
            r[2] += a[i + 2]; r[3] += a[i + 3];
            r[4] += a[i + 4]; r[5] += a[i + 5];
            r[6] += a[i + 6]; r[7] += a[i + 7];
        }
        res = ((r[0] + r[1]) + (r[2] + r[3])) +
              ((r[4] + r[5]) + (r[6] + r[7]));
        for (; i < n; i++)
            res += a[i];
        return res;
    }
    else {
        int n2 = n / 2;
        n2 -= n2 % 8;
        return _ce_pairwise_sum_f64(a, n2) + _ce_pairwise_sum_f64(a + n2, n - n2);
    }
}

/* 批量帧能量: bufs 为 n_frames × n（行主序 f32）, out_rms/out_peak 为
 * n_frames 个 f64。每帧独立 → OpenMP 按帧分块, 帧内与 ce_audio_frame_energy
 * 同路径 → 逐帧位级一致（无跨帧耦合, 无竞态）。 */
CE_API void ce_audio_frame_energy_batch(const float* bufs, int n_frames, int n,
                                        double gain, double* out_rms,
                                        double* out_peak)
{
    int f;
    if (n_frames <= 0)
        return;
#pragma omp parallel for schedule(static)
    for (f = 0; f < n_frames; f++) {
        ce_audio_frame_energy(bufs + (size_t)f * (size_t)n, n, gain,
                              &out_rms[f], &out_peak[f]);
    }
}

/* 单帧 8 频段: out8[0..7]（行主序, 调用方预分配） */
CE_API void ce_audio_bands(const double* fft, int n_bins, double sr, int n,
                           double gain, double* out8)
{
    static const double k_lo[8] = {20.0, 60.0, 250.0, 500.0,
                                   2000.0, 4000.0, 6000.0, 12000.0};
    static const double k_hi[8] = {60.0, 250.0, 500.0, 2000.0,
                                   4000.0, 6000.0, 12000.0, 20000.0};
    if (out8 == NULL)
        return;
    if (n_bins <= 0 || n <= 0) {
        for (int b = 0; b < 8; b++)
            out8[b] = 0.0;
        return;
    }
    double d = 1.0 / sr;
    double inv = 1.0 / ((double)n * d);
    double* sel = (double*)malloc((size_t)n_bins * sizeof(double));
    if (sel == NULL) {
        for (int b = 0; b < 8; b++)
            out8[b] = 0.0;
        return;
    }
    for (int b = 0; b < 8; b++) {
        double lo = k_lo[b], hi = k_hi[b];
        int count = 0;
        for (int k = 0; k < n_bins; k++) {
            double freq = (double)k * inv;
            if (freq >= lo && freq < hi)
                sel[count++] = fft[k];
        }
        double v;
        if (count == 0) {
            /* mask.any() 假 → energy = 0.0, 但 gain 数学不跳过:
             * 0.0 * NaN = NaN → min(1.0, NaN) = 1.0, 同 Python */
            v = (0.0 * gain) * 0.01;
        }
        else {
            double sum = _ce_pairwise_sum_f64(sel, count);
            double mean = sum / (double)count;
            v = (mean * gain) * 0.01;
        }
        out8[b] = (v < 1.0) ? v : 1.0;     /* min(1.0, x): NaN → 1.0 */
    }
    free(sel);
}

/* 批量 8 频段: ffts 为 n_frames × n_bins（行主序）, out8n 为
 * n_frames × 8（行主序）。每帧独立 → OpenMP 按帧分块, 帧内与
 * ce_audio_bands 同路径 → 逐帧位级一致（无跨帧耦合, 无竞态）。 */
CE_API void ce_audio_bands_batch(const double* ffts, int n_bins, int n_frames,
                                 double sr, int n, double gain, double* out8n)
{
    int f;
    if (n_frames <= 0)
        return;
#pragma omp parallel for schedule(static)
    for (f = 0; f < n_frames; f++) {
        ce_audio_bands(ffts + (size_t)f * (size_t)n_bins, n_bins, sr, n,
                       gain, out8n + (size_t)f * 8);
    }
}
/* ── T6: 参数包络求值（对齐 src/ui/param_envelope.py ParamEnvelope.value_at
 *       :77 + _eval_phase :88 + src/core/models.py CurveMapping.evaluate :541）
 *
 * D1 通用参数包络: 把绑定了包络的效果参数替换成时刻 t 的曲线值
 * （effect_engine._apply_param_envelopes 每帧每绑定调用）。
 * 输入:
 *   t        f64  时刻（秒）
 *   duration f64  周期时长（秒）; <=0 → 4.0（同 Python dur 兜底, NaN 原样保留）
 *   xs/ys    f64[n]  控制点（x=相位[0,1], y=归一化值[0,1]）
 *   n        控制点数; n<=0 → 返回 0.0（同 Python 无点兜底）
 *   mode     0=curve （CurveMapping.evaluate 语义: 不排序, 最终兜底 0.0）
 *            1=points（ParamEnvelope 兜底: 先按 x 稳定排序, 最终兜底末点 y）
 * 返回: 归一化包络值, 已 clamp [0,1]（NaN 原样穿过, 同 _clamp01）
 *
 * 位级等价纪律（与 Python 逐行一致, 全程 double）:
 *   1. phase = (t % duration)/duration —— Python float % = fmod + 符号修正
 *      （除数恒正 → fmod 负则 +duration, 同 CPython float_rem）
 *   2. 两次 _clamp01（value_at 外层 + _eval_phase 内层）
 *   3. 插值 t = (x-p0.x)/(p1.x-p0.x) if p1.x != p0.x else 0 → p0.y + t*(p1.y-p0.y)
 *   4. mode 1 稳定插入排序（同 Python sorted 稳定性; 点数上限 CE_ENV_MAX_PTS,
 *      现实包络 ≤ 数十点, 超出按截断处理）
 */
#define CE_ENV_MAX_PTS 256

static double _ce_clamp01(double v)
{
    if (v < 0.0)
        return 0.0;
    if (v > 1.0)
        return 1.0;
    return v;
}

/* Python float % : fmod + 符号修正（除数恒正） */
static double _ce_env_mod(double t, double dur)
{
    double m = fmod(t, dur);
    if (m < 0.0)
        m += dur;
    return m;
}

static double _ce_env_eval_phase(double phase, const double* xs, const double* ys,
                                 int n, int mode)
{
    int i;
    if (phase <= xs[0])
        return ys[0];
    if (phase >= xs[n - 1])
        return ys[n - 1];
    for (i = 0; i < n - 1; i++) {
        double x0 = xs[i], y0 = ys[i];
        double x1 = xs[i + 1], y1 = ys[i + 1];
        if (x0 <= phase && phase <= x1) {
            double k = (x1 != x0) ? (phase - x0) / (x1 - x0) : 0.0;
            return y0 + k * (y1 - y0);
        }
    }
    return (mode == 1) ? ys[n - 1] : 0.0;
}

/* 银行家舍入（同 Python round(): 半值舍入到偶数; -0.0 归正为 +0.0,
 * 同 int(round(x)) 的整数语义） */
static double _ce_round_half_even(double x)
{
    double f = floor(x);
    double d = x - f;
    double r;
    if (d > 0.5)
        return f + 1.0;
    if (d < 0.5)
        return f;
    /* d == 0.5 恰半值: 取偶数 */
    r = (fmod(f, 2.0) == 0.0) ? f : f + 1.0;
    if (r == 0.0)
        return 0.0;   /* -0.0 → +0.0, 同 int(round(-0.5)) = 0 */
    return r;
}

CE_API double ce_param_envelope(double t, double duration,
                                const double* xs, const double* ys,
                                int n, int mode)
{
    double dur, phase, v;
    double sx[CE_ENV_MAX_PTS], sy[CE_ENV_MAX_PTS];
    const double* px = xs;
    const double* py = ys;
    int i;
    if (n < 0)
        n = 0;
    if (n == 0)
        return 0.0;
    dur = duration;
    if (dur <= 0.0)
        dur = 4.0;
    phase = _ce_env_mod(t, dur) / dur;
    phase = _ce_clamp01(phase);
    if (mode == 1) {
        /* 稳定插入排序（同 Python sorted(pts, key=p[0]) 稳定性） */
        if (n > CE_ENV_MAX_PTS)
            n = CE_ENV_MAX_PTS;
        for (i = 0; i < n; i++) {
            sx[i] = xs[i];
            sy[i] = ys[i];
        }
        for (i = 1; i < n; i++) {
            double kx = sx[i], ky = sy[i];
            int j = i - 1;
            while (j >= 0 && sx[j] > kx) {
                sx[j + 1] = sx[j];
                sy[j + 1] = sy[j];
                j--;
            }
            sx[j + 1] = kx;
            sy[j + 1] = ky;
        }
        px = sx;
        py = sy;
    }
    v = _ce_env_eval_phase(phase, px, py, n, mode);
    return _ce_clamp01(v);
}

/* 单条绑定完整管线（对齐 effect_engine._apply_param_envelopes :1204-1231）:
 * norm = max(0.0, min(1.0, value_at(t))); val = lo + norm*(hi-lo);
 * int 型参数 → int(round(val))（银行家舍入）。返回最终参数值 f64
 * （int 型结果即整数值, |val|<2^53 时与 Python int 精确可比）。
 * ★ max/min 链与 _clamp01 不同: NaN → min(1.0, NaN)=1.0（NaN<1.0 为假, min
 *   保留首参）→ max(0.0,1.0)=1.0 → 满刻度值; 必须逐位复刻（fuzz 实测差异源）。 */
CE_API double ce_param_envelope_map(double t, double duration,
                                    const double* xs, const double* ys,
                                    int n, int mode,
                                    double lo, double hi, int is_int)
{
    double norm = ce_param_envelope(t, duration, xs, ys, n, mode);
    double y;
    double val;
    y = (norm < 1.0) ? norm : 1.0;   /* min(1.0, norm): 保留首参语义 */
    y = (0.0 < y) ? y : 0.0;         /* max(0.0, y) */
    val = lo + y * (hi - lo);
    if (is_int)
        return _ce_round_half_even(val);
    return val;

}
/* ── T7: ArtNet ArtDMX 打包（对齐 src/network/artnet_node.py _build_artdmx :31-46）
 *
 * ArtDMX 帧（18 字节头 + data）:
 *   [0:8]   "Art-Net\0"            — 协议头
 *   [8:10]  OpCode 0x5000 小端     — struct.pack("<H", OPCODE_DMX)
 *   [10:12] ProtVer 14 大端        — struct.pack(">H", 14)
 *   [12]    Sequence               — 原实现硬编码 b"\x00"; C 内核参数化供调用方自增
 *   [13]    Physical 恒 0          — b"\x00"
 *   [14:16] Universe 小端          — struct.pack("<H", universe)
 *   [16:18] Length 大端            — struct.pack(">H", length)
 *   [18:18+length] data[0:length]  — 原样拷贝（0..512）
 *
 * 契约: length 钳制到 [0,512], data==NULL 视为 length=0; out 须 ≥18+length 字节;
 * 返回总帧长 18+length。纯打包内核, socket 发送留在 Python
 * （ArtNetSender.send_dmx, 逐目标 sendto）。
 */
CE_API int ce_artnet_pack(uint16_t universe, uint8_t sequence,
                          const uint8_t* data, int length, uint8_t* out)
{
    int n = length;
    if (n < 0)
        n = 0;
    if (n > 512)
        n = 512;
    if (data == NULL)
        n = 0;
    out[0] = 'A'; out[1] = 'r'; out[2] = 't'; out[3] = '-';
    out[4] = 'N'; out[5] = 'e'; out[6] = 't'; out[7] = 0;
    out[8]  = 0x00; out[9]  = 0x50;             /* OpCode 0x5000 小端 */
    out[10] = 0x00; out[11] = 0x0E;             /* ProtVer 14 大端 */
    out[12] = sequence;
    out[13] = 0x00;                             /* Physical */
    out[14] = (uint8_t)(universe & 0xFF);       /* Universe 小端 */
    out[15] = (uint8_t)((universe >> 8) & 0xFF);
    out[16] = (uint8_t)((n >> 8) & 0xFF);       /* Length 大端 */
    out[17] = (uint8_t)(n & 0xFF);
    if (n > 0)
        memcpy(out + 18, data, (size_t)n);
    return 18 + n;
}
/* ── T8: 批量协议打包（帧数据 [n_univ*512] → 全部协议包, 一次 C 调用）
 *
 * 帧缓冲布局（域主序）: universe i 的 512 字节数据位于 frame + i*512,
 * 仅取前 lengths[i] 字节。批量输出与逐包调用逐字节一致（ArtNet 批量
 * 内部即调用 ce_artnet_pack, sACN 与 ce_sacn_pack 共用同一填充逻辑）。
 * socket 发送仍留在 Python 发送线程（_send_to_hw 边界永不破）。
 */
CE_API int ce_artnet_pack_batch(int n_univ,
                                const uint16_t* universes,
                                const uint8_t* sequences,
                                const int* lengths,
                                const uint8_t* frame,
                                uint8_t* out,
                                int* offsets)
{
    int i, off = 0;
    if (n_univ <= 0)
        return 0;
    if (universes == NULL || sequences == NULL || lengths == NULL || out == NULL)
        return 0;
    for (i = 0; i < n_univ; i++) {
        const uint8_t* src = (frame != NULL) ? frame + (size_t)i * 512 : NULL;
        int n = ce_artnet_pack(universes[i], sequences[i], src,
                               lengths[i], out + off);
        if (offsets != NULL)
            offsets[i] = off;
        off += n;
    }
    return off;
}
/* sACN E1.31 单包填充（对齐 src/network/sacn_node.py _build_sacn :43-81）:
 *   [0:4]    Preamble 0x0010 + Postamble 0x0000
 *   [4:16]   "ASC-E1.17\0\0\0" (12)
 *   [16:18]  Root Flags & Length = 0x7000 | (len(framing)+22) 大端
 *   [18:22]  Root Vector 0x00000004
 *   [22:38]  CID (16)
 *   [38:40]  Framing Flags & Length = 0x7000 | (77 + len(dmp)) 大端
 *   [40:44]  Framing Vector 0x00000002
 *   [44:108] Source Name (64)
 *   [108]    Priority
 *   [109:111] Synchronization Address 0x0000
 *   [111]    Sequence
 *   [112]    Options 0x00
 *   [113:115] Universe 大端
 *   [115:117] DMP Flags & Length = 0x7000 | (11+length) 大端
 *   [117]    DMP Vector 0x02
 *   [118]    Address & Data Type 0xa1
 *   [119:121] First Property Address 0x0000
 *   [121:123] Address Increment 0x0001
 *   [123:125] Property Count = length+1 大端
 *   [125]    DMX start code 0x00
 *   [126:]   data[0:length]
 * 总长 = 126 + length。dmp_len/framing_len/root_len 按原实现公式原样复刻
 * （dmp_len=11+length, framing_len=77+len(dmp)=77+(11+n),
 *   root_len=len(framing)+22=(88+n)+22=110+n）。
 */
static int _ce_sacn_fill(const uint8_t* cid, const uint8_t* name,
                         uint16_t universe, uint8_t sequence,
                         uint8_t priority, const uint8_t* data,
                         int length, uint8_t* out)
{
    int n = length;
    int dmp_len, framing_len, root_len;
    if (n < 0)
        n = 0;
    if (n > 512)
        n = 512;
    if (data == NULL)
        n = 0;
    dmp_len = 11 + n;                 /* DMP flags 字段值: 11+length */
    framing_len = 77 + dmp_len;       /* Framing flags 字段值: 77+len(dmp) */
    root_len = framing_len + 22;      /* Root flags 字段值: len(framing)+22 */
    out[0] = 0x00; out[1] = 0x10; out[2] = 0x00; out[3] = 0x00;
    out[4] = 'A'; out[5] = 'S'; out[6] = 'C'; out[7] = '-';
    out[8] = 'E'; out[9] = '1'; out[10] = '.'; out[11] = '1';
    out[12] = '7'; out[13] = 0x00; out[14] = 0x00; out[15] = 0x00;
    out[16] = (uint8_t)((0x7000 | root_len) >> 8);
    out[17] = (uint8_t)(0x7000 | root_len);
    out[18] = 0x00; out[19] = 0x00; out[20] = 0x00; out[21] = 0x04;
    if (cid != NULL)
        memcpy(out + 22, cid, 16);
    out[38] = (uint8_t)((0x7000 | framing_len) >> 8);
    out[39] = (uint8_t)(0x7000 | framing_len);
    out[40] = 0x00; out[41] = 0x00; out[42] = 0x00; out[43] = 0x02;
    if (name != NULL)
        memcpy(out + 44, name, 64);
    out[108] = priority;
    out[109] = 0x00; out[110] = 0x00;
    out[111] = sequence;
    out[112] = 0x00;
    out[113] = (uint8_t)((universe >> 8) & 0xFF);
    out[114] = (uint8_t)(universe & 0xFF);
    out[115] = (uint8_t)((0x7000 | dmp_len) >> 8);
    out[116] = (uint8_t)(0x7000 | dmp_len);
    out[117] = 0x02;
    out[118] = 0xa1;
    out[119] = 0x00; out[120] = 0x00;
    out[121] = 0x00; out[122] = 0x01;
    out[123] = (uint8_t)((n + 1) >> 8);
    out[124] = (uint8_t)(n + 1);
    out[125] = 0x00;
    if (n > 0)
        memcpy(out + 126, data, (size_t)n);
    return 126 + n;
}

CE_API int ce_sacn_pack(const uint8_t* cid, const uint8_t* name,
                        uint16_t universe, uint8_t sequence,
                        uint8_t priority, const uint8_t* data,
                        int length, uint8_t* out)
{
    return _ce_sacn_fill(cid, name, universe, sequence, priority,
                         data, length, out);
}

CE_API int ce_sacn_pack_batch(int n_univ,
                              const uint8_t* cid, const uint8_t* name,
                              const uint16_t* universes,
                              const uint8_t* sequences,
                              const uint8_t* priorities,
                              const int* lengths,
                              const uint8_t* frame,
                              uint8_t* out,
                              int* offsets)
{
    int i, off = 0;
    if (n_univ <= 0)
        return 0;
    if (cid == NULL || name == NULL || universes == NULL
        || sequences == NULL || priorities == NULL
        || lengths == NULL || out == NULL)
        return 0;
    for (i = 0; i < n_univ; i++) {
        const uint8_t* src = (frame != NULL) ? frame + (size_t)i * 512 : NULL;
        int n = _ce_sacn_fill(cid, name, universes[i], sequences[i],
                              priorities[i], src, lengths[i], out + off);
        if (offsets != NULL)
            offsets[i] = off;
        off += n;
    }
    return off;
}
/* 稳定排序条目: key 为极角或中心距离, idx 为原索引 */
typedef struct {
    double key;
    int32_t idx;
} _ce_order_item;

/* 稳定自底向上归并排序（左 <= 右取左 → 同键保原序, 同 Python sorted） */
static void _ce_order_sort(_ce_order_item* items, _ce_order_item* tmp, int n)
{
    for (int width = 1; width < n; width *= 2) {
        for (int lo = 0; lo + width < n; lo += 2 * width) {
            int mid = lo + width;
            int hi = lo + 2 * width;
            int i = lo, j = mid, k = lo;
            if (hi > n)
                hi = n;
            while (i < mid && j < hi) {
                if (!(items[j].key < items[i].key))
                    tmp[k++] = items[i++];
                else
                    tmp[k++] = items[j++];
            }
            while (i < mid)
                tmp[k++] = items[i++];
            while (j < hi)
                tmp[k++] = items[j++];
            for (k = lo; k < hi; k++)
                items[k] = tmp[k];
        }
    }
}

CE_API void ce_effect_direction(int n, int mode, int32_t* out)
{
    int i;
    if (n <= 0 || out == NULL)
        return;
    if (n <= 1 || mode == 0) {
        for (i = 0; i < n; i++)
            out[i] = (int32_t)i;
        return;
    }
    if (mode == 1) {   /* reverse */
        for (i = 0; i < n; i++)
            out[i] = (int32_t)(n - 1 - i);
        return;
    }
    if (mode != 2 && mode != 3) {   /* 未知 mode → 恒等（同 Python 末尾 return items） */
        for (i = 0; i < n; i++)
            out[i] = (int32_t)i;
        return;
    }
    {
        _ce_order_item* items = (_ce_order_item*)malloc(
            (size_t)n * sizeof(_ce_order_item));
        _ce_order_item* tmp = (_ce_order_item*)malloc(
            (size_t)n * sizeof(_ce_order_item));
        if (items == NULL || tmp == NULL) {
            free(items);
            free(tmp);
            return;   /* 分配失败: 输出不变, 调用方回退原路径 */
        }
        for (i = 0; i < n; i++) {
            int64_t d = 2LL * (int64_t)i - (int64_t)(n - 1);
            if (d < 0)
                d = -d;   /* |2i-(n-1)|: 与 abs(i-(n-1)/2.0) 同序（n<2^53） */
            items[i].key = (double)d;
            items[i].idx = (int32_t)i;
        }
        _ce_order_sort(items, tmp, n);
        if (mode == 2) {   /* center_out: 键升序 */
            for (i = 0; i < n; i++)
                out[i] = items[i].idx;
        } else {           /* edge_in: 整体反序 */
            for (i = 0; i < n; i++)
                out[i] = items[n - 1 - i].idx;
        }
        free(items);
        free(tmp);
    }
}

/* fid → (位置, 单元映射) 哈希表: 开放寻址线性探测, 负载 ≤ 0.5 */
typedef struct {
    int64_t key;
    int has;        /* 1 = 槽位占用 */
    double px, py;  /* 位置负载（缺失 → 0.0, 同 dict.get 默认） */
    int unit;       /* 单元映射负载（-1 = 无; 首个含它的保留组） */
} _ce_fid_slot;

static size_t _ce_fid_hash(int64_t key, size_t cap)
{
    uint64_t h = (uint64_t)key * 0x9E3779B97F4A7C15ULL;
    return (size_t)((h >> 32) & (cap - 1));
}

static _ce_fid_slot* _ce_fid_map_find(const _ce_fid_slot* slots, size_t cap,
                                      int64_t key)
{
    size_t i = _ce_fid_hash(key, cap);
    while (slots[i].has) {
        if (slots[i].key == key)
            return (_ce_fid_slot*)&slots[i];
        i = (i + 1) & (cap - 1);
    }
    return NULL;
}

static _ce_fid_slot* _ce_fid_map_insert(_ce_fid_slot* slots, size_t cap,
                                        int64_t key)
{
    size_t i = _ce_fid_hash(key, cap);
    while (slots[i].has) {
        if (slots[i].key == key)
            return &slots[i];
        i = (i + 1) & (cap - 1);
    }
    slots[i].key = key;
    slots[i].has = 1;
    slots[i].px = 0.0;
    slots[i].py = 0.0;
    slots[i].unit = -1;
    return &slots[i];
}

CE_API void ce_spatial_scope(const int64_t* item_fids, int n,
                             const int64_t* pos_fids,
                             const double* pos_x, const double* pos_y, int m,
                             const int64_t* group_fids,
                             const int* group_lens, int g,
                             int32_t* out)
{
    int i, gi, off;
    if (n <= 0 || out == NULL)
        return;
    for (i = 0; i < n; i++)
        out[i] = (int32_t)i;   /* 恒等兜底: 任何早退路径保持原序 */
    if (n < 2 || g <= 0 || item_fids == NULL ||
        group_fids == NULL || group_lens == NULL)
        return;
    {
        int total_fids = 0, kept = 0, cur = 0;
        size_t need, cap;
        _ce_fid_slot* slots;
        int* cnt;
        int32_t* item_unit;
        int32_t* members;
        int32_t* starts;
        int* fillpos;
        uint8_t* emitted;
        _ce_order_item* srt;
        _ce_order_item* srt_tmp;
        double* gxs;
        double* gys;
        for (gi = 0; gi < g; gi++) {
            int len = group_lens[gi];
            if (len >= 2) {
                kept++;
                total_fids += len;
            }
        }
        if (kept == 0)
            return;   /* 无保留组 → 恒等（同 Python if not units: return items） */
        need = (size_t)m + (size_t)total_fids;
        cap = 16;
        while (cap < need * 2 + 1)
            cap <<= 1;
        slots = (_ce_fid_slot*)calloc(cap, sizeof(_ce_fid_slot));
        cnt = (int*)calloc((size_t)g, sizeof(int));
        item_unit = (int32_t*)malloc((size_t)n * sizeof(int32_t));
        members = (int32_t*)malloc((size_t)n * sizeof(int32_t));
        starts = (int32_t*)malloc((size_t)(g + 1) * sizeof(int32_t));
        fillpos = (int*)malloc((size_t)g * sizeof(int));
        emitted = (uint8_t*)calloc((size_t)g, sizeof(uint8_t));
        srt = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
        srt_tmp = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
        gxs = (double*)malloc((size_t)total_fids * sizeof(double));
        gys = (double*)malloc((size_t)total_fids * sizeof(double));
        if (slots == NULL || cnt == NULL || item_unit == NULL ||
            members == NULL || starts == NULL || fillpos == NULL ||
            emitted == NULL || srt == NULL || srt_tmp == NULL ||
            gxs == NULL || gys == NULL) {
            free(slots); free(cnt); free(item_unit); free(members);
            free(starts); free(fillpos); free(emitted); free(srt);
            free(srt_tmp); free(gxs); free(gys);
            return;   /* 分配失败: out 保持恒等, 调用方回退原路径 */
        }
        /* 1. positions 载入（键唯一, 覆盖写） */
        for (i = 0; i < m; i++) {
            _ce_fid_slot* s = _ce_fid_map_insert(slots, cap, pos_fids[i]);
            s->px = pos_x[i];
            s->py = pos_y[i];
        }
        /* 2. 组映射: fid → 首个保留组（setdefault 首胜, 组序+组内序） */
        off = 0;
        for (gi = 0; gi < g; gi++) {
            int len = group_lens[gi];
            if (len >= 2) {
                for (i = 0; i < len; i++) {
                    _ce_fid_slot* s = _ce_fid_map_insert(
                        slots, cap, group_fids[off + i]);
                    if (s->unit < 0)
                        s->unit = gi;
                }
            }
            off += len;
        }
        /* 3. 条目 → 组（无映射 = -1） */
        for (i = 0; i < n; i++) {
            _ce_fid_slot* s = _ce_fid_map_find(slots, cap, item_fids[i]);
            item_unit[i] = (s != NULL && s->unit >= 0) ? s->unit : -1;
        }
        /* 4. 成员计数与填充（按 items 原序, 同 unit_items 列表） */
        for (i = 0; i < n; i++) {
            if (item_unit[i] >= 0)
                cnt[item_unit[i]]++;
        }
        starts[0] = 0;
        for (gi = 0; gi < g; gi++)
            starts[gi + 1] = starts[gi] + cnt[gi];
        for (gi = 0; gi < g; gi++)
            fillpos[gi] = starts[gi];
        for (i = 0; i < n; i++) {
            int u = item_unit[i];
            if (u >= 0)
                members[fillpos[u]++] = (int32_t)i;
        }
        /* 5. 质心（组完整成员表, 含幽灵/重复 fid; Neumaier sum）+ 极角排序 */
        off = 0;
        for (gi = 0; gi < g; gi++) {
            int len = group_lens[gi];
            if (len >= 2 && cnt[gi] >= 2) {
                int c0 = starts[gi];
                double cx, cy;
                for (i = 0; i < len; i++) {
                    _ce_fid_slot* s = _ce_fid_map_find(
                        slots, cap, group_fids[off + i]);
                    gxs[i] = (s != NULL) ? s->px : 0.0;
                    gys[i] = (s != NULL) ? s->py : 0.0;
                }
                cx = _ce_scope_sum_f64(gxs, len) / (double)len;
                cy = _ce_scope_sum_f64(gys, len) / (double)len;
                for (i = 0; i < cnt[gi]; i++) {
                    int32_t idx = members[c0 + i];
                    _ce_fid_slot* s = _ce_fid_map_find(
                        slots, cap, item_fids[idx]);
                    double px = (s != NULL) ? s->px : 0.0;
                    double py = (s != NULL) ? s->py : 0.0;
                    srt[i].key = atan2(py - cy, px - cx);
                    srt[i].idx = idx;
                }
                _ce_order_sort(srt, srt_tmp, cnt[gi]);
                for (i = 0; i < cnt[gi]; i++)
                    members[c0 + i] = srt[i].idx;
            }
            off += len;
        }
        /* 6. 发射: 非组成员原位; 组首次出现整块发射并标记, 再出现跳过 */
        for (i = 0; i < n; i++) {
            int uidx = item_unit[i];
            if (uidx < 0) {
                out[cur++] = (int32_t)i;
            } else if (emitted[uidx]) {
                /* 跳过: 组已整块发射 */
            } else if (cnt[uidx] < 2) {
                out[cur++] = (int32_t)i;   /* 组不重排: 成员原位散落 */
            } else {
                int k;
                emitted[uidx] = 1;
                for (k = 0; k < cnt[uidx]; k++)
                    out[cur++] = members[starts[uidx] + k];
            }
        }
        free(slots); free(cnt); free(item_unit); free(members);
        free(starts); free(fillpos); free(emitted); free(srt);
        free(srt_tmp); free(gxs); free(gys);
    }
}
CE_API void ce_interlock_process(
    float* result, int frame_len, int universe,
    int n, const int* cu, const int* cs, const int* cn,
    const float* const* cmin, const float* const* cmax,
    const float* const* crate, const float* const* cdef,
    int* states, int* counts,
    int* has_prev, int* prev_len, float* const* prevs,
    int warning_t, int softlimit_t, int hardstop_t,
    double blackout_ratio, int* blackout, uint8_t* out)
{
    /* 全局 BLACKOUT 判定由 Python hook 侧按「全部约束」计算；C 只做本 universe
     * 约束处理，避免多 universe 时比例算错导致过早全零。 */
    *blackout = 0;
    for (int i = 0; i < n; i++) {
        if (cu[i] != universe) continue;
        int state = states[i];
        if (state == 0) continue;                 /* DISARMED 直通 */
        int start = cs[i];
        int ch_end = start + cn[i];
        if (ch_end > frame_len) ch_end = frame_len;
        if (start >= frame_len) continue;
        int length = ch_end - start;
        const float* mn = cmin[i];
        const float* mx = cmax[i];
        const float* rt = crate[i];
        const float* df = cdef[i];
        float* slice = result + start;
        /* 违规检测: 越界（numpy any(a<min) / any(a>max)）+ 变化率 any(|d-p|>rate) */
        int range_violation = 0, rate_violation = 0;
        for (int j = 0; j < length; j++) {
            if (slice[j] < mn[j]) { range_violation = 1; break; }
        }
        if (!range_violation) {
            for (int j = 0; j < length; j++) {
                if (slice[j] > mx[j]) { range_violation = 1; break; }
            }
        }
        if (has_prev[i] && prev_len[i] >= length) {
            const float* prev = prevs[i];
            for (int j = 0; j < length; j++) {
                if (fabsf(slice[j] - prev[j]) > rt[j]) {
                    rate_violation = 1; break;
                }
            }
        }
        int has_violation = range_violation || rate_violation;
        /* 计数与状态迁移（HARD_STOP 不自动降级） */
        if (has_violation) {
            int count = counts[i] + 1;
            counts[i] = count;
            if (state == 1) {
                if (count >= hardstop_t) state = 4;
                else if (count >= softlimit_t) state = 3;
                else if (count >= warning_t) state = 2;
            } else if (state == 2) {
                if (count >= hardstop_t) state = 4;
                else if (count >= softlimit_t) state = 3;
            } else if (state == 3) {
                if (count >= hardstop_t + 3) state = 4;
            }
        } else {
            if (counts[i] > 0) {
                counts[i] -= 1;
            } else if (state == 2 || state == 3) {
                state = 1;
            }
        }
        states[i] = state;
        /* 动作: 硬停写 safe_default / 钳位 clip(min,max)（新状态即生效） */
        if (state >= 4) {
            for (int j = 0; j < length; j++) slice[j] = df[j];
        } else if (state >= 2) {
            for (int j = 0; j < length; j++) {
                slice[j] = ce_np_minf(ce_np_maxf(slice[j], mn[j]), mx[j]);
            }
        }
        /* 存储当前值（修改后）用于变化率检测 */
        has_prev[i] = 1;
        prev_len[i] = length;
        float* pv = prevs[i];
        for (int j = 0; j < length; j++) pv[j] = slice[j];
    }
    /* 最终链: np.round(半偶) → np.clip(0,255) → astype uint8（NaN→0）。
     * H3 修复: 先值域早退再舍入（同 ce_fader_effect）——ce_round_half_even 的
     * (int64_t)floor(v) 对 |v| ≥ 2^63 / NaN / inf 是 UB（MSVC 产出 0, numpy
     * 产出 255）; 帧数据 bits 随机含 3e38/NaN/inf 时即分歧。numpy 实测钉死:
     * NaN → astype 0; round(-0.5) = -0.0 → clip → 0; round(255.5) = 256 →
     * clip → 255。故按值域早退:
     *   v ≤ -0.5 → round ≤ 0 → 0（含 -inf）;
     *   v ≥ 255.5 → round ≥ 256 → 255（含 +inf）;
     *   域内 (-0.5, 255.5) 的 round 结果恒 ∈ [0,255], int64 转换安全。 */
    for (int j = 0; j < frame_len; j++) {
        float v = result[j];
        if (v != v) {
            out[j] = 0;                 /* NaN: round→NaN → clip→NaN → astype→0 */
        } else if (v <= -0.5f) {
            out[j] = 0;                 /* round ≤ 0 → clip 0 */
        } else if (v >= 255.5f) {
            out[j] = 255;               /* round ≥ 256 → clip 255 */
        } else {
            out[j] = (uint8_t)ce_round_half_even((double)v);  /* 域内安全 */
        }
    }
}
/* ═══════════════════════════════════════════════════════════════════
 * T7: DEPENCE 工程 XML 快速扫描（.depence → project.xml 字节扫描）
 * ───────────────────────────────────────────────────────────────────
 * 背景: 真实大工程 project.xml 解压后 149.6MB / 72 万元素（至冬7.0,
 *   11382 台设备）。src/ui/import_depence.py 现路径 = ET.fromstring
 *   建树 7.6s + Python 树遍历 7.6s（合计约 15.5s）。本内核单遍字节
 *   扫描直接产出解析所需的 (tag, attr) 值区间，跳过建树与树遍历。
 *
 * 契约:
 *   int ce_xml_scan(const char* xml, int64_t xml_len,
 *                   const char* const* tags,  const int* tag_lens,  int n_tags,
 *                   const char* const* attrs, const int* attr_lens, int n_attrs,
 *                   const int* pair_tags, const int* pair_attrs, int n_pairs,
 *                   int64_t* out_el_off, int64_t* out_off, int64_t* out_len,
 *                   int* out_pair, int max_hits)
 *
 *   - 请求 = (pair_tags[p], pair_attrs[p]):
 *       attr_lens[a] > 0 → 元素上 attr="value" 的 value 字节区间
 *                           （不含引号；实体保持原文，调用方自行 unescape）
 *       attr_lens[a] == 0 → 元素 content 区间（开标签 '>' 之后 →
 *                           '</tag' 之前；自闭合元素不产生 hit）。
 *                           ★ 原始字节区间: 含子元素标记/CDATA/实体原文,
 *                           调用方负责解码（与 ET .text 的语义不同——
 *                           .text 只含直接文本子节点且已解码）
 *   - 命中输出（仅写前 max_hits 个）: out_el_off=元素 '<' 偏移,
 *     out_off/out_len=值字节区间, out_pair=请求下标
 *   - 返回命中总数（> max_hits 表示截断；参数非法返回 -1）
 *   - 预处理跳过: <?…?> / <!--…--> / <!DOCTYPE…[内部子集]…> / <![CDATA[…]]>
 *   - 标签名按最后一个 ':' 之后匹配（去命名空间前缀: 请求 "t" 匹配
 *     <ns:t>）；属性名逐字节精确匹配（不去前缀）
 *   - 支持 ' 与 " 引号值；引号内 '>' '<' 不作为分隔符；自闭合 <name/>；
 *     无引号值容错（非法 XML 也可提取）
 *   - 单遍、零分配、无 SIMD（字节扫描带宽主导）；元素栈上限
 *     CE_XML_STACK_MAX_A，溢出后 content 模式关闭、attr 模式继续；
 *     n_tags ≤ CE_XML_MAX_TAGS、n_pairs ≤ CE_XML_MAX_PAIRS
 *   - 扫描器非解析器：不做合法性校验
 */
#define CE_XML_STACK_MAX_A 4096
#define CE_XML_MAX_TAGS 64
#define CE_XML_MAX_PAIRS 64

typedef struct {
    int tag_idx;        /* tags[] 下标; -1 = 未请求标签 */
    int64_t el_start;   /* 元素 '<' 偏移 */
    int64_t content_start;  /* 开标签 '>' 后一字节 */
} ce_xml_el;

/* 标签名匹配: 文档内名称去掉最后一个 ':' 前缀后与请求逐字节比较 */
static int ce_xml_name_match(const char* name, int nlen,
                             const char* want, int wlen)
{
    const char* p = name;
    int i, mlen;
    for (i = 0; i < nlen; i++) {
        if (name[i] == ':')
            p = name + i + 1;
    }
    mlen = nlen - (int)(p - name);
    if (mlen != wlen)
        return 0;
    return memcmp(p, want, (size_t)wlen) == 0;
}

/* 属性值命中写入（quoted 与 unquoted 共用）— 只遍历该标签的请求对 */
static int64_t ce_xml_emit_attr(
    const int* plist, int pcount, int64_t el_start,
    const char* an_start, int alen,
    int64_t vstart, int64_t vend,
    const char* const* attrs, const int* attr_lens, int n_attrs,
    const int* pair_attrs,
    int64_t* out_el_off, int64_t* out_off, int64_t* out_len,
    int* out_pair, int max_hits, int64_t hits)
{
    int k;
    for (k = 0; k < pcount; k++) {
        int p = plist[k];
        int a = pair_attrs[p];
        if (a < 0 || a >= n_attrs || attr_lens[a] <= 0)
            continue;                       /* 空串=content 对，不在这里 */
        if (attr_lens[a] == alen
            && memcmp(an_start, attrs[a], (size_t)alen) == 0) {
            if (hits < (int64_t)max_hits) {
                out_el_off[hits] = el_start;
                out_off[hits] = vstart;
                out_len[hits] = vend - vstart;
                out_pair[hits] = p;
            }
            hits++;
        }
    }
    return hits;
}

CE_API int ce_xml_scan(
    const char* xml, int64_t xml_len,
    const char* const* tags, const int* tag_lens, int n_tags,
    const char* const* attrs, const int* attr_lens, int n_attrs,
    const int* pair_tags, const int* pair_attrs, int n_pairs,
    int64_t* out_el_off, int64_t* out_off, int64_t* out_len,
    int* out_pair, int max_hits)
{
    ce_xml_el stack[CE_XML_STACK_MAX_A];
    int pair_list[CE_XML_MAX_TAGS][CE_XML_MAX_PAIRS];
    int pair_count[CE_XML_MAX_TAGS];
    int64_t i = 0, hits = 0;
    int depth = 0, stack_ok = 1;
    int p, t;

    if (xml == NULL || xml_len < 0 || tags == NULL || tag_lens == NULL
        || attrs == NULL || attr_lens == NULL || pair_tags == NULL
        || pair_attrs == NULL || n_tags < 0 || n_attrs < 0 || n_pairs < 0
        || max_hits < 0 || n_tags > CE_XML_MAX_TAGS
        || n_pairs > CE_XML_MAX_PAIRS)
        return -1;
    if (max_hits > 0 && (out_el_off == NULL || out_off == NULL
                         || out_len == NULL || out_pair == NULL))
        return -1;
    /* 预计算: 每个 tag → 它的请求对下标表（消除每属性全量对遍历） */
    for (t = 0; t < n_tags; t++) {
        pair_count[t] = 0;
        for (p = 0; p < n_pairs; p++) {
            if (pair_tags[p] == t && pair_count[t] < CE_XML_MAX_PAIRS)
                pair_list[t][pair_count[t]++] = p;
        }
    }

    while (i < xml_len) {
        int64_t name_start, name_end, j, el_start;
        const char *lt, *lname;
        int llen;
        char ch;

        /* 文本区向量化跳读: memchr 一次跳到下一个 '<'（文本区不逐字节分支） */
        lt = (const char*)memchr(xml + i, '<', (size_t)(xml_len - i));
        if (lt == NULL)
            break;
        i = (int64_t)(lt - xml);
        if (i + 1 >= xml_len)
            break;
        ch = xml[i + 1];

        if (ch == '?') {                    /* <?xml …?> 处理指令 */
            for (j = i + 2; j + 1 < xml_len; j++) {
                if (xml[j] == '?' && xml[j + 1] == '>')
                    break;
            }
            i = (j + 2 <= xml_len) ? j + 2 : xml_len;
            continue;
        }
        if (ch == '!') {
            if (i + 3 < xml_len && xml[i + 2] == '-' && xml[i + 3] == '-') {
                for (j = i + 4; j + 2 < xml_len; j++) {   /* <!-- …--> */
                    if (xml[j] == '-' && xml[j + 1] == '-' && xml[j + 2] == '>')
                        break;
                }
                i = (j + 3 <= xml_len) ? j + 3 : xml_len;
                continue;
            }
            if (i + 8 < xml_len && xml[i + 2] == '[' && xml[i + 3] == 'C'
                && xml[i + 4] == 'D' && xml[i + 5] == 'A' && xml[i + 6] == 'T'
                && xml[i + 7] == 'A' && xml[i + 8] == '[') {
                for (j = i + 9; j + 2 < xml_len; j++) {   /* <![CDATA[…]]> */
                    if (xml[j] == ']' && xml[j + 1] == ']' && xml[j + 2] == '>')
                        break;
                }
                i = (j + 3 <= xml_len) ? j + 3 : xml_len;
                continue;
            }
            {                                   /* <!DOCTYPE …[内部子集]…> */
                int bdepth = 0;
                for (j = i + 2; j < xml_len; j++) {
                    if (xml[j] == '[') {
                        bdepth++;
                    } else if (xml[j] == ']') {
                        bdepth = (bdepth > 0) ? bdepth - 1 : 0;
                    } else if (xml[j] == '>' && bdepth == 0) {
                        j++;
                        break;
                    }
                }
                i = (j <= xml_len) ? j : xml_len;
                continue;
            }
        }
        if (ch == '/') {                    /* 闭合标签 </name> */
            int64_t cname_start, cname_end, k;
            cname_start = i + 2;
            for (k = cname_start; k < xml_len; k++) {
                char nc = xml[k];
                if (!((nc >= 'A' && nc <= 'Z') || (nc >= 'a' && nc <= 'z')
                      || (nc >= '0' && nc <= '9') || nc == '_' || nc == ':'
                      || nc == '.' || nc == '-'))
                    break;
            }
            cname_end = k;
            if (cname_end > cname_start && depth > 0) {
                int top = depth - 1;
                int ti = stack[top].tag_idx;
                if (ti >= 0
                    && ce_xml_name_match(xml + cname_start,
                                         (int)(cname_end - cname_start),
                                         tags[ti], tag_lens[ti])) {
                    for (p = 0; p < pair_count[ti]; p++) {
                        int pr = pair_list[ti][p];
                        int a = pair_attrs[pr];
                        if (a < 0 || a >= n_attrs || attr_lens[a] != 0)
                            continue;
                        if (hits < (int64_t)max_hits) {
                            out_el_off[hits] = stack[top].el_start;
                            out_off[hits] = stack[top].content_start;
                            out_len[hits] = i - stack[top].content_start;
                            out_pair[hits] = pr;
                        }
                        hits++;
                    }
                }
                depth--;
            }
            i = (cname_end < xml_len) ? cname_end : xml_len;
            continue;
        }

        /* 开标签 <name …> / <name …/> */
        el_start = i;
        name_start = i + 1;
        for (j = name_start; j < xml_len; j++) {
            char nc = xml[j];
            if (!((nc >= 'A' && nc <= 'Z') || (nc >= 'a' && nc <= 'z')
                  || (nc >= '0' && nc <= '9') || nc == '_' || nc == ':'
                  || nc == '.' || nc == '-'))
                break;
        }
        name_end = j;
        if (name_end == name_start) {       /* 非元素起始（如文本中的 '<'） */
            i++;
            continue;
        }
        /* 去命名空间前缀（每元素一次, 避免逐标签重复扫 ':'） */
        lname = xml + name_start;
        llen = (int)(name_end - name_start);
        for (j = name_start; j < name_end; j++) {
            if (xml[j] == ':') {
                lname = xml + j + 1;
                llen = (int)(name_end - j - 1);
            }
        }
        t = -1;
        for (p = 0; p < n_tags; p++) {
            if (llen == tag_lens[p]
                && memcmp(lname, tags[p], (size_t)llen) == 0) {
                t = p;
                break;
            }
        }
        /* 找开标签结尾 '>'（引号内忽略） */
        {
            int q = 0;
            int64_t tag_end;
            for (j = name_end; j < xml_len; j++) {
                char ac = xml[j];
                if (q) {
                    if (ac == q)
                        q = 0;
                } else if (ac == '"' || ac == '\'') {
                    q = ac;
                } else if (ac == '>') {
                    break;
                }
            }
            if (j >= xml_len)
                break;
            tag_end = j;
            /* 属性区扫描 [name_end, tag_end) — 未请求的标签直接跳过 */
            if (t >= 0) {
                int64_t q0 = name_end;
                while (q0 < tag_end) {
                    int64_t an_start, an_end, eq, vstart, vend;
                    char c0 = xml[q0];
                    if (c0 == ' ' || c0 == '\t' || c0 == '\r' || c0 == '\n'
                        || c0 == '/') {
                        q0++;
                        continue;
                    }
                    an_start = q0;
                    while (q0 < tag_end && xml[q0] != '=' && xml[q0] != ' '
                           && xml[q0] != '\t' && xml[q0] != '\r'
                           && xml[q0] != '\n' && xml[q0] != '/')
                        q0++;
                    an_end = q0;
                    eq = q0;
                    while (eq < tag_end && (xml[eq] == ' ' || xml[eq] == '\t'
                           || xml[eq] == '\r' || xml[eq] == '\n'))
                        eq++;
                    if (eq >= tag_end || xml[eq] != '=') {
                        q0 = eq;            /* 布尔属性无值 */
                        continue;
                    }
                    eq++;
                    while (eq < tag_end && (xml[eq] == ' ' || xml[eq] == '\t'
                           || xml[eq] == '\r' || xml[eq] == '\n'))
                        eq++;
                    if (eq >= tag_end)
                        break;
                    if (xml[eq] == '"' || xml[eq] == '\'') {
                        int qc = xml[eq];
                        vstart = eq + 1;
                        vend = vstart;
                        while (vend < tag_end && xml[vend] != qc)
                            vend++;
                        if (vend >= tag_end)
                            break;          /* 未闭合引号 */
                        hits = ce_xml_emit_attr(
                            pair_list[t], pair_count[t], el_start,
                            xml + an_start, (int)(an_end - an_start),
                            vstart, vend,
                            attrs, attr_lens, n_attrs, pair_attrs,
                            out_el_off, out_off, out_len, out_pair,
                            max_hits, hits);
                        q0 = vend + 1;
                    } else {
                        vstart = eq;        /* 无引号值（容错） */
                        vend = eq;
                        while (vend < tag_end && xml[vend] != ' '
                               && xml[vend] != '\t' && xml[vend] != '\r'
                               && xml[vend] != '\n' && xml[vend] != '/')
                            vend++;
                        hits = ce_xml_emit_attr(
                            pair_list[t], pair_count[t], el_start,
                            xml + an_start, (int)(an_end - an_start),
                            vstart, vend,
                            attrs, attr_lens, n_attrs, pair_attrs,
                            out_el_off, out_off, out_len, out_pair,
                            max_hits, hits);
                        q0 = vend;
                    }
                }
            }
            /* 入栈（content 模式）: 自闭合不入栈 */
            {
                int64_t back = tag_end - 1;
                int self_close = 0;
                while (back > name_end && (xml[back] == ' ' || xml[back] == '\t'
                       || xml[back] == '\r' || xml[back] == '\n'))
                    back--;
                if (back > name_end && xml[back] == '/')
                    self_close = 1;
                if (!self_close && stack_ok && depth < CE_XML_STACK_MAX_A) {
                    stack[depth].tag_idx = t;
                    stack[depth].el_start = el_start;
                    stack[depth].content_start = tag_end + 1;
                    depth++;
                } else if (!self_close) {
                    stack_ok = 0;
                }
            }
            i = tag_end + 1;
        }
    }
    return (int)hits;
}
/* ═══════════════════════════════════════════════════════════════════
 * T8: _channel_defaults 通道默认值表 —— 构建 / 消费
 * ───────────────────────────────────────────────────────────────────
 * 背景: src/network/network_manager.py 的 set_channel_defaults(:367)
 *   把 {universe → {addr → value}} 聚合为每 universe 512 通道默认表
 *   (data u8[512], active bool[512]), 由 _mix_one_universe(:1733) 与
 *   _stack_base_layers(:2045) 的 BASE LTP 阶段消费。构建 = 逐条目
 *   clamp 写表（同址后写覆盖）; 消费 = 512 通道 u8→f32 cast +
 *   active/default_mask 拷贝。二者均为纯数值聚合; 设备对象模型
 *   （fixture_defs 按 id 查表 / _channel_type_key 字符串归一化 /
 *   motor 与 function_* 前缀分类, set_fixture_defaults_from_project
 *   :443-485）留在 Python 侧, 本内核只收已解析出的 (addr, val) 数值对。
 *
 * 契约 1 — 构建（每 universe 一次, 调用方先按 universe 分桶）:
 *   int ce_defaults_build(const double* addrs, const double* vals,
 *                         int n, uint8_t* out_data, uint8_t* out_active)
 *
 *   语义逐条对齐 Python set_channel_defaults :375-386:
 *     - 入口整体清零 out_data/out_active（同 np.zeros 预置 :375-376）
 *     - addr: NaN/±inf → Python int(addr) 抛异常且在 try/except 内
 *       （:378-381）→ 跳过该条; 其余按 int(addr) 向零截断
 *       （-0.999→0, 511.99→511）, 截断值 0<=idx<512 才写入。
 *       区间外 addr 直接跳过（截断值必出界, 免大数 cast 的 UB）。
 *     - val: Python int(val) 向零截断后 max(0, min(255, ·))（:383）;
 *       等价分支: v<1.0→0（int(v)<=0, 含 (-1,1) 截断到 0）, v>=255.0
 *       →255, 其余 (int)v（区间内截断安全）。val 必须有限: NaN/inf
 *       时 Python int() 抛异常且不在 try 内 → 构建整体中止（真实
 *       调用路径值恒为 int, :475-482）, 不在本内核契约内。
 *     - 同址多条第后写覆盖（Python dict 迭代序 = 插入序, 数组同序）。
 *   返回 1 = 至少一条写入（np.any(active), :385）; 0 = 空表,
 *   调用方按 Python 语义跳过该 universe 的登记（:386）。
 *
 * 契约 2 — 消费（BASE LTP 阶段; 对齐 :1738-1746 与 :2048-2050）:
 *   void ce_defaults_base(const uint8_t* data, const uint8_t* active,
 *                         float* base_val, uint8_t* base_active,
 *                         uint8_t* default_mask, int n_univ)
 *     逐行（n_univ 行 × 512 通道 row-major）:
 *       base_val[c] = (float)data[c]        — u8→f32 精确
 *       base_active[c] = default_mask[c] = active[c]
 *     仅填写调用方传入的行; 无默认表的 universe 行由调用方预置 0
 *     （Python 侧 np.zeros((N,512)) 预置, :2041-2043）。
 * ───────────────────────────────────────────────────────────────────
 */

CE_API int ce_defaults_build(const double* addrs, const double* vals,
                             int n, uint8_t* out_data, uint8_t* out_active)
{
    memset(out_data, 0, 512);
    memset(out_active, 0, 512);
    int has_active = 0;
    for (int i = 0; i < n; i++) {
        double a = addrs[i];
        if (isnan(a) || isinf(a))
            continue;                     /* int(addr) 抛异常被吞 → 跳过 */
        if (a <= -1.0 || a >= 512.0)
            continue;                     /* 截断值必出 [0,512), 免 UB */
        int idx = (int)a;                 /* 向零截断, 同 int(addr) */
        double v = vals[i];
        int vi;
        if (v < 1.0)
            vi = 0;                       /* int(v)<=0 → max(0,·) */
        else if (v >= 255.0)
            vi = 255;                     /* min(255,·) */
        else
            vi = (int)v;                  /* 向零截断, 同 int(val) */
        out_data[idx] = (uint8_t)vi;
        out_active[idx] = 1;
        has_active = 1;
    }
    return has_active;
}

CE_API void ce_defaults_base(const uint8_t* data, const uint8_t* active,
                             float* base_val, uint8_t* base_active,
                             uint8_t* default_mask, int n_univ)
{
    for (int r = 0; r < n_univ; r++) {
        const uint8_t* d = data + (size_t)r * 512;
        const uint8_t* ac = active + (size_t)r * 512;
        float* bv = base_val + (size_t)r * 512;
        uint8_t* ba = base_active + (size_t)r * 512;
        uint8_t* dm = default_mask + (size_t)r * 512;
        for (int c = 0; c < 512; c++) {
            bv[c] = (float)d[c];
            ba[c] = ac[c];
            dm[c] = ac[c];
        }
    }
}

/* ── P7: OutputSmoother 输出平滑器 C 化（对齐 src/engine/output_smoother.py
 *      OutputSmoother.process 的 numpy 向量化语义, 逐位复刻）────────────────
 *
 * 调用契约（每 universe 每 tick 一次）:
 *   ce_smoother_create(max_universes) → 状态全部持久在 C（每 universe:
 *   current/target/velocity 各 512×f32 + last_update/last_target_change
 *   各 512×f64, 与 Python _UniverseState 布局一致）
 *   ce_smoother_apply(ctx, uni, now, raw[512], attack_s, release_s,
 *                     smoothing, inertia, overshoot, deadband, curve,
 *                     out[512])
 *     now 由调用方传 time.monotonic()（与 Python process() 同源）
 *     attack_s/release_s 由调用方按 max(0.001, ms/1000.0) 计算（同 get_param）
 *     deadband 为 int（同 int(self.get_param("deadband"))）
 *   返回: 0 = 首帧（out=raw 原样拷贝, 状态已初始化）; 1 = 平滑帧; -1 = 参数错
 *   旁通(bypass) 不进 C——调用方 is_bypass() 时直接原样直通（同 Python）。
 *
 * Python 逐帧语义对照（全部通道运算 float32; numpy 弱标量 NEP50 不下提升级）:
 *   dt = clip32(now - last_update, 0.001, 0.5);  last_update = now
 *   changed = |target_f - target| > deadband → target=target_f,
 *             last_target_change = now
 *   diff = target - current;  tc = f32(going_up ? attack_s : release_s)
 *   converged = |diff| < 0.5 → current = target, velocity = 0
 *   active 通道:
 *     frac = clip01(dt/tc); frac = ease(curve, frac)（f32→double 算→f32,
 *       与 Python _apply_curve 同式; curve: 0=linear 1=ease_in 2=ease_out
 *       3=ease_in_out 4=exponential）
 *     smoothing > 0: step = f32(1-smoothing) * diff * frac
 *     else:          step = diff * frac
 *     new_val = current + step
 *     inertia > 0:  inertia_c = clamp(inertia, 0, 0.98)   （R181 P2-1 上限）
 *                   velocity += (diff/tc - velocity) * f32(1-inertia_c)
 *                   nv_i = current + velocity*dt
 *                   new_val = (nv_i 越过 target ? new_val : nv_i)
 *                   （R181 P2-1: 越过即回退基础步 → 单帧位移有界;
 *                     旧实现 dt>tc 时冲过目标 → 反向加速 → 值冲轨不收敛）
 *     else:         velocity = (new_val - current) / max(dt, 0.001)  (过冲前!)
 *     overshoot > 0: elapsed = clip32(now - last_target_change, 0, +inf)
 *                    progress = clip01(elapsed/tc)
 *                    progress > 0.7 时: oph = clip01((progress-0.7)/0.3)
 *                      oa = sin32(oph*pi) * overshoot * |diff|（going_up 取号）
 *                      new_val += oa
 *     current = clip(new_val, 0, 255)
 *   输出: out = (u8) clip(np.round(current), 0, 255)
 *
 * 位级等价要点:
 *   - np.round(float32) == rintf（半偶数舍入, 2M 采样实证一致）
 *   - np.sin(float32) ≠ sinf —— numpy 2.4.4 用 Highway simd_sincos_f32
 *     （Cody-Waite 三段归约 + FMA 多项式, 逐 lane 独立）; 见 ce_npy_sinf。
 *   - 仅标量实现（两 DLL 变体同码, 构造上保证双变体输出一致）。
 */

CE_API float ce_npy_sinf(float x)
{
    /* numpy FLOAT_sin SIMD 内核: |x| <= 117435.992f 走 Cody-Waite,
     * 域外/NaN 回退 npy_sinf = UCRT sinf（与 numpy 同函数）。
     * 位级等价域（fuzz 实证）: |x| <= 22 与 [0, pi] 及回退域 100% 一致;
     * 22 < |x| <= 117435.992 内 x 恰近 pi/4 奇倍数的灾难性对消点有 ~0.03%
     * 1-ulp 偏差（numpy wheel 二进制内部舍入细节, 源级不可复现; 平滑器
     * sin 输入域恒为 oph*pi ∈ [0, pi], 不在其内）。 */
    const float max_cody = 117435.992f;
    if (!(x <= max_cody && x >= -max_cody))
        return sinf(x);

    const float two_over_pi = 0x1.45f306p-1f;
    const float pio2_high   = -0x1.921fb0p+00f;
    const float pio2_med    = -0x1.5110b4p-22f;
    const float pio2_low    = -0x1.846988p-48f;
    const float rint_magic  = 0x1.800000p+23f;   /* 12582912.0f */
    const float invf9 = 0x1.7d3bbcp-19f, invf7 = -0x1.a06bbap-13f;
    const float invf5 = 0x1.11119ap-07f, invf3 = -0x1.555556p-03f;
    const float invf8 = 0x1.98e616p-16f, invf6 = -0x1.6c06dcp-10f;
    const float invf4 = 0x1.55553cp-05f, invf2 = -0x1.000000p-01f;
    const float invf0 = 0x1.000000p+00f;

    /* quadrant = rint(x*2/pi): 2^23 魔法数取整（半整数→偶, 同 NearestInt）*/
    float q = x * two_over_pi;
    q = q + rint_magic;
    q = q - rint_magic;

    /* Cody-Waite 三段归约 r = x + q*(c1+c2+c3), 每步 fma（同 Highway MulAdd）*/
    float r = fmaf(q, pio2_high, x);
    r = fmaf(q, pio2_med, r);
    r = fmaf(q, pio2_low, r);
    float r2 = r * r;

    /* sine 多项式（x∈[-pi/4,pi/4], T. Myklebust; 末步乘 r 加 r）*/
    float s = fmaf(invf9, r2, invf7);
    s = fmaf(s, r2, invf5);
    s = fmaf(s, r2, invf3);
    s = fmaf(s, r2, 0.0f);
    s = fmaf(s, r, r);

    /* cosine 多项式 */
    float c = fmaf(invf8, r2, invf6);
    c = fmaf(c, r2, invf4);
    c = fmaf(c, r2, invf2);
    c = fmaf(c, r2, invf0);

    int iq = (int)q;                 /* 魔法取整后已是整值 float */
    float out = ((iq & 1) == 0) ? s : c;
    if ((iq & 2) == 2)
        out = -out;
    return out;
}

typedef struct {
    float* current;             /* 512 f32 持久 */
    float* target;              /* 512 f32 持久 */
    float* velocity;            /* 512 f32 持久 */
    double* last_update;        /* 512 f64 持久（np.full(512, now, f64)） */
    double* last_target_change; /* 512 f64 持久 */
} ce_smoother_univ;

typedef struct {
    ce_smoother_univ* univs;
    uint8_t* touched;           /* 1 = 已初始化（Python _states 有键） */
    int cap;
} ce_smoother;

CE_API void ce_smoother_destroy(void* vsm);   /* create 出错路径提前用到 */

CE_API void* ce_smoother_create(int max_universes)
{
    ce_smoother* sm = (ce_smoother*)calloc(1, sizeof(ce_smoother));
    if (!sm) return NULL;
    sm->cap = max_universes > 0 ? max_universes : 64;
    sm->univs = (ce_smoother_univ*)calloc((size_t)sm->cap, sizeof(ce_smoother_univ));
    sm->touched = (uint8_t*)calloc((size_t)sm->cap, 1);
    if (!sm->univs || !sm->touched) { ce_smoother_destroy(sm); return NULL; }
    for (int i = 0; i < sm->cap; i++) {
        ce_smoother_univ* u = &sm->univs[i];
        u->current = (float*)malloc(512 * sizeof(float));
        u->target = (float*)malloc(512 * sizeof(float));
        u->velocity = (float*)malloc(512 * sizeof(float));
        u->last_update = (double*)malloc(512 * sizeof(double));
        u->last_target_change = (double*)malloc(512 * sizeof(double));
        if (!u->current || !u->target || !u->velocity ||
            !u->last_update || !u->last_target_change) {
            ce_smoother_destroy(sm);
            return NULL;
        }
    }
    return sm;
}

CE_API void ce_smoother_destroy(void* vsm)
{
    ce_smoother* sm = (ce_smoother*)vsm;
    if (!sm) return;
    for (int i = 0; i < sm->cap; i++) {
        ce_smoother_univ* u = &sm->univs[i];
        free(u->current); free(u->target); free(u->velocity);
        free(u->last_update); free(u->last_target_change);
    }
    free(sm->univs);
    free(sm->touched);
    free(sm);
}

/* reset_states(): 全清 */
CE_API void ce_smoother_reset(void* vsm)
{
    ce_smoother* sm = (ce_smoother*)vsm;
    if (!sm) return;
    memset(sm->touched, 0, (size_t)sm->cap);
}

/* reset_universe(): 清单个 universe */
CE_API void ce_smoother_reset_universe(void* vsm, int uni)
{
    ce_smoother* sm = (ce_smoother*)vsm;
    if (!sm || uni < 0 || uni >= sm->cap) return;
    sm->touched[uni] = 0;
}

/* 状态导出探针（对拍用; 未 touch 的 universe 不改写输出缓冲）*/
CE_API void ce_smoother_dump_state(void* vsm, int uni,
                                   float* current, float* target, float* velocity,
                                   double* last_update, double* last_target_change)
{
    ce_smoother* sm = (ce_smoother*)vsm;
    if (!sm || uni < 0 || uni >= sm->cap || !sm->touched[uni]) return;
    ce_smoother_univ* u = &sm->univs[uni];
    memcpy(current, u->current, 512 * sizeof(float));
    memcpy(target, u->target, 512 * sizeof(float));
    memcpy(velocity, u->velocity, 512 * sizeof(float));
    memcpy(last_update, u->last_update, 512 * sizeof(double));
    memcpy(last_target_change, u->last_target_change, 512 * sizeof(double));
}

/* 缓动曲线（双精度）: 与 Python output_smoother._apply_curve 同式。
 * f32 frac 由调用方先转 double（无损），此处 double 计算，返回 double；
 * 调用方再 (float) 转回（round-to-nearest, 与 numpy f32 转回逐位一致）。
 * curve: 0=linear 1=ease_in 2=ease_out 3=ease_in_out 4=exponential。
 * exponential 分母 = math.e - 1.0（0x1.5bf0a8b145769p+1 == math.e）。 */
static double ce_smoother_ease(int curve, double t)
{
    switch (curve) {
    case 1: return t * t;                                     /* ease_in */
    case 2: return 1.0 - pow(1.0 - t, 2.0);                   /* ease_out */
    case 3: return t * t * (3.0 - 2.0 * t);                   /* ease_in_out */
    case 4: return (exp(t) - 1.0) / (0x1.5bf0a8b145769p+1 - 1.0); /* exponential */
    default: return t;                                        /* linear */
    }
}

CE_API int ce_smoother_apply(void* vsm, int uni, double now,
                             const uint8_t* raw_data,
                             double attack_s, double release_s,
                             double smoothing, double inertia,
                             double overshoot, int deadband,
                             int curve, uint8_t* out)
{
    ce_smoother* sm = (ce_smoother*)vsm;
    if (!sm || uni < 0 || uni >= sm->cap || !raw_data || !out) return -1;
    ce_smoother_univ* st = &sm->univs[uni];

    if (!sm->touched[uni]) {
        sm->touched[uni] = 1;
        for (int c = 0; c < 512; c++) {
            float v = (float)raw_data[c];
            st->current[c] = v;
            st->target[c] = v;
            st->velocity[c] = 0.0f;
            st->last_update[c] = now;
            st->last_target_change[c] = now;
            out[c] = raw_data[c];
        }
        return 0;
    }

    const float deadband_f = (float)deadband;
    const int use_smoothing = smoothing > 0.0;
    const float alpha = (float)(1.0 - smoothing);
    /* R181 P2-1: 惯性上限 0.98（同 Python _clamp_inertia）——inertia=1.0 旧语义
     * vel_blend=0 → velocity 恒 0 → 输出永久冻结（"值死亡"）。NaN/负数 → 0。 */
    double inertia_c = inertia;
    if (!(inertia_c > 0.0)) inertia_c = 0.0;
    else if (inertia_c > 0.98) inertia_c = 0.98;
    const int use_inertia = inertia_c > 0.0;
    const float vel_blend = (float)(1.0 - inertia_c);
    const int use_overshoot = overshoot > 0.0;
    const float overshoot_f = (float)overshoot;

    /* 两遍结构: numpy 的 inertia `state.velocity += ...` 是 np.any(active) 门内
     * 的全数组操作（对刚收敛通道也写 blend*dv, diff 用收敛前值）; 且整个
     * 平滑块在 无任何 active 通道 时整体跳过。第一遍算各通道帧内量, 第二遍
     * 在 any_active 时执行更新。 */
    uint8_t active_f[512];
    float dv_f[512], tc_f[512], dt_f[512];
    int any_active = 0;
    for (int c = 0; c < 512; c++) {
        const float target_f = (float)raw_data[c];

        /* dt = clip32(now - last_update, 0.001, 0.5) */
        float dt = (float)(now - st->last_update[c]);
        if (dt < 0.001f) dt = 0.001f;
        else if (dt > 0.5f) dt = 0.5f;
        st->last_update[c] = now;

        /* 目标值变化检测（> deadband, 严格大于） */
        if (fabsf(target_f - st->target[c]) > deadband_f) {
            st->target[c] = target_f;
            st->last_target_change[c] = now;
        }

        const float diff = st->target[c] - st->current[c];
        const float tc = (float)((diff > 0.0f) ? attack_s : release_s);
        dt_f[c] = dt;
        tc_f[c] = tc;
        dv_f[c] = diff / tc;
        if (fabsf(diff) < 0.5f) {
            /* 已收敛（无条件）: current = target, velocity = 0 */
            st->current[c] = st->target[c];
            st->velocity[c] = 0.0f;
            active_f[c] = 0;
        } else {
            active_f[c] = 1;
            any_active = 1;
        }
    }

    if (any_active) {
        if (use_inertia) {
            /* 全数组 +=（含刚收敛通道, 用收敛前 diff/tc; np.any(active) 门内） */
            for (int c = 0; c < 512; c++) {
                st->velocity[c] = st->velocity[c]
                                + (dv_f[c] - st->velocity[c]) * vel_blend;
            }
        }
        for (int c = 0; c < 512; c++) {
            if (!active_f[c])
                continue;
            const float diff = st->target[c] - st->current[c];
            const float tc = tc_f[c];
            const float dt = dt_f[c];
            float clip_q = dt / tc;
            if (clip_q < 0.0f) clip_q = 0.0f;
            else if (clip_q > 1.0f) clip_q = 1.0f;
            clip_q = (float)ce_smoother_ease(curve, (double)clip_q);
            float step = use_smoothing ? (alpha * diff * clip_q)
                                       : (diff * clip_q);
            float new_val = st->current[c] + step;

            if (use_inertia) {
                /* R181 P2-1: 惯性步越过目标 → 回退基础步（同 Python
                 * _velocity_step）。不变式: 每帧位移朝目标且不超剩余距离;
                 * 惯性只许滞后。速度状态不归零（未越帧逐位同修前）。 */
                const float nv_i = st->current[c] + st->velocity[c] * dt;
                if (!((nv_i - st->target[c]) * diff > 0.0f))
                    new_val = nv_i;              /* 未越过 → 采用惯性步 */
            } else {
                const float denom = dt > 0.001f ? dt : 0.001f;
                st->velocity[c] = (new_val - st->current[c]) / denom;
            }

            if (use_overshoot) {
                float elapsed = (float)(now - st->last_target_change[c]);
                if (elapsed < 0.0f) elapsed = 0.0f;
                float progress = elapsed / tc;
                if (progress < 0.0f) progress = 0.0f;
                else if (progress > 1.0f) progress = 1.0f;
                if (progress > 0.7f) {
                    float oph = (progress - 0.7f) / 0.3f;
                    if (oph < 0.0f) oph = 0.0f;
                    else if (oph > 1.0f) oph = 1.0f;
                    float oa = ce_npy_sinf(oph * (float)M_PI)
                             * overshoot_f * fabsf(diff);
                    float oa_signed = (diff > 0.0f) ? oa : -oa;
                    new_val += oa_signed;
                }
            }

            float cv = new_val;
            if (cv < 0.0f) cv = 0.0f;
            else if (cv > 255.0f) cv = 255.0f;
            st->current[c] = cv;
        }
    }

    for (int c = 0; c < 512; c++) {
        /* out = (u8) clip(np.round(current), 0, 255); np.round==rintf */
        float rv = rintf(st->current[c]);
        if (rv < 0.0f) rv = 0.0f;
        else if (rv > 255.0f) rv = 255.0f;
        out[c] = (uint8_t)rv;
    }
    return 1;
}

/* ── T7: 摇杆轴处理纯数学（对齐 src/ui/physical_joystick_driver.py
 *    _apply_deadzone :653 与 src/ui/joystick_control_binding.py
 *    _normalize_axis :125 / _apply_curve :83 / _route_axis_values :341）
 *
 * 注意: 本节需标量 ucrtbase pow（CPython `**` 解析到的就是它, 实测逐位一致）。
 * 静态 CRT pow 非整数指数与之差 1 ulp（实测 d8f vs d90）; /Oi-、volatile
 * 函数指针、#pragma function(pow) 均无法取到动态实现 → 运行时按名解析
 * ucrtbase.dll 的 pow 导出（见 _ce_pow）。文件前部已验证内核只用整数指数
 * pow（两实现逐位一致, 如 transition 的 pow(prog, 2.0)）——不受影响。
 * ────────────────────────────────────────────────────────────────────────────
 * 三阶段管线（Python 侧每 25ms 每轴逐条绑定规则调用）:
 *   1. ce_joystick_deadzone  — 驱动层死区: |v|<dz → 0, 否则 sign*(|v|-dz)/(1-dz)
 *   2. ce_joystick_normalize — 绑定层归一: (raw-0.5) 死区/斜率重映射回 [0,1] + 反转
 *   3. ce_joystick_curve     — 6 类曲线 (linear/gamma/s_curve/stepped/
 *                              inverted/feel_balance)
 *   4. ce_joystick_axis      — 全链合成: normalize→curve→round(半偶)→int→clamp[0,255]
 * 位级等价纪律:
 *   - 全程 f64, 与 CPython 同用 UCRT libm (pow/floor/fabs) — MSVC /fp:precise
 *   - _ce_py_min/_ce_py_max 逐位复刻 Python max/min（-0.0 边缘: max(0,-0.0)=+0.0）
 *   - deadzone 边界 |v|==dz → +0.0 / -0.0 与原逻辑一致（sign 取 value>0 判否）
 *   - feel_balance 变指数 exp = lo_exp+(hi_exp-lo_exp)*v_abs 逐位一致
 *   - int(round()) 复用 ce_round_half_even（Python round 半偶舍入）
 * curve_id: 0=linear 1=gamma 2=s_curve 3=stepped 4=inverted 5=feel_balance
 * （其余值 = linear 兜底, 同 Python 未识别曲线名走 else 分支）
 */

/* 标量 ucrtbase pow（CPython `**` 同一导出）。volatile 防编译器缓存; 初始化
 * 幂等, 多线程竞态无害。兜底静态 pow 在无 ucrtbase 的机器才走（现实不可达）。 */
static double (*volatile _ce_ucrt_pow_fn)(double, double) = NULL;

static double _ce_pow(double x, double y)
{
    if (_ce_ucrt_pow_fn == NULL) {
        HMODULE m = GetModuleHandleA("ucrtbase.dll");
        if (m == NULL)
            m = LoadLibraryA("ucrtbase.dll");
        if (m != NULL)
            _ce_ucrt_pow_fn = (double (*)(double, double))
                (void*)GetProcAddress(m, "pow");
    }
    if (_ce_ucrt_pow_fn != NULL)
        return _ce_ucrt_pow_fn(x, y);
    return pow(x, y);
}

static double _ce_py_min(double a, double b)
{
    return (b < a) ? b : a;
}

static double _ce_py_max(double a, double b)
{
    return (a < b) ? b : a;
}

CE_API double ce_joystick_deadzone(double value, double dz)
{
    double a = fabs(value);
    if (a < dz)
        return 0.0;
    return ((value > 0.0) ? 1.0 : -1.0) * (a - dz) / (1.0 - dz);
}

CE_API double ce_joystick_normalize(double raw, double deadzone, int invert)
{
    double v = raw - 0.5;                    /* raw - 0.5 中心归零 */
    double half_dz = deadzone / 2.0;
    double span = 0.5 - half_dz;
    if (fabs(v) < half_dz)
        return 0.5;                          /* 死区内 → 中位 */
    if (v > 0.0)
        v = (v - half_dz) / span;
    else
        v = (v + half_dz) / span;
    v = v * 0.5 + 0.5;                       /* 映射回 [0,1] */
    if (invert)
        v = 1.0 - v;
    return _ce_py_max(0.0, _ce_py_min(1.0, v));
}

CE_API double ce_joystick_curve(double normalized, int curve_id,
                                double gamma, int steps)
{
    double v = _ce_py_max(0.0, _ce_py_min(1.0, normalized));
    double g = (gamma < 0.1) ? 0.1 : gamma;  /* max(0.1, gamma) */
    switch (curve_id) {
    case 1:   /* gamma */
        v = _ce_pow(v, g);
        break;
    case 2: { /* s_curve: 分段幂 + 再归一（先 v/2.0 或 0.5+(v-1)/-2.0 再钳位） */
        if (v < 0.5)
            v = 2.0 * _ce_pow(v, g);
        else
            v = 1.0 - 2.0 * _ce_pow(1.0 - v, g);
        v = _ce_py_max(0.0, _ce_py_min(1.0,
            (v < 0.5) ? (v / 2.0) : (0.5 + (v - 1.0) / -2.0)));
        break;
    }
    case 3: { /* stepped: floor(v*s)/(s-1), s=max(2,steps) */
        int s = (steps < 2) ? 2 : steps;
        v = floor(v * (double)s) / (double)(s - 1);
        break;
    }
    case 4:   /* inverted */
        v = 1.0 - v;
        break;
    case 5: { /* feel_balance: 变指数幂, 指数随 |偏差| 从 lo_exp 渐变到 hi_exp */
        double balance = gamma;
        if (balance < 0.0) balance = 0.0;
        if (balance > 5.0) balance = 5.0;
        if (balance >= 0.01) {
            double v_centered = v - 0.5;
            double sign = (v_centered >= 0.0) ? 1.0 : -1.0;
            double v_abs = fabs(v_centered) * 2.0;
            double lo_exp = 0.55 - balance * 0.18;
            double hi_exp = 1.0 + balance * 0.8;
            double exp, v_out;
            if (lo_exp < 0.15) lo_exp = 0.15;
            if (hi_exp > 4.0) hi_exp = 4.0;
            exp = lo_exp + (hi_exp - lo_exp) * v_abs;
            v_out = _ce_py_max(0.0, _ce_py_min(1.0, _ce_pow(v_abs, exp)));
            v = 0.5 + sign * v_out / 2.0;
        }
        break;
    }
    default:  /* linear 与未知曲线 */
        break;
    }
    return _ce_py_max(0.0, _ce_py_min(1.0, v));
}

CE_API int ce_joystick_axis(double raw, double deadzone, int invert,
                            int curve_id, double gamma, int steps,
                            int range_lo, int range_hi)
{
    double norm = ce_joystick_normalize(raw, deadzone, invert);
    double curved = ce_joystick_curve(norm, curve_id, gamma, steps);
    int64_t mapped = ce_round_half_even(
        (double)range_lo + curved * (double)(range_hi - range_lo));
    if (mapped < 0) mapped = 0;              /* max(0, min(255, mapped)) */
    if (mapped > 255) mapped = 255;
    return (int)mapped;
}
/* T7 续: ce_xml_attrs_batch — 对一批命中 (开标签跨度) 批量提取属性值的

/* ══════════════════════════════════════════════════════════════════════
 * P5-XML: 工程 XML 标签快速扫描 (2026-08-03 探索, 未接 hook)
 *
 * 动机: src/ui/import_depence.py 用 xml.etree.ElementTree 解析大工程
 *       project.xml (至冬7.0 实测 157MB / 720,828 个开标签), ET.parse
 *       单步 ~4.9s 是导入主瓶颈。本组内核做 C 级单遍扫描:
 *         1) ce_xml_scan_tags   — 找出目标标签的全部开标签命中, 输出
 *            (tag_idx, start, span_end, elem_end); 内部维护元素开标签栈,
 *            在闭标签处回填每个命中元素的结束偏移 (自闭合 elem_end==span_end)。
 *         2) ce_xml_attrs_batch — 对一批命中 (开标签跨度) 批量提取属性值的
 *            原始字节区间 (缺失 val_off=-1; 值含 '&' 置 has_entity=1,
 *            Python 侧按 XML 实体反义 —— 实测 DEPENCE 工程 0 次出现)。
 *       Python 侧据此重建"目标标签剪枝树"再喂给现有解析函数, 语义零改动;
 *       基准与一致性对拍见 render_lab/bench/ce_bench_xml.py。
 *
 * 语义对齐 expat/ET:
 *   - 标签/属性名取局部名 (带前缀 '<p:Mode>' 亦匹配 "Mode"; 工程无前缀)
 *   - 跳过 <?...?> / <!--...--> / <![CDATA[...]]> / <!DOCTYPE ...>
 *     (DOCTYPE 内部子集不支持 —— 工程 XML 无 DTD)
 *   - 属性值支持单/双引号; '>' 允许出现在引号内; 重复属性 last-wins (同 ET)
 *   - 假定 well-formed (开/闭标签名配对)
 * 返回: >=0 命中数; -1 = out 容量不足 (max_hits); -2 = 元素栈溢出。
 */
typedef struct {
    int32_t tag_idx;   /* 命中标签在 tags[] 中的下标 */
    int32_t start;     /* '<' 相对 data 的偏移 */
    int32_t span_end;  /* 开标签 '>' 之后一字节 (自闭合则为 '/>' 之后) */
    int32_t elem_end;  /* 闭标签 '>' 之后一字节; 自闭合 == span_end */
} CE_XMLHIT;

typedef struct {
    int32_t start;
    int32_t span_end;
} CE_XMLSPAN;

typedef struct {
    int32_t val_off;    /* 属性值首字节偏移 (引号之间); 缺失 = -1 */
    int32_t val_len;    /* 原始值长度 */
    int32_t has_entity; /* 值含 '&' → Python 侧需按实体反义 */
} CE_XMLATTR;

/* 元素开标签栈条目: 只记名字 (指向 data 内) 与命中下标 */
#define CE_XML_STACK_MAX_B 65536
typedef struct {
    const char* name;
    int32_t name_len;
    int32_t hit_idx;
} CE_XMLSTK;

/* 在 [p,end) 中找 pat 首次出现, 返回位置; 找不到 NULL */
static const char* _ce_xml_skip_until(const char* p, const char* end,
                                      const char* pat, int plen)
{
    while (p + plen <= end) {
        if (p[0] == pat[0] &&
            (plen == 1 || memcmp(p, pat, (size_t)plen) == 0))
            return p;
        p++;
    }
    return NULL;
}

/* 读 XML 名字 (含前缀 ':' 与 '-','.','_'), 返回全长; *local = 局部名起点
 * (最后一个 ':' 之后)。名字后可接空白 / '=' / '/' / '>' / 引号。 */
static int _ce_xml_name(const char* p, const char* end, const char** local)
{
    const char* q = p;
    const char* last_colon = NULL;
    while (q < end) {
        char c = *q;
        if (c == ':') {
            last_colon = q;
            q++;
            continue;
        }
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n' ||
            c == '/' || c == '>' || c == '=' || c == '"' || c == '\'')
            break;
        q++;
    }
    *local = last_colon ? last_colon + 1 : p;
    return (int)(q - p);
}

/* 局部名精确匹配 tags[], 返回下标或 -1 */
static int _ce_xml_match(const char* local, int local_len,
                         const char* const* tags, int n_tags)
{
    for (int i = 0; i < n_tags; i++) {
        size_t tl = strlen(tags[i]);
        if ((size_t)local_len == tl && memcmp(local, tags[i], tl) == 0)
            return i;
    }
    return -1;
}

/* 跳过 '<?'/'<!' 分支 (PI/注释/CDATA/DOCTYPE); 返回跳过后的位置, 异常 NULL */
static const char* _ce_xml_skip_misc(const char* lt, const char* end)
{
    if (lt + 1 < end && lt[1] == '?') {
        const char* q = _ce_xml_skip_until(lt + 2, end, "?>", 2);
        return q ? q + 2 : NULL;
    }
    if (lt + 1 < end && lt[1] == '!') {
        if (end - lt >= 4 && memcmp(lt, "<!--", 4) == 0) {
            /* 注释: -- 之后允许空白再 '>' */
            const char* q = lt + 4;
            while (q + 1 < end) {
                if (q[0] == '-' && q[1] == '-') {
                    const char* r = q + 2;
                    while (r < end && (*r == ' ' || *r == '\t' ||
                                       *r == '\r' || *r == '\n'))
                        r++;
                    if (r < end && *r == '>')
                        return r + 1;
                }
                q++;
            }
            return NULL;
        }
        if (end - lt >= 9 && memcmp(lt, "<![CDATA[", 9) == 0) {
            const char* q = _ce_xml_skip_until(lt + 9, end, "]]>", 3);
            return q ? q + 3 : NULL;
        }
        /* 其他 <!...> (DOCTYPE 无内部子集) */
        {
            const char* q = (const char*)memchr(lt, '>', (size_t)(end - lt));
            return q ? q + 1 : NULL;
        }
    }
    return NULL; /* 非 '?' 非 '!' → 调用方按开标签处理 */
}

/* 闭标签 </name>: 出栈并回填命中元素的 elem_end; 返回 '>' 之后位置或 NULL。
 * 名字比较用局部名 (带前缀时 ':' 后段), 与开标签入栈的局部名语义一致。 */
static const char* _ce_xml_close_tag(const char* lt, const char* end,
                                     const char* data, CE_XMLSTK* stack,
                                     int* sp, CE_XMLHIT* out)
{
    const char* local = NULL;
    int nlen = _ce_xml_name(lt + 2, end, &local);
    int local_len = nlen - (int)(local - (lt + 2));
    const char* gt = (const char*)memchr(lt + 2 + nlen, '>',
                                         (size_t)(end - lt - 2 - nlen));
    if (!gt)
        return NULL;
    if (*sp > 0 && stack[*sp - 1].name_len == local_len &&
        memcmp(stack[*sp - 1].name, local, (size_t)local_len) == 0) {
        if (stack[*sp - 1].hit_idx >= 0)
            out[stack[*sp - 1].hit_idx].elem_end = (int32_t)(gt - data) + 1;
        (*sp)--;
    } else {
        /* 防御: 顶名不配时向下搜 (well-formed 下不走此路) */
        int k = *sp - 1;
        while (k >= 0 && !(stack[k].name_len == local_len &&
                           memcmp(stack[k].name, local,
                                  (size_t)local_len) == 0))
            k--;
        if (k >= 0) {
            for (int m = *sp - 1; m >= k; m--)
                if (stack[m].hit_idx >= 0)
                    out[stack[m].hit_idx].elem_end =
                        (int32_t)(gt - data) + 1;
            *sp = k;
        }
    }
    return gt + 1;
}

CE_API int ce_xml_scan_tags(const char* data, size_t len,
                            const char* const* tags, int n_tags,
                            CE_XMLHIT* out, int max_hits)
{
    static CE_XMLSTK stack[CE_XML_STACK_MAX_B]; /* 单线程基准; 合入时改线程局部 */
    int sp = 0;
    int n_hits = 0;
    const char* end = data + len;
    const char* p = data;
    while (p < end) {
        const char* lt = (const char*)memchr(p, '<', (size_t)(end - p));
        if (!lt || lt + 1 >= end)
            break;
        if (lt[1] == '?' || lt[1] == '!') {
            const char* np = _ce_xml_skip_misc(lt, end);
            if (!np)
                break;
            p = np;
            continue;
        }
        if (lt[1] == '/') {
            const char* np = _ce_xml_close_tag(lt, end, data, stack,
                                               &sp, out);
            if (!np)
                break;
            p = np;
            continue;
        }
        /* 开标签: 读名 → 找 '>' (引号内允许) → 自闭合判定 → 匹配/入栈 */
        {
            const char* local = NULL;
            int nlen = _ce_xml_name(lt + 1, end, &local);
            int local_len = nlen - (int)(local - (lt + 1));
            const char* q = lt + 1 + nlen;
            const char* gt = NULL;
            int self_closing = 0;
            while (q < end) {
                char ch = *q;
                if (ch == '"' || ch == '\'') {
                    char quote = ch;
                    q++;
                    while (q < end && *q != quote)
                        q++;
                    if (q >= end)
                        break;
                    q++;
                    continue;
                }
                if (ch == '/' && q + 1 < end && q[1] == '>') {
                    self_closing = 1;
                    gt = q + 1;
                    break;
                }
                if (ch == '>') {
                    gt = q;
                    break;
                }
                q++;
            }
            if (!gt)
                break;
            int32_t span_end = (int32_t)(gt - data) + 1;
            int ti = _ce_xml_match(local, local_len, tags, n_tags);
            int32_t hit_idx = -1;
            if (ti >= 0) {
                if (n_hits >= max_hits)
                    return -1;
                hit_idx = n_hits;
                out[n_hits].tag_idx = ti;
                out[n_hits].start = (int32_t)(lt - data);
                out[n_hits].span_end = span_end;
                out[n_hits].elem_end = -1;
                n_hits++;
            }
            if (self_closing) {
                if (hit_idx >= 0)
                    out[hit_idx].elem_end = span_end;
            } else {
                if (sp >= CE_XML_STACK_MAX_B)
                    return -2;
                stack[sp].name = local;
                stack[sp].name_len = local_len;
                stack[sp].hit_idx = hit_idx;
                sp++;
            }
            p = gt + 1;
        }
    }
    return n_hits;
}

/* ══════════════════════════════════════════════════════════════════════
 * ce_xml_scan_tags_par — ce_xml_scan_tags 分区并行版 (2026-08-04)
 *
 * 动机: 149MB 工程 XML 单遍串行扫描 380-500ms 是导入首段; 并行化 =
 *   字节流分区 + 事件两阶段:
 *     P1 (OpenMP 并行): 每块独立扫描, 对开/闭标签 (PI/注释/CDATA/
 *       DOCTYPE 跳过与串行版同一函数) 产出事件 {start, 局部名,
 *       span_end, 开/闭/自闭合}; 只记录 start < b1 的事件 (块边界
 *       归属前块, 零重复); 块内构造失败记 abort_at。
 *     P2 (串行): 按块序 (= 文档序) 重放事件, 栈配对回填 elem_end,
 *       逻辑与 _ce_xml_close_tag / 串行开标签段逐字一致 (含防御性
 *       向下搜栈); 全局最早 abort_at 截断 (等价串行版 break 点)。
 *   位级等价依据: P1 对每个标签的识别只依赖该标签自身字节 (上下文
 *   无关, 界限=全文件 end), well-formed 输入下与串行逐字节一致;
 *   单线程路径直接委托串行版 (n_threads<=1 或 len<2MB), 零开销。
 *   返回: 同 ce_xml_scan_tags (>=0 命中数; -1 容量; -2 栈溢出; -3 分配失败)。
 */
#define CE_XML_SCAN_MARGIN (1 << 20) /* 1MB: 块间查找延续区 (真实标签 <1KB) */

typedef struct {
    int32_t start;      /* '<' 相对 data 偏移 */
    int32_t name_off;   /* 局部名起点 (相对 data) */
    int32_t name_len;
    int32_t span_end;   /* 开: '>' 之后; 闭: '>' 之后 (elem_end 回填值) */
    int32_t flags;      /* 1 = 闭标签; 2 = 自闭合开标签 */
} CE_XMLEV;

typedef struct {
    CE_XMLEV* ev;
    int64_t cap;
    int64_t n;
    int32_t abort_at;   /* <0 = 无; 否则块内首个失败 '<' 偏移 */
    int32_t alloc_fail;
} CE_XMLCHUNK;

/* P1 单块扫描: [b0, b1) 内找 '<', 只记录 start < b1 的事件;
 * 标签解析界限=全文件 end (与串行同), 故无 MARGIN 截断问题。 */
static void _ce_xml_ev_chunk(const char* data, size_t len,
                             size_t b0, size_t b1,
                             CE_XMLCHUNK* ch)
{
    const char* end = data + len;
    const char* search_end = data + b1;
    const char* p = data + b0;
    CE_XMLEV* ev = ch->ev;
    int64_t cap = ch->cap;
    int64_t n = 0;
    ch->abort_at = -1;
    while (p < search_end) {
        const char* lt = (const char*)memchr(p, '<',
                                             (size_t)(search_end - p));
        if (!lt || lt + 1 >= end)
            break;
        if (lt[1] == '?' || lt[1] == '!') {
            const char* np = _ce_xml_skip_misc(lt, end);
            if (!np) {
                ch->abort_at = (int32_t)(lt - data);
                break;
            }
            p = np;
            continue;
        }
        if (lt[1] == '/') {
            /* 闭标签: 名 + 普通 memchr '>' (与 _ce_xml_close_tag 同) */
            const char* local = NULL;
            int nlen = _ce_xml_name(lt + 2, end, &local);
            int local_len = nlen - (int)(local - (lt + 2));
            const char* gt = (const char*)memchr(
                lt + 2 + nlen, '>', (size_t)(end - lt - 2 - nlen));
            if (!gt) {
                ch->abort_at = (int32_t)(lt - data);
                break;
            }
            if (n >= cap) {
                int64_t ncap = cap * 2;
                CE_XMLEV* nev = (CE_XMLEV*)realloc(
                    ev, (size_t)ncap * sizeof(CE_XMLEV));
                if (!nev) {
                    ch->alloc_fail = 1;
                    break;
                }
                ev = nev;
                cap = ncap;
            }
            ev[n].start = (int32_t)(lt - data);
            ev[n].name_off = (int32_t)(local - data);
            ev[n].name_len = local_len;
            ev[n].span_end = (int32_t)(gt - data) + 1;
            ev[n].flags = 1;
            n++;
            p = gt + 1;
            continue;
        }
        /* 开标签: 名 + 引号感知 '>' 搜索 + 自闭合 (与串行段逐字同) */
        {
            const char* local = NULL;
            int nlen = _ce_xml_name(lt + 1, end, &local);
            int local_len = nlen - (int)(local - (lt + 1));
            const char* q = lt + 1 + nlen;
            const char* gt = NULL;
            int self_closing = 0;
            while (q < end) {
                char ch = *q;
                if (ch == '"' || ch == '\'') {
                    char quote = ch;
                    q++;
                    while (q < end && *q != quote)
                        q++;
                    if (q >= end)
                        break;
                    q++;
                    continue;
                }
                if (ch == '/' && q + 1 < end && q[1] == '>') {
                    self_closing = 1;
                    gt = q + 1;
                    break;
                }
                if (ch == '>') {
                    gt = q;
                    break;
                }
                q++;
            }
            if (!gt) {
                ch->abort_at = (int32_t)(lt - data);
                break;
            }
            if (n >= cap) {
                int64_t ncap = cap * 2;
                CE_XMLEV* nev = (CE_XMLEV*)realloc(
                    ev, (size_t)ncap * sizeof(CE_XMLEV));
                if (!nev) {
                    ch->alloc_fail = 1;
                    break;
                }
                ev = nev;
                cap = ncap;
            }
            ev[n].start = (int32_t)(lt - data);
            ev[n].name_off = (int32_t)(local - data);
            ev[n].name_len = local_len;
            ev[n].span_end = (int32_t)(gt - data) + 1;
            ev[n].flags = self_closing ? 2 : 0;
            n++;
            p = gt + 1;
        }
    }
    ch->ev = ev;
    ch->cap = cap;
    ch->n = n;
}

CE_API int ce_xml_scan_tags_par(const char* data, size_t len,
                                const char* const* tags, int n_tags,
                                CE_XMLHIT* out, int max_hits,
                                int n_threads)
{
    int rc = 0;
    if (n_threads <= 1 || len < (2u << 20))
        return ce_xml_scan_tags(data, len, tags, n_tags, out, max_hits);
    {
        int P = n_threads > 64 ? 64 : n_threads;
        size_t chunk = (len + (size_t)P - 1) / (size_t)P;
        CE_XMLCHUNK* chunks = (CE_XMLCHUNK*)calloc(
            (size_t)P, sizeof(CE_XMLCHUNK));
        CE_XMLSTK* stack = NULL;
        if (!chunks)
            return -3;
        for (int c = 0; c < P; c++) {
            size_t b0 = (size_t)c * chunk;
            size_t b1 = b0 + chunk < len ? b0 + chunk : len;
            chunks[c].abort_at = -1;
            if (b0 >= len)
                continue;
            {
                size_t span = (b1 - b0) + CE_XML_SCAN_MARGIN;
                int64_t est = (int64_t)(span / 96) + 1024;
                chunks[c].ev = (CE_XMLEV*)malloc(
                    (size_t)est * sizeof(CE_XMLEV));
                chunks[c].cap = est;
                chunks[c].alloc_fail = chunks[c].ev ? 0 : 1;
            }
        }
        int c;
        #pragma omp parallel for schedule(static) num_threads(P)
        for (c = 0; c < P; c++) {
            if (chunks[c].ev) {
                size_t b0 = (size_t)c * chunk;
                size_t b1 = b0 + chunk < len ? b0 + chunk : len;
                _ce_xml_ev_chunk(data, len, b0, b1, &chunks[c]);
            }
        }
        for (int c = 0; c < P; c++) {
            if (chunks[c].alloc_fail) {
                rc = -3;
                goto done;
            }
        }
        stack = (CE_XMLSTK*)malloc((size_t)CE_XML_STACK_MAX_B
                                   * sizeof(CE_XMLSTK));
        if (!stack) {
            rc = -3;
            goto done;
        }
        /* P2: 事件重放 (块序 = 文档序) */
        {
            int64_t abort_at = (int64_t)len + 1;
            int sp = 0;
            int n_hits = 0;
            int done_p2 = 0;
            for (int c = 0; c < P; c++)
                if (chunks[c].abort_at >= 0
                    && chunks[c].abort_at < abort_at)
                    abort_at = chunks[c].abort_at;
            for (int c = 0; c < P && !done_p2; c++) {
                CE_XMLEV* ev = chunks[c].ev;
                for (int64_t k = 0; k < chunks[c].n && !done_p2; k++) {
                    CE_XMLEV* e = &ev[k];
                    if ((int64_t)e->start >= abort_at) {
                        done_p2 = 1;
                        break;
                    }
                    if (e->flags & 1) {
                        /* 闭: 与 _ce_xml_close_tag 同逻辑 */
                        if (sp > 0
                            && stack[sp - 1].name_len == e->name_len
                            && memcmp(stack[sp - 1].name,
                                      data + e->name_off,
                                      (size_t)e->name_len) == 0) {
                            if (stack[sp - 1].hit_idx >= 0)
                                out[stack[sp - 1].hit_idx].elem_end
                                    = e->span_end;
                            sp--;
                        } else {
                            int k2 = sp - 1;
                            while (k2 >= 0
                                   && !(stack[k2].name_len == e->name_len
                                        && memcmp(stack[k2].name,
                                                  data + e->name_off,
                                                  (size_t)e->name_len) == 0))
                                k2--;
                            if (k2 >= 0) {
                                for (int m2 = sp - 1; m2 >= k2; m2--)
                                    if (stack[m2].hit_idx >= 0)
                                        out[stack[m2].hit_idx].elem_end
                                            = e->span_end;
                                sp = k2;
                            }
                        }
                    } else {
                        /* 开: 匹配 → 计数/命中 → 自闭合或入栈 */
                        int ti = _ce_xml_match(data + e->name_off,
                                               e->name_len, tags, n_tags);
                        int32_t hit_idx = -1;
                        if (ti >= 0) {
                            if (n_hits >= max_hits) {
                                rc = -1;
                                done_p2 = 1;
                                break;
                            }
                            hit_idx = n_hits;
                            out[n_hits].tag_idx = ti;
                            out[n_hits].start = e->start;
                            out[n_hits].span_end = e->span_end;
                            out[n_hits].elem_end = -1;
                            n_hits++;
                        }
                        if (e->flags & 2) {
                            if (hit_idx >= 0)
                                out[hit_idx].elem_end = e->span_end;
                        } else {
                            if (sp >= CE_XML_STACK_MAX_B) {
                                rc = -2;
                                done_p2 = 1;
                                break;
                            }
                            stack[sp].name = data + e->name_off;
                            stack[sp].name_len = e->name_len;
                            stack[sp].hit_idx = hit_idx;
                            sp++;
                        }
                    }
                }
            }
            if (rc == 0)
                rc = n_hits;
        }
        free(stack);
    done:
        for (int c = 0; c < P; c++)
            free(chunks[c].ev);
        free(chunks);
        return rc;
    }
}

/* 批量属性提取: 对 spans[0..n_spans) 每个开标签跨度, 提取 attr_names[0..n_attrs)
 * 的原始值区间到 out[n_spans*n_attrs] (行主序, 每元素 n_attrs 条, 缺失
 * val_off=-1; 重复属性 last-wins, 与 ET dict 行为一致)。
 *
 * 并行化 (2026-08-04): span 间完全独立 (只读 spans/data, 只写自己的
 * out 行) → OpenMP 静态分块按 span 并行; 属性名长度表提出外层循环
 * (原实现每次比较都 strlen — Object 组 118K span × 10 属性实测 592ms
 * 成为导入热段)。输出逐字节同串行版。 */
#define CE_XML_MAX_ATTRS_TAB 32
CE_API void ce_xml_attrs_batch(const char* data, size_t len,
                               const CE_XMLSPAN* spans, int n_spans,
                               const char* const* attr_names, int n_attrs,
                               CE_XMLATTR* out)
{
    size_t alen_tab[CE_XML_MAX_ATTRS_TAB];
    int tab_n = (n_attrs < CE_XML_MAX_ATTRS_TAB) ? n_attrs
                                                 : CE_XML_MAX_ATTRS_TAB;
    (void)len;
    for (int j = 0; j < tab_n; j++)
        alen_tab[j] = strlen(attr_names[j]);
    int s;
    #pragma omp parallel for schedule(static)
    for (s = 0; s < n_spans; s++) {
        const char* pend = data + spans[s].span_end;
        const char* p = data + spans[s].start + 1;
        CE_XMLATTR* rec = out + (size_t)s * (size_t)n_attrs;
        for (int j = 0; j < n_attrs; j++) {
            rec[j].val_off = -1;
            rec[j].val_len = 0;
            rec[j].has_entity = 0;
        }
        while (p < pend && *p != ' ' && *p != '\t' && *p != '\r' &&
               *p != '\n' && *p != '/' && *p != '>')
            p++; /* 跳过标签名 */
        for (;;) {
            while (p < pend && (*p == ' ' || *p == '\t' ||
                                *p == '\r' || *p == '\n'))
                p++;
            if (p >= pend || *p == '/' || *p == '>')
                break;
            const char* aname = p;
            const char* local = NULL;
            int anlen = _ce_xml_name(p, pend, &local);
            if (anlen <= 0)
                break;
            p += anlen;
            while (p < pend && (*p == ' ' || *p == '\t' ||
                                *p == '\r' || *p == '\n'))
                p++;
            if (p < pend && *p == '=') {
                p++;
                while (p < pend && (*p == ' ' || *p == '\t' ||
                                    *p == '\r' || *p == '\n'))
                    p++;
                if (p < pend && (*p == '"' || *p == '\'')) {
                    char quote = *p;
                    const char* vstart = p + 1;
                    const char* q = vstart;
                    while (q < pend && *q != quote)
                        q++;
                    int32_t voff = (int32_t)(vstart - data);
                    int32_t vlen = (int32_t)(q - vstart);
                    int alen_local = anlen - (int)(local - aname);
                    for (int j = 0; j < n_attrs; j++) {
                        size_t al = (j < tab_n) ? alen_tab[j]
                                                : strlen(attr_names[j]);
                        if ((size_t)alen_local == al &&
                            memcmp(local, attr_names[j], al) == 0) {
                            int b;
                            int has_ent = 0;
                            for (b = 0; b < vlen; b++)
                                if (vstart[b] == '&')
                                    has_ent = 1;
                            rec[j].val_off = voff;
                            rec[j].val_len = vlen;
                            rec[j].has_entity = has_ent;
                            break;
                        }
                    }
                    p = (q < pend) ? q + 1 : pend;
                    continue;
                }
            }
            /* 无值属性 (畸形): 跳到下一个名字起点 */
            while (p < pend && *p != ' ' && *p != '\t' && *p != '\r' &&
                   *p != '\n' && *p != '/' && *p != '>')
                p++;
        }
    }
}

/* ── P5-XML 续: ce_xml_attrs_flatten — 批量属性值扁平化 (2026-08-04) ─────
 *
 * 动机: ce_probe_xml_attr_prof 分段实测 attrs 段 C 内核仅 ~0.11s
 *       (S2), 生产包装 ~2.0s 全在 Python 侧 (S1 逐命中建 _SPAN 数组、
 *       S3 bytes(recs) 拷贝、S4 平铺 iter_unpack 循环逐条切片+decode)。
 *       本 API 把属性值一次性写入连续扁平缓冲 (C 内实体反义 + 空白
 *       归一化, expat 同语义), Python 侧一次 decode + 一次切片。
 *
 * 与 ce_xml_attrs_batch 的差异:
 *   - 输入直接吃 CE_XMLHIT 数组 + tag_idx 过滤 (Python 侧不再逐命中
 *     建 _SPAN, 每标签一次调用)
 *   - out_idx 每记录 4 槽 (匹配命中行主序, 每命中 n_attrs 组):
 *       [0] flat_off  — 值在扁平缓冲内的字节偏移
 *       [1] flat_len  — 解码后字节长
 *       [2] char_off  — 值在解码后字符串内的字符偏移 (UTF-8 码点计数,
 *                        Python 侧整体 decode 后按字符切片)
 *       [3] char_len  — 解码后字符长
 *     缺失属性 → [-1, 0, -1, 0]
 *   - 实体反义单遍 C 实现 (expat 同语义, fuzz 见 bench/ce_fuzz_xml_attrs.py):
 *     具名 5 项 / 数字引用 &#N; &#xH; / \r\n \r \n \t → 空格 (引用不参与
 *     归一化); 其他 '&' 形式 → *unknown_entity 递增 (Python 回退 ET)
 *   - 调用方保证 flat_cap >= Σ(匹配命中 span 长) (反义只缩不增);
 *     返回值 = 实际写入字节数, 恒 <= flat_cap
 *
 * 两遍 OMP: 遍1 扫描 + 统计解码后字节/字符长, 同时把 (data 内值偏移,
 *   原始长) 存入临时表 → 顺序双前缀和 (字节 + 字符) → 遍2 由临时表
 *   直拷 (免重扫)。out_idx 在遍1 暂存 [-2, len, -2, clen], 前缀后
 *   改写为 [flat_off, len, char_off, clen]。
 */

/* 数字字符引用解析: 输入 '&' 后的 "&#..." 段 (q 指向 '#' 之后的数字起点,
 * 或 'x'/'X' 之后的十六进制起点), 成功 → *after = ';' 后一字节, 返回码点;
 * 畸形/越界/非法字符 (0, 代理区, >0x10FFFF) → -1 (调用方记 unknown 回退)。
 * expat 同语义: 数字引用不参与空白归一化。 */
static long _ce_xml_num_ref(const char* q, const char* end, const char** after)
{
    int base = 10;
    if (q < end && (*q == 'x' || *q == 'X')) {
        base = 16;
        q++;
    }
    const char* d0 = q;
    unsigned long cp = 0;
    while (q < end) {
        int dv;
        if (*q >= '0' && *q <= '9')
            dv = *q - '0';
        else if (base == 16 && *q >= 'a' && *q <= 'f')
            dv = *q - 'a' + 10;
        else if (base == 16 && *q >= 'A' && *q <= 'F')
            dv = *q - 'A' + 10;
        else
            break;
        cp = cp * (unsigned long)base + (unsigned long)dv;
        if (cp > 0x10FFFFu)
            return -1;
        q++;
    }
    if (q == d0 || q >= end || *q != ';')
        return -1;
    if (!((cp >= 1 && cp <= 0xD7FF) || (cp >= 0xE000 && cp <= 0x10FFFF)))
        return -1; /* 0 / NULL / 代理区 / 非字符区 → expat 拒绝 */
    *after = q + 1;
    return (long)cp;
}

/* 码点 → UTF-8 (expat 输出同式), 返回写入字节数 */
static int _ce_utf8_enc(char* dst, unsigned long cp)
{
    if (cp < 0x80) {
        dst[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800) {
        dst[0] = (char)(0xC0 | (cp >> 6));
        dst[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        dst[0] = (char)(0xE0 | (cp >> 12));
        dst[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        dst[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    dst[0] = (char)(0xF0 | (cp >> 18));
    dst[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    dst[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    dst[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

/* 具名实体 5 项 (XML 1.0 预定义): 返回替换字符或 0 (非具名) */
static char _ce_xml_named_ref(const char* q, const char* end, int* consumed)
{
    if (end - q >= 4 && q[0] == 'a' && q[1] == 'm' && q[2] == 'p' &&
        q[3] == ';') { *consumed = 4; return '&'; }
    if (end - q >= 3 && q[0] == 'l' && q[1] == 't' && q[2] == ';') { *consumed = 3; return '<'; }
    if (end - q >= 3 && q[0] == 'g' && q[1] == 't' && q[2] == ';') { *consumed = 3; return '>'; }
    if (end - q >= 5 && q[0] == 'q' && q[1] == 'u' && q[2] == 'o' &&
        q[3] == 't' && q[4] == ';') { *consumed = 5; return '"'; }
    if (end - q >= 5 && q[0] == 'a' && q[1] == 'p' && q[2] == 'o' &&
        q[3] == 's' && q[4] == ';') { *consumed = 5; return '\''; }
    return 0;
}

/* 实体反义单字符解码: 返回解码后长度 (>=0) 或 -1 (未知序列)。
 * expat 同语义 (2026-08-04 全量对拍, fuzz 见 bench/ce_fuzz_xml_attrs.py):
 *   - 具名 5 项 → 单字符 (不二次解析: &amp;lt; → &lt;)
 *   - 数字引用 &#N; / &#xH; → UTF-8 字符 (非 BMP 4 字节)
 *   - 字面空白归一化: \r\n → 1 空格, \r → 1 空格, \n → 1 空格, \t → 1 空格;
 *     \n\r 不合并 (XML 2.11 线端归一化先于 3.3.3 属性归一化)
 *   - 数字/具名引用不参与空白归一化 (&#10; → 字面 \n)
 *   - 其他 '&' 形式 (DTD 实体/畸形) → -1 → unknown → Python 回退 ET
 * 长度与拷贝必须同式: 前缀和按本函数分配 flat 偏移, 拷贝按本式写入。 */
static int _ce_xml_unesc_len(const char* v, int vlen)
{
    int out = 0;
    int i = 0;
    while (i < vlen) {
        if (v[i] != '&') {
            if (v[i] == '\r') {
                if (i + 1 < vlen && v[i+1] == '\n')
                    i += 2;
                else
                    i += 1;
                out += 1;
                continue;
            }
            out++;
            i++;
            continue;
        }
        if (i + 1 < vlen && v[i+1] == '#') {
            const char* after;
            long cp = _ce_xml_num_ref(v + i + 2, v + vlen, &after);
            if (cp < 0)
                return -1;
            out += (cp < 0x80) ? 1 : (cp < 0x800) ? 2
                 : (cp < 0x10000) ? 3 : 4;
            i = (int)(after - v);
            continue;
        }
        {
            int consumed;
            char r = _ce_xml_named_ref(v + i + 1, v + vlen, &consumed);
            if (r == 0)
                return -1;
            out += 1;
            i += consumed + 1;
        }
    }
    return out;
}

/* 反义拷贝: 把 v[0..vlen) 解码写入 dst, 返回写入字节数。
 * 与 _ce_xml_unesc_len 逐字同式 (未知序列原样拷贝 — 调用方见 unknown 即
 * 回退, 值未用)。 */
static int _ce_xml_unesc_copy(char* dst, const char* v, int vlen)
{
    int out = 0;
    int i = 0;
    while (i < vlen) {
        if (v[i] != '&') {
            if (v[i] == '\r') {
                if (i + 1 < vlen && v[i+1] == '\n')
                    i++;
                dst[out++] = ' ';
                i++;
                continue;
            }
            if (v[i] == '\n' || v[i] == '\t') {
                dst[out++] = ' ';
                i++;
                continue;
            }
            dst[out++] = v[i++];
            continue;
        }
        if (i + 1 < vlen && v[i+1] == '#') {
            const char* after;
            long cp = _ce_xml_num_ref(v + i + 2, v + vlen, &after);
            if (cp < 0) {
                dst[out++] = v[i++];
                continue;
            }
            out += _ce_utf8_enc(dst + out, (unsigned long)cp);
            i = (int)(after - v);
            continue;
        }
        {
            int consumed;
            char r = _ce_xml_named_ref(v + i + 1, v + vlen, &consumed);
            if (r == 0) {
                dst[out++] = v[i++];
                continue;
            }
            dst[out++] = r;
            i += consumed + 1;
        }
    }
    return out;
}

/* 单跨度扫描: 对 [start, span_end) 内的每个属性, 若局部名命中 attr_names,
 * 调 sink(ctx, j, vstart, vlen)。返回处理过的属性数 (供调试)。 */
typedef void (*_ce_xml_span_sink)(void* ctx, int j, const char* vstart,
                                  int vlen);

static void _ce_xml_scan_span(const char* data, const CE_XMLHIT* h,
                              int n_attrs, const char* const* attr_names,
                              const size_t* alen_tab, int tab_n,
                              _ce_xml_span_sink sink, void* ctx)
{
    const char* pend = data + h->span_end;
    const char* p = data + h->start + 1;
    while (p < pend && *p != ' ' && *p != '\t' && *p != '\r' &&
           *p != '\n' && *p != '/' && *p != '>')
        p++; /* 跳过标签名 */
    for (;;) {
        while (p < pend && (*p == ' ' || *p == '\t' || *p == '\r' ||
                            *p == '\n'))
            p++;
        if (p >= pend || *p == '/' || *p == '>')
            break;
        const char* aname = p;
        const char* local = NULL;
        int anlen = _ce_xml_name(p, pend, &local);
        if (anlen <= 0)
            break;
        p += anlen;
        while (p < pend && (*p == ' ' || *p == '\t' || *p == '\r' ||
                            *p == '\n'))
            p++;
        if (p < pend && *p == '=') {
            p++;
            while (p < pend && (*p == ' ' || *p == '\t' || *p == '\r' ||
                                *p == '\n'))
                p++;
            if (p < pend && (*p == '"' || *p == '\'')) {
                char quote = *p;
                const char* vstart = p + 1;
                const char* q = vstart;
                while (q < pend && *q != quote)
                    q++;
                int alen_local = anlen - (int)(local - aname);
                for (int j = 0; j < n_attrs; j++) {
                    size_t al = (j < tab_n) ? alen_tab[j]
                                            : strlen(attr_names[j]);
                    if ((size_t)alen_local == al &&
                        memcmp(local, attr_names[j], al) == 0) {
                        sink(ctx, j, vstart, (int)(q - vstart));
                        break;
                    }
                }
                p = (q < pend) ? q + 1 : pend;
                continue;
            }
        }
        /* 无值属性 (畸形): 跳到下一个名字起点 */
        while (p < pend && *p != ' ' && *p != '\t' && *p != '\r' &&
               *p != '\n' && *p != '/' && *p != '>')
            p++;
    }
}

/* 输出字符数 (UTF-8 码点计数, expat 语义同 _ce_xml_unesc_len):
 * 具名实体/数字引用/空白归一化各计 1 字符; 多字节 UTF-8 按引导字节计数。
 * 未知 '&' 序列 → -1 (调用方回退)。 */
static int _ce_xml_unesc_chars(const char* v, int vlen)
{
    int out = 0;
    int i = 0;
    while (i < vlen) {
        if (v[i] != '&') {
            if (v[i] == '\r') {
                if (i + 1 < vlen && v[i+1] == '\n')
                    i += 2;
                else
                    i += 1;
                out += 1;
                continue;
            }
            if (v[i] == '\n' || v[i] == '\t') {
                out += 1;
                i++;
                continue;
            }
            if ((v[i] & 0xC0) != 0x80)
                out++;
            i++;
            continue;
        }
        if (i + 1 < vlen && v[i+1] == '#') {
            const char* after;
            long cp = _ce_xml_num_ref(v + i + 2, v + vlen, &after);
            if (cp < 0)
                return -1;
            out += 1;   /* 一个码点 = 一个字符, 无论 UTF-8 字节数 */
            i = (int)(after - v);
            continue;
        }
        {
            int consumed;
            char r = _ce_xml_named_ref(v + i + 1, v + vlen, &consumed);
            if (r == 0)
                return -1;
            out += 1;
            i += consumed + 1;
        }
    }
    return out;
}

/* 原始字节数 → 字符数 (未知实体回退分支用; 值未被消费, 仅为对齐占位) */
static int _ce_xml_raw_chars(const char* v, int vlen)
{
    int out = 0;
    for (int i = 0; i < vlen; i++)
        if ((v[i] & 0xC0) != 0x80)
            out++;
    return out;
}

/* 遍1 回调: 统计解码后字节/字符长度, 存值跨度 (data 内偏移+原始长) 到
 * tmp[rec*2..rec*2+1] (供遍2 直拷, 免重扫); out_idx 每记录 4 槽:
 *   [0]=-2 命中标记(前缀改写)  [1]=解码后字节长
 *   [2]=-2 命中标记(前缀改写)  [3]=解码后字符长 */
typedef struct {
    int32_t* out_idx;
    int32_t* tmp;
    int n_attrs;
    int64_t* total;
    int* unknown_entity;
    const char* data;
    int64_t rec;
} _ce_xml_pass1_ctx;

static void _ce_xml_pass1_sink(void* vctx, int j, const char* vstart, int vlen)
{
    _ce_xml_pass1_ctx* ctx = (_ce_xml_pass1_ctx*)vctx;
    int len_out = _ce_xml_unesc_len(vstart, vlen);
    int chars_out = _ce_xml_unesc_chars(vstart, vlen);
    if (len_out < 0 || chars_out < 0) {
        len_out = vlen;
        chars_out = _ce_xml_raw_chars(vstart, vlen);
        #pragma omp atomic
        *ctx->unknown_entity += 1;
    }
    int64_t rec = ctx->rec * (int64_t)ctx->n_attrs + j;
    ctx->out_idx[rec * 4] = -2;       /* 命中标记 (前缀和处改写为真实偏移) */
    ctx->out_idx[rec * 4 + 1] = len_out;
    ctx->out_idx[rec * 4 + 2] = -2;
    ctx->out_idx[rec * 4 + 3] = chars_out;
    ctx->tmp[rec * 2] = (int32_t)(vstart - (ctx->data));
    ctx->tmp[rec * 2 + 1] = vlen;
    #pragma omp atomic
    *ctx->total += len_out;
}

CE_API int64_t ce_xml_attrs_flatten(const char* data, size_t len,
                                    const CE_XMLHIT* hits, int n_hits,
                                    int tag_idx,
                                    const char* const* attr_names,
                                    int n_attrs,
                                    char* out_flat, int64_t flat_cap,
                                    int32_t* out_idx, int* unknown_entity)
{
    (void)len;
    (void)flat_cap; /* 调用方保证 cap >= Σspan, 实体反义只缩不增 */
    size_t alen_tab[CE_XML_MAX_ATTRS_TAB];
    int tab_n = (n_attrs < CE_XML_MAX_ATTRS_TAB) ? n_attrs
                                                 : CE_XML_MAX_ATTRS_TAB;
    for (int j = 0; j < tab_n; j++)
        alen_tab[j] = strlen(attr_names[j]);
    *unknown_entity = 0;

    /* 遍0 (顺序): 匹配命中 → 行主序秩 (out_idx 只写匹配命中) */
    int32_t* rank = (int32_t*)malloc((size_t)n_hits * sizeof(int32_t));
    if (rank == NULL)
        return -1;
    int64_t n_matched = 0;
    for (int i = 0; i < n_hits; i++) {
        if (hits[i].tag_idx == tag_idx)
            rank[i] = (int32_t)(n_matched++);
        else
            rank[i] = -1;
    }
    if (n_matched == 0) {
        free(rank);
        return 0;
    }
    int64_t total = 0;

    /* 临时值跨度表: 遍1 存 (data 内偏移, 原始长) → 遍2 直拷免重扫。
     * 布局 per 记录 2 槽; 缺失 = -1。out_idx 每记录 4 槽:
     *   [flat_off, flat_len, char_off, char_len] (前缀和处落位)。 */
    int32_t* tmp = (int32_t*)malloc((size_t)n_matched * (size_t)n_attrs * 2 *
                                    sizeof(int32_t));
    if (tmp == NULL) {
        free(rank);
        return -1;
    }
    memset(tmp, 0xFF, (size_t)n_matched * (size_t)n_attrs * 2 *
                      sizeof(int32_t));

    /* 预清零: 缺失属性记录保持全 0, 前缀和处复位为 [-1,0,-1,0] */
    memset(out_idx, 0, (size_t)n_matched * (size_t)n_attrs * 4 *
                        sizeof(int32_t));

    /* 遍1 (并行): 每匹配命中解码后字节/字符长 + total 归约 */
    _ce_xml_pass1_ctx p1ctx;
    p1ctx.out_idx = out_idx;
    p1ctx.tmp = tmp;
    p1ctx.n_attrs = n_attrs;
    p1ctx.total = &total;
    p1ctx.unknown_entity = unknown_entity;
    p1ctx.data = data;
    {
        int i;
        #pragma omp parallel for schedule(static)
        for (i = 0; i < n_hits; i++) {
            if (hits[i].tag_idx != tag_idx)
                continue;
            _ce_xml_pass1_ctx c = p1ctx;
            c.rec = rank[i];
            _ce_xml_scan_span(data, &hits[i], n_attrs, attr_names, alen_tab,
                              tab_n, _ce_xml_pass1_sink, &c);
        }
    }

    /* 前缀和: 行主序累计 → 每个命中记录的 flat 字节偏移 + 字符偏移;
     * 缺失记录复位 [-1,0,-1,0] (空值命中记录 off 仍有效) */
    {
        int64_t pos = 0;
        int64_t cpos = 0;
        int64_t n_rec = n_matched * (int64_t)n_attrs;
        for (int64_t r = 0; r < n_rec; r++) {
            int32_t ln = out_idx[r * 4 + 1];
            if (out_idx[r * 4] == -2) {
                out_idx[r * 4] = (int32_t)pos;
                out_idx[r * 4 + 2] = (int32_t)cpos;
                pos += ln;
                cpos += out_idx[r * 4 + 3];
            } else {
                out_idx[r * 4] = -1;
                out_idx[r * 4 + 1] = 0;
                out_idx[r * 4 + 2] = -1;
                out_idx[r * 4 + 3] = 0;
            }
        }
    }

    /* 遍2 (并行): 由遍1 存下的值跨度直拷 (免重扫; 未知实体时值未用) */
    {
        int i;
        #pragma omp parallel for schedule(static)
        for (i = 0; i < n_hits; i++) {
            if (hits[i].tag_idx != tag_idx)
                continue;
            int64_t base = (int64_t)rank[i] * n_attrs;
            for (int j = 0; j < n_attrs; j++) {
                int64_t rec = base + j;
                int32_t vstart = tmp[rec * 2];
                if (vstart < 0)
                    continue;
                _ce_xml_unesc_copy(out_flat + out_idx[rec * 4],
                                   data + vstart, tmp[rec * 2 + 1]);
            }
        }
    }
    free(tmp);
    free(rank);
    return total;
}

/* ══════════════════════════════════════════════════════════════════════
 * P5-XML 续: ce_xml_hit_parents — 命中→直接父命中下标 + tag_idx 数组
 * (2026-08-04)
 *
 * 动机: rebuild 段的 Python 栈式重建 (bench 实测 1.79s, 其中 while 栈
 * 循环 + 每命中 (node, elem_end) 元组分配占大半) 整体前置到 C: 栈算法
 * 与 Python 原实现逐字一致 (见 _pruned_root 历史版本注释), 输出
 * out_parents[i] = 直接父命中下标 (无 = -1), out_tag_idx[i] = 命中标签
 * 下标。Python 侧重建退化为单遍: Element 创建 + attrib 挂载 + 按父
 * 下标 append, 无 while 循环、无元组分配。
 * 栈上限 CE_XML_STACK_MAX_B 与扫描器同一常量: 扫描深度超过上限会先
 * 返回 -2 回退, 故命中子集深度恒 < 上限 (防御分支仅作保底)。
 */
CE_API void ce_xml_hit_parents(const CE_XMLHIT* hits, int n_hits,
                               size_t data_len, int* out_parents,
                               int* out_tag_idx)
{
    static struct {
        int32_t hit_idx;
        int32_t elem_end;
    } stack[CE_XML_STACK_MAX_B];
    int sp = 0;
    int32_t dl = (data_len < 0x7FFFFFFFu) ? (int32_t)data_len : 0x7FFFFFFF;
    for (int i = 0; i < n_hits; i++) {
        while (sp > 0 && stack[sp - 1].elem_end <= hits[i].start)
            sp--;
        out_parents[i] = (sp > 0) ? stack[sp - 1].hit_idx : -1;
        out_tag_idx[i] = hits[i].tag_idx;
        if (sp < CE_XML_STACK_MAX_B) {
            stack[sp].hit_idx = i;
            stack[sp].elem_end = (hits[i].elem_end >= 0) ? hits[i].elem_end
                                                         : dl;
            sp++;
        }
    }
}
/* ── T7 续: 电机 16bit 粗/精拆分与合并（对齐 src/engine/xy_reference.py）──
 *
 * 对齐目标（真实函数直引, 非抄写）:
 *   src/engine/xy_reference.py  _logical_to_16bit_pair :212  → ce_split_16bit
 *   src/engine/xy_reference.py  _read_axis_logical     :204  → ce_merge_16bit
 *   （src/ui/programmer_panel.py _motor_8bit_to_16bit_pair :197 为逐字节同式,
 *     同一内核覆盖两个真源）
 *
 * 拆分语义（_logical_to_16bit_pair, 逐行复刻）:
 *   1. logical = max(0.0, min(255.0, float(value))) —— 先 min 后 max；
 *      NaN 经 min 比较失败取首参 255.0（Python min/max 语义）
 *   2. logical >= 255.0 → v16 = 65535（满刻度直通, 不经 round）
 *   3. 否则 v16 = int(round(logical * 256.0)) —— Python round 是银行家
 *      舍入（ties-to-even）, 不是 C round()（half away from zero）
 *   4. v16 再钳 [0, 65535]；coarse = (v16 >> 8) & 0xFF, fine = v16 & 0xFF
 *
 * 合并语义（_read_axis_logical）:
 *   float((coarse << 8) + fine) / 256.0 —— int 精确移位相加后双精度除法；
 *   coarse/fine ∈ [0,255] 时 (coarse<<8|fine)/256.0 全程精确,
 *   与 Python float 逐位一致。无 fine 通道时 Python 读回 float(coarse):
 *   merge(coarse, 0) == coarse 精确成立, 该分支由调用方传 fine=0 覆盖,
 *   无需独立内核。
 */

static int64_t _ce_py_round_even(double x)
{
    double fl = floor(x);
    double diff = x - fl;
    int64_t r = (int64_t)fl;
    if (diff > 0.5) {
        r += 1;
    } else if (diff == 0.5 && (r & 1)) {
        r += 1;                 /* 精确半整: ties-to-even */
    }
    return r;
}

CE_API void ce_split_16bit(double value, uint8_t* out_coarse, uint8_t* out_fine)
{
    double m, logical;
    int64_t v16;

    m = (value < 255.0) ? value : 255.0;        /* min(255.0, value); NaN → 255.0 */
    logical = (0.0 < m) ? m : 0.0;              /* max(0.0, m) */

    if (logical >= 255.0) {
        v16 = 65535;
    } else {
        v16 = _ce_py_round_even(logical * 256.0);
    }
    if (v16 < 0) v16 = 0;
    if (v16 > 65535) v16 = 65535;

    if (out_coarse != NULL) {
        *out_coarse = (uint8_t)((v16 >> 8) & 0xFF);
    }
    if (out_fine != NULL) {
        *out_fine = (uint8_t)(v16 & 0xFF);
    }
}

CE_API double ce_merge_16bit(int coarse, int fine)
{
    return (double)((coarse << 8) + fine) / 256.0;
}
/* ── T8: fade 几何排序（对齐 src/engine/playback_engine.py
 *       _linear_direction_positions_for_fixture_ids :5818 /
 *       _order_fixture_ids_for_direction :6009）
 *
 * 分界: TopView 坐标解析（_virtual_topview_fixture_positions_for_ids, 依赖工程
 * 数据结构）留在 Python; 本内核承接纯数学:
 *   A. ce_fade_dir_positions — 线性方向位置系数: 按 scope 坐标域 span 归一化 →
 *      rtl/btf 反转(1.0-v) → 并列次轴排序 → 排名/max(1,n-1) 赋值（float32 输出,
 *      逐位对齐 numpy float32 数组赋值）
 *   B. ce_ordered_fixtures — 稳定排序: 按 (位置, 原索引) 升序（同 Python
 *      sorted(range(n), key=(float(pos[i]), i))）; 亦覆盖 cw/ccw 圆角 deltas
 *      的 np.lexsort((arange, deltas)) 语义（lexsort 稳定 = 按 deltas 稳定排序）
 *
 * 位级等价纪律:
 *   1. 全程 f64 同 Python; 最终 float32 存储 cast 同 numpy（四舍五入到偶）
 *   2. 排序稳定 = 自底向上归并（同键左优先, 同 Python sorted）
 *   3. 并列次键语义逐字复刻: 线性四向 (rank, 次轴坐标, 原索引);
 *      中心/边缘 (rank, 原索引)
 *   4. 阈值 1e-6 / denom = max(1, n-1) / 反转 1.0-v / 中心 (min+max)*0.5
 *      全部同原实现
 * 契约外: NaN 坐标（真实路径 span>1e-6 守卫保证有限; 位置为 [0,1] 归一值
 *   或模 tau 的圆角 deltas, 均有限）
 */

static double _ce_fade_scope_min(const double* v, int n)
{
    double m = v[0];
    for (int i = 1; i < n; i++)
        if (v[i] < m)
            m = v[i];
    return m;
}

static double _ce_fade_scope_max(const double* v, int n)
{
    double m = v[0];
    for (int i = 1; i < n; i++)
        if (v[i] > m)
            m = v[i];
    return m;
}

/* 三键条目: 线性四向分支排序键 = (key, tie, idx); idx 唯一, 稳定性兜底 */
typedef struct {
    double key;
    double tie;
    int32_t idx;
} _ce_fade_order_item;

/* 稳定自底向上归并（键比较同 Python 元组逐分量: 相等 → 左优先保原序） */
static void _ce_fade_order_sort3(_ce_fade_order_item* items,
                                 _ce_fade_order_item* tmp, int n)
{
    for (int width = 1; width < n; width *= 2) {
        for (int lo = 0; lo + width < n; lo += 2 * width) {
            int mid = lo + width;
            int hi = lo + 2 * width;
            int i = lo, j = mid, k = lo;
            if (hi > n)
                hi = n;
            while (i < mid && j < hi) {
                int take_left;
                if (items[j].key < items[i].key)
                    take_left = 0;
                else if (items[i].key < items[j].key)
                    take_left = 1;
                else if (items[j].tie < items[i].tie)
                    take_left = 0;
                else
                    take_left = 1;   /* (key,tie) 相等 → 左（原索引小）优先 */
                if (take_left)
                    tmp[k++] = items[i++];
                else
                    tmp[k++] = items[j++];
            }
            while (i < mid)
                tmp[k++] = items[i++];
            while (j < hi)
                tmp[k++] = items[j++];
            for (k = lo; k < hi; k++)
                items[k] = tmp[k];
        }
    }
}

/* 线性方向位置系数（mode: 0=ltr 1=rtl 2=ftb 3=btf 4=ctr 5=rtc）
 *
 * 输入:
 *   xs/ys   [n] 设备坐标（TopView 虚拟坐标已解析）
 *   sxs/sys [sn] scope 坐标（= scope_with_pos 已解析; NULL 或 sn<=0 →
 *           scope 回退 ids, 同 `scope_with_pos = [...] or ids`）
 * 输出:
 *   out     [n] f32 位置系数（0..1, 排名/max(1,n-1)）
 * 返回: 1 成功; 0 无效（未知方向/参数错/分配失败 → 调用方回退原路径）
 */
CE_API int ce_fade_dir_positions(const double* xs, const double* ys, int n,
                                 const double* sxs, const double* sys, int sn,
                                 int mode, float* out)
{
    const double* scope_x;
    const double* scope_y;
    double span_x, span_y, denom;
    int i;
    if (n <= 0)
        return 1;                  /* 空输入: 无输出, 成功 */
    if (mode < 0 || mode > 5)
        return 0;
    if (xs == NULL || ys == NULL || out == NULL)
        return 0;
    scope_x = sxs;
    scope_y = sys;
    if (scope_x == NULL || sn <= 0) {
        scope_x = xs;              /* scope_with_pos = [...] or ids */
        scope_y = ys;
        sn = n;
    }
    if (n == 1) {
        out[0] = 0.5f;
        return 1;
    }
    span_x = _ce_fade_scope_max(scope_x, sn) - _ce_fade_scope_min(scope_x, sn);
    span_y = _ce_fade_scope_max(scope_y, sn) - _ce_fade_scope_min(scope_y, sn);

    if (mode <= 3) {
        /* ── 线性四向 (ltr/rtl/ftb/btf): 主轴归一化 → 反转 → (rank,次轴,idx) ── */
        int use_y_axis = (mode == 2 || mode == 3);        /* ftb/btf */
        double primary_span = use_y_axis ? span_y : span_x;
        double fallback_span = use_y_axis ? span_x : span_y;
        int axis_index, tie_axis;
        double lo;
        _ce_fade_order_item* items;
        _ce_fade_order_item* tmp;
        if (primary_span > 1e-6) {
            axis_index = use_y_axis ? 1 : 0;
            lo = _ce_fade_scope_min(use_y_axis ? scope_y : scope_x, sn);
        } else if (fallback_span > 1e-6) {
            axis_index = use_y_axis ? 0 : 1;
            lo = _ce_fade_scope_min(use_y_axis ? scope_x : scope_y, sn);
        } else {
            axis_index = use_y_axis ? 1 : 0;
            lo = 0.0;
        }
        tie_axis = 1 - axis_index;
        items = (_ce_fade_order_item*)malloc((size_t)n * sizeof(_ce_fade_order_item));
        tmp = (_ce_fade_order_item*)malloc((size_t)n * sizeof(_ce_fade_order_item));
        if (items == NULL || tmp == NULL) {
            free(items);
            free(tmp);
            return 0;              /* 分配失败: 调用方回退原路径 */
        }
        for (i = 0; i < n; i++) {
            double rank;
            if (primary_span > 1e-6 || fallback_span > 1e-6) {
                double coord = axis_index ? ys[i] : xs[i];
                double span = primary_span > 1e-6 ? primary_span : fallback_span;
                rank = (coord - lo) / span;
            } else {
                rank = 0.5;
            }
            if (mode == 1 || mode == 3)        /* rtl/btf: 1.0 - v */
                rank = 1.0 - rank;
            items[i].key = rank;
            items[i].tie = tie_axis ? ys[i] : xs[i];
            items[i].idx = (int32_t)i;
        }
        _ce_fade_order_sort3(items, tmp, n);
        denom = (double)(n > 1 ? (n - 1) : 1);   /* max(1, n-1) */
        for (i = 0; i < n; i++)
            out[items[i].idx] = (float)((double)i / denom);
        free(items);
        free(tmp);
        return 1;
    }

    /* ── 中心/边缘 (ctr/rtc): 主导轴距中心距离 → (rank, idx) ── */
    {
        int use_x_axis = span_x >= span_y;
        const double* axis_values = use_x_axis ? scope_x : scope_y;
        double span = use_x_axis ? span_x : span_y;
        double center, max_dist;
        _ce_order_item* items;
        _ce_order_item* tmp;
        if (span <= 1e-6) {
            for (i = 0; i < n; i++)
                out[i] = 0.5f;
            return 1;
        }
        center = (_ce_fade_scope_min(axis_values, sn)
                  + _ce_fade_scope_max(axis_values, sn)) * 0.5;
        max_dist = 0.0;
        for (i = 0; i < sn; i++) {
            double d = fabs(axis_values[i] - center);
            if (d > max_dist)
                max_dist = d;
        }
        if (max_dist <= 1e-6) {
            for (i = 0; i < n; i++)
                out[i] = 0.5f;
            return 1;
        }
        items = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
        tmp = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
        if (items == NULL || tmp == NULL) {
            free(items);
            free(tmp);
            return 0;
        }
        for (i = 0; i < n; i++) {
            double coord = use_x_axis ? xs[i] : ys[i];
            double dist = fabs(coord - center) / max_dist;
            items[i].key = (mode == 4) ? dist : (1.0 - dist);   /* ctr/rtc */
            items[i].idx = (int32_t)i;
        }
        _ce_order_sort(items, tmp, n);
        denom = (double)(n > 1 ? (n - 1) : 1);
        for (i = 0; i < n; i++)
            out[items[i].idx] = (float)((double)i / denom);
        free(items);
        free(tmp);
        return 1;
    }
}

/* 稳定排序: 按 (位置, 原索引) 升序（同 sorted(range(n), key=(pos, idx))）
 *
 * 输入:
 *   positions [n] f32 位置系数（float32 值 → f64 加宽同 Python float() 键）
 * 输出:
 *   out       [n] int32 排序后原索引
 * 契约外: NaN（真实路径位置有限; NaN 排序序无定义, Python timsort 亦不稳）
 */
CE_API void ce_ordered_fixtures(const float* positions, int n, int32_t* out)
{
    _ce_order_item* items;
    _ce_order_item* tmp;
    int i;
    if (n <= 0 || positions == NULL || out == NULL)
        return;
    if (n == 1) {
        out[0] = 0;
        return;
    }
    items = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
    tmp = (_ce_order_item*)malloc((size_t)n * sizeof(_ce_order_item));
    if (items == NULL || tmp == NULL) {
        free(items);
        free(tmp);
        return;                    /* 分配失败: 输出不变, 调用方回退原路径 */
    }
    for (i = 0; i < n; i++) {
        items[i].key = (double)positions[i];
        items[i].idx = (int32_t)i;
    }
    _ce_order_sort(items, tmp, n);
    for (i = 0; i < n; i++)
        out[i] = items[i].idx;
    free(items);
    free(tmp);
}

/* ── T8: 音乐→效果桥接纯数学（对齐 src/audio/audio_param_modulator.py
 *    ModRule.map_value :126-146 / AudioParamModulator._smooth :393-413;
 *    src/audio/music_to_effect_bridge.py MusicToEffectBridge._apply_common_range
 *    :601-606）────────────────────────────────────────────────────────────
 * 四个独立标量内核（全程 double, 与 Python float 语义逐位一致）:
 *   ce_music_map          — ModRule.map_value: 归一化 → clamp[0,1] → 曲线 →
 *                           输出域映射。curve_idx: 0=linear 1=exp(t²) 2=log(√)
 *                           3=step(t≥0.5→1 否则 0)。
 *   ce_music_smooth_ema   — _smooth ema 路径: cur + (1-alpha)*(target-cur);
 *                           首帧 cur=target（Python state.get 缺省）。状态由
 *                           调用方持有。
 *   ce_music_smooth_fir5  — _smooth fir5 路径: 加权核 (1,2,3,2,1)/9; hist[5]
 *                           为调用方持有的滚动状态（初值=首个 target, 语义
 *                           同 deque([target]*5, maxlen=5).append(target)）。
 *   ce_music_common_range — _apply_common_range 标量: clamp[0,255] → 线性重
 *                           映射 [lo,hi]; is_int 时 *out_int = rint(mapped)
 *                           （银行家舍入, 同 Python round）。
 * 位级纪律（fuzz 实证 2026-08-03）:
 *   1. clamp 复刻 CPython 内建 min/max 语义（实测）: min(a,b)= b if (b<a)
 *      else a; max(a,b)= b if (b>a) else a → t=NaN 时 min(1.0,NaN)=1.0（clamp
 *      后无 NaN 进入曲线; 与 numpy clip 的 NaN 保留语义不同, 严格按 Python）。
 *   2. 1e-9 阈值用 fabs（Python abs(-0.0)=+0.0）; in_range 恰为 ±1e-9 时
 *      abs 比较恒 False → 走除法分支（实测一致）。
 *   3. 曲线 exp=t*t（可溢出为 inf, 同 Python）; sqrt/rint 为 IEEE 正确舍入。
 *   4. lo/hi 为 Python int: span=hi-lo 精确; 浮点提升时机与 Python int+float
 *      混合运算一致（NEP50 前移无关——纯 Python float）。
 *   5. int 路径输入恒有限（Python int 无法携带 NaN/inf）→ rint 结果无溢出。
 */

CE_API double ce_music_map(double raw, double in_min, double in_max,
                           double out_min, double out_max, int curve_idx)
{
    double in_range = in_max - in_min;
    double t;
    if (fabs(in_range) < 1e-9) {
        t = 0.5;
    } else {
        t = (raw - in_min) / in_range;
    }
    /* CPython min(1.0, t): b if (b < a) else a → t=NaN → 1.0 */
    t = (t < 1.0) ? t : 1.0;
    /* CPython max(0.0, t): b if (b > a) else a → 恒非 NaN 后比较 */
    t = (t > 0.0) ? t : 0.0;
    switch (curve_idx) {
        case 1:  /* exp */
            t = t * t;
            break;
        case 2:  /* log */
            t = sqrt(t);
            break;
        case 3:  /* step */
            t = (t >= 0.5) ? 1.0 : 0.0;
            break;
        default: /* linear */
            break;
    }
    return out_min + t * (out_max - out_min);
}

CE_API double ce_music_smooth_ema(double cur, double target, double alpha)
{
    /* _smooth ema: cur + (1.0 - alpha) * (target - cur); 首帧 cur=target */
    return cur + (1.0 - alpha) * (target - cur);
}

CE_API double ce_music_smooth_fir5(double* hist, double target)
{
    /* deque(maxlen=5) append: 丢最旧(hist[0]), 新值入尾(hist[4]) */
    hist[0] = hist[1];
    hist[1] = hist[2];
    hist[2] = hist[3];
    hist[3] = hist[4];
    hist[4] = target;
    /* (h0 + 2*h1 + 3*h2 + 2*h3 + h4) / 9.0 左结合, 同 Python */
    return (hist[0] + 2.0 * hist[1] + 3.0 * hist[2]
            + 2.0 * hist[3] + hist[4]) / 9.0;
}

CE_API double ce_music_common_range(double v, int lo, int hi, int is_int,
                                    long long* out_int)
{
    double span = (double)hi - (double)lo;      /* Python int 减（精确） */
    double vc = (v < 0.0) ? 0.0 : ((v > 255.0) ? 255.0 : v);  /* NaN 透传 */
    double mapped = (double)lo + (vc / 255.0) * span;
    if (is_int && out_int != NULL)
        *out_int = (long long)rint(mapped);     /* Python round 半偶 */
    return mapped;
}

/* ── T8: auto_arrange 自动编曲纯数学内核 ────────────────────────────────
 * 对齐 src/engine/auto_arrange/（只读参考, 不碰 src）。
 *
 * 家族 A: PhraseDynamics 乐句力度弧线（phrase_dynamics.py）——
 *   ce_arrange_dyn_params          = from_energy(:67) 参数映射
 *   ce_arrange_dyn_level           = level_at_time(:142) + _interp_shape(:263)
 *   ce_arrange_dyn_speed           = effective_speed(:162)（终止式渐慢）
 *   ce_arrange_dyn_level_full      = from_energy+level_at_time 合成
 *   ce_arrange_dyn_speed_full      = from_energy+effective_speed 合成
 *   ce_arrange_dyn_section_weight  = TensionArcBuilder.section_weight(:241)
 * 家族 B: CaseLibrary 案例相似度（case_retrieval.py nearest_weighted :55）——
 *   ce_arrange_case_sim            = 单案例加权欧氏距离+软阈相似度
 *
 * 形状编码: 0=linear 1=ease_in 2=ease_out 3=s_curve, 其他 → linear（Python else）。
 * 参数映射（逐字对齐 Python 字面量）:
 *   from_energy 分支: <0.25 / <0.40 / <0.60 / <0.75 / else（严格 <, NaN/±inf
 *     落入 else, 同 Python 链式比较）; is_first: attack *= 0.3 + ease_in;
 *     is_last: release=0.05 + ease_out + cadence=1 + rit=0.60。
 *   level_at_time: t = max(0,min(1,t_rel)); t<=pk → 起音段 else 释放段;
 *     pk 恒 >0（from_energy 产出 {0.50,0.55,0.62,0.68,0.72}）, pk==±0.0 时
 *     Python t/pk 抛 ZeroDivisionError——不在内核域内。
 *   _interp_shape: t**2 必须直接调 ucrtbase!pow（CPython float_pow(int 指数)
 *     逐位等于 ucrtbase pow, 非 x*x; 本 DLL 默认 /MT 静态 CRT 的 pow(x,2.0)
 *     内部实为 x*x——266/50万差 1 ulp, 故用 _ce_pow_ucrt 走动态 ucrtbase,
 *     s_curve 的 t2 = t*t*(3-2t) 纯乘法左结合同 Python 字面量。
 *   section_weight: dist = abs(section_idx-peak)/max(n-1,1);
 *     weight = exp(-(dist**2)/(2*sigma**2)), sigma=0.35; 回 max(0.08, weight)。
 *   case_sim: d += w*dv*dv（左结合 (w*dv)*dv）; dist=sqrt(max(0,d));
 *     weight = 1/(1+(dist/0.35)**2); 回 clamp01。
 * 位级纪律: 全程 double; max/min 用 _ce_max_py/_ce_min_py 复刻 CPython 内建
 *   min/max 逐元素 `if (b > a) b` 语义（NaN 比较恒假 → 首参数胜出）;
 *   -0.0 与 +0.0 严格同 CPython（max(0.0,-0.0)=+0.0）。 */
static double _ce_min_py(double a, double b) { return (b < a) ? b : a; }
static double _ce_max_py(double a, double b) { return (b > a) ? b : a; }

/* Python max(0.0, min(1.0, x)): min(1.0,x) 先跑——NaN → 1.0（NaN<1.0 恒假） */
static double _ce_clamp01_py(double x)
{
    return _ce_max_py(0.0, _ce_min_py(1.0, x));
}

/* Python `x ** 2`（int 指数）实为 CRT pow(x, 2.0) 调用（float_pow 特殊路径,
 * 实测 50 万样本与 math.pow 逐位一致）。本 DLL 默认 /MT 静态 CRT（libcmt）,
 * 其 pow(x, 2.0) 内部实为 x*x（探针实测含函数指针调用路径, 266/50 万与
 * ucrtbase 差 1 ulp）; CPython 链接 ucrtbase.dll——必须直接调 ucrtbase 的
 * pow 才能逐位对齐。首次调用 GetProcAddress 解析, 之后走静态缓存。 */
static double _ce_pow_ucrt(double x, double y)
{
    static double (__cdecl *fn)(double, double) = NULL;
    if (fn == NULL) {
        HMODULE h = GetModuleHandleA("ucrtbase.dll");
        if (h != NULL)
            fn = (double (__cdecl *)(double, double))
                 (void*)GetProcAddress(h, "pow");
        if (fn == NULL)
            fn = pow;   /* 兜底: 解析失败退回静态 CRT（非常规路径） */
    }
    return fn(x, y);
}

static double _ce_pow2(double x)
{
    return _ce_pow_ucrt(x, 2.0);
}

/* _interp_shape(:263): 形状插值（t 先 clamp01, 同 Python 双重钳制） */
static double _ce_interp_shape(double a, double b, double t, int shape)
{
    t = _ce_clamp01_py(t);
    switch (shape) {
    case 1:  /* ease_in:  a + (b-a) * (t ** 2) */
        return a + (b - a) * _ce_pow2(t);
    case 2:  /* ease_out: a + (b-a) * (1 - (1-t) ** 2) */
        return a + (b - a) * (1.0 - _ce_pow2(1.0 - t));
    case 3:  /* s_curve:  t2 = t*t*(3-2t), 纯乘法左结合 */
        {
            double t2 = t * t * (3.0 - 2.0 * t);
            return a + (b - a) * t2;
        }
    default: /* linear 及未知形状 else */
        return a + (b - a) * t;
    }
}

/* from_energy(:67) 参数映射（duration_s 在 Python 仅存储不参与计算, 略） */
static void _ce_dyn_params(double energy, int first, int last,
                           double* attack, double* peak, double* release,
                           double* pk, int* a_shape, int* d_shape,
                           int* cadence, double* rit)
{
    if (energy < 0.25) {
        *attack = 0.25; *peak = 0.30 + energy * 0.15; *release = 0.20;
        *pk = 0.50; *a_shape = 1; *d_shape = 2;      /* ease_in / ease_out */
    } else if (energy < 0.40) {
        *attack = 0.30; *peak = 0.45 + energy * 0.30; *release = 0.25;
        *pk = 0.55; *a_shape = 1; *d_shape = 2;
    } else if (energy < 0.60) {
        *attack = 0.35; *peak = 0.65 + energy * 0.25; *release = 0.30;
        *pk = 0.62; *a_shape = 0; *d_shape = 2;      /* linear / ease_out */
    } else if (energy < 0.75) {
        *attack = 0.40; *peak = 0.80 + energy * 0.15; *release = 0.35;
        *pk = 0.68; *a_shape = 3; *d_shape = 2;      /* s_curve / ease_out */
    } else {
        *attack = 0.50; *peak = 0.90 + (energy - 0.75) * 0.40; *release = 0.40;
        *pk = 0.72; *a_shape = 3; *d_shape = 0;      /* s_curve / linear */
    }
    if (first) { *attack *= 0.3; *a_shape = 1; }
    if (last) { *release = 0.05; *d_shape = 2; *cadence = 1; *rit = 0.60; }
    else { *cadence = 0; *rit = 1.0; }
}

/* level_at_time(:142): t<=pk → 起音段, 否则释放段; pk==±0.0 除零域外 */
static double _ce_dyn_level(double attack, double peak, double release,
                            double pk, int a_shape, int d_shape, double t_rel)
{
    double t = _ce_clamp01_py(t_rel);
    if (t <= pk) {
        double progress = t / pk;
        return _ce_interp_shape(attack, peak, progress, a_shape);
    }
    {
        double progress = (t - pk) / (1.0 - pk);
        return _ce_interp_shape(peak, release, progress, d_shape);
    }
}

/* effective_speed(:162): t_rel 原值比较（不钳制）, speed = base * rit */
static double _ce_dyn_speed(int has_cadence, double rit, double t_rel,
                            double base_speed)
{
    double speed = base_speed;
    if (has_cadence && t_rel > 0.85) {
        double cadence_progress = (t_rel - 0.85) / 0.15;
        double r = 1.0 - (1.0 - rit) * cadence_progress;
        speed *= r;
    }
    return speed;
}

CE_API void ce_arrange_dyn_params(double section_energy, int is_first,
                                  int is_last, double* out8)
{
    /* out8: [0]=attack [1]=peak [2]=release [3]=peak_position
     *       [4]=attack_shape [5]=decay_shape [6]=has_cadence [7]=ritardando */
    double attack, peak, release, pk;
    int a_shape, d_shape, cadence;
    double rit;
    _ce_dyn_params(section_energy, is_first, is_last, &attack, &peak,
                   &release, &pk, &a_shape, &d_shape, &cadence, &rit);
    out8[0] = attack;
    out8[1] = peak;
    out8[2] = release;
    out8[3] = pk;
    out8[4] = (double)a_shape;
    out8[5] = (double)d_shape;
    out8[6] = (double)cadence;
    out8[7] = rit;
}

CE_API double ce_arrange_dyn_level(double attack_level, double peak_level,
                                   double release_level, double peak_position,
                                   int attack_shape, int decay_shape,
                                   double t_rel)
{
    return _ce_dyn_level(attack_level, peak_level, release_level,
                         peak_position, attack_shape, decay_shape, t_rel);
}

CE_API double ce_arrange_dyn_speed(int has_cadence, double ritardando_factor,
                                   double t_rel, double base_speed)
{
    return _ce_dyn_speed(has_cadence, ritardando_factor, t_rel, base_speed);
}

CE_API double ce_arrange_dyn_level_full(double section_energy, int is_first,
                                        int is_last, double t_rel)
{
    double attack, peak, release, pk;
    int a_shape, d_shape, cadence;
    double rit;
    _ce_dyn_params(section_energy, is_first, is_last, &attack, &peak,
                   &release, &pk, &a_shape, &d_shape, &cadence, &rit);
    return _ce_dyn_level(attack, peak, release, pk, a_shape, d_shape, t_rel);
}

CE_API double ce_arrange_dyn_speed_full(double section_energy, int is_first,
                                        int is_last, double t_rel,
                                        double base_speed)
{
    double attack, peak, release, pk;
    int a_shape, d_shape, cadence;
    double rit;
    _ce_dyn_params(section_energy, is_first, is_last, &attack, &peak,
                   &release, &pk, &a_shape, &d_shape, &cadence, &rit);
    return _ce_dyn_speed(cadence, rit, t_rel, base_speed);
}

CE_API double ce_arrange_dyn_section_weight(int peak_idx, int n,
                                            int section_idx)
{
    if (n == 0)
        return 0.5;
    {
        int denom = (n - 1) > 1 ? (n - 1) : 1;      /* max(n-1, 1) */
        double dist = (double)abs(section_idx - peak_idx) / (double)denom;
        double sigma2 = 2.0 * _ce_pow2(0.35);       /* 2 * sigma**2 */
        double weight = exp(-(_ce_pow2(dist)) / sigma2);
        return _ce_max_py(0.08, weight);
    }
}

CE_API void ce_arrange_case_sim(const double* feat, const double* cfeat,
                                const double* w, int nf,
                                double* out_dist, double* out_weight)
{
    double d = 0.0;
    for (int k = 0; k < nf; k++) {
        double dv = feat[k] - cfeat[k];
        d += w[k] * dv * dv;                        /* (w*dv)*dv 左结合 */
    }
    {
        double dist = sqrt(_ce_max_py(0.0, d));
        double weight = 1.0 / (1.0 + _ce_pow2(dist / 0.35));
        *out_dist = dist;
        *out_weight = _ce_clamp01_py(weight);
    }
}
/* ── T8: XY 参考表构建 — 纯数值映射段（对齐 src/engine/xy_reference.py
 * build_xy_reference_map :117 的地址/角度/符号算术, 不碰对象模型）────────
 * C 契约: 行式输入 n 个 fixture → n 行输出; out_valid[i]=1 才有效。
 *   addr = start_channel - 1 + channel_off（int64 算术防溢出, 同 Python 无限 int）
 *   跳过: mode 非 sync / 缺 motor_x 或 motor_y / x/y 地址越界 [0,512)
 *   fine 地址: fine_off>=0 且 addr∈[0,512) 才输出, 否则 -1
 *   角度: 四分支 if/elif 链（顺序与 Python 逐字一致）+ 全局修正; f64 逐位
 *   y_axis_sign: 原始值 < 0.0 → -1.0, 否则 +1.0（-0.0/NaN 恒 +1.0, 同 IEEE）
 * Python 边界（不进 C, 见 render_lab/bench/ce_fuzz_xyref.py）: mode 字符串归一化
 *   （normalize_reference_mode 别名集合）/ 通道类型别名表（channel_type_key
 *   26 别名首现胜出）/ _safe_int _safe_float 强转 / cfg 键存在性解析。 */
CE_API void ce_xy_ref_build(
    const int32_t* uni_in, const int64_t* start_ch_in,
    const int32_t* mode_code, const int32_t* has_x, const int32_t* has_y,
    const int32_t* x_off, const int32_t* y_off,
    const int32_t* xf_off, const int32_t* yf_off,
    const int32_t* ang_flags,
    const double* v_main, const double* v_base,
    const double* v_ang, const double* v_off,
    const double* y_sign_raw,
    double global_adjust_deg,
    int n,
    int32_t* out_valid, int32_t* out_x_addr, int32_t* out_y_addr,
    int32_t* out_xf_addr, int32_t* out_yf_addr,
    double* out_angle_deg, double* out_y_sign)
{
    int i;
    for (i = 0; i < n; i++) {
        int64_t start0 = start_ch_in[i] - 1;
        int64_t x_addr = start0 + x_off[i];
        int64_t y_addr = start0 + y_off[i];
        int ok = (mode_code[i] == 1 && has_x[i] && has_y[i]
                  && x_addr >= 0 && x_addr < 512
                  && y_addr >= 0 && y_addr < 512);
        int32_t xf = -1, yf = -1;
        double ang = 0.0, ys = 1.0;
        if (ok) {
            int64_t a;
            if (xf_off[i] >= 0) {
                a = start0 + xf_off[i];
                if (a >= 0 && a < 512)
                    xf = (int32_t)a;
            }
            if (yf_off[i] >= 0) {
                a = start0 + yf_off[i];
                if (a >= 0 && a < 512)
                    yf = (int32_t)a;
            }
            /* 角度分支: 与 Python if/elif 链同序（has_phys→has_base→has_ang→has_off）*/
            if (ang_flags[i] & 0x01)
                ang = v_main[i] + v_off[i] + global_adjust_deg;
            else if (ang_flags[i] & 0x02)
                ang = v_base[i] + v_off[i] + global_adjust_deg;
            else if (ang_flags[i] & 0x04)
                ang = v_ang[i];
            else if (ang_flags[i] & 0x08)
                ang = v_off[i] + global_adjust_deg;
            else
                ang = global_adjust_deg;   /* Python else: 无角度键 → 仅全局角 */
            ys = (y_sign_raw[i] < 0.0) ? -1.0 : 1.0;
        }
        out_valid[i] = ok;
        out_x_addr[i] = (int32_t)x_addr;
        out_y_addr[i] = (int32_t)y_addr;
        out_xf_addr[i] = xf;
        out_yf_addr[i] = yf;
        out_angle_deg[i] = ang;
        out_y_sign[i] = ys;
    }
}
/* ── T8: 虚拟单元 merge-max 合并（对齐 src/ui/effect_engine.py _merge_values :853）──
 *
 * 背景: compute_at(:780) 的两条"虚拟单元"路径都把多单元通道值合并回真实设备:
 *   1. 重复 orig_i 虚拟序列路径(:936-944) — unit 按 seq_fixtures 顺序折叠
 *   2. wings/block 广播路径(:1044-1050) — 同一虚拟单元广播到多个设备
 *      （集成侧展开为重复行: 每 (virtual_idx, orig_i) 一行, 行序保持）
 * merge_channels{dimmer,R,G,B,W,valve,pump,level} 取 max, 其余通道 last-write-wins。
 * 本内核只做纯数值合并（通道名→整数 id 映射由调用方约定, merge_kind 携带类别）;
 * 非数值（字符串等）超出内核范围, 由调用方按 except 语义处理。
 *
 * 输入契约（行优先, 与 Python 折叠完全同序）:
 *   unit_orig  [n_units]           每行目标设备下标（0..n_dev-1; 越界行整体跳过）
 *   unit_chan  [n_units*n_chan]    每行每槽位通道 id; <0 = 该槽位跳过
 *   unit_val   [n_units*n_chan]    每行每槽位值（f64）
 *   merge_kind [max_chan]          每通道 0=覆盖(最后写胜出) 1=merge-max
 *   n_units / n_chan / n_dev / max_chan   维度（n_units=0 → 仅拷贝 prev）
 *   prev_val / prev_has [n_dev*max_chan]  设备既有帧值（:1050 的 ef.values[orig_i]
 *                                         前置状态; 均 NULL = 无前置, 对齐 :940 首单元）
 * 输出:
 *   out_val / out_has [n_dev*max_chan]   合并结果; 未写通道 out_has=0、值=NaN 哨兵
 *
 * Python 语义逐条对齐（_merge_values :853, 数值子集）:
 *   1. merge-max 通道: merged = max(int(prev or 0), int(val or 0))
 *      - 假值(0/0.0/-0.0/False) → 0（`or 0` 语义; -0.0!=0.0 为假 → 0）
 *      - int() 对浮点向零截断（与 C 有符号 cast 同向）
 *      - NaN/±inf 使 int() 抛异常 → except 路径: merged = val（原始值覆盖）
 *      - prev 缺失（out_has=0）视作数值 0 → 正常 max 路径
 *   2. 覆盖通道: merged = val（原始值, 行序后写胜出）
 *   3. 通道独立; 合并行按序处理 → 覆盖通道的胜负序与 Python 折叠一致
 */
CE_API void ce_merge_max(const int64_t* unit_orig, const int64_t* unit_chan,
                         const double* unit_val, const uint8_t* merge_kind,
                         int n_units, int n_chan, int n_dev, int max_chan,
                         const double* prev_val, const uint8_t* prev_has,
                         double* out_val, uint8_t* out_has)
{
    for (int d = 0; d < n_dev; d++) {
        const size_t base = (size_t)d * max_chan;
        for (int c = 0; c < max_chan; c++) {
            const int has = (prev_has != NULL) && prev_has[base + c];
            out_has[base + c] = (uint8_t)has;
            out_val[base + c] = has ? prev_val[base + c] : NAN;
        }
    }
    for (int u = 0; u < n_units; u++) {
        const int64_t orig = unit_orig[u];
        if (orig < 0 || orig >= n_dev)
            continue;               /* 越界设备下标: 整行丢弃（对齐 len 判空） */
        const size_t db = (size_t)orig * max_chan;
        for (int k = 0; k < n_chan; k++) {
            const int64_t ch = unit_chan[(size_t)u * n_chan + k];
            if (ch < 0 || ch >= max_chan)
                continue;
            const double v = unit_val[(size_t)u * n_chan + k];
            if (!merge_kind[ch]) {
                out_val[db + ch] = v;       /* 覆盖通道: 原始值, 后写胜出 */
                out_has[db + ch] = 1;
                continue;
            }
            /* merge-max 通道: prev 或 val 不可 int() → except 原始覆盖 */
            if ((out_has[db + ch] && !isfinite(out_val[db + ch]))
                || !isfinite(v)) {
                out_val[db + ch] = v;
                out_has[db + ch] = 1;
                continue;
            }
            const int64_t p = (out_has[db + ch] && out_val[db + ch] != 0.0)
                              ? (int64_t)out_val[db + ch] : 0;
            const int64_t q = (v != 0.0) ? (int64_t)v : 0;
            out_val[db + ch] = (double)((p > q) ? p : q);
            out_has[db + ch] = 1;
        }
    }
}
/* ═══════════════════════════════════════════════════════════════════
 * T8: DEPENCE 对象构建热路径 — _ln/_at 批量内核（对齐 src/ui/import_depence.py）
 * ───────────────────────────────────────────────────────────────────
 * 背景: 至冬7.0 工程解析 15.5s = ET 建树 7.6s + Python 对象构建 7.6s。
 *   建树已由 ce_xml_scan (T7) 替代（380ms）。本组内核替代对象构建的
 *   Python 字符串热点: _ln（tag.split("}")[-1], 3.3M 次 2.5s）与
 *   _at（el.attrib 查找, 948K 次 1.0s）。
 *
 * 语义（与 Python 逐字节一致）:
 *   _ln(tag)  = tag.split("}")[-1] → 最后一个 '}' 之后的子串; 无 '}'
 *               时返回整个字符串。UTF-8 字节级等价（'}' 是 ASCII，
 *               按码点 split 与按字节切分等价）。
 *   _at(el, key) = 两段查找, 优先级固定:
 *     1) el.attrib.get(key) —— 全键精确匹配（key 可带命名空间前缀）
 *     2) 否则按 attrib 插入序扫描, 首个 局部名(最后一个 '}' 之后) == key
 *     3) 均未命中 → None
 *
 * 契约:
 *   void ce_local_name_batch(const char* blob, const int* lens, int n,
 *                            int32_t* out_beg)
 *     - blob = 全部标签 UTF-8 字节按序连接; lens[i] = 各标签字节长
 *     - out_beg[i] = 最后一个 '}' 之后一字节的下标（无 '}' → 0）;
 *       调用方按 out_beg[i] 切 blob 即得 _ln 结果
 *
 *   void ce_attr_lookup_batch(
 *       const char* kblob, const int* klen,
 *       const char* vblob, const int* vlen,
 *       const int* attr_beg, int n_elem,
 *       const char* const* want_keys, const int* want_lens, int n_keys,
 *       int32_t* out_idx, int64_t* out_start, int32_t* out_len)
 *     - 属性按 元素主序 全局编号: 元素 e 的属性区间 [attr_beg[e],
 *       attr_beg[e+1]); attr_beg[n_elem] = 总属性数（klen/vlen 长度）
 *     - kblob/vblob = 全部键/值 UTF-8 字节连接; klen/vlen = 各属性长
 *     - want_keys[w] = 目标键（_at 的 key 参数, 通常为不带前缀局部名）
 *     - 输出 [n_elem*n_keys], 下标 = e*n_keys + w:
 *         out_idx   = 命中属性的全局下标, -1 = 未命中
 *         out_start/out_len = 该属性值在 vblob 中的字节区间
 *     - 命中判定同 _at: 全键精确匹配优先（唯一, 可提前退出）,
 *       否则插入序首个局部名匹配
 *     - 单遍、零分配; 顶层参数 NULL/负长 或属性长度<0 → 静默返回
 */
CE_API void ce_local_name_batch(const char* blob, const int* lens, int n,
                                int32_t* out_beg)
{
    int64_t pos = 0;
    int i;
    if (blob == NULL || lens == NULL || out_beg == NULL || n < 0)
        return;
    for (i = 0; i < n; i++) {
        int l = lens[i], beg = 0, j;
        if (l < 0)
            return;                     /* 非法长度: 立即停止 */
        for (j = 0; j < l; j++) {
            if (blob[pos + j] == '}')
                beg = j + 1;
        }
        out_beg[i] = beg;
        pos += l;
    }
}

/* 局部名匹配: k 的最后一个 '}' 之后逐字节 == want
 * （复刻 k.split("}")[-1] == key; 无 '}' → 整串比较） */
static int ce_attr_local_eq(const char* k, int klen,
                            const char* want, int wlen)
{
    const char* p = k;
    int i, llen;
    for (i = 0; i < klen; i++) {
        if (k[i] == '}')
            p = k + i + 1;
    }
    llen = klen - (int)(p - k);
    if (llen != wlen)
        return 0;
    return memcmp(p, want, (size_t)wlen) == 0;
}

CE_API void ce_attr_lookup_batch(
    const char* kblob, const int* klen,
    const char* vblob, const int* vlen,
    const int* attr_beg, int n_elem,
    const char* const* want_keys, const int* want_lens, int n_keys,
    int32_t* out_idx, int64_t* out_start, int32_t* out_len)
{
    int e, w, a;
    int64_t kpos = 0, vpos = 0;
    if (kblob == NULL || klen == NULL || vblob == NULL || vlen == NULL
        || attr_beg == NULL || want_keys == NULL || want_lens == NULL
        || out_idx == NULL || out_start == NULL || out_len == NULL)
        return;
    if (n_elem <= 0 || n_keys <= 0 || attr_beg[0] != 0)
        return;
    for (e = 0; e < n_elem; e++) {
        int a0 = attr_beg[e], a1 = attr_beg[e + 1];
        if (a1 < a0)
            return;                     /* 非法区间: 立即停止 */
        for (w = 0; w < n_keys; w++) {
            const char* want = want_keys[w];
            int wlen = want_lens[w];
            int64_t kk = kpos, vv = vpos;
            int found = -1, first_local = -1;
            int64_t found_vs = 0, first_vs = 0;
            int32_t found_vl = 0, first_vl = 0;
            if (want == NULL || wlen < 0) {
                out_idx[e * n_keys + w] = -1;
                out_start[e * n_keys + w] = 0;
                out_len[e * n_keys + w] = 0;
                continue;
            }
            for (a = a0; a < a1; a++) {
                const char* k = kblob + kk;
                const char* v = vblob + vv;
                if (klen[a] < 0 || vlen[a] < 0)
                    return;             /* 非法属性长度: 立即停止 */
                if (klen[a] == wlen
                    && memcmp(k, want, (size_t)wlen) == 0) {
                    /* 全键精确匹配: _at 第一步 .get(key), 唯一且优先 */
                    found = a;
                    found_vs = vv;
                    found_vl = vlen[a];
                    break;
                }
                if (first_local < 0
                    && ce_attr_local_eq(k, klen[a], want, wlen)) {
                    first_local = a;
                    first_vs = vv;
                    first_vl = vlen[a];
                }
                kk += klen[a];
                vv += vlen[a];
            }
            if (found >= 0) {
                out_idx[e * n_keys + w] = found;
                out_start[e * n_keys + w] = found_vs;
                out_len[e * n_keys + w] = found_vl;
            } else if (first_local >= 0) {
                out_idx[e * n_keys + w] = first_local;
                out_start[e * n_keys + w] = first_vs;
                out_len[e * n_keys + w] = first_vl;
            } else {
                out_idx[e * n_keys + w] = -1;
                out_start[e * n_keys + w] = 0;
                out_len[e * n_keys + w] = 0;
            }
        }
        /* 推进到下一元素: kpos/vpos 单调累计（vlen 已在扫描内验证 ≥0） */
        for (a = a0; a < a1; a++) {
            kpos += klen[a];
            vpos += vlen[a];
        }
    }
}

/* ── T4续: v29 有状态空间编组矩阵效果（对齐 effect_presets_v29.py）────────
 * 线上真值 = _render_groups :276 全量复刻（全局 _STATES + random.Random(42)
 * 跨帧状态）。状态所有权在 C（ce_mxs_t 持久句柄），Python 每帧只传 t/参数。
 *
 * 状态结构（对齐 _get_state :26）:
 *   last_beat  上次触发节拍（int 值存 double; 初值 -1.0）
 *   rng        MT19937 init_by_array(42) —— 逐位复刻 CPython 3.13
 *   groups[]   活跃组（cx/cy/start_t/anim_idx/单元列表; 顺序=生成序）
 *
 * 参数映射（对齐 _render_groups :288-292 与 _fn :389）:
 *   max_groups  = max(1, int)         （引擎参数域 1..20）
 *   shape_size  = max(1, int)         （引擎参数域 1..5）
 *   shape_idx   SHAPES 字典序 0..14（越界 → 2 = _shape_diamond 兜底）
 *   anim_idx    由 beat_idx % 8 循环（ANIMATIONS 字典序 0..7）——
 *               preset 的 anim_name 参数在 Python 中从不生效（死兜底），
 *               组动画始终 = anim_variants[beat_idx % 8]，此处如实复刻。
 *
 * 节拍触发（:295-312）: beat_phase = (t*bpm/60)%1.0; beat_idx = int(...);
 *   条件 beat_idx != last_beat && beat_phase < 0.3 → 先 spawn 后 prune。
 * randint 复刻: _randbelow_with_getrandbits（顶部 k 位 + 位掩码拒绝）。
 * 渲染（:318-345）: 全量 lo → 逐组 max 叠加; 单元顺序语义无关（max 可交换），
 *   形状只需集合一致; 集合去重仅 cross/star/spiral_arm/arrow_right/v_shape
 *   五个 list(set(...)) 形状，其余形状 Python 侧本就无重复。
 * 音频特征: v29 在线注册的 60 个预设不依赖外部音频输入（音频版全注释），
 *   trigger_bpm 为纯标量参数 —— 内核为纯函数 + 状态，无外部输入。
 */

#define CE_MXS_ANIM_N 8

typedef struct {
    double start_t;
    int cx, cy;
    int anim_idx;          /* 0..7 ANIMATIONS 字典序 */
    int n_cells;
    int16_t* dc;           /* 相对列偏移（动态分配, 容量 (2*size+1)^2） */
    int16_t* dr;           /* 相对行偏移 */
} ce_mxs_group_t;

typedef struct {
    int cols, rows;        /* 状态键维度（对齐 _get_state 键 {id}_{cols}_{rows}） */
    double last_beat;
    double max_t;          /* R181 倒带复位基准（Python _get_state max_t 同语义） */
    ce_mt_state_t rng;     /* seed 42 逐位同 random.Random(42) */
    ce_mxs_group_t* groups;
    int n_groups, group_cap;
} ce_mxs_t;

/* Python round()（银行家舍入）→ ce_round_half_even（:2304 已有） */

/* 单元追加 + 集合去重（list(set(...)) 语义: 首现保留） */
static int _ce_mxs_push(int16_t* dc, int16_t* dr, int n, int c, int r)
{
    for (int i = 0; i < n; i++)
        if (dc[i] == (int16_t)c && dr[i] == (int16_t)r) return n;
    dc[n] = (int16_t)c;
    dr[n] = (int16_t)r;
    return n + 1;
}

/* ── 15 种形状（对齐 _shape_* 集合语义; size ≥ 1）──────────────────── */

static int _ce_mxs_tri_up(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = 0; r <= s; r++)
        for (int c = -r; c <= r; c++) {
            dc[n] = (int16_t)c; dr[n] = (int16_t)(s - r); n++;
        }
    return n;
}

static int _ce_mxs_tri_down(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = 0; r <= s; r++)
        for (int c = -r; c <= r; c++) {
            dc[n] = (int16_t)c; dr[n] = (int16_t)(r - s); n++;
        }
    return n;
}

static int _ce_mxs_diamond(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = -s; r <= s; r++)
        for (int c = -s; c <= s; c++)
            if (abs(r) + abs(c) <= s) {
                dc[n] = (int16_t)c; dr[n] = (int16_t)r; n++;
            }
    return n;
}

static int _ce_mxs_square(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = -s; r <= s; r++)
        for (int c = -s; c <= s; c++) {
            dc[n] = (int16_t)c; dr[n] = (int16_t)r; n++;
        }
    return n;
}

static int _ce_mxs_cross(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = -s; i <= s; i++) {
        n = _ce_mxs_push(dc, dr, n, i, 0);
        n = _ce_mxs_push(dc, dr, n, 0, i);
    }
    return n;
}

static int _ce_mxs_star(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = -s; i <= s; i++) {
        n = _ce_mxs_push(dc, dr, n, i, 0);
        n = _ce_mxs_push(dc, dr, n, 0, i);
    }
    for (int i = -s; i <= s; i++)
        if (i != 0) {
            n = _ce_mxs_push(dc, dr, n, i, i);
            n = _ce_mxs_push(dc, dr, n, i, -i);
        }
    return n;
}

static int _ce_mxs_ring(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = -s; r <= s; r++)
        for (int c = -s; c <= s; c++)
            if (abs(r) + abs(c) == s) {
                dc[n] = (int16_t)c; dr[n] = (int16_t)r; n++;
            }
    return n;
}

static int _ce_mxs_l_shape(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = 0; i <= s; i++) {
        dc[n] = 0; dr[n] = (int16_t)i; n++;
    }
    for (int i = 1; i <= s; i++) {
        dc[n] = (int16_t)i; dr[n] = 0; n++;
    }
    return n;
}

static int _ce_mxs_spiral_arm(int s, int16_t* dc, int16_t* dr)
{
    int n = _ce_mxs_push(dc, dr, 0, 0, 0);
    for (int i = 1; i <= s; i++)
        for (int arm = 0; arm < 3; arm++) {
            /* Python: angle = arm*2*math.pi/3; round(i*sin/cos) 同序 */
            double angle = (double)(arm * 2) * M_PI / 3.0;
            int64_t rr = ce_round_half_even((double)i * sin(angle));
            int64_t cc = ce_round_half_even((double)i * cos(angle));
            n = _ce_mxs_push(dc, dr, n, (int)cc, (int)rr);
        }
    return n;
}

static int _ce_mxs_hexagon(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int r = -s; r <= s; r++)
        for (int c = -s; c <= s; c++)
            if (abs(r) <= s && abs(c) <= s && abs(r + c) <= s) {
                dc[n] = (int16_t)c; dr[n] = (int16_t)r; n++;
            }
    return n;
}

static int _ce_mxs_arrow_right(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = -s; i <= s; i++)
        n = _ce_mxs_push(dc, dr, n, 0, i);
    for (int i = 1; i <= s; i++) {
        n = _ce_mxs_push(dc, dr, n, i, i);
        n = _ce_mxs_push(dc, dr, n, i, -i);
    }
    return n;
}

static int _ce_mxs_v_shape(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = 0; i <= s; i++) {
        n = _ce_mxs_push(dc, dr, n, -i, i);
        n = _ce_mxs_push(dc, dr, n, i, i);
    }
    return n;
}

static int _ce_mxs_zigzag(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = 0; i < s; i++) {
        dc[n] = (int16_t)(i * 2); dr[n] = 0; n++;
        dc[n] = (int16_t)(i * 2 + 1); dr[n] = 1; n++;
    }
    return n;
}

static int _ce_mxs_triangle_group(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    int cells0[6][2] = {{0, 0}, {-1, 1}, {1, 1}, {0, 2}, {-2, 2}, {2, 2}};
    for (int i = 0; i < 3; i++) {
        dc[n] = (int16_t)cells0[i][0]; dr[n] = (int16_t)cells0[i][1]; n++;
    }
    if (s >= 2)
        for (int i = 3; i < 6; i++) {
            dc[n] = (int16_t)cells0[i][0]; dr[n] = (int16_t)cells0[i][1]; n++;
        }
    return n;
}

static int _ce_mxs_wave_line(int s, int16_t* dc, int16_t* dr)
{
    int n = 0;
    for (int i = 0; i <= s * 2; i++) {
        int64_t rr = ce_round_half_even(sin((double)i * M_PI / 2.0));
        dc[n] = (int16_t)(i - s); dr[n] = (int16_t)rr; n++;
    }
    return n;
}

/* 形状分派（SHAPES 字典序 0..14; 越界 → diamond 兜底同 SHAPES.get） */
static int _ce_mxs_shape(int shape_idx, int size, int16_t* dc, int16_t* dr)
{
    switch (shape_idx) {
    case 0: return _ce_mxs_tri_up(size, dc, dr);
    case 1: return _ce_mxs_tri_down(size, dc, dr);
    case 3: return _ce_mxs_square(size, dc, dr);
    case 4: return _ce_mxs_cross(size, dc, dr);
    case 5: return _ce_mxs_star(size, dc, dr);
    case 6: return _ce_mxs_ring(size, dc, dr);
    case 7: return _ce_mxs_l_shape(size, dc, dr);
    case 8: return _ce_mxs_spiral_arm(size, dc, dr);
    case 9: return _ce_mxs_hexagon(size, dc, dr);
    case 10: return _ce_mxs_arrow_right(size, dc, dr);
    case 11: return _ce_mxs_v_shape(size, dc, dr);
    case 12: return _ce_mxs_zigzag(size, dc, dr);
    case 13: return _ce_mxs_triangle_group(size, dc, dr);
    case 14: return _ce_mxs_wave_line(size, dc, dr);
    default: return _ce_mxs_diamond(size, dc, dr);
    }
}

/* 8 种动画求值（对齐 _anim_*; 全程 double, 同序运算） */
static double _ce_mxs_anim(int anim_idx, double progress, int cell_dist,
                           int max_dist)
{
    switch (anim_idx) {
    case 0: { /* ripple */
        double wave = progress * (double)(max_dist + 1);
        double d = fabs((double)cell_dist - wave);
        return fmax(0.0, 1.0 - d * 1.5);
    }
    case 1: { /* fill_expand */
        double th = progress * (double)(max_dist + 1);
        if ((double)cell_dist <= th) return 1.0;
        if ((double)cell_dist <= th + 1.0) return (th + 1.0) - (double)cell_dist;
        return 0.0;
    }
    case 2: { /* pulse_fade */
        double appear = fmin(1.0, progress * 4.0);
        double fade = (progress > 0.5)
                      ? fmax(0.0, 1.0 - (progress - 0.5) * 2.0) : 1.0;
        return appear * fade;
    }
    case 3: { /* explode */
        double wave = progress * (double)(max_dist + 2);
        double d = fabs((double)cell_dist - wave);
        double b = fmax(0.0, 1.0 - d);
        double f = fmax(0.0, 1.0 - progress * 1.5);
        return b * f + (1.0 - progress) * 0.3;
    }
    case 4: { /* implode */
        double wave = (1.0 - progress) * (double)(max_dist + 1);
        double d = fabs((double)cell_dist - wave);
        return fmax(0.0, 1.0 - d * 1.5);
    }
    case 5: { /* strobe_appear */
        double th = progress * (double)(max_dist + 1);
        if ((double)cell_dist > th) return 0.0;
        return ((int)(progress * 10.0) % 2 == 0) ? 1.0 : 0.5;
    }
    case 6: { /* wave_sweep */
        double wave = progress * (double)(max_dist * 2);
        double d = fabs((double)cell_dist - wave);
        return fmax(0.0, 1.0 - d);
    }
    case 7: /* breathe: math.sin(progress * math.pi) */
        return sin(progress * M_PI);
    default: /* 不可达（anim 0..7 循环）; 对齐 Python 兜底 ripple */
        return fmax(0.0, 1.0 - fabs((double)cell_dist
                   - progress * (double)(max_dist + 1)) * 1.5);
    }
}

/* CPython _randbelow_with_getrandbits: k = n.bit_length();
 * r = getrandbits(k)（k≤32: genrand >> (32-k) 顶部 k 位）; 拒绝 r >= n */
static uint32_t _ce_mt_randbelow(ce_mt_state_t* s, uint32_t n)
{
    int k = 0;
    uint32_t v = n;
    while (v) { k++; v >>= 1; }
    uint32_t r;
    do {
        r = _ce_mt_next(s);
        if (k < 32) r >>= (32 - k);
    } while (r >= n);
    return r;
}

CE_API void* ce_matrix_stateful_create(int cols, int n)
{
    ce_mxs_t* ctx = (ce_mxs_t*)calloc(1, sizeof(ce_mxs_t));
    if (!ctx) return NULL;
    if (cols < 1) cols = 1;
    ctx->cols = cols;
    int rows = (n > 0) ? (n + cols - 1) / cols : 1;   /* max(1, ceil) */
    ctx->rows = rows < 1 ? 1 : rows;
    ctx->last_beat = -1.0;
    ctx->max_t = -1e300;                           /* R181: -inf 等价哨兵 */
    _ce_mt_seed(&ctx->rng, 42);
    ctx->group_cap = 8;
    ctx->groups = (ce_mxs_group_t*)calloc((size_t)ctx->group_cap,
                                          sizeof(ce_mxs_group_t));
    if (!ctx->groups) { free(ctx); return NULL; }
    return ctx;
}

CE_API void ce_matrix_stateful_destroy(void* vctx)
{
    ce_mxs_t* ctx = (ce_mxs_t*)vctx;
    if (!ctx) return;
    for (int i = 0; i < ctx->n_groups; i++) {
        free(ctx->groups[i].dc);
        free(ctx->groups[i].dr);
    }
    free(ctx->groups);
    free(ctx);
}

CE_API void ce_matrix_stateful_apply(void* vctx, double t, int n,
                                     int shape_idx, int max_groups,
                                     double group_duration, double trigger_bpm,
                                     int shape_size, int lo, int hi,
                                     uint8_t* out)
{
    ce_mxs_t* ctx = (ce_mxs_t*)vctx;
    if (!ctx || n <= 0) return;
    if (max_groups < 1) max_groups = 1;
    if (shape_size < 1) shape_size = 1;

    /* ★ R181 倒带复位（同 Python effect_presets_v29._rewind_state）：
     * t 回退（新一轮播放/重烘焙/循环回绕）→ groups/last_beat/rng/max_t 全清重开；
     * 单调推进只抬基准 → 实时逐帧/顺序烘焙与改前逐位相同。 */
    if (t < ctx->max_t) {
        for (int i = 0; i < ctx->n_groups; i++) {
            free(ctx->groups[i].dc);
            free(ctx->groups[i].dr);
            ctx->groups[i].dc = NULL;
            ctx->groups[i].dr = NULL;
        }
        ctx->n_groups = 0;
        ctx->last_beat = -1.0;
        _ce_mt_seed(&ctx->rng, 42);
        ctx->max_t = t;
    } else if (t > ctx->max_t) {
        ctx->max_t = t;
    }

    /* 节拍触发 + spawn（同 :295-312） */
    double x = t * trigger_bpm / 60.0;
    double beat_phase = _ce_mod1(x);
    long long beat_idx = (long long)x;             /* int() 截断 */
    if ((double)beat_idx != ctx->last_beat && beat_phase < 0.3) {
        ctx->last_beat = (double)beat_idx;
        if (ctx->n_groups < max_groups) {
            uint32_t cx = _ce_mt_randbelow(&ctx->rng, (uint32_t)ctx->cols);
            uint32_t cy = _ce_mt_randbelow(&ctx->rng, (uint32_t)ctx->rows);
            int anim_idx = (int)_ce_py_mod_ll(beat_idx, CE_MXS_ANIM_N);
            if (ctx->n_groups == ctx->group_cap) {
                int nc2 = ctx->group_cap * 2;
                ce_mxs_group_t* ng = (ce_mxs_group_t*)realloc(
                    ctx->groups, (size_t)nc2 * sizeof(ce_mxs_group_t));
                if (!ng) return;                   /* OOM 防御（引擎域不可达） */
                ctx->groups = ng;
                ctx->group_cap = nc2;
            }
            ce_mxs_group_t* g = &ctx->groups[ctx->n_groups];
            int cap = (2 * shape_size + 1) * (2 * shape_size + 1);
            g->dc = (int16_t*)malloc((size_t)cap * sizeof(int16_t));
            g->dr = (int16_t*)malloc((size_t)cap * sizeof(int16_t));
            if (!g->dc || !g->dr) {
                free(g->dc); free(g->dr);
                g->dc = NULL; g->dr = NULL;
                return;                            /* OOM 防御（引擎域不可达） */
            }
            g->n_cells = _ce_mxs_shape(shape_idx, shape_size, g->dc, g->dr);
            g->cx = (int)cx;
            g->cy = (int)cy;
            g->anim_idx = anim_idx;
            g->start_t = t;
            ctx->n_groups++;
        }
    }

    /* 剪除过期（同 :314-316, 保序压缩） */
    int w = 0;
    for (int i = 0; i < ctx->n_groups; i++) {
        if (t - ctx->groups[i].start_t < group_duration) {
            if (w != i) {
                ctx->groups[w] = ctx->groups[i];
                ctx->groups[i].dc = NULL;
                ctx->groups[i].dr = NULL;
            }
            w++;
        } else {
            free(ctx->groups[i].dc);
            free(ctx->groups[i].dr);
            ctx->groups[i].dc = NULL;
            ctx->groups[i].dr = NULL;
        }
    }
    ctx->n_groups = w;

    /* 渲染（同 :318-345）: 全量 lo → 逐组 max 叠加 */
    for (int i = 0; i < n; i++) out[i] = (uint8_t)lo;
    for (int gi = 0; gi < ctx->n_groups; gi++) {
        ce_mxs_group_t* g = &ctx->groups[gi];
        double progress = (t - g->start_t) / group_duration;
        if (progress < 0.0) progress = 0.0;
        else if (progress > 1.0) progress = 1.0;
        int max_dist = 1;                          /* default=1 */
        for (int ci = 0; ci < g->n_cells; ci++) {
            int d = abs((int)g->dc[ci]) + abs((int)g->dr[ci]);
            if (d > max_dist) max_dist = d;
        }
        for (int ci = 0; ci < g->n_cells; ci++) {
            int col = g->cx + (int)g->dc[ci];
            int row = g->cy + (int)g->dr[ci];
            if (col < 0 || col >= ctx->cols || row < 0 || row >= ctx->rows)
                continue;
            int seq_i = row * ctx->cols + col;
            if (seq_i >= n) continue;
            int cell_dist = abs((int)g->dc[ci]) + abs((int)g->dr[ci]);
            double brightness = _ce_mxs_anim(g->anim_idx, progress,
                                             cell_dist, max_dist);
            /* _clamp_v29(v) 调用点不传 lo/hi → 默认参数 (0, 255):
             * max(0, min(255, int(v))) —— 钳位到 DMX 全域, 非 [lo,hi]! */
            double _dv = (double)lo + brightness * ((double)hi - (double)lo);

            int iv;

            if (_dv <= 0.0) iv = 0;        /* B2-2 值域先判断 */

            else if (_dv >= 255.0) iv = 255;

            else iv = (int)_dv;
            if (iv > out[seq_i]) out[seq_i] = (uint8_t)iv;
        }
    }
}

/* 状态导出（fuzz 对拍）: rng.mt[624] + 字消耗 index + last_beat + 组数 */
CE_API void ce_matrix_stateful_export(void* vctx, uint32_t* mt, int* index,
                                      double* last_beat, int* n_groups)
{
    ce_mxs_t* ctx = (ce_mxs_t*)vctx;
    if (!ctx) return;
    if (mt) memcpy(mt, ctx->rng.mt, sizeof(ctx->rng.mt));
    if (index) *index = ctx->rng.index;
    if (last_beat) *last_beat = ctx->last_beat;
    if (n_groups) *n_groups = ctx->n_groups;
}

/* 组明细导出（fuzz 对拍）; gi 越界返回 -1 */
CE_API int ce_matrix_stateful_group_get(void* vctx, int gi,
                                        double* start_t, int* cx, int* cy,
                                        int* anim_idx, int* n_cells,
                                        int16_t* dc, int16_t* dr)
{
    ce_mxs_t* ctx = (ce_mxs_t*)vctx;
    if (!ctx || gi < 0 || gi >= ctx->n_groups) return -1;
    ce_mxs_group_t* g = &ctx->groups[gi];
    if (start_t) *start_t = g->start_t;
    if (cx) *cx = g->cx;
    if (cy) *cy = g->cy;
    if (anim_idx) *anim_idx = g->anim_idx;
    if (n_cells) *n_cells = g->n_cells;
    if (dc && g->dc) memcpy(dc, g->dc, (size_t)g->n_cells * sizeof(int16_t));
    if (dr && g->dr) memcpy(dr, g->dr, (size_t)g->n_cells * sizeof(int16_t));
    return 0;
}


/* ── T9: 网络心跳/保活纯数学（对齐 src/network/network_manager.py
 * _mix_and_send_locked :1522-1535 空闲态心跳轮转）────────────────────────
 *
 * Python 原逻辑（逐行）:
 *   到期判定  :  now - last_heartbeat_send >= heartbeat_interval      (:1522)
 *   周期计划  :  interval = max(0.001, float(mix_interval_sec))       (:1526)
 *                hb       = max(interval, float(heartbeat_interval))  (:1527)
 *                ticks    = max(1, int(round(hb / interval)))         (:1528)
 *                batch    = max(1, -(-qlen // ticks)) if qlen else 0  (:1530)
 *   批取轮转  :  take = batch if batch > 0 else qlen                  (:1533)
 *                queue = queue[take:]                                 (:1535)
 *
 * 位级纪律: 全程 double（同 CPython float）;
 *   round = rint（半偶银行家舍入, 同 CPython round, 与 :4893 相同约定）;
 *   ceil(qlen/ticks) = (qlen-1)/ticks+1（正数恒等 -(-a//b), int64 无溢出）;
 *   max 三元同 _ce_max_py 语义（NaN 比较恒假 → 首参胜出）。
 * 域约束: mix_interval/hb_interval 须有限（来源为 env 钳制 0.025~2.0 的有限
 *   值, :142-147）; NaN/inf 不在域内（CPython round(NaN) 抛 ValueError, 无
 *   位级对应）→ 防御钳 ticks=1; queue_len ≥ 0（Python 列表长度不可能为负）;
 *   域内 ratio = hb/interval 恒 ∈ [1, 2e6]。
 */

/* 到期判定: (now - last) >= hb_interval → 1/0（:1522; 全 double IEEE 逐位） */
CE_API int ce_heartbeat_due(double now, double last_send, double hb_interval)
{
    return (now - last_send) >= hb_interval ? 1 : 0;
}

/* 周期计划: interval/hb 钳制 + ticks（半偶舍入）+ batch（ceil 均分）。
 * 返回 batch（与 *out_batch 一致）。 */
CE_API int64_t ce_heartbeat_plan(double mix_interval, double hb_interval,
                                 int64_t queue_len,
                                 double* out_interval, double* out_hb,
                                 int64_t* out_ticks, int64_t* out_batch)
{
    double interval = (mix_interval > 0.001) ? mix_interval : 0.001;
    double hb = (hb_interval > interval) ? hb_interval : interval;
    double ratio = hb / interval;
    int64_t ticks = 1;
    if (ratio >= 1.0 && ratio <= 9.2e18) {   /* 域内恒真; 域外防御钳 1 */
        ticks = (int64_t)rint(ratio);        /* Python round: 半偶舍入 */
        if (ticks < 1)
            ticks = 1;                        /* max(1, ·) */
    }
    int64_t batch = 0;
    if (queue_len > 0) {
        batch = (queue_len - 1) / ticks + 1;  /* ceil ≡ -(-qlen//ticks) */
        if (batch < 1)
            batch = 1;                        /* max(1, ·) */
    }
    if (out_interval != NULL)
        *out_interval = interval;
    if (out_hb != NULL)
        *out_hb = hb;
    if (out_ticks != NULL)
        *out_ticks = ticks;
    if (out_batch != NULL)
        *out_batch = batch;
    return batch;
}

/* 批取轮转: take = batch>0 ? batch : qlen（:1533, 非正值/缺省 → 全量取走）;
 * 队列原地前移剩余元素, 返回剩余长度（:1535 queue = queue[take:]）。
 * take >= qlen → 队列清空返回 0; q == NULL 防御返回 0。 */
CE_API int64_t ce_heartbeat_rotate(int64_t* q, int64_t qlen, int64_t batch)
{
    if (q == NULL || qlen <= 0)
        return 0;
    int64_t take = batch > 0 ? batch : qlen;
    if (take >= qlen)
        return 0;
    int64_t remain = qlen - take;
    memmove(q, q + take, (size_t)remain * sizeof(int64_t));
    return remain;
}

/* ── T4续: 脉冲效果族（对齐 effect_engine.py _dimmer_pulse :2856 /
 *    _valve_pulse :3325 / effect_presets_v30.py _light_pulse_burst_breath :471）─
 * 统一入口, kind 0-2（全部设备同值——Python 逐设备写同一 val, 与 n 无关）:
 *   kind 0 = dimmer_pulse (对齐 _dimmer_pulse :2856): 锯齿衰减脉冲
 *            phase=(t*rate)%1.0; v = int(peak*(1.0 - phase/width)) if
 *            phase < width else base; 再 clamp[0,255]
 *   kind 1 = valve_pulse  (对齐 _valve_pulse :3325): 占空比方波
 *            val = open_val if (t*rate)%1.0 < duty else close_val
 *            （Python 不 clamp; 参数域 open/close∈[0,255] 内与 C clamp 恒等）
 *   kind 2 = pulse_burst  (对齐 _light_pulse_burst_breath :471): 前半段
 *            pulses 个正弦脉冲群, 后半段静止:
 *            pulses=max(1,int); phase=(t*speed)%1.0; v=0.0;
 *            if phase<0.6: pulse_phase=phase/0.6*pulses; sub=_mod1(pulse_phase);
 *            v=_sine(sub*0.5) = (sin(2.0*pi*(sub*0.5))+1.0)/2.0
 * 参数映射: speed=rate(Hz); width=脉宽 width/duty; peak=峰值 peak/open_val;
 *   base=基础值 base/close_val; pulses 仅 kind 2; lo/hi 仅 kind 2。
 * 位级纪律: 全程 double; % 1.0 用 _ce_mod1（同 ce_wave_effect, 负数 t 语义与
 *   CPython float_rem 一致）; int()+clamp 先值域早退再截断（同 ce_fader_effect,
 *   避免超 int32 的 C (int) UB）; sin 参数左结合对齐 Python; 参数域内无 NaN。 */
CE_API void ce_pulse_effect(double t, double speed, int kind,
                            double width, double peak, double base,
                            int pulses, double lo, double hi,
                            int n, uint8_t* out)
{
    double phase = _ce_mod1(t * speed);       /* 全部设备同值, 每帧算一次 */
    int pulses_c = pulses < 1 ? 1 : pulses;   /* max(1, int(...)) */
    for (int i = 0; i < n; i++) {
        int iv;
        switch (kind) {
        case 0: {  /* dimmer_pulse: 锯齿衰减脉冲 */
            if (phase < width) {
                double vv = peak * (1.0 - phase / width);
                if (vv <= 0.0) iv = 0;
                else if (vv >= 255.0) iv = 255;
                else iv = (int)vv;            /* Python int() 截断 */
            } else {
                iv = (int)base;
            }
            break;
        }
        case 1:  /* valve_pulse: 占空比方波 */
            iv = (phase < width) ? (int)peak : (int)base;
            break;
        case 2: {  /* pulse_burst: 正弦脉冲群呼吸 */
            double v = 0.0;
            if (phase < 0.6) {
                double pulse_phase = phase / 0.6 * (double)pulses_c;
                double sub = _ce_mod1(pulse_phase);
                v = (sin(2.0 * M_PI * (sub * 0.5)) + 1.0) / 2.0;
            }
            double val = lo + v * (hi - lo);
            if (val <= 0.0) iv = 0;
            else if (val >= 255.0) iv = 255;
            else iv = (int)val;
            break;
        }
        default:
            iv = 0;
            break;
        }
        if (iv < 0) iv = 0;
        else if (iv > 255) iv = 255;
        out[i] = (uint8_t)iv;
    }
}

/* ── T4续: 阶梯/彗星追逐族（对齐 effect_presets_v30.py _light_staircase_chase
 *    :252 / _light_comet_chase :291 / _light_dual_comet :329）─────────────
 * 统一入口, kind 0-2（逐设备 phase, out[i] 对应 seq_i=sorted_fx 序）:
 *   kind 0 = staircase  (对齐 :252): steps=max(2,int);
 *            pos=(t*speed*steps)%steps; step_idx=int(seq_i/n*steps);
 *            active=int(pos)%steps; v = 1.0 if step_idx<=active else 0.1
 *   kind 1 = comet      (对齐 :291): pos=(t*speed)%1.0; p=seq_i/n;
 *            diff=(pos-p)%1.0; v = 1.0-diff/tail if diff<tail else 0.0
 *   kind 2 = dual_comet (对齐 :329): pos1=(t*speed)%1.0;
 *            pos2=(1.0-t*speed)%1.0; d1=(pos1-p)%1.0;
 *            v1 = max(0.0, 1.0-d1/tail) if d1<tail else 0.0; d2/v2 同;
 *            v = max(v1, v2)
 * 参数映射: speed=速度; steps 仅 kind 0; tail 仅 kind 1/2; lo/hi 通用。
 * 输出: val = lo + v*(hi-lo) → int()+clamp[0,255]（v30 _clamp = max(lo,
 *   min(hi, int(v))), lo/hi∈[0,255] 且 v∈[0,1] 时值域早退+截断恒等）。
 * 位级纪律: 全程 double; % steps(正) 用 _ce_mod_div（CPython float_rem 逐位）;
 *   % 1.0 用 _ce_fmod_pos（-0.0 归一 copysign, 同 float_rem）; int() 截断向零;
 *   int()%steps 用 _ce_py_mod_ll; max 用三元（参数域内无 NaN）; n==0 零迭代
 *   与 Python `if n==0: return` 等价。 */
CE_API void ce_stair_comet_effect(double t, double speed, int kind,
                                  int steps, double tail,
                                  double lo, double hi,
                                  int n, uint8_t* out)
{
    int steps_c = steps < 2 ? 2 : steps;      /* max(2, int(...)) */
    double pos = _ce_mod1(t * speed);         /* kind 1/2 */
    double pos2 = _ce_mod1(1.0 - t * speed);  /* 仅 kind 2 */
    int active = 0;                           /* 仅 kind 0 */
    if (kind == 0) {
        double pos_s = _ce_mod_div(t * speed * (double)steps_c,
                                   (double)steps_c);
        active = (int)_ce_py_mod_ll((long long)pos_s, (long long)steps_c);
    }
    for (int i = 0; i < n; i++) {
        double p = (double)i / (double)n;     /* seq_i / n 浮点除 */
        double v;
        switch (kind) {
        case 0: {  /* staircase */
            int step_idx = (int)(p * (double)steps_c);
            v = (step_idx <= active) ? 1.0 : 0.1;
            break;
        }
        case 1: {  /* comet */
            double diff = _ce_fmod_pos(pos - p, 1.0);
            v = (diff < tail) ? (1.0 - diff / tail) : 0.0;
            break;
        }
        case 2: {  /* dual_comet */
            double d1 = _ce_fmod_pos(pos - p, 1.0);
            double v1 = 0.0;
            if (d1 < tail) {
                double t1 = 1.0 - d1 / tail;
                v1 = (t1 < 0.0) ? 0.0 : t1;   /* max(0.0, ...) */
            }
            double d2 = _ce_fmod_pos(pos2 - p, 1.0);
            double v2 = 0.0;
            if (d2 < tail) {
                double t2 = 1.0 - d2 / tail;
                v2 = (t2 < 0.0) ? 0.0 : t2;
            }
            v = (v1 > v2) ? v1 : v2;          /* max(v1, v2) */
            break;
        }
        default:
            v = 0.0;
            break;
        }
        double val = lo + v * (hi - lo);
        int iv;
        if (val <= 0.0) iv = 0;
        else if (val >= 255.0) iv = 255;
        else iv = (int)val;
        out[i] = (uint8_t)iv;
    }
}
/* ── T8续: 音乐结构分析纯数学（对齐 src/audio/music_structure_analyzer.py）───
 *
 * 目标函数（每 25ms 帧频调用, 纯数学, 无 FFT 依赖）:
 *   1. _spectral_flux :107-112（频谱通量: 帧间正向能量差异）
 *        flux = sum(max(0.0, b - p) for b, p in zip(bands, prev_bands))
 *   2. _compute_novelty :552-560（频谱新颖度: 帧间总变化, 段落边界检测）
 *        d = sum(abs(a - b) for a, b in zip(bands, prev))
 *        return min(1.0, d / max(1.0, n * 0.3))
 *   3. _compute_centroid :540-550（频谱重心, 段落分类特征）
 *        s = sum(bands); if s <= 1e-6 或 n <= 1 → 0.0
 *        denom = s * max(1, n - 1); if denom <= 1e-12 → 0.0
 *        return sum(i * b for i, b in enumerate(bands)) / denom
 *
 * 位级等价纪律（全程 f64, 同 ce_audio_spectral_flatness 内核）:
 *   1. 所有 sum() 均为 Python 3.13 内建 sum → Neumaier 补偿求和,
 *      与 _ce_py_sum_f64 同算法（此处内联到求值循环, 200k+ 对拍 0 差异）
 *   2. max(0.0, x) = (x > 0.0) ? x : 0.0 —— NaN → 0.0, 同 Python max
 *   3. abs(x) = fabs —— -0.0 → +0.0, NaN → NaN, 同 Python abs
 *   4. min(1.0, x) = (x < 1.0) ? x : 1.0 —— NaN → 1.0, 同 Python min
 *   5. 比较直译（NaN 比较为 False 即穿过）: s <= 1e-6 / denom <= 1e-12 / n <= 1
 *   6. i * b = (double)i * b（小 int → double 无损）; n * 0.3 = (double)n * 0.3
 *   7. prev 缺失/长度不等 → 0.0 的守卫在调用方（None/长度是 Python 语义,
 *      不进 C）; C 侧 n <= 0 一律返回 0.0（同 Python 空输入行为）
 *   8. 首元素经 0.0 + x0（PyNumber_Add(0, x0): 0.0 + -0.0 = +0.0 保号）,
 *      之后 Neumaier, 收尾 c != 0.0 且有限才补偿
 */

/* 频谱通量 + 频谱新颖度（一帧两特征单遍求值, 两路累加器互相独立） */
CE_API void ce_music_spectral_delta(const double* bands, const double* prev,
                                    int n, double* out_flux, double* out_novelty)
{
    double f = 0.0, c_f = 0.0;   /* flux 累加器 + Neumaier 余项 */
    double d = 0.0, c_d = 0.0;   /* novelty 累加器 + Neumaier 余项 */
    if (n > 0) {
        double diff = bands[0] - prev[0];
        double m = (diff > 0.0) ? diff : 0.0;
        double x = fabs(diff);
        f = 0.0 + m;
        d = 0.0 + x;
        for (int i = 1; i < n; i++) {
            double t;
            diff = bands[i] - prev[i];
            m = (diff > 0.0) ? diff : 0.0;
            x = fabs(diff);
            t = f + m;
            if (fabs(f) >= fabs(m))
                c_f += (f - t) + m;
            else
                c_f += (m - t) + f;
            f = t;
            t = d + x;
            if (fabs(d) >= fabs(x))
                c_d += (d - t) + x;
            else
                c_d += (x - t) + d;
            d = t;
        }
        if (c_f != 0.0 && isfinite(c_f))
            f += c_f;
        if (c_d != 0.0 && isfinite(c_d))
            d += c_d;
    }
    if (out_flux)
        *out_flux = f;
    if (out_novelty) {
        double denom = ((double)n * 0.3 > 1.0) ? (double)n * 0.3 : 1.0;
        double r = d / denom;
        *out_novelty = (r < 1.0) ? r : 1.0;
    }
}

/* 频谱重心: 同 _compute_centroid :540-550 */
CE_API double ce_music_centroid(const double* bands, int n)
{
    double s, denom, num, c, t, x;
    int i;
    if (n <= 0)
        return 0.0;                 /* sum([]) = 0 → s <= 1e-6 → 0.0 */
    s = _ce_py_sum_f64(bands, n);   /* s = sum(bands) */
    if (s <= 1e-6)
        return 0.0;
    if (n <= 1)
        return 0.0;
    denom = s * (double)(n - 1);    /* max(1, n - 1) = n - 1（n >= 2） */
    if (denom <= 1e-12)
        return 0.0;
    /* 分子: sum(i * b for i, b in enumerate(bands)) —— Neumaier（内联） */
    num = 0.0 + (0.0 * bands[0]);   /* i=0: 0 * b（0*inf = NaN, 同 Python） */
    c = 0.0;
    for (i = 1; i < n; i++) {
        x = (double)i * bands[i];
        t = num + x;
        if (fabs(num) >= fabs(x))
            c += (num - t) + x;
        else
            c += (x - t) + num;
        num = t;
    }
    if (c != 0.0 && isfinite(c))
        num += c;
    return num / denom;
}

/* ── T7续: update_layer 批量稀疏写入（对齐 src/network/network_manager.py
 *    update_layer_sparse_arrays :1093-1175 / update_layer_sparse_grouped_arrays
 *    :1177-1258 的每 universe 纯数组块 :1134-1157 / :1217-1237）──
 *
 * 纯内存操作: 传入层缓冲就地写入, 不触碰任何 Python 对象状态。
 * Python 侧外层循环（分组/锁/LTP/dirty set）保持不变, 仅内块换本内核。
 *
 * 输入契约（与 numpy 语义逐位一致）:
 *   addrs int32[n]    — DMX 地址（0..511 有效; 越界条目被内核忽略——Python
 *                        侧在分组前已过滤, 此处双保险, 语义同「过滤后不参与」）
 *   vals  uint8[n]    — 目标值（Python 侧已 clamp 0..255, 内核不再钳位）
 *   data  uint8[512]  — 层数据缓冲（in-place 读写; 重复地址 last-wins, 同
 *                        numpy `d[addr_u] = val_u` 顺序赋值）
 *   active uint8[512] — 层激活缓冲 0/1（in-place; 非零视为激活, 同 numpy bool）
 *   use_fast_skip int — 1 = prev 存在且 universe 在 _universe_snapshot
 *                        （早跳: 全部有效条目 active 且值相等 → 不写）
 *   touch  uint8[512] — 输出: 有效条目地址置 1（内核先清零; 对齐 Python
 *                        `touch_mask[addr_u] = True`, 早跳路径同样输出）
 * 输出:
 *   out_changed int*  — 1 = 写入路径（有有效条目且早跳未命中）; 0 = 早跳或无有效条目
 * 返回: 有效条目数 n_valid（0 表示本组无贡献, Python 侧 continue）
 *
 * 早跳比对细节（与 numpy 逐位一致）:
 *   1. 重复地址逐条比对（array_equal 语义: 每条 (addr,val) 都必须满足
 *      active[addr] 且 data[addr]==val —— 例如 (5,10),(5,20) 恒不早跳）
 *   2. 越界条目不参与早跳比对（Python 先过滤后比对）
 */
CE_API int ce_layer_sparse_write(const int32_t* addrs, const uint8_t* vals,
                                 int64_t n, uint8_t* data, uint8_t* active,
                                 int use_fast_skip, uint8_t* touch,
                                 int* out_changed)
{
    if (touch) memset(touch, 0, 512);
    if (out_changed) *out_changed = 0;
    if (!addrs || !vals || !data || !active || n <= 0)
        return 0;

    int n_valid = 0;
    for (int64_t i = 0; i < n; i++) {
        int32_t a = addrs[i];
        if (a >= 0 && a < 512) n_valid++;
    }
    if (n_valid == 0)
        return 0;

    if (use_fast_skip) {
        int skip = 1;
        for (int64_t i = 0; i < n; i++) {
            int32_t a = addrs[i];
            if (a < 0 || a >= 512) continue;
            if (!active[a] || data[a] != vals[i]) {
                skip = 0;
                break;
            }
        }
        if (skip) {
            for (int64_t i = 0; i < n; i++) {
                int32_t a = addrs[i];
                if (a >= 0 && a < 512) touch[a] = 1;
            }
            return n_valid;               /* 早跳: 无写入, out_changed 保持 0 */
        }
    }

    for (int64_t i = 0; i < n; i++) {
        int32_t a = addrs[i];
        if (a < 0 || a >= 512) continue;
        data[a] = vals[i];                /* 重复地址 last-wins, 同 numpy */
        active[a] = 1;
        touch[a] = 1;
    }
    if (out_changed) *out_changed = 1;
    return n_valid;
}

/* ── Programmer 层数值 C 化（对齐 src/ui/programmer_panel.py，纯计算段，不接 hook）
 *
 * 1) ce_programmer_layer_mix — ChannelTypeRow._compute_merged_value :2663
 *    四层（fader→joystick→audio→matrix）按固定层序混合，模式
 *    override/add/multiply/max（与 _FADER_BLEND_MAP/_JOY_BLEND_MAP 同集）。
 *    Python 语义逐条复刻（位级等价纪律）:
 *      - 层缺值(None) → NaN 哨兵 → 跳过；mode 未知 → no-op（if/elif 链无 else）
 *      - add: min(255, r+v) → (s<255.0)?s:255.0 —— Python min 返回首参：NaN→255
 *      - max: max(r,v)     → (r<v)?v:r         —— Python max 返回首参：NaN→r
 *      - multiply: int(r*v/255) → (int32_t)((r*v)/255.0) 截断向零（积 ≤2^53 精确）
 *      - 全程 double：Python int/float 混合算术升为 double，逐位一致
 *    返回值 = 原始合并值（可为 float，同 Python——调用侧自行 int(round(·))）。
 *
 * 2) ce_programmer_motor_16bit — _motor_8bit_to_16bit_pair :197
 *    8bit 逻辑位 → 16bit 粗/精双字节。要点:
 *      - 钳位复刻 Python max(0.0, min(255.0, x))（min 先、max 后）：
 *        NaN 输入 → 255.0 → v16=65535（同 Python 行为）
 *      - v16 = int(round(logical*256.0)) — Python round 半偶 → ce_round_half_even
 *      - 返回 packed uint32: (coarse<<8)|fine
 */
CE_API double ce_programmer_layer_mix(double v0, int32_t m0,
                                      double v1, int32_t m1,
                                      double v2, int32_t m2,
                                      double v3, int32_t m3)
{
    const double vals[4] = {v0, v1, v2, v3};
    const int32_t modes[4] = {m0, m1, m2, m3};
    double result = 0.0;
    for (int i = 0; i < 4; i++) {
        const double layer_val = vals[i];
        if (isnan(layer_val))
            continue;              /* Python None → 本层跳过 */
        switch (modes[i]) {
        case 0:                    /* override */
            result = layer_val;
            break;
        case 1: {                  /* add: min(255, result + layer_val) */
            const double s = result + layer_val;
            result = (s < 255.0) ? s : 255.0;
            break;
        }
        case 2:                    /* multiply: int(result * layer_val / 255) */
            result = (double)(int32_t)((result * layer_val) / 255.0);
            break;
        case 3:                    /* max: max(result, layer_val) */
            result = (result < layer_val) ? layer_val : result;
            break;
        default:                   /* 未知模式 → no-op（Python if/elif 无 else） */
            break;
        }
    }
    return result;
}

CE_API uint32_t ce_programmer_motor_16bit(double value)
{
    double logical = (value < 255.0) ? value : 255.0;   /* min(255.0, x) */
    logical = (logical < 0.0) ? 0.0 : logical;          /* max(0.0, ·) */
    int64_t v16;
    if (logical >= 255.0) {
        v16 = 65535;
    } else {
        v16 = ce_round_half_even(logical * 256.0);
    }
    if (v16 < 0)
        v16 = 0;
    else if (v16 > 65535)
        v16 = 65535;
    const uint32_t coarse = (uint32_t)((v16 >> 8) & 0xFF);
    const uint32_t fine = (uint32_t)(v16 & 0xFF);
    return (coarse << 8) | fine;
}
/* ── T8: voice_model 声部决策数值核心 ────────────────────────────────────
 * 对齐 src/engine/auto_arrange/voice_model.py（只读参考, 不碰 src）:
 *   ce_voice_decide      = decide_voice(:299) 纯数值决策段
 *   ce_voice_texture_cap = arrange_voices(:530) 织体上限 max_active
 *   piecewise_band(:289) 以 _ce_piecewise_band 内联
 *
 * vc 编码: 0=motor 1=pump 2=valve 3=matrix_rgb 4=fountain_rgb 5=dimmer;
 *   其他值 → dimmer（Python if/elif 链 else 兜底）。
 * 输出编码:
 *   out_num[0]=drive [1]=speed_dmx [2]=clip_dur [3]=fade_in [4]=fade_out
 *            [5]=level [6]=base_hue
 *   out_flags[0]=direction 0=L2R 1=R2L（int(round(e*10))%2, Python 非负余数）
 *            [1]=color_mode 0=white 1=pure 2=color
 *            [2]=mechanism 0=static 1=effect 2=matrix
 *            [3]=shape 0=mountain 1=gradient_x 2=multi_wave
 *                      3=two_high_two_low 4=odd_even 5=circle 6=breath
 *            [4]=topo_kind 0=whole 1=odd_even 2=arc 3=circle
 *            [5]=solo_path [6]=use_motor_phase [7]=use_sequence
 * 参数映射（逐字对齐 Python 字面量）:
 *   drive: motor=0.55*e+(0.45 if has_mel else 0.30)+(0.10 if is_build else 0.0)
 *     再 min(1.0, drive)（仅电机钳制）; pump=0.5*e+0.5*min(1.0,perc);
 *     valve=min(1.0,perc); matrix/dimmer=e; fountain=0.6*e+0.4*(1.0 if has_mel
 *     else 0.5)。+ 与 * 左结合同 Python。
 *   speed_dmx = piecewise_band(speed_band, drive) * global_soft;
 *   clip_dur = min(max(piecewise_band(dur_band, 1.0-0.5*drive), 1.0),
 *                  seg_dur if seg_dur>0.5 else dur_band[1]);
 *   fade_in/out = base * (1.3 if e<0.4 else 1.0);
 *   level = max(0.15, min(1.0, 0.4+0.6*drive))。
 *   base_hue = ((key_kh + int(round(e*360))) % 360) / 360.0——round 为
 *     CPython 半偶舍入（_ce_py_round_half_even 逐位复刻, 对 -0.5 同 CPython
 *     返 0）; % 360 取 Python 非负余数（负和 +360 修正）; key_kh 由调用方按
 *     voice.key 循环 (_kh*131+ord(c))&0xFFFF 预算（C 不做 UTF-32 ord, 防中文
 *     键歧义）, 域 [0, 65535]。
 * 域约束: e 必须有限（Python round(±inf/NaN) 抛 OverflowError/ValueError,
 *   不在内核域内）; 且 |e| ≤ 1e16（e*360 须可放入 int64, 同 Python 无限 int
 *   的 int64 化边界）。seg_dur/perc 可任意有限值。
 * 位级纪律: 全程 double; min/max 用 _ce_min_py/_ce_max_py 复刻 CPython 内建
 *   min/max（NaN 比较恒假 → 首参数胜出）; 严格 < / <= / > 比较逐字同 Python;
 *   clip_dur 的 min/max 不接 NaN 语义特判——与 CPython 同序同结果。 */
static double _ce_py_round_half_even(double x)
{
    if (x >= 0.0) {
        double r = floor(x);
        double frac = x - r;              /* 精确: x-floor(x) 无舍入 */
        if (frac < 0.5)
            return r;
        if (frac > 0.5)
            return r + 1.0;
        return (fmod(r, 2.0) == 0.0) ? r : r + 1.0;   /* 恰半 → 偶 */
    } else {
        double r = ceil(x);
        double frac = r - x;              /* 精确: ceil(x)-x 无舍入 */
        if (frac < 0.5)
            return r;
        if (frac > 0.5)
            return r - 1.0;
        return (fmod(r, 2.0) == 0.0) ? r : r - 1.0;
    }
}

/* piecewise_band(:289): t∈[0,1] 钳制后两段线性插值 p10→med→p90 */
static double _ce_piecewise_band(double p10, double med, double p90, double t)
{
    t = _ce_clamp01_py(t);
    if (t <= 0.5)
        return p10 + (med - p10) * (t / 0.5);
    return med + (p90 - med) * ((t - 0.5) / 0.5);
}

/* decide_voice(:318-332) drive: 各声部对不同音乐特征敏感度 */
static double _ce_voice_drive(int vc, double e, double perc,
                              int has_mel, int is_build)
{
    switch (vc) {
    case 0:  /* motor: 旋律主奏/常驻表现, 钳 1.0 */
        return _ce_min_py(1.0, 0.55 * e + (has_mel ? 0.45 : 0.30)
                          + (is_build ? 0.10 : 0.0));
    case 1:  /* pump: 能量+打击 */
        return 0.5 * e + 0.5 * _ce_min_py(1.0, perc);
    case 2:  /* valve: 跟打击节奏 */
        return _ce_min_py(1.0, perc);
    case 3:  /* matrix_rgb: 跟能量 */
        return e;
    case 4:  /* fountain_rgb: 能量+和声 */
        return 0.6 * e + 0.4 * (has_mel ? 1.0 : 0.5);
    default: /* dimmer */
        return e;
    }
}

/* decide_voice(:348-360) 输出机制编码 */
static int _ce_voice_mechanism(int vc, double e, double perc)
{
    switch (vc) {
    case 1:  return (e >= 0.55 || perc >= 0.45) ? 1 : 0;  /* pump 突发 */
    case 3:  return 2;                                    /* matrix */
    case 0:  return 1;                                    /* motor 动态 */
    case 4:  return (e >= 0.55) ? 1 : 0;                  /* fountain */
    case 2:  return (perc >= 0.4 || e >= 0.6) ? 1 : 0;    /* valve */
    default: return 0;                                    /* dimmer */
    }
}

/* decide_voice(:385-397) 空间形状编码（pump 依赖机制） */
static int _ce_voice_shape(int vc, double e, double perc, int mechanism)
{
    switch (vc) {
    case 1:  /* pump */
        if (mechanism == 0)
            return (e >= 0.35) ? 0 : 1;    /* mountain / gradient_x */
        return 2;                          /* multi_wave */
    case 2:  return (perc > 0.6) ? 3 : 4;  /* two_high_two_low / odd_even */
    case 0:  return (e >= 0.5) ? 5 : 6;    /* circle / breath */
    case 3:  return (e >= 0.5) ? 2 : 1;    /* multi_wave / gradient_x */
    default: return 2;                     /* multi_wave */
    }
}

/* decide_voice(:400-407) 拓扑子选编码 */
static int _ce_voice_topo(int vc, double e, double perc, int is_drop)
{
    if (is_drop || e >= 0.85)
        return 0;                          /* whole 高潮全开 */
    if (vc == 2 && perc > 0.5)
        return 1;                          /* odd_even 阀奇偶呼应 */
    if (e < 0.4)
        return 2;                          /* arc 柔美一段弧 */
    return 3;                              /* circle */
}

CE_API void ce_voice_decide(
    int vc, double e, double perc, int has_mel, int is_build, int is_drop,
    double seg_dur,
    const double* speed_band, const double* dur_band,
    double fade_in_base, double fade_out_base,
    double seq_tendency, double motor_phase_tendency,
    double global_soft, long long key_kh,
    double* out_num, int* out_flags)
{
    double drive = _ce_voice_drive(vc, e, perc, has_mel, is_build);
    double pb_speed = _ce_piecewise_band(speed_band[0], speed_band[1],
                                         speed_band[2], drive);
    double pb_dur = _ce_piecewise_band(dur_band[0], dur_band[1],
                                       dur_band[2], 1.0 - 0.5 * drive);
    int mechanism = _ce_voice_mechanism(vc, e, perc);
    int color_mode = 0;
    double hue = 0.0;
    long long r, m;
    int solo_path = 0, use_motor_phase = 0;

    out_num[0] = drive;
    out_num[1] = pb_speed * global_soft;
    out_num[2] = _ce_min_py(_ce_max_py(pb_dur, 1.0),
                            (seg_dur > 0.5) ? seg_dur : dur_band[1]);
    out_num[3] = fade_in_base * ((e < 0.4) ? 1.3 : 1.0);
    out_num[4] = fade_out_base * ((e < 0.4) ? 1.3 : 1.0);
    out_num[5] = _ce_max_py(0.15, _ce_min_py(1.0, 0.4 + 0.6 * drive));

    /* 配色: 仅 fountain/matrix/dimmer（Python :365-382; 色相 key+能量派生） */
    if (vc == 3 || vc == 4 || vc == 5) {
        r = (long long)_ce_py_round_half_even(e * 360.0);
        m = (key_kh + r) % 360;
        if (m < 0)
            m += 360;                     /* Python % 非负 */
        hue = (double)m / 360.0;
        if (is_drop || e >= 0.78)
            color_mode = 2;               /* color 高潮彩色流转 */
        else if (e >= 0.5)
            color_mode = 1;               /* pure 中能纯色 */
        else
            color_mode = 0;               /* white */
        if (vc == 3 && e < 0.45)
            color_mode = 0;               /* 矩阵低能偏白 */
    }
    out_num[6] = hue;

    /* 电机 solo/相位（Python :410-413, 仅电机） */
    if (vc == 0) {
        solo_path = (has_mel && e < 0.6) ? 1 : 0;
        use_motor_phase = (motor_phase_tendency > 0.15 || is_build) ? 1 : 0;
    }

    /* 方向（Python :417）: int(round(e*10)) % 2 == 0 → L2R */
    r = (long long)_ce_py_round_half_even(e * 10.0);
    m = r % 2;
    if (m < 0)
        m += 2;

    out_flags[0] = (m == 0) ? 0 : 1;
    out_flags[1] = color_mode;
    out_flags[2] = mechanism;
    out_flags[3] = _ce_voice_shape(vc, e, perc, mechanism);
    out_flags[4] = _ce_voice_topo(vc, e, perc, is_drop);
    out_flags[5] = solo_path;
    out_flags[6] = use_motor_phase;
    out_flags[7] = (seq_tendency > 0.15 || (vc == 2 && perc > 0.5)) ? 1 : 0;
}

/* arrange_voices(:530-537) 织体上限: is_drop 或 e>=0.85 → tutti(n_voices);
 * e>=0.55 → max(2, round(n*0.7)); e>=0.35 → max(1, round(n*0.45));
 * else → max(1, round(n*0.3))。round 半偶; NaN e 比较恒假 → 落入 else,
 * 同 Python 链式 if。 */
CE_API int ce_voice_texture_cap(int n_voices, double e, int is_drop)
{
    long long r;
    if (is_drop || e >= 0.85)
        return n_voices;
    if (e >= 0.55) {
        r = (long long)_ce_py_round_half_even((double)n_voices * 0.7);
        return (r > 2) ? (int)r : 2;
    }
    if (e >= 0.35) {
        r = (long long)_ce_py_round_half_even((double)n_voices * 0.45);
        return (r > 1) ? (int)r : 1;
    }
    r = (long long)_ce_py_round_half_even((double)n_voices * 0.3);
    return (r > 1) ? (int)r : 1;
}
/* ── T10: 同步模式几何内核（对齐 src/ui/topview_panel.py _compute_xy_reference_angles
 *       + src/ui/depence_groups.py spline_param/spline_sample）────────────
 *
 * 同步基准角链路（2026-08-03 记忆: 弯折弧线同步基准角/切向角计算）:
 *   1. spline_param — 设备在样条折线上的参数位置 t∈[0,1]（最近线段投影,
 *      depence_groups.spline_param :495, topview_panel._spline_order_ts :2291
 *      numpy 批量版声明与标量逐位等价, 本内核实现标量语义）;
 *   2. Catmull-Rom 弦长参数化导数 — topview_panel._catmull_rom_tangent :541
 *      （内部点中心差分/端点单向差分/闭合回绕; 归一化用 math.hypot）;
 *   3. ce_sync_tangent_angles — 回退路径整链 :2964-:2981（弦长累积 ts +
 *      逐台切向 + normalize(-degrees(atan2)) + RotY [-90,90] 翻转）;
 *   4. ce_spline_sample — DEPENCE TSpline3D 均匀自然三次样条采样+解析切向
 *      （depence_groups.spline_sample :238; Thomas 追赶法 + 每段 per_seg
 *      插值点, 线性/闭合折线分支 = 边方向角）;
 *   5. ce_sync_angle_from_tangent — 切向→同步角公共段（邻域点积翻转 +
 *      角度归一 + 可选 RotY 翻转; _spline_cluster_angles 阶段2 :508-:534
 *      与圆分支 :2937-:2939 共用）。
 *
 * 位级等价纪律（2026-08-03 实测验证, 见 bench/ce_fuzz_sync.py）:
 *   1. math.hypot 不是 CRT hypot（CPython 3.13 自实现 vector_norm）——
 *      本文件逐行复刻（Neumaier 补偿 + fma 精确乘法 + sqrt 微分校正）;
 *   2. Python x**2 = float_pow = ucrtbase.pow(x, 2.0) 而非 x*x, 且 MSVC
 *      内置 pow 最后一位不同 → GetProcAddress 直调 ucrtbase.pow;
 *   3. math.degrees = rad*(180.0/M_PI), math.radians = deg*(M_PI/180.0)
 *      （编译期常量折叠同 CPython 的 MSVC 构建）;
 *   4. _normalize_angle_deg 的 % 用 Python float 取模语义（fmod 异号加
 *      除数, 零结果 copysign 为除数符号）;
 *   5. sin/cos/atan2/sqrt/fmod/fabs/floor/copysign/frexp/ldexp 直接 CRT
 *      调用（与 Python math 模块逐位一致, 已 30 万次对拍确认）。
 */

typedef struct { double hi; double lo; } ce_dl;

/* CPython mathmodule.c dl_fast_sum（要求 fabs(a) >= fabs(b), 调用方保证） */
static ce_dl ce_dl_fast_sum(double a, double b)
{
    double x = a + b;
    double y = (a - x) + b;
    return (ce_dl){x, y};
}

/* CPython mathmodule.c dl_mul — fma 精确乘积（ucrt fma 正确舍入, 同 Python） */
static ce_dl ce_dl_mul(double x, double y)
{
    double z = x * y;
    double zz = fma(x, y, -z);
    return (ce_dl){z, zz};
}

/* CPython 3.13 math.hypot 2 元复刻: fabs → running-max → vector_norm。
 * 实测: 普通域 30 万 + 次正规/极大 20 万 + inf/nan/±0 全部语义与
 * math.hypot 逐位一致。 */
static double ce_py_hypot2(double ax, double ay)
{
    double max = 0.0;
    double csum = 1.0, frac1 = 0.0, frac2 = 0.0;
    double x, h, scale;
    ce_dl pr, sm;
    int max_e;
    ax = fabs(ax);                   /* math.hypot 包装层: x = fabs(x)（CPython） */
    ay = fabs(ay);                   /* 负输入缺此步 → max 恒 0 → 恒返 0（已修） */
    if (ax > max) max = ax;          /* Python: if (x > max) max = x — nan 恒输 */
    if (ay > max) max = ay;
    if (isinf(max)) return max;      /* inf 优先于 nan, 同 CPython 判断次序 */
    if (isnan(ax) || isnan(ay)) return NAN;
    if (max == 0.0) return max;
    frexp(max, &max_e);
    if (max_e < -1023) {             /* 次正规: 除 DBL_MIN 后递归（同 CPython） */
        return DBL_MIN * ce_py_hypot2(ax / DBL_MIN, ay / DBL_MIN);
    }
    scale = ldexp(1.0, -max_e);
    x = ax * scale;
    pr = ce_dl_mul(x, x);
    sm = ce_dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
    x = ay * scale;
    pr = ce_dl_mul(x, x);
    sm = ce_dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
    h = sqrt(csum - 1.0 + (frac1 + frac2));
    pr = ce_dl_mul(-h, h);
    sm = ce_dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
    x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);              /* sqrt 微分校正 */
    return h / scale;
}

/* Python float % 精确复刻（CPython float_rem）: 除数正负一律成立 */
static double ce_py_mod(double x, double y)
{
    double m = fmod(x, y);
    if (m != 0.0) {
        if ((y < 0.0) != (m < 0.0))
            m += y;
    } else {
        m = copysign(0.0, y);
    }
    return m;
}

/* topview_panel._normalize_angle_deg :297-:298 */
static double ce_norm_angle_deg(double angle)
{
    return ce_py_mod(angle + 180.0, 360.0) - 180.0;
}

static double ce_deg(double rad) { return rad * (180.0 / M_PI); }
/* 调用方做 cos/sin(radians) 转换（radians = deg * (M_PI/180.0) 已对拍一致） */

/* x**2 = ucrtbase.pow(x, 2.0)（GetProcAddress: MSVC 内置 pow 最后一位不同;
 * Python float_pow 逐位等于 CRT pow, 已 30 万次对拍确认）。 */
typedef double (__cdecl *ce_pow2_fn)(double, double);
static double ce_py_pow2(double x)
{
    static ce_pow2_fn f = NULL;
    if (f == NULL) {
        HMODULE m = GetModuleHandleA("ucrtbase.dll");
        if (m != NULL)
            f = (ce_pow2_fn)(void *)GetProcAddress(m, "pow");
    }
    if (f != NULL)
        return f(x, 2.0);
    return pow(x, 2.0);   /* 兜底（Python 进程内 ucrtbase 恒已加载, 不触发） */
}

/* ── T10a: 折线投影参数 t∈[0,1]（depence_groups.spline_param :495-:518）───
 *
 * cp 交错 (x0,z0,x1,z1,...), n_cp ≥ 1; 退化段 seg2<=1e-12 跳过;
 * t = ((x-ax)*vx + (z-az)*vz)/seg2 → clamp[0,1]（min 先做, 同 Python）;
 * d2 用 pow(x,2.0) 复刻 x**2; 严格 < 保留首个最近段（同 Python/np.argmin）。
 */
CE_API double ce_spline_param(double x, double z,
                              const double* cp, int n_cp)
{
    double best_t = 0.0, best_d2 = INFINITY;
    int n = (n_cp > 1) ? (n_cp - 1) : 1;   /* max(1, len-1) */
    int i;
    if (n_cp <= 0 || cp == NULL)
        return 0.0;
    for (i = 0; i < n_cp - 1; i++) {
        double ax = cp[2 * i], az = cp[2 * i + 1];
        double bx = cp[2 * i + 2], bz = cp[2 * i + 3];
        double vx = bx - ax, vz = bz - az;
        double seg2 = vx * vx + vz * vz;
        double t, px, pz, d2;
        if (seg2 <= 1e-12)
            continue;
        t = ((x - ax) * vx + (z - az) * vz) / seg2;
        if (t > 1.0) t = 1.0;              /* min(1.0, t) */
        if (t < 0.0) t = 0.0;              /* max(0.0, ...) */
        t = t + 0.0;                       /* -0.0 → +0.0（同 max 对 -0.0） */
        px = ax + vx * t;
        pz = az + vz * t;
        d2 = ce_py_pow2(x - px) + ce_py_pow2(z - pz);
        if (d2 < best_d2) {
            best_d2 = d2;
            best_t = ((double)i + t) / (double)n;
        }
    }
    return best_t;
}

/* ── T10b: 弦长参数化 Catmull-Rom 导数（topview_panel._catmull_rom_tangent :541-:579）
 *
 * pts 交错 (x0,z0,...) 已按最终顺序; ts[n] 弦长累积（调用方/ce_sync_tangent_angles）;
 * 内部点中心差分 (P[i+1]-P[i-1])/max(1e-9, ts[i+1]-ts[i-1]), 端点单向差分;
 * closed=1 邻居回绕且不归一化（同 Python）; 其余 hypot 归一化, 长度<=1e-9
 * 退回 (1,0)。
 */
CE_API void ce_catmull_rom_tangent(const double* pts, const double* ts,
                                   int n, int idx, int closed,
                                   double* out_dx, double* out_dy)
{
    double dx, dy, length;
    if (pts == NULL || ts == NULL || out_dx == NULL || out_dy == NULL)
        return;
    if (n <= 1) {
        *out_dx = 1.0;
        *out_dy = 0.0;
        return;
    }
    if (closed) {
        int ip = (idx - 1 + n) % n;        /* Python (idx-1) % n, n>0 */
        int in_ = (idx + 1) % n;
        *out_dx = pts[2 * in_] - pts[2 * ip];
        *out_dy = pts[2 * in_ + 1] - pts[2 * ip + 1];
        return;                            /* 闭合分支不归一化（同 Python） */
    }
    if (idx <= 0) {
        dx = pts[2] - pts[0];
        dy = pts[3] - pts[1];
    } else if (idx >= n - 1) {
        dx = pts[2 * (n - 1)] - pts[2 * (n - 2)];
        dy = pts[2 * (n - 1) + 1] - pts[2 * (n - 2) + 1];
    } else {
        double dt = ts[idx + 1] - ts[idx - 1];
        if (dt < 1e-9) dt = 1e-9;          /* max(1e-9, dt)（NaN 同 Python max） */
        dx = (pts[2 * (idx + 1)] - pts[2 * (idx - 1)]) / dt;
        dy = (pts[2 * (idx + 1) + 1] - pts[2 * (idx - 1) + 1]) / dt;
    }
    length = ce_py_hypot2(dx, dy);
    if (length <= 1e-9) {
        *out_dx = 1.0;
        *out_dy = 0.0;
        return;
    }
    *out_dx = dx / length;
    *out_dy = dy / length;
}

/* ── T10c: 同步基准角全链（topview_panel._compute_xy_reference_angles 回退路径
 *       :2964-:2981）
 *
 * 输入 pts 为已排好序的设备定位点（排序/闭合判定 = 调用方 numpy/UI 逻辑）;
 * ts[0]=0, ts[k]=ts[k-1]+hypot(相邻差) 逐点累积; 每台取 Catmull-Rom 切向,
 * 角 = normalize(-degrees(atan2(dy,dx))), 再按 DEPENCE RotY 恒在 [-90,90]
 * 翻转 180°。n=1 时数学路径自然给出 0.0（与调用方早退 {fid:0.0} 一致）。
 */
CE_API void ce_sync_tangent_angles(const double* pts, int n, int closed,
                                   double* out_deg)
{
    int k;
    double* ts;
    if (pts == NULL || out_deg == NULL || n <= 0)
        return;
    ts = (double*)malloc((size_t)n * sizeof(double));
    if (ts == NULL)
        return;
    ts[0] = 0.0;
    for (k = 1; k < n; k++) {
        ts[k] = ts[k - 1] + ce_py_hypot2(pts[2 * k] - pts[2 * (k - 1)],
                                         pts[2 * k + 1] - pts[2 * (k - 1) + 1]);
    }
    for (k = 0; k < n; k++) {
        double dx, dy, ang;
        ce_catmull_rom_tangent(pts, ts, n, k, closed, &dx, &dy);
        ang = ce_norm_angle_deg(-ce_deg(atan2(dy, dx)));
        if (ang > 90.0)
            ang -= 180.0;                  /* RotY [-90,90] 翻转, 同 Python */
        else if (ang < -90.0)
            ang += 180.0;
        out_deg[k] = ang;
    }
    free(ts);
}

/* ── T10d: DEPENCE 样条采样（depence_groups.spline_sample :238-:355）────────
 *
 * cp 交错控制点 (x,z); mode: 0=linear(折线, 切向=边方向角), 1=cubic(均匀自然
 * 三次样条); 首尾重合(<1e-6)自动走 linear（同 Python 闭合折线分支）。
 * 输出容量: n_cp<2 → n_cp; 否则 (n_cp-1)*per_seg+1（out_xy 交错 n_out*2）。
 * 返回实际采样点数; 分配失败返回 0（合法输出恒 ≥1, 调用方以此回退）。
 * x==0.0 控制点替换 0.0001（DEPENCE 同, 替换先于闭合判定）。
 */
static int ce_spline_sample_linear(const double* pts, int n_cp, int per_seg,
                                   double* out_xy, double* out_ang)
{
    int out_i = 0, i, k;
    double da = 0.0;
    for (i = 0; i < n_cp - 1; i++) {
        double ax = pts[2 * i], az = pts[2 * i + 1];
        double bx = pts[2 * i + 2], bz = pts[2 * i + 3];
        da = ce_deg(atan2(bz - az, bx - ax));
        for (k = 0; k < per_seg; k++) {
            double t = (double)k / (double)per_seg;
            out_xy[2 * out_i] = ax + (bx - ax) * t;
            out_xy[2 * out_i + 1] = az + (bz - az) * t;
            out_ang[out_i] = da;
            out_i++;
        }
    }
    out_xy[2 * out_i] = pts[2 * (n_cp - 1)];
    out_xy[2 * out_i + 1] = pts[2 * (n_cp - 1) + 1];
    out_ang[out_i] = da;
    return out_i + 1;
}

static int ce_spline_sample_cubic(const double* pts, int n_cp, int per_seg,
                                  double* out_xy, double* out_ang)
{
    int n = n_cp, N = n_cp - 1, size = N - 1, total = N * per_seg, j, i;
    double h = 1.0 / (double)N;
    double inv_h2 = 6.0 / (h * h);
    double *rx, *rz, *mx, *mz;
    double c0x, c0z;
    if (total < 1)
        return 0;
    rx = (double*)malloc((size_t)n * sizeof(double));
    rz = (double*)malloc((size_t)n * sizeof(double));
    mx = (double*)malloc((size_t)n * sizeof(double));
    mz = (double*)malloc((size_t)n * sizeof(double));
    if (rx == NULL || rz == NULL || mx == NULL || mz == NULL) {
        free(rx); free(rz); free(mx); free(mz);
        return 0;
    }
    for (i = 0; i < n; i++) {
        rx[i] = 0.0; rz[i] = 0.0; mx[i] = 0.0; mz[i] = 0.0;
    }
    /* 右端项 d[i] = 6*(P[i+1] - 2P[i] + P[i-1])/h^2, i = 1..N-1 */
    for (i = 1; i < N; i++) {
        rx[i] = inv_h2 * (pts[2 * (i + 1)] - 2.0 * pts[2 * i] + pts[2 * (i - 1)]);
        rz[i] = inv_h2 * (pts[2 * (i + 1) + 1] - 2.0 * pts[2 * i + 1]
                          + pts[2 * (i - 1) + 1]);
    }
    /* Thomas 追赶法（三对角 1/4/1, 自然边界 M[0]=M[N]=0 隐含） */
    if (size >= 1) {
        double* mu = (double*)malloc((size_t)(size + 2) * sizeof(double));
        double div = 4.0;
        if (mu == NULL) {
            free(rx); free(rz); free(mx); free(mz);
            return 0;
        }
        mx[1] = rx[1] / div;
        mz[1] = rz[1] / div;
        for (i = 2; i <= size; i++) {
            mu[i] = 1.0 / div;
            div = 4.0 - mu[i];
            mx[i] = (rx[i] - mx[i - 1]) / div;
            mz[i] = (rz[i] - mz[i - 1]) / div;
        }
        for (i = size - 1; i >= 1; i--) {
            mx[i] -= mu[i + 1] * mx[i + 1];
            mz[i] -= mu[i + 1] * mz[i + 1];
        }
        free(mu);
    }
    /* 起点 P[0]: S'(0) = (P1-P0)/h - M1*h/6 */
    c0x = (pts[2] - pts[0]) / h - mx[1] * h / 6.0;
    c0z = (pts[3] - pts[1]) / h - mz[1] * h / 6.0;
    out_xy[0] = pts[0];
    out_xy[1] = pts[1];
    out_ang[0] = ce_deg(atan2(c0z, c0x));
    /* 逐点重采样: t = j/total, k = floor(t*N)+1, u = t-(k-1)/N */
    for (j = 1; j < total; j++) {
        double t = (double)j / (double)total;
        int k = (int)floor(t * (double)N) + 1;
        double u, u2, ax2, bx2, cx2, az2, bz2, cz2, x, z, dx, dz;
        if (k > N)
            k = N;
        u = t - (double)(k - 1) / (double)N;
        u2 = u * u;
        ax2 = (mx[k] - mx[k - 1]) / 6.0 / h;
        bx2 = mx[k - 1] / 2.0;
        cx2 = (pts[2 * k] - pts[2 * (k - 1)]) / h
            - (2.0 * mx[k - 1] + mx[k]) * h / 6.0;
        az2 = (mz[k] - mz[k - 1]) / 6.0 / h;
        bz2 = mz[k - 1] / 2.0;
        cz2 = (pts[2 * k + 1] - pts[2 * (k - 1) + 1]) / h
            - (2.0 * mz[k - 1] + mz[k]) * h / 6.0;
        x = ax2 * u2 * u + bx2 * u2 + cx2 * u + pts[2 * (k - 1)];
        z = az2 * u2 * u + bz2 * u2 + cz2 * u + pts[2 * (k - 1) + 1];
        dx = 3.0 * ax2 * u2 + 2.0 * bx2 * u + cx2;
        dz = 3.0 * az2 * u2 + 2.0 * bz2 * u + cz2;
        out_xy[2 * j] = x;
        out_xy[2 * j + 1] = z;
        out_ang[j] = ce_deg(atan2(dz, dx));
    }
    /* 端点 P[N]: 段 N 在 u=h 的解析导数（M[N]=0） */
    {
        double mkx = mx[N - 1], mkz = mz[N - 1];
        double ax2 = (0.0 - mkx) / 6.0 / h;
        double bx2 = mkx / 2.0;
        double cx2 = (pts[2 * N] - pts[2 * (N - 1)]) / h
            - (2.0 * mkx + 0.0) * h / 6.0;
        double az2 = (0.0 - mkz) / 6.0 / h;
        double bz2 = mkz / 2.0;
        double cz2 = (pts[2 * N + 1] - pts[2 * (N - 1) + 1]) / h
            - (2.0 * mkz + 0.0) * h / 6.0;
        double h2 = h * h;
        out_xy[2 * total] = pts[2 * N];
        out_xy[2 * total + 1] = pts[2 * N + 1];
        out_ang[total] = ce_deg(atan2(3.0 * az2 * h2 + 2.0 * bz2 * h + cz2,
                                      3.0 * ax2 * h2 + 2.0 * bx2 * h + cx2));
    }
    free(rx); free(rz); free(mx); free(mz);
    return total + 1;
}

CE_API int ce_spline_sample(const double* cp, int n_cp, int per_seg, int mode,
                            double* out_xy, double* out_ang)
{
    double* pts;
    int i;
    if (cp == NULL || out_xy == NULL || out_ang == NULL)
        return 0;
    if (per_seg < 1)
        per_seg = 1;
    if (n_cp < 2) {
        for (i = 0; i < n_cp; i++) {
            out_xy[2 * i] = cp[2 * i];
            out_xy[2 * i + 1] = cp[2 * i + 1];
            out_ang[i] = 0.0;
        }
        return n_cp;
    }
    pts = (double*)malloc((size_t)n_cp * 2 * sizeof(double));
    if (pts == NULL)
        return 0;
    for (i = 0; i < n_cp; i++) {
        double x = cp[2 * i];
        if (x == 0.0)
            x = 0.0001;                  /* DEPENCE 同: x==0 强制 0.0001 */
        pts[2 * i] = x;
        pts[2 * i + 1] = cp[2 * i + 1];
    }
    /* 闭合折线（首尾重合 <1e-6）或 Linear 模式 → 边直连采样 */
    if (mode == 0 || ce_py_hypot2(pts[2 * (n_cp - 1)] - pts[0],
                                  pts[2 * (n_cp - 1) + 1] - pts[1]) < 1e-6) {
        int ret = ce_spline_sample_linear(pts, n_cp, per_seg, out_xy, out_ang);
        free(pts);
        return ret;
    }
    {
        int ret = ce_spline_sample_cubic(pts, n_cp, per_seg, out_xy, out_ang);
        free(pts);
        return ret;
    }
}

/* ── T10e: 切向→同步角公共段（_spline_cluster_angles 阶段2 :508-:534 /
 *       圆分支 :2937-:2939）
 *
 * (tx,ty) 切向（调用方自 sp_ang 经 cos/sin(radians) 或圆心垂直向量生成）;
 * has_nd=1 时与邻域弦 (ndx,ndy) 点积 <0 则翻转 180°（Python 无邻域时
 * nd=(tx,ty), dot=tx²+ty²≥0 恒不翻转 → 等价于 has_nd=0）;
 * 角 = normalize(-degrees(atan2(ty,tx))); flip_roty=1 再按 RotY [-90,90]
 * 翻转（圆分支无翻转, 传 0）。
 */
CE_API double ce_sync_angle_from_tangent(double tx, double ty,
                                         double ndx, double ndy, int has_nd,
                                         int flip_roty)
{
    double out;
    if (has_nd && tx * ndx + ty * ndy < 0.0) {
        tx = -tx;
        ty = -ty;
    }
    out = ce_norm_angle_deg(-ce_deg(atan2(ty, tx)));
    if (flip_roty) {
        if (out > 90.0)
            out -= 180.0;
        else if (out < -90.0)
            out += 180.0;
    }
    return out;
}


/* ── T9续: 曲线/渐变族逐设备求值（对齐 v22/v28/v30/v31/v32 渐变预设）────────
 * 空间渐变统一入口, kind 0-4, 覆盖 10 个预设:
 *   kind 0 = multi_gradient（对齐 _light_multi_gradient :754 /
 *             _pump_multi_gradient :622 / _motor_multi_gradient :562）:
 *             p = (seq_i/n*segments - t*speed) % 1.0 → 三角波 v = 1-|2p-1|
 *   kind 1 = stair_gradient（对齐 _light_brightness_stair :787 /
 *             _pump_stair_gradient :681 / _motor_stair_gradient :621）:
 *             step = int(seq_i/n*steps), phase = (step/(steps-1)-t*speed)%1.0
 *   kind 2 = center_gradient（对齐 _light_center_gradient :822）:
 *             v = (1-|2p-1|) * _sine(t*speed), p = seq_i/n
 *   kind 3 = phase_breath（对齐 _light_gradient_breath :1391 /
 *             _pump_gradient_rise :1821）: v = _sine(t*speed + seq_i/n*a)
 *   kind 4 = direction_grad（对齐 _fountain_height_gradient :66）:
 *             ratio = seq_i/max(1,n-1), a: 0=左→右 1=右→左 2=中→外 3=外→中
 * 参数: a = segments(kind0)/steps(kind1, 均先 int() 再 max(2,·) 同预设)/
 *   phase_offset(kind3)/direction(kind4); lo/hi 为输出值域参数（非钳位界）;
 * 输出钳位恒为 _clamp(v)=max(0,min(255,int(v)))（同各预设 _clamp 默认界）。
 * 位级纪律: 全程 double; x % 1.0 用 _ce_mod_div（负参返回非负, 同 CPython
 *   float_rem）; _sine(x)=(sin(2πx)+1)/2, sin 参数左结合对齐 Python
 *   （t*speed+offset → (t*speed)+offset）; int() 截断用 (long long) 强转
 *   （val 恒在 [min(lo,hi),max(lo,hi)]⊆[-255,510] 内, 无 UB）;
 *   n<=0 早退（同预设 if n==0: return）。
 */
CE_API void ce_gradient_effect(
    int kind, double t, double speed, double a,
    int lo, int hi, int n, uint8_t* out)
{
    if (n <= 0)
        return;
    for (int i = 0; i < n; i++) {
        double val;
        switch (kind) {
        case 0: {  /* multi_gradient: 三角波多段渐变 */
            long long segi = (long long)a;          /* max(2, int(segments)) */
            if (segi < 2) segi = 2;
            double seg = (double)segi;
            double p = _ce_mod_div((double)i / (double)n * seg - t * speed, 1.0);
            double v = 1.0 - fabs(2.0 * p - 1.0);
            val = (double)lo + v * (double)(hi - lo);
            break;
        }
        case 1: {  /* stair_gradient: 阶梯渐变 */
            long long sti = (long long)a;           /* max(2, int(steps)) */
            if (sti < 2) sti = 2;
            double st = (double)sti;
            long long step = (long long)((double)i / (double)n * st);
            double v = (double)step / (st - 1.0);
            double phase = _ce_mod_div(v - t * speed, 1.0);
            val = (double)lo + phase * (double)(hi - lo);
            break;
        }
        case 2: {  /* center_gradient: 中心渐变呼吸 */
            double pulse = (sin(2.0 * M_PI * (t * speed)) + 1.0) / 2.0;
            double p = (double)i / (double)n;
            double dist = fabs(p - 0.5) * 2.0;
            double v = (1.0 - dist) * pulse;
            val = (double)lo + v * (double)(hi - lo);
            break;
        }
        case 3: {  /* phase_breath: 相位偏移呼吸 */
            double offset = (double)i / (double)n * a;
            double s = sin(2.0 * M_PI * (t * speed + offset));
            double v = (s + 1.0) / 2.0;
            val = (double)lo + v * (double)(hi - lo);
            break;
        }
        case 4: {  /* direction_grad: 静态方向渐变 */
            double denom = (n > 1) ? (double)(n - 1) : 1.0;
            double ratio = (double)i / denom;
            switch ((int)a) {
            case 1:  ratio = 1.0 - ratio; break;              /* 右→左 */
            case 2:  ratio = fabs(2.0 * ratio - 1.0); break;  /* 中→外 */
            case 3:  ratio = 1.0 - fabs(2.0 * ratio - 1.0); break;  /* 外→中 */
            default: break;                                   /* 左→右 */
            }
            val = (double)lo + ratio * (double)(hi - lo);
            break;
        }
        default:  /* 未知 kind: 恒 lo（无对应预设, 防御兜底） */
            val = (double)lo;
            break;
        }
        /* Python int()+clamp 语义: int() 截断 → min(255) → max(0) */
        double _dv = val;

        int iv;

        if (_dv <= 0.0) iv = 0;        /* B2-2 值域先判断 */

        else if (_dv >= 255.0) iv = 255;

        else iv = (int)_dv;
        out[i] = (uint8_t)iv;
    }
}

/* ── T9续: 颜色渐变族逐设备求值（对齐 v22/v24/v26 彩色渐变预设）────────────
 * 统一入口, kind 0-3, 输出 R/G/B 三通道（三路输出, 同 ce_chase_effect）:
 *   kind 0 = rainbow（对齐 _color_gradient_rainbow :860）: 饱和/亮度可调,
 *             h=hue*6, c=s*b, x=c*(1-|h%2-1|), m=b-c, 六扇区
 *   kind 1 = hue_chase（对齐 _light_gradient_chase :233）: 色相追逐渐变,
 *             h6=hue*6, hi_h=int(h6)%6, f=h6-int(h6), 六扇区
 *   kind 2 = wave（对齐 _light_color_gradient_wave :1355）: 4 色板
 *             (0=rainbow 1=warm 2=cool 3=fire, 其它=rainbow), _hsv_to_rgb
 *   kind 3 = matrix2d（对齐 _light_color_matrix_gradient :1452）: 行列双轴
 *             相位合成 hue, _hsv_to_rgb(hue, 1.0, 1.0)
 * 参数槽 p[3]: p[0]=repeats(kind0)/hue_range(kind1)/palette(kind2)/cols(kind3);
 *   p[1]=saturation p[2]=brightness（仅 kind0）。
 * 位级纪律: 全程 double; x % m 用 _ce_mod_div（模恒正）/ _ce_int_mod（int）;
 *   int() 截断用 (long long) 强转; h % 2 用 _ce_mod_div 逐位复刻 float_rem;
 *   输出 _clamp(ch*255) = int() 截断 → min(255) → max(0); sin 参数左结合对齐。
 */

/* _hsv_to_rgb :1378 逐位复刻（s==0 分支保留; i%6 用 _ce_int_mod） */
static void _ce_hsv_to_rgb(double h, double s, double v,
                           double* out_r, double* out_g, double* out_b)
{
    if (s == 0.0) {
        *out_r = v;
        *out_g = v;
        *out_b = v;
        return;
    }
    long long i = (long long)(h * 6.0);
    double f = h * 6.0 - (double)i;
    double p = v * (1.0 - s);
    double q = v * (1.0 - s * f);
    double t_val = v * (1.0 - s * (1.0 - f));
    i = _ce_int_mod(i, 6);
    if (i == 0)      { *out_r = v; *out_g = t_val; *out_b = p; }
    else if (i == 1) { *out_r = q; *out_g = v; *out_b = p; }
    else if (i == 2) { *out_r = p; *out_g = v; *out_b = t_val; }
    else if (i == 3) { *out_r = p; *out_g = q; *out_b = v; }
    else if (i == 4) { *out_r = t_val; *out_g = p; *out_b = v; }
    else             { *out_r = v; *out_g = p; *out_b = q; }
}

CE_API void ce_color_gradient_effect(
    int kind, double t, double speed, const double* p,
    int n, uint8_t* out_r, uint8_t* out_g, uint8_t* out_b)
{
    if (n <= 0)
        return;
    for (int i = 0; i < n; i++) {
        double r, g, bl;
        switch (kind) {
        case 0: {  /* rainbow: Depence 风格渐变彩虹 */
            double hue = _ce_mod_div((double)i / (double)n * p[0] + t * speed, 1.0);
            double h = hue * 6.0;
            double c = p[1] * p[2];
            double x = c * (1.0 - fabs(_ce_mod_div(h, 2.0) - 1.0));
            double m = p[2] - c;
            if (h < 1.0)      { r = c; g = x; bl = 0.0; }
            else if (h < 2.0) { r = x; g = c; bl = 0.0; }
            else if (h < 3.0) { r = 0.0; g = c; bl = x; }
            else if (h < 4.0) { r = 0.0; g = x; bl = c; }
            else if (h < 5.0) { r = x; g = 0.0; bl = c; }
            else              { r = c; g = 0.0; bl = x; }
            r += m;
            g += m;
            bl += m;
            break;
        }
        case 1: {  /* hue_chase: 色相渐变追逐 */
            double hue = _ce_mod_div(t * speed + (double)i / (double)n * p[0], 1.0);
            double h6 = hue * 6.0;
            long long hi_h = (long long)h6;      /* int(h6) */
            long long sec = hi_h % 6;            /* % 6, h6∈[0,6) 恒非负 */
            double f = h6 - (double)hi_h;
            if (sec == 0)      { r = 1.0; g = f; bl = 0.0; }
            else if (sec == 1) { r = 1.0 - f; g = 1.0; bl = 0.0; }
            else if (sec == 2) { r = 0.0; g = 1.0; bl = f; }
            else if (sec == 3) { r = 0.0; g = 1.0 - f; bl = 1.0; }
            else if (sec == 4) { r = f; g = 0.0; bl = 1.0; }
            else               { r = 1.0; g = 0.0; bl = 1.0 - f; }
            break;
        }
        case 2: {  /* wave: 颜色渐变波（4 色板） */
            double progress = (double)i / (double)((n > 1) ? (n - 1) : 1);
            double hue = _ce_mod_div(t * speed + progress, 1.0);
            int pal = (int)p[0];
            if (pal == 1) {        /* warm */
                _ce_hsv_to_rgb(hue * 0.15, 1.0, 1.0, &r, &g, &bl);
            } else if (pal == 2) { /* cool */
                _ce_hsv_to_rgb(0.5 + hue * 0.2, 1.0, 1.0, &r, &g, &bl);
            } else if (pal == 3) { /* fire */
                r = 1.0;
                if (hue < 0.5) {
                    double gv = hue * 2.0 - 0.5;
                    g = (gv > 0.0) ? gv : 0.0;   /* max(0, ·) */
                } else {
                    g = 1.0 - (hue - 0.5) * 2.0;
                }
                bl = 0.0;
            } else {               /* rainbow(默认) */
                _ce_hsv_to_rgb(hue, 1.0, 1.0, &r, &g, &bl);
            }
            break;
        }
        case 3: {  /* matrix2d: 颜色矩阵渐变 */
            int cols = (int)p[0];
            if (cols < 1) cols = 1;               /* max(1, int(cols)) */
            int rows = (n + cols - 1) / cols;     /* max(1, (n+cols-1)//cols) */
            if (rows < 1) rows = 1;
            int row = i / cols;
            int col = i % cols;
            double hue = _ce_mod_div(
                t * speed + (double)col / (double)cols * 0.5
                + (double)row / (double)rows * 0.5, 1.0);
            _ce_hsv_to_rgb(hue, 1.0, 1.0, &r, &g, &bl);
            break;
        }
        default:   /* 未知 kind: 全 0（无对应预设, 防御兜底） */
            r = 0.0;
            g = 0.0;
            bl = 0.0;
            break;
        }
        /* _clamp(ch*255): int() 截断 → min(255) → max(0) */
        long long ir = (long long)(r * 255.0);
        if (ir > 255) ir = 255;
        if (ir < 0) ir = 0;
        long long ig = (long long)(g * 255.0);
        if (ig > 255) ig = 255;
        if (ig < 0) ig = 0;
        long long ib = (long long)(bl * 255.0);
        if (ib > 255) ib = 255;
        if (ib < 0) ib = 0;
        out_r[i] = (uint8_t)ir;
        out_g[i] = (uint8_t)ig;
        out_b[i] = (uint8_t)ib;
    }
}
/* ── T8续: auto_arrange 总指挥/喷泉编导数值内核（对齐 arrangement_director.py +
 * fountain_arranger.py 纯计算段, 不接 hook）──────────────────────────────
 * 家族 C: FountainArranger 设备速度带（fountain_arranger.py arrange :246-266）—
 *   ce_orch_speed_band = 4 段能量线性插值 + 打击/旋律倍率（device_speeds 数值源）
 * 家族 D: ArrangementDirector 选组评分（arrangement_director.py
 *   _pick_groups_for_lead :236-245 / _pick_groups_for_support :250-251）——
 *   ce_orch_group_select = 评分 + 稳定降序选前 k + 过滤 group_id
 *
 * 形状编码: 0=其他 1=line 2=ring（Python 字符串 shape 由调用方映射后传入）
 * 参数映射（逐字对齐 Python 字面量）:
 *   speed_band: 严格 < 分支 0.30/0.55/0.75（NaN/±inf 落入 else, 同 Python 链式
 *     比较）; else 分支 min(t_energy, 1.0) 用 _ce_min_py —— NaN 首参胜出,
 *     同 CPython 内建 min; 倍率 1.0+perc*0.5 / 1.0+melodic*1.0 左结合。
 *   group_select: score = (ring+center)+size_pen 左结合; size_pen =
 *     -0.001*max(0, count-36); 键降序稳定取前 k（lead=2, support=1）——
 *     按原下标递增单遍扫描, 等键恒保留先到者 = Python 稳定排序 reverse=True
 *     的头部 k 个; 再过滤 has_id —— 同 [g for g in groups[:k] if g.group_id],
 *     非"前 k 个有效 id"。
 * 位级纪律: 全程 double 串行, /fp:precise 禁 FMA; 排序键域无 NaN
 *   （score 由有限常量+int 构成, count 键为 int 精确转 double）。 */

CE_API double ce_orch_speed_band(double energy, double percussive, double melodic,
                                 double speed_lo, double speed_mid, double speed_hi,
                                 double speed_peak,
                                 int responds_to_percussion, int responds_to_melody)
{
    double base_speed;
    if (energy < 0.30) {
        base_speed = speed_lo;
    } else if (energy < 0.55) {
        double t_energy = (energy - 0.30) / 0.25;
        base_speed = speed_lo + (speed_mid - speed_lo) * t_energy;
    } else if (energy < 0.75) {
        double t_energy = (energy - 0.55) / 0.20;
        base_speed = speed_mid + (speed_hi - speed_mid) * t_energy;
    } else {
        double t_energy = (energy - 0.75) / 0.25;
        base_speed = speed_hi + (speed_peak - speed_hi) * _ce_min_py(t_energy, 1.0);
    }
    if (responds_to_percussion)
        base_speed *= 1.0 + percussive * 0.5;
    if (responds_to_melody)
        base_speed *= 1.0 + melodic * 1.0;
    return base_speed;
}

CE_API int ce_orch_group_select(const int* shapes, const int* has_center,
                                const int* counts, const int* has_id,
                                int n_groups, int mode, int* out_idx)
{
    /* mode 0 = lead（评分键, 选前 2）; mode 1 = support（count 键, 选前 1） */
    if (n_groups <= 0)
        return 0;
    const int k = (mode == 1) ? 1 : 2;
    int best0 = 0, best1 = -1;
    double key0 = 0.0, key1 = 0.0;
    for (int i = 0; i < n_groups; i++) {
        double key;
        if (mode == 1) {
            key = (double)counts[i];
        } else {
            double ring = (shapes[i] == 2) ? 2.0 : ((shapes[i] == 1) ? 1.0 : 0.0);
            double center = has_center[i] ? 1.0 : 0.0;
            int size_v = (counts[i] > 36) ? (counts[i] - 36) : 0;
            key = (ring + center) + (-0.001 * (double)size_v);
        }
        if (i == 0) {
            key0 = key;
        } else if (key > key0) {
            key1 = key0;
            best1 = best0;
            key0 = key;
            best0 = i;
        } else if (best1 < 0) {
            key1 = key;
            best1 = i;
        } else if (key > key1) {
            key1 = key;
            best1 = i;
        }
    }
    int out_n = 0;
    if (has_id[best0])
        out_idx[out_n++] = best0;
    if (k > 1 && best1 >= 0 && has_id[best1])
        out_idx[out_n++] = best1;
    return out_n;
}

/* ── T8续2: 喷泉编导设备排序内核（fountain_arranger.py
 * _rank_devices_for_energy :328-370, 不接 hook）──────────────────────────
 * 家族 E: ce_rank_devices = 固定 6 设备激活优先级计算 + 稳定降序排序,
 *   输出设备下标序（0=数控 1=泵 2=阀 3=LED灯组|RGB 4=喷泉RGB 5=气爆,
 *   = PERSONALITIES.items() 插入序）。
 *
 * 参数映射（逐字对齐 Python :337-364, 严格 > 比较, NaN 落入 else 分支
 *   同 Python 链式比较）:
 *   "RGB"/"LED" 名设备（下标 3/4）恒 10.0
 *   数控: energy > 0.15 → 8.0, 否则 3.0
 *   泵:   energy > 0.25 → 7.0, 否则 1.0
 *   阀:   energy > 0.30 → 6.0, 否则 1.0
 *   气爆: energy > 0.35 → 5.0, 否则 0.0
 *   percussive > 0.70 且响应打击 → +1.5; melodic > 0.15 且响应旋律 → +1.5
 *   （响应表 = 6 人格 responds_to_percussion/responds_to_melody 实值）。
 * 排序 = 稳定降序: 严格 < 才换位的插入排序, 同 Python stable sort
 *   reverse=True; 键域 = 精确半整数常数和, 无 NaN 无舍入。
 * 注意: PERSONALITIES 现为固定 6 键, 若增减设备键需同步本内核。 */
CE_API int ce_rank_devices(double energy, double percussive, double melodic,
                           int* out_order)
{
    /* 0数控 1泵 2阀 3LED灯组|RGB 4喷泉RGB 5气爆 */
    static const int resp_perc[6] = {0, 1, 1, 0, 0, 1};
    static const int resp_mel[6]  = {1, 0, 0, 0, 1, 0};
    double key[6];
    int order[6];
    for (int i = 0; i < 6; i++) {
        double p;
        if (i == 3 || i == 4) {
            p = 10.0;
        } else if (i == 0) {
            p = (energy > 0.15) ? 8.0 : 3.0;
        } else if (i == 1) {
            p = (energy > 0.25) ? 7.0 : 1.0;
        } else if (i == 2) {
            p = (energy > 0.30) ? 6.0 : 1.0;
        } else {
            p = (energy > 0.35) ? 5.0 : 0.0;
        }
        if (percussive > 0.70 && resp_perc[i])
            p += 1.5;
        if (melodic > 0.15 && resp_mel[i])
            p += 1.5;
        key[i] = p;
        order[i] = i;
    }
    for (int i = 1; i < 6; i++) {
        int t = order[i];
        double tk = key[t];
        int j = i - 1;
        while (j >= 0 && key[order[j]] < tk) {
            order[j + 1] = order[j];
            j--;
        }
        order[j + 1] = t;
    }
    for (int i = 0; i < 6; i++)
        out_order[i] = order[i];
    return 6;
}

/* ═══════════════════════════════════════════════════════════════════════
 * T8: 工程保存序列化 — JSON 字节生成（对齐 src/core/models.py Project.save
 *     → json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)）
 *
 * 边界: 模型→dict→token 流（对象遍历/键名转换/UTF-8 编码）留在 Python;
 *       dict→字节 全部在 C（浮点 repr、字符串转义、缩进/换行结构、整型直拷）。
 *
 * 位级等价纪律（逐字节复刻 CPython 3.13 _json.c, indent>=0 模式）:
 *   1. ce_fmt_double = float.__repr__: %.{p-1}e 最短往返扫描（p=17→1,
 *      strtod 校验; 17 位十进制必可往返任意有限 double）; 指数 e0<-4 或
 *      e0>=16 → 科学计数（指数 ≥2 位, 无前导零）; 否则十进制（整数倍补 .0）;
 *      -0.0 保符号; nan/inf 由调用方转 JSON 常量。
 *   2. 缩进结构: 容器项缩进 = indent×(层级+1), 闭合缩进 = indent×层级;
 *      空容器内联 "{}"/"[]"; 项分隔 = ",\n"+缩进; 键分隔 = ": "。
 *   3. 字符串转义: '"'→\"  '\\'→\\\\  0x08/09/0A/0C/0D → \b\t\n\f\r
 *      其他 <0x20 → \u00xx（小写 hex）; 其余字节原样——含孤立代理项:
 *      实测 3.13 的 ensure_ascii=False 不做回退转义, 而是把代理项码元
 *      原样输出（= surrogatepass/WTF-8 编码字节, 本机实证 '"\ud800"'
 *      长度为 3 码元）; 载荷即 Python 侧 utf-8/surrogatepass 编码字节。
 *   4. token 流契约: kinds[n] u8 + offs[n+1] i64（载荷区间）+ payload
 *      连续字节; FLOAT 载荷 = 8 字节 little-endian IEEE754。
 * ═══════════════════════════════════════════════════════════════════════ */

#define CE_JSON_OBJ_OPEN  0
#define CE_JSON_OBJ_CLOSE 1
#define CE_JSON_ARR_OPEN  2
#define CE_JSON_ARR_CLOSE 3
#define CE_JSON_KEY       4   /* 载荷: UTF-8/surrogatepass 字节 */
#define CE_JSON_STR       5
#define CE_JSON_INT       6   /* 载荷: 十进制 ASCII */
#define CE_JSON_FLOAT     7   /* 载荷: 8B little-endian IEEE754 */
#define CE_JSON_TRUE      8
#define CE_JSON_FALSE     9
#define CE_JSON_NULL      10

/* ── T8a: float.__repr__ 最短往返格式化（Python repr 风格）────────────
 * 算法: 谓词 P(p) = "%.{p-1}e 正确舍入串 strtod 往返" 单调（p 位正确
 * 舍入值随 p 增大单调更近 x）→ 二分 [1,16] 找最小 p（P(17) 恒真不用探测,
 * p=1 快速路径先行）; 最短串 = 最近舍入 = dtoa 最短模式一致。
 * 返回写入字节数（不含 NUL, buf 恒 NUL 结尾）; cap 不足 → -1。
 */
CE_API int ce_fmt_double(double x, char* buf, size_t cap)
{
    char tmp[48];
    if (isnan(x)) {
        if (cap < 4) return -1;
        memcpy(buf, "nan", 3);
        buf[3] = '\0';
        return 3;
    }
    if (isinf(x)) {
        if (x > 0.0) {
            if (cap < 4) return -1;
            memcpy(buf, "inf", 3);
            buf[3] = '\0';
            return 3;
        }
        if (cap < 5) return -1;
        memcpy(buf, "-inf", 4);
        buf[4] = '\0';
        return 4;
    }
    if (x == 0.0) {
        int l = signbit(x) ? 4 : 3;
        if (cap < (size_t)l + 1) return -1;
        memcpy(buf, (l == 4) ? "-0.0" : "0.0", (size_t)l);
        buf[l] = '\0';
        return l;
    }
    /* 最短往返: 谓词 P(p) = "%.{p-1}e 正确舍入串 strtod 往返" 单调
     * （p 位正确舍入值随 p 增大单调更近 x）→ 二分 [1,16] 找最小 p;
     * P(17) 恒真（IEEE: 17 位十进制必可往返）故不用探测。线性 17 次
     * snprintf+strtod 实测 16.9µs/值, 二分 ~4 次 → ~4µs/值。
     * 最后一次成功探测的串即最终正确舍入串, 直接复用（省一次 snprintf）。 */
    int best = 17;
    if (snprintf(tmp, sizeof(tmp), "%.0e", x) > 0
        && strtod(tmp, NULL) == x) {
        best = 1;
    } else {
        int lo = 1, hi = 16;
        while (lo <= hi) {
            int mid = lo + (hi - lo) / 2;
            snprintf(tmp, sizeof(tmp), "%.*e", mid - 1, x);
            if (strtod(tmp, NULL) == x) {
                best = mid;
                hi = mid - 1;
            } else {
                lo = mid + 1;
            }
        }
        snprintf(tmp, sizeof(tmp), "%.*e", best - 1, x);
    }
    /* tmp = [-]d.dddddde±XX */
    const char* epos = strchr(tmp, 'e');
    int neg = (tmp[0] == '-');
    const char* d = tmp + (neg ? 1 : 0);
    char digs[32];
    int nd = 0;
    for (const char* q = d; q < epos; q++)
        if (*q != '.')
            digs[nd++] = *q;
    while (nd > 1 && digs[nd - 1] == '0')   /* 防御: 最短表示不应有尾零 */
        nd--;
    int e0 = atoi(epos + 1);
    char out[40];
    size_t o = 0;
    if (neg)
        out[o++] = '-';
    if (e0 < -4 || e0 >= 16) {
        out[o++] = digs[0];
        if (nd > 1) {
            out[o++] = '.';
            for (int q = 1; q < nd; q++)
                out[o++] = digs[q];
        }
        out[o++] = 'e';
        int ae = (e0 < 0) ? -e0 : e0;
        out[o++] = (e0 < 0) ? '-' : '+';
        if (ae < 10) out[o++] = '0';
        if (ae >= 100) out[o++] = (char)('0' + ae / 100);
        if (ae >= 10) out[o++] = (char)('0' + (ae / 10) % 10);
        out[o++] = (char)('0' + ae % 10);
    } else if (e0 < 0) {
        out[o++] = '0';
        out[o++] = '.';
        for (int q = 0; q < -e0 - 1; q++)
            out[o++] = '0';
        for (int q = 0; q < nd; q++)
            out[o++] = digs[q];
    } else {
        int ipos = e0 + 1;
        if (ipos >= nd) {
            for (int q = 0; q < nd; q++)
                out[o++] = digs[q];
            for (int q = 0; q < ipos - nd; q++)
                out[o++] = '0';
            out[o++] = '.';
            out[o++] = '0';
        } else {
            for (int q = 0; q < ipos; q++)
                out[o++] = digs[q];
            out[o++] = '.';
            for (int q = ipos; q < nd; q++)
                out[o++] = digs[q];
        }
    }
    if (o + 1 > cap)
        return -1;
    memcpy(buf, out, o);
    buf[o] = '\0';
    return (int)o;
}

/* ── T8b: JSON 编码器（indent>=0 模式, ensure_ascii=False）────────────
 * measure 与 encode 共用同一遍历（out==NULL = 只计长）→ 长度零漂移。
 */
typedef struct {
    const uint8_t* kinds;
    const int64_t* offs;
    const uint8_t* payload;
    int n_tok;
    int indent;
} ce_json_t;

static void _ce_json_put(char* out, int64_t* pos, char c)
{
    if (out != NULL) out[*pos] = c;
    *pos += 1;
}

static void _ce_json_putn(char* out, int64_t* pos, const char* s, int64_t n)
{
    if (out != NULL) memcpy(out + *pos, s, (size_t)n);
    *pos += n;
}

static void _ce_json_indent(ce_json_t* j, char* out, int64_t* pos, int level)
{
    int64_t n = (int64_t)j->indent * level;
    if (n > 0) {
        if (out != NULL) memset(out + *pos, ' ', (size_t)n);
        *pos += n;
    }
}

static char _ce_json_hex(int v)
{
    return "0123456789abcdef"[v & 0xF];
}

/* 控制字符 → 转义串（\b\t\n\f\r 或 \u00xx 小写 hex）; 返回写入字节数 */
static int64_t _ce_json_small_esc(uint8_t c, char* out)
{
    switch (c) {
        case 0x08: out[0] = '\\'; out[1] = 'b'; return 2;
        case 0x09: out[0] = '\\'; out[1] = 't'; return 2;
        case 0x0A: out[0] = '\\'; out[1] = 'n'; return 2;
        case 0x0C: out[0] = '\\'; out[1] = 'f'; return 2;
        case 0x0D: out[0] = '\\'; out[1] = 'r'; return 2;
        default:
            out[0] = '\\'; out[1] = 'u'; out[2] = '0'; out[3] = '0';
            out[4] = _ce_json_hex(c >> 4);
            out[5] = _ce_json_hex(c);
            return 6;
    }
}

/* 转义串长度（控制字符/引号/反斜杠转义, 其余字节原样） */
static int64_t _ce_json_esc_len(const uint8_t* p, int64_t n)
{
    int64_t len = 0;
    for (int64_t i = 0; i < n; i++) {
        uint8_t c = p[i];
        if (c == '"' || c == '\\') {
            len += 2;
        } else if (c < 0x20) {
            char esc[8];
            len += _ce_json_small_esc(c, esc);
        } else {
            len += 1;
        }
    }
    return len;
}

/* 引号包裹的转义串; out==NULL 只计长（与 encode 同一实现, 零漂移） */
static void _ce_json_emit_escaped(ce_json_t* j, int64_t i,
                                  char* out, int64_t* pos)
{
    const uint8_t* p = j->payload + j->offs[i];
    int64_t n = j->offs[i + 1] - j->offs[i];
    _ce_json_put(out, pos, '"');
    for (int64_t q = 0; q < n; q++) {
        uint8_t c = p[q];
        if (c == '"' || c == '\\') {
            char e[2] = { '\\', (char)c };
            _ce_json_putn(out, pos, e, 2);
        } else if (c < 0x20) {
            char e[8];
            _ce_json_putn(out, pos, e, _ce_json_small_esc(c, e));
        } else {
            _ce_json_put(out, pos, (char)c);
        }
    }
    _ce_json_put(out, pos, '"');
}

/* 单 token 或容器子树; 返回下一 token 下标（-1 非法流/超深） */
static int64_t _ce_json_emit(ce_json_t* j, int64_t i, int level,
                             char* out, int64_t* pos)
{
    if (i < 0 || i >= j->n_tok || level > 512)
        return -1;
    uint8_t k = j->kinds[i];
    if (k == CE_JSON_OBJ_OPEN || k == CE_JSON_ARR_OPEN) {
        int is_obj = (k == CE_JSON_OBJ_OPEN);
        int close_k = is_obj ? CE_JSON_OBJ_CLOSE : CE_JSON_ARR_CLOSE;
        int64_t next = i + 1;
        if (next < j->n_tok && j->kinds[next] == close_k) {
            _ce_json_putn(out, pos, is_obj ? "{}" : "[]", 2);
            return next + 1;
        }
        _ce_json_put(out, pos, is_obj ? '{' : '[');
        _ce_json_put(out, pos, '\n');
        _ce_json_indent(j, out, pos, level);
        int64_t ii = next;
        int first = 1;
        while (ii < j->n_tok && j->kinds[ii] != close_k) {
            if (!first) {
                _ce_json_put(out, pos, ',');
                _ce_json_put(out, pos, '\n');
                _ce_json_indent(j, out, pos, level);
            }
            first = 0;
            if (is_obj) {
                if (j->kinds[ii] != CE_JSON_KEY)
                    return -1;
                _ce_json_emit_escaped(j, ii, out, pos);
                _ce_json_putn(out, pos, ": ", 2);
                ii = _ce_json_emit(j, ii + 1, level + 1, out, pos);
            } else {
                ii = _ce_json_emit(j, ii, level + 1, out, pos);
            }
            if (ii < 0)
                return -1;
        }
        if (ii >= j->n_tok)
            return -1;
        _ce_json_put(out, pos, '\n');
        if (level > 0)
            _ce_json_indent(j, out, pos, level - 1);
        _ce_json_put(out, pos, (char)(is_obj ? '}' : ']'));
        return ii + 1;
    }
    switch (k) {
    case CE_JSON_KEY:
        return -1;                       /* KEY 只能出现在 dict 项首 */
    case CE_JSON_STR:
        _ce_json_emit_escaped(j, i, out, pos);
        return i + 1;
    case CE_JSON_INT: {
        int64_t n = j->offs[i + 1] - j->offs[i];
        _ce_json_putn(out, pos, (const char*)(j->payload + j->offs[i]), n);
        return i + 1;
    }
    case CE_JSON_FLOAT: {
        double x;
        char fb[40];
        memcpy(&x, j->payload + j->offs[i], 8);
        if (isnan(x)) {
            _ce_json_putn(out, pos, "NaN", 3);
        } else if (isinf(x)) {
            if (x > 0.0)
                _ce_json_putn(out, pos, "Infinity", 8);
            else
                _ce_json_putn(out, pos, "-Infinity", 9);
        } else {
            int fl = ce_fmt_double(x, fb, sizeof(fb));
            if (fl < 0)
                return -1;
            _ce_json_putn(out, pos, fb, fl);
        }
        return i + 1;
    }
    case CE_JSON_TRUE:
        _ce_json_putn(out, pos, "true", 4);
        return i + 1;
    case CE_JSON_FALSE:
        _ce_json_putn(out, pos, "false", 5);
        return i + 1;
    case CE_JSON_NULL:
        _ce_json_putn(out, pos, "null", 4);
        return i + 1;
    default:
        return -1;
    }
}

/* 返回序列化总字节数; 非法 token 流/参数 → -1（indent 0..64） */
CE_API int64_t ce_json_measure(const uint8_t* kinds, const int64_t* offs,
                               const uint8_t* payload, int n_tok, int indent)
{
    if (n_tok <= 0 || kinds == NULL || offs == NULL || payload == NULL)
        return -1;
    if (indent < 0 || indent > 64)
        return -1;
    ce_json_t j = { kinds, offs, payload, n_tok, indent };
    int64_t pos = 0;
    int64_t end = _ce_json_emit(&j, 0, 1, NULL, &pos);
    if (end != n_tok)
        return -1;
    return pos;
}

/* 写入 out（容量须 ≥ ce_json_measure 返回值）; 返回写入字节数, 失败 -1 */
CE_API int64_t ce_json_encode(const uint8_t* kinds, const int64_t* offs,
                              const uint8_t* payload, int n_tok, int indent,
                              char* out)
{
    if (n_tok <= 0 || kinds == NULL || offs == NULL || payload == NULL
        || out == NULL)
        return -1;
    if (indent < 0 || indent > 64)
        return -1;
    ce_json_t j = { kinds, offs, payload, n_tok, indent };
    int64_t pos = 0;
    int64_t end = _ce_json_emit(&j, 0, 1, out, &pos);
    if (end != n_tok)
        return -1;
    return pos;
}

/* ── T10续: 曲线族遗留 4 预设逐设备求值（v22/v25/v35/v36, 纯计算段不接 hook;
 *    合入由主分支统一做）─────────────────────────────────────────────
 * 两个统一入口（同 ce_gradient_effect/ce_color_gradient_effect 双内核模式）:
 *   ce_ramp_effect    kind 0 = strobe_ramp            (对齐 effect_presets_v22.py :1500)
 *                     kind 1 = light_breath_curves    (对齐 effect_presets_v25.py :820)
 *   ce_fan_cue_effect kind 0 = motor_fan_gradient_open(对齐 effect_presets_v35.py :2869)
 *                     kind 1 = motor_cue_speed_ramp   (对齐 effect_presets_v36.py :1735)
 * 参数槽 p[16]（每 kind 布局见各函数注释）; 输出按 seq 序 i=0..n-1。
 * 位级纪律: 全程 double; x % m 用 _ce_mod_div（负参非负, 同 CPython float_rem）;
 *   v22/v25 输出 _clamp = int() 截断（_ce_clamp_trunc）; v35/v36 输出 _clamp =
 *   int(round()) 半偶（_ce_clamp_round, 同 Python round）; v25 _sine =
 *   (sin(2πx)+1)/2（0..1 波形）; v35 用 -1..+1 专用表 _ce_wave_v35（与 ce_wave
 *   的 0..1 表不同）; v25 `raw ** 0.5` 走 _ce_pow_ucrt（CPython float_pow 解析
 *   到 ucrtbase pow, 探针 20 万 0 差异, 非 sqrt）; exp 用 CRT（同 math.exp,
 *   探针 20 万 0 差异）; sin/exp 参数左结合对齐 Python 表达式树; clamp 先值域
 *   早退再截断/舍入（同 ce_fader_effect, 避免 C (int) 超 int32 UB）; n<=0 早退。
 */

/* int() 截断 → clamp[0,255]（同 v22/v25 _clamp: max(0, min(255, int(v)))） */
static long long _ce_clamp_trunc(double v)
{
    if (v <= 0.0)
        return 0;
    if (v >= 255.0)
        return 255;
    return (long long)v;
}

/* int(round(v)) 半偶 → clamp[0,255]（同 v35/v36 _clamp:
 * max(0, min(255, int(round(v))))）; 值域早退同 _ce_clamp_trunc */
static long long _ce_clamp_round(double v)
{
    if (v <= 0.0)
        return 0;
    if (v >= 255.0)
        return 255;
    return (long long)_ce_round_half_even(v);
}

/* v35 专用 -1..+1 波形表（对齐 effect_presets_v35.py _wave :60; 与 ce_wave
 * 的 0..1 表不同）。wtype: 0=正弦 1=三角 2=锯齿上 3=锯齿下 4=弹跳 5=脉冲,
 * 其它=正弦兜底（同 Python else 分支）。x % 1.0 用 _ce_mod_div。 */
static double _ce_wave_v35(double x, int wtype)
{
    switch (wtype) {
    case 0: return sin(x * 2.0 * M_PI);                  /* _sine_raw */
    case 1: {                                            /* _tri_raw */
        double m = _ce_mod_div(x, 1.0);
        return 2.0 * fabs(2.0 * m - 1.0) - 1.0;
    }
    case 2: return 2.0 * _ce_mod_div(x, 1.0) - 1.0;      /* _saw_raw */
    case 3: return -(2.0 * _ce_mod_div(x, 1.0) - 1.0);   /* -_saw_raw */
    case 4: {                                            /* _bounce_raw */
        double m = _ce_mod_div(x, 1.0);
        double v = 1.0 - fabs(2.0 * m - 1.0);
        return 2.0 * v * v * (3.0 - 2.0 * v) - 1.0;
    }
    case 5: return (_ce_mod_div(x, 1.0) < 0.5) ? 1.0 : -1.0;  /* _pulse_raw(0.5) */
    default: return sin(x * 2.0 * M_PI);                 /* 默认正弦 */
    }
}

/* 灯光 ramp/曲线族: kind 0 = strobe_ramp（渐变频闪, 全体同值）; kind 1 =
 * light_breath_curves（4 曲线 × 4 延迟模式）。参数槽:
 *   kind 0: p[0]=freq_lo p[1]=freq_hi p[2]=period p[3]=hi p[4]=lo
 *   kind 1: p[0]=speed p[1]=curve(0=sine 1=ease 2=exponential 3=square_breath)
 *           p[2]=delay_mode(0=none 1=cascade 2=reverse 3=center) */
CE_API void ce_ramp_effect(
    int kind, double t, const double* p,
    int n, uint8_t* out)
{
    if (n <= 0)
        return;
    switch (kind) {
    case 0: {  /* strobe_ramp: 频率随时间线性渐变（v22 :1500） */
        double freq_lo = p[0], freq_hi = p[1], period = p[2];
        long long hi = (long long)p[3], lo = (long long)p[4];
        double ramp = _ce_mod_div(t, period) / period;
        double freq = freq_lo + ramp * (freq_hi - freq_lo);
        double phase = _ce_mod_div(t * freq, 1.0);
        uint8_t v = (phase < 0.5) ? (uint8_t)hi : (uint8_t)lo;
        for (int i = 0; i < n; i++)
            out[i] = v;
        break;
    }
    case 1: {  /* light_breath_curves: 逐设备延迟 × 曲线（v25 :820） */
        double speed = p[0];
        int curve = (int)p[1], dmode = (int)p[2];
        int mid = n / 2;                              /* n // 2 */
        for (int i = 0; i < n; i++) {
            double local_t;
            if (dmode == 1)
                local_t = t * speed - (double)i * 0.1;             /* cascade */
            else if (dmode == 2)
                local_t = t * speed - (double)(n - i) * 0.1;       /* reverse */
            else if (dmode == 3)
                local_t = t * speed - fabs((double)(i - mid)) * 0.1;  /* center */
            else
                local_t = t * speed;                               /* none */
            double s = sin(local_t * 2.0 * M_PI);
            double raw = (s + 1.0) / 2.0;              /* _sine: 0..1 */
            double val;
            switch (curve) {
            case 1: {                                 /* ease */
                double e = raw * raw * (3.0 - 2.0 * raw);
                val = e * 255.0;
                break;
            }
            case 2: {                                 /* exponential */
                double num = exp(raw * 3.0) - 1.0;
                double den = exp(3.0) - 1.0;
                val = num / den * 255.0;
                break;
            }
            case 3:                                   /* square_breath */
                val = _ce_pow_ucrt(raw, 0.5) * 255.0;
                break;
            default:                                  /* sine(默认) */
                val = raw * 255.0;
                break;
            }
            out[i] = (uint8_t)_ce_clamp_trunc(val);
        }
        break;
    }
    default:  /* 未知 kind: 全 0（无对应预设, 防御兜底） */
        for (int i = 0; i < n; i++)
            out[i] = 0;
        break;
    }
}

/* 电机族: kind 0 = motor_fan_gradient_open（扇形幅度渐变开合, v35 -1..+1
 * 波形）; kind 1 = motor_cue_speed_ramp（相位积分速度渐变, 一次性执行）。
 * 参数槽:
 *   kind 0: p[0]=fans p[1]=speed p[2]=fan_angle p[3]=min_amp p[4]=max_amp
 *           p[5]=fan_phase_diff p[6]=wave(0=正弦 1=三角 2=锯齿上 3=锯齿下
 *           4=弹跳 5=脉冲)
 *   kind 1: p[0]=duration p[1]=start_speed p[2]=end_speed p[3]=amp
 *           p[4]=center p[5]=phase_diff
 * 输出均为 v35/v36 _clamp = int(round()) 半偶。 */
CE_API void ce_fan_cue_effect(
    int kind, double t, const double* p,
    int n, uint8_t* out)
{
    if (n <= 0)
        return;
    switch (kind) {
    case 0: {  /* fan_gradient_open: 每把扇子开合幅度渐变（v35 :2869） */
        long long fans_ll = (long long)p[0];        /* max(2, int(fans)) */
        if (fans_ll < 2) fans_ll = 2;
        int fans = (int)fans_ll;
        double speed = p[1], fan_angle = p[2];
        double min_amp = p[3], max_amp = p[4];
        double fan_phase = p[5];
        int wave = (int)p[6];
        int fan_size = n / fans;                    /* max(1, n // fans) */
        if (fan_size < 1) fan_size = 1;
        int fan_den = (fans > 1) ? (fans - 1) : 1;  /* max(1, fans-1) */
        for (int i = 0; i < n; i++) {
            int fan_idx = i / fan_size;             /* min(fans-1, ·) */
            if (fan_idx > fans - 1) fan_idx = fans - 1;
            int local_i = i - fan_idx * fan_size;
            int local_n = n - fan_idx * fan_size;   /* min(fan_size, ·) */
            if (local_n > fan_size) local_n = fan_size;
            int num_den = (local_n > 1) ? (local_n - 1) : 1;
            double local_ratio = ((double)local_i / (double)num_den) * 2.0 - 1.0;
            double amp_ratio = min_amp
                + (max_amp - min_amp) * (double)fan_idx / (double)fan_den;
            double open_amt = _ce_wave_v35(
                t * speed + (double)fan_idx * fan_phase, wave);
            double angle = local_ratio * fan_angle * 0.5 * open_amt * amp_ratio;
            double val = 128.0 + angle * 255.0 / 360.0;   /* _deg2dmx */
            out[i] = (uint8_t)_ce_clamp_round(val);
        }
        break;
    }
    case 1: {  /* cue_speed_ramp: 累积相位积分（v36 :1735） */
        double duration = p[0], start_speed = p[1], end_speed = p[2];
        double amp = p[3], center = p[4], phase_diff = p[5];
        double denom = (duration > 0.001) ? duration : 0.001;
        double prog = t / denom;                    /* min(1.0, ·) */
        if (prog > 1.0) prog = 1.0;
        double phase_acc = start_speed * t
            + (end_speed - start_speed) * t * prog * 0.5;
        for (int i = 0; i < n; i++) {
            double phase = phase_acc - (double)i * phase_diff;
            double w = sin(2.0 * M_PI * phase);
            double val = center + amp * w * 127.0 / 90.0;
            out[i] = (uint8_t)_ce_clamp_round(val);
        }
        break;
    }
    default:  /* 未知 kind: 全 0（无对应预设, 防御兜底） */
        for (int i = 0; i < n; i++)
            out[i] = 0;
        break;
    }
}

/* ── T10c: voice_clip_renderer 离线渲染数值内核 ──────────────────────────
 * 对齐 src/engine/auto_arrange/voice_clip_renderer.py（只读参考, 不碰 src）:
 *   ce_vc_clamp8          = _clamp8(:458)
 *   ce_vc_round_even      = int(round(x)) CPython 半偶舍入
 *                           （hi=round(255*level) / v*env / v*factor / foff）
 *   ce_vc_envelope        = _envelope(:462)（mode 0=none 1=pulse 2=swell;
 *                           其它 mode → none, 同 Python if/elif 无 else）
 *   ce_vc_speed_to_cycles = _speed_to_cycles(:212)
 *   ce_vc_pulse_rate      = render_voice_window(:376) pulse_rate
 *   ce_vc_hsv_to_rgb      = _hsv_to_rgb(:478)（colorsys.hsv_to_rgb 逐行同构）
 *   ce_vc_rgb_frame       = RGB 分支帧值块(:399-414)（floor/vv + 三配色 → int）
 * 16bit 拆分（_logical_16）已有 ce_programmer_motor_16bit(P5b), 本段不重复。
 *
 * 位级等价纪律:
 *   1. min/max 用 CPython 比较序（首参优先: min(a,b)=(b<a)?b:a;
 *      max(a,b)=(b>a)?b:a — NaN 时留首参, 与 fmin/fmax 不同, 不可用）
 *   2. float % 1.0 用 _ce_mod_div(x, 1.0)（float_rem 复刻, -0.0→+0.0 归一）
 *   3. int(round(x)) 复用 ce_round_half_even（半偶舍入, round(-0.5)=0）
 *   4. exp 与 CPython math.exp 同为 MSVC UCRT 实现, 逐位一致（T5 音频内核同源）
 *   5. 域限有限值: round(±inf/NaN) 抛 OverflowError/ValueError, 不在内核域内
 *      （T8 同款纪律）; nseq 域限 ≥1（Python max(1,·) 保证, 除零不入域）
 */

CE_API int ce_vc_clamp8(double x)
{
    /* _clamp8: max(0, min(255, int(round(x)))) */
    int64_t r = ce_round_half_even(x);
    if (r < 0)
        r = 0;
    else if (r > 255)
        r = 255;
    return (int)r;
}

CE_API int64_t ce_vc_round_even(double x)
{
    /* int(round(x)) — CPython 半偶舍入; 域限有限值 */
    return ce_round_half_even(x);
}

CE_API double ce_vc_envelope(double t_frac, int mode, double rate)
{
    /* _envelope: t = max(0.0, min(1.0, t_frac)); pulse/swell/none */
    double t = (t_frac < 1.0) ? t_frac : 1.0;   /* min(1.0, ·) = (t<1.0)?t:1.0 */
    t = (t > 0.0) ? t : 0.0;                    /* max(0.0, ·) = (t>0.0)?t:0.0,
                                                   -0.0/NaN→+0.0 */
    if (mode == 1) {                            /* pulse */
        double rmax = (rate > 0.5) ? rate : 0.5;    /* max(0.5, rate) 首参优先 */
        double ph = _ce_mod_div(t * rmax, 1.0);     /* float_rem 复刻 */
        double e = exp(-3.0 * ph);
        return (e > 0.0) ? e : 0.0;                 /* max(0.0, ·), NaN→0.0 */
    }
    if (mode == 2) {                            /* swell */
        if (t < 0.2)
            return t / 0.2;
        if (t > 0.75) {
            double d = (1.0 - t) / 0.25;
            return (d > 0.0) ? d : 0.0;             /* max(0.0, ·) */
        }
        return 1.0;
    }
    return 1.0;                                 /* none / 未知 mode */
}

CE_API double ce_vc_speed_to_cycles(double speed_dmx, double duration)
{
    /* _speed_to_cycles: duration<=0 → 1.0; cycles=总变化/510; 限幅 [0.25, dur*4] */
    if (duration <= 0.0)
        return 1.0;
    double m = (speed_dmx > 1.0) ? speed_dmx : 1.0; /* max(1.0, ·) 首参优先 */
    double cycles = (m * duration) / 510.0;
    double cap = duration * 4.0;
    double lo = (cap < cycles) ? cap : cycles;      /* min(cycles, cap) */
    return (lo > 0.25) ? lo : 0.25;                 /* max(0.25, ·), NaN→0.25 */
}

CE_API double ce_vc_pulse_rate(double speed_dmx)
{
    /* render_voice_window: pulse_rate = max(0.5, min(4.0, speed_dmx / 120.0)) */
    double x = speed_dmx / 120.0;
    double m = (x < 4.0) ? x : 4.0;                 /* min(4.0, ·) */
    return (m > 0.5) ? m : 0.5;                     /* max(0.5, ·) 首参优先 */
}

CE_API void ce_vc_hsv_to_rgb(double h, double s, double v,
                             int* r, int* g, int* b)
{
    /* _hsv_to_rgb: h%1.0 + clamp(s/v) + colorsys.hsv_to_rgb + int(round(x*255)) */
    double hh = _ce_mod_div(h, 1.0);
    double ss = (s < 1.0) ? s : 1.0;            /* min(1.0, ·) */
    ss = (ss > 0.0) ? ss : 0.0;                 /* max(0.0, ·) 首参优先 */
    double vv = (v < 1.0) ? v : 1.0;            /* min(1.0, ·) */
    vv = (vv > 0.0) ? vv : 0.0;                 /* max(0.0, ·) 首参优先 */
    double rr, gg, bb;
    if (ss == 0.0) {
        rr = vv; gg = vv; bb = vv;
    } else {
        double h6 = hh * 6.0;
        int i = (int)h6;                            /* Python int() 截断 */
        double f = h6 - (double)i;
        double p = vv * (1.0 - ss);
        double q = vv * (1.0 - ss * f);
        double t = vv * (1.0 - ss * (1.0 - f));
        i = i % 6;                                  /* hh∈[0,1) → i∈[0,6) */
        switch (i) {
        case 0: rr = vv; gg = t; bb = p; break;
        case 1: rr = q; gg = vv; bb = p; break;
        case 2: rr = p; gg = vv; bb = t; break;
        case 3: rr = p; gg = q; bb = vv; break;
        case 4: rr = t; gg = p; bb = vv; break;
        default: rr = vv; gg = p; bb = q; break;    /* i==5 */
        }
    }
    *r = (int)ce_round_half_even(rr * 255.0);
    *g = (int)ce_round_half_even(gg * 255.0);
    *b = (int)ce_round_half_even(bb * 255.0);
}

CE_API void ce_vc_rgb_frame(double level, int v, int color_mode,
                            double base_hue, double hue_span, double t_frac,
                            int seq_i, int nseq, int* r, int* g, int* b)
{
    /* render_voice_window RGB 分支(:399-414): floor/vv + white/pure/color */
    double floor_ = 0.45 + 0.35 * level;
    double vn = (double)v / 255.0;
    vn = (vn < 1.0) ? vn : 1.0;                     /* min(1.0, ·) */
    vn = (vn > 0.0) ? vn : 0.0;                     /* max(0.0, ·) 首参优先 */
    double vv = floor_ + (1.0 - floor_) * vn;
    if (color_mode == 0) {                          /* white */
        int w8 = (int)ce_round_half_even(vv * 255.0);
        *r = w8; *g = w8; *b = w8;
    } else if (color_mode == 1) {                   /* pure */
        ce_vc_hsv_to_rgb(base_hue, 0.80, vv, r, g, b);
    } else {                                        /* color: 色相旋转 */
        double hue = _ce_mod_div(
            base_hue + hue_span * t_frac + (0.30 * (double)seq_i) / (double)nseq,
            1.0);
        ce_vc_hsv_to_rgb(hue, 0.90, vv, r, g, b);
    }
}
/* ── T8续: 编曲器 preset 池选择内核（voice_model.py _pick_preset_from_pool
 * :435-479 纯数学段, 不接 hook; 合入由主分支统一做）────────────────────
 * 选择语义（逐字对齐 Python, 全确定性纯数学）:
 *   1. 哈希: kh = ((kh*131 + ord(ch)) & 0x7FFFFFFF) 逐字符遍历
 *      voice.key + "|" + str(tier) + "|" + str(block_ordinal)。
 *      str() = 十进制无前导零, 负数带 '-' 前缀（_ce_hash_int64 全量复刻,
 *      INT64_MIN 安全）; key 以 Unicode 码点数组传入 = ord() 语义,
 *      CJK/emoji（astral）安全。
 *   2. rng = random.Random(kh) → _ce_mt_seed（CPython 3.13 init_by_array
 *      逐位复刻, 见 :1356）; rng.random() → _ce_mt_res53。
 *   3. 候选序列 = 骨架区 A = pool[:la] + 填充区 B = pool[skel_n:skel_n+off]:
 *      la = skel_n>0 ? min(skel_n,pool_len) : min(8,pool_len);
 *      off = tier==0 ? 0 : (tier==1 ? 40 : 120)。
 *      skel_n==0 且 tier>0 时 A 与 B 重叠 —— Python 列表拼接重复项各自
 *      独立计权, 逐条复刻; 空串条目按 pool_valid 过滤, 全空时兜底 = 整个
 *      pool 不过滤（同 `[c for c in cands if c] or pool`）。
 *   4. 权重 w = pool_skel[i] ? 2.0 : 1.0; pool_prev[i] → w *= 0.1。
 *      pool_skel/pool_prev/pool_valid 为调用方按字符串等价预计算的标志
 *      （`c in skel` / `c == prev_preset` / `c != ""` 的精确语义 —— 池含
 *      重复串时以字符串判等为准, 非下标; 骨架区成员恒为真）。
 *   5. total = Σw（0.0 → 1.0, 同 `sum(weights) or 1.0`）;
 *      r = rng.random()*total; 累加首中 `r <= acc`; 无中取末条目
 *      （r < total 恒成立, 末条目为防御兜底, 同 `return cands[-1]`）。
 * 返回: 选中条目的池下标 [0, pool_len); pool_len<=0 或越契约 → -1
 * （调用方回退 Python 原路径）。
 * 契约: pool_len ≤ 4096（真实 preset 池 ≤ ~213 项）; skel_n ≥ 0（负数
 * 按 0 防御钳制 —— Python 负切片语义超出契约, 不保证）; key_len ≥ 0;
 * tier/block_ordinal 任意 int64（哈希十进制全量复刻）。
 * 位级纪律: 全程 double 串行, /fp:precise 禁 FMA; 哈希 int64 不溢出
 * （kh<2^31, 131*kh+ord ≤ 2^38）; 仅追加新函数, 不改动既有函数体。 */

/* Python str(int): 十进制逐字符喂哈希（逐字符 & 0x7FFFFFFF 掩码同步） */
static void _ce_hash_int64(int64_t* kh, long long v)
{
    char buf[24];
    int n = 0;
    unsigned long long uv;
    if (v < 0) {
        *kh = (*kh * 131 + (int64_t)'-') & 0x7FFFFFFFLL;
        uv = (unsigned long long)(-(v + 1)) + 1ULL;   /* INT64_MIN 安全取负 */
    } else {
        uv = (unsigned long long)v;
    }
    do {
        buf[n++] = (char)('0' + (int)(uv % 10));
        uv /= 10;
    } while (uv != 0);
    while (n > 0) {
        *kh = (*kh * 131 + (int64_t)buf[--n]) & 0x7FFFFFFFLL;
    }
}

/* 哈希直连导出（供 fuzz 对拍/合入后自检） */
CE_API long long ce_preset_key_hash(const uint32_t* key_cp, int key_len,
                                    int tier, long long block_ordinal)
{
    int64_t kh = 0;
    int i;
    for (i = 0; i < key_len; i++)
        kh = (kh * 131 + (int64_t)key_cp[i]) & 0x7FFFFFFFLL;
    kh = (kh * 131 + (int64_t)'|') & 0x7FFFFFFFLL;
    _ce_hash_int64(&kh, tier);
    kh = (kh * 131 + (int64_t)'|') & 0x7FFFFFFFLL;
    _ce_hash_int64(&kh, block_ordinal);
    return kh;
}

#define _CE_POOL_MAX_ENTRIES 4224   /* 4096 池上限 + 120 填充余量 */

CE_API int ce_preset_pool_pick(
    const uint32_t* key_cp, int key_len,
    int tier,
    long long block_ordinal,
    int skel_n,
    const uint8_t* pool_valid,
    const uint8_t* pool_skel,
    const uint8_t* pool_prev,
    int pool_len)
{
    int i, k, n_entries, la, off, b_start, b_end;
    int entries[_CE_POOL_MAX_ENTRIES];
    double weights[_CE_POOL_MAX_ENTRIES];
    double total, r, acc, w;
    ce_mt_state_t rng;
    int64_t kh;

    if (pool_len <= 0)
        return -1;
    if (pool_len > 4096)                /* 契约外: 调用方回退 Python */
        return -1;
    if (skel_n < 0)                     /* 契约外防御（Python 负切片不保证） */
        skel_n = 0;

    kh = ce_preset_key_hash(key_cp, key_len, tier, block_ordinal);
    _ce_mt_seed(&rng, kh);

    /* 候选序列: 骨架区 A + 填充区 B（Python 切片语义, 各自独立过滤） */
    la = (skel_n > 0) ? ((skel_n < pool_len) ? skel_n : pool_len)
                      : ((8 < pool_len) ? 8 : pool_len);
    off = (tier == 0) ? 0 : ((tier == 1) ? 40 : 120);
    n_entries = 0;
    for (i = 0; i < la; i++)
        if (pool_valid[i])
            entries[n_entries++] = i;
    if (off > 0) {
        b_start = (skel_n < pool_len) ? skel_n : pool_len;
        {
            long long be = (long long)b_start + off;   /* int64 防溢出 */
            b_end = (be < pool_len) ? (int)be : pool_len;
        }
        for (i = b_start; i < b_end; i++)
            if (pool_valid[i])
                entries[n_entries++] = i;
    }
    if (n_entries == 0) {               /* 全空 → 兜底全池（不过滤） */
        for (i = 0; i < pool_len; i++)
            entries[n_entries++] = i;
    }

    total = 0.0;
    for (k = 0; k < n_entries; k++) {
        w = pool_skel[entries[k]] ? 2.0 : 1.0;
        if (pool_prev[entries[k]])
            w *= 0.1;
        weights[k] = w;
        total += w;
    }
    if (total == 0.0)                   /* sum(weights) or 1.0 */
        total = 1.0;

    r = _ce_mt_res53(&rng) * total;
    acc = 0.0;
    for (k = 0; k < n_entries; k++) {
        acc += weights[k];
        if (r <= acc)
            return entries[k];
    }
    return entries[n_entries - 1];      /* 防御兜底, 同 cands[-1] */
}

/* ═══════════════════════════════════════════════════════════════════════
 * T10: TopView 几何数学 C 化
 * 对拍对象: src/ui/topview_autogroup.py 纯数学段（无 PyQt 依赖）:
 *   _circle_from_3(:70) / _ang_norm(:88) / _ang_diff(:93) / _ellipse_angle(:101)
 *   _assign_to_shapes(:112) / _detect_circles_ransac(:159)
 *   AutoGroupEngine._circle_axis_order(:279) / _members_in_window(:297)
 * 位级等价纪律:
 *   - math.hypot → CPython 3.13 vector_norm(n=2) 逐位复刻（fma 精确乘积 +
 *     Neumaier 求和 + sqrt 微分校正; 参考 Modules/mathmodule.c :2529-2596,
 *     同 ucrt fma/ldexp/sqrt → 与 CPython 位级一致）
 *   - random.Random(seed&0x7FFFFFFF) → 复用上方 _ce_mt_seed; randrange(n) →
 *     CPython _randbelow_with_getrandbits（Lib/random.py :245-252）:
 *     k = n.bit_length(); r = getrandbits(k) = genrand >> (32-k); r>=n 重试
 *   - x ** 2 → pow(x, 2.0)（同 CPython float_pow → ucrt pow, 正确舍入 =
 *     x*x 位级一致）
 *   - sum(生成器) → CPython builtin_sum 浮点快路径逐位复刻（bltinmodule.c
 *     :2660-2709）: 首项直赋 + Neumaier 补偿累加 + 收尾 c 有限才加回
 *   - 稳定排序 → qsort + (key..., idx) 全序（idx 决胜 = Python 稳定排序）
 *   - /fp:precise + #pragma fp_contract(off) 保证无 FMA 收缩
 * ═══════════════════════════════════════════════════════════════════════ */

#include <float.h>

/* CPython Modules/mathmodule.c dl_fast_sum（Algorithm 1.1, 补偿和） */
typedef struct { double hi; double lo; } _ce_dl_t;

static _ce_dl_t _ce_dl_fast_sum(double a, double b)
{
    double x = a + b;
    double y = (a - x) + b;
    _ce_dl_t r;
    r.hi = x;
    r.lo = y;
    return r;
}

/* CPython dl_mul（Algorithm 3.5, fma 精确乘积拆分; UNRELIABLE_FMA 未定义时） */
static _ce_dl_t _ce_dl_mul(double x, double y)
{
    double z = x * y;
    double zz = fma(x, y, -z);
    _ce_dl_t r;
    r.hi = z;
    r.lo = zz;
    return r;
}

/* CPython vector_norm(n=2) 逐位复刻（mathmodule.c :2529-2596）。
 * 入口先取 fabs——同 CPython math_hypot 的 `x = fabs(x)` 坐标预处理,
 * 再 isinf 先于 isnan（同 vector_norm 的 max 检查顺序: hypot(inf,nan)=inf）。 */
static double _ce_hypot2(double x, double y)
{
    double max, h, scale, csum = 1.0, frac1 = 0.0, frac2 = 0.0;
    _ce_dl_t pr, sm;
    int max_e;
    x = fabs(x);
    y = fabs(y);
    if (isinf(x) || isinf(y)) return INFINITY;
    if (isnan(x) || isnan(y)) return NAN;
    max = 0.0;
    if (x > max) max = x;
    if (y > max) max = y;
    if (max == 0.0) return 0.0;
    frexp(max, &max_e);
    if (max_e < -1023) {
        return DBL_MIN * _ce_hypot2(x / DBL_MIN, y / DBL_MIN);
    }
    scale = ldexp(1.0, -max_e);
    {
        double xx = x * scale;
        pr = _ce_dl_mul(xx, xx);
        sm = _ce_dl_fast_sum(csum, pr.hi);
        csum = sm.hi;
        frac1 += pr.lo;
        frac2 += sm.lo;
    }
    {
        double yy = y * scale;
        pr = _ce_dl_mul(yy, yy);
        sm = _ce_dl_fast_sum(csum, pr.hi);
        csum = sm.hi;
        frac1 += pr.lo;
        frac2 += sm.lo;
    }
    h = sqrt(csum - 1.0 + (frac1 + frac2));
    pr = _ce_dl_mul(-h, h);
    sm = _ce_dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
    {
        double xx = csum - 1.0 + (frac1 + frac2);
        h += xx / (2.0 * h);
    }
    return h / scale;
}

/* CPython getrandbits/randbelow 语义由既有 _ce_mt_randbelow(:5737) 提供:
 * k = n.bit_length(); r = genrand >> (32-k); r>=n 时重试。 */

/* CPython builtin_sum 浮点快路径逐位复刻（bltinmodule.c :2660-2709）:
 * start=0(int) + 首个 float 项 → f_result=首项(0+x1=x1 精确);
 * 其后 Neumaier 补偿累加; 收尾 c 非零且有限才加回。
 * 对应 Python `sum(p[1] for p in best_inliers)` 等生成器求和。 */
static double _ce_py_sum(const double* vals, int n)
{
    if (n <= 0) return 0.0;
    double f_result = vals[0];
    double c = 0.0;
    for (int i = 1; i < n; i++) {
        double x = vals[i];
        double t = f_result + x;
        if (fabs(f_result) >= fabs(x)) {
            c += (f_result - t) + x;
        } else {
            c += (x - t) + f_result;
        }
        f_result = t;
    }
    if (c != 0.0 && isfinite(c)) f_result += c;
    return f_result;
}

/* topview_autogroup._circle_from_3(:70): 三点定圆; 返回 0=共线/退化 */
static int _ce_circle_from_3(double ax, double ay, double bx, double by,
                             double cx3, double cy3,
                             double* out_cx, double* out_cy, double* out_r)
{
    double d = 2.0 * (ax * (by - cy3) + bx * (cy3 - ay) + cx3 * (ay - by));
    if (fabs(d) < 1e-9) return 0;
    double a2 = ax * ax + ay * ay;
    double b2 = bx * bx + by * by;
    double c2 = cx3 * cx3 + cy3 * cy3;
    double ux = (a2 * (by - cy3) + b2 * (cy3 - ay) + c2 * (ay - by)) / d;
    double uy = (a2 * (cx3 - bx) + b2 * (ax - cx3) + c2 * (bx - ax)) / d;
    *out_cx = ux;
    *out_cy = uy;
    *out_r = _ce_hypot2(ax - ux, ay - uy);
    return 1;
}

/* _ang_norm(:88): a % TWO_PI → _ce_fmod_pos（同 CPython float_rem） */
static double _ce_ang_norm(double a)
{
    return _ce_fmod_pos(a, 6.283185307179586);
}

/* _ang_diff(:93): d=(a-b)%τ; d>π 时 d-=τ; 结果∈(-π,π] */
static double _ce_ang_diff(double a, double b)
{
    double d = _ce_fmod_pos(a - b, 6.283185307179586);
    if (d > 3.141592653589793) d -= 6.283185307179586;
    return d;
}

/* _ellipse_angle(:101): rx/ry 的 1e-6 兜底(仅替换局部值, 不做 abs) + atan2 归一 */
static double _ce_ellipse_angle(double x, double y, double cx, double cy,
                                double rx, double ry)
{
    if (fabs(rx) <= 1e-6) rx = 1.0;
    if (fabs(ry) <= 1e-6) ry = 1.0;
    return _ce_ang_norm(atan2((y - cy) / ry, (x - cx) / rx));
}

/* ── RANSAC 多圆贪心提取（_detect_circles_ransac :159）──────────────
 * 一轮贪心: 采样 iters 次三点定圆; 每迭代固定先取 3 次 randrange
 * （即使 i==j 等重复/退化也先取完——同 Python 元组求值顺序）。
 * 返回最优内点数（0=无候选）; best[] 装内点索引（rem 序）。 */
static int _ce_ransac_round(ce_mt_state_t* rng, const double* xs,
                            const double* ys, const int* rem, int nrem,
                            int iters, double r_max, double diag,
                            int* scratch, int* best,
                            double* bcx, double* bcy, double* br)
{
    int best_count = 0;
    for (int it = 0; it < iters; it++) {
        int i = (int)_ce_mt_randbelow(rng, (uint32_t)nrem);
        int j = (int)_ce_mt_randbelow(rng, (uint32_t)nrem);
        int k = (int)_ce_mt_randbelow(rng, (uint32_t)nrem);
        if (i == j || j == k || i == k) continue;
        int pi = rem[i], pj = rem[j], pk = rem[k];
        double ux, uy, r;
        if (!_ce_circle_from_3(xs[pi], ys[pi], xs[pj], ys[pj],
                               xs[pk], ys[pk], &ux, &uy, &r)) continue;
        if (r <= 1e-3 || r > r_max) continue;
        double t1 = diag * 0.02;
        double t2 = r * 0.10;
        double tol = t1 > t2 ? t1 : t2;
        int cnt = 0;
        for (int t = 0; t < nrem; t++) {
            int pidx = rem[t];
            double dist = _ce_hypot2(xs[pidx] - ux, ys[pidx] - uy) - r;
            if (fabs(dist) <= tol) scratch[cnt++] = pidx;
        }
        if (cnt > best_count) {
            best_count = cnt;
            memcpy(best, scratch, (size_t)cnt * sizeof(int));
            *bcx = ux; *bcy = uy; *br = r;
        }
    }
    return best_count;
}

/* _detect_circles_ransac(:159) 全量复刻: 贪心多圆提取。
 * 输出: out_cx/out_cy/out_r[0..n], out_counts[i]=每圆成员数,
 * out_member_idx/out_member_ang 按圆序拼接（成员索引 + 角度, 同 Python
 * best_inliers 顺序）。返回圆数; -1=分配失败。 */
CE_API int ce_ransac_circles(const double* xs, const double* ys, int n,
                             long long seed, int min_pts, int iters,
                             double* out_cx, double* out_cy, double* out_r,
                             int* out_counts, int* out_member_idx,
                             double* out_member_ang)
{
    if (n < min_pts) return 0;
    double min_x = xs[0], max_x = xs[0], min_y = ys[0], max_y = ys[0];
    for (int i = 1; i < n; i++) {
        if (xs[i] < min_x) min_x = xs[i];
        if (xs[i] > max_x) max_x = xs[i];
        if (ys[i] < min_y) min_y = ys[i];
        if (ys[i] > max_y) max_y = ys[i];
    }
    double dx = max_x - min_x;
    double dy = max_y - min_y;
    if (dx == 0.0) dx = 1.0;
    if (dy == 0.0) dy = 1.0;
    double diag = _ce_hypot2(dx, dy);
    double r_max = diag * 1.2;

    ce_mt_state_t rng;
    _ce_mt_seed(&rng, seed & 0x7FFFFFFFLL);

    int* rem = (int*)malloc((size_t)n * sizeof(int));
    int* scratch = (int*)malloc((size_t)n * sizeof(int));
    int* best = (int*)malloc((size_t)n * sizeof(int));
    char* mark = (char*)malloc((size_t)n);
    double* dsum = (double*)malloc((size_t)n * sizeof(double));
    if (rem == NULL || scratch == NULL || best == NULL || mark == NULL
        || dsum == NULL) {
        free(rem); free(scratch); free(best); free(mark); free(dsum);
        return -1;
    }
    for (int i = 0; i < n; i++) rem[i] = i;
    int nrem = n;
    int n_circles = 0;
    int member_total = 0;

    while (nrem >= min_pts) {
        double bcx = 0.0, bcy = 0.0, br = 0.0;
        int best_count = _ce_ransac_round(&rng, xs, ys, rem, nrem, iters,
                                          r_max, diag, scratch, best,
                                          &bcx, &bcy, &br);
        if (best_count == 0 || best_count < min_pts) break;
        for (int t = 0; t < best_count; t++) dsum[t] = xs[best[t]];
        double mcx = _ce_py_sum(dsum, best_count) / (double)best_count;
        for (int t = 0; t < best_count; t++) dsum[t] = ys[best[t]];
        double mcy = _ce_py_sum(dsum, best_count) / (double)best_count;
        for (int t = 0; t < best_count; t++) {
            dsum[t] = _ce_hypot2(xs[best[t]] - mcx, ys[best[t]] - mcy);
        }
        double mr = _ce_py_sum(dsum, best_count) / (double)best_count;
        if (mr <= 1e-3) { mcx = bcx; mcy = bcy; mr = br; }
        out_cx[n_circles] = mcx;
        out_cy[n_circles] = mcy;
        out_r[n_circles] = mr;
        out_counts[n_circles] = best_count;
        for (int t = 0; t < best_count; t++) {
            out_member_idx[member_total + t] = best[t];
            out_member_ang[member_total + t] =
                _ce_ang_norm(atan2(ys[best[t]] - mcy, xs[best[t]] - mcx));
        }
        member_total += best_count;
        n_circles++;
        memset(mark, 0, (size_t)n);
        for (int t = 0; t < best_count; t++) mark[best[t]] = 1;
        int w = 0;
        for (int t = 0; t < nrem; t++) {
            if (!mark[rem[t]]) rem[w++] = rem[t];
        }
        nrem = w;
    }
    free(rem); free(scratch); free(best); free(mark); free(dsum);
    return n_circles;
}

/* _assign_to_shapes(:112): 已知圆/椭圆归属聚类。
 * 输入: xs/ys 点集 + 形状四元组(scx,scy,srx,sry 原始值——rx/ry 的 abs/
 * 1e-3 跳过/ry 兜底在核内复刻)。
 * 输出: 有效形状(geoms)按序填 out_cx/out_cy/out_r/out_counts; 空圆
 * (out_counts[i]==0) 由调用方过滤——对应 Python `[c for c in circles
 * if c.members]`。成员按圆序分块拼接（圆内按点处理序, 同 Python 逐圆
 * append）; 调用方按 out_counts 前缀和定位每圆成员。返回 geoms 数;
 * -1=分配失败。 */
CE_API int ce_assign_shapes(const double* xs, const double* ys, int n_pts,
                            const double* scx, const double* scy,
                            const double* srx, const double* sry, int n_shapes,
                            double* out_cx, double* out_cy, double* out_r,
                            int* out_counts, int* out_member_idx,
                            double* out_member_ang)
{
    if (n_shapes <= 0) return 0;
    double* gx = (double*)malloc((size_t)n_shapes * sizeof(double));
    double* gy = (double*)malloc((size_t)n_shapes * sizeof(double));
    double* grx = (double*)malloc((size_t)n_shapes * sizeof(double));
    double* gry = (double*)malloc((size_t)n_shapes * sizeof(double));
    if (gx == NULL || gy == NULL || grx == NULL || gry == NULL) {
        free(gx); free(gy); free(grx); free(gry);
        return -1;
    }
    int n_geoms = 0;
    for (int i = 0; i < n_shapes; i++) {
        double rx = fabs(srx[i]);
        double ry = fabs(sry[i]);
        if (rx <= 1e-3) continue;
        if (ry <= 1e-3) ry = rx;
        gx[n_geoms] = scx[i];
        gy[n_geoms] = scy[i];
        grx[n_geoms] = rx;
        gry[n_geoms] = ry;
        out_cx[n_geoms] = scx[i];
        out_cy[n_geoms] = scy[i];
        out_r[n_geoms] = (rx + ry) / 2.0;
        out_counts[n_geoms] = 0;
        n_geoms++;
    }
    if (n_geoms == 0) {
        free(gx); free(gy); free(grx); free(gry);
        return 0;
    }
    int total = 0;
    int* best_scratch = (int*)malloc((size_t)n_pts * sizeof(int));
    double* ang_scratch = (double*)malloc((size_t)n_pts * sizeof(double));
    int* offsets = (int*)malloc((size_t)n_geoms * sizeof(int));
    int* idx_tmp = (int*)malloc((size_t)n_pts * sizeof(int));
    if (best_scratch == NULL || ang_scratch == NULL || offsets == NULL
        || idx_tmp == NULL) {
        free(gx); free(gy); free(grx); free(gry);
        free(best_scratch); free(ang_scratch); free(offsets); free(idx_tmp);
        return -1;
    }
    for (int p = 0; p < n_pts; p++) {
        double x = xs[p], y = ys[p];
        int best_i = -1;
        double best_res = 0.0;
        for (int i = 0; i < n_geoms; i++) {
            double nx = (x - gx[i]) / grx[i];
            double ny = (y - gy[i]) / gry[i];
            double res = fabs(_ce_hypot2(nx, ny) - 1.0);
            if (best_i < 0 || res < best_res) { best_res = res; best_i = i; }
        }
        if (best_res <= 0.35) {
            best_scratch[total] = best_i;
            ang_scratch[total] = _ce_ellipse_angle(x, y, gx[best_i], gy[best_i],
                                                   grx[best_i], gry[best_i]);
            out_member_idx[total] = p;
            total++;
            out_counts[best_i]++;
        }
    }
    /* 重排为圆序分块（圆内保持点处理序）: 源流先复制到 scratch
     * （块序 ≠ 首现序时原地搬运会覆盖未读位置） */
    memcpy(idx_tmp, out_member_idx, (size_t)total * sizeof(int));
    int acc = 0;
    for (int i = 0; i < n_geoms; i++) {
        offsets[i] = acc;
        acc += out_counts[i];
    }
    for (int t = 0; t < total; t++) {
        int dst = offsets[best_scratch[t]]++;
        out_member_idx[dst] = idx_tmp[t];
        out_member_ang[dst] = ang_scratch[t];
    }
    free(gx); free(gy); free(grx); free(gry);
    free(best_scratch); free(ang_scratch); free(offsets); free(idx_tmp);
    return n_geoms;
}

/* ── 圆排序/窗口成员 ──────────────────────────────────────────────── */

typedef struct { double proj; double x; double y; int idx; } _ce_axis_t;

/* qsort 比较器: (proj, x, y, idx) 全序 = Python 稳定排序 + 元组 key */
static int _ce_cmp_axis(const void* a, const void* b)
{
    const _ce_axis_t* pa = (const _ce_axis_t*)a;
    const _ce_axis_t* pb = (const _ce_axis_t*)b;
    if (pa->proj < pb->proj) return -1;
    if (pa->proj > pb->proj) return 1;
    if (pa->x < pb->x) return -1;
    if (pa->x > pb->x) return 1;
    if (pa->y < pb->y) return -1;
    if (pa->y > pb->y) return 1;
    if (pa->idx < pb->idx) return -1;
    if (pa->idx > pb->idx) return 1;
    return 0;
}

/* _circle_axis_order(:279): 圆心 PCA 主轴投影排序 + (x, y) 决胜 */
CE_API int ce_circle_axis_order(const double* cx, const double* cy, int n,
                                int* out_order)
{
    if (n <= 1) {
        for (int i = 0; i < n; i++) out_order[i] = i;
        return n;
    }
    double* tmp = (double*)malloc((size_t)n * sizeof(double));
    if (tmp == NULL) return -1;
    for (int i = 0; i < n; i++) tmp[i] = cx[i];
    double mx = _ce_py_sum(tmp, n) / (double)n;
    for (int i = 0; i < n; i++) tmp[i] = cy[i];
    double my = _ce_py_sum(tmp, n) / (double)n;
    for (int i = 0; i < n; i++) tmp[i] = pow(cx[i] - mx, 2.0);
    double sxx = _ce_py_sum(tmp, n);
    for (int i = 0; i < n; i++) tmp[i] = pow(cy[i] - my, 2.0);
    double syy = _ce_py_sum(tmp, n);
    for (int i = 0; i < n; i++) tmp[i] = (cx[i] - mx) * (cy[i] - my);
    double sxy = _ce_py_sum(tmp, n);
    double vx, vy;
    if (fabs(sxy) < 1e-9 && fabs(sxx - syy) < 1e-9) {
        vx = 1.0; vy = 0.0;
    } else {
        double ang = 0.5 * atan2(2.0 * sxy, sxx - syy);
        vx = cos(ang); vy = sin(ang);
    }
    _ce_axis_t* arr = (_ce_axis_t*)malloc((size_t)n * sizeof(_ce_axis_t));
    if (arr == NULL) { free(tmp); return -1; }
    for (int i = 0; i < n; i++) {
        arr[i].proj = cx[i] * vx + cy[i] * vy;
        arr[i].x = cx[i];
        arr[i].y = cy[i];
        arr[i].idx = i;
    }
    qsort(arr, (size_t)n, sizeof(_ce_axis_t), _ce_cmp_axis);
    for (int i = 0; i < n; i++) out_order[i] = arr[i].idx;
    free(arr);
    free(tmp);
    return n;
}

typedef struct { double off; double ang; int idx; } _ce_win_t;

/* qsort 比较器: (off, ang, idx) 全序 = _arc_chain 的 (t[2], t[1]) 稳定排序 */
static int _ce_cmp_win(const void* a, const void* b)
{
    const _ce_win_t* pa = (const _ce_win_t*)a;
    const _ce_win_t* pb = (const _ce_win_t*)b;
    if (pa->off < pb->off) return -1;
    if (pa->off > pb->off) return 1;
    if (pa->ang < pb->ang) return -1;
    if (pa->ang > pb->ang) return 1;
    if (pa->idx < pb->idx) return -1;
    if (pa->idx > pb->idx) return 1;
    return 0;
}

/* _members_in_window(:297) + win.sort((t[2], t[1])) 复刻:
 * scope_flags[i]=1 才参与(对应 _scoped_members 的 set 过滤, 由调用方
 * 按 fid 计算); off=_ang_diff(ang, center_phase); |off|<=half_width+1e-9。
 * 返回窗口成员数; out_idx 为成员在原列表中的索引, 按 (off, ang) 升序。 */
CE_API int ce_circle_window_order(const double* angs, const uint8_t* scope_flags,
                                  int n_members, double center_phase,
                                  double half_width, int* out_idx)
{
    if (n_members <= 0) return 0;
    _ce_win_t* win = (_ce_win_t*)malloc((size_t)n_members * sizeof(_ce_win_t));
    if (win == NULL) return -1;
    double limit = half_width + 1e-9;
    int cnt = 0;
    for (int i = 0; i < n_members; i++) {
        if (scope_flags != NULL && !scope_flags[i]) continue;
        double off = _ce_ang_diff(angs[i], center_phase);
        if (fabs(off) <= limit) {
            win[cnt].off = off;
            win[cnt].ang = angs[i];
            win[cnt].idx = i;
            cnt++;
        }
    }
    qsort(win, (size_t)cnt, sizeof(_ce_win_t), _ce_cmp_win);
    for (int i = 0; i < cnt; i++) out_idx[i] = win[i].idx;
    free(win);
    return cnt;
}

/* ── 标量几何导出 ──────────────────────────────────────────────────── */

CE_API int ce_circle_from_3(double ax, double ay, double bx, double by,
                            double cx3, double cy3,
                            double* out_cx, double* out_cy, double* out_r)
{
    return _ce_circle_from_3(ax, ay, bx, by, cx3, cy3,
                             out_cx, out_cy, out_r);
}

CE_API double ce_ang_norm(double a)
{
    return _ce_ang_norm(a);
}

CE_API double ce_ang_diff(double a, double b)
{
    return _ce_ang_diff(a, b);
}

CE_API double ce_ellipse_angle(double x, double y, double cx, double cy,
                               double rx, double ry)
{
    return _ce_ellipse_angle(x, y, cx, cy, rx, ry);
}

/* ── 阶段B: condvar 常驻线程池并行渲染（池实现在 ce_pool.c, 契约 §4.3）─
 * 背景: P8 spin/事件池实测负收益（忙等烧 CPU, 帧 17→32ms, iter#17）→
 * 替换为 ce_pool.c 的 SRWLOCK + SleepConditionVariableSRW 常驻池:
 * kernel 等待全程零 spin（无 _mm_pause/Sleep(0)/忙等）; 固定容量环形
 * 队列, 提交满则条件变量阻塞; 批量提交全部入队后一次唤醒 worker。
 * 池为进程级静态全局: 首次并行调用懒创建, 常驻复用（destroy never）;
 * 线程数: QASCADE_CE_THREADS 覆写（1..16 钳制）否则物理核-1 cap 16。
 * 分块: 任务集 → min(worker, tasks) 连续块, 块内串行 — 146 任务 →
 * ~10 任务/块（小任务合批不散开, iter#17 教训; 每任务输出独立缓冲
 * → 块序与串行序逐位一致, 门1 73d9e1c2 守护）。
 * 并发契约: 单提交者（渲染主线程持锁调用）; 静态共享缓冲仅提交者写,
 * worker 在 wait 返回前只读 → 无数据竞争。
 */

#define CE_RENDER_MAX_WORKERS 16   /* 分块上限（与 ce_pool_create 线程数 cap 一致） */

typedef struct {
    ce_ctx* ctx;
    const int* clip_idx;
    const double* source_t;
    const double* start_t;
    const double* rate;
    const double* timeline_now;
    const int* hold;
    const float* gain;
    const int* fade_mode;
    int n_tasks;
    int* out_idx;
    uint8_t* out;
    uint8_t* act;
    double* scores;
} ce_render_shared;

typedef struct {
    ce_render_shared* sh;
    int t_begin;
    int t_end;
} ce_render_chunk_arg;

static ce_pool* g_ce_pool = NULL;            /* 常驻池（InitOnce 单次初始化, destroy never） */
static int g_ce_pool_threads = 0;            /* 池线程数（创建时固化, 分块依据） */
static INIT_ONCE g_ce_pool_once = INIT_ONCE_STATIC_INIT;  /* W1 Phase2: 懒创建加锁 */
static ce_render_shared g_render_shared;     /* 共享渲染参数（单提交者写） */
static ce_render_chunk_arg g_render_chunks[CE_RENDER_MAX_WORKERS];
static ce_task_fn g_chunk_fns[CE_RENDER_MAX_WORKERS];
static void* g_chunk_args[CE_RENDER_MAX_WORKERS];

/* W1 Phase2: 池单次初始化回调（InitOnceExecuteOnce, 多线程首调安全;
 * 原裸懒创建在 tick+effect 并发首调时可能双池/覆盖/错池等待）。 */
static BOOL CALLBACK _ce_pool_init_once(PINIT_ONCE once, PVOID param, PVOID* out)
{
    (void)once; (void)param; (void)out;
    int n_threads = 0;
    const char* env = getenv("QASCADE_CE_THREADS");
    if (env != NULL && env[0] != '\0') {
        n_threads = atoi(env);
    }
    if (n_threads < 1) {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        n_threads = (int)si.dwNumberOfProcessors - 1;
    }
    if (n_threads < 1) n_threads = 1;
    if (n_threads > CE_RENDER_MAX_WORKERS) n_threads = CE_RENDER_MAX_WORKERS;
    g_ce_pool = ce_pool_create(n_threads);
    if (g_ce_pool == NULL) {
        g_ce_pool_threads = 0;
        return TRUE;   /* 池创建失败 → 调用方走 serial 兜底 */
    }
    g_ce_pool_threads = n_threads;
    return TRUE;
}

/* 连续任务块渲染 — 块内串行, 与 ce_render_frame 逐任务完全一致
 * （ce_render_clip_frame 调用与 out/act/scores 行寻址 i*512 原样保留）→
 * 位级等价; thread_id = worker 下标, 本块不使用（ce_task_fn 契约要求）。
 * 块数 = min(worker, tasks) 而非任务数 — iter#17 教训: 146×3µs 小任务
 * 散开并行 = 领取/唤醒开销 > 负载 → 合批成块。 */
static void _ce_pool_render_chunk(void* varg, int thread_id)
{
    ce_render_chunk_arg* c = (ce_render_chunk_arg*)varg;
    ce_render_shared* s = c->sh;
    (void)thread_id;
    for (int t = c->t_begin; t < c->t_end; t++) {
        if (t < 0 || t >= s->n_tasks) continue;
        int ci = s->clip_idx[t];
        if (ci < 0 || ci >= s->ctx->clip_count || s->ctx->clips[ci].frame_times == NULL) {
            s->out_idx[t] = -1; continue;
        }
        ce_clip_entry* e = &s->ctx->clips[ci];
        s->out_idx[t] = ce_render_clip_frame(
            e->frame_times, e->n_frames, e->frame_data, e->last_change,
            s->source_t[t], s->start_t[t], s->rate[t], s->timeline_now[t],
            s->hold[t], s->gain[t], e->base_active, s->fade_mode[t],
            e->prev, e->target,
            s->out + (size_t)t * 512, s->act + (size_t)t * 512, s->scores + (size_t)t * 512);
    }
}

/* 并行渲染入口 — 与 ce_render_frame 同签名。池懒创建（首次调用,
 * 进程级常驻复用, destroy never）; 线程数: QASCADE_CE_THREADS
 * （1..16 钳制）覆写, 否则物理核-1 cap 16。
 * 任务量: ≥2 即上池 — 146 任务 → min(worker, 146) 连续块, 每块
 * ~10 任务串行（小任务合批不散开, iter#17 教训）。
 * 失败契约: 池创建/提交失败 → 串行兜底（ce_render_frame, 语义逐位一致）。 */
CE_API void ce_render_frame_parallel(void* vctx,
                                     const int* clip_idx, const int* universe,
                                     const double* source_t, const double* start_t,
                                     const double* rate, const double* timeline_now,
                                     const int* hold, const float* gain,
                                     const int* fade_mode, int n_tasks,
                                     int* out_idx, uint8_t* out, uint8_t* act,
                                     double* scores)
{
    ce_ctx* ctx = (ce_ctx*)vctx;
    if (ctx == NULL || n_tasks <= 0) return;
    if (n_tasks < 2) {
        ce_render_frame(vctx, clip_idx, universe, source_t, start_t, rate,
                        timeline_now, hold, gain, fade_mode, n_tasks,
                        out_idx, out, act, scores);
        return;
    }
    if (g_ce_pool == NULL) {
        if (!InitOnceExecuteOnce(&g_ce_pool_once, _ce_pool_init_once, NULL, NULL))
            goto serial;
        if (g_ce_pool == NULL) goto serial;
    }
    {
        ce_render_shared* sh = &g_render_shared;
        sh->ctx = ctx;
        sh->clip_idx = clip_idx;
        sh->source_t = source_t;
        sh->start_t = start_t;
        sh->rate = rate;
        sh->timeline_now = timeline_now;
        sh->hold = hold;
        sh->gain = gain;
        sh->fade_mode = fade_mode;
        sh->n_tasks = n_tasks;
        sh->out_idx = out_idx;
        sh->out = out;
        sh->act = act;
        sh->scores = scores;
        /* 分块: 连续任务切片（块内串行, 块间并行）。
         * 块数 = min(worker 数, 任务数) — 146 任务 → ~15 块, 每块 ~10 任务
         * 串行; 入队开销从 O(n_tasks) 降到 O(n_workers)。
         * 每任务输出独立缓冲 → 块序与串行序逐位一致。 */
        int n_chunks = n_tasks < g_ce_pool_threads ? n_tasks : g_ce_pool_threads;
        if (n_chunks < 1) n_chunks = 1;
        int t = 0;
        for (int c = 0; c < n_chunks; c++) {
            int end = (c + 1) * n_tasks / n_chunks;   /* 均分, 后块略大 */
            if (end < t + 1) end = t + 1;
            if (end > n_tasks) end = n_tasks;
            g_render_chunks[c].sh = sh;
            g_render_chunks[c].t_begin = t;
            g_render_chunks[c].t_end = end;
            g_chunk_fns[c] = _ce_pool_render_chunk;
            g_chunk_args[c] = &g_render_chunks[c];
            t = end;
            if (t >= n_tasks) { n_chunks = c + 1; break; }
        }
        if (ce_pool_submit_batch(g_ce_pool, g_chunk_fns, g_chunk_args, n_chunks) != 0)
            goto serial;
        ce_pool_wait(g_ce_pool);
        return;
    }
serial:
    ce_render_frame(vctx, clip_idx, universe, source_t, start_t, rate,
                    timeline_now, hold, gain, fade_mode, n_tasks,
                    out_idx, out, act, scores);
}
/* ── K3: 离线烘焙 — OpenMP 多核 + GPU 分派预留（ce_bake_sequence_omp）──────
 * 背景: 离线烘焙 = 整段效果预渲染成大矩阵（11382 台 × 160 帧 ≈ 182 万值）,
 *   无实时约束、数据搬移一次——多核/GPU 最理想场景。ce_compute_sequence
 *   已 C 化但串行; ce_full_mix_batch 已有 OpenMP（按域并行）。本函数提供
 *   compute 序列求值的 OpenMP 版: 任务 = (frame, chunk), chunk_size 默认
 *   1000 台 ≈ ~1ms/任务（11382×160 → 1824 任务）——大粒度并行正收益,
 *   离线单次大调用, 线程池 spin 占比可忽略。
 * 位级等价: 每任务只写 out 的独立区间——无共享写/无累积/无跨任务状态,
 *   与串行逐位一致（对拍见 bench/ce_fuzz_bake.py）。
 * GPU 分派预留（ce_gpu_api.h 未就绪, 约定如下供 GPU agent 对齐）:
 *   typedef int (*ce_gpu_bake_sequence_fn)(double fps, double speed,
 *       double phase_step, double lo, double hi, int wave_fn_idx,
 *       int n_frames, int n_fixtures, uint8_t* out);
 *   注册: prev = ce_gpu_bake_sequence_register(fn)（fn==NULL 取消）;
 *   分派序（本函数内）: GPU 钩子 → OpenMP → 串行。
 *   GPU 实现契约: 返回 1 = 已烘焙 out[n_frames*n_fixtures]（行主序, 值域
 *   [0,255]）且与 ce_compute_sequence 逐字节相等; 返回 0 = 本机无 GPU /
 *   驱动失败 → 调用方回退 OpenMP/串行。
 */
typedef int (*ce_gpu_bake_sequence_fn)(double fps, double speed,
                                       double phase_step, double lo, double hi,
                                       int wave_fn_idx, int n_frames,
                                       int n_fixtures, uint8_t* out);

static ce_gpu_bake_sequence_fn g_gpu_bake_sequence = NULL;

CE_API ce_gpu_bake_sequence_fn ce_gpu_bake_sequence_register(
    ce_gpu_bake_sequence_fn fn)
{
    ce_gpu_bake_sequence_fn old = g_gpu_bake_sequence;
    g_gpu_bake_sequence = fn;
    return old;
}

CE_API void ce_bake_sequence_omp(
    double fps, double speed, double phase_step,
    double lo, double hi, int wave_fn_idx,
    int n_frames, int n_fixtures,
    uint8_t* out, int n_threads, int chunk_size)
{
    int f;
    if (fps == 0.0 || isnan(fps))
        return;
    if (n_frames <= 0 || n_fixtures <= 0 || out == NULL)
        return;
    /* 1) GPU 分派点（ce_gpu_api.h 合入后由 GPU agent 注册实现） */
    if (g_gpu_bake_sequence != NULL &&
        g_gpu_bake_sequence(fps, speed, phase_step, lo, hi, wave_fn_idx,
                            n_frames, n_fixtures, out) != 0)
        return;
    /* 2) OpenMP: 任务 = (frame, chunk), 每任务写 out 独立区间 */
    if (chunk_size <= 0)
        chunk_size = 1000;
    if (n_threads < 1)
        n_threads = 1;
    if (n_threads > 1) {
        int n_chunks = (n_fixtures + chunk_size - 1) / chunk_size;
        int n_tasks = n_frames * n_chunks;
        if (n_tasks >= 2 && n_tasks / n_chunks == n_frames) {
            /* n_tasks 除法回验: 防 int 溢出（超 21 亿任务 → 串行兜底） */
            int tk;
            #pragma omp parallel for schedule(static) num_threads(n_threads)
            for (tk = 0; tk < n_tasks; tk++) {
                int fr = tk / n_chunks;
                int c = tk % n_chunks;
                int i0 = c * chunk_size;
                int i1 = i0 + chunk_size;
                if (i1 > n_fixtures)
                    i1 = n_fixtures;
                double t = (double)fr / fps;
                for (int i = i0; i < i1; i++) {
                    double phase = (double)i * phase_step;
                    double v = _ce_wave(t * speed + phase, wave_fn_idx);
                    double val = lo + v * (hi - lo);
                    int iv;
        if (val <= 0.0) iv = 0;        /* B2-2: 先值域后截断, 防 int32 溢出 UB */
        else if (val >= 255.0) iv = 255;
        else iv = (int)val;
                    out[(size_t)fr * (size_t)n_fixtures + (size_t)i] = (uint8_t)iv;
                }
            }
            return;
        }
    }
    /* 3) 串行兜底: 与 ce_compute_sequence 完全同路径 */
    for (f = 0; f < n_frames; f++) {
        double t = (double)f / fps;
        ce_wave_effect(t, speed, phase_step, lo, hi, wave_fn_idx,
                       n_fixtures, out + (size_t)f * (size_t)n_fixtures);
    }}
/* ── P9: C 侧发送环（stage C 流水线重叠 — 蓝图 §3.3/§3.4/§5）─────────
 * 常驻发送线程的字节通道: 一次预分配 3×512 记录环（512B/记录 ≈ 0.8MB,
 * 蓝图 §5: 3-4 槽 × 1MB 量级, 流式轮转, 零重分配）。
 *
 * 并发契约: 单生产者（tick 线程）+ 单消费者（常驻发送线程）。
 *   - 环形下标 head 仅生产者写, tail 仅消费者写（互不共享下标）;
 *   - count 经 Interlocked 维护（x86 锁前缀 RMW = 全屏障）;
 *   - 生产者: 先写 data, 再 count++; 消费者: 见 count>0 后读 data ——
 *     x86 TSO + Interlocked 屏障保证数据先于计数可见;
 *   - 环满 = 保序丢旧（M1 修复, AUDIT_BUGS_MEM）: push 覆盖环内最新旧记录
 *     （head-1 槽）, 最新记录进环。出队序 = 入队序的子序列（永不乱序）,
 *     接收端状态单调前进、收敛到最新; tick 线程延迟有界（≈ memcpy, 禁阻塞,
 *     消费者停摆时绝不冻结实时 tick 环）。禁止回退直发 —— 直发会让环内
 *     旧帧与直发新帧乱序交错（原 100ms 超时回退路径的缺陷, 已移除）;
 *   - 空 = 消费者事件等待（禁 spin）; stop 置位 + 事件唤醒 → pop 返回 0。
 * 输出边界不变: 消费者按 FIFO 把记录交给 _send_to_hw（顺序与字节均不变）。
 */

#define CE_SEND_RING_CAP 1536   /* 3 槽 × 512 记录（Art-Net universe 上限） */

typedef struct {
    uint16_t uni[CE_SEND_RING_CAP];
    uint8_t data[CE_SEND_RING_CAP][512];
    volatile LONG head;         /* 生产者下一写入位（仅生产者写） */
    volatile LONG tail;         /* 消费者下一读取位（仅消费者写） */
    volatile LONG count;        /* 在队记录数（Interlocked） */
    volatile LONG stopping;     /* 1=停机 */
    HANDLE ev_pushed;           /* 有数据可读（消费者等待） */
} ce_send_ring;

CE_API void* ce_send_ring_create(void)
{
    ce_send_ring* r = (ce_send_ring*)calloc(1, sizeof(ce_send_ring));
    if (r == NULL) return NULL;
    r->ev_pushed = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (r->ev_pushed == NULL) {
        free(r);
        return NULL;
    }
    return r;
}

CE_API void ce_send_ring_destroy(void* vring)
{
    ce_send_ring* r = (ce_send_ring*)vring;
    if (r == NULL) return;
    InterlockedExchange(&r->stopping, 1);
    SetEvent(r->ev_pushed);
    /* 调用方须先 join 消费者线程再 destroy（避免句柄与等待竞态） */
    CloseHandle(r->ev_pushed);
    free(r);
}

/* 入队一记录（512B 拷贝）。返回 1 成功 / -2 停机。
 * 环满（反压）= 保序丢旧（M1 修复）: 覆盖环内最新旧记录（head-1 槽）——
 * 满时 head==tail, 消费者读 tail 槽, 写 head-1 槽互不冲突; 消费者只在
 * count==0 时等 ev_pushed, 满时必在忙, 无需发事件。出队序 = 入队序的
 * 子序列（绝无新帧先于旧帧出队）, 接收端状态单调前进收敛到最新。 */
CE_API int ce_send_ring_push(void* vring, uint16_t uni, const uint8_t* data)
{
    ce_send_ring* r = (ce_send_ring*)vring;
    if (r == NULL || data == NULL) return -1;
    for (;;) {
        if (InterlockedCompareExchange(&r->stopping, 0, 0)) return -2;
        if (InterlockedCompareExchange(&r->count, 0, 0) < CE_SEND_RING_CAP) break;
        LONG h = r->head - 1;                /* 环满: 最新旧记录让位 */
        if (h < 0) h = CE_SEND_RING_CAP - 1;
        r->uni[h] = uni;
        memcpy(r->data[h], data, 512);
        _ReadWriteBarrier();                 /* 数据先于消费者读到该槽可见 */
        return 1;
    }
    LONG h = r->head;
    r->uni[h] = uni;
    memcpy(r->data[h], data, 512);
    _ReadWriteBarrier();                     /* 数据先于计数可见 */
    r->head = (h + 1) % CE_SEND_RING_CAP;
    InterlockedIncrement(&r->count);
    SetEvent(r->ev_pushed);
    return 1;
}

/* 取出一记录（512B 拷贝）。返回 1 有数据 / 0 停机。阻塞等待（事件, 禁 spin）。 */
CE_API int ce_send_ring_pop(void* vring, uint16_t* out_uni, uint8_t* out_data)
{
    ce_send_ring* r = (ce_send_ring*)vring;
    if (r == NULL) return 0;
    for (;;) {
        if (InterlockedCompareExchange(&r->stopping, 0, 0)) return 0;
        if (InterlockedCompareExchange(&r->count, 0, 0) > 0) {
            LONG t = r->tail;
            if (out_uni != NULL) *out_uni = r->uni[t];
            if (out_data != NULL) memcpy(out_data, r->data[t], 512);
            _ReadWriteBarrier();
            r->tail = (t + 1) % CE_SEND_RING_CAP;
            InterlockedDecrement(&r->count);
            return 1;
        }
        WaitForSingleObject(r->ev_pushed, INFINITE);
    }
}

/* 在队记录数（诊断/drain 用; 计数值不保证严格单调读, 只用于判零）。 */
CE_API int ce_send_ring_pending(void* vring)
{
    ce_send_ring* r = (ce_send_ring*)vring;
    return r == NULL ? 0 : (int)InterlockedCompareExchange(&r->count, 0, 0);
}

/* 停机: 置位 + 唤醒阻塞中的 push/pop。 */
CE_API int ce_send_ring_stop(void* vring)
{
    ce_send_ring* r = (ce_send_ring*)vring;
    if (r == NULL) return -1;
    InterlockedExchange(&r->stopping, 1);
    SetEvent(r->ev_pushed);
    return 0;
}
/* ═════════════════════════════════════════════════════════════════════════
 * T-recon2: MSA 音频结构分析纯数学段 → C（src/audio/music_structure_analyzer.py
 * _on_frame 热路径: 每 25ms 帧频实时 + 离线整曲烘焙 0.25s 步长共用）。
 *
 * Python 原逻辑（profile: _on_frame 每帧 ~660us, 其中 numpy mean 调用 ~18 次
 * ≈107us / _classify_section_v26 57us / _detect_buildup_v26 40us /
 * _compute_instruments_v26 35us（6×_weighted_band_score）/ sum 内建 22us /
 * sorted 600 元素 17us）——本批内核全部为纯函数叶子, 状态机留在 Python:
 *
 *   1. ce_mean_f64             ← np.mean(vals[start:end])（numpy f64
 *      add.reduce pairwise, 同 _ce_pairwise_sum_f64 —— ce_audio_bands 已
 *      对拍 0 差异; mean = pairwise_sum / count, 同 numpy true_divide）
 *   2. ce_sorted_low_mean_f64  ← sorted(vals)[:k] 后 np.mean —— 有限输入
 *      qsort 全序 = Python TimSort 全序（等值元素序无关: 零元素对求和
 *      无影响, pairwise 累加器从 +0.0 起步恒 +0.0 保号）; 输入含 NaN →
 *      返回 1（无全序, 调用方回退 Python sorted, 字节不变）
 *   3. ce_weighted_band_score_f64 ← _weighted_band_score: 两处 Python 3.13
 *      内建 sum() → Neumaier（_ce_py_sum_f64 同算法, 200k+ 对拍 0 差异）;
 *      max(w_sum, 1e-6): (w_sum < 1e-6) ? 1e-6 : w_sum（NaN → w_sum, 同 Python）
 *   4. ce_spectral_flux_f64    ← _spectral_flux: sum(max(0.0, b-p) ...)
 *      Neumaier; max(0.0, d) = (0.0 < d) ? d : 0.0（NaN → 0.0, 同 Python）
 *   5. ce_centroid_f64         ← _compute_centroid: sum(bands) 与
 *      sum(i*b ...) 均 Neumaier; 守卫比较逐字同 Python（NaN 语义一致:
 *      NaN <= 1e-6 → False → 继续）
 *   6. ce_novelty_f64          ← _compute_novelty: sum(abs(a-b)) Neumaier;
 *      max(1.0, n*0.3) = (1.0 < n*0.3) ? n*0.3 : 1.0; min(1.0, v) =
 *      (v < 1.0) ? v : 1.0（NaN → 1.0, 同 Python）
 *
 * 位级等价纪律: 全程 f64, /fp:precise（无 fast-math）; 所有 sum 均复刻
 * CPython 3.13 内建 sum（gh-100425 Neumaier 补偿）; mean 复刻 numpy 2.4.4
 * pairwise; 契约不满足（空/NaN 排序/内存失败）→ 调用方回退原版, 输出不变。
 * 新路径自门控: QASCADE_CT_ENGINE=1 且 DLL 含本批符号才挂; QASCADE_CE_AUDIO_MSA=0
 * 显式关闭。fuzz: ce_fuzz_audio_msa.py（每核 ≥10 万次, NaN/±inf/±0/次正规/
 * 边界）+ ce_fuzz_msa_state.py（整链状态逐帧深对拍）。
 */

static int _ce_dbl_cmp(const void* pa, const void* pb)
{
    double a = *(const double*)pa;
    double b = *(const double*)pb;
    return (a > b) - (a < b);   /* 有限全序; 等值（含 ±0）→ 0 */
}

/* np.mean(vals[start:end]) 位级等价; end <= start → NaN（hook 契约守卫
 * 保证不传空——原版 np.mean(空) 返回 nan + RuntimeWarning） */
CE_API double ce_mean_f64(const double* a, int start, int end)
{
    int n = end - start;
    if (n <= 0)
        return NAN;
    return _ce_pairwise_sum_f64(a + start, n) / (double)n;
}

/* np.mean(sorted(vals)[:k]) 位级等价; 返回 0 = out 有效, 1 = NaN 输入
 * （无全序）或参数非法 —— 调用方回退 Python 原版 */
CE_API int ce_sorted_low_mean_f64(const double* a, int n, int k, double* out)
{
    if (n <= 0 || k <= 0 || k > n) {
        *out = NAN;
        return 1;
    }
    for (int i = 0; i < n; i++) {
        if (a[i] != a[i])
            return 1;           /* NaN: Python sorted 无全序, 不可位级对齐 */
    }
    double* buf = (double*)malloc((size_t)n * sizeof(double));
    if (buf == NULL)
        return 1;
    memcpy(buf, a, (size_t)n * sizeof(double));
    qsort(buf, (size_t)n, sizeof(double), _ce_dbl_cmp);
    *out = _ce_pairwise_sum_f64(buf, k) / (double)k;
    free(buf);
    return 0;
}

/* _weighted_band_score: s = sum(b*w ...) Neumaier, w_sum = sum(w) Neumaier,
 * 返回 s / max(w_sum, 1e-6) */
CE_API double ce_weighted_band_score_f64(const double* bands,
                                         const double* weights, int n)
{
    if (n <= 0)
        return 0.0;
    double* s_arr = (double*)malloc((size_t)n * sizeof(double));
    if (s_arr == NULL)
        return 0.0;
    for (int i = 0; i < n; i++)
        s_arr[i] = bands[i] * weights[i];
    double s = _ce_py_sum_f64(s_arr, n);
    free(s_arr);
    double w_sum = _ce_py_sum_f64(weights, n);
    double denom = (w_sum < 1e-6) ? 1e-6 : w_sum;
    return s / denom;
}

/* _spectral_flux: sum(max(0.0, b - p) ...) Neumaier; NaN → 0.0 项 */
CE_API double ce_spectral_flux_f64(const double* bands, const double* prev,
                                   int n)
{
    if (n <= 0)
        return 0.0;
    double* s_arr = (double*)malloc((size_t)n * sizeof(double));
    if (s_arr == NULL)
        return 0.0;
    for (int i = 0; i < n; i++) {
        double d = bands[i] - prev[i];
        s_arr[i] = (0.0 < d) ? d : 0.0;
    }
    double flux = _ce_py_sum_f64(s_arr, n);
    free(s_arr);
    return flux;
}

/* _compute_centroid: 守卫比较逐字同 Python（NaN <= 1e-6 → False → 继续） */
CE_API double ce_centroid_f64(const double* bands, int n)
{
    double s = _ce_py_sum_f64(bands, n);
    if (s <= 1e-6)
        return 0.0;
    if (n <= 1)
        return 0.0;
    double denom = s * (double)(n - 1);   /* max(1, n-1): n>=2 → n-1 */
    if (denom <= 1e-12)
        return 0.0;
    double* s_arr = (double*)malloc((size_t)n * sizeof(double));
    if (s_arr == NULL)
        return NAN;
    for (int i = 0; i < n; i++)
        s_arr[i] = (double)i * bands[i];
    double num = _ce_py_sum_f64(s_arr, n);
    free(s_arr);
    return num / denom;
}

/* _compute_novelty: sum(abs(a - b)) Neumaier; max/min 复刻 Python 语义 */
CE_API double ce_novelty_f64(const double* bands, const double* prev, int n)
{
    if (n <= 0)
        return 0.0;
    double* s_arr = (double*)malloc((size_t)n * sizeof(double));
    if (s_arr == NULL)
        return 0.0;
    for (int i = 0; i < n; i++) {
        double d = bands[i] - prev[i];
        s_arr[i] = fabs(d);
    }
    double dd = _ce_py_sum_f64(s_arr, n);
    free(s_arr);
    double n03 = (double)n * 0.3;
    double denom = (1.0 < n03) ? n03 : 1.0;
    double v = dd / denom;
    return (v < 1.0) ? v : 1.0;
}

/* ─────────────────────────────────────────────────────────────────────────
 * T-recon2 复合内核: 每方法一次 ctypes 调用的切片均值批（ctypes 往返 ~3us
 * 摊销到整批; 逐槽位 = ce_mean_f64 同算法 → 位级一致; 未满足守卫的槽 = NAN,
 * 调用方只读守卫满足的槽。守卫公式与 ce_classify/ce_buildup/ce_on_frame
 * 逐行同构（Python 侧逐字照抄原版）:
 *   classify:  ch>=12 → third=max(3,ch_n//3); fh>0 → 全均值;
 *              fh>=12 → third=max(3,fh_n//3); rms>=20 → third=max(5,rms_n//3)
 *   buildup:   ch/rs>=20 → win=max(12,int(2.5*40))=100, third=max(3,win_n//3);
 *              fh_win>=6 → f_third=max(2,fh_win_n//3)
 *   energies:  三个窗口均值（空 → NAN 槽, 调用方守卫绕过）
 */

/* ce_classify_means_f64: out7 = [head_ch, tail_ch, avg_fh, tail_fh,
 * head_fh, tail_rms, head_rms] */
CE_API void ce_classify_means_f64(const double* ch, int ch_n,
                                  const double* fh, int fh_n,
                                  const double* rms, int rms_n,
                                  double* out7)
{
    out7[0] = NAN; out7[1] = NAN; out7[2] = NAN; out7[3] = NAN;
    out7[4] = NAN; out7[5] = NAN; out7[6] = NAN;
    if (ch_n >= 12) {
        int third = (ch_n / 3 > 3) ? ch_n / 3 : 3;
        out7[0] = _ce_pairwise_sum_f64(ch, third) / (double)third;
        out7[1] = _ce_pairwise_sum_f64(ch + ch_n - third, third) / (double)third;
    }
    if (fh_n > 0)
        out7[2] = _ce_pairwise_sum_f64(fh, fh_n) / (double)fh_n;
    if (fh_n >= 12) {
        int third = (fh_n / 3 > 3) ? fh_n / 3 : 3;
        out7[3] = _ce_pairwise_sum_f64(fh + fh_n - third, third) / (double)third;
        out7[4] = _ce_pairwise_sum_f64(fh, third) / (double)third;
    }
    if (rms_n >= 20) {
        int third = (rms_n / 3 > 5) ? rms_n / 3 : 5;
        out7[5] = _ce_pairwise_sum_f64(rms + rms_n - third, third) / (double)third;
        out7[6] = _ce_pairwise_sum_f64(rms, third) / (double)third;
    }
}

/* ce_buildup_means_f64: out6 = [tail_ch, head_ch, tail_rs, head_rs,
 * tail_fh, head_fh]（窗口 = 后 win=100 个元素; 守卫 ch_n<20 或 rs_n<20 →
 * 全槽 NAN, 调用方早退） */
CE_API void ce_buildup_means_f64(const double* ch, int ch_n,
                                 const double* rs, int rs_n,
                                 const double* fh, int fh_n,
                                 double* out6)
{
    out6[0] = NAN; out6[1] = NAN; out6[2] = NAN;
    out6[3] = NAN; out6[4] = NAN; out6[5] = NAN;
    if (ch_n < 20 || rs_n < 20)
        return;
    int win = 100;   /* max(12, int(2.5 * 40)) */
    int ch_win_n = (ch_n < win) ? ch_n : win;
    int rs_win_n = (rs_n < win) ? rs_n : win;
    int fh_win_n = (fh_n < win) ? fh_n : win;
    int third = (ch_win_n / 3 > 3) ? ch_win_n / 3 : 3;
    /* ch_win = ch[ch_n-ch_win_n : ch_n]; [-third:] 与 [:third] */
    out6[0] = _ce_pairwise_sum_f64(ch + ch_n - third, third) / (double)third;
    out6[1] = _ce_pairwise_sum_f64(ch + ch_n - ch_win_n, third) / (double)third;
    /* rs 窗口可能短于 third（rs_n < ch_win_n 时, Python 负索引钳制到窗口
       起点 → 取 min(third, rs_win_n) 个尾部/头部元素, 逐位同 Python） */
    {
        int rs_n0 = (third < rs_win_n) ? third : rs_win_n;
        out6[2] = _ce_pairwise_sum_f64(rs + rs_n - rs_n0, rs_n0) / (double)rs_n0;
        out6[3] = _ce_pairwise_sum_f64(rs + rs_n - rs_win_n, rs_n0) / (double)rs_n0;
    }
    if (fh_win_n >= 6) {
        int f_third = (fh_win_n / 3 > 2) ? fh_win_n / 3 : 2;
        out6[4] = _ce_pairwise_sum_f64(fh + fh_n - f_third, f_third)
                  / (double)f_third;
        out6[5] = _ce_pairwise_sum_f64(fh + fh_n - fh_win_n, f_third)
                  / (double)f_third;
    }
}

/* ce_energy_means_f64: out3 = [mean(s), mean(m), mean(l)]（空 → NAN 槽） */
CE_API void ce_energy_means_f64(const double* s, int s_n,
                                const double* m, int m_n,
                                const double* l, int l_n,
                                double* out3)
{
    out3[0] = (s_n > 0) ? _ce_pairwise_sum_f64(s, s_n) / (double)s_n : NAN;
    out3[1] = (m_n > 0) ? _ce_pairwise_sum_f64(m, m_n) / (double)m_n : NAN;
    out3[2] = (l_n > 0) ? _ce_pairwise_sum_f64(l, l_n) / (double)l_n : NAN;
}

/* ═════════════════════════════════════════════════════════════════════════
 * T-recon2 环形镜像内核: 窗口数据以 f64 环形缓冲常驻（镜像随 deque 追加同步,
 * hook 侧零转换直读）——ctypes 往返 + 逐帧 list→array 转换的 Python 开销
 * （~59us/帧）被消除, 均值计算全部在 C 内完成。
 *
 * 环形约定（与 hook 侧 _ce_mir 同步）: buf f64[buf_n], filled = 逻辑长度,
 * idx = 下一写入位。逻辑元素 i（0<=i<filled）:
 *   - filled < buf_n（暖机期）: buf[i]（追加到 buf[0..filled), idx==filled）
 *   - filled == buf_n（满）   : buf[(idx+i) % buf_n]
 * 逐槽算法与 ce_mean_f64 相同（_ce_pairwise_sum_f64）→ 位级一致; 守卫
 * 不满足的槽 = NAN, 调用方只读守卫满足的槽。暖机期窗口即 deque 内容同序
 * （逐位 = np.mean(list(deque))）。NaN 无特判（pairwise 复刻同 Python）;
 * 排序路径含 NaN → 返回 rc=1, 调用方回退原版。
 */

static int _ce_ring_copy(const double* buf, int buf_n, int filled, int idx,
                         int start, int end, double* tmp)
{
    if (buf_n <= 0 || filled <= 0 || start < 0 || end > filled || end <= start)
        return -1;
    int cnt = end - start;
    if (filled < buf_n) {
        memcpy(tmp, buf + start, (size_t)cnt * sizeof(double));
    } else {
        for (int i = start; i < end; i++)
            tmp[i - start] = buf[(idx + i) % buf_n];
    }
    return cnt;
}

/* 单窗口均值: 逻辑区间 [start, end) 的 mean（空/非法 → NAN） */
CE_API double ce_ring_mean_f64(const double* buf, int buf_n, int filled,
                               int idx, int start, int end)
{
    int cnt = end - start;
    if (cnt <= 0 || filled <= 0 || start < 0 || end > filled)
        return NAN;
    double* tmp = (double*)malloc((size_t)cnt * sizeof(double));
    if (tmp == NULL)
        return NAN;
    if (_ce_ring_copy(buf, buf_n, filled, idx, start, end, tmp) < 0) {
        free(tmp);
        return NAN;
    }
    double r = _ce_pairwise_sum_f64(tmp, cnt) / (double)cnt;
    free(tmp);
    return r;
}

/* 环形 sorted + mean[:k]; 返回 0 = out 有效, 1 = NaN/非法（调用方回退） */
CE_API int ce_ring_sorted_low_mean_f64(const double* buf, int buf_n,
                                       int filled, int idx, int k, double* out)
{
    if (filled <= 0 || k <= 0 || k > filled) {
        *out = NAN;
        return 1;
    }
    double* tmp = (double*)malloc((size_t)filled * sizeof(double));
    if (tmp == NULL)
        return 1;
    if (_ce_ring_copy(buf, buf_n, filled, idx, 0, filled, tmp) < 0) {
        free(tmp);
        return 1;
    }
    for (int i = 0; i < filled; i++) {
        if (tmp[i] != tmp[i]) {
            free(tmp);
            return 1;
        }
    }
    qsort(tmp, (size_t)filled, sizeof(double), _ce_dbl_cmp);
    *out = _ce_pairwise_sum_f64(tmp, k) / (double)k;
    free(tmp);
    return 0;
}

/* 三窗口均值批: out3 = [mean(s), mean(m), mean(l)]（空 → NAN 槽） */
CE_API void ce_ring_energy_means_f64(
    const double* sb, int sb_n, int s_filled, int s_idx,
    const double* mb, int mb_n, int m_filled, int m_idx,
    const double* lb, int lb_n, int l_filled, int l_idx, double* out3)
{
    out3[0] = ce_ring_mean_f64(sb, sb_n, s_filled, s_idx, 0, s_filled);
    out3[1] = ce_ring_mean_f64(mb, mb_n, m_filled, m_idx, 0, m_filled);
    out3[2] = ce_ring_mean_f64(lb, lb_n, l_filled, l_idx, 0, l_filled);
}

/* classify 批: out7 = [head_ch, tail_ch, avg_fh, tail_fh, head_fh,
 * tail_rms, head_rms]（守卫公式同 ce_classify_means_f64） */
CE_API void ce_ring_classify_means_f64(
    const double* cb, int cb_n, int c_filled, int c_idx,
    const double* fb, int fb_n, int f_filled, int f_idx,
    const double* rb, int rb_n, int r_filled, int r_idx, double* out7)
{
    out7[0] = NAN; out7[1] = NAN; out7[2] = NAN; out7[3] = NAN;
    out7[4] = NAN; out7[5] = NAN; out7[6] = NAN;
    if (c_filled >= 12) {
        int third = (c_filled / 3 > 3) ? c_filled / 3 : 3;
        out7[0] = ce_ring_mean_f64(cb, cb_n, c_filled, c_idx, 0, third);
        out7[1] = ce_ring_mean_f64(cb, cb_n, c_filled, c_idx,
                                   c_filled - third, c_filled);
    }
    if (f_filled > 0)
        out7[2] = ce_ring_mean_f64(fb, fb_n, f_filled, f_idx, 0, f_filled);
    if (f_filled >= 12) {
        int third = (f_filled / 3 > 3) ? f_filled / 3 : 3;
        out7[3] = ce_ring_mean_f64(fb, fb_n, f_filled, f_idx,
                                   f_filled - third, f_filled);
        out7[4] = ce_ring_mean_f64(fb, fb_n, f_filled, f_idx, 0, third);
    }
    if (r_filled >= 20) {
        int third = (r_filled / 3 > 5) ? r_filled / 3 : 5;
        out7[5] = ce_ring_mean_f64(rb, rb_n, r_filled, r_idx,
                                   r_filled - third, r_filled);
        out7[6] = ce_ring_mean_f64(rb, rb_n, r_filled, r_idx, 0, third);
    }
}

/* buildup 批: out6 = [tail_ch, head_ch, tail_rs, head_rs, tail_fh, head_fh]
 * （窗口 = 逻辑后 win=100 个; ch/rs <20 → 全槽 NAN, 调用方早退） */
CE_API void ce_ring_buildup_means_f64(
    const double* cb, int cb_n, int c_filled, int c_idx,
    const double* rb, int rb_n, int r_filled, int r_idx,
    const double* fb, int fb_n, int f_filled, int f_idx, double* out6)
{
    out6[0] = NAN; out6[1] = NAN; out6[2] = NAN;
    out6[3] = NAN; out6[4] = NAN; out6[5] = NAN;
    if (c_filled < 20 || r_filled < 20)
        return;
    int win = 100;   /* max(12, int(2.5*40)) */
    int c_win_n = (c_filled < win) ? c_filled : win;
    int r_win_n = (r_filled < win) ? r_filled : win;
    int f_win_n = (f_filled < win) ? f_filled : win;
    int third = (c_win_n / 3 > 3) ? c_win_n / 3 : 3;
    out6[0] = ce_ring_mean_f64(cb, cb_n, c_filled, c_idx,
                               c_filled - third, c_filled);
    out6[1] = ce_ring_mean_f64(cb, cb_n, c_filled, c_idx,
                               c_filled - c_win_n, c_filled - c_win_n + third);
    /* rs 窗口可能短于 third → 钳到窗口实际长度（Python 负索引语义） */
    {
        int r_n0 = (third < r_win_n) ? third : r_win_n;
        out6[2] = ce_ring_mean_f64(rb, rb_n, r_filled, r_idx,
                                   r_filled - r_n0, r_filled);
        out6[3] = ce_ring_mean_f64(rb, rb_n, r_filled, r_idx,
                                   r_filled - r_win_n, r_filled - r_win_n + r_n0);
    }
    if (f_win_n >= 6) {
        int f_third = (f_win_n / 3 > 2) ? f_win_n / 3 : 2;
        out6[4] = ce_ring_mean_f64(fb, fb_n, f_filled, f_idx,
                                   f_filled - f_third, f_filled);
        out6[5] = ce_ring_mean_f64(fb, fb_n, f_filled, f_idx,
                                   f_filled - f_win_n,
                                   f_filled - f_win_n + f_third);
    }
}

/* ═════════════════════════════════════════════════════════════════════════
 * T-recon2b: 6 乐器在场度得分一次 C 调用（bands[8] × 权重矩阵 w6[6][8]）。
 *
 * 对齐 src/audio/music_structure_analyzer.py _compute_instruments_v26 内
 * 6× _weighted_band_score(bands, weights): 逐行 = ce_weighted_band_score_f64
 * 同算法 —— s = Neumaier(b[i]*w[i]), w_sum = Neumaier(w), 返回
 * s / max(w_sum, 1e-6)。位级等价实测: 逐次 ctypes 封装 15.6us vs Python
 * 4.3us/次（转换+往返开销 > C 计算）——故 6 次合并为一次调用 + bands 转换
 * 1 次（实测 ~12us/帧 vs 纯 Python ~26us/帧）。
 *
 * 契约: 1 <= n_b <= 8（行宽 = n_b, MSA 恒 8）; 返回 0 = out6 有效,
 * 1 = 参数非法（调用方逐乐器回退 Python 原版, 输出字节不变）。
 * fuzz: ce_fuzz_audio_msa.py fuzz_instrument_scores（随机 bands/权重矩阵,
 * NaN/±inf/±0/次正规全含, 逐行对拍 py_weighted_band_score）。
 */
static double _ce_neumaier_prod(const double* a, const double* b, int n)
{
    /* 复刻 _ce_py_sum_f64 施于乘积数组 a[i]*b[i]（gh-100425 Neumaier） */
    if (n <= 0)
        return 0.0;
    double f_result = 0.0 + a[0] * b[0];
    double c = 0.0;
    for (int i = 1; i < n; i++) {
        double x = a[i] * b[i];
        double t = f_result + x;
        if (fabs(f_result) >= fabs(x))
            c += (f_result - t) + x;
        else
            c += (x - t) + f_result;
        f_result = t;
    }
    if (c != 0.0 && isfinite(c))
        f_result += c;
    return f_result;
}

CE_API int ce_instrument_scores_f64(const double* bands, int n_b,
                                    const double* w6, int n_i, double* out6)
{
    if (n_b <= 0 || n_b > 8 || n_i <= 0 || out6 == NULL)
        return 1;
    if (bands == NULL || w6 == NULL)
        return 1;
    for (int i = 0; i < n_i; i++) {
        const double* wrow = w6 + (size_t)i * (size_t)n_b;
        double s = _ce_neumaier_prod(bands, wrow, n_b);
        double w_sum = _ce_py_sum_f64(wrow, n_b);
        double denom = (w_sum < 1e-6) ? 1e-6 : w_sum;
        out6[i] = s / denom;
    }
    return 0;
}/* ==== T4续3: 精炼库(cur_*) 6 内核 + wave_rainbow/wave_dual ====
 * 对齐 effect_presets_curated.py 各 kernel 函数体（逐位复刻）:
 *   _scalar_kernel(shape)    -> ce_cur_scalar   (19 shape x 4 ease)
 *   _envelope_kernel(kind)   -> ce_cur_env      (9 kind)
 *   _chase_kernel(kind)      -> ce_cur_chase    (15 kind + 4 direction)
 *   _random_kernel(kind)     -> ce_cur_rand     (9 kind)
 *   _breathe_kernel(kind)    -> ce_cur_breathe  (3 kind)
 *   _color_kernel(kind)      -> ce_cur_color    (14 kind, RGB 3buf)
 *   _rainbow_wave / _dual_wave (effect_engine.py) -> ce_wave_rainbow / ce_wave_dual
 *   _randrun_kernel (R172 W2b)   -> ce_cur_randrun    (31 kind: v1 参数槽驱动
 *                                                     + E1 15-30 RR2 分发)
 *   _varyspeed_kernel (R172 W2b) -> ce_cur_varyspeed  (22 look × lookvar)
 *   _foldcascade_kernel (R172 W2b)-> ce_cur_fcdc      (26 kind: v1 + E1
 *                                                     fronts/device 分发表)
 * 位级纪律: 全程 double; phase % 1.0 / % 2.0 / % 3.0 用 _ce_mod_div;
 * int() 截断用 (long long)/(int) 强转; round() 用 ce_round_half_even;
 * 输出不 clamp (原版无 clamp 直写 int) -> int32 缓冲, hook 侧原样写回。
 * 随机源 _vnoise 逐位复刻 Python & 0xFFFFFFFF 语义。
 */

/* Python _vnoise :50 逐位复刻（任意精度 & 0xFFFFFFFF = uint64 低 32 位） */
static double _ce_vnoise(long long i, long long seed)
{
    unsigned long long n =
        (unsigned long long)((i * 374761393LL + seed * 668265263LL)
                             & 0xFFFFFFFFLL);
    n = ((n ^ (n >> 13)) * 1274126177ULL) & 0xFFFFFFFFULL;
    return (double)((n ^ (n >> 16)) & 0xFFFFULL) / 65535.0;
}

/* Python _clamp01 :27 */
static double _ce_clamp01_cur(double x)
{
    if (x < 0.0) return 0.0;
    if (x > 1.0) return 1.0;
    return x;
}

/* Python _smooth :31（先 clamp01 再 smoothstep） */
static double _ce_smooth_cur(double x)
{
    x = _ce_clamp01_cur(x);
    return x * x * (3.0 - 2.0 * x);
}

/* Python _ease :36（linear/smooth/in/out; 未知 mode -> linear） */
static double _ce_ease_cur(double x, int mode)
{
    x = _ce_clamp01_cur(x);
    if (mode == 1) return x * x * (3.0 - 2.0 * x);   /* smooth */
    if (mode == 2) return x * x;                     /* in */
    if (mode == 3) return 1.0 - (1.0 - x) * (1.0 - x); /* out */
    return x;                                        /* linear */
}

/* Python _waveshape :89（19 shape; p = phase % 1.0 模恒正） */
static double _ce_waveshape_cur(int shape, double phase, double duty)
{
    double p = _ce_mod_div(phase, 1.0);
    switch (shape) {
    case 0:  return 0.5 - 0.5 * cos(p * 2.0 * M_PI);            /* sine */
    case 1:  return 1.0 - fabs(2.0 * p - 1.0);                  /* tri */
    case 2:  return p;                                          /* saw_up */
    case 3:  return 1.0 - p;                                    /* saw_down */
    case 4:  return (p < duty) ? 1.0 : 0.0;                     /* square */
    case 5:  return (p < duty * 0.5) ? 1.0 : 0.0;               /* pulse */
    case 6:  return p * p;                                      /* expo */
    case 7:  return 1.0 - (1.0 - p) * (1.0 - p);                /* log */
    case 8:  return fabs(sin(p * M_PI * 2.0)) * (1.0 - p);      /* bounce */
    case 9: {                                                  /* fold */
        double x = _ce_mod_div(2.0 * p, 2.0);
        return 1.0 - fabs(x - 1.0);
    }
    case 10: return (p > 0.4 && p < 0.6) ? 0.0 : 1.0;           /* notch */
    case 11: return 0.5 - 0.5 * cos(p * 4.0 * M_PI);            /* twin */
    case 12: {                                                 /* trapz */
        double rise = (1.0 - duty) * 0.5;
        if (rise < 1e-4) rise = 1e-4;
        if (p < rise) return p / rise;
        if (p < rise + duty) return 1.0;
        if (p < rise + duty + rise)
            return 1.0 - (p - rise - duty) / rise;
        return 0.0;
    }
    case 13: {                                                 /* gauss */
        double sig = duty * 0.25;
        double d;
        if (sig < 0.02) sig = 0.02;
        d = p - 0.5;
        return exp(-(d * d) / (2.0 * sig * sig));
    }
    case 14: return fabs(sin(p * 2.0 * M_PI));                  /* rectsin */
    case 15: {                                                 /* skewtri */
        double pk = duty;
        double v;
        if (pk > 0.99) pk = 0.99;
        if (pk < 0.01) pk = 0.01;
        v = (p < pk) ? p / pk : (1.0 - p) / (1.0 - pk);
        return v;
    }
    case 16:                                                   /* dpulse */
        if (p < duty * 0.5) return 1.0;
        if (p >= 0.5 && p < 0.5 + duty * 0.5) return 1.0;
        return 0.0;
    case 17: {                                                 /* flattop */
        double v = 0.5 - 0.5 * cos(p * 2.0 * M_PI);
        double m = v * 1.6;
        return (m > 1.0) ? 1.0 : m;
    }
    case 18: {                                                 /* stairsin */
        double v = 0.5 - 0.5 * cos(p * 2.0 * M_PI);
        long long steps = (long long)ce_round_half_even(2.0 + duty * 8.0);
        if (steps < 2) steps = 2;
        return floor(v * (double)steps) / (double)(steps - 1);
    }
    default: return 0.5 - 0.5 * cos(p * 2.0 * M_PI);
    }
}

/* Python _kelvin_rgb :70（返回 int 域; clamp 后向零截断） */
static void _ce_kelvin_rgb(double k, double* r, double* g, double* b)
{
    double rv, gv, bv;
    if (k < 1000.0) k = 1000.0;
    else if (k > 12000.0) k = 12000.0;
    k /= 100.0;
    if (k <= 66.0) {
        rv = 255.0;
        gv = (k > 0.0) ? 99.47 * log(k) - 161.12 : 0.0;
    } else {
        rv = 329.7 * pow(k - 60.0, -0.1332);
        gv = 288.12 * pow(k - 60.0, -0.0755);
    }
    if (k >= 66.0) bv = 255.0;
    else if (k <= 19.0) bv = 0.0;
    else bv = 138.52 * log(k - 10.0) - 305.04;
    if (rv < 0.0) rv = 0.0; else if (rv > 255.0) rv = 255.0;
    if (gv < 0.0) gv = 0.0; else if (gv > 255.0) gv = 255.0;
    if (bv < 0.0) bv = 0.0; else if (bv > 255.0) bv = 255.0;
    *r = (double)(long long)rv;
    *g = (double)(long long)gv;
    *b = (double)(long long)bv;
}

/* 标量波形内核（_scalar_kernel :152; 19 shape x 4 ease） */
CE_API void ce_cur_scalar(
    int shape_idx, int ease_idx,
    double t, double speed, double freq, double duty,
    double lo, double hi, int n, int32_t* out)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double ph = pos * freq - t * speed;
        double w = _ce_ease_cur(_ce_waveshape_cur(shape_idx, ph, duty),
                                ease_idx);
        double val = lo + w * (hi - lo);
        out[i] = (int32_t)(long long)val;
    }
}

/* 包络内核（_envelope_kernel :170; 9 kind） */
CE_API void ce_cur_env(
    int kind, double t, double lo, double hi, double speed,
    double attack, double decay, int steps, double spread, int seed,
    int n, int32_t* out)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    double period = 1.0 / ((speed < 0.01) ? 0.01 : speed);
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double lt = _ce_mod_div(t + pos * spread * period, period);
        double ph = lt / period;
        double w;
        double a_den = (attack < 1e-4) ? 1e-4 : attack;
        double d_den = (decay < 1e-4) ? 1e-4 : decay;
        switch (kind) {
        case 0:  /* adsr */
            if (ph < attack)
                w = ph / a_den;
            else if (ph < attack + decay)
                w = 1.0 - 0.4 * (ph - attack) / d_den;
            else if (ph < 0.8)
                w = 0.6;
            else
                w = 0.6 * (1.0 - (ph - 0.8) / 0.2);
            break;
        case 1:  /* sh */
            w = _ce_vnoise((long long)(t * speed) + (long long)i * 7, seed);
            break;
        case 2:  /* step */
            w = floor(ph * (double)steps)
                / (double)(steps > 1 ? steps - 1 : 1);
            break;
        case 3:  /* ramp_hold */
            w = (ph < 0.85) ? ((ph / 0.6 < 1.0) ? ph / 0.6 : 1.0) : 0.0;
            break;
        case 4:  /* slew */
            w = _ce_smooth_cur(0.5 - 0.5 * cos(ph * 2.0 * M_PI));
            break;
        case 5:  /* pluck */
            w = exp(-ph / ((decay < 0.02) ? 0.02 : decay));
            break;
        case 6:  /* swell */
            w = _ce_smooth_cur(
                (ph / ((attack < 0.02) ? 0.02 : attack) < 1.0)
                    ? ph / ((attack < 0.02) ? 0.02 : attack) : 1.0);
            break;
        case 7:  /* multistrike */
            { int hits = steps > 2 ? steps : 2;
              double lp = _ce_mod_div(ph * (double)hits, 1.0);
              w = exp(-lp / ((decay < 0.02) ? 0.02 : decay)); }
            break;
        default:
            w = ph;
            break;
        }
        {
            double cw = _ce_clamp01_cur(w);
            out[i] = (int32_t)(long long)(lo + cw * (hi - lo));
        }
    }
}

/* 追逐内核（_chase_kernel :217; 15 kind x 4 direction） */
CE_API void ce_cur_chase(
    int kind, int dir_idx,
    double t, double lo, double hi, double speed,
    double width, double tail, int heads, int n, int32_t* out)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    double head = _ce_mod_div(t * speed, 1.0);
    double w_den = (width < 0.01) ? 0.01 : width;
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double dist, back, w;
        if (dir_idx == 1) pos = 1.0 - pos;                       /* 反向 */
        else if (dir_idx == 2) pos = fabs(pos - 0.5) * 2.0;      /* 中心向外 */
        else if (dir_idx == 3) pos = 1.0 - fabs(pos - 0.5) * 2.0;/* 两侧向中 */
        dist = _ce_mod_div(pos - head, 1.0);
        switch (kind) {
        case 0:  /* comet */
            w = (dist < tail) ? exp(-dist / w_den) : 0.0;
            break;
        case 1:  /* dot */
            w = (dist < width) ? 1.0 : 0.0;
            break;
        case 2:  /* scan */
            back = _ce_mod_div(head - pos, 1.0);
            w = ((dist < back ? dist : back) < width) ? 1.0 : 0.0;
            break;
        case 3:  /* wipe */
            w = (pos < head) ? 1.0 : 0.0;
            break;
        case 4:  /* wave-saw-tool */
            w = (dist < width * 2.0)
                ? _ce_smooth_cur(1.0 - dist / w_den) : 0.0;
            break;
        case 5:  /* bounce */
            { double bh = 1.0 - fabs(_ce_mod_div(t * speed, 2.0) - 1.0);
              w = exp(-fabs(pos - bh) / w_den); }
            break;
        case 6:  /* marquee */
            { long long grp = (long long)ce_round_half_even(
                  1.0 / ((width < 0.05) ? 0.05 : width));
              long long idx;
              if (grp < 2) grp = 2;
              idx = (long long)((double)i + t * speed * (double)grp);
              w = (_ce_int_mod(idx, (int)grp) == 0) ? 1.0 : 0.0; }
            break;
        case 7:  /* kitt */
            { double bh = 1.0 - fabs(_ce_mod_div(t * speed, 2.0) - 1.0);
              w = exp(-fabs(pos - bh) / w_den); }
            break;
        case 8:  /* build */
            w = (pos <= head) ? 1.0 : 0.0;
            break;
        case 9:  /* dissolve */
            w = (pos <= head) ? 0.0 : 1.0;
            break;
        case 10: /* jumpseq */
            { double order = _ce_vnoise(i, 17);
              double d = _ce_mod_div(order - head, 1.0);
              w = (d < width) ? 1.0 : 0.0; }
            break;
        case 11: /* collide */
            { double d1 = _ce_mod_div(pos - head, 1.0);
              double d2 = _ce_mod_div(1.0 - pos - head, 1.0);
              double e1 = (d1 < tail) ? exp(-d1 / w_den) : 0.0;
              double e2 = (d2 < tail) ? exp(-d2 / w_den) : 0.0;
              w = (e1 > e2) ? e1 : e2; }
            break;
        case 12: /* multihead */
            { int hh;
              w = 0.0;
              if (heads < 2) heads = 2;
              for (hh = 0; hh < heads; hh++) {
                  double d = _ce_mod_div(
                      pos - (head + (double)hh / (double)heads), 1.0);
                  double e = (d < tail) ? exp(-d / w_den) : 0.0;
                  if (e > w) w = e;
              } }
            break;
        case 13: /* levelbar */
            { double lvl = 0.5 - 0.5 * cos(t * speed * 2.0 * M_PI);
              w = (pos <= lvl) ? 1.0 : 0.0; }
            break;
        case 14: /* mirror */
            { double m = fabs(pos - 0.5) * 2.0;
              double d = _ce_mod_div(m - head, 1.0);
              w = (d < tail) ? exp(-d / w_den) : 0.0; }
            break;
        default:
            w = (dist < width) ? 1.0 : 0.0;
            break;
        }
        {
            double cw = _ce_clamp01_cur(w);
            out[i] = (int32_t)(long long)(lo + cw * (hi - lo));
        }
    }
}

/* 随机内核（_random_kernel :291; 9 kind） */
CE_API void ce_cur_rand(
    int kind, double t, double lo, double hi, double speed,
    double density, int seed, int n, int32_t* out)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    long long tick = (long long)(t * speed * 6.0);
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double r = _ce_vnoise((long long)i * 13 + tick, seed);
        double w;
        switch (kind) {
        case 0:  /* sparkle */
            w = (r < density) ? 1.0 : 0.0;
            break;
        case 1:  /* twinkle */
            { double base = _ce_vnoise(i, seed);
              w = (base < density + 0.4)
                  ? _ce_smooth_cur(0.5 + 0.5 * sin(t * speed * 2.0
                                                   + base * 6.28))
                  : 0.1; }
            break;
        case 2:  /* jitter */
            w = 0.5 + (r - 0.5) * density * 2.0;
            break;
        case 3:  /* perlin */
            { double a = _ce_vnoise(i, seed);
              double b = _ce_vnoise((long long)i + 1, seed);
              double fphase = _ce_mod_div(t * speed * 0.3, 1.0);
              w = a + (b - a) * _ce_smooth_cur(fphase); }
            break;
        case 4:  /* popcorn */
            { long long period = (long long)(
                  8.0 / ((density < 0.05) ? 0.05 : density));
              long long tick4, bin_i;
              int ignite;
              double ww;
              if (period < 1) period = 1;
              tick4 = (long long)(t * speed * 4.0);
              bin_i = _ce_int_mod(tick4 + (long long)i * 7, (int)period);
              ignite = _ce_vnoise((long long)i * 13 + (tick4 - bin_i), seed)
                       < density;
              ww = ((double)period * 0.35 < 1.0) ? 1.0
                                                 : (double)period * 0.35;
              w = ignite ? exp(-(double)bin_i / ww) : 0.0; }
            break;
        case 5:  /* glitch */
            { long long tick8 = (long long)(t * speed * 8.0);
              double rr = _ce_vnoise((long long)i * 5 + tick8, seed);
              w = (rr > (1.0 - density)) ? rr
                  : ((rr < density * 0.5) ? 0.0 : 0.15); }
            break;
        case 6:  /* lightning */
            { long long tick6 = (long long)(t * speed * 6.0);
              double ph = _ce_mod_div(t * speed * 6.0, 1.0);
              int strike = _ce_vnoise((long long)(i / 3) + tick6, seed)
                           < density * 0.5;
              w = strike ? ((ph < 0.1) ? 1.0
                             : 0.4 * exp(-(ph - 0.1) * 6.0)) : 0.0; }
            break;
        case 7:  /* ripple */
            { double acc = 0.0;
              int k;
              for (k = 0; k < 3; k++) {
                  double tt = _ce_mod_div(
                      t * speed + _ce_vnoise(k, seed) * 3.0, 3.0);
                  double c = _ce_vnoise(
                      (long long)k + (long long)((t * speed) / 3.0),
                      seed + 1);
                  double d = fabs(pos - c);
                  double v = cos((d * 12.0 - tt * 6.0)) * exp(-tt * 0.8)
                             * exp(-d * 4.0);
                  if (v > acc) acc = v;
              }
              w = _ce_clamp01_cur(acc); }
            break;
        default:
            w = r;
            break;
        }
        {
            double cw = _ce_clamp01_cur(w);
            out[i] = (int32_t)(long long)(lo + cw * (hi - lo));
        }
    }
}

/* 呼吸内核（_breathe_kernel :344; 3 kind） */
CE_API void ce_cur_breathe(
    int kind, double t, double lo, double hi, double speed, double spread,
    int n, int32_t* out)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double ph = t * speed + pos * spread;
        double w;
        switch (kind) {
        case 0:  /* breathe */
            w = _ce_smooth_cur(0.5 - 0.5 * cos(ph * 2.0 * M_PI));
            break;
        case 1:  /* heartbeat */
            { double p = _ce_mod_div(ph, 1.0);
              double g1 = (p - 0.1) * (p - 0.1);
              double g2 = (p - 0.3) * (p - 0.3);
              w = exp(-g1 / 0.004) + 0.6 * exp(-g2 / 0.004); }
            break;
        case 2:  /* tide */
            w = 0.5 - 0.5 * cos(ph * 2.0 * M_PI)
                + 0.15 * sin(ph * 6.0 * M_PI);
            break;
        default:
            w = 0.5 - 0.5 * cos(ph * 2.0 * M_PI);
            break;
        }
        {
            double cw = _ce_clamp01_cur(w);
            out[i] = (int32_t)(long long)(lo + cw * (hi - lo));
        }
    }
}

/* 颜色内核（_color_kernel :368; 14 kind, RGB 三缓冲 int32） */
CE_API void ce_cur_color(
    int kind, double t, double speed, double sat, double bright,
    double freq, double spread, int seed,
    double k_lo, double k_hi, double hue_a, double hue_b,
    int n, int32_t* out_r, int32_t* out_g, int32_t* out_b)
{
    if (n <= 0) return;
    double denom = (double)(n > 1 ? n - 1 : 1);
    for (int i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double r, g, b;
        double h;
        int domain255 = 0;   /* 1 = r/g/b 已是 0-255 int 域（kelvin 系） */
        switch (kind) {
        case 0:  /* rainbow */
            h = _ce_mod_div(pos * freq + t * speed, 1.0);
            _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b);
            break;
        case 1:  /* hue_rot */
            h = _ce_mod_div(t * speed + pos * spread, 1.0);
            _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b);
            break;
        case 2:  /* colortemp */
            { double w = 0.5 - 0.5 * cos(
                  (t * speed + pos * spread) * 2.0 * M_PI);
              double kr, kg, kb;
              _ce_kelvin_rgb(k_lo + w * (k_hi - k_lo), &kr, &kg, &kb);
              domain255 = 1;
              r = kr * bright; g = kg * bright; b = kb * bright; }
            break;
        case 3:  /* plasma */
            { double v = (sin(pos * 6.0 * freq + t * speed)
                          + sin(pos * 9.0 * freq - t * speed * 1.3));
              h = _ce_mod_div(0.5 + 0.5 * v / 2.0, 1.0);
              _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b); }
            break;
        case 4:  /* perlin_color */
            { double a = _ce_vnoise(i, seed);
              double bb = _ce_vnoise((long long)i + 1, seed);
              double fphase = _ce_mod_div(t * speed, 1.0);
              h = _ce_mod_div(a + (bb - a) * _ce_smooth_cur(fphase), 1.0);
              _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b); }
            break;
        case 5:  /* fire */
            { double fl = 0.5 + 0.5 * sin(t * speed * 3.0 + pos * 8.0)
                          * _ce_vnoise(i, (long long)(t * 4.0));
              h = 0.02 + 0.08 * fl;
              _ce_hsv_to_rgb(h, 1.0,
                             _ce_clamp01_cur(0.4 + fl) * bright,
                             &r, &g, &b); }
            break;
        case 6:  /* warmcool */
            { double w = 0.5 - 0.5 * cos(
                  (t * speed + pos) * 2.0 * M_PI);
              double kr, kg, kb;
              _ce_kelvin_rgb(2400.0 + w * 4000.0, &kr, &kg, &kb);
              domain255 = 1;
              r = kr; g = kg; b = kb; }
            break;
        case 7:  /* dualtone */
            { double w = 0.5 + 0.5 * cos(
                  (t * speed + pos * freq) * 2.0 * M_PI);
              h = _ce_mod_div(hue_a * w + hue_b * (1.0 - w), 1.0);
              _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b); }
            break;
        case 8:  /* tricolor */
            { long long idx = _ce_int_mod(
                  (long long)((t * speed + pos) * 3.0), 3);
              h = (idx == 0) ? 0.0 : (idx == 1) ? 0.33 : 0.66;
              _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b); }
            break;
        case 9:  /* colorpulse */
            { double pulse = sin((t * speed + pos) * M_PI);
              if (pulse < 0.0) pulse = 0.0;
              h = _ce_mod_div(pos * freq + t * speed * 0.2, 1.0);
              _ce_hsv_to_rgb(h, sat, bright * pulse, &r, &g, &b); }
            break;
        case 10: /* colorwave */
            { double w = 0.5 - 0.5 * cos(
                  (pos * freq - t * speed) * 2.0 * M_PI);
              h = _ce_mod_div(0.55 + 0.25 * w, 1.0);
              _ce_hsv_to_rgb(h, sat, bright * (0.4 + 0.6 * w),
                             &r, &g, &b); }
            break;
        case 11: /* starfield */
            { double tw = _ce_vnoise(i, (long long)(t * speed * 2.0));
              int lit = tw < 0.25;
              h = _ce_vnoise(i, 7);
              _ce_hsv_to_rgb(h, sat * 0.6, lit ? bright : 0.05,
                             &r, &g, &b); }
            break;
        case 12: /* sparkles */
            { double v = sin(pos * 3.0 + t * speed)
                         + 0.5 * sin(pos * 7.0 - t * speed * 0.6);
              h = _ce_mod_div(0.4 + 0.18 * v, 1.0);
              _ce_hsv_to_rgb(h, sat,
                             bright * _ce_clamp01_cur(0.4 + 0.4 * v),
                             &r, &g, &b); }
            break;
        case 13: /* ocean */
            { double v = 0.5 - 0.5 * cos(
                  (pos * freq - t * speed) * 2.0 * M_PI);
              h = _ce_mod_div(0.5 + 0.08 * v, 1.0);
              _ce_hsv_to_rgb(h, sat, bright * (0.5 + 0.5 * v),
                             &r, &g, &b); }
            break;
        default: /* rainbow */
            h = _ce_mod_div(pos + t * speed, 1.0);
            _ce_hsv_to_rgb(h, sat, bright, &r, &g, &b);
            break;
        }
        if (domain255) {
            out_r[i] = (int32_t)(long long)r;
            out_g[i] = (int32_t)(long long)g;
            out_b[i] = (int32_t)(long long)b;
        } else {
            out_r[i] = (int32_t)(long long)(r * 255.0);
            out_g[i] = (int32_t)(long long)(g * 255.0);
            out_b[i] = (int32_t)(long long)(b * 255.0);
        }
    }
}

/* wave_rainbow（_rainbow_wave :1491; RGB 三缓冲 int32） */
CE_API void ce_wave_rainbow(
    double t, double speed, double phase_step, double brightness,
    int n, int32_t* out_r, int32_t* out_g, int32_t* out_b)
{
    if (n <= 0) return;
    for (int i = 0; i < n; i++) {
        double hue = _ce_mod_div(t * speed + (double)i * phase_step, 1.0);
        double r, g, b;
        _ce_hsv_to_rgb(hue, 1.0, brightness, &r, &g, &b);
        out_r[i] = (int32_t)(long long)(r * 255.0);
        out_g[i] = (int32_t)(long long)(g * 255.0);
        out_b[i] = (int32_t)(long long)(b * 255.0);
    }
}

/* wave_dual（_dual_wave :1520; 单通道, clamp 0-255 同原版） */
CE_API void ce_wave_dual(
    double t, double speed, double phase_step, double lo, double hi,
    int n, int32_t* out)
{
    if (n <= 0) return;
    for (int i = 0; i < n; i++) {
        double v1 = (sin((t * speed + (double)i * phase_step) * 2.0 * M_PI)
                     + 1.0) / 2.0;
        double v2 = (sin((t * speed - (double)i * phase_step) * 2.0 * M_PI)
                     + 1.0) / 2.0;
        double v = (v1 + v2) / 2.0;
        double val = lo + v * (hi - lo);
        double _dv = val;

        int iv;

        if (_dv <= 0.0) iv = 0;        /* B2-2 值域先判断 */

        else if (_dv >= 255.0) iv = 255;

        else iv = (int)_dv;
        out[i] = (int32_t)iv;
    }
}

/* ── R172 三族新效果（W2b; docs/clip_studio_r172_design.md §5.4）──────────
 * 对齐 src/ui/effect_presets_curated.py W2a 三内核（逐位复刻; 变体差异全在
 * 参数/标志槽, 由 Python wrapper 从 W2a 单真源表解析 → C 无变体分支表）:
 *   _randrun_kernel(:2949)     -> ce_cur_randrun
 *   _varyspeed_kernel(:3129)   -> ce_cur_varyspeed
 *   _foldcascade_kernel(:3260) -> ce_cur_fcdc
 * 位级纪律（沿本区既有 ce_cur_*）: 整数哈希复用 _ce_vnoise; 浮点 % 用
 * _ce_mod_div（CPython float_rem）; int() 截断 (long long) 强转; Python **
 * 以 pow 同参复刻; 浮点运算顺序逐行对齐 Python（IEEE double 顺序不同即失配）。
 * randrun: 触发链无缓存重算（Python _RANDRUN_SITES 缓存只增不改 → 重算同值）,
 *   链值按 _RANDRUN_CAP=4096 环回取（k ≥ CAP 时 Python sites[k%CAP] 语义）,
 *   单趟重算 + 环回窗口捕获, O(1) 空间; 包起点吸附设备格点 int(floor(site))。
 * varyspeed: t_eff = Φ(t) 解析积分同式; swing/floor 全零恒速快路径同式。
 * fcdc: 闭式波序/落点接力同式; pause 静默窗整波写 lo; 回卷/淡接/花开交接同式。
 * 输出 int() 直写 int32（原版权值 _clamp01 后无 clamp 直写）。
 */

/* Python int % n（n > 0）恒非负语义（防御性; t ≥ 0 时 k ≥ 0 同 plain %） */
static long long _ce_pmod_ll(long long a, long long b)
{
    long long r = a % b;
    if (r < 0) r += b;
    return r;
}

/* ══ E1 扩容（R172 三族; R173 精编后留 +27）: randrun RR2 每 kind 权重函数 ═
 * Python 真源: effect_presets_curated.py :3336-3711（_rr2_env/_rr2_zonepack/
 * _rr2_perm + _rr2_* 8 fn）+ _randrun_chain :2836。kind 15-30 对齐
 * qascade_ce._CUR_RANDRUN_KIND 扩容序（dualzone..wavewalk; R173 下架号留空不重编）。
 * 位级纪律: 链值 O(k) 单趟重算（与 Python 模块级缓存值逐位同, 递推式同序）;
 * float % 用 _ce_mod_div; int() 截断用 (long long); int % 用 _ce_pmod_ll;
 * Python `x ** 2/3/4` 用 pow()（同 UCRT）; 运算顺序逐行对照 Python。 */

/* Python _rr2_env :3336 — 单触发包络（起-燃-衰减） */
static double _ce_rr2_env(double tau, double attack, double dscale, int soft)
{
    if (tau < 0.0) return 0.0;
    if (tau < attack)
        return soft ? _ce_smooth_cur(tau / attack) : tau / attack;
    return exp(-(tau - attack) / dscale);
}

/* Python _randrun_chain :2836 窗口捕获（值与模块级缓存版逐位同）:
 * site_0 = _vnoise(101,seed)*n; step_j = (_vnoise(j*7+3,seed)-0.5)*2*span;
 * site_j = (site_{j-1}+step_j) % n; span = max(1.0,(1-prox)*n)。
 * 窗口 win[k-k_lo]（调用方保证 k_lo ≥ 0、容量 ≥ k_hi-k_lo+1）。 */
static void _ce_rr2_chain_win(double* site_win, double* step_win,
                              long long seed, int nn, double prox,
                              long long k_lo, long long k_hi)
{
    double span = (1.0 - prox) * (double)nn;
    double site = _ce_vnoise(101, seed) * (double)nn;
    long long j;
    if (span < 1.0) span = 1.0;
    if (k_lo <= 0) { site_win[0 - k_lo] = site; step_win[0 - k_lo] = 0.0; }
    for (j = 1; j <= k_hi; j++) {
        double step = (_ce_vnoise(j * 7 + 3, seed) - 0.5) * 2.0 * span;
        site = _ce_mod_div(site + step, (double)nn);
        if (j >= k_lo) {
            site_win[j - k_lo] = site;
            step_win[j - k_lo] = step;
        }
    }
}

/* Python _rr_pack_dir :2877 — 跑动包首方向（0=跟随 1=交替 其余=哈希） */
static double _ce_rr2_pack_dir(int dmode, long long k, double step,
                               long long seed)
{
    if (dmode == 0) return (step >= 0.0) ? 1.0 : -1.0;
    if (dmode == 1) return ((k % 2) == 0) ? 1.0 : -1.0;
    return (_ce_vnoise(k * 3 + 1, seed) < 0.5) ? 1.0 : -1.0;
}

/* Python _rr2_perm :3379 — Fisher-Yates 种子全排列（_vnoise 驱动, 同序） */
static void _ce_rr2_perm_fill(int* perm, int nn, long long seed)
{
    int i;
    for (i = 0; i < nn; i++) perm[i] = i;
    for (i = nn - 1; i >= 1; i--) {
        long long j = (long long)(_ce_vnoise((long long)i * 31 + 7, seed + 13)
                                  * (double)(i + 1));
        int tmp;
        j = _ce_pmod_ll(j, (long long)(i + 1));
        tmp = perm[i];
        perm[i] = perm[j];
        perm[j] = tmp;
    }
}

/* E1 RR2 kind 15-30 分发（每 kind 独立权重函数, 对照 _RR2_FNS.update :3704） */
static void _ce_randrun_rr2(
    int kind,
    double t, double lo, double hi,
    double rate, double phase_gap, double decay,
    int run_size, int seed,
    int dmode, int soft, int punch,
    double density, double mod_depth, double prox_raw, int has_prox,
    int n, int32_t* out)
{
    const long long CAP = 4096;             /* Python _RANDRUN_CAP */
    double span_run, dscale, attack, tr, gap;
    long long k0;
    int i, lookback;

    span_run = (double)((run_size < n) ? ((run_size < 1) ? 1 : run_size) : n);
    dscale = decay * 0.6;
    if (dscale < 0.06) dscale = 0.06;
    attack = soft ? 0.3 : (punch ? 0.05 : 0.15);
    lookback = (decay > 0.45) ? 3 : 2;
    tr = t * rate;
    k0 = (long long)tr;                     /* Python int() 截断 */
    gap = phase_gap;
    switch (kind) {
    case 15: {  /* dualzone: 左右半区独立双链（seed / seed+991）: _rr2_dualzone */
        int half = (n + 1) / 2;
        double pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.6);
        double sw0[8], tw0[8], sw1[8], tw1[8];
        long long k_lo = k0 - lookback;
        if (k_lo < 0) k_lo = 0;
        _ce_rr2_chain_win(sw0, tw0, seed, half, pd, k_lo, k0);
        if (n - half > 0)
            _ce_rr2_chain_win(sw1, tw1, seed + 991, n - half, pd, k_lo, k0);
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            int back;
            for (back = 0; back <= lookback; back++) {
                long long k = k0 - back;
                double tau0;
                int z;
                if (k < 0) continue;
                tau0 = tr - (double)k;
                for (z = 0; z < 2; z++) {
                    int z0 = (z == 0) ? 0 : half;
                    int z1 = (z == 0) ? half : n;
                    int zn = z1 - z0;
                    long long idx;
                    double start, d1, rel, span_z, envv;
                    if (zn <= 0) continue;
                    if (i < z0 || i >= z1) continue;   /* _rr2_zonepack 半区门 */
                    idx = k - k_lo;
                    d1 = _ce_rr2_pack_dir(dmode, k, (z == 0) ? tw0[idx] : tw1[idx],
                                          seed);
                    start = (double)z0 + (double)_ce_pmod_ll(
                        (long long)((z == 0) ? sw0[idx] : sw1[idx]), zn);
                    span_z = span_run;
                    if (span_z > (double)zn) span_z = (double)zn;
                    if (span_z < 1.0) span_z = 1.0;
                    rel = _ce_mod_div(((double)i - start) * d1, (double)zn);
                    if (rel >= span_z) continue;
                    envv = _ce_rr2_env(tau0 - gap * (rel / span_z),
                                       attack, dscale, soft);
                    if (envv > acc) acc = envv;
                }
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    case 16: {  /* shufwalk: 种子全排列依次触发: _rr2_shufwalk（_rr2_perm） */
        int* perm = (int*)malloc((size_t)n * sizeof(int));
        if (perm == NULL) {
            for (i = 0; i < n; i++)
                out[i] = (int32_t)(long long)lo;    /* 防御（实际不可达） */
            return;
        }
        _ce_rr2_perm_fill(perm, n, seed);
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            int back;
            for (back = 0; back <= lookback; back++) {
                long long k = k0 - back;
                double tau0, sm, c, envv, wv, denom, dd;
                long long cur, nxt;
                if (k < 0) continue;
                tau0 = tr - (double)k;
                cur = perm[_ce_pmod_ll(k, n)];
                nxt = perm[_ce_pmod_ll(k + 1, n)];
                sm = _ce_smooth_cur((tau0 < 1.0) ? tau0 : 1.0);
                c = (double)cur + ((double)nxt - (double)cur) * gap * sm;
                envv = _ce_rr2_env(tau0, attack, dscale, soft);
                denom = span_run * 0.6;
                if (denom < 1.0) denom = 1.0;
                dd = (double)i - c;
                wv = envv * exp(-pow(dd, 2.0) / (2.0 * pow(denom, 2.0)));
                if (wv > acc) acc = wv;
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        free(perm);
        return;
    }
    case 18: {  /* siteripple: 随机落点双向扩散环: _rr2_siteripple */
        double pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.6);
        double sw[8], tw[8];
        long long k_lo = k0 - 7;
        if (k_lo < 0) k_lo = 0;
        _ce_rr2_chain_win(sw, tw, seed, n, pd, k_lo, k0);
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            int back;
            for (back = 0; back < 8; back++) {
                long long k = k0 - back;
                double tau, c, dabs, d, r, th, g, wv;
                long long idx;
                if (k < 0) continue;
                idx = k - k_lo;
                tau = tr - (double)k;
                c = sw[idx];
                dabs = fabs((double)i - c);
                d = (dabs < (double)n - dabs) ? dabs : (double)n - dabs;
                r = tau * (1.2 + 0.5 * span_run);
                th = 0.6 + 0.3 * span_run;
                g = fabs(d - r);
                if (g < th) {
                    wv = _ce_rr2_env(tau * 0.45, attack, dscale, soft)
                         * (1.0 - g / th);
                    if (wv > acc) acc = wv;
                }
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    case 20: {  /* burst: 2-4 连发簇 + 静默窗（density 槽）: _rr2_burst */
        double gap_step = 0.42 - 0.30 * density;
        double cdiv = tr / 2.4;
        long long cyc = (long long)cdiv;
        double fr, pd, site0, d1, span_b, sw[8], tw[8];
        int hits, j2;
        if (cyc < 0) cyc = 0;
        fr = tr - (double)cyc * 2.4;
        hits = 2 + (int)(_ce_vnoise(cyc * 13 + 1, seed + 5) * 2.999);
        pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.6);
        _ce_rr2_chain_win(sw, tw, seed, n, pd, cyc, cyc);
        site0 = (double)_ce_pmod_ll((long long)sw[0], n);
        d1 = _ce_rr2_pack_dir(dmode, cyc, tw[0], seed);
        span_b = span_run;
        if (span_b > (double)n) span_b = (double)n;
        if (span_b < 1.0) span_b = 1.0;
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            for (j2 = 0; j2 < hits; j2++) {
                double off = (double)j2 * gap_step;
                double rel;
                if (fr < off) continue;
                rel = fabs((double)i
                           - _ce_mod_div(site0 + (double)j2 * d1, (double)n));
                if (rel < span_b) {
                    double wv = _ce_rr2_env(
                        fr - off - gap * 0.4 * (rel / span_b),
                        attack, dscale, soft);
                    if (wv > acc) acc = wv;
                }
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    case 22: {  /* converge: 双高斯相向 + 对撞闪光: _rr2_converge */
        double cdiv = tr / 5.0;
        long long cyc = (long long)cdiv;
        double fr, pd, s1, prog, p1, wdt, flash;
        if (cyc < 0) cyc = 0;
        fr = tr / 5.0 - (double)cyc;
        pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.5);
        s1 = 0.04 + (1.0 - pd) * 0.30 * _ce_vnoise(cyc * 7 + 3, seed + 9);
        prog = _ce_smooth_cur((fr / 0.86 < 1.0) ? fr / 0.86 : 1.0);
        p1 = (s1 + (0.5 - s1) * prog) * (double)(n - 1);
        wdt = span_run * 0.45;
        if (wdt < 0.7) wdt = 0.7;
        flash = exp(-pow(fr - 0.92, 2.0) / 0.0016);
        for (i = 0; i < n; i++) {
            double acc, wv;
            double dd1 = (double)i - p1;
            double dd2 = (double)i - ((double)(n - 1) - p1);
            acc = exp(-pow(dd1, 2.0) / (2.0 * pow(wdt, 2.0)));
            wv = exp(-pow(dd2, 2.0) / (2.0 * pow(wdt, 2.0)));
            if (wv > acc) acc = wv;
            if (flash > 0.02) {
                double dd3 = (double)i - (double)(n - 1) * 0.5;
                wv = flash * exp(-pow(dd3, 2.0)
                                 / (2.0 * pow((double)n * 0.12, 2.0)));
                if (wv > acc) acc = wv;
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    case 24: {  /* parit: 奇偶交替双链（seed+577·par）: _rr2_parit */
        double pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.5);
        double sw0[8], tw0[8], sw1[8], tw1[8];
        double span_p = span_run * 0.5;
        long long k_lo = k0 - lookback;
        long long k_lo2, k_hi2;
        if (k_lo < 0) k_lo = 0;
        k_lo2 = k_lo / 2;
        k_hi2 = k0 / 2;
        if (span_p < 1.0) span_p = 1.0;
        _ce_rr2_chain_win(sw0, tw0, seed, n, pd, k_lo2, k_hi2);
        _ce_rr2_chain_win(sw1, tw1, seed + 577, n, pd, k_lo2, k_hi2);
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            int back;
            for (back = 0; back <= lookback; back++) {
                long long k = k0 - back;
                long long site;
                int par;
                double relp, envv;
                if (k < 0) continue;
                par = (int)_ce_pmod_ll(k, 2);
                if ((i % 2) != par) continue;
                site = _ce_pmod_ll((long long)
                    ((par == 0) ? sw0[k / 2 - k_lo2] : sw1[k / 2 - k_lo2]), n);
                if ((site % 2) != par) site = _ce_pmod_ll(site + 1, n);
                relp = fabs((double)i - (double)site) * 0.5;
                if (relp < span_p) {
                    envv = _ce_rr2_env(
                        tr - (double)k - gap * 0.5 * (relp / span_p),
                        attack, dscale, soft);
                    if (envv > acc) acc = envv;
                }
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    case 25: {  /* denswave: 14 触发慢波调制门控（mod_depth 槽）: _rr2_denswave */
        double depth = mod_depth;
        double pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.6);
        double sw[8], tw[8];
        long long k_lo = k0 - lookback;
        if (k_lo < 0) k_lo = 0;
        _ce_rr2_chain_win(sw, tw, seed, n, pd, k_lo, k0);
        for (i = 0; i < n; i++) {
            double acc = 0.0;
            int back;
            for (back = 0; back <= lookback; back++) {
                long long k = k0 - back;
                double tide, start, d1, rel, envv;
                long long idx;
                if (k < 0) continue;
                idx = k - k_lo;
                tide = 0.5 + 0.5 * depth
                       * cos((double)k * 2.0 * M_PI / 14.0);
                if (_ce_vnoise(k * 23 + 9, seed + 31) > tide) continue;
                start = (double)_ce_pmod_ll((long long)sw[idx], n);
                d1 = _ce_rr2_pack_dir(dmode, k, tw[idx], seed);
                rel = _ce_mod_div(((double)i - start) * d1, (double)n);
                if (rel < span_run) {
                    envv = _ce_rr2_env(tr - (double)k - gap * (rel / span_run),
                                       attack, dscale, soft);
                    if (envv > acc) acc = envv;
                }
            }
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
        }
        return;
    }
    default: {  /* case 30: wavewalk: 正弦峰位沿链 smooth 滑动: _rr2_wavewalk */
        double pd = _ce_clamp01_cur(has_prox ? prox_raw : 0.8);
        double sw[8], tw[8];
        long long kw = (k0 < CAP - 2) ? k0 : (CAP - 2);
        double tau = tr - (double)kw;
        double s0, s1, delta, c, lam;
        _ce_rr2_chain_win(sw, tw, seed, n, pd, kw, kw + 1);
        s0 = sw[0];
        s1 = sw[1];
        delta = _ce_mod_div(s1 - s0 + (double)n * 0.5, (double)n)
                - (double)n * 0.5;
        c = _ce_mod_div(s0 + delta * _ce_smooth_cur((tau < 1.0) ? tau : 1.0),
                        (double)n);
        lam = 2.0 + 2.0 * span_run;
        for (i = 0; i < n; i++) {
            double dabs = fabs((double)i - c);
            double d = (dabs < (double)n - dabs) ? dabs : (double)n - dabs;
            double cw = cos(M_PI * d / lam);
            double wv;
            if (cw < 0.0) cw = 0.0;             /* max(0.0, cos(..)) 先截再仿射: 底板 0.25 */
            wv = 0.25 + 0.75 * cw;
            out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(wv) * (hi - lo));
        }
        return;
    }
    }
}

/* ── 5.1 伪随机增强族 ce_cur_randrun（_randrun_kernel :2949）─────────────
 * 参数槽（wrapper 按 Python fn 同式提取）:
 *   rate      = speed * tick（speed 已 max(0.01,·), tick 来自变体表）
 *   proximity/phase_gap/decay 已 _clamp01
 *   run_size  = int(params.run_size / 变体 rs)（原始 int, C 内 max(1,min(n,·))）
 *   dmode     0=跟随 1=交替 2=随机（其余落 2 = Python 哈希分支）
 *   bias      0=无 1=edge 2=center
 * E1 扩容（kind ≥ 15 走 _ce_randrun_rr2 分发, 每 kind 独立权重函数）:
 *   kind      0-14 = v1 参数槽驱动; 15-30 = dualzone..wavewalk（_RR2_FNS 序）
 *   density/mod_depth = params 原槽（wrapper 已 _clamp01; Python st.get 语义）
 *   prox_raw/has_prox = params.proximity 原值 + 在场标志（RR2 各 fn 的
 *                       st.get("proximity", <kind 默认>) 逐位复刻）
 */
CE_API void ce_cur_randrun(
    int kind,
    double t, double lo, double hi,
    double rate, double proximity, double phase_gap, double decay,
    int run_size, int seed,
    int dmode, int bias,
    int soft, int flick, int slide, int mirror, int punch, int heads,
    double density, double mod_depth, double prox_raw, int has_prox,
    int n, int32_t* out)
{
    const long long CAP = 4096;             /* Python _RANDRUN_CAP */
    double span_run, span_step, dscale, attack, tr;
    long long k0, k_lo, a, jend, j;
    int lookback, npkg = 0, i, q, wcap;
    double site_cap[8], step_cap[8];
    double pk_start[20], pk_dir[20], pk_tau0[20], pk_tgt[20];
    long long pk_k[20];

    if (n <= 0) return;
    if (kind >= 15) {            /* E1 扩容 RR2: 每 kind 独立权重函数 */
        _ce_randrun_rr2(kind, t, lo, hi, rate, phase_gap, decay,
                        run_size, seed, dmode, soft, punch,
                        density, mod_depth, prox_raw, has_prox, n, out);
        return;
    }
    span_run = (double)((run_size < n) ? ((run_size < 1) ? 1 : run_size) : n);
    span_step = (1.0 - proximity) * (double)n;
    if (span_step < 1.0) span_step = 1.0;
    dscale = decay * 0.6;
    if (dscale < 0.06) dscale = 0.06;
    attack = soft ? 0.3 : (punch ? 0.05 : 0.15);
    lookback = (decay > 0.45) ? 3 : 2;
    tr = t * rate;
    k0 = (long long)tr;                     /* Python int() 截断 */
    /* 触发链窗口捕获: 需要链值 idx ∈ [k_lo, k0+1]（k0+1 供 slide 落点）,
     * idx = k % CAP 环回 → 单趟重算 j=0..jend, 命中窗口槽位即捕获。 */
    k_lo = k0 - (lookback - 1);
    if (k_lo < 0) k_lo = 0;
    a = k_lo % CAP;
    wcap = (int)(((k0 + 1) - k_lo) % CAP) + 1;
    {
        double site = _ce_vnoise(101, seed) * (double)n;   /* 链 site_0 */
        long long off = (0 - a) % CAP;
        if (off < 0) off += CAP;
        if ((int)off < wcap) { site_cap[off] = site; step_cap[off] = 0.0; }
        jend = (k0 + 1 < CAP) ? (k0 + 1) : (CAP - 1);
        for (j = 1; j <= jend; j++) {
            double step = (_ce_vnoise(j * 7 + 3, seed) - 0.5) * 2.0 * span_step;
            off = (j - a) % CAP;
            if (off < 0) off += CAP;
            site = _ce_mod_div(site + step, (double)n);
            if ((int)off < wcap) { site_cap[off] = site; step_cap[off] = step; }
        }
    }
    /* 跑动包构建（_rr_pkgs :3020; 顺序无关 — 权值仅取 max） */
    for (q = 0; q < lookback; q++) {
        long long k = k0 - q;
        long long off;
        double site_raw, step_raw, tau0, d1, site, start;
        if (k < 0) continue;
        off = (k - a) % CAP;
        if (off < 0) off += CAP;
        site_raw = site_cap[off];
        step_raw = step_cap[off];
        tau0 = tr - (double)k;
        if (tau0 > 1.0 + phase_gap + dscale * 4.0) continue;  /* 包已熄灭 */
        if (dmode == 0) d1 = (step_raw >= 0.0) ? 1.0 : -1.0;
        else if (dmode == 1) d1 = ((k % 2) == 0) ? 1.0 : -1.0;
        else d1 = (_ce_vnoise(k * 3 + 1, seed) < 0.5) ? 1.0 : -1.0;
        site = site_raw;
        if (bias == 1 && _ce_vnoise(k * 11 + 5, seed + 31) < 0.6)
            site = (_ce_vnoise(k * 5 + 9, seed + 17) < 0.5)
                   ? 0.0 : (double)(n - 1);
        else if (bias == 2)
            site = (site + (double)n * 0.5) * 0.5
                   + (_ce_vnoise(k, seed + 7) - 0.5) * (double)n * 0.15;
        start = (double)((long long)floor(site) % n);  /* 吸附设备格点 */
        if (npkg > 18) break;                          /* 防御（lookback×4） */
        if (mirror) {                                  /* 双头对跑镜像落点 */
            pk_k[npkg] = k;
            pk_start[npkg] = (double)n - 1.0 - start;
            pk_dir[npkg] = -d1;
            pk_tau0[npkg] = tau0;
            npkg++;
        }
        if (heads > 1) {
            int h;
            for (h = 0; h < heads && npkg < 20; h++) {
                pk_k[npkg] = k;
                pk_start[npkg] = _ce_mod_div(
                    start + (double)((long long)h * (long long)n)
                            / (double)heads, (double)n);
                pk_dir[npkg] = d1;
                pk_tau0[npkg] = tau0 - (double)h / (double)heads;
                npkg++;
            }
        } else {
            pk_k[npkg] = k;
            pk_start[npkg] = start;
            pk_dir[npkg] = d1;
            pk_tau0[npkg] = tau0;
            npkg++;
        }
    }
    if (slide) {  /* 群星坠落: 包尾目标 = 下一站链值吸附格点（_rr_weight） */
        for (q = 0; q < npkg; q++) {
            long long off = ((pk_k[q] + 1) - a) % CAP;
            double ts;
            if (off < 0) off += CAP;
            ts = site_cap[off];
            pk_tgt[q] = (double)((long long)floor(ts) % n);
        }
    }
    for (i = 0; i < n; i++) {
        double acc = 0.0;
        for (q = 0; q < npkg; q++) {
            double start = pk_start[q];
            double rel, tau, w;
            if (slide) {
                double tk = pk_tau0[q];
                tk = (tk < 0.0) ? 0.0 : ((tk > 1.0) ? 1.0 : tk);
                start = start + (pk_tgt[q] - start) * tk;
            }
            rel = _ce_mod_div(((double)i - start) * pk_dir[q], (double)n);
            if (rel >= span_run) continue;
            tau = pk_tau0[q] - phase_gap * (rel / span_run);
            if (tau < 0.0) continue;
            if (tau < attack)
                w = soft ? _ce_smooth_cur(tau / attack) : tau / attack;
            else
                w = exp(-(tau - attack) / dscale);
            if (flick)  /* 包内逐设备噪声调制 */
                w *= 0.55 + 0.45 * _ce_vnoise(pk_k[q] * 13 + (long long)i * 5,
                                              seed + 3);
            if (w > acc) acc = w;
        }
        out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(acc) * (hi - lo));
    }
}

/* ── 5.2 变速载体族 ce_cur_varyspeed（_varyspeed_kernel :3129）───────────
 * look 0=wave 1=chase 2=breathe 3=twinkle 4=pumpwave 5=arch 6=pendulum
 *      7=windmill; E1 扩容: 8=meteor 9=tideflat 10=pulse 11=wavepkt
 *      12=string 13=soliton 14=marquee 15=levelbar 16=buildup 17=pluck
 *      18=swell 19=stair 20=skewtri 21=dpulse 22=fountain;
 * lv = 各 look 变体（chase: 0=comet 1=dot 2=kitt;
 *      breathe: 0=breathe 1=heartbeat 2=tide; pumpwave: 0=wave 1=tide;
 *      pendulum: 0=std 1=soft; windmill: 0=fold 1=twin; 其余: 0）
 * wshape = wave 载体 _waveshape 索引（Python 读 vs_wave 参数, 未知→sine=0）
 * sshape = _vs_shape_S 形状 0=正弦 1=三角 2=阶梯 3=顿挫 4=呼吸 5=心跳
 *          E1: 6=单循环 7=游走（_vs_wander_teff 累积表, 用 seed）
 * center/swing/cycle/uni/vfloor 已按 Python 同式 clamp/max。
 */
static double _ce_vs_hump_int(double y, double c, double w, double h)
{
    double a = c - w;
    double d;
    if (y <= a) return 0.0;
    d = (y < c + w) ? y : (c + w);
    d = d - a;
    return h * 0.5 * (d + (w / M_PI) * sin(M_PI * d / w));
}

static double _ce_vs_shape_s(int shape, double y, double uni)
{
    if (shape == 1) {            /* 三角 */
        double pk = 0.5 + 0.35 * uni;
        double base;
        if (y < pk) return y * y / (2.0 * pk) - 0.5 * y;
        base = pk / 2.0
               + ((y - y * y / 2.0) - (pk - pk * pk / 2.0)) / (1.0 - pk);
        return base - 0.5 * y;
    }
    if (shape == 2) {            /* 阶梯 */
        double f = 0.5 - 0.35 * uni;
        return (y < f) ? (1.0 - f) * y / f : 1.0 - y;
    }
    if (shape == 3) {            /* 顿挫 */
        double f = 0.28 - 0.13 * uni;
        return (y < f) ? (1.0 - f) * y / f : 1.0 - y;
    }
    if (shape == 4) {            /* 呼吸 */
        double w1 = 0.40 * (1.0 - 0.4 * uni);
        double w2 = 0.16 * (1.0 - 0.4 * uni);
        return (_ce_vs_hump_int(y, 0.5, w1, 1.0)
                + 0.55 * _ce_vs_hump_int(y, 0.5, w2, 1.0))
               - (w1 + 0.55 * w2) * y;
    }
    if (shape == 5) {            /* 心跳 */
        double w1 = 0.11 * (1.0 - 0.4 * uni);
        double w2 = 0.06 * (1.0 - 0.4 * uni);
        return (_ce_vs_hump_int(y, 0.22, w1, 1.0)
                + 0.5 * _ce_vs_hump_int(y, 0.52, w2, 1.0))
               - (w1 + 0.5 * w2) * y;
    }
    if (shape == 6)              /* E1 单循环: 一次加减速完整过程（S(1)=0） */
        return (1.0 - cos(y * M_PI)) * 0.5 - y;
    {                            /* 正弦（默认/未知 shape 落此） */
        double w = 0.5 - 0.3 * uni;
        return _ce_vs_hump_int(y, 0.5, w, 1.0) - w * y;
    }
}

/* E1 游走变速: _vs_wander_teff :3739 — m(u)=center*(1+swing*(2v-1)) 的
 * 1024 步闭式累积表（_VS_WANDER_CACHE 同式同序: vs 表 + cum 前缀和 +
 * 三/四次 Hermite 余项插值, 逐位对齐重点）。 */
static double _ce_vs_wander_teff(double t, double center, double swing,
                                 double cycle_w, long long seed)
{
    double vs[1025], cum[1025];
    double step = cycle_w * 0.25;
    double x, f, base, within;
    long long idx, per;
    int i, j;
    if (step < 0.2) step = 0.2;              /* Python max(0.2, cycle*0.25) */
    x = t / step;
    idx = (long long)x;                      /* Python int() 截断（x ≥ 0） */
    f = x - (double)idx;
    i = (int)_ce_pmod_ll(idx, 1024);         /* idx % _VS_WANDER_PERIOD */
    per = idx / 1024;                        /* idx // _VS_WANDER_PERIOD */
    for (j = 0; j <= 1024; j++)
        vs[j] = _ce_vnoise(j * 5 + 13, seed);
    cum[0] = 0.0;
    for (j = 0; j < 1024; j++)
        cum[j + 1] = cum[j] + 1.0 + swing * (vs[j] + vs[j + 1] - 1.0);
    base = (double)per * cum[1024] + cum[i];
    within = f * (1.0 + swing * (2.0 * vs[i] - 1.0))
             + swing * 2.0 * (vs[i + 1] - vs[i])
               * (pow(f, 3.0) - 0.5 * pow(f, 4.0));
    return center * step * (base + within);
}

/* _varyspeed_teff :3096 — t_eff = Φ(t) = ∫₀ᵗ m(u)du 闭式解析 */
static double _ce_vs_teff(double t, double center, double swing,
                          double cycle, double uni, double vfloor, int sshape,
                          long long seed)
{
    double x, cyc, s, active, psi, cw;
    if (swing <= 1e-9 && vfloor <= 1e-9)
        return t * center;       /* 恒速快路径（与 Python 同式同序） */
    if (sshape == 7) {           /* E1 游走: vnoise 平滑随机游走累积表 */
        cw = (cycle > 0.5) ? cycle : 0.5;    /* Python max(0.5, cycle) */
        return _ce_vs_wander_teff(t, center, swing, cw, seed);
    }
    x = t / cycle;
    cyc = floor(x);
    s = x - cyc;
    active = 1.0 - vfloor;
    if (s >= active || active <= 1e-6)
        psi = active;            /* 停顿窗: m 触底 0, Φ 保持 */
    else
        psi = s + swing * active * _ce_vs_shape_s(sshape, s / active, uni);
    return center * cycle * (cyc * active + psi);
}

/* 载体 look（_vslook_* :3165-3220; seq_i=i, n 仅占位同 Python 签名） */
static double _ce_vs_look(int look, int lv, int wshape, double teff,
                          double pos, long long i,
                          double freq, double duty, double width,
                          double tail, double spread, double density,
                          long long seed)
{
    switch (look) {
    case 0:  /* wave */
        return _ce_waveshape_cur(wshape, pos * freq - teff, duty);
    case 1: {  /* chase */
        double bh, dist;
        if (lv == 2) {  /* kitt */
            bh = 1.0 - fabs(_ce_mod_div(teff, 2.0) - 1.0);
            return exp(-fabs(pos - bh) / width);
        }
        dist = _ce_mod_div(pos - _ce_mod_div(teff, 1.0), 1.0);
        if (lv == 1) return (dist < width) ? 1.0 : 0.0;      /* dot */
        return (dist < tail) ? exp(-dist / width) : 0.0;     /* comet */
    }
    case 2: {  /* breathe */
        double ph = teff + pos * spread;
        if (lv == 1) {  /* heartbeat */
            double q = _ce_mod_div(ph, 1.0);
            double g1 = (q - 0.1) * (q - 0.1);
            double g2 = (q - 0.3) * (q - 0.3);
            return exp(-g1 / 0.004) + 0.6 * exp(-g2 / 0.004);
        }
        if (lv == 2)    /* tide */
            return 0.5 - 0.5 * cos(ph * 2.0 * M_PI)
                   + 0.15 * sin(ph * 6.0 * M_PI);
        return _ce_smooth_cur(0.5 - 0.5 * cos(ph * 2.0 * M_PI));
    }
    case 3: {  /* twinkle */
        double base = _ce_vnoise(i, seed);
        if (base < density + 0.4)
            return _ce_smooth_cur(0.5 + 0.5 * sin(teff * 2.0 + base * 6.28));
        return 0.1;
    }
    case 4: {  /* pumpwave */
        double ph = _ce_mod_div(teff, 1.0);
        double w = 0.5 - 0.5 * cos((pos * 2.0 - ph) * 2.0 * M_PI);
        if (lv == 1) w += 0.12 * sin(ph * 6.0 * M_PI + pos * 3.0);
        return w;
    }
    case 5: {  /* arch */
        double ring = _ce_mod_div(
            _ce_mod_div(teff, 1.0) - fabs(pos - 0.5) * 2.0, 1.0);
        return exp(-ring / width);
    }
    case 6: {  /* pendulum */
        double w = 0.5 + 0.5 * sin(teff * M_PI + pos * M_PI);
        return (lv == 1) ? _ce_smooth_cur(w) : w;
    }
    case 7: {  /* windmill: lv==1 twin(11), 否则 fold(9) */
        return _ce_waveshape_cur((lv == 1) ? 11 : 9, pos * freq - teff, duty);
    }
    /* ── E1 扩容 look（R173 精编留 9 个, 下架号留空; 参数已由 wrapper 按
     *    各 look 的 st.get 默认语义解析）── */
    case 11: { /* wavepkt: 高斯包 × 载波（spatial_freq） */
        double d = pos - _ce_mod_div(teff, 1.0);
        double env = exp(-(d * d) / 0.016);
        return 0.5 + 0.5 * env * cos(d * freq * 18.85);
    }
    case 12:   /* string: cos(pos)·cos(t) 驻波（spatial_freq） */
        return 0.5 + 0.45 * cos(pos * freq * 6.283) * cos(teff * 6.283);
    case 14: { /* marquee: 等宽亮块循环（width） */
        long long grp = (long long)ce_round_half_even(1.0 / width);
        long long idx;
        if (grp < 2) grp = 2;
        idx = (long long)((double)i + teff * (double)grp);
        return (_ce_pmod_ll(idx, grp) == 0) ? 1.0 : 0.0;
    }
    case 15: { /* levelbar: pos≤lvl 填充条 */
        double lvl = 0.5 - 0.5 * cos(teff * 2.0 * M_PI);
        return (pos <= lvl) ? 1.0 : 0.06;
    }
    case 16: { /* buildup: 4 级台阶短过渡 */
        double h = _ce_mod_div(teff, 1.0);
        double stp = floor(h * 4.0);
        double frac = (h * 4.0 - stp) / 0.35;
        double hq;
        if (frac > 1.0) frac = 1.0;
        hq = (stp + frac) / 4.0;
        return (pos <= hq) ? 1.0 : 0.04;
    }
    case 17: { /* pluck: 0.04 快攻 + exp 慢衰 */
        double q = _ce_mod_div(teff, 1.0);
        return (q < 0.04) ? q / 0.04 : exp(-q / 0.30);
    }
    case 18: { /* swell: 梯形 smooth 包沿 spread 行进 */
        double q = _ce_mod_div(teff - pos * spread, 1.0);
        return _ce_smooth_cur((q / 0.45 < 1.0) ? q / 0.45 : 1.0)
               * _ce_smooth_cur(((1.0 - q) / 0.30 < 1.0)
                                ? (1.0 - q) / 0.30 : 1.0);
    }
    case 19: { /* stair: 5 档量化 */
        double q = _ce_mod_div(teff, 1.0);
        return floor(q * 5.0) / 4.0;
    }
    default: { /* case 22 fountain: _pump_height("runjet") 同式（width） */
        double c = _ce_mod_div(teff, 1.0);   /* ph=teff*1.0; c=ph%1.0 */
        double d = fabs(pos - c);
        double dv = (d < 1.0 - d) ? d : 1.0 - d;   /* _wrapdist */
        double wv = (width < 0.01) ? 0.01 : width;
        return exp(-(dv * dv) / (2.0 * pow(wv, 2.0)));
    }
    }
}

CE_API void ce_cur_varyspeed(
    int look, int lv, int wshape,
    double t, double lo, double hi,
    double center, double swing, double cycle, double uni,
    double vfloor, int sshape,
    double freq, double duty, double width, double tail,
    double spread, double density, int seed,
    int n, int32_t* out)
{
    double denom, teff;
    int i;
    if (n <= 0) return;
    denom = (double)(n > 1 ? n - 1 : 1);
    teff = _ce_vs_teff(t, center, swing, cycle, uni, vfloor, sshape,
                       (long long)seed);
    for (i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double w = _ce_vs_look(look, lv, wshape, teff, pos, (long long)i,
                               freq, duty, width, tail, spread, density,
                               (long long)seed);
        out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(w) * (hi - lo));
    }
}

/* ── 5.3 折返级联族 ce_cur_fcdc（_foldcascade_kernel :3260）──────────────
 * kind 0=fold 1=slide 2=bounce 3=chasef 4=surge 5=layer 6=arch 7=ripple
 *      8=fold3 9=bloomc 10=bloomedge 11=dual 12=pingpong;
 *      E1 扩容: 13=trianchor 14=meetbloom 15=decay 16=wavepkt 17=echofol
 *      18=stairbloom 19=elevator 20=trough 21=parab 22=wallpush
 *      23=interleave 24=ringfold 25=microbloom
 * front 0=exp 1=band 2=sharp 3=cos; handoff 0=硬接 1=淡接 2=花开 3=回卷
 *      4=其它（Python str ≠ 全部已知名 → hh=0.22 但无淡接/花开/回卷分支）
 * speed/pause/width/accel 已按 Python 同式 max/clamp; groups 已 max(1, int)。
 */
static void _ce_fc_origins(int kind, long long k, int oneway,
                           double* o, double* L, int* mode)
{
    if (kind == 9) {             /* bloomc: 中心外扩 → 两端接力交替 */
        double oo = (k == 0) ? 0.5
                             : ((_ce_pmod_ll(k, 2) == 1) ? 1.0 : 0.0);
        *o = oo;
        *L = (oo > 0.5) ? 0.0 : 1.0;
        *mode = 1;
        return;
    }
    if (kind == 10) {            /* bloomedge: 两端交替外扩 */
        double oo = ((_ce_pmod_ll(k, 2) == 0) || oneway) ? 0.0 : 1.0;
        *o = oo;
        *L = 1.0 - oo;
        *mode = 1;
        return;
    }
    if (kind == 11) {            /* dual: 两端向中 ↔ 中向外 交替 */
        if ((_ce_pmod_ll(k, 2) == 0) || oneway) {
            *o = 0.0; *L = 0.5; *mode = 2;
        } else {
            *o = 0.5; *L = 0.5; *mode = 1;
        }
        return;
    }
    if (kind == 12) {            /* pingpong: 恒相对对穿 */
        *o = 0.0; *L = 1.0; *mode = 3;
        return;
    }
    if (kind == 8) {             /* fold3: 1/3 段落跳接, 段内方向交替 */
        long long seg = _ce_pmod_ll(k, 3);
        double lo3 = (double)seg / 3.0;
        double hi3 = (double)(seg + 1) / 3.0;
        if ((k / 3) % 2 == 0 || oneway) { *o = lo3; *L = hi3; }
        else { *o = hi3; *L = lo3; }
        *mode = 0;
        return;
    }
    {                            /* fold/slide/bounce/chasef/surge/layer/arch/ripple */
        double oo = ((_ce_pmod_ll(k, 2) == 0) || oneway) ? 0.0 : 1.0;
        *o = oo;
        *L = 1.0 - oo;
        *mode = 0;
    }
}

/* ── E1 扩容 fc2 共享记录（_FC2_FRONTS/_FC2_DEVICE 分发表复刻）──────────
 * Python 真源: effect_presets_curated.py :3889-4082。fronts 绘制期间对
 * st 的副作用记录（ej/pdir/ring + decay 的 amp/width 覆写）在此 struct
 * 落地; 花开 bc 二次 fronts 调用会覆写这些记录（Python 同序, 逐位对齐）。 */
typedef struct {
    double ej_o, ej_L;           /* echofol st["ej"] = (o, L) */
    double pdir;                 /* wallpush st["pdir"] */
    double ring_a, ring_b;       /* ringfold st["ring"] = (a, b) */
    double amp;                  /* st["amp"]（decay 每波覆写） */
    double width;                /* st["width"]（decay 每波覆写） */
    double w0;                   /* decay 原始宽缓存（st["w0"]） */
    int has_ej, has_ring, has_w0;
} _ce_fc2_st;

/* _fc2_stdf single 模式（:3889 — ret 折回 + 单波前线性推进） */
static double _ce_fc2_single(int ret, double o, double L, double p)
{
    double pp = ret ? (1.0 - fabs(2.0 * p - 1.0)) : p;
    return o + (L - o) * pp;
}

/* _fc2_fold_ol :3904 — 往返折返（起点, 落点） */
static void _ce_fc2_fold_ol(long long k, int oneway, double* o, double* L)
{
    *o = ((_ce_pmod_ll(k, 2) == 0) || oneway) ? 0.0 : 1.0;
    *L = 1.0 - *o;
}

/* _fc_fronts :3222 + _FC2_FRONTS :4060 — 波 k 在进度 p 下的波前
 * （nf ∈ 1..4; E1 kind 13-25（R173 下架 16/18/21 留空）走分发表分支, 其余（含 trough=20, 无
 * fronts 覆盖）走 std 几何）。 */
static int _ce_fc_fronts(int kind, _ce_fc2_st* s2, long long k, int oneway,
                         long long seed, int ret, double p, double* F)
{
    double o, L;
    int mode;
    switch (kind) {
    case 13: {  /* trianchor: 锚循环 [(0,½),(½,1),(1,0)][k%3] */
        long long seg = _ce_pmod_ll(k, 3);
        if (seg == 0) { o = 0.0; L = 0.5; }
        else if (seg == 1) { o = 0.5; L = 1.0; }
        else { o = 1.0; L = 0.0; }
        F[0] = _ce_fc2_single(ret, o, L, p);
        return 1;
    }
    case 14: {  /* meetbloom: 偶波汇中(in) / 奇波双环开花 [½±d, ½±0.45d] */
        if (_ce_pmod_ll(k, 2) == 0) {
            F[0] = p * 0.5;
            F[1] = 1.0 - p * 0.5;
            return 2;
        }
        {
            double d = p * 0.5;
            F[0] = 0.5 - d;
            F[1] = 0.5 + d;
            F[2] = 0.5 - d * 0.45;
            F[3] = 0.5 + d * 0.45;
        }
        return 4;
    }
    case 15: {  /* decay: amp=0.12+0.88·0.78^(k%5), width 同比缩（st 覆写） */
        double e = pow(0.78, (double)_ce_pmod_ll(k, 5));
        double wv;
        s2->amp = 0.12 + 0.88 * e;
        if (!s2->has_w0) {
            s2->w0 = s2->width;          /* Python st.get("w0") or st["width"] */
            s2->has_w0 = 1;
        }
        wv = s2->w0 * (0.35 + 0.65 * e);
        s2->width = (wv < 0.03) ? 0.03 : wv;
        _ce_fc2_fold_ol(k, oneway, &o, &L);
        F[0] = _ce_fc2_single(ret, o, L, p);
        return 1;
    }
    case 17: {  /* echofol fronts: 记锚 */
        _ce_fc2_fold_ol(k, oneway, &o, &L);
        s2->ej_o = o;
        s2->ej_L = L;
        s2->has_ej = 1;
        F[0] = _ce_fc2_single(ret, o, L, p);
        return 1;
    }
    case 19: {  /* elevator: 4 级走 60% 停 40% + 顶层折返 origin 交替 */
        double step = floor(p * 4.0);
        double frac = (p * 4.0 - step) / 0.6;
        double pq;
        if (frac > 1.0) frac = 1.0;
        pq = (step + frac) / 4.0;
        _ce_fc2_fold_ol(k, oneway, &o, &L);
        F[0] = o + (L - o) * pq;
        return 1;
    }
    case 22: {  /* wallpush fronts: 记向 + band 墙头单波前推进 */
        _ce_fc2_fold_ol(k, oneway, &o, &L);
        s2->pdir = (o < 0.5) ? 1.0 : -1.0;
        F[0] = o + (L - o) * p;
        return 1;
    }
    case 23: {  /* interleave: 偶波 ⅓→1 / 奇波 ⅔→0 对穿 */
        if (_ce_pmod_ll(k, 2) == 0)
            F[0] = _ce_fc2_single(ret, 1.0 / 3.0, 1.0, p);
        else
            F[0] = _ce_fc2_single(ret, 2.0 / 3.0, 0.0, p);
        return 1;
    }
    case 24: {  /* ringfold: 折返点 (k·0.25+p·0.5) 沿环 + 双向绕环 */
        double f = _ce_mod_div((double)k * 0.25 + p * 0.5, 1.0);
        double a = _ce_mod_div(f - p * 0.5, 1.0);
        double b = _ce_mod_div(f + p * 0.5, 1.0);
        s2->ring_a = a;
        s2->ring_b = b;
        s2->has_ring = 1;
        F[0] = a;
        F[1] = b;
        return 2;
    }
    case 25: {  /* microbloom: 每波随机落点 0.22 半径小开花 */
        double oo = 0.1 + 0.8 * _ce_vnoise(k * 9 + 2, seed + 3);
        double d = p * 0.22;
        F[0] = (oo - d > 0.0) ? oo - d : 0.0;
        F[1] = (oo + d < 1.0) ? oo + d : 1.0;
        return 2;
    }
    default:    /* v1 13 kind + trough(20, 无 fronts 覆盖 → std 几何) */
        break;
    }
    _ce_fc_origins(kind, k, oneway, &o, &L, &mode);
    if (mode == 0) {             /* single */
        double pp = ret ? (1.0 - fabs(2.0 * p - 1.0)) : p;
        F[0] = o + (L - o) * pp;
        return 1;
    }
    if (mode == 1) {             /* out: 自起点双向外扩 */
        double d = p * ((o > 1.0 - o) ? o : (1.0 - o));
        if (kind == 10 && seed != 0) {   /* bloomedge 双瓣交错 */
            double d2 = d * (0.7 + 0.6 * _ce_vnoise(k * 3, seed + 2));
            F[0] = (o - d > 0.0) ? (o - d) : 0.0;
            F[1] = (o + d2 < 1.0) ? (o + d2) : 1.0;
        } else {
            F[0] = (o - d > 0.0) ? (o - d) : 0.0;
            F[1] = (o + d < 1.0) ? (o + d) : 1.0;
        }
        return 2;
    }
    if (mode == 2) {             /* in: 两端向中 */
        F[0] = p * 0.5;
        F[1] = 1.0 - p * 0.5;
        return 2;
    }
    F[0] = p;                    /* cross: 两端对穿全幅 */
    F[1] = 1.0 - p;
    return 2;
}

/* _FC_FRONT_FNS :3165 — 波前轮廓（front 0=exp 1=band 2=sharp 3=cos） */
static double _ce_fc_front_fn(int front, double d, double wd)
{
    double wd2;
    switch (front) {
    case 1:                      /* band */
        wd2 = (wd * 2.0 < 0.02) ? 0.02 : wd * 2.0;
        { double v = 1.0 - d / wd2;
          return (v > 0.0) ? v : 0.0; }
    case 2:                      /* sharp */
        if (d < wd) return 1.0;
        if (d < wd * 2.5) {
            wd2 = (wd * 0.4 < 0.02) ? 0.02 : wd * 0.4;
            return exp(-(d - wd) / wd2);
        }
        return 0.0;
    case 3:                      /* cos */
        if (d < wd * 2.0) {
            double c;
            wd2 = (wd * 2.0 < 0.02) ? 0.02 : wd * 2.0;
            c = cos(((d / wd2 < 1.0) ? d / wd2 : 1.0) * M_PI * 0.5);
            return (c > 0.0) ? c : 0.0;
        }
        return 0.0;
    default:                     /* exp */
        wd2 = (wd < 0.02) ? 0.02 : wd;
        return exp(-d / wd2);
    }
}

/* _fc_weight :3236 — 各波前轮廓取最大（+ 拱形包络 / 弹球衰减） */
static double _ce_fc_weight(int front, const double* F, int nf, double width,
                            int arch, double pr, double amp, double x)
{
    double w = 0.0;
    int j;
    for (j = 0; j < nf; j++) {
        double v = _ce_fc_front_fn(front, fabs(x - F[j]), width);
        if (v > w) w = v;
    }
    if (arch) w *= sin(((pr < 1.0) ? pr : 1.0) * M_PI);
    if (amp < 1.0) w *= amp;
    return w;
}

/* _fc_device :3243 + _FC2_DEVICE :4068 — 单设备权重（E1 4 个 kind 覆盖
 * 设备绘制 + 波尾交接: 淡接 / 花开; 覆盖 kind 的 handoff 语义由覆盖函数
 * 自决 —— Python _fc_device 对分发表命中 kind 不走 handoff 分支）。 */
static double _ce_fc_device(int kind, _ce_fc2_st* s2, int handoff, int front,
                            int arch, const double* F, int nf,
                            const double* BC, int nbc,
                            double pr, double v, double x)
{
    double width = s2->width;
    switch (kind) {
    case 17: {                   /* echofol: 主峰 + 55% 进度 0.45 幅随影 */
        double F1 = s2->ej_o + (s2->ej_L - s2->ej_o) * pr;
        double F2 = s2->ej_o + (s2->ej_L - s2->ej_o) * (pr * 0.55);
        double w = _ce_fc_front_fn(front, fabs(x - F1), width);
        double w2 = 0.45 * _ce_fc_front_fn(front, fabs(x - F2), width * 0.8);
        return _ce_clamp01_cur((w2 > w) ? w2 : w);
    }
    case 20: {                   /* trough: 反相暗谷 w=0.9−0.9·fold+落点暗花 */
        double w = _ce_fc_weight(front, F, nf, width, 0, pr, s2->amp, x);
        double out = 0.9 - 0.9 * w;
        if (v > 0.0 && nbc > 0) {
            double vv = _ce_smooth_cur(v);
            int j;
            for (j = 0; j < nbc; j++) {
                double dark = _ce_fc_front_fn(0, fabs(x - BC[j]),
                                              width * 0.7) * vv;
                double o2 = 0.9 - 0.9 * dark;
                if (o2 < out) out = o2;
            }
        }
        return _ce_clamp01_cur(out);
    }
    case 22: {                   /* wallpush: band 墙头 + 墙后 0.82 保持 */
        double w = 0.0;
        int j;
        for (j = 0; j < nf; j++) {
            double wv = _ce_fc_front_fn(1, fabs(x - F[j]), width);
            int behind;
            if (wv > w) w = wv;
            behind = (s2->pdir > 0.0) ? (x < F[j]) : (x > F[j]);
            if (behind && 0.82 > w) w = 0.82;
        }
        return _ce_clamp01_cur(w);
    }
    case 24: {                   /* ringfold: `_wrapdist` 环距波前 */
        double w = 0.0;
        double R[2];
        int j;
        R[0] = s2->ring_a;
        R[1] = s2->ring_b;
        for (j = 0; j < 2; j++) {
            double d = fabs(x - R[j]);
            double dv = (d < 1.0 - d) ? d : 1.0 - d;
            double wv = _ce_fc_front_fn(front, dv, width);
            if (wv > w) w = wv;
        }
        return _ce_clamp01_cur(w);
    }
    default:                     /* std 路径（含 handoff 淡接/花开） */
        break;
    }
    {
        double w = _ce_fc_weight(front, F, nf, width, arch, pr, s2->amp, x);
        if (v <= 0.0) return w;
        if (handoff == 1)            /* 淡接 */
            return w * (1.0 - _ce_smooth_cur(v));
        if (handoff == 2) {          /* 花开: 落点双向开花重叠 */
            double vv = _ce_smooth_cur(v);
            double dspan = vv * 0.35;
            double wb = 0.0;
            double wd = width * 0.7;
            int j;
            for (j = 0; j < nbc; j++) {
                double v1 = _ce_fc_front_fn(0, fabs(x - (BC[j] - dspan)), wd);
                if (v1 > wb) wb = v1;
                v1 = _ce_fc_front_fn(0, fabs(x - (BC[j] + dspan)), wd);
                if (v1 > wb) wb = v1;
            }
            {
                double wv = w * (1.0 - 0.6 * vv);
                return (wv > wb) ? wv : wb;
            }
        }
        return w;
    }
}

CE_API void ce_cur_fcdc(
    int kind, int front, int arch, int ret,
    double t, double lo, double hi,
    double speed, double pause, double width, double accel,
    int handoff, int oneway, int seed, int groups,
    int n, int32_t* out)
{
    _ce_fc2_st s2;
    double denom, tw, tp, u, p, hh, v, pr, F[4], BC[4];
    long long k;
    int nf, nbc = 0, i;
    if (n <= 0) return;
    denom = (double)(n > 1 ? n - 1 : 1);
    tw = 1.0 / speed;                        /* 单波时长（不含停顿） */
    tp = tw * (1.0 + pause);
    k = (long long)(t / tp);
    u = (t - (double)k * tp) / tw;           /* 波内进度 [0,1) */
    if (u < 0.0) u = 0.0;                    /* 波界浮点误差 k*tp>t: 负底 pow 得 NaN */
    if (u >= 1.0) {                          /* 停顿窗: 整波静默写 lo */
        for (i = 0; i < n; i++)
            out[i] = (int32_t)(long long)lo;
        return;
    }
    s2.amp = 1.0;
    if (kind == 2)                           /* bounce: 弹球衰减高度 */
        s2.amp = 0.35 + 0.65 * pow(0.72, (double)_ce_pmod_ll(k, 6));
    s2.width = width;
    s2.w0 = 0.0;
    s2.ej_o = s2.ej_L = 0.0;
    s2.pdir = 0.0;
    s2.ring_a = s2.ring_b = 0.0;
    s2.has_ej = s2.has_ring = s2.has_w0 = 0;
    p = pow(u, 1.0 + 2.2 * accel);           /* 加速度: 由慢到快推进 */
    hh = (handoff == 0) ? 0.0 : 0.22;
    v = 0.0;
    if (hh > 0.0 && u > 1.0 - hh)
        v = (u - (1.0 - hh)) / hh;           /* 波尾交接段 */
    pr = (handoff == 3) ? p * (1.0 - _ce_smooth_cur(v)) : p;  /* 回卷 */
    nf = _ce_fc_fronts(kind, &s2, k, oneway, (long long)seed, ret, pr, F);
    if (v > 0.0 && handoff == 2)             /* 花开: 落点(p=1)波前双向开花
                                              * （二次 fronts 调用, pkt/ring
                                              * 等记录被覆写 — Python 同序） */
        nbc = _ce_fc_fronts(kind, &s2, k, oneway, (long long)seed, ret, 1.0,
                            BC);
    for (i = 0; i < n; i++) {
        double pos = (double)i / denom;
        double x = (groups <= 1)
            ? pos
            : (floor(pos * (double)groups) + 0.5) / (double)groups;
        double w = _ce_fc_device(kind, &s2, handoff, front, arch, F, nf,
                                 BC, nbc, pr, v, x);
        out[i] = (int32_t)(long long)(lo + _ce_clamp01_cur(w) * (hi - lo));
    }
}

/* ── K5/K6: 灯跟踪(Light Tracking)C 化内核(lt_c_spec.md §2.2/§2.5, 2026-08-09)
 *
 * 语义基准 = playback_engine.py _apply_clip_light_tracking(:4150-4650, 含
 * 08-06 F5 包内泵→同包灯 + 08-07 跨域泵帧查找/帧0实时回退/泵0写0) +
 * _lt_scope_for_universe(变更E :4079-4148)。主路径/同域泵(F5)/跨域泵三段
 * 与 Python 逐公式同序; 状态槽引擎级共享(键 = (uni,fid,source)/(uni,fid)),
 * 跨 clip 共享, 由 wrapper 按 touched[] 增量回写 dict。
 * 数值纪律: 全程 f64 同序; fmin/fmax; Python round()=lrint(round-half-even);
 * 先值域后截断; 禁 fast-math; 时钟全部来自入参(local_t), 不读全局状态。
 *
 * ── lt_scope 扁平块布局(K4 构建, C 只读; 头部 int32 字段 memcpy 进局部
 *    struct 读取, 对齐安全)─────────────────────────────────────────────
 *   +0    n_fx n_pumps n_cross n_clip_unis threshold color_is_white cr cg cb
 *         max_brightness (int32×10)
 *   +40   attack f64 | +48 release f64
 *   +56   light_mask[512] u8        (_clip_light_tracking_active_mask)
 *   +568  src_active_mask[512] u8   (_light_tracking_active_source_mask)
 *   +1080 clip_unis[16] int32       (clip.clip_universes, ≤16 防御钳)
 *   +1144 off_fx int32 | +1148 off_pump int32 | +1152 off_cross int32
 *   +1156 off_pkg int32 | +1160 n_pkg int32 | +1164 blob_len int32
 *   fx 表(主路径+F5 泵合并, 保 project.fixtures 序 — 同调用内重叠通道的
 *     写赢家由设备序决定; 行 = 91×int32 = 364B):
 *     kind(0=主/1=泵) state_off level_off(泵-1) color_off(泵-1) n_src
 *     nr ng nb nw n_pkg pkg_off  src[16] r[16] g[16] b[16] w[16]
 *   cross 表(保 _lt_pkg_map_by_light_uni 索引序; 行 = 21×int32 = 84B):
 *     state_off pump_uni n_src n_pkg pkg_off src[16]
 *   pkg 平面: int32 通道索引平铺(包灯 r+g+b+w 合并, 全部同值写; 只收录
 *     本域灯 — Python 写循环本就过滤 light.universe == universe)
 * ── 泵帧表 lt_pump_tbl_t ────────────────────────────────────────────────
 *   { int n; const double* times; const u8* const* rows; const int* lens; }
 *   rows/lens 保留原始长度(Python np.asarray(sel.data,u8) 未填充 512 —
 *   size 守卫 `size > max(src_idx)` 语义依赖它, 红线 2)。
 * ── K6: ce_render_clip_frame_lt ─────────────────────────────────────────
 *   fetch→LT(raw→ltscr)→fade→clamp→act(base_active, 注册期已 = base|light
 *   mask)→scores, 结构复刻 ce_render_clip_frame; K2 的 rule-else 角与
 *   resolve/mix 对 LT 任务原样适用(act 已含 light_mask)。
 */
typedef struct {
    int n_fx, n_pumps, n_cross, n_clip_unis, threshold;
    int color_is_white, cr, cg, cb, max_brightness;
    double attack, release;
    const uint8_t* light_mask;
    const uint8_t* src_active_mask;
    const int32_t* clip_unis;
    const int32_t* fx;        /* 合并表(主+泵)行数组, 行 = 91 int32 */
    const int32_t* cross;     /* 跨域表行数组, 行 = 21 int32 */
    const int32_t* pkg;       /* 包灯通道平面 */
} _lt_scope;

static int _lt_scope_parse(const void* blob, _lt_scope* s)
{
    const uint8_t* b = (const uint8_t*)blob;
    int32_t h[10];
    double d[2];
    int32_t off[6];
    if (b == NULL)
        return -1;
    memcpy(h, b, sizeof(h));
    memcpy(d, b + 40, sizeof(d));
    memcpy(off, b + 1144, sizeof(off));
    s->n_fx = h[0]; s->n_pumps = h[1]; s->n_cross = h[2];
    s->n_clip_unis = h[3]; s->threshold = h[4]; s->color_is_white = h[5];
    s->cr = h[6]; s->cg = h[7]; s->cb = h[8];
    s->max_brightness = h[9];
    s->attack = d[0]; s->release = d[1];
    s->light_mask = b + 56;
    s->src_active_mask = b + 568;
    s->clip_unis = (const int32_t*)(b + 1080);
    s->fx = (const int32_t*)(b + off[0]);
    s->cross = (const int32_t*)(b + off[2]);
    s->pkg = (const int32_t*)(b + off[3]);
    return 0;
}

static int _lt_max3(int a, int b, int c)
{
    int m = a > b ? a : b;
    return m > c ? m : c;
}

/* 共享核心: 在 out[512](=raw 拷贝)上应用 LT 改写; 返回 found_source
 * (Python :4647 `return out if found_source else raw` — out==raw 拷贝,
 * 返回两者字节相同; found 仅语义保留)。 */
static int _lt_apply_core(
    const _lt_scope* s,
    const uint8_t* raw, uint8_t* out,
    double local_t,
    const uint8_t* nm_rgb, const int* fx_live,
    const void* const* cross_tbl, const double* cross_t0, const int* cross_live,
    double* state, double* state_t, uint8_t* state_exists,
    uint8_t* color, uint8_t* color_valid,
    uint8_t* touched, uint8_t* touched_color,
    int n_state, int n_color)
{
    int found = 0;
    if (s->n_fx == 0 && s->n_pumps == 0 && s->n_cross == 0)
        return 0;                       /* ★ 前置早退(:4218-4225), 零状态 */
    const uint8_t* smask = s->src_active_mask;
    const int32_t* pkg = s->pkg;
    const int n_row = s->n_fx + s->n_pumps;

    /* ── 主路径 + 同域泵(F5): 合并表保序扫描(:4236-4495)────────────── */
    for (int i = 0; i < n_row; i++) {
        const int32_t* fx = s->fx + (size_t)i * 91;
        int kind = fx[0];
        int state_off = fx[1];
        int level_off = fx[2];
        int color_off = fx[3];
        int n_src = fx[4];
        const int32_t* src = fx + 11;
        const int32_t* r = fx + 27;
        const int32_t* g = fx + 43;
        const int32_t* b = fx + 59;
        const int32_t* w = fx + 75;
        int nr = fx[5], ng = fx[6], nb = fx[7], nw = fx[8];

        if (kind == 0) {
            /* ── 路径 A — 主路径 fixture(:4402-4495)── */
            int raw_r = nr ? raw[r[0]] : 0;
            int raw_g = ng ? raw[g[0]] : 0;
            int raw_b = nb ? raw[b[0]] : 0;
            int raw_w = nw ? raw[w[0]] : 0;
            /* 颜色缓存(:4413-4416) */
            if (raw_r || raw_g || raw_b) {
                color[color_off * 3] = (uint8_t)raw_r;
                color[color_off * 3 + 1] = (uint8_t)raw_g;
                color[color_off * 3 + 2] = (uint8_t)raw_b;
                color_valid[color_off] = 1;
                touched_color[color_off] = 1;
            } else if (raw_w) {
                color[color_off * 3] = (uint8_t)raw_w;
                color[color_off * 3 + 1] = (uint8_t)raw_w;
                color[color_off * 3 + 2] = (uint8_t)raw_w;
                color_valid[color_off] = 1;
                touched_color[color_off] = 1;
            }
            found = 1;                                  /* :4418 */
            int sl = 0;
            for (int k = 0; k < n_src; k++) {
                int v = raw[src[k]];
                if (v > sl) sl = v;
            }
            int active = 0;
            for (int k = 0; k < n_src; k++) {
                if (smask[src[k]]) { active = 1; break; }
            }
            /* 3 分支源状态(:4426-4433) */
            double* st = &state[state_off];
            if (sl > 0) {
                st[0] = (double)sl / 255.0;
                state_t[state_off] = local_t;
                state_exists[state_off] = 1;
                touched[state_off] = 1;
            } else if (active) {
                st[0] = 0.0;
                state_t[state_off] = local_t;
                state_exists[state_off] = 1;
                touched[state_off] = 1;
            } else if (state_exists[state_off]) {
                sl = (int)lrint(st[0] * 255.0);   /* ★ 分支3 不写 t/exists */
            }
            double target = (sl >= s->threshold) ? (double)s->max_brightness / 255.0 : 0.0;
            /* 颜色(:4439-4466) */
            int use_r = s->cr, use_g = s->cg, use_b = s->cb;
            if (!s->color_is_white) {
                color[color_off * 3] = (uint8_t)s->cr;
                color[color_off * 3 + 1] = (uint8_t)s->cg;
                color[color_off * 3 + 2] = (uint8_t)s->cb;
                color_valid[color_off] = 1;
                touched_color[color_off] = 1;
            } else if (!color_valid[color_off] && nm_rgb != NULL) {
                color[color_off * 3] = nm_rgb[i * 3];
                color[color_off * 3 + 1] = nm_rgb[i * 3 + 1];
                color[color_off * 3 + 2] = nm_rgb[i * 3 + 2];
                color_valid[color_off] = 1;
                touched_color[color_off] = 1;
            }
            if (color_valid[color_off]) {
                use_r = color[color_off * 3];
                use_g = color[color_off * 3 + 1];
                use_b = color[color_off * 3 + 2];
            }
            /* 平滑(:4467-4474) */
            double* lv = &state[level_off];
            double cur = state_exists[level_off] ? lv[0] : target;
            double last = state_exists[level_off] ? state_t[level_off] : local_t;
            double dt = fmax(0.001, fmin(0.25, local_t - last));
            double tau = (target > cur) ? s->attack : s->release;
            cur = cur + (target - cur) * fmin(1.0, dt / fmax(0.001, tau));
            lv[0] = cur;
            state_t[level_off] = local_t;
            state_exists[level_off] = 1;
            touched[level_off] = 1;
            /* 写(补光式 :4476-4495 同式): RGB 完整彩色分量 + W 承载颜色
             * 最大分量补光, 四灯珠按比例同时输出(白色 → 全同值全亮);
             * 写序 = rgbw_indices.items() = r,g,b,w */
            int tv_r = use_r;
            int tv_g = use_g;
            int tv_b = use_b;
            int tv_w = nw ? _lt_max3(use_r, use_g, use_b) : 0;
            if (!(nr || ng || nb))
                tv_w = _lt_max3(use_r, use_g, use_b);   /* 仅 W 灯具 :4487-4489 */
            for (int k = 0; k < nr; k++)
                out[r[k]] = (uint8_t)lrint(fmax(0.0, fmin(255.0, (double)tv_r * cur)));
            for (int k = 0; k < ng; k++)
                out[g[k]] = (uint8_t)lrint(fmax(0.0, fmin(255.0, (double)tv_g * cur)));
            for (int k = 0; k < nb; k++)
                out[b[k]] = (uint8_t)lrint(fmax(0.0, fmin(255.0, (double)tv_b * cur)));
            for (int k = 0; k < nw; k++)
                out[w[k]] = (uint8_t)lrint(fmax(0.0, fmin(255.0, (double)tv_w * cur)));
        } else {
            /* ── 路径 B — 同域泵 F5(:4267-4400)── */
            int sl = 0;
            for (int k = 0; k < n_src; k++) {
                int v = raw[src[k]];
                if (v > sl) sl = v;
            }
            /* 帧0实时回退(:4303-4315; wrapper 预算, exclude clip 层) */
            if (sl <= 0 && fx_live != NULL && fx_live[i] > 0)
                sl = fx_live[i];
            int active = 0;
            for (int k = 0; k < n_src; k++) {
                if (smask[src[k]]) { active = 1; break; }
            }
            double* st = &state[state_off];
            if (sl > 0) {
                st[0] = (double)sl / 255.0;
                state_t[state_off] = local_t;
                state_exists[state_off] = 1;
                touched[state_off] = 1;
            } else if (active) {
                st[0] = 0.0;
                state_t[state_off] = local_t;
                state_exists[state_off] = 1;
                touched[state_off] = 1;
            } else if (state_exists[state_off]) {
                sl = (int)lrint(st[0] * 255.0);
            }
            double target = (sl >= s->threshold) ? (double)s->max_brightness / 255.0 : 0.0;
            double cur = state_exists[state_off] ? st[0] : target;
            double last = state_exists[state_off] ? state_t[state_off] : local_t;
            double dt = fmax(0.001, fmin(0.25, local_t - last));
            double tau = (target > cur) ? s->attack : s->release;
            cur = cur + (target - cur) * fmin(1.0, dt / fmax(0.001, tau));
            st[0] = cur; state_t[state_off] = local_t;   /* ★ 无条件写 */
            state_exists[state_off] = 1; touched[state_off] = 1;
            int cw = (int)lrint(cur * 255.0);
            int npk = fx[9];
            if (npk > 0) {
                int po = fx[10];
                for (int k = 0; k < npk; k++)
                    out[pkg[po + k]] = (uint8_t)cw;
                found = 1;                               /* :4397 */
            }
        }
    }

    /* ── 路径 C — 跨域泵段(:4501-4646)── */
    for (int e = 0; e < s->n_cross; e++) {
        const int32_t* cx = s->cross + (size_t)e * 21;
        int state_off = cx[0];
        int pump_uni = cx[1];
        int n_src = cx[2];
        const int32_t* src = cx + 5;
        int max_src = 0;
        for (int k = 0; k < n_src; k++)
            if (src[k] > max_src) max_src = src[k];
        /* cu_set 跳过角(:4569-4570): clip 有域集且泵域∉域集且无帧表 → 跳过 */
        const void* tbl = (cross_tbl != NULL) ? cross_tbl[e] : NULL;
        if (s->n_clip_unis > 0) {
            int in_cu = 0;
            for (int k = 0; k < s->n_clip_unis; k++) {
                if (s->clip_unis[k] == pump_uni) { in_cu = 1; break; }
            }
            if (!in_cu && tbl == NULL)
                continue;
        }
        int sl = 0;
        if (tbl != NULL) {
            const ce_lt_pump_tbl* pt = (const ce_lt_pump_tbl*)tbl;
            /* 帧选择(:4572-4585): 最后一个 timestamp <= pt_local 的帧,
               全 >  → 第 0 帧(非最近邻!) */
            int sel = 0;
            if (pt->n > 0) {
                int lo = 0, hi = pt->n;
                while (lo < hi) {
                    int mid = lo + (hi - lo) / 2;
                    if (pt->times[mid] > cross_t0[e]) hi = mid;
                    else lo = mid + 1;
                }
                sel = (lo > 0) ? lo - 1 : 0;
            }
            /* size 守卫(:4587-4590): 泵帧原始长度未填充 512 */
            if (pt->n > 0 && pt->lens[sel] > max_src) {
                sl = 0;
                for (int k = 0; k < n_src; k++) {
                    int v = pt->rows[sel][src[k]];
                    if (v > sl) sl = v;
                }
            }
        }
        if (sl <= 0 && cross_live != NULL && cross_live[e] > 0)
            sl = cross_live[e];
        double target = (sl >= s->threshold) ? (double)s->max_brightness / 255.0 : 0.0;
        double* st = &state[state_off];
        double cur = state_exists[state_off] ? st[0] : target;
        double last = state_exists[state_off] ? state_t[state_off] : local_t;
        double dt = fmax(0.001, fmin(0.25, local_t - last));
        double tau = (target > cur) ? s->attack : s->release;
        cur = cur + (target - cur) * fmin(1.0, dt / fmax(0.001, tau));
        st[0] = cur; state_t[state_off] = local_t;
        state_exists[state_off] = 1; touched[state_off] = 1;
        int cw = (int)lrint(cur * 255.0);
        int npk = cx[3];
        if (npk > 0) {
            int po = cx[4];
            for (int k = 0; k < npk; k++)
                out[pkg[po + k]] = (uint8_t)cw;
        }
        found = 1;                                       /* :4644 无条件 */
    }
    return found;
}

/* 每 (clip, universe) 一次调用。out 恒 = raw 拷贝 + LT 改写;
 * 返回 1 = 有修改(found_source), 0 = 未修改(调用方回 raw 对象)。 */
CE_API int ce_lt_apply(
    const void* scope,
    const uint8_t* raw,
    uint8_t* out,
    double local_t,
    const uint8_t* nm_rgb,
    const int* fx_live,
    const void* const* cross_tbl,
    const double* cross_t0,
    const int* cross_live,
    double* state, double* state_t, uint8_t* state_exists,
    uint8_t* color, uint8_t* color_valid,
    uint8_t* touched, uint8_t* touched_color,
    int n_state, int n_color)
{
    _lt_scope sc;
    if (scope == NULL || raw == NULL || out == NULL)
        return 0;
    if (_lt_scope_parse(scope, &sc) < 0)
        return 0;
    memcpy(out, raw, 512);               /* out 恒 = raw 拷贝(Python 顶部 :4217 同序) */
    if (sc.n_fx == 0 && sc.n_pumps == 0 && sc.n_cross == 0)
        return 0;                        /* scope 空: 零状态早退, out=raw */
    return _lt_apply_core(&sc, raw, out, local_t, nm_rgb, fx_live,
                          cross_tbl, cross_t0, cross_live,
                          state, state_t, state_exists,
                          color, color_valid, touched, touched_color,
                          n_state, n_color);
}

/* ── K6: LT 任务融合渲染(ce_render_clip_frame + §2.2 LT 核心)────────────
 * fetch→LT(raw→ltscr)→fade→clamp→active(注册期 base_active 已含 light_mask)
 * →scores。返回 idx(<0 = 无帧, 任务丢弃, LT 不执行 — Python :3702 同序)。
 */
static int ce_render_clip_frame_lt(
    const double* frame_times, int n_frames,
    const uint8_t* frame_data, const float* last_change,
    double source_t, double clip_start, double rate, double timeline_now,
    int hold_extension, float gain, const uint8_t* base_active,
    int fade_mode, const float* prev, const float* target,
    const void* scope, double local_t,
    const uint8_t* nm_rgb, const int* fx_live,
    const void* const* cross_tbl, const double* cross_t0, const int* cross_live,
    double* state, double* state_t, uint8_t* state_exists,
    uint8_t* color, uint8_t* color_valid,
    uint8_t* touched, uint8_t* touched_color,
    int n_state, int n_color,
    uint8_t* ltscr,
    uint8_t* out, uint8_t* active_out, double* scores)
{
    int idx = ce_frame_index(frame_times, n_frames, source_t);
    if (idx < 0) {
        for (int c = 0; c < 512; c++) {
            scores[c] = -INFINITY;
            if (base_active[c])
                scores[c] = clip_start;
            active_out[c] = 0;
        }
        return -1;
    }
    const uint8_t* raw = frame_data + (size_t)idx * 512;
    const float* lc = (last_change != NULL) ? last_change + (size_t)idx * 512 : NULL;
    _lt_scope sc;
    _lt_scope_parse(scope, &sc);
    memcpy(ltscr, raw, 512);
    _lt_apply_core(&sc, raw, ltscr, local_t, nm_rgb, fx_live,
                   cross_tbl, cross_t0, cross_live,
                   state, state_t, state_exists,
                   color, color_valid, touched, touched_color,
                   n_state, n_color);
    for (int c = 0; c < 512; c++) {
        float r = (float)ltscr[c];
        float v;
        if (fade_mode == 1 && prev != NULL) {
            v = prev[c] + gain * (r - prev[c]);
        } else if (fade_mode == 2 && target != NULL) {
            v = target[c] + gain * (r - target[c]);
        } else {
            v = r * gain;
        }
        if (v < 0.0f)
            v = 0.0f;
        else if (v > 255.0f)
            v = 255.0f;
        out[c] = (uint8_t)v;
        uint8_t a = base_active[c];
        active_out[c] = a;
        double s = (lc != NULL)
            ? clip_start + (double)lc[c] / rate
            : clip_start;
        if (hold_extension && s > timeline_now)
            s = timeline_now;
        scores[c] = a ? s : -INFINITY;
    }
    return idx;
}

/* ── Route Runner CE 覆盖内核 (ce_route_coverage) ─────────────────────
 * 文本合入本编译单元 (build.py 只编译 ce_engine.c + ce_pool.c, 不单独
 * 编译 ce_route.c)。契约与位级等价纪律见 render_lab/csrc/ce_route.c 顶部。
 * 导出: ce_route_ctx_create/destroy, ce_route_paths_set,
 *       ce_route_devices_project, ce_route_frame。
 */

/* ═══ CE_LNK_BEGIN ═══ */

/* 关联器（Track Linker）计算内核 —— ce_linker_preset_eval / ce_linker_rule。
 * 文本合入本编译单元（build.py 只编译 ce_engine.c + ce_pool.c, 不单独编译
 * .inc）→ 全仓库只有本处 #include, 符号恒定义一次。
 * ★ 2026-09-19: 本内核此前在 ce_engine.c 内**内联抄了一份**, 与 .inc 双真源
 *   → 「改 .inc 却编不进去」实测踩到一次（white 槽位缺陷修完仍复现）。
 *   现改为唯一真源 = .inc（与 CE_MTX / CE_EFFW 同款）。
 * 契约与位级等价纪律见 render_lab/csrc/_ce_linker_kernel.c.inc 顶部。 */
#include "_ce_linker_kernel.c.inc"
/* ═══ CE_LNK_END ═══ */

/* ═══ CE_MTX_BEGIN ═══ */
/* 矩阵像素引擎（matrix slideshow pixel engine）计算内核 ——
 * ce_mtx_pattern / ce_mtx_particle。文本合入本编译单元（build.py 只编译
 * ce_engine.c + ce_pool.c, 不单独编译 .inc）。契约与位级等价纪律见
 * render_lab/csrc/_ce_mtx_kernel.c.inc 顶部（含 math.hypot 逐位复刻的
 * 实证说明 —— 那是本内核唯一不能直接用 ucrtbase 的数学）。 */
#include "_ce_mtx_kernel.c.inc"
/* ═══ CE_MTX_END ═══ */

/* ═══ CE_EFFW_BEGIN ═══ */
/* effect `_tick_play` 脚手架写回内核（ce_eff_write_direct / ce_eff_write_rgbw）。
 * 与 CE_MTX 同款: 文本合入本编译单元（build.py 只编译 ce_engine.c +
 * ce_pool.c, 不单独编译 .inc）→ 全仓库只有本处 #include, 符号恒定义一次。
 * 契约与位级等价纪律见 render_lab/csrc/_ce_eff_kernel.c.inc 顶部。 */
#include "_ce_eff_kernel.c.inc"
/* ═══ CE_EFFW_END ═══ */

#include "ce_route.c"
#include "ce_prepared_frame.inc"
#include "ce_output_domain_ownership.inc"
#include "ce_output_domain_inferred.inc"
#include "ce_output_domain_compact.inc"
#include "ce_output_domain_pump.inc"
#include "ce_output_domain_color.inc"
#include "ce_output_domain_xy.inc"
#include "ce_output_mask_count.inc"
#include "ce_audio_common.inc"
#include "ce_audio_envelope.inc"
#include "ce_audio_chain.inc"
#include "ce_audio_scope.inc"
#include "ce_audio_prepare_types.inc"
#include "ce_audio_prepare_decimal.inc"
#include "ce_audio_prepare_scalar.inc"
#include "ce_audio_prepare_rows.inc"
#include "ce_audio_prepare_sort.inc"
#include "ce_audio_prepare_api.inc"
#include "ce_source_motion_curve.inc"
#include "ce_source_motion_direction.inc"
#include "ce_source_motion_positions.inc"
#include "ce_source_motion_raw.inc"
#include "ce_source_motion_clock.inc"
#include "ce_render_timing.inc"
#include "ce_source_motion_geometry_base.inc"
#include "ce_source_motion_geometry_fit.inc"
#include "ce_source_motion_geometry_rank.inc"
#include "ce_source_motion_geometry_segments.inc"
#include "ce_output_filter.inc"
#include "ce_output_filter_plain.inc"
#include "ce_legacy_direction_prepare.inc"
#include "ce_curve_lut.inc"
#include "ce_trajectory_types.inc"
#include "ce_trajectory_integer_bytes.inc"
#include "ce_trajectory_integer_arithmetic.inc"
#include "ce_trajectory_integer_float.inc"
#include "ce_trajectory_time_sample.inc"
#include "ce_trajectory_applier.inc"
#include "ce_trajectory_merge.inc"
#include "ce_trajectory_api.inc"
#include "ce_curve_scalar_types.inc"
#include "ce_curve_scalar_values.inc"
#include "ce_programmer_library_sort_types.inc"
#include "ce_programmer_library_sort_core_types.inc"
#include "ce_programmer_library_sort_compare.inc"
#include "ce_programmer_library_sort_runs.inc"
#include "ce_programmer_library_sort_gallop.inc"
#include "ce_programmer_library_sort_memory.inc"
#include "ce_programmer_library_sort_merge_lo.inc"
#include "ce_programmer_library_sort_merge_hi.inc"
#include "ce_programmer_library_sort_schedule.inc"
#include "ce_programmer_library_sort_driver.inc"
#include "ce_curve_scalar_model.inc"
#include "ce_curve_scalar_sort.inc"
#include "ce_curve_scalar_trajectory.inc"
#include "ce_curve_scalar_api.inc"
#include "ce_programmer_row_types.inc"
#include "ce_programmer_row_integer.inc"
#include "ce_programmer_row_mix.inc"
#include "ce_programmer_row_physical.inc"
#include "ce_programmer_row_range.inc"
#include "ce_programmer_row_divide.inc"
#include "ce_programmer_row_mean.inc"
#include "ce_programmer_row_sum.inc"
#include "ce_programmer_row_curve.inc"
#include "ce_programmer_row_fill.inc"
#include "ce_programmer_row_clock.inc"
#include "ce_programmer_row_endpoints.inc"
#include "ce_programmer_row_api.inc"
#include "ce_programmer_library_types.inc"
#include "ce_programmer_library_math.inc"
#include "ce_programmer_library_typed.inc"
#include "ce_programmer_library_clock.inc"
#include "ce_programmer_library_groups.inc"
#include "ce_programmer_library_windows.inc"
#include "ce_programmer_library_direction.inc"
#include "ce_programmer_library_shape.inc"
#include "ce_programmer_library_api.inc"
#include "ce_programmer_library_sort_sum.inc"
#include "ce_programmer_library_sort_keys.inc"
#include "ce_programmer_library_sort_api.inc"

#include "ce_cold_clip_change_times.inc"
#include "ce_filter_prepare.inc"
#include "ce_fade_target.inc"

#include "ce_lt_prepare_types.inc"
#include "ce_lt_prepare_api.inc"

/* SNTC input state shares this CE DLL; no separate numerical process. */
#include "ce_sntc_v1_types.inc"
#include "ce_sntc_v1_api.inc"
#include "ce_sntc_consumer_v1.inc"
#include "ce_sntc_local_clock_v1.inc"
#include "ce_sntc_media_v1_types.inc"
#include "ce_sntc_media_v1_api.inc"
#include "ce_sntc_reference_policy_v1.inc"
