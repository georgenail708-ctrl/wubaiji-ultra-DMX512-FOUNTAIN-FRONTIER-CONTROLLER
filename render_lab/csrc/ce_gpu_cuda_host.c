/*
 * Qascade CE — CUDA 后端 host 侧（CUDA Driver API 版, clang 交叉编译路径）。
 *
 * 与 ce_gpu_cuda.cu（nvcc + Runtime API 路径）的关系:
 *   两条路径产物同名同契约（ce_gpu_api.h 11 个符号）, 行为等价; 本文件供
 *   **无 CUDA Toolkit 的开发机**用 clang 编设备端 + MSVC 编 host 产出
 *   ce_gpu_cuda.dll（见 tools/build_cuda_clang.py）。
 *   差异: 内核以 cubin 字节数组随 DLL 内嵌（构建脚本生成
 *   ce_gpu_cuda_kernel_bin.h）, 运行时 cuModuleLoadData 加载; 无 cudart
 *   依赖 —— 只动态加载驱动自带 nvcuda.dll（无驱动机器 probe=0 优雅降级,
 *   而非加载失败）。
 *
 * 位级等价: 内核算术 100% 来自 ce_gpu_kernels.h（与 CPU 桩/OpenCL 同源）,
 *   构建期 -ffp-contract=off（等价 -fmad=false）。本文件只做索引映射与
 *   内存搬运, 不改任何运算。K0 = CE_GPU_CAP_EXACT_MIX; K1 超越函数
 *   与主机 libm 最后 1 ulp 不保证 → CE_GPU_CAP_EXACT_WAVE=0。
 *
 * 线程安全: 单句柄单流, 不支持并发 run（调用方串行化, 同 ce_gpu_cuda.cu）;
 *   每次入口 cuCtxSetCurrent 兜底跨线程切换（烘焙 worker 线程场景）。
 */
#include "ce_gpu_api.h"
#include "ce_gpu_cuda_kernel_bin.h"

#include <windows.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CE_GPU_MAX_ERR 256
#define CU_CTX_SCHED_AUTO 0x00u

/* ── CUDA Driver API 精简声明（动态加载, 免 cuda.h / nvcuda.lib）──────── */
typedef int CUresult;
typedef int CUdevice;
typedef void* CUcontext;
typedef void* CUmodule;
typedef void* CUfunction;
typedef void* CUstream;
typedef uint64_t CUdeviceptr;
typedef unsigned long long CUtexref;

#define cu_ok(r) ((r) == 0)

typedef CUresult (__stdcall *PFN_cuInit)(unsigned int);
typedef CUresult (__stdcall *PFN_cuDeviceGetCount)(int*);
typedef CUresult (__stdcall *PFN_cuDeviceGet)(CUdevice*, int);
typedef CUresult (__stdcall *PFN_cuDeviceGetName)(char*, int, CUdevice);
typedef CUresult (__stdcall *PFN_cuCtxCreate)(CUcontext*, unsigned int,
                                              CUdevice);
typedef CUresult (__stdcall *PFN_cuCtxDestroy)(CUcontext);
typedef CUresult (__stdcall *PFN_cuCtxSetCurrent)(CUcontext);
typedef CUresult (__stdcall *PFN_cuModuleLoadData)(CUmodule*, const void*);
typedef CUresult (__stdcall *PFN_cuModuleUnload)(CUmodule);
typedef CUresult (__stdcall *PFN_cuModuleGetFunction)(CUfunction*, CUmodule,
                                                      const char*);
typedef CUresult (__stdcall *PFN_cuMemAlloc)(CUdeviceptr*, size_t);
typedef CUresult (__stdcall *PFN_cuMemFree)(CUdeviceptr);
typedef CUresult (__stdcall *PFN_cuMemcpyHtoDAsync)(CUdeviceptr, const void*,
                                                    size_t, CUstream);
typedef CUresult (__stdcall *PFN_cuMemcpyDtoHAsync)(void*, CUdeviceptr,
                                                    size_t, CUstream);
typedef CUresult (__stdcall *PFN_cuStreamCreate)(CUstream*, unsigned int);
typedef CUresult (__stdcall *PFN_cuStreamDestroy)(CUstream);
typedef CUresult (__stdcall *PFN_cuStreamSynchronize)(CUstream);
typedef CUresult (__stdcall *PFN_cuLaunchKernel)(CUfunction,
                                                 unsigned int, unsigned int,
                                                 unsigned int, unsigned int,
                                                 unsigned int, unsigned int,
                                                 unsigned int, CUstream,
                                                 void**, void**);
