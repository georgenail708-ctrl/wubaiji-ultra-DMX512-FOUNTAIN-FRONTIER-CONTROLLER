/*
 * Qascade CE — GPU API 的 CPU 串行桩（语义验证工件, 不随产品发布）
 *
 * 用途: 本机无 NVIDIA/CUDA 时, 用本桩验证「GPU 内核算术」语义——
 *   桩与 ce_gpu_cuda.cu / ce_gpu_opencl.c 共享同一份内核源码
 *   ce_gpu_kernels.h（CUDA 侧编译为 __device__, 本桩编译为普通内联）;
 *   探针 bench/ce_probe_gpu_equiv.py 把桩输出与 ce_engine.dll 的
 *   ce_mix_clips / ce_wave_effect 位级对拍。桩一致 ⟹ 内核逻辑一致;
 *   设备端只差编译选项（-fmad=false 等）与超越函数实现（已在
 *   CE_GPU_CAP_EXACT_WAVE 文档中声明）。
 *
 * 构建: 见 render_lab/qascade_ce/build_gpu.py stub 目标。
 */
#include "ce_gpu_api.h"
#include "ce_gpu_kernels.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CE_GPU_MAX_ERR 256

struct ce_gpu_dev {
    ce_gpu_backend_e backend;
    char name[CE_GPU_MAX_ERR];
    char err[CE_GPU_MAX_ERR];
};

/* ── ce_gpu_api 实现（串行, 逐通道同序）────────────────────────────── */

CE_GPU_API ce_gpu_backend_e ce_gpu_probe(void)
{
    /* 桩无 GPU; 返回 NONE（本机无可用后端时探测结果一致） */
    return CE_GPU_BACKEND_NONE;
}

CE_GPU_API ce_gpu_dev* ce_gpu_init(ce_gpu_backend_e backend,
                                   const char* device_hint)
{
    /* 桩是语义替身: 接受任一后端请求（不校验设备存在性） */
    if (backend == CE_GPU_BACKEND_NONE)
        return NULL;
    ce_gpu_dev* d = (ce_gpu_dev*)calloc(1, sizeof(ce_gpu_dev));
    if (d == NULL)
        return NULL;
    d->backend = backend;
    snprintf(d->name, CE_GPU_MAX_ERR, "cpu-stub");
    return d;
}

CE_GPU_API void ce_gpu_destroy(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return;
    free(dev);
}

CE_GPU_API const char* ce_gpu_name(ce_gpu_dev* dev)
{
    return (dev != NULL) ? dev->name : "";
}

CE_GPU_API uint32_t ce_gpu_caps(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return 0;
    /* 桩即参考语义本身 → 两能力位均保证 */
    return CE_GPU_CAP_EXACT_MIX | CE_GPU_CAP_EXACT_WAVE;
}

CE_GPU_API int ce_gpu_build(ce_gpu_dev* dev)
{
    return (dev != NULL) ? 1 : -1;
}

CE_GPU_API int ce_gpu_mix_batch(ce_gpu_dev* dev,
                                const float* vals, const uint8_t* acts,
                                const float* anchors, const int* nlays,
                                int n_univ, int lmax, float mod_gain,
                                uint8_t* out, uint8_t* act_any)
{
    if (dev == NULL || vals == NULL || acts == NULL || anchors == NULL ||
        nlays == NULL || out == NULL || act_any == NULL)
        return -1;
    if (n_univ <= 0)
        return 0;
    if (lmax <= 0) {
        snprintf(dev->err, CE_GPU_MAX_ERR, "ce_gpu_mix_batch: lmax<=0");
        return -1;
    }
    for (int u = 0; u < n_univ; u++) {
        for (int c = 0; c < 512; c++) {
            ce_gpu_mix_channel(
                vals + (size_t)u * lmax * 512,
                acts + (size_t)u * lmax * 512,
                anchors + (size_t)u * lmax * 512,
                nlays[u], 512, c, mod_gain,
                out + (size_t)u * 512 + c, act_any + (size_t)u * 512 + c);
        }
    }
    return 0;
}

CE_GPU_API int ce_gpu_wave_batch(ce_gpu_dev* dev,
                                 const double* t, const double* speed,
                                 const double* phase_step, const double* lo,
                                 const double* hi, const int* wave_fn,
                                 const int* n, const int* offsets,
                                 int ntask, uint8_t* out)
{
    if (dev == NULL || t == NULL || speed == NULL || phase_step == NULL ||
        lo == NULL || hi == NULL || wave_fn == NULL || n == NULL ||
        offsets == NULL || out == NULL)
        return -1;
    if (ntask <= 0)
        return 0;
    if (offsets[ntask] <= 0)
        return 0;
    for (int task = 0; task < ntask; task++) {
        for (int i = 0; i < n[task]; i++) {
            ce_gpu_wave_item(t[task], speed[task], phase_step[task],
                             lo[task], hi[task], wave_fn[task], i,
                             out + (size_t)offsets[task] + i);
        }
    }
    return 0;
}

CE_GPU_API const char* ce_gpu_last_error(ce_gpu_dev* dev)
{
    return (dev != NULL) ? dev->err : "";
}
