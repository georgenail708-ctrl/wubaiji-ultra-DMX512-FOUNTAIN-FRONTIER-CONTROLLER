# cython: boundscheck=False, wraparound=False, cdivision=True
import numpy as np
cimport numpy as cnp
from libc.math cimport fabs, isnan
from libc.stdint cimport uint32_t, uint8_t
from cython.parallel cimport prange
cimport cython


@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void _mix_channel(float[:, ::1] vals,
                              unsigned char[:, ::1] acts,
                              float[:, ::1] anchors,
                              float mod_gain,
                              Py_ssize_t c,
                              Py_ssize_t nlay,
                              unsigned char[::1] out,
                              unsigned char[::1] act_any) noexcept nogil:
    """单通道融合计算。所有累加器为本函数局部(线程私有)，无跨线程 reduction。"""
    cdef Py_ssize_t l, pidx = -1
    cdef float best_abs = -1.0, d, sum_delta = 0.0, primary_delta, others, res
    cdef float sum_active = 0.0, cnt_active = 0.0
    cdef int any_a = 0
    for l in range(nlay):
        if acts[l, c]:
            any_a = 1
            d = vals[l, c] - anchors[l, c]
            sum_active += vals[l, c]
            cnt_active += 1.0
        else:
            d = 0.0
        sum_delta += d
        if fabs(d) > best_abs:
            best_abs = fabs(d)
            pidx = l
    act_any[c] = <unsigned char>any_a
    if pidx >= 0 and acts[pidx, c]:
        primary_delta = vals[pidx, c] - anchors[pidx, c]
        others = (sum_delta - primary_delta) * mod_gain
        res = vals[pidx, c] + others
    elif any_a:
        res = sum_active / cnt_active if cnt_active > 0.0 else 0.0
    else:
        res = 0.0
    if res < 0.0:
        res = 0.0
    elif res > 255.0:
        res = 255.0
    if isnan(res):
        res = 0.0
    out[c] = <unsigned char>res  # 截断，与 numpy .astype(uint8) 一致


@cython.boundscheck(False)
@cython.wraparound(False)
cdef void _mix_core(float[:, ::1] vals,
                    unsigned char[:, ::1] acts,
                    float[:, ::1] anchors,
                    float mod_gain,
                    unsigned char[::1] out,
                    unsigned char[::1] act_any) noexcept nogil:
    """跨通道 prange 并行，全程 nogil。

    每个通道 c 只写 out[c]/act_any[c]，通道间无依赖 → prange 竞态安全。
    """
    cdef Py_ssize_t nlay = vals.shape[0], nch = vals.shape[1]
    cdef Py_ssize_t c
    for c in prange(nch, nogil=True):
        _mix_channel(vals, acts, anchors, mod_gain, c, nlay, out, act_any)


def mix_clips_kernel(cnp.ndarray[cnp.float32_t, ndim=2] vals,
                     cnp.ndarray[cnp.uint8_t, ndim=2] acts,
                     cnp.ndarray[cnp.float32_t, ndim=2] anchors,
                     float mod_gain):
    """融合版：一次 C 循环遍历 512 通道，等价 numpy 多趟。

    acts 用 uint8(0/1) 传入(bool 视图)。输入 ndarray 若非 C 连续则复制成连续再取
    memoryview。输出每次新分配(512B)——调用方跨 universe 累积后统一消费，复用会别名污染。
    """
    cdef Py_ssize_t nch = vals.shape[1]
    # memoryview 要求 C 连续；调用方几乎总是连续，非连续时 ascontiguousarray 兜底
    cdef cnp.ndarray[cnp.float32_t, ndim=2] vals_c = np.ascontiguousarray(vals)
    cdef cnp.ndarray[cnp.uint8_t, ndim=2] acts_c = np.ascontiguousarray(acts)
    cdef cnp.ndarray[cnp.float32_t, ndim=2] anchors_c = np.ascontiguousarray(anchors)

    cdef cnp.ndarray[cnp.uint8_t, ndim=1] out_arr = np.empty(nch, dtype=np.uint8)
    cdef cnp.ndarray[cnp.uint8_t, ndim=1] act_arr = np.empty(nch, dtype=np.uint8)

    _mix_core(vals_c, acts_c, anchors_c, mod_gain, out_arr, act_arr)
    return out_arr, act_arr
