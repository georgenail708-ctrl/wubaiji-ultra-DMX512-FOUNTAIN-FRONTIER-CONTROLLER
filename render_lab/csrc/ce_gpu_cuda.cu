/*
 * Qascade CE — CUDA 后端（ce_gpu_api 实现, NVIDIA 全适配）
 *
 * 构建（工程机, 需 CUDA Toolkit）:
 *   nvcc -O3 -fmad=false -arch=sm_80 -cudart=static --shared \
 *         -Xcompiler "/O2,/utf-8" -o ce_gpu_cuda.dll ce_gpu_cuda.cu
 *   （完整命令见 render_lab/qascade_ce/build_gpu.py; -fmad=false 为位级
 *     等价硬前提: 禁 fma 收缩, 与 MSVC /fp:precise 同级舍入）
 *
 * 位级等价:
 *   内核算术 100% 来自 ce_gpu_kernels.h（与 CPU 桩同源, 探针位级对拍
 *   ce_engine.dll）; 本文件只做索引映射与内存搬运, 不改任何运算。
 *   - K0 帧合成: 纯 IEEE f32 → CE_GPU_CAP_EXACT_MIX 保证位级一致
 *   - K1 波形: sin/cos/exp 走 libdevice, 与主机 ucrtbase 最后 1 ulp
 *     不保证 → CE_GPU_CAP_EXACT_WAVE=0, 由调用方按位级纪律决策
 *     （波形效果需字节等价时回退 CPU ce_wave_effect）
 *
 * 线程安全: 单句柄单流, 不支持并发 run（调用方串行化）。
 */
#include "ce_gpu_api.h"
#include "ce_gpu_kernels.h"

#include <cuda_runtime.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 禁 fma 收缩（与 -fmad=false 双保险; MSVC /fp:precise 同语义） */
#pragma nv_fp_contract(off)

#define CE_GPU_MAX_ERR 256

/* 块大小（2026-09-18）: 默认 256（memory-bound 内核通用最优）;
 * QASCADE_CE_CUDA_BLOCK 可调（128/256/512/1024）供真机调优。
 * 块大小不改任何 work-item 的计算内容 → 位级等价不受影响。 */
static int ce_gpu_block_size(void)
{
    const char* v = getenv("QASCADE_CE_CUDA_BLOCK");
    if (v != NULL && v[0] != '\0') {
        long n = atol(v);
        if (n == 128 || n == 256 || n == 512 || n == 1024)
            return (int)n;
    }
    return 256;
}

/* 设备缓冲（按需增长缓存, 避免每帧 cudaMalloc — DMX tick 热路径） */
typedef struct ce_gpu_buf {
    void* d;
    size_t cap;
} ce_gpu_buf;

struct ce_gpu_dev {
    ce_gpu_backend_e backend;
    int device;
    char name[CE_GPU_MAX_ERR];
    cudaStream_t stream;
    ce_gpu_buf m_vals, m_acts, m_anchors, m_nlays, m_out, m_act_any;
    ce_gpu_buf w_t, w_speed, w_phase, w_lo, w_hi, w_fn, w_off, w_out;
    char err[CE_GPU_MAX_ERR];
};

/* ── 内部工具 ─────────────────────────────────────────────────────────── */

static void ce_gpu_cuerr(ce_gpu_dev* dev, const char* ctx, cudaError_t e)
{
    snprintf(dev->err, CE_GPU_MAX_ERR, "%s: %s", ctx, cudaGetErrorString(e));
}

static void ce_gpu_buf_free(ce_gpu_buf* b)
{
    if (b->d != NULL) {
        cudaFree(b->d);
        b->d = NULL;
        b->cap = 0;
    }
}

static int ce_gpu_buf_ensure(ce_gpu_dev* dev, ce_gpu_buf* b, size_t bytes,
                             const char* what)
{
    if (b->d != NULL && b->cap >= bytes)
        return 0;
    if (b->d != NULL) {
        cudaFree(b->d);
        b->d = NULL;
        b->cap = 0;
    }
    if (bytes == 0)
        return 0;
    cudaError_t e = cudaMalloc(&b->d, bytes);
    if (e != cudaSuccess) {
        ce_gpu_cuerr(dev, what, e);
        return -1;
    }
    b->cap = bytes;
    return 0;
}

/* 内核 1: 批量帧合成 — 每 work-item 单通道, 通道内 nlay 串行（K0 契约） */
__global__ void ce_gpu_kernel_mix_batch(
    const float* vals, const uint8_t* acts, const float* anchors,
    const int* nlays, int n_univ, int lmax, float mod_gain,
    uint8_t* out, uint8_t* act_any)
{
    int g = blockIdx.x * blockDim.x + threadIdx.x;
    if (g >= n_univ * 512)
        return;
    int u = g / 512;
    int c = g - u * 512;
    ce_gpu_mix_channel(
        vals + (size_t)u * lmax * 512, acts + (size_t)u * lmax * 512,
        anchors + (size_t)u * lmax * 512, nlays[u], 512, c, mod_gain,
        out + (size_t)u * 512 + c, act_any + (size_t)u * 512 + c);
}

