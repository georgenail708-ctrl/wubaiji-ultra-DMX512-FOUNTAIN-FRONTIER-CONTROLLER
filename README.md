# 雾白纪 ULTRA（Wubaiji Ultra）

**雾白纪 ULTRA 是一款完全免费、非营利的开源 DMX512 灯光控制台（Windows / PyQt6）。**

## 为什么不一样

**全录制式编曲**
像录音乐一样"录"灯光——水型与灯光编排全部通过实时录制完成，而不是逐帧手填数据，所见即所得。

**原生摇杆录制**
开箱即用的摇杆接入，直接推杆录制喷泉摆动、俯仰与旋转，实时手感直达时间线，是喷泉编曲的天然工作流。

**幅度控制模式 + Effect 预设库**
把常用动作做成 Effect 预设，配合幅度控制模式，随时制作、保存、调用——一个水型全场合复用，按幅度自由缩放。

**面向喷泉编排的行业领先表现**
水泵、气爆、跑泉、一维/二维摇摆、旋转等 fountain 设备深度适配；录制、预设、幅度三件套带来前沿的编排效率与舞台表现力。

> **公益声明**：本项目是完全免费的公益项目，不以任何形式收费或营利。
> 本项目的全部代码均由 AI 生成，按 MIT 许可证开放源代码，供任何人自由使用、学习与修改。
> 软件按"现状"（AS IS）提供，**不提供任何形式的担保，作者不对使用本软件造成的任何直接或间接损失承担责任**。用于真实演出或驱动真实灯光设备前，请自行充分测试并评估风险。

---

## 功能特性

- **录制式编排**：实时录制驱动全部编排工作流，支持摇杆设备直录
- **喷泉水型系统**：幅度控制模式 + Effect 预设库，水型制作/保存/调用一体化
- **编程器（Programmer）**：通道级控台操作、分组、调色（色轮/基色/色纸）、效果与跑灯
- **时间线**：多轨时间线节目编辑、增量烘焙、帧缓存与撤销/重做
- **效果引擎**：波浪/扫描/渐变等效果族、相位与方向编排、效果总速度控制
- **音频联动**：实时音频分析（频段/能量/频谱质心）驱动灯光调制
- **TopView 俯视布局**：2D 舞台布局、设备位置与朝向编辑、编组与选择同步
- **Clip Studio**：效果卡片画布与复合过渡创作
- **网络输出**：Art-Net 与 sACN（ANSI E1.31）双协议、多 Universe、严格单播模式
- **配接表导入**：CSV 配接表导入、XY 云台自动配对、组合设备
- **原生计算引擎（CE）**：纯 C 实现的数学内核（混合、烘焙、样条、排序、音频特征等），AVX2/SSE2 双指令集，未编译时自动回退纯 Python 路径

## 快速开始

要求：Windows 10/11，Python 3.13。

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 原生计算引擎（可选）

CE 引擎为可选加速组件：仓库内置已编译的 `render_lab/qascade_ce/ce_engine_avx2.dll` / `ce_engine_sse2.dll`（C 源码见 `render_lab/csrc/`）。DLL 缺失时程序自动使用等价的纯 Python 路径，功能不受影响。

## 演示项目与手册

