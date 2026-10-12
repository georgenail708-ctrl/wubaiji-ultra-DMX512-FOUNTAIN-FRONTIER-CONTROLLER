# 第三方组件与许可说明

本仓库自有代码以 MIT 许可发布（见 `LICENSE`）。下列第三方内容按其原始许可使用，许可全文保留在 `licenses/` 目录或资源目录内。

| 组件 | 用途 | 许可 | 许可文件 |
|---|---|---|---|
| CPython v3.13.13（固定 commit `01104ce1…`） | 原生 CE 引擎中适配的精确整数算术、float/int 比较、类型求和与稳定排序算法 | PSF License Version 2 | `licenses/CPython-3.13.13-LICENSE.txt`、`licenses/CPython-R211-ADAPTATION-NOTICE.md`、`licenses/CPython-R214-ADAPTATION-NOTICE.md` |
| NumPy（固定 commit `70fa998…`） | 原生 CE 引擎中适配的自定义曲线冷 LUT 查找与插值语义 | BSD-3-Clause | `licenses/NumPy-70fa998-LICENSE.txt` |
| OpenCL 头文件（Khronos Group） | CE 引擎 OpenCL 后端编译所需头文件 | Apache-2.0 | `render_lab/csrc/third_party/opencl/CL/` 文件头 |
| Tabler Icons | 界面图标 | MIT | `src/ui/icon_assets/tabler/package/LICENSE` |
| Fluent Emoji | 界面图标 | MIT | `src/ui/icon_assets/fluent_emoji/LICENSE` |
| 美术资源（Met Open Access、PolyHaven、Kenney、Bootstrap Icons、Game-Icons、Hero-Patterns、Open Doodles） | 主题纹理/图案/绘画素材 | CC0 / 公有领域 / MIT（逐项） | `src/ui/art_assets/LICENSES.md` 与 `src/ui/art_assets/licenses/` |

运行依赖（PyQt6、NumPy、pandas、librosa、sounddevice、pygame 等）以 pip 分发，权利归各自作者，许可见各发行包。

适配说明：CE 引擎对 CPython / NumPy 算法的采用均为源码级适配（非逐字复制），实际采用范围与修改边界逐项记录于上述 adaptation notice 文件中。
