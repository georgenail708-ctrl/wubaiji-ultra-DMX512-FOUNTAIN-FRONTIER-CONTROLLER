# -*- coding: utf-8 -*-
"""组装本机 CUDA 编译工具链（免管理员, 全部来自 pip wheel）。

背景: 开发机无管理员权限、装不了 CUDA Toolkit 安装器; 但 Windows 上 pip
提供两个 wheel, 其内容合起来 = clang 编译 CUDA 所需的全部件:

  nvidia-cuda-nvcc-cu12     → bin/ptxas.exe, nvvm/bin/nvvm64_*.dll,
                              nvvm/libdevice/libdevice.10.bc, include/crt/*.h
  nvidia-cuda-runtime-cu12  → include/cuda_runtime.h 等全套运行时头,
                              lib/x64/cudart_static.lib（静态, 目标机免 DLL）

clang（本机 LLVM 22, 自带 NVPTX 后端）用 --cuda-path 指向组装目录即可把
csrc/ce_gpu_cuda.cu 编成 ce_gpu_cuda.dll（-ffp-contract=off 等价 -fmad=false,
位级等价硬前提）。产物与 nvcc 编译结果同语义（同一份 ce_gpu_kernels.h 算术）。

组装位置: %LOCALAPPDATA%\\qascade_cuda_toolchain（可用 QASCADE_CUDA_TOOLCHAIN
覆盖）。幂等: 已存在则跳过（--force 重新组装）。

用法: venv\\Scripts\\python render_lab\\qascade_ce\\setup_cuda_toolchain.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

WHEELS = (("nvidia-cuda-nvcc-cu12", "*"),
          ("nvidia-cuda-runtime-cu12", "12.9.*"),   # 与 nvcc 同版本线
          ("nvidia-curand-cu12", "*"),              # clang wrapper 强制包含
                                                    # curand_mtgp32_kernel.h
          ("nvidia-cuda-cccl-cu12", "*"))           # libcu++（nv/target 等）


def _toolchain_dir() -> str:
    env = os.environ.get("QASCADE_CUDA_TOOLCHAIN")
    if env:
        return os.path.abspath(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "qascade_cuda_toolchain")


def _download(spec: str, dest: str) -> str:
    """pip download（优先清华镜像, 失败回落官方源）。返回本次新增的 wheel 路径。"""
    before = set(os.listdir(dest)) if os.path.isdir(dest) else set()
    for extra in (["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"], []):
        cmd = [sys.executable, "-m", "pip", "download", "--no-deps",
               *extra, "-d", dest, spec]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            break
    else:
        raise RuntimeError(f"下载失败 {spec}:\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
    after = set(os.listdir(dest))
    new = sorted(f for f in (after - before) if f.endswith(".whl"))
    if not new:
        new = sorted(f for f in after if f.endswith(".whl")
                     and f.split("-")[0].replace("_", "-") in spec.replace("_", "-"))
    if not new:
        raise RuntimeError(f"未找到 wheel: {dest}")
    return os.path.join(dest, new[-1])


def main() -> int:
    force = "--force" in sys.argv
    root = _toolchain_dir()
    marker = os.path.join(root, ".assembled")
    if os.path.isdir(root) and os.path.exists(marker) and not force:
        print(f"[CUDA-TC] 已存在, 跳过: {root}")
        return 0
    print(f"[CUDA-TC] 组装目标: {root}")
    tmp = tempfile.mkdtemp(prefix="qc_cuda_tc_")
    try:
        for pkg, _ver in WHEELS:
            whl = _download(pkg, tmp)
            print(f"[CUDA-TC] 解包 {os.path.basename(whl)}")
            with zipfile.ZipFile(whl) as z:
                z.extractall(os.path.join(tmp, "x"))
        src_nvcc = os.path.join(tmp, "x", "nvidia", "cuda_nvcc")
        src_rt = os.path.join(tmp, "x", "nvidia", "cuda_runtime")
        src_curand = os.path.join(tmp, "x", "nvidia", "curand", "include")
        if not os.path.isdir(src_nvcc) or not os.path.isdir(src_rt):
            raise RuntimeError("wheel 内容布局不符预期（nvidia/cuda_* 缺失）")
        if os.path.isdir(root):
            shutil.rmtree(root)
        os.makedirs(root)
        # bin/ ← ptxas（clang 调它做 PTX→cubin）
        os.makedirs(os.path.join(root, "bin"))
        for exe in ("ptxas.exe",):
            s = os.path.join(src_nvcc, "bin", exe)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(root, "bin", exe))
        # nvvm/ ← libdevice.bc + nvvm64 dll
        shutil.copytree(os.path.join(src_nvcc, "nvvm"),
                        os.path.join(root, "nvvm"))
        # include/ ← 运行时头 + crt/ 设备侧头（cuda_runtime.h 依赖 crt/*）
        inc = os.path.join(root, "include")
        shutil.copytree(os.path.join(src_rt, "include"), inc)
        crt_src = os.path.join(src_nvcc, "include", "crt")
        if os.path.isdir(crt_src):
            shutil.copytree(crt_src, os.path.join(inc, "crt"))
        for extra in ("fatbinary_section.h", "nvPTXCompiler.h"):
            s = os.path.join(src_nvcc, "include", extra)
            if os.path.exists(s):
                shutil.copy2(s, inc)
        # curand 头（clang __clang_cuda_runtime_wrapper.h 无条件包含
        # curand_mtgp32_kernel.h; 内核本身不用 curand, 仅为满足编译）
        if os.path.isdir(src_curand):
            for f in os.listdir(src_curand):
                if f.endswith(".h"):
                    shutil.copy2(os.path.join(src_curand, f), inc)
        # CCCL / libcu++: cuda/std/ + nv/ 目录（nv/target 等头; clang 的
        # curand 头链会拉进来）
        src_cccl = os.path.join(tmp, "x", "nvidia", "cuda_cccl", "include")
        if os.path.isdir(src_cccl):
            for entry in os.listdir(src_cccl):
                s = os.path.join(src_cccl, entry)
                d = os.path.join(inc, entry)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
        # lib/x64/ ← cudart_static.lib（静态链接, 目标机零依赖）
        libdir = os.path.join(root, "lib", "x64")
        os.makedirs(libdir)
        for lib in ("cudart_static.lib",):
            s = os.path.join(src_rt, "lib", "x64", lib)
            if os.path.exists(s):
                shutil.copy2(s, libdir)
        # 校验关键件
        need = [os.path.join(root, "bin", "ptxas.exe"),
                os.path.join(root, "nvvm", "libdevice", "libdevice.10.bc"),
                os.path.join(inc, "cuda_runtime.h"),
                os.path.join(libdir, "cudart_static.lib")]
        missing = [p for p in need if not os.path.exists(p)]
        if missing:
            raise RuntimeError("组装缺件: %s" % missing)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("assembled from pip wheels\n")
        print(f"[CUDA-TC] 完成: {root}")
        print(f"[CUDA-TC]   ptxas      = {os.path.join(root, 'bin', 'ptxas.exe')}")
        print(f"[CUDA-TC]   libdevice  = nvvm/libdevice/libdevice.10.bc")
        print(f"[CUDA-TC]   headers    = include/cuda_runtime.h")
        print(f"[CUDA-TC]   cudart     = lib/x64/cudart_static.lib")
        return 0
    except Exception as e:
        print(f"[CUDA-TC] FAIL: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
