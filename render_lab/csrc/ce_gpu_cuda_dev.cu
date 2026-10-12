/*
 * Qascade CE — CUDA 设备端内核（clang 交叉编译路径）。
 *
 * 与 ce_gpu_cuda.cu 的差异: 本文件只含 __global__ 内核（设备端）, host 侧
 * 在 ce_gpu_cuda_host.c（CUDA Driver API）。分离的原因: 开发机无 CUDA
 * Toolkit（无 fatbinary 工具）, clang 无法完成 host+device 一体化链接;
 * 但 clang --cuda-device-only 可生成 cubin, 由 Driver API 在运行时
 * cuModuleLoadData 加载 —— 官方支持的等价加载路径, 且免 cudart 依赖
 * （DLL 只依赖驱动自带 nvcuda.dll）。
 *
 * 位级等价（与 ce_gpu_cuda.cu/nvcc 路径完全一致）:
 *   - 内核算术 100% 来自 ce_gpu_kernels.h（与 CPU 桩/OpenCL 同源）
 *   - 禁 fma 收缩: 编译期 -ffp-contract=off（等价 -fmad=false）
 *   - 索引映射与 ce_gpu_cuda.cu 逐字一致（逐位对拍契约不变）
 *
 * 构建: tools/build_cuda_clang.py（clang --cuda-device-only → cubin →
 * 生成 C 数组头 → MSVC 编 host → ce_gpu_cuda.dll）
 */
#include "ce_gpu_kernels.h"

#include <cuda_runtime.h>
#include <stddef.h>

extern "C" {

/* 内核 1: 批量帧合成 — 每 work-item 单通道, 通道内 nlay 串行（K0 契约） */
__global__ void ce_gpu_kernel_mix_batch(
    const float* vals, const uint8_t* acts,
    const float* anchors, const int* nlays,
    int n_univ, int lmax, float mod_gain,
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
    const double* t, const double* speed,
    const double* phase_step, const double* lo,
    const double* hi, const int* wave_fn,
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

}  /* extern "C" */
