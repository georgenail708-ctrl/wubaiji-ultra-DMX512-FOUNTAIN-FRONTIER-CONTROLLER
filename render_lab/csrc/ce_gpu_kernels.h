/*
 * Qascade CE — GPU 内核算术核心（CUDA __device__ / CPU 桩双编译共用）
 *
 * 位级等价锚点: 本头文件是 ce_engine.c 的 K0/K1 内核逐语句复刻,
 *   同一份源码同时编译进:
 *     ce_gpu_cuda.cu   — CUDA 后端（__device__ 内联, 设备端逐字执行）
 *     ce_gpu_cpu_stub.c— CPU 桩（MSVC 内联, 本机语义验证: 与 ce_engine.dll
 *                         位级对拍, 探针 bench/ce_probe_gpu_equiv.py）
 *   因此「CPU 桩 == ce_engine 位级一致」⟹ 「CUDA 内核算术 == ce_engine
 *   位级一致」只差设备端编译选项与超越函数实现:
 *     - 四则运算/取模/fabs/rint 均为 IEEE 正确舍入 → 设备端逐位一致
 *     - sin/cos/exp（wave 0/5/6/7 号）设备 libdevice 与主机 ucrtbase
 *       最后 1 ulp 不保证一致（CE_GPU_CAP_EXACT_WAVE 恒 0 的原因）
 *
 * 编译纪律（破坏即不等价）:
 *   CUDA  : -fmad=false（禁 fma 收缩: (a*b)+c 模式舍入不同）
 *   CPU   : /fp:precise（build.py 既有约定, 禁收缩）
 *   OpenCL: #pragma OPENCL FP_CONTRACT OFF + 双精度扩展（见 ce_gpu_opencl.c）
 *
 * 语义纪律（与 ce_engine.c :55-119 / :1184-1219 逐行对齐）:
 *   1. 每通道内 nlay 累加严格同序（f32 串行）, 通道间独立
 *   2. 非 active 层 d=0.0 也参与 sum_delta 与 primary 竞争（严格 > 比较）
 *   3. primary 取 |d| 最大层（NaN 永不竞争成功）
 *   4. clamp[0,255] 之后 isnan→0（用 x!=x 判定, 与 isfinite 补集等价）
 *   5. 波形全 f64; mod1 = fmod(x,1.0) 负数校正（CPython % 语义）;
 *      wave 4 号 splitmix64（rint = 银行家舍入, 同 Python round）
 */
#ifndef CE_GPU_KERNELS_H
#define CE_GPU_KERNELS_H

#include <math.h>
#include <stdint.h>

#ifdef __CUDACC__
#define CE_GPU_DEV __device__ static inline
#else
#define CE_GPU_DEV static inline
#endif

/* 与 ce_engine.c 同字面量（MSVC/CUDA 均解析为同一 double） */
#define CE_GPU_M_PI 3.14159265358979323846
#define CE_GPU_M_E 2.7182818284590452354

/* Python t % 1.0 语义（负数返回非负）— 同 ce_engine.c _ce_mod1 */
CE_GPU_DEV double ce_gpu_mod1(double x)
{
    double m = fmod(x, 1.0);
    if (m < 0.0)
        m += 1.0;
    return m;
}

/* 8 波形求值 — 同 ce_engine.c _ce_wave :1184 */
CE_GPU_DEV double ce_gpu_wave(double x, int idx)
{
    switch (idx) {
    case 0: return (sin(x * 2.0 * CE_GPU_M_PI) + 1.0) / 2.0;
    case 1: return (ce_gpu_mod1(x) < 0.5) ? 1.0 : 0.0;
    case 2: return ce_gpu_mod1(x);
    case 3: return 1.0 - fabs(2.0 * ce_gpu_mod1(x) - 1.0);
    case 4: {
        /* splitmix64 逐位复刻（rint = 银行家舍入, 同 Python round）。
         * 负数转型: 经 (long long) 中转——MSVC 直接转 u64 与经 s64 转
         * 逐位相同（探针 cast_probe.c 实测: 两补码回绕）; GPU 直转 u64
         * 未定义行为分歧（OpenCL 780M 实测 fn4 负 x 17.3% 差）,
         * 中转后三方一致。 */
        unsigned long long k = (unsigned long long)(long long)rint(
            x * 100000.0);
        k = (k ^ (k >> 30)) * 0xBF58476D1CE4E5B9ULL;
        k = (k ^ (k >> 27)) * 0x94D049BB133111EBULL;
        k = (k ^ (k >> 31));
        return (double)(k & 0xFFFFFFFFULL) / 4294967296.0;
    }
    case 5: return (cos(x * 2.0 * CE_GPU_M_PI) + 1.0) / 2.0;
    case 6: return fabs(sin(x * CE_GPU_M_PI));
    case 7: return (exp(ce_gpu_mod1(x)) - 1.0) / (CE_GPU_M_E - 1.0);
    default: return (sin(x * 2.0 * CE_GPU_M_PI) + 1.0) / 2.0;
    }
}

/* 单通道帧合成 — 同 ce_engine.c ce_mix_clips :75（每通道内 nlay 串行同序）
 *
 * 参数:
 *   vals/acts/anchors 为该 universe 首层指针; stride = 层跨度（打包布局 lmax）
 *   nlay 实际层数; c 通道号; mod_gain f32 标量
 *   out_c/act_any_c 指向该通道输出元素 */
CE_GPU_DEV void ce_gpu_mix_channel(const float* vals, const uint8_t* acts,
                                   const float* anchors, int nlay, int stride,
                                   int c, float mod_gain,
                                   uint8_t* out_c, uint8_t* act_any_c)
{
    int pidx = -1, any_a = 0;
    float best_abs = -1.0f, sum_delta = 0.0f, primary_delta, res;
    float sum_active = 0.0f, cnt_active = 0.0f;
    for (int l = 0; l < nlay; l++) {
        const float v = vals[(size_t)l * stride + c];
        float d;
        if (acts[(size_t)l * stride + c]) {
            any_a = 1;
            d = v - anchors[(size_t)l * stride + c];
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
    *act_any_c = (uint8_t)any_a;
    if (pidx >= 0 && acts[(size_t)pidx * stride + c]) {
        primary_delta = vals[(size_t)pidx * stride + c]
                      - anchors[(size_t)pidx * stride + c];
        res = vals[(size_t)pidx * stride + c]
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
    if (res != res)
        res = 0.0f;
    *out_c = (uint8_t)res;
}

/* 单 (task, device) 波形求值 — 同 ce_engine.c ce_wave_effect :1206 逐 i 语义 */
CE_GPU_DEV void ce_gpu_wave_item(double t, double speed, double phase_step,
                                 double lo, double hi, int wave_fn_idx,
                                 int i, uint8_t* out)
{
    double phase = (double)i * phase_step;
    double v = ce_gpu_wave(t * speed + phase, wave_fn_idx);
    double val = lo + v * (hi - lo);
    int iv = (int)val;
    if (iv < 0)
        iv = 0;
    else if (iv > 255)
        iv = 255;
    *out = (uint8_t)iv;
}

#endif /* CE_GPU_KERNELS_H */
