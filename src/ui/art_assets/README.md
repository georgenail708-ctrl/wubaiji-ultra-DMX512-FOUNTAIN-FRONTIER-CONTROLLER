# art_assets — 真实美术资产库（R183 B0）

第三方可商用美术资产，供卡面底图 / 效果卡面 / 空态插画 / 纹理 / 面板头图使用。
**加载一律走** `src/ui/art_asset_library.py`（勿直接拼路径读文件）。

> 计数口径（R183 循环轮⑤统一）：`manifest.json` 164 条目（含 eff_ 别名复用同文件）→ 110 个唯一资产文件；目录另有 9 个 docs/licenses。manifest 为唯一计数真源。


```
src/ui/art_assets/
├── manifest.json      # 唯一索引：id → {path, category, source_url, license, author, ...}
├── LICENSES.md        # 许可汇总 + CC-BY 逐件署名表 + 归一化说明
├── README.md          # 本文件
├── licenses/          # 各来源许可原文（证据，勿删）
├── cards/             # 卡面底图（25 个 art key + 4 面板角标）
├── effects/           # Draw 效果卡面（7 组，41 基础图 + M4 新增 44 效果别名）
├── empty/             # 空态插画（Open Doodles，8 张）
├── patterns/          # 无缝图案/背景（Hero Patterns 12 + Kenney 10）
├── textures/          # 材质纹理（Poly Haven CC0，10 张 512px JPG）
└── paintings/         # 公共领域画作（Met Open Access CC0；卡面"一幅画"素材池）
```

## 用法

```python
from src.ui.art_asset_library import (
    art_path, load_art_pixmap, load_art_icon, has_art, art_ids, art_meta,
)

pix = load_art_pixmap("arrow_ltr", 96, 64)      # 卡面底图（等比适配，透明底）
pix = load_art_pixmap("empty_float", 260, 200)  # 空态插画（多色，不染色）
ico = load_art_icon("eff_heartbeat", 24, "#ff4444")  # 效果卡图标（tintable 染色）
tex = load_art_pixmap("tex_metal_plate", 512, 512)   # 无缝纹理平铺

if has_art("grid"):  ...
for aid in art_ids("empty_"): ...   # 前缀过滤
art_meta("eff_heartbeat")           # {'license': 'CC-BY-3.0', 'author': 'delapouite', ...}
```

**回退语义（重要）**：找不到 id / 文件缺失 / 解析失败一律返回**空** `QPixmap`/`QIcon`，
不抛异常、不弹窗。调用方负责回退，例如：

```python
pix = load_art_pixmap(art_key, w, h)
if pix.isNull():
    paint_card_art(painter, rect, art_key, state)   # clip_studio 程序化底图
```

## 命名约定

| 前缀 | 类别 | 例 |
|---|---|---|
| （裸名 art key） | 卡面底图 | `arrow_ltr` / `stair_rev` / `valve` |
| `eff_<effect_key>` | Draw 效果卡面 | `eff_heartbeat`（对应 `draw_effects.EFFECT_CATALOG` 的 `heartbeat`） |
| `eff_group_<组>` | 分组代表图（别名） | `eff_group_groove` → `eff_pulse_beat` |
| `badge_*` | 面板角标 | `badge_check` / `badge_award` |
| `panel_header_*` | 面板头图（别名） | `panel_header_wave` → `pat_topography` |
| `empty_*` | 空态插画 | `empty_unboxing` |
| `pat_*` | 无缝图案 | `pat_circuit_board` |
| `pat_k_pattern_NN` | Kenney 图案 | `pat_k_pattern_01` |
| `tex_*` | 材质纹理 | `tex_metal_plate` |

**别名条目**：manifest 中带 `alias_of` 的 id 指向上游 id 的同一文件（不复制文件），
`art_path()` 正常返回，`art_meta()` 同时给出 `alias_of`。

## 新增资产纪律（四步，缺一不可）

1. **许可先验**：只接受 CC0 / MIT / ISC / Apache-2.0 / Unlicense / CC-BY（须能提供署名）
   / 或来源方明确书面声明可商用。**许可存疑一律不用**（宁缺毋滥）。
2. **证据入库**：许可原文放 `licenses/<source>-LICENSE.txt`（或 `-SOURCE.txt`
   记录站点声明文本 + 抓取 URL + 日期）。禁止只写「网上找的」。
3. **登记 manifest**：每条必须有 `path` / `category` / `source_url` / `license`
   / `author` / `attribution_required` / `tintable`；`source_asset` 记原始文件名，
   `normalize` 记改写方式（如有）。CC-BY 资产同步更新 `LICENSES.md` 署名表。
   改动文件后重算 `bytes` 与 `sha256`（`test_manifest_sha256_integrity` 会校验）。
4. **归一化可选**：要染色的 SVG → 把颜色字面量改 `currentColor` 并置 `tintable: true`；
   多色插画（如 Open Doodles）仅把墨色改 `currentColor`（品牌色保留），并置 `tintable: true`。

体积门禁：总量 ≤ 25MB；SVG 优先；单件 PNG/JPG ≤ 500KB（纹理长边 ≤ 512px）。

## 禁止事项

* **不得**下载任何第三方成品软件的截图、图标、
  皮肤资源 — 版权物，禁止入库。
* **不得**把 unDraw 等「禁止作为独立素材再分发」的来源入库。
* **不得**新增 `spec.icon` / `spec.art` 断言依赖 — 新图片走本目录 + manifest，
  既有 art key（`clip_studio.studio_art.ART_KEYS`）保持程序化底图为主、图片为可选增强。

## 维护

* 增删资产后跑 `tests/test_r183_art_library.py`（校验 manifest ↔ 文件一致性、API 回退语义）。
* 联系表（人工目检）：`render_lab/bench/results/r183_art_contact_sheet.png`。
* 主题切换若需重染色：`from src.ui.art_asset_library import clear_art_cache; clear_art_cache()`。
