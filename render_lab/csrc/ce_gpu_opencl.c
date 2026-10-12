/*
 * Qascade CE — OpenCL 后端（ce_gpu_api 实现, AMD 780M 等无 CUDA 机器路径）
 *
 * 构建（需 OpenCL SDK 头文件; 本仓库已随附 Khronos 官方头
 *   render_lab/csrc/third_party/opencl/CL/）:
 *   cl.exe /LD /O2 /fp:precise /utf-8 /I csrc /I csrc/third_party/opencl
 *         /Fe:ce_gpu_opencl.dll ce_gpu_opencl.c
 *   （完整命令见 render_lab/qascade_ce/build_gpu.py）
 *
 * 位级等价:
 *   内核源码内嵌于 ce_gpu_opencl_src.h（OpenCL C 1.2 无 static inline,
 *   无法直接含 ce_gpu_kernels.h; 与 ce_gpu_kernels.h 逐行同构, 变更需
 *   同步）; #pragma OPENCL FP_CONTRACT OFF 禁 fma 收缩（位级硬前提）。
 *   f32 除法走 -cl-fp32-correctly-rounded-divide-sqrt（构建选项）;
 *   f64 需 cl_khr_fp64（现代 AMD/Intel 驱动均支持）。
 *   超越函数 sin/cos/exp 与主机 ucrtbase 最后 1 ulp 不保证
 *   → CE_GPU_CAP_EXACT_WAVE=0（同 CUDA 后端决策）。
 *
 * v1 简化: 缓冲按调用创建/释放（不缓存, 正确性优先; CUDA 后端已做
 * 缓冲缓存, OpenCL 性能优化留工程机实测后跟进）。
 */
#include "ce_gpu_api.h"
#include "ce_gpu_opencl_src_enc.h"  /* 内核源码密文（tools/ce_gpu_encrypt_src.py
                                     * 生成；明文 ce_gpu_opencl_src.h 只存构建机，
                                     * 绝不随包出货——防 strings 直读 GPU 源码） */

#define CL_TARGET_OPENCL_VERSION 120
#include <CL/cl.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CE_GPU_MAX_ERR 256

struct ce_gpu_dev {
    ce_gpu_backend_e backend;
    cl_platform_id platform;
    cl_device_id device;
    cl_context context;
    cl_command_queue queue;
    cl_program program;
    cl_kernel kmix;
    cl_kernel kwave;
    /* 常驻混合缓冲（蓝图 §5.2: init 建一次, 增长时重建, destroy 释放;
     * 消除每调用 clCreateBuffer×6 + 释放 —— 实测每调用 ~10ms 开销,
     * 常驻后离线烘焙逐帧混合可摊销到 ~µs 级。内核/算术零改动 → 位级不变） */
    cl_mem rv;              /* vals   f32 [n_univ*lmax*512] */
    cl_mem ra;              /* acts   u8  [n_univ*lmax*512] */
    cl_mem rn;              /* anchors f32 [n_univ*lmax*512] */
    cl_mem rl;              /* nlays  int [n_univ] */
    cl_mem ro;              /* out    u8  [n_univ*512] */
    cl_mem rany;            /* act_any u8 [n_univ*512] */
    size_t rv_bytes, ra_bytes, rn_bytes, rl_bytes;
    size_t ro_bytes, rany_bytes;
    /* 常驻波形缓冲（同 §5.2 纪律; wave 不为烘焙热路径, 一并常驻省重复） */
    cl_mem wt, ws, wp, wl, wh, wf, wo, wout;
    size_t wt_b, ws_b, wp_b, wl_b, wh_b, wf_b, wo_b, wout_b;
    char name[CE_GPU_MAX_ERR];
    char err[CE_GPU_MAX_ERR];
};

/* ── 内部工具 ─────────────────────────────────────────────────────────── */

/* 解密内核源码到堆缓冲（XOR 滚动密钥；防的是零成本静态提取，
 * 根治属 SPIR-V 离线编译，见 tools/ce_gpu_encrypt_src.py 头注）。 */