typedef CUresult (__stdcall *PFN_cuGetErrorName)(CUresult, const char**);
typedef CUresult (__stdcall *PFN_cuGetErrorString)(CUresult, const char**);

typedef struct ce_cu_api {
    HMODULE dll;
    PFN_cuInit Init;
    PFN_cuDeviceGetCount DeviceGetCount;
    PFN_cuDeviceGet DeviceGet;
    PFN_cuDeviceGetName DeviceGetName;
    PFN_cuCtxCreate CtxCreate;
    PFN_cuCtxDestroy CtxDestroy;
    PFN_cuCtxSetCurrent CtxSetCurrent;
    PFN_cuModuleLoadData ModuleLoadData;
    PFN_cuModuleUnload ModuleUnload;
    PFN_cuModuleGetFunction ModuleGetFunction;
    PFN_cuMemAlloc MemAlloc;
    PFN_cuMemFree MemFree;
    PFN_cuMemcpyHtoDAsync MemcpyHtoDAsync;
    PFN_cuMemcpyDtoHAsync MemcpyDtoHAsync;
    PFN_cuStreamCreate StreamCreate;
    PFN_cuStreamDestroy StreamDestroy;
    PFN_cuStreamSynchronize StreamSynchronize;
    PFN_cuLaunchKernel LaunchKernel;
    PFN_cuGetErrorName GetErrorName;
    PFN_cuGetErrorString GetErrorString;
} ce_cu_api;

static ce_cu_api g_cu;
static int g_cu_state = 0;   /* 0=未尝试 1=可用 -1=不可用 */

/* GetProcAddress 兼容 _v2 后缀（64 位导出名差异） */
static void* cu_sym(const char* base)
{
    char buf[128];
    void* p = (void*)GetProcAddress(g_cu.dll, base);
    if (p != NULL)
        return p;
    if (snprintf(buf, sizeof(buf), "%s_v2", base) < (int)sizeof(buf)) {
        p = (void*)GetProcAddress(g_cu.dll, buf);
    }
    return p;
}

static int cu_load(void)
{
    if (g_cu_state != 0)
        return g_cu_state == 1;
    g_cu_state = -1;
    g_cu.dll = LoadLibraryA("nvcuda.dll");
    if (g_cu.dll == NULL)
        return 0;
#define BIND(member, name)                                                  \
    do {                                                                    \
        g_cu.member = (void*)cu_sym(name);                                  \
        if (g_cu.member == NULL)                                            \
            return 0;                                                       \
    } while (0)
    BIND(Init, "cuInit");
    BIND(DeviceGetCount, "cuDeviceGetCount");
    BIND(DeviceGet, "cuDeviceGet");
    BIND(DeviceGetName, "cuDeviceGetName");
    BIND(CtxCreate, "cuCtxCreate");
    BIND(CtxDestroy, "cuCtxDestroy");
    BIND(CtxSetCurrent, "cuCtxSetCurrent");
    BIND(ModuleLoadData, "cuModuleLoadData");
    BIND(ModuleUnload, "cuModuleUnload");
    BIND(ModuleGetFunction, "cuModuleGetFunction");
    BIND(MemAlloc, "cuMemAlloc");
    BIND(MemFree, "cuMemFree");
    BIND(MemcpyHtoDAsync, "cuMemcpyHtoDAsync");
    BIND(MemcpyDtoHAsync, "cuMemcpyDtoHAsync");
    BIND(StreamCreate, "cuStreamCreate");
    BIND(StreamDestroy, "cuStreamDestroy");
    BIND(StreamSynchronize, "cuStreamSynchronize");
    BIND(LaunchKernel, "cuLaunchKernel");
    BIND(GetErrorName, "cuGetErrorName");
    BIND(GetErrorString, "cuGetErrorString");
#undef BIND
    /* 驱动需可用（cuInit 失败 = 无设备/驱动异常 → 本后端不可用） */
    if (!cu_ok(g_cu.Init(0)))
        return 0;
    g_cu_state = 1;
    return 1;
}

/* ── 设备缓冲（按需增长缓存, 同 ce_gpu_cuda.cu）──────────────────────── */
typedef struct ce_gpu_buf {
    CUdeviceptr d;
    size_t cap;
} ce_gpu_buf;

