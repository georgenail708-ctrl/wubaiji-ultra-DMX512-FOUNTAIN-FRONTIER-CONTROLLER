# -*- coding: utf-8 -*-
"""构建渲染 C 内核扩展(.pyd/.so)。打包前运行一次:
    python src/engine/native/build_kernels.py build_ext --inplace

产物 _render_kernels.<abi>.pyd 与本文件同目录,playback_engine 惰性 import;
import 失败(未编译/无编译器)自动回退纯 numpy 路径,行为逐字节不变。
发行机无需编译器——.pyd 由开发机编好随包分发(同 numpy)。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _build():
    from setuptools import setup, Extension
    from Cython.Build import cythonize
    import numpy as np
    # OpenMP 供 prange 跨通道并行。MSVC 用 /openmp；GCC/Clang 用 -fopenmp。
    if sys.platform == "win32":
        omp_compile = ["/openmp"]
        omp_link = []
    else:
        omp_compile = ["-fopenmp"]
        omp_link = ["-fopenmp"]
    ext = Extension(
        "_render_kernels",
        [os.path.join(_HERE, "_render_kernels.pyx")],
        include_dirs=[np.get_include()],
        extra_compile_args=omp_compile,
        extra_link_args=omp_link,
    )
    # 让产物落在本目录
    os.chdir(_HERE)
    setup(
        name="qascade_render_kernels",
        ext_modules=cythonize([ext], language_level=3),
        script_args=sys.argv[1:] or ["build_ext", "--inplace"],
    )


if __name__ == "__main__":
    _build()