static char* ce_gpu_dec_src(const unsigned char* enc, unsigned int len,
                            unsigned long long key)
{
    char* buf;
    unsigned int i;
    if (enc == NULL || len == 0)
        return NULL;
    buf = (char*)malloc((size_t)len + 1);
    if (buf == NULL)
        return NULL;
    for (i = 0; i < len; i++)
        buf[i] = (char)((unsigned char)enc[i] ^
                        (unsigned char)((key >> ((i & 7u) * 8u)) & 0xffu));
    buf[len] = '\0';
    return buf;
}

static void ce_gpu_clerr(ce_gpu_dev* dev, const char* ctx, cl_int e)
{
    const char* s;
    switch (e) {
    case CL_INVALID_PLATFORM: s = "CL_INVALID_PLATFORM"; break;
    case CL_INVALID_DEVICE: s = "CL_INVALID_DEVICE"; break;
    case CL_INVALID_CONTEXT: s = "CL_INVALID_CONTEXT"; break;
    case CL_INVALID_COMMAND_QUEUE: s = "CL_INVALID_COMMAND_QUEUE"; break;
    case CL_INVALID_MEM_OBJECT: s = "CL_INVALID_MEM_OBJECT"; break;
    case CL_INVALID_KERNEL: s = "CL_INVALID_KERNEL"; break;
    case CL_INVALID_ARG_VALUE: s = "CL_INVALID_ARG_VALUE"; break;
    case CL_INVALID_WORK_GROUP_SIZE: s = "CL_INVALID_WORK_GROUP_SIZE"; break;
    case CL_BUILD_PROGRAM_FAILURE: s = "CL_BUILD_PROGRAM_FAILURE"; break;
    case CL_MEM_OBJECT_ALLOCATION_FAILURE: s = "CL_MEM_OBJECT_ALLOCATION_FAILURE"; break;
    case CL_OUT_OF_RESOURCES: s = "CL_OUT_OF_RESOURCES"; break;
    case CL_OUT_OF_HOST_MEMORY: s = "CL_OUT_OF_HOST_MEMORY"; break;
    case CL_DEVICE_NOT_FOUND: s = "CL_DEVICE_NOT_FOUND"; break;
    default: s = "CL_ERROR"; break;
    }
    snprintf(dev->err, CE_GPU_MAX_ERR, "%s: %s (%d)", ctx, s, (int)e);
}

/* ── ce_gpu_api 实现 ─────────────────────────────────────────────────── */

CE_GPU_API ce_gpu_backend_e ce_gpu_probe(void)
{
    cl_uint nplat = 0;
    if (clGetPlatformIDs(0, NULL, &nplat) != CL_SUCCESS || nplat == 0)
        return CE_GPU_BACKEND_NONE;
    for (cl_uint p = 0; p < nplat; p++) {
        cl_platform_id plat = NULL;
        if (clGetPlatformIDs(1, &plat, NULL) != CL_SUCCESS)
            continue;
        cl_uint ndev = 0;
        if (clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU | CL_DEVICE_TYPE_ACCELERATOR,
                           0, NULL, &ndev) == CL_SUCCESS && ndev > 0)
            return CE_GPU_BACKEND_OPENCL;
    }
    return CE_GPU_BACKEND_NONE;
}