struct ce_gpu_dev {
    ce_gpu_backend_e backend;
    int device;
    char name[CE_GPU_MAX_ERR];
    CUcontext ctx;
    CUstream stream;
    CUmodule mod;
    CUfunction k_mix;
    CUfunction k_wave;
    ce_gpu_buf m_vals, m_acts, m_anchors, m_nlays, m_out, m_act_any;
    ce_gpu_buf w_t, w_speed, w_phase, w_lo, w_hi, w_fn, w_off, w_out;
    char err[CE_GPU_MAX_ERR];
};

static void ce_gpu_cuerr(ce_gpu_dev* dev, const char* ctxname, CUresult r)
{
    const char* nm = NULL;
    const char* ds = NULL;
    if (g_cu_state == 1) {
        if (g_cu.GetErrorName != NULL)
            g_cu.GetErrorName(r, &nm);
        if (g_cu.GetErrorString != NULL)
            g_cu.GetErrorString(r, &ds);
    }
    snprintf(dev->err, CE_GPU_MAX_ERR, "%s: %s (%s)",
             ctxname, nm != NULL ? nm : "CUDA_ERROR",
             ds != NULL ? ds : "unknown");
}

static void ce_gpu_buf_free(ce_gpu_buf* b)
{
    if (b->d != 0) {
        if (g_cu_state == 1)
            g_cu.MemFree(b->d);
        b->d = 0;
        b->cap = 0;
    }
}

static int ce_gpu_buf_ensure(ce_gpu_dev* dev, ce_gpu_buf* b, size_t bytes,
                             const char* what)
{
    if (b->d != 0 && b->cap >= bytes)
        return 0;
    if (b->d != 0) {
        g_cu.MemFree(b->d);
        b->d = 0;
        b->cap = 0;
    }
    if (bytes == 0)
        return 0;
    CUdeviceptr p = 0;
    CUresult r = g_cu.MemAlloc(&p, bytes);
    if (!cu_ok(r)) {
        ce_gpu_cuerr(dev, what, r);
        return -1;
    }
    b->d = p;
    b->cap = bytes;
    return 0;
}

/* 每次入口兜底当前 context（跨线程调用场景, 见文件头注释） */
static void ce_gpu_ctx_guard(ce_gpu_dev* dev)
{
    if (dev != NULL && dev->ctx != NULL && g_cu.CtxSetCurrent != NULL)
        g_cu.CtxSetCurrent(dev->ctx);
}

/* 块大小（2026-09-18）: 默认 256（该类 memory-bound 内核的通用最优值）。
 * 环境变量 QASCADE_CE_CUDA_BLOCK 可调（128/256/512）, 供 4080 真机调优;
 * 块大小不改变任何 work-item 的计算内容 → 位级等价不受影响。
 * 非法值/越界 → 回落 256。 */
static unsigned int ce_gpu_block_size(void)
{
    const char* v = getenv("QASCADE_CE_CUDA_BLOCK");
    if (v != NULL && v[0] != '\0') {
        long n = atol(v);
        if (n == 128 || n == 256 || n == 512 || n == 1024)
            return (unsigned int)n;
    }
    return 256u;
}

/* ── ce_gpu_api 实现 ─────────────────────────────────────────────────── */

CE_GPU_API ce_gpu_backend_e ce_gpu_probe(void)
{
    if (!cu_load())
        return CE_GPU_BACKEND_NONE;
    int n = 0;
    if (!cu_ok(g_cu.DeviceGetCount(&n)) || n <= 0)
        return CE_GPU_BACKEND_NONE;
    return CE_GPU_BACKEND_CUDA;
}