| 文件 | 说明 |
|---|---|
| `demo/喷泉演示项目.dmxt` | **演示项目**（约 91MB，含完整录制数据）。**免费使用与修改**——可直接打开、改编、用于你自己的演出。 |
| `docs/用户手册-旧版.html` | **旧版用户手册（已过期，待更新）**：对应早期版本，界面与功能以当前代码为准。 |
| [演示视频（B站）](https://b23.tv/gGfbogh) | 软件演示视频在作者 B 站账号，欢迎观看学习。 |

## 项目结构

```
main.py                 程序入口
launcher.py             启动器
src/core                数据模型与常量
src/engine              播放引擎：烘焙、时间线、效果、音频调制
src/network             Art-Net / sACN 输出与网络统计
src/processing          派发、场景页、跑灯状态机
src/ui                  全部界面面板与主题
render_lab/qascade_ce   原生 CE 引擎（ctypes 绑定 + DLL 加载）
render_lab/csrc         CE 引擎 C 源码
licenses/               第三方算法采用许可（CPython / NumPy 等）
demo/                   演示项目（免费使用与修改）
docs/                   旧版手册（已过期，待更新）
```

## 第三方组件与许可

- 本项目自有代码按 **MIT** 许可发布（见 [LICENSE](LICENSE)）。
- CE 引擎中适配自 **CPython**（PSF License）与 **NumPy**（BSD-3-Clause）的算法逐项记录于 [LICENSES.md](LICENSES.md) 与 [licenses/](licenses/)。
- 界面图标与美术资源分别来自 Tabler Icons、Fluent Emoji、Met Open Access、PolyHaven、Kenney、Bootstrap Icons、Game-Icons、Hero-Patterns、Open Doodles 等，均以 MIT / CC0 / 公有领域许可使用，原始许可保留在各资源目录内。
- OpenCL 头文件来自 Khronos Group（Apache-2.0）。

## 开源版功能说明：外部工程文件导入

开源版**不包含**读取外部商业灯光软件专有工程文件的功能（工程/设备配接导入、节目时间线导入、效果库导入、录制流导入）。原因：此类文件以专有加密格式分发，完整的读取实现依赖对加密机制的绕过；为保持项目完全干净、零法律风险，相关代码已整体移除。

**保留且完整可用的通用导入/导出路径：**

- CSV 配接表导入（设备类型、通道定义、配接、XY 云台自动配对、组合设备合成）
- MVR 导出（DIN SPEC 15800 开放标准）
- TopView 2D 舞台布局自建与样条几何工具（`src/ui/geometry_splines.py`）

**如何自行补全导入功能**（合法途径，不涉及任何加密绕过）：

1. 若你拥有**未加密**的工程文件（例如由你已获授权的软件导出的开放格式副本），可以为其编写解析器：此类格式本质是 ZIP 容器 + XML。解析产物接入本项目的 `PatchRow` / `FixtureDef`（配接管线）与 `TimelineProgram` / `Track` / `Clip`（时间线管线）即可复用全部下游功能，接入点见 `src/ui/import_patch_csv.py` 与 `src/core/models.py`。
2. 推荐的标准格式路线：实现 **MVR / GDTF 导入**（开放行业标准；本项目已实现 MVR 导出，可作为结构参照）。
3. 摆位/样条几何计算已沉淀为独立模块，可直接复用于任何来源的布局数据。

**免责声明**：本项目不包含、不支持、不推荐任何绕过第三方商业软件文件加密或技术保护措施的功能、代码或指引。任何第三方基于本项目代码自行添加此类功能的行为，均属其个人行为，由此产生的一切法律后果（包括但不限于侵犯著作权、规避技术保护措施）由行为人自行承担，与本项目、项目作者及全部贡献者无关。

## 安全提示

本软件可通过 Art-Net / sACN 控制真实灯光设备。首次连接真实设备前，请在隔离环境验证输出宇宙、通道映射与总控（Grand Master）行为，避免误触发机械装置（如喷泉、烟雾机、升降设备）。

## 独立性声明

本项目为独立开发的公益项目，与任何商业灯光/舞台软件厂商均无关联。"Art-Net"、"sACN" 等为相关协议/标准名称，其它可能出现的商标归其各自所有者所有。

## License

[MIT](LICENSE) — 完全免费，无任何担保（AS IS）。

---

## English

Wubaiji Ultra (雾白纪 ULTRA) is a **completely free, non-profit, open-source DMX512 lighting console** for Windows, built with Python 3.13 and PyQt6. It is built around a **fully recording-based programming workflow**: every cue can be performed and recorded in real time, with native joystick support for driving and recording fountain water patterns. An amplitude-control mode plus an effect-preset library lets you create, save and recall water shapes at any scale. It also features a channel programmer, multi-track timeline with incremental baking, an effect engine, audio-reactive modulation, a 2D top-view stage editor, a clip-studio effect canvas, and dual-protocol Art-Net / sACN output with multi-universe support. An optional native C compute engine (AVX2/SSE2) accelerates the math and falls back to a pure-Python path automatically.

**All code in this project is AI-generated.** It is published under the MIT License as a free public-welfare project, provided **AS IS, without any warranty**; the authors accept no liability for any damage arising from its use. Test thoroughly before driving real fixtures.