CE_GPU_API ce_gpu_dev* ce_gpu_init(ce_gpu_backend_e backend,
                                   const char* device_hint)
{
    if (backend != CE_GPU_BACKEND_OPENCL)
        return NULL;
    cl_uint nplat = 0;
    if (clGetPlatformIDs(0, NULL, &nplat) != CL_SUCCESS || nplat == 0)
        return NULL;
    cl_platform_id plat = NULL;
    cl_device_id dev = NULL;
    cl_int e;
    /* device_hint: NULL/"" = 自动择优; "0"/"1"... = 显式索引（旧语义）。
     * 自动择优（2026-09-18）: 混合显卡笔记本上平台枚举顺序不定, 旧行为
     * "第一个平台的第一个设备"可能选中核显（如 610M）而非独显（4080）。
     * 打分: 独显 > 核显（CL_DEVICE_HOST_UNIFIED_MEMORY）→ 计算单元数
     * → 显存; 平局取先枚举者。显式 hint 恒尊重用户选择。 */
    int auto_pick = (device_hint == NULL || device_hint[0] == '\0');
    int hint = auto_pick ? 0 : atoi(device_hint);
    int found = -1;
    unsigned long long best_score = 0;
    cl_platform_id best_plat = NULL;
    for (cl_uint p = 0; p < nplat; p++) {
        if (clGetPlatformIDs(1, &plat, NULL) != CL_SUCCESS)
            continue;
        cl_uint ndev = 0;
        e = clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU | CL_DEVICE_TYPE_ACCELERATOR,
                           0, NULL, &ndev);
        if (e != CL_SUCCESS || ndev == 0)
            continue;
        cl_device_id* devs = (cl_device_id*)calloc(ndev, sizeof(cl_device_id));
        if (devs == NULL)
            return NULL;
        if (clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU | CL_DEVICE_TYPE_ACCELERATOR,
                           ndev, devs, NULL) == CL_SUCCESS) {
            if (!auto_pick) {
                /* 显式索引: 旧语义（首个有设备的平台内取 hint, 越界取 0） */
                int pick = (hint >= 0 && hint < (int)ndev) ? hint : 0;
                dev = devs[pick];
                best_plat = plat;
                found = 0;
            } else {
                for (cl_uint k = 0; k < ndev; k++) {
                    cl_bool unified = CL_FALSE;
                    cl_uint cu = 0;
                    cl_ulong gmem = 0;
                    clGetDeviceInfo(devs[k], CL_DEVICE_HOST_UNIFIED_MEMORY,
                                    sizeof(unified), &unified, NULL);
                    clGetDeviceInfo(devs[k], CL_DEVICE_MAX_COMPUTE_UNITS,
                                    sizeof(cu), &cu, NULL);
                    clGetDeviceInfo(devs[k], CL_DEVICE_GLOBAL_MEM_SIZE,
                                    sizeof(gmem), &gmem, NULL);
                    /* 独显权重远大于算力差（核显共享内存常报大值） */
                    unsigned long long score =
                        ((unified == CL_TRUE) ? 0ull : (1ull << 62)) +
                        ((unsigned long long)cu << 24) +
                        (gmem >> 20);
                    if (found < 0 || score > best_score) {
                        best_score = score;
                        dev = devs[k];
                        best_plat = plat;
                        found = 0;
                    }
                }
                if (found < 0) {
                    dev = devs[0];
                    best_plat = plat;
                    found = 0;
                }
            }
        }
        free(devs);
        if (found == 0 && !auto_pick)
            break;
    }
    if (found < 0)
        return NULL;
    plat = best_plat;
    cl_context ctx = clCreateContext(NULL, 1, &dev, NULL, NULL, &e);
    if (e != CL_SUCCESS)
        return NULL;
    cl_command_queue q = clCreateCommandQueue(ctx, dev, 0, &e);
    if (e != CL_SUCCESS) {
        clReleaseContext(ctx);
        return NULL;
    }
    ce_gpu_dev* d = (ce_gpu_dev*)calloc(1, sizeof(ce_gpu_dev));
    if (d == NULL) {
        clReleaseCommandQueue(q);
        clReleaseContext(ctx);
        return NULL;
    }
    d->backend = CE_GPU_BACKEND_OPENCL;
    d->platform = plat;
    d->device = dev;
    d->context = ctx;
    d->queue = q;
    char vendor[128] = {0}, dname[128] = {0};
    clGetPlatformInfo(plat, CL_PLATFORM_VENDOR, sizeof(vendor), vendor, NULL);
    clGetDeviceInfo(dev, CL_DEVICE_NAME, sizeof(dname), dname, NULL);
    snprintf(d->name, CE_GPU_MAX_ERR, "%s: %s",
             vendor[0] ? vendor : "opencl", dname[0] ? dname : "device");
    return d;
}

