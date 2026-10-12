# 音乐结构深度分析 + 自动 Effect 调度

## 概览

本次改动在 **不修改任何现有功能** 的前提下，新增两个接口与一个桥接模块，
实现「音乐结构识别（乐器/段落/drop/buildup）→ 自动选择并切换 effect」。

```
                  ┌──────────────────────────────────────────────────┐
                  │  src/ui/audio_driver.py   (★ 不修改)               │
                  │   AudioAnalyzer.frame_ready(AudioFrame)          │
                  │   AudioAnalyzer.unified_value(float)             │
                  └──────────┬─────────────────────────────┬─────────┘
                             │                             │
              ┌──────────────┘                             │
              │ 接口 1: 现有功能保留不动                      │ 接口 2: 新增
              │ (Programmer 滑块/摇杆/推杆等沿用)            │
              ▼                                             ▼
       (各 Programmer 控件)                  ┌───────────────────────────────────┐
                                            │ src/audio/music_structure_         │
                                            │   analyzer.py    (★ 新增)          │
                                            │  输入: AudioFrame                   │
                                            │  输出: MusicStructureEvent          │
                                            │   - instruments: dict[str,float]   │
                                            │   - section_label                  │
                                            │   - drop / buildup_start/_end      │
                                            │   - section_change                 │
                                            └─────────────┬─────────────────────┘
                                                          │
                                                          ▼
                                            ┌───────────────────────────────────┐
                                            │ src/audio/music_to_effect_         │
                                            │   bridge.py     (★ 新增)           │
                                            │  EffectMapping 表: trigger→preset  │
                                            │  发出 effect_apply_request          │
                                            │   (preset_id, params,              │
                                            │    ch_types, sort_mode)            │
                                            │  ※ 与 EffectBrowserWidget          │
                                            │    .effect_applied 同形信号        │
                                            └─────────────┬─────────────────────┘
                                                          │
                                                          ▼  (直接接到现有槽)
                                          MainWindow._on_effect_applied
                                                          │
                                                          ▼
                                          ProgrammerPanel rows _apply_preset
                                                          │
                                                          ▼
                                          EffectEngine.compute_at  (★ 不修改)
```

## 新增文件

| # | 文件 | 作用 |
|---|---|---|
| 1 | `src/audio/__init__.py` | 包入口，re-export 公共 API |
| 2 | `src/audio/music_structure_analyzer.py` | **接口 2**：音乐结构分析器 |
| 3 | `src/audio/music_to_effect_bridge.py` | **新模块**：结构事件 → effect 自动调度桥 |
| 4 | `src/ui/audio/__init__.py` | UI 子包入口 |
| 5 | `src/ui/audio/audio_bridge_panel.py` | 桥接器配置/监控面板（dock） |

## 修改的文件（仅 1 个，且全部为加法）

`src/ui/main_window.py` —— 共 6 处加法，无任何删除或修改：

1. `PanelToolbar` 新增 `audio_bridge_toggled = pyqtSignal(bool)` 信号
2. `PanelToolbar._setup_ui` 新增 `_audio_bridge_btn` 按钮
3. `MainWindow._build_panel_toolbar` 连接 `audio_bridge_toggled` → `_toggle_dock(...)`
4. 视图菜单新增「🎧 Audio Bridge 音乐桥接器」条目
5. `MainWindow._make_dock_widgets` 新增 `_audio_bridge_dock` 与 `_audio_bridge_widget = None`
6. `MainWindow._lazy_load_panels` 新增 `AudioBridgePanel` 懒加载块；
   关键一行：`w.effect_apply_request.connect(self._on_effect_applied)`
   —— 直接复用 EffectBrowser 的现有槽，零下游修改。

## 核心数据结构

### `MusicStructureEvent`

每帧音乐结构快照。包含原 `AudioFrame` 全部字段（rms/peak/bands/bpm/beat_phase）
**加上**：

