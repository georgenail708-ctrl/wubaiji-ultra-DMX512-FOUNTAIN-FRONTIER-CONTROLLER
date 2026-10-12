/*
 * Qascade CE — GPU 统一后端 API（CUDA / OpenCL 双实现, CPU 桩语义验证）
 *
 * 契约:
 *   init/build/run/destroy 生命周期; run 分两类:
 *     ce_gpu_mix_batch  — K0 批量帧合成（位级等价 ce_mix_clips, 保证）
 *     ce_gpu_wave_batch — K1 批量波形求值（对齐 ce_wave_effect; 超越函数
 *                         sin/cos/exp 与主机 libm 最后 1 ulp 不保证——见
 *                         CE_GPU_CAP_EXACT_WAVE, 由调用方按位级纪律决策）
 *
 * 位级等价纪律（与 ce_engine.c 同源）:
 *   1. 每 work-item 单通道, 通道内 nlay 累加严格同序（f32 串行）
 *   2. 非 active 层 d=0.0 也参与 sum_delta 与 primary 竞争（严格 > 比较）
 *   3. primary 取 |d| 最大层（NaN 永不竞争成功）
 *   4. clamp[0,255] 之后 isnan→0, 最后截断 cast
 *   5. 禁 FMA 收缩（CUDA: -fmad=false / #pragma nv_fp_contract(off);
 *      OpenCL: #pragma OPENCL FP_CONTRACT OFF）——否则 a*b+c 模式被融合,
 *      舍入不同, 位级等价破坏
 *
 * 数据布局:
 *   mix:  vals/acts/anchors 打包 [U][lmax][512] row-major, nlays[u] ≤ lmax
 *         为每域实际层数（C 参考 ce_mix_clips 只读前 nlays[u] 层）
 *   wave: 每 task 一组标量参数 + n; offsets[ntask+1] = 前缀 n 和;
 *         out 总长 = offsets[ntask]; work-item g → (task, i) 二分查表
 *
 * 线程安全: 单 dev 句柄不支持并发 run（DMX tick 与 UI 线程须串行化;
 *   CUDA 内部单流）。多线程使用需每线程句柄或外部锁。
 *
 * 运行时设备选择: 各后端 DLL 导出同组符号, 调用方按 LoadLibrary 顺序探测
 *   ce_gpu_cuda.dll → ce_gpu_opencl.dll → 均失败回退 CPU DLL（ce_engine）。
 *   ce_gpu_probe() 在各自 DLL 内判定本机设备（CUDA: cudaGetDeviceCount;
 *   OpenCL: clGetPlatformIDs + vendor）。
 */
#ifndef CE_GPU_API_H
#define CE_GPU_API_H

#include <stdint.h>

#ifdef _WIN32
#define CE_GPU_API __declspec(dllexport)
#else
#define CE_GPU_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* 后端类型 */
typedef enum ce_gpu_backend_e {
    CE_GPU_BACKEND_NONE = 0,   /* 未初始化 / 本机不可用 */
    CE_GPU_BACKEND_CUDA = 1,   /* NVIDIA → CUDA */
    CE_GPU_BACKEND_OPENCL = 2, /* AMD/其他 → OpenCL */
} ce_gpu_backend_e;

/* 能力标志（位级等价保证等级, 调用方按此决策哪些效果走 GPU） */
#define CE_GPU_CAP_EXACT_MIX  (1u << 0)  /* 帧合成与 ce_mix_clips 位级一致(纯 IEEE, 保证) */
#define CE_GPU_CAP_EXACT_WAVE (1u << 1)  /* 波形与 ce_wave_effect 位级一致(GPU 恒 0: 超越函数) */

typedef struct ce_gpu_dev ce_gpu_dev;  /* 不透明句柄（各后端自有定义） */

/* ── 生命周期 ─────────────────────────────────────────────────────────── */

/* 探测本机可用后端（在本 DLL 内; 返回 NONE 表示该 DLL 的后端不可用） */
CE_GPU_API ce_gpu_backend_e ce_gpu_probe(void);

/* 初始化。backend 不匹配本 DLL / 设备不可用 → NULL。
 * device_hint: NULL 或 "0"/"1"...（设备索引, 0 起）。 */
CE_GPU_API ce_gpu_dev* ce_gpu_init(ce_gpu_backend_e backend,
                                   const char* device_hint);

/* 释放句柄（含设备缓冲/流/上下文）。NULL 安全。 */
CE_GPU_API void ce_gpu_destroy(ce_gpu_dev* dev);

/* 信息: 设备名（"RTX 4070" 之类, 空则后端名）; 能力位 */
CE_GPU_API const char* ce_gpu_name(ce_gpu_dev* dev);
CE_GPU_API uint32_t ce_gpu_caps(ce_gpu_dev* dev);

/* 构建内核。幂等; CUDA 内核随 DLL 编译为 fatbin（返回 1）;
 * OpenCL 在此真正编译 kernel source（失败 → 0 + ce_gpu_last_error）。 */
CE_GPU_API int ce_gpu_build(ce_gpu_dev* dev);

/* ── run: K0 批量帧合成（位级等价 ce_mix_clips, 逐通道独立）──────────
 * vals    f32 [U][lmax][512]   各层已求值数据
 * acts    u8  [U][lmax][512]   0/1 层激活掩码（非零=激活, 同 C 标量版）
 * anchors f32 [U][lmax][512]   各层锚点
 * nlays   int [U]              每域实际层数（≤ lmax）
 * lmax    int                  打包布局层跨度（≥ max(nlays)）
 * mod_gain f32                 调制增益（标量）
 * out     u8  [U][512]         混合结果
 * act_any u8  [U][512]         任意层激活位
 * 返回 0 成功 / -1 失败（ce_gpu_last_error）。n_univ<=0 → 0（无操作）。 */
CE_GPU_API int ce_gpu_mix_batch(ce_gpu_dev* dev,
                                const float* vals, const uint8_t* acts,
                                const float* anchors, const int* nlays,
                                int n_univ, int lmax, float mod_gain,
                                uint8_t* out, uint8_t* act_any);

/* ── run: K1 批量波形求值（对齐 ce_wave_effect, 每 work-item = 单 (task, device)）
 * 每 task 独立参数组（t/speed/phase_step/lo/hi 为 f64 标量数组, 同 C 语义）:
 *   phase = i * phase_step;  v = wave(t*speed + phase, wave_fn[i]);
 *   val = lo + v*(hi - lo);  int() 截断 + clamp[0,255]
 * wave_fn 0-7 波形（0 sin / 1 方波 / 2 锯齿 / 3 三角 / 4 splitmix64 确定性
 *   随机 / 5 cos / 6 整流 sin / 7 exp 包络; 其他 → 0 号波形）
 * n        int [ntask]       每 task 设备数
 * offsets  int [ntask+1]     前缀 n 和（offsets[0]=0）; out 总长 = offsets[ntask]
 * out      u8  [offsets[ntask]]
 * 返回 0 成功 / -1 失败。ntask<=0 或 offsets[ntask]<=0 → 0（无操作）。 */
CE_GPU_API int ce_gpu_wave_batch(ce_gpu_dev* dev,
                                 const double* t, const double* speed,
                                 const double* phase_step, const double* lo,
                                 const double* hi, const int* wave_fn,
                                 const int* n, const int* offsets,
                                 int ntask, uint8_t* out);

/* ── 错误信息（dev 私有缓冲, 本次错误覆盖上次）─────────────────────── */
CE_GPU_API const char* ce_gpu_last_error(ce_gpu_dev* dev);

#ifdef __cplusplus
}
#endif

#endif /* CE_GPU_API_H */