/* 内核 2: 批量波形求值 — 每 work-item 单 (task, device), 独立求值（K1 契约）
 * n 仅主机契约（offsets 已含其信息, 设备端不冗余上传） */
__global__ void ce_gpu_kernel_wave_batch(
    const double* t, const double* speed, const double* phase_step,
    const double* lo, const double* hi, const int* wave_fn,
    const int* offsets, int ntask, uint8_t* out)
{
    int total = offsets[ntask];
    int g = blockIdx.x * blockDim.x + threadIdx.x;
    if (g >= total)
        return;
    /* work-item → (task, i): offsets 前缀和二分（task 数少, 常数级） */
    int lo_t = 0, hi_t = ntask - 1;
    while (lo_t < hi_t) {
        int mid = (lo_t + hi_t) >> 1;
        if (offsets[mid + 1] <= g)
            lo_t = mid + 1;
        else
            hi_t = mid;
    }
    int task = lo_t;
    ce_gpu_wave_item(t[task], speed[task], phase_step[task], lo[task],
                     hi[task], wave_fn[task], g - offsets[task], out + g);
}

/* ── ce_gpu_api 实现 ─────────────────────────────────────────────────── */

CE_GPU_API ce_gpu_backend_e ce_gpu_probe(void)
{
    int n = 0;
    cudaError_t e = cudaGetDeviceCount(&n);
    if (e != cudaSuccess || n <= 0)
        return CE_GPU_BACKEND_NONE;
    return CE_GPU_BACKEND_CUDA;
}

CE_GPU_API ce_gpu_dev* ce_gpu_init(ce_gpu_backend_e backend,
                                   const char* device_hint)
{
    if (backend != CE_GPU_BACKEND_CUDA)
        return NULL;
    int dev = (device_hint != NULL) ? atoi(device_hint) : 0;
    int n = 0;
    if (cudaGetDeviceCount(&n) != cudaSuccess || n <= 0)
        return NULL;
    if (dev < 0 || dev >= n)
        dev = 0;
    if (cudaSetDevice(dev) != cudaSuccess)
        return NULL;
    ce_gpu_dev* d = (ce_gpu_dev*)calloc(1, sizeof(ce_gpu_dev));
    if (d == NULL)
        return NULL;
    d->backend = CE_GPU_BACKEND_CUDA;
    d->device = dev;
    cudaDeviceProp prop;
    if (cudaGetDeviceProperties(&prop, dev) == cudaSuccess && prop.name[0] != '\0')
        snprintf(d->name, CE_GPU_MAX_ERR, "%s", prop.name);
    else
        snprintf(d->name, CE_GPU_MAX_ERR, "cuda-device-%d", dev);
    if (cudaStreamCreate(&d->stream) != cudaSuccess) {
        free(d);
        return NULL;
    }
    return d;
}

CE_GPU_API void ce_gpu_destroy(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return;
    ce_gpu_buf_free(&dev->m_vals);
    ce_gpu_buf_free(&dev->m_acts);
    ce_gpu_buf_free(&dev->m_anchors);
    ce_gpu_buf_free(&dev->m_nlays);
    ce_gpu_buf_free(&dev->m_out);
    ce_gpu_buf_free(&dev->m_act_any);
    ce_gpu_buf_free(&dev->w_t);
    ce_gpu_buf_free(&dev->w_speed);
    ce_gpu_buf_free(&dev->w_phase);
    ce_gpu_buf_free(&dev->w_lo);
    ce_gpu_buf_free(&dev->w_hi);
    ce_gpu_buf_free(&dev->w_fn);
    ce_gpu_buf_free(&dev->w_off);
    ce_gpu_buf_free(&dev->w_out);
    cudaStreamDestroy(dev->stream);
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
    /* K0 纯 IEEE 保证位级; K1 超越函数不保证（libdevice vs ucrtbase） */
    return CE_GPU_CAP_EXACT_MIX;
}