CE_GPU_API void ce_gpu_destroy(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return;
    if (dev->rv != NULL)
        clReleaseMemObject(dev->rv);
    if (dev->ra != NULL)
        clReleaseMemObject(dev->ra);
    if (dev->rn != NULL)
        clReleaseMemObject(dev->rn);
    if (dev->rl != NULL)
        clReleaseMemObject(dev->rl);
    if (dev->ro != NULL)
        clReleaseMemObject(dev->ro);
    if (dev->rany != NULL)
        clReleaseMemObject(dev->rany);
    if (dev->wt != NULL)
        clReleaseMemObject(dev->wt);
    if (dev->ws != NULL)
        clReleaseMemObject(dev->ws);
    if (dev->wp != NULL)
        clReleaseMemObject(dev->wp);
    if (dev->wl != NULL)
        clReleaseMemObject(dev->wl);
    if (dev->wh != NULL)
        clReleaseMemObject(dev->wh);
    if (dev->wf != NULL)
        clReleaseMemObject(dev->wf);
    if (dev->wo != NULL)
        clReleaseMemObject(dev->wo);
    if (dev->wout != NULL)
        clReleaseMemObject(dev->wout);
    if (dev->kmix != NULL)
        clReleaseKernel(dev->kmix);
    if (dev->kwave != NULL)
        clReleaseKernel(dev->kwave);
    if (dev->program != NULL)
        clReleaseProgram(dev->program);
    if (dev->queue != NULL)
        clReleaseCommandQueue(dev->queue);
    if (dev->context != NULL)
        clReleaseContext(dev->context);
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
    return CE_GPU_CAP_EXACT_MIX;
}

CE_GPU_API int ce_gpu_build(ce_gpu_dev* dev)
{
    if (dev == NULL)
        return -1;
    cl_int e;
    /* 解密内核源码 → clCreateProgramWithSource（规范保证同步拷贝源串）
     * → 用后即焚：明文仅在本次调用引用期间存在于堆上。 */
    char* mix_src = ce_gpu_dec_src(CE_GPU_CL_MIX_ENC, CE_GPU_CL_MIX_ENC_LEN,
                                   CE_GPU_CL_KEY);
    char* wave_src = ce_gpu_dec_src(CE_GPU_CL_WAVE_ENC, CE_GPU_CL_WAVE_ENC_LEN,
                                    CE_GPU_CL_KEY);
    if (mix_src == NULL || wave_src == NULL) {
        free(mix_src);
        free(wave_src);
        snprintf(dev->err, CE_GPU_MAX_ERR, "kernel src decrypt alloc failed");
        return -1;
    }
    const char* srcs[2];
    srcs[0] = mix_src;
    srcs[1] = wave_src;
    cl_program prog = clCreateProgramWithSource(
        dev->context, 2, srcs, NULL, &e);
    memset(mix_src, 0, strlen(mix_src));
    memset(wave_src, 0, strlen(wave_src));
    free(mix_src);
    free(wave_src);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clCreateProgramWithSource", e);
        return -1;
    }
    const char* opts =
        "-cl-fp32-correctly-rounded-divide-sqrt";
    e = clBuildProgram(prog, 1, &dev->device, opts, NULL, NULL);
    if (e != CL_SUCCESS) {
        char log[4096] = {0};
        size_t len = 0;
        clGetProgramBuildInfo(prog, dev->device, CL_PROGRAM_BUILD_LOG,
                              sizeof(log), log, &len);
        snprintf(dev->err, CE_GPU_MAX_ERR, "clBuildProgram: %.*s",
                 (int)((len < sizeof(log)) ? len : sizeof(log) - 1), log);
        clReleaseProgram(prog);
        return -1;
    }
    dev->program = prog;
    cl_kernel km = clCreateKernel(prog, "ce_gpu_cl_mix_batch", &e);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clCreateKernel mix", e);
        return -1;
    }
    cl_kernel kw = clCreateKernel(prog, "ce_gpu_cl_wave_batch", &e);
    if (e != CL_SUCCESS) {
        clReleaseKernel(km);
        ce_gpu_clerr(dev, "clCreateKernel wave", e);
        return -1;
    }
    dev->kmix = km;
    dev->kwave = kw;
    return 1;
}

/* 常驻缓冲: 已够大 → 复用; 不够 → 释放重建。失败返回 NULL（旧缓冲已在
 * 重建失败时释放, 调用方走 fail 路径, 下一调用重新分配——同 v1 语义）。 */
static cl_mem ce_gpu_cl_resident(ce_gpu_dev* dev, cl_mem* slot,
                                 size_t* slot_bytes, cl_mem_flags flags,
                                 size_t bytes, const char* what)
{
    if (*slot != NULL && *slot_bytes >= bytes)
        return *slot;
    if (*slot != NULL) {
        clReleaseMemObject(*slot);
        *slot = NULL;
        *slot_bytes = 0;
    }
    cl_int e;
    cl_mem m = clCreateBuffer(dev->context, flags, bytes, NULL, &e);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, what, e);
        return NULL;
    }
    *slot = m;
    *slot_bytes = bytes;
    return m;
}