CE_GPU_API ce_gpu_dev* ce_gpu_init(ce_gpu_backend_e backend,
                                   const char* device_hint)
{
    if (backend != CE_GPU_BACKEND_CUDA)
        return NULL;
    if (!cu_load())
        return NULL;
    int dev = (device_hint != NULL) ? atoi(device_hint) : 0;
    int n = 0;
    if (!cu_ok(g_cu.DeviceGetCount(&n)) || n <= 0)
        return NULL;
    if (dev < 0 || dev >= n)
        dev = 0;
    CUdevice cudev = 0;
    if (!cu_ok(g_cu.DeviceGet(&cudev, dev)))
        return NULL;
    ce_gpu_dev* d = (ce_gpu_dev*)calloc(1, sizeof(ce_gpu_dev));
    if (d == NULL)
        return NULL;
    d->backend = CE_GPU_BACKEND_CUDA;
    d->device = dev;
    char nm[CE_GPU_MAX_ERR];
    nm[0] = '\0';
    if (cu_ok(g_cu.DeviceGetName(nm, (int)sizeof(nm), cudev)) &&
        nm[0] != '\0')
        snprintf(d->name, CE_GPU_MAX_ERR, "%s", nm);
    else
        snprintf(d->name, CE_GPU_MAX_ERR, "cuda-device-%d", dev);
    CUresult r = g_cu.CtxCreate(&d->ctx, CU_CTX_SCHED_AUTO, cudev);
    if (!cu_ok(r)) {
        ce_gpu_cuerr(d, "cuCtxCreate", r);
        free(d);
        return NULL;
    }
    r = g_cu.StreamCreate(&d->stream, 0);
    if (!cu_ok(r)) {
        ce_gpu_cuerr(d, "cuStreamCreate", r);
        g_cu.CtxDestroy(d->ctx);
        free(d);
        return NULL;
    }
    return d;
}

