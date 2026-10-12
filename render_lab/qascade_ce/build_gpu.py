"""Qascade CE GPU 后端构建脚本 — stub(本机语义验证) / cuda(工程机) / opencl。

用法: venv\\Scripts\\python render_lab\\qascade_ce\\build_gpu.py [all|stub|cuda|opencl]

目标:
  stub    ce_gpu_cpu_stub.dll  MSVC 串行桩（本机必跑: 语义位级对拍前提,
                                探针 bench/ce_probe_gpu_equiv.py 用它）
  cuda    ce_gpu_cuda.dll      两条路径自动选择:
                                ① nvcc 存在（装了 CUDA Toolkit）→ Runtime API
                                   路径, 编 csrc/ce_gpu_cuda.cu
                                ② 无 nvcc（仅开发机/CI）→ clang 交叉编译路径:
                                   clang --cuda-device-only 编 csrc/ce_gpu_cuda_dev.cu
                                   → cubin 生成 C 数组头 → MSVC 编
                                   csrc/ce_gpu_cuda_host.c（Driver API, 无 cudart
                                   依赖, 运行期动态加载 nvcuda.dll）
                                两条路径产物同契约（ce_gpu_api.h 11 符号）且
                                位级同源（同一份 ce_gpu_kernels.h + 禁 FMA 收缩）
  opencl  ce_gpu_opencl.dll    MSVC + 随附 Khronos OpenCL 头
                                （csrc/third_party/opencl/CL/）

环境变量:
  CUDA_ARCH         nvcc -arch 值（默认 sm_89 cubin + compute_89 PTX）
  CUDA_BIN          nvcc.exe 所在目录（默认自动探测 CUDA_PATH）
  QASCADE_CLANG     clang++ 路径（clang 路径用; 默认自动探测）
  QASCADE_CUDA_TOOLCHAIN  clang 路径的 CUDA 件目录（默认 %LOCALAPPDATA%\\
                    qascade_cuda_toolchain, 缺失时自动由 pip wheel 组装）
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

from build import _find_msvc, _find_sdk, _find_vs  # noqa: E402 同目录复用

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRC = os.path.join(ROOT, "csrc")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_msvc() -> tuple[str, str, str, str]:
    """组装 (cl, msvc_dir, sdk_ver, kits_root) — 同 build.py 定位逻辑。"""
    vs = _find_vs()
    msvc = _find_msvc(vs)
    sdk_ver, kits_root = _find_sdk()
    cl = os.path.join(msvc, "bin", "Hostx64", "x64", "cl.exe")
    if not os.path.exists(cl):
        raise RuntimeError(f"cl.exe 不存在: {cl}")
    return cl, msvc, sdk_ver, kits_root


def _env_for_cl(cl: str, msvc: str, sdk_ver: str, kits_root: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(cl) + os.pathsep + env.get("PATH", "")
    env["INCLUDE"] = ";".join([
        os.path.join(msvc, "include"),
        os.path.join(kits_root, "Include", sdk_ver, "ucrt"),
        os.path.join(kits_root, "Include", sdk_ver, "shared"),
        os.path.join(kits_root, "Include", sdk_ver, "um"),
    ])
    env["LIB"] = ";".join([
        os.path.join(msvc, "lib", "x64"),
        os.path.join(kits_root, "Lib", sdk_ver, "ucrt", "x64"),
        os.path.join(kits_root, "Lib", sdk_ver, "um", "x64"),
    ])
    return env


def build_stub(cl: str, msvc: str, sdk_ver: str, kits_root: str) -> str:
    dll_path = os.path.join(OUT_DIR, "ce_gpu_cpu_stub.dll")
    cmd = [cl, "/nologo", "/utf-8", "/LD", "/O2", "/fp:precise",
           "/Fe:" + dll_path,
           os.path.join(CSRC, "ce_gpu_cpu_stub.c")]
    subprocess.run(cmd, check=True, env=_env_for_cl(cl, msvc, sdk_ver, kits_root),
                   text=True)
    if not os.path.exists(dll_path):
        raise RuntimeError("stub 编译失败: ce_gpu_cpu_stub.dll 未生成")
    return dll_path


def _find_nvcc() -> str | None:
    nvcc = shutil.which("nvcc")
    if nvcc:
        return nvcc
    cu_path = os.environ.get("CUDA_PATH", "")
    if cu_path:
        cand = os.path.join(cu_path, "bin", "nvcc.exe")
        if os.path.exists(cand):
            return cand
    cands = glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\*\bin\nvcc.exe")
    return cands[-1] if cands else None


def _make_opencl_import_lib(cl: str, out_dir: str) -> str | None:
    """由 System32\\OpenCL.dll（ICD loader）生成 OpenCL.lib（构建期工件）。

    Windows 不随 OpenCL SDK 发布导入库; loader 按名导出全部 cl* 符号,
    故 dumpbin 提取符号表 → .def → lib.exe 生成导入库, 与 SDK 版等价。
    """
    bin_dir = os.path.dirname(cl)
    dumpbin = os.path.join(bin_dir, "dumpbin.exe")
    libexe = os.path.join(bin_dir, "lib.exe")
    opencl_dll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "OpenCL.dll")
    if not (os.path.exists(dumpbin) and os.path.exists(libexe) and
            os.path.exists(opencl_dll)):
        print("[GPU] OpenCL.dll / dumpbin 缺失 — opencl 目标跳过")
        return None
    out = subprocess.run([dumpbin, "/exports", opencl_dll],
                         check=True, capture_output=True, text=True).stdout
    syms = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].startswith("cl"):
            syms.append(parts[3])
    if not syms:
        print("[GPU] OpenCL.dll 无 cl* 导出 — opencl 目标跳过")
        return None
    def_path = os.path.join(out_dir, "opencl.def")
    with open(def_path, "w", encoding="utf-8") as f:
        f.write("LIBRARY OpenCL\nEXPORTS\n")
        for s in syms:
            f.write(f"    {s}\n")
    lib_path = os.path.join(out_dir, "OpenCL.lib")
    subprocess.run([libexe, "/nologo", f"/def:{def_path}",
                    f"/out:{lib_path}", "/machine:x64"],
                   check=True, capture_output=True, text=True)
    return lib_path


def build_cuda() -> str | None:
    """CUDA DLL 构建。

    路径优先级（2026-09-18 起）:
      ① clang 路径（默认; 本机已验证可复现, 免 Toolkit/免管理员）——
         clang --cuda-device-only 编 cubin + MSVC 编 Driver API host
      ② nvcc 路径（QASCADE_CUDA_USE_NVCC=1 或 clang 不可用时）——
         Runtime API, 需 CUDA Toolkit
    两路径产物同契约（ce_gpu_api.h 11 符号）且位级同源（同一份 ce_gpu_kernels.h）。
    """
    use_nvcc = (os.environ.get("QASCADE_CUDA_USE_NVCC", "0") == "1")
    if use_nvcc:
        nvcc = _find_nvcc()
        if nvcc is not None:
            return _build_cuda_nvcc(nvcc)
        print("[GPU] QASCADE_CUDA_USE_NVCC=1 但 nvcc 未找到 → 回落 clang 路径")
    clang = _find_clang()
    if clang is not None:
        return _build_cuda_clang()
    nvcc = _find_nvcc()
    if nvcc is not None:
        return _build_cuda_nvcc(nvcc)
    print("[GPU] clang 与 nvcc 均未找到 — cuda 目标跳过")
    return None


def _build_cuda_nvcc(nvcc: str) -> str | None:
    # 默认面向正式使用机 RTX 4080（Ada = sm_89）:
    #   sm_89 cubin（Ada 原生最优）+ compute_89 PTX（前向 JIT 兜底新卡）。
    # 显式设置 CUDA_ARCH 时退化为单一 -arch（兼容旧用法, 如 sm_80/11.8 工具链）。
    env_arch = (os.environ.get("CUDA_ARCH", "") or "").strip()
    if env_arch:
        arch_args = ["-arch=" + env_arch]
        arch_desc = env_arch
    else:
        arch_args = ["-gencode", "arch=compute_89,code=sm_89",
                     "-gencode", "arch=compute_89,code=compute_89"]
        arch_desc = "sm_89 cubin + compute_89 PTX"
    dll_path = os.path.join(OUT_DIR, "ce_gpu_cuda.dll")
    cmd = [nvcc, "-O3", "-fmad=false", *arch_args, "-cudart=static",
           "--shared", "-Xcompiler", "/O2,/utf-8",
           f"-o{dll_path}",
           os.path.join(CSRC, "ce_gpu_cuda.cu")]
    print(f"[GPU] nvcc: {nvcc}")
    print(f"[GPU] 架构: {arch_desc}")
    subprocess.run(cmd, check=True, text=True)
    if not os.path.exists(dll_path):
        raise RuntimeError("cuda 编译失败: ce_gpu_cuda.dll 未生成")
    return dll_path


# ── clang + CUDA Driver API 路径（无 CUDA Toolkit 的开发机）─────────────
# 原理: clang 自带 NVPTX 后端, 用 pip wheel 组装的件（ptxas/libdevice/头）
# 即可编译设备端 → cubin; host 用 MSVC 编 Driver API 版（动态加载 nvcuda.dll,
# 无 cudart 依赖）。产物契约与 nvcc 路径完全一致（ce_gpu_api.h 11 符号）。

def _find_clang() -> str | None:
    env = os.environ.get("QASCADE_CLANG", "")
    if env and os.path.exists(env):
        return env
    for name in ("clang++.exe", "clang++"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (r"C:\Program Files\LLVM\bin\clang++.exe",
                 r"C:\Program Files (x86)\LLVM\bin\clang++.exe"):
        if os.path.exists(cand):
            return cand
    return None


def _cuda_toolchain_dir() -> str:
    env = os.environ.get("QASCADE_CUDA_TOOLCHAIN", "")
    if env and os.path.isdir(env):
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "qascade_cuda_toolchain")


def _ensure_cuda_toolchain() -> str | None:
    """确保 clang 用 CUDA 件就位（ptxas/libdevice/头）; 缺则自动组装。"""
    tc = _cuda_toolchain_dir()
    need = (os.path.join(tc, "bin", "ptxas.exe"),
            os.path.join(tc, "nvvm", "libdevice", "libdevice.10.bc"),
            os.path.join(tc, "include", "cuda_runtime.h"))
    if all(os.path.exists(p) for p in need):
        return tc
    print("[GPU] 本机 CUDA 件缺失 → 自动组装（pip wheel, 免管理员）")
    setup = os.path.join(OUT_DIR, "setup_cuda_toolchain.py")
    r = subprocess.run([sys.executable, setup], text=True)
    if r.returncode != 0 or not all(os.path.exists(p) for p in need):
        return None
    return tc


def _build_cuda_clang() -> str | None:
    """clang 编设备端 cubin → 嵌入 C 头 → MSVC 编 host（Driver API）。"""
    clang = _find_clang()
    if clang is None:
        print("[GPU] nvcc 与 clang 均未找到 — cuda 目标跳过")
        return None
    tc = _ensure_cuda_toolchain()
    if tc is None:
        print("[GPU] CUDA 件组装失败 — cuda 目标跳过")
        return None
    arch = (os.environ.get("CUDA_ARCH", "") or "sm_89").strip()
    if not arch.startswith("sm_"):
        arch = "sm_89"
    dev_src = os.path.join(CSRC, "ce_gpu_cuda_dev.cu")
    host_src = os.path.join(CSRC, "ce_gpu_cuda_host.c")
    bin_hdr = os.path.join(CSRC, "ce_gpu_cuda_kernel_bin.h")
    cubin = os.path.join(OUT_DIR, "_ce_gpu_kernel.cubin")
    print(f"[GPU] clang: {clang}")
    print(f"[GPU] 架构: {arch}（cubin 直嵌, CUDA Driver API）")
    cmd = [clang, "-x", "cuda", "--cuda-path=" + tc,
           f"--cuda-gpu-arch={arch}", "-O3",
           "-ffp-contract=off",          # 位级硬前提（等价 -fmad=false）
           "-Xcuda-ptxas", "-O3",
           "--cuda-device-only", "-c",
           "-o", cubin, dev_src]
    _run_checked(cmd, "clang 设备端编译")
    if not os.path.exists(cubin):
        print("[GPU] cubin 未生成 — cuda 目标跳过")
        return None
    # cubin → C 数组头（读入后删除, 不留中间物）
    with open(cubin, "rb") as fh:
        blob = fh.read()
    try:
        os.remove(cubin)
    except OSError:
        pass
    lines = ["/* 自动生成（build_gpu.py clang 路径）— 勿手改。",
             " * 源: csrc/ce_gpu_cuda_dev.cu → clang --cuda-device-only -cubin",
             " */",
             "#ifndef CE_GPU_CUDA_KERNEL_BIN_H",
             "#define CE_GPU_CUDA_KERNEL_BIN_H",
             "",
             "static const unsigned char CE_GPU_CUDA_CUBIN[] = {"]
    for i in range(0, len(blob), 12):
        chunk = blob[i:i + 12]
        lines.append("    " + " ".join("0x%02x," % b for b in chunk))
    lines += ["};", "",
              "static const unsigned long CE_GPU_CUDA_CUBIN_LEN = %dul;"
              % len(blob), "",
              "#endif /* CE_GPU_CUDA_KERNEL_BIN_H */", ""]
    with open(bin_hdr, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"[GPU] cubin {len(blob)} B → {os.path.basename(bin_hdr)}")

    # host: MSVC 编（复用 build.py 的 MSVC/SDK 定位）
    cl, msvc, sdk_ver, kits_root = _env_msvc()
    dll_path = os.path.join(OUT_DIR, "ce_gpu_cuda.dll")
    env = _env_for_cl(cl, msvc, sdk_ver, kits_root)
    env["INCLUDE"] += os.pathsep + os.path.join(CSRC, "third_party", "opencl")
    # 中间物落临时目录（避免污染仓库根: cl.exe 默认把 .obj 放 CWD）
    host_obj = os.path.join(OUT_DIR, "_ce_gpu_cuda_host.obj")
    cmd = [cl, "/nologo", "/utf-8", "/LD", "/O2", "/fp:precise",
           "/Fo:" + host_obj,
           "/Fe:" + dll_path, host_src,
           "/link", "Advapi32.lib"]
    try:
        _run_checked(cmd, "MSVC host 编译", env=env)
    finally:
        for tmpf in (host_obj, os.path.join(OUT_DIR, "_ce_gpu_cuda_host.exp"),
                     os.path.join(OUT_DIR, "_ce_gpu_cuda_host.lib")):
            try:
                if os.path.exists(tmpf):
                    os.remove(tmpf)
            except OSError:
                pass
    if not os.path.exists(dll_path):
        raise RuntimeError("cuda 编译失败: ce_gpu_cuda.dll 未生成")
    return dll_path


def _run_checked(cmd: list, what: str, env: dict | None = None) -> None:
    r = subprocess.run(cmd, text=True, env=env,
                       capture_output=True)
    if r.returncode != 0:
        print(f"[GPU] {what} 失败（rc={r.returncode}）")
        tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
        print(tail)
        raise RuntimeError(f"{what} 失败")


def build_opencl(cl: str, msvc: str, sdk_ver: str, kits_root: str) -> str:
    # P1.5 堵漏: 先生成内核源码密文头（与 build.py build_gpu 同一生成器）
    enc_tool = os.path.join(ROOT, "..", "tools", "ce_gpu_encrypt_src.py")
    subprocess.run([sys.executable, enc_tool], check=True, text=True)
    dll_path = os.path.join(OUT_DIR, "ce_gpu_opencl.dll")
    ocl_lib = _make_opencl_import_lib(cl, OUT_DIR)
    if ocl_lib is None:
        raise RuntimeError("opencl 目标失败: 无法生成 OpenCL.lib")
    env = _env_for_cl(cl, msvc, sdk_ver, kits_root)
    env["INCLUDE"] += os.pathsep + os.path.join(CSRC, "third_party", "opencl")
    env["LIB"] += os.pathsep + OUT_DIR
    cmd = [cl, "/nologo", "/utf-8", "/LD", "/O2", "/fp:precise",
           "/Fe:" + dll_path,
           os.path.join(CSRC, "ce_gpu_opencl.c"), "OpenCL.lib"]
    subprocess.run(cmd, check=True, env=env, text=True)
    if not os.path.exists(dll_path):
        raise RuntimeError("opencl 编译失败: ce_gpu_opencl.dll 未生成")
    return dll_path


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in ("all", "stub", "cuda", "opencl"):
        raise SystemExit("用法: build_gpu.py [all|stub|cuda|opencl]")
    cl, msvc, sdk_ver, kits_root = _env_msvc()
    print(f"[GPU] MSVC: {msvc}")
    if target in ("all", "stub"):
        p = build_stub(cl, msvc, sdk_ver, kits_root)
        print(f"[GPU] 已生成 {os.path.basename(p)} ({os.path.getsize(p)} B)")
    if target in ("all", "cuda"):
        p = build_cuda()
        if p:
            print(f"[GPU] 已生成 {os.path.basename(p)} ({os.path.getsize(p)} B)")
    if target in ("all", "opencl"):
        p = build_opencl(cl, msvc, sdk_ver, kits_root)
        print(f"[GPU] 已生成 {os.path.basename(p)} ({os.path.getsize(p)} B)")
    print("[GPU] 构建完成")


if __name__ == "__main__":
    main()