static int ce_gpu_cl_write(ce_gpu_dev* dev, cl_mem m, size_t bytes,
                           const void* host, const char* what)
{
    cl_int e = clEnqueueWriteBuffer(dev->queue, m, CL_TRUE, 0, bytes, host,
                                    0, NULL, NULL);
    if (e != CL_SUCCESS)
        ce_gpu_clerr(dev, what, e);
    return e;
}

/* ── 常驻拷贝路径（实测最快: 本机 AMD 驱动 3661.0 上 USE_HOST_PTR 直读
 * 反而 3× 慢——内核写穿主机缓冲 + clFinish 触发整段 cache 同步, 9.98ms vs
 * 3.04ms, 2026-08-04 门测）。6 缓冲生命周期 = dev 生命周期, 仅尺寸不足重建;
 * fail 不释放（可复用）。位级与零拷贝路径无差异（同一份字节同一份算术）。 */
static int ce_gpu_mix_batch_copy(ce_gpu_dev* dev,
                                 const float* vals, const uint8_t* acts,
                                 const float* anchors, const int* nlays,
                                 int n_univ, int lmax, float mod_gain,
                                 uint8_t* out, uint8_t* act_any,
                                 size_t layers, size_t total)
{
    cl_mem bv = ce_gpu_cl_resident(dev, &dev->rv, &dev->rv_bytes,
                                   CL_MEM_READ_ONLY, layers * sizeof(float),
                                   "buf vals");
    if (bv == NULL)
        goto fail;
    cl_mem ba = ce_gpu_cl_resident(dev, &dev->ra, &dev->ra_bytes,
                                   CL_MEM_READ_ONLY, layers, "buf acts");
    if (ba == NULL)
        goto fail;
    cl_mem bn = ce_gpu_cl_resident(dev, &dev->rn, &dev->rn_bytes,
                                   CL_MEM_READ_ONLY, layers * sizeof(float),
                                   "buf anchors");
    if (bn == NULL)
        goto fail;
    cl_mem bl = ce_gpu_cl_resident(dev, &dev->rl, &dev->rl_bytes,
                                   CL_MEM_READ_ONLY,
                                   (size_t)n_univ * sizeof(int), "buf nlays");
    if (bl == NULL)
        goto fail;
    cl_mem bo = ce_gpu_cl_resident(dev, &dev->ro, &dev->ro_bytes,
                                   CL_MEM_WRITE_ONLY, total, "buf out");
    if (bo == NULL)
        goto fail;
    cl_mem bany = ce_gpu_cl_resident(dev, &dev->rany, &dev->rany_bytes,
                                     CL_MEM_WRITE_ONLY, total,
                                     "buf act_any");
    if (bany == NULL)
        goto fail;
    cl_int w;
    w = ce_gpu_cl_write(dev, bv, layers * sizeof(float), vals, "write vals");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, ba, layers, acts, "write acts");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bn, layers * sizeof(float), anchors,
                            "write anchors");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bl, (size_t)n_univ * sizeof(int), nlays,
                            "write nlays");
    if (w != CL_SUCCESS)
        goto fail;
    cl_kernel k = dev->kmix;
    cl_int e;
    e = clSetKernelArg(k, 0, sizeof(cl_mem), &bv);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 1, sizeof(cl_mem), &ba);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 2, sizeof(cl_mem), &bn);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 3, sizeof(cl_mem), &bl);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 4, sizeof(cl_int), &n_univ);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 5, sizeof(cl_int), &lmax);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 6, sizeof(cl_float), &mod_gain);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 7, sizeof(cl_mem), &bo);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 8, sizeof(cl_mem), &bany);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clSetKernelArg mix", e);
        goto fail;
    }
    size_t gsize = total;
    e = clEnqueueNDRangeKernel(dev->queue, k, 1, NULL, &gsize, NULL, 0, NULL,
                               NULL);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clEnqueueNDRangeKernel mix", e);
        goto fail;
    }
    e = clEnqueueReadBuffer(dev->queue, bo, CL_TRUE, 0, total, out, 0, NULL,
                            NULL);
    if (e != CL_SUCCESS) ce_gpu_clerr(dev, "clEnqueueReadBuffer out", e);
    else {
        e = clEnqueueReadBuffer(dev->queue, bany, CL_TRUE, 0, total, act_any,
                                0, NULL, NULL);
        if (e != CL_SUCCESS) ce_gpu_clerr(dev, "clEnqueueReadBuffer act_any", e);
    }
    return (e == CL_SUCCESS) ? 0 : -1;
