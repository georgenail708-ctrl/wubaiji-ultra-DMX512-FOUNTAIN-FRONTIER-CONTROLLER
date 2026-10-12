"""Qascade CE 构建脚本 — MSVC 双变体 DLL 编译（手动组装环境，不依赖 vcvars）。

用法: venv\\Scripts\\python render_lab\\qascade_ce\\build.py [all|avx2|sse2]

变体:
  ce_engine_avx2.dll   /O2 /arch:AVX2 /openmp /fp:precise   (现代 CPU, 默认)
  ce_engine_sse2.dll   /O2 /arch:SSE2 /openmp /fp:precise   (无 AVX2 机器兜底)

vswhere 定位 VS 安装 → 直接定位 cl.exe 全路径 + Windows SDK，
手动组装 INCLUDE/LIB/PATH 环境变量（本机 vcvars64.bat 异常失效，故绕开）。
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSRC = os.path.join(ROOT, "csrc", "ce_engine.c")
CE_POOL_SRC = os.path.join(ROOT, "csrc", "ce_pool.c")   # 阶段B condvar 池
GPUSRC = os.path.join(ROOT, "csrc", "ce_gpu_opencl.c")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

VARIANTS = {
    "avx2": ["/O2", "/arch:AVX2", "/openmp", "/fp:precise", "/GL-", "-DCE_AVX2"],
    "sse2": ["/O2", "/arch:SSE2", "/openmp", "/fp:precise", "/GL-"],
}

# GPU 后端（ce_gpu_opencl.dll）: 动态加载 OpenCL.dll, 无需 /openmp 与 -DCE_AVX2
GPU_FLAGS = ["/O2", "/fp:precise", "/GL-"]

VSWHERE = (
    r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
)


def _find_vs() -> str:
    if not os.path.exists(VSWHERE):
        raise RuntimeError(f"vswhere 不存在: {VSWHERE}")
    out = subprocess.check_output([
        VSWHERE, "-latest", "-products", "*",
        "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property", "installationPath",
    ], text=True).strip()
    if not out:
        raise RuntimeError("未找到 VS（缺少 VC.Tools.x86.x64 组件）")
    return out


def _find_msvc(vs: str) -> str:
    """定位最新 MSVC 工具链版本目录（含 cl.exe）。"""
    cands = sorted(
        glob.glob(os.path.join(vs, "VC", "Tools", "MSVC", "*")),
        key=lambda p: tuple(int(x) for x in re.findall(r"\d+", os.path.basename(p))),
    )
    if not cands:
        raise RuntimeError(f"VC/Tools/MSVC 为空: {vs}")
    return cands[-1]


def _find_sdk() -> str:
    """定位最新 Windows SDK 版本号（用 Include 版本目录探测，返回 (版本, Kits10根)）。"""
    kits_root = r"C:\Program Files (x86)\Windows Kits\10"
    cands = sorted(
        glob.glob(os.path.join(kits_root, "Include", "*")),
        key=lambda p: tuple(int(x) for x in re.findall(r"\d+", os.path.basename(p))),
    )
    if not cands:
        raise RuntimeError(f"Windows SDK Include 为空: {kits_root}")
    return os.path.basename(cands[-1]), kits_root


def _build_env(cl: str, msvc: str, sdk_ver: str, kits_root: str) -> dict:
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


def build_source(src: str, dll_path: str, flags: list, cl: str, msvc: str,
                 sdk_ver: str, kits_root: str) -> str:
    env = _build_env(cl, msvc, sdk_ver, kits_root)
    srcs = src if isinstance(src, list) else [src]
    # P2a 导出名混淆：/FI 强制包含改名头（声明+内部引用一致重命名；
    # Python 侧由 qascade_ce/ce_api_map.py 翻译回不透明名）
    cmd = ([cl, "/nologo", "/utf-8", "/LD", "/Fe:" + dll_path,
            "/FI" + os.path.join(ROOT, "csrc", "ce_export_renames.h")]
           + flags + srcs)
    subprocess.run(cmd, check=True, env=env, text=True)
    if not os.path.exists(dll_path):
        raise RuntimeError(f"编译失败: {os.path.basename(dll_path)} 未生成")
    return dll_path


def build_variant(variant: str, cl: str, msvc: str, sdk_ver: str, kits_root: str) -> str:
    dll_name = f"ce_engine_{variant}.dll"
    return build_source([CSRC, CE_POOL_SRC], os.path.join(OUT_DIR, dll_name),
                        VARIANTS[variant], cl, msvc, sdk_ver, kits_root)


def _make_opencl_import_lib(cl: str, out_dir: str) -> str | None:
    """由 System32\\OpenCL.dll（ICD loader）生成 OpenCL.lib（构建期工件）。

    Windows 不随 OpenCL SDK 发布导入库; loader 按名导出全部 cl* 符号,
    故 dumpbin 提取符号表 → .def → lib.exe 生成导入库, 与 SDK 版等价
    （与 build_gpu.py 的 _make_opencl_import_lib 同款）。
    """
    bin_dir = os.path.dirname(cl)
    dumpbin = os.path.join(bin_dir, "dumpbin.exe")
    libexe = os.path.join(bin_dir, "lib.exe")
    opencl_dll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "OpenCL.dll")
    if not (os.path.exists(dumpbin) and os.path.exists(libexe)
            and os.path.exists(opencl_dll)):
        print("[CE] OpenCL.dll / dumpbin 缺失 — opencl 目标跳过")
        return None
    out = subprocess.run([dumpbin, "/exports", opencl_dll],
                         check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    syms = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].startswith("cl"):
            syms.append(parts[3])
    if not syms:
        print("[CE] OpenCL.dll 无 cl* 导出 — opencl 目标跳过")
        return None
    def_path = os.path.join(out_dir, "opencl.def")
    with open(def_path, "w", encoding="utf-8") as f:
        f.write("LIBRARY OpenCL\nEXPORTS\n")
        for s in syms:
            f.write(f"    {s}\n")
    lib_path = os.path.join(out_dir, "OpenCL.lib")
    subprocess.run([libexe, "/nologo", f"/def:{def_path}",
                    f"/out:{lib_path}", "/machine:x64"],
                   check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return lib_path


def build_gpu(cl: str, msvc: str, sdk_ver: str, kits_root: str) -> str:
    """GPU 后端 DLL: 运行时动态加载 OpenCL.dll（ICD, 系统目录自带）——
    AMD/NVIDIA/Intel 驱动均安装; 链接用 dumpbin 现场生成的 OpenCL.lib
    （System32\\OpenCL.dll 按名导出 cl* 符号）。"""
    # P1.5 堵漏: 先生成内核源码密文头（此前明文源码随 DLL 内嵌 122 行,
    # strings 直读即泄; 明文头 ce_gpu_opencl_src.h 只存构建机）
    enc_tool = os.path.join(ROOT, "..", "tools", "ce_gpu_encrypt_src.py")
    subprocess.run([sys.executable, enc_tool], check=True, text=True, encoding="utf-8", errors="replace")
    ocl_lib = _make_opencl_import_lib(cl, OUT_DIR)
    if ocl_lib is None:
        raise RuntimeError("opencl 目标失败: 无法生成 OpenCL.lib")
    env = _build_env(cl, msvc, sdk_ver, kits_root)
    env["INCLUDE"] += os.pathsep + os.path.join(ROOT, "csrc", "third_party",
                                                "opencl")
    env["LIB"] += os.pathsep + OUT_DIR
    dll_path = os.path.join(OUT_DIR, "ce_gpu_opencl.dll")
    cmd = ([cl, "/nologo", "/utf-8", "/LD", "/Fe:" + dll_path]
           + GPU_FLAGS + [GPUSRC, "OpenCL.lib"])
    subprocess.run(cmd, check=True, env=env, text=True)
    if not os.path.exists(dll_path):
        raise RuntimeError(f"编译失败: {os.path.basename(dll_path)} 未生成")
    return dll_path


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "all"
    if variant not in ("all", "avx2", "sse2", "gpu"):
        raise SystemExit("用法: build.py [all|avx2|sse2|gpu]")
    # P2a: 确保导出名混淆工件存在（静态入库物；缺失才生成）
    subprocess.run([sys.executable,
                    os.path.join(ROOT, "..", "tools", "ce_export_rename.py")],
                   check=True, text=True)
    vs = _find_vs()
    msvc = _find_msvc(vs)
    sdk_ver, kits_root = _find_sdk()
    cl = os.path.join(msvc, "bin", "Hostx64", "x64", "cl.exe")
    if not os.path.exists(cl):
        raise RuntimeError(f"cl.exe 不存在: {cl}")
    print(f"[CE] MSVC: {msvc}")
    print(f"[CE] SDK : {sdk_ver} ({kits_root})")
    if variant == "gpu":
        path = build_gpu(cl, msvc, sdk_ver, kits_root)
        print(f"[CE] 已生成 {os.path.basename(path)} ({os.path.getsize(path)} B)")
        print("[CE] 构建完成")
        return
    targets = ["avx2", "sse2"] if variant == "all" else [variant]
    for v in targets:
        path = build_variant(v, cl, msvc, sdk_ver, kits_root)
        print(f"[CE] 已生成 {os.path.basename(path)} ({os.path.getsize(path)} B)")
    if variant == "all":
        path = build_gpu(cl, msvc, sdk_ver, kits_root)
        print(f"[CE] 已生成 {os.path.basename(path)} ({os.path.getsize(path)} B)")
    print("[CE] 构建完成")


if __name__ == "__main__":
    main()
