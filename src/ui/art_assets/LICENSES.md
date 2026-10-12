# 美术资产许可清单（R183 B0）

本目录全部资产均为**可商用**许可。逐件来源、许可、作者见 `manifest.json`
（字段：`source_url` / `license` / `author` / `attribution_required` / `source_asset`）。

## 来源汇总

| 来源 | 件数 | 许可 | 作者 | 署名要求 | 许可证据文件 |
|---|---|---|---|---|---|
| [Bootstrap Icons v1.13.1](https://github.com/twbs/icons) | 56 | MIT | Bootstrap Icons (twbs) | 无需署名（随附 LICENSE 副本） | `licenses/bootstrap-icons-LICENSE.txt` |
| [game-icons.net](https://game-icons.net) | 14 | CC-BY-3.0 | 各作者（下节逐件列出） | **必须署名**：派生作品中注明 Icons made by {author} | `licenses/game-icons-LICENSE.txt` |
| [Hero Patterns](https://heropatterns.com/) | 12 | CC-BY-4.0 | Steve Schoger | **必须署名**（CC BY 4.0 条款要求） | `licenses/hero-patterns-SOURCE.txt` |
| [Open Doodles](https://www.opendoodles.com/) | 8 | 站点声明：Free for Commercial and Personal Use（无需署名） | Pablo Stanley | 无需署名 | `licenses/open-doodles-SOURCE.txt` |
| [Kenney Pattern Pack](https://kenney.nl/assets/pattern-pack) | 10 | CC0-1.0 | Kenney | 无需署名（建议保留） | `licenses/kenney-pattern-pack-LICENSE.txt` |
| [Poly Haven textures](https://polyhaven.com/) | 10 | CC0-1.0 | Poly Haven contributors | 无需署名 | `licenses/polyhaven-SOURCE.txt` |
| [The Met Open Access](https://www.metmuseum.org/about-the-met/policies-and-documents/open-access) | 2 | CC0-1.0（公共领域作品影像） | Vincent van Gogh（1853–1890）；影像 The Metropolitan Museum of Art | 无需署名（建议保留） | `licenses/met-open-access-SOURCE.txt` |

## game-icons.net 逐件作者（CC-BY-3.0 署名对象）

| 资产 id | 作者 | 原始图标 | 来源页 |
|---|---|---|---|
| `eff_bass_punch` | lorc | punch-blast | https://game-icons.net/1x1/lorc/punch-blast.html |
| `eff_bounce_wave` | delapouite | bouncing-spring | https://game-icons.net/1x1/delapouite/bouncing-spring.html |
| `eff_breath_deep` | delapouite | lungs | https://game-icons.net/1x1/delapouite/lungs.html |
| `eff_breathe_fast` | lorc | energy-breath | https://game-icons.net/1x1/lorc/energy-breath.html |
| `eff_cosine_wave` | lorc | static-waves | https://game-icons.net/1x1/lorc/static-waves.html |
| `eff_groove_swing` | delapouite | metronome | https://game-icons.net/1x1/delapouite/metronome.html |
| `eff_hard_gate` | delapouite | logic-gate-and | https://game-icons.net/1x1/delapouite/logic-gate-and.html |
| `eff_heartbeat` | delapouite | heart-beats | https://game-icons.net/1x1/delapouite/heart-beats.html |
| `eff_spark_burst` | delapouite | sparkles | https://game-icons.net/1x1/delapouite/sparkles.html |
| `eff_stomp` | lorc | stomp-tornado | https://game-icons.net/1x1/lorc/stomp-tornado.html |
| `eff_swell` | lorc | big-wave | https://game-icons.net/1x1/lorc/big-wave.html |
| `eff_tide` | delapouite | high-tide | https://game-icons.net/1x1/delapouite/high-tide.html |
| `pump` | delapouite | manual-water-pump | https://game-icons.net/1x1/delapouite/manual-water-pump.html |
| `valve` | delapouite | valve | https://game-icons.net/1x1/delapouite/valve.html |

## Hero Patterns 署名对象（CC-BY-4.0）

资产 id 前缀 `pat_`（不含 `pat_k_`）共 12 件，
作者 Steve Schoger（https://twitter.com/SteveSchoger），原始图案名见 manifest 的 `source_asset`。

## 归一化说明（derived work）

为支持单色染色，落库时对部分 SVG 做**等价改写**（只改颜色声明，不改几何）：

* `game-icons-mono`：移除全画布背景矩形，`#fff` 填充改为 `currentColor`；
* `heropattern-tint`：全部 `fill="#..."` 改为 `currentColor`；
* `kenney-pattern-tint`：全部填充改 `currentColor`，按原灰度换算 `fill-opacity`（0.30-0.95，保留双色调对比）。
* `opendoodles-ink-tint`：仅把主墨色 `#000000` 改 `currentColor`（品牌粉 `#FF5678` 原样保留，插画仍为双色）。

上述改写属许可允许的修改（MIT / CC0 / CC-BY 均允许修改与再分发）。
Poly Haven 纹理另做长边 512px 缩放 + JPEG q80 重编码（体积门禁）。

## 未采用来源（许可存疑或不适配）

* unDraw.co — 其许可不允许把插画作为独立素材再分发，而入库即再分发；改用 Open Doodles（同风格，明确可商用）。
* ambientCG — CC0 可用，但 Poly Haven 已覆盖纹理需求，避免多来源维护成本。
* 各成品软件的截图与图标 — 版权物，**禁止入库**（硬约束）。

## 再分发注意

* 若本仓库对外分发，请保留 `licenses/` 目录全部文件；CC-BY 资产须保留 `LICENSES.md` 的署名表。
* 二进制分发（打包 exe）同样适用上述署名要求，CC-BY 署名可写入「关于」对话框。