CE_GPU_API int ce_gpu_build(ce_gpu_dev* dev)
{
    /* 内核已随 DLL 编译为 fatbin, 启动即就绪; 保持 API 对称返回成功 */
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
    size_t layers = (size_t)n_univ * lmax * 512;
    size_t total = (size_t)n_univ * 512;
    if (ce_gpu_buf_ensure(dev, &dev->m_vals, layers * sizeof(float),
                          "cudaMalloc vals") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_acts, layers, "cudaMalloc acts") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_anchors, layers * sizeof(float),
                          "cudaMalloc anchors") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_nlays, (size_t)n_univ * sizeof(int),
                          "cudaMalloc nlays") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_out, total, "cudaMalloc out") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_act_any, total,
                          "cudaMalloc act_any") < 0)
        return -1;
    cudaError_t e;
    e = cudaMemcpyAsync(dev->m_vals.d, vals, layers * sizeof(float),
                        cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D vals", e); return -1; }
    e = cudaMemcpyAsync(dev->m_acts.d, acts, layers, cudaMemcpyHostToDevice,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D acts", e); return -1; }
    e = cudaMemcpyAsync(dev->m_anchors.d, anchors, layers * sizeof(float),
                        cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D anchors", e); return -1; }
    e = cudaMemcpyAsync(dev->m_nlays.d, nlays, (size_t)n_univ * sizeof(int),
                        cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D nlays", e); return -1; }

    int bs = ce_gpu_block_size();
    int blocks = (int)((total + (size_t)bs - 1) / (size_t)bs);
    ce_gpu_kernel_mix_batch<<<blocks, bs, 0, dev->stream>>>(
        (const float*)dev->m_vals.d, (const uint8_t*)dev->m_acts.d,
        (const float*)dev->m_anchors.d, (const int*)dev->m_nlays.d,
        n_univ, lmax, mod_gain, (uint8_t*)dev->m_out.d,
        (uint8_t*)dev->m_act_any.d);
    e = cudaGetLastError();
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "kernel launch", e); return -1; }
    e = cudaMemcpyAsync(out, dev->m_out.d, total, cudaMemcpyDeviceToHost,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "D2H out", e); return -1; }
    e = cudaMemcpyAsync(act_any, dev->m_act_any.d, total,
                        cudaMemcpyDeviceToHost, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "D2H act_any", e); return -1; }
    e = cudaStreamSynchronize(dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "stream sync", e); return -1; }
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
    size_t total = (size_t)offsets[ntask];
    if (total <= 0)
        return 0;
    size_t nb = (size_t)ntask * sizeof(double);
    size_t ni = (size_t)ntask * sizeof(int);
    if (ce_gpu_buf_ensure(dev, &dev->w_t, nb, "cudaMalloc t") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_speed, nb, "cudaMalloc speed") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_phase, nb, "cudaMalloc phase") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_lo, nb, "cudaMalloc lo") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_hi, nb, "cudaMalloc hi") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_fn, ni, "cudaMalloc wave_fn") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_off, (size_t)(ntask + 1) * sizeof(int),
                          "cudaMalloc offsets") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_out, total, "cudaMalloc out") < 0)
        return -1;
    cudaError_t e;
    e = cudaMemcpyAsync(dev->w_t.d, t, nb, cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D t", e); return -1; }
    e = cudaMemcpyAsync(dev->w_speed.d, speed, nb, cudaMemcpyHostToDevice,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D speed", e); return -1; }
    e = cudaMemcpyAsync(dev->w_phase.d, phase_step, nb, cudaMemcpyHostToDevice,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D phase", e); return -1; }
    e = cudaMemcpyAsync(dev->w_lo.d, lo, nb, cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D lo", e); return -1; }
    e = cudaMemcpyAsync(dev->w_hi.d, hi, nb, cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D hi", e); return -1; }
    e = cudaMemcpyAsync(dev->w_fn.d, wave_fn, ni, cudaMemcpyHostToDevice,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D wave_fn", e); return -1; }
    e = cudaMemcpyAsync(dev->w_off.d, offsets, (size_t)(ntask + 1) * sizeof(int),
                        cudaMemcpyHostToDevice, dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "H2D offsets", e); return -1; }

    int bs = ce_gpu_block_size();
    int blocks = (int)((total + (size_t)bs - 1) / (size_t)bs);
    ce_gpu_kernel_wave_batch<<<blocks, bs, 0, dev->stream>>>(
        (const double*)dev->w_t.d, (const double*)dev->w_speed.d,
        (const double*)dev->w_phase.d, (const double*)dev->w_lo.d,
        (const double*)dev->w_hi.d, (const int*)dev->w_fn.d,
        (const int*)dev->w_off.d, ntask, (uint8_t*)dev->w_out.d);
    e = cudaGetLastError();
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "kernel launch", e); return -1; }
    e = cudaMemcpyAsync(out, dev->w_out.d, total, cudaMemcpyDeviceToHost,
                        dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "D2H out", e); return -1; }
    e = cudaStreamSynchronize(dev->stream);
    if (e != cudaSuccess) { ce_gpu_cuerr(dev, "stream sync", e); return -1; }
    return 0;
}

CE_GPU_API const char* ce_gpu_last_error(ce_gpu_dev* dev)
{
    return (dev != NULL) ? dev->err : "";
}

/* ── CUDA 后端便捷别名（ce_cuda_* 直连, 与 ce_gpu_* 等价）────────────── */

CE_GPU_API ce_gpu_dev* ce_cuda_init(const char* device_hint)
{
    return ce_gpu_init(CE_GPU_BACKEND_CUDA, device_hint);
}

CE_GPU_API void ce_cuda_destroy(ce_gpu_dev* dev)
{
    ce_gpu_destroy(dev);
}