- `instruments: Dict[str, float]` — 6 件乐器在场度（kick/snare/hihat/bass/melody/pad），各 0..1
- `section_label: str` — `intro/verse/build/chorus/drop/breakdown/outro/unknown`
- `section_age: float` — 当前段落已持续多少秒
- `energy_short / energy_long` — 短/长期 RMS 移动平均
- `spectral_centroid: float` — 频谱重心（0=低频集中，1=高频集中）
- `novelty: float` — 频谱变化新颖度
- `event_type: str` — `tick / section_change / drop / buildup_start / buildup_end / instrument_onset`

### `EffectMapping`

桥接器中的一条规则：
```python
EffectMapping(
    trigger="chorus",                # 段落标签 / "drop" / "buildup_start" / ...
    preset_id="atx_beat_pulse",      # EffectEngine 中已注册的 preset id（R173 起：
                                      # audio_color_beat 已下架，示例改用真音频等价 id）
    ch_types=["R", "G", "B"],        # 应用到的通道类型；空=用 preset 默认
    sort_mode="index",               # TopView 排序模式
    params_override={"bpm": 128.0},  # 覆盖 preset 默认参数
    enabled=True,
    min_interval_s=1.5,              # 防抖
    priority=1.0,                    # 多规则冲突时取大
)
```

## 使用方式（运行时）

1. 启动应用 → 点击工具栏「🎧 Audio Bridge」按钮，弹出新面板
2. 点击面板顶部「⏻ 启用桥接」开关
3. 在表格中配置每个段落/事件对应的 effect preset
4. 加载音频文件（沿用现有 `audio_driver` 文件模式）或开启麦克风
5. 桥接器监听后台分析结果，自动在段落切换时触发 effect

整个过程下游链路（`MainWindow._on_effect_applied → ProgrammerPanel
→ EffectEngine.compute_at`）完全复用现有实现。

## 默认映射

| Trigger | Preset | 通道 | 排序 |
|---|---|---|---|
| intro | fader_breath | dimmer | linear_x |
| verse | audio_energy | dimmer | index |
| build | chase_single | dimmer | linear_x |
| chorus | audio_color_beat | R+G+B | index |
| drop (持续段) | chase_strobe_global | dimmer | index |
| breakdown | fader_breath | dimmer | linear_y |
| outro | fader_linear | dimmer | index |
| drop (瞬触) | chase_strobe_global | dimmer | index |
| buildup_start | audio_bpm_chase | dimmer | linear_x |

用户可在 UI 面板「映射规则」表格中任意修改，并通过「恢复默认映射」一键还原。

## 安全保证

- 新分析器对原 `AudioAnalyzer.frame_ready` 是 **只读订阅**，不影响其它消费者
- 任何分析阶段抛出的异常都被静默吞掉（`logging.debug`），保证老分析链路始终
  正常工作
- 桥接器默认关闭（`enabled=False`），需用户在 UI 上显式启用
- 所有新模块的全局单例都做了 C++ 对象失效检测，主窗口重建时安全可重复创建

## 编程式 API

如需在脚本/插件中直接驱动桥：

```python
from src.audio import (
    get_global_structure_analyzer,
    get_global_bridge,
    EffectMapping,
)

msa = get_global_structure_analyzer()    # 自动 attach 到全局 AudioAnalyzer
bridge = get_global_bridge()             # 自动 attach 到 msa
bridge.set_enabled(True)

# 自定义映射
bridge.replace_mappings([
    EffectMapping(trigger="chorus", preset_id="my_custom_preset",
                  ch_types=["dimmer"], sort_mode="radial"),
    EffectMapping(trigger="drop", preset_id="chase_strobe_global",
                  ch_types=["dimmer"], priority=3.0),
])

# 订阅信号
bridge.effect_apply_request.connect(your_apply_handler)
msa.section_changed.connect(lambda prev, new: print(f"{prev} → {new}"))
```