fail:
    return -1;
}

/* ── 零拷贝路径（备用; 蓝图 §5.1 设想, 本机实测 AMD 驱动负优化——保留为
 * 其他平台回退: 缓冲创建失败或驱动不支持时才有意义）。位级与拷贝路径相同。 */
static int ce_gpu_mix_batch_zc(ce_gpu_dev* dev,
                               const float* vals, const uint8_t* acts,
                               const float* anchors, const int* nlays,
                               int n_univ, int lmax, float mod_gain,
                               uint8_t* out, uint8_t* act_any,
                               size_t layers, size_t total)
{
    cl_mem zc[6] = {NULL};
    void* host[6] = {(void*)vals, (void*)acts, (void*)anchors,
                     (void*)nlays, (void*)out, (void*)act_any};
    size_t hsz[6] = {layers * sizeof(float), layers,
                     layers * sizeof(float), (size_t)n_univ * sizeof(int),
                     total, total};
    cl_int ez = CL_SUCCESS;
    int i;
    for (i = 0; i < 6 && ez == CL_SUCCESS; i++) {
        zc[i] = clCreateBuffer(dev->context,
                               CL_MEM_READ_WRITE | CL_MEM_USE_HOST_PTR,
                               hsz[i], host[i], &ez);
    }
    if (ez != CL_SUCCESS) {
        for (i = 0; i < 6; i++) {
            if (zc[i] != NULL)
                clReleaseMemObject(zc[i]);
        }
        return -1;   /* 缓冲创建失败 → 调用方已试过拷贝路径, 直接失败 */
    }
    cl_kernel kz = dev->kmix;
    ez = clSetKernelArg(kz, 0, sizeof(cl_mem), &zc[0]);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 1, sizeof(cl_mem), &zc[1]);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 2, sizeof(cl_mem), &zc[2]);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 3, sizeof(cl_mem), &zc[3]);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 4, sizeof(cl_int), &n_univ);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 5, sizeof(cl_int), &lmax);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 6, sizeof(cl_float), &mod_gain);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 7, sizeof(cl_mem), &zc[4]);
    if (ez == CL_SUCCESS) ez = clSetKernelArg(kz, 8, sizeof(cl_mem), &zc[5]);
    if (ez == CL_SUCCESS)
        ez = clEnqueueNDRangeKernel(dev->queue, kz, 1, NULL, &total,
                                    NULL, 0, NULL, NULL);
    if (ez == CL_SUCCESS)
        ez = clFinish(dev->queue);
    for (i = 0; i < 6; i++)
        clReleaseMemObject(zc[i]);
    if (ez != CL_SUCCESS)
        ce_gpu_clerr(dev, "zero-copy mix", ez);
    return (ez == CL_SUCCESS) ? 0 : -1;
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
    if (dev->kmix == NULL && ce_gpu_build(dev) < 0)
        return -1;
    size_t layers = (size_t)n_univ * lmax * 512;
    size_t total = (size_t)n_univ * 512;
    int rc = ce_gpu_mix_batch_copy(dev, vals, acts, anchors, nlays,
                                   n_univ, lmax, mod_gain,
                                   out, act_any, layers, total);
    if (rc == 0)
        return 0;
    /* 拷贝路径失败（设备级）→ 零拷贝再试一次; 仍失败 → 调用方同帧回退 CPU */
    return ce_gpu_mix_batch_zc(dev, vals, acts, anchors, nlays,
                               n_univ, lmax, mod_gain,
                               out, act_any, layers, total);
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
    if (dev->kwave == NULL && ce_gpu_build(dev) < 0)
        return -1;
    size_t nb = (size_t)ntask * sizeof(double);
    size_t ni = (size_t)ntask * sizeof(int);
    cl_mem bt = ce_gpu_cl_resident(dev, &dev->wt, &dev->wt_b,
                                   CL_MEM_READ_ONLY, nb, "buf t");
    if (bt == NULL)
        goto fail;
    cl_mem bs = ce_gpu_cl_resident(dev, &dev->ws, &dev->ws_b,
                                   CL_MEM_READ_ONLY, nb, "buf speed");
    if (bs == NULL)
        goto fail;
    cl_mem bp = ce_gpu_cl_resident(dev, &dev->wp, &dev->wp_b,
                                   CL_MEM_READ_ONLY, nb, "buf phase");
    if (bp == NULL)
        goto fail;
    cl_mem bl = ce_gpu_cl_resident(dev, &dev->wl, &dev->wl_b,
                                   CL_MEM_READ_ONLY, nb, "buf lo");
    if (bl == NULL)
        goto fail;
    cl_mem bh = ce_gpu_cl_resident(dev, &dev->wh, &dev->wh_b,
                                   CL_MEM_READ_ONLY, nb, "buf hi");
    if (bh == NULL)
        goto fail;
    cl_mem bf = ce_gpu_cl_resident(dev, &dev->wf, &dev->wf_b,
                                   CL_MEM_READ_ONLY, ni, "buf fn");
    if (bf == NULL)
        goto fail;
    cl_mem bo = ce_gpu_cl_resident(dev, &dev->wo, &dev->wo_b,
                                   CL_MEM_READ_ONLY, (size_t)(ntask + 1) *
                                   sizeof(int), "buf offsets");
    if (bo == NULL)
        goto fail;
    cl_mem bout = ce_gpu_cl_resident(dev, &dev->wout, &dev->wout_b,
                                     CL_MEM_WRITE_ONLY, total, "buf out");
    if (bout == NULL)
        goto fail;
    cl_int w;
    w = ce_gpu_cl_write(dev, bt, nb, t, "write t");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bs, nb, speed, "write speed");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bp, nb, phase_step, "write phase");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bl, nb, lo, "write lo");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bh, nb, hi, "write hi");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bf, ni, wave_fn, "write fn");
    if (w == CL_SUCCESS)
        w = ce_gpu_cl_write(dev, bo, (size_t)(ntask + 1) * sizeof(int),
                            offsets, "write offsets");
    if (w != CL_SUCCESS)
        goto fail;
    cl_kernel k = dev->kwave;
    cl_int e;
    e = clSetKernelArg(k, 0, sizeof(cl_mem), &bt);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 1, sizeof(cl_mem), &bs);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 2, sizeof(cl_mem), &bp);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 3, sizeof(cl_mem), &bl);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 4, sizeof(cl_mem), &bh);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 5, sizeof(cl_mem), &bf);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 6, sizeof(cl_mem), &bo);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 7, sizeof(cl_int), &ntask);
    if (e == CL_SUCCESS) e = clSetKernelArg(k, 8, sizeof(cl_mem), &bout);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clSetKernelArg wave", e);
        goto fail;
    }
    size_t gsize = total;
    e = clEnqueueNDRangeKernel(dev->queue, k, 1, NULL, &gsize, NULL, 0, NULL,
                               NULL);
    if (e != CL_SUCCESS) {
        ce_gpu_clerr(dev, "clEnqueueNDRangeKernel wave", e);
        goto fail;
    }
    e = clEnqueueReadBuffer(dev->queue, bout, CL_TRUE, 0, total, out, 0, NULL,
                            NULL);
    if (e != CL_SUCCESS)
        ce_gpu_clerr(dev, "clEnqueueReadBuffer wave out", e);
    return (e == CL_SUCCESS) ? 0 : -1;
fail:
    return -1;
}

CE_GPU_API const char* ce_gpu_last_error(ce_gpu_dev* dev)
{
    return (dev != NULL) ? dev->err : "";
}

/* ── OpenCL 后端便捷别名（ce_opencl_* 直连, 与 ce_gpu_* 等价）────────── */

CE_GPU_API ce_gpu_dev* ce_opencl_init(const char* device_hint)
{
    return ce_gpu_init(CE_GPU_BACKEND_OPENCL, device_hint);
}

CE_GPU_API void ce_opencl_destroy(ce_gpu_dev* dev)
{
    ce_gpu_destroy(dev);
}