CE_GPU_API void ce_gpu_destroy(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return;
    ce_gpu_ctx_guard(dev);
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
    if (dev->mod != NULL)
        g_cu.ModuleUnload(dev->mod);
    if (dev->stream != NULL)
        g_cu.StreamDestroy(dev->stream);
    if (dev->ctx != NULL)
        g_cu.CtxDestroy(dev->ctx);
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
    if (dev == NULL)
        return -1;
    if (dev->mod != NULL)
        return 1;
    ce_gpu_ctx_guard(dev);
    CUmodule mod = NULL;
    CUresult r = g_cu.ModuleLoadData(&mod, (const void*)CE_GPU_CUDA_CUBIN);
    if (!cu_ok(r)) {
        ce_gpu_cuerr(dev, "cuModuleLoadData(cubin)", r);
        return 0;
    }
    CUfunction fmix = NULL;
    CUfunction fwave = NULL;
    r = g_cu.ModuleGetFunction(&fmix, mod, "ce_gpu_kernel_mix_batch");
    if (!cu_ok(r)) {
        ce_gpu_cuerr(dev, "cuModuleGetFunction(mix)", r);
        g_cu.ModuleUnload(mod);
        return 0;
    }
    r = g_cu.ModuleGetFunction(&fwave, mod, "ce_gpu_kernel_wave_batch");
    if (!cu_ok(r)) {
        ce_gpu_cuerr(dev, "cuModuleGetFunction(wave)", r);
        g_cu.ModuleUnload(mod);
        return 0;
    }
    dev->mod = mod;
    dev->k_mix = fmix;
    dev->k_wave = fwave;
    return 1;
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
    if (ce_gpu_build(dev) != 1)
        return -1;
    ce_gpu_ctx_guard(dev);
    size_t layers = (size_t)n_univ * lmax * 512;
    size_t total = (size_t)n_univ * 512;
    if (ce_gpu_buf_ensure(dev, &dev->m_vals, layers * sizeof(float),
                          "cuMemAlloc vals") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_acts, layers, "cuMemAlloc acts") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_anchors, layers * sizeof(float),
                          "cuMemAlloc anchors") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_nlays, (size_t)n_univ * sizeof(int),
                          "cuMemAlloc nlays") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_out, total, "cuMemAlloc out") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->m_act_any, total,
                          "cuMemAlloc act_any") < 0)
        return -1;
    CUresult r;
    r = g_cu.MemcpyHtoDAsync(dev->m_vals.d, vals, layers * sizeof(float),
                             dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D vals", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->m_acts.d, acts, layers, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D acts", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->m_anchors.d, anchors,
                             layers * sizeof(float), dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D anchors", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->m_nlays.d, nlays,
                             (size_t)n_univ * sizeof(int), dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D nlays", r); return -1; }

    int n_univ_v = n_univ, lmax_v = lmax;
    float mod_gain_v = mod_gain;
    const float* p_vals = (const float*)dev->m_vals.d;
    const uint8_t* p_acts = (const uint8_t*)dev->m_acts.d;
    const float* p_anchors = (const float*)dev->m_anchors.d;
    const int* p_nlays = (const int*)dev->m_nlays.d;
    uint8_t* p_out = (uint8_t*)dev->m_out.d;
    uint8_t* p_any = (uint8_t*)dev->m_act_any.d;
    void* args[9];
    args[0] = &p_vals;
    args[1] = &p_acts;
    args[2] = &p_anchors;
    args[3] = &p_nlays;
    args[4] = &n_univ_v;
    args[5] = &lmax_v;
    args[6] = &mod_gain_v;
    args[7] = &p_out;
    args[8] = &p_any;
    unsigned int bs = ce_gpu_block_size();
    unsigned int blocks = (unsigned int)((total + bs - 1) / bs);
    r = g_cu.LaunchKernel(dev->k_mix, blocks, 1, 1, bs, 1, 1, 0,
                          dev->stream, args, NULL);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "cuLaunchKernel(mix)", r); return -1; }
    r = g_cu.MemcpyDtoHAsync(out, dev->m_out.d, total, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "D2H out", r); return -1; }
    r = g_cu.MemcpyDtoHAsync(act_any, dev->m_act_any.d, total, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "D2H act_any", r); return -1; }
    r = g_cu.StreamSynchronize(dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "stream sync", r); return -1; }
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
    if (ce_gpu_build(dev) != 1)
        return -1;
    ce_gpu_ctx_guard(dev);
    size_t nb = (size_t)ntask * sizeof(double);
    size_t ni = (size_t)ntask * sizeof(int);
    if (ce_gpu_buf_ensure(dev, &dev->w_t, nb, "cuMemAlloc t") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_speed, nb, "cuMemAlloc speed") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_phase, nb, "cuMemAlloc phase") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_lo, nb, "cuMemAlloc lo") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_hi, nb, "cuMemAlloc hi") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_fn, ni, "cuMemAlloc wave_fn") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_off, (size_t)(ntask + 1) * sizeof(int),
                          "cuMemAlloc offsets") < 0)
        return -1;
    if (ce_gpu_buf_ensure(dev, &dev->w_out, total, "cuMemAlloc out") < 0)
        return -1;
    CUresult r;
    r = g_cu.MemcpyHtoDAsync(dev->w_t.d, t, nb, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D t", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_speed.d, speed, nb, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D speed", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_phase.d, phase_step, nb, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D phase_step", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_lo.d, lo, nb, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D lo", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_hi.d, hi, nb, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D hi", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_fn.d, wave_fn, ni, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D wave_fn", r); return -1; }
    r = g_cu.MemcpyHtoDAsync(dev->w_off.d, offsets,
                             (size_t)(ntask + 1) * sizeof(int), dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "H2D offsets", r); return -1; }

    const double* p_t = (const double*)dev->w_t.d;
    const double* p_speed = (const double*)dev->w_speed.d;
    const double* p_phase = (const double*)dev->w_phase.d;
    const double* p_lo = (const double*)dev->w_lo.d;
    const double* p_hi = (const double*)dev->w_hi.d;
    const int* p_fn = (const int*)dev->w_fn.d;
    const int* p_off = (const int*)dev->w_off.d;
    int ntask_v = ntask;
    uint8_t* p_out = (uint8_t*)dev->w_out.d;
    void* args[9];
    args[0] = &p_t;
    args[1] = &p_speed;
    args[2] = &p_phase;
    args[3] = &p_lo;
    args[4] = &p_hi;
    args[5] = &p_fn;
    args[6] = &p_off;
    args[7] = &ntask_v;
    args[8] = &p_out;
    unsigned int bs = ce_gpu_block_size();
    unsigned int blocks = (unsigned int)((total + bs - 1) / bs);
    r = g_cu.LaunchKernel(dev->k_wave, blocks, 1, 1, bs, 1, 1, 0,
                          dev->stream, args, NULL);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "cuLaunchKernel(wave)", r); return -1; }
    r = g_cu.MemcpyDtoHAsync(out, dev->w_out.d, total, dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "D2H out", r); return -1; }
    r = g_cu.StreamSynchronize(dev->stream);
    if (!cu_ok(r)) { ce_gpu_cuerr(dev, "stream sync", r); return -1; }
    return 0;
}

CE_GPU_API const char* ce_gpu_last_error(ce_gpu_dev* dev)
{
    return (dev != NULL) ? dev->err : "";
}

/* ── 便捷别名（ce_cuda_* 直连, 与 ce_gpu_* 等价）────────────────────── */

CE_GPU_API ce_gpu_dev* ce_cuda_init(const char* device_hint)
{
    return ce_gpu_init(CE_GPU_BACKEND_CUDA, device_hint);
}

CE_GPU_API void ce_cuda_destroy(ce_gpu_dev* dev)
{
    ce_gpu_destroy(dev);
}
