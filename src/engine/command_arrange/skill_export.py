"""
command_arrange.skill_export — 「命令行代码编曲」教学文档导出器

把本项目真实的语法契约(ir.py)、项目画像(project_profile.py)、配接通道
(voice_clip_renderer.build_patch)、沙箱函数(sandbox.safe_namespace)汇编成
一份给通用大模型阅读的 Markdown 教学稿：读完它，模型就能直接化身「这个喷泉
系统的自动编曲程序员」，写出合法的 clip 命令。

设计准绳：
  * 关键字/方向/通道/scope/hold 等参考表全部**程序化**从 ir.py 的字典生成，
    永不与代码漂移。
  * build_skill_markdown(None) 也能产出完整的语法/参考/函数/示例(>2500字)，
    仅项目相关的设备表降级为「不可用」提示。
  * 任何 project 字段缺失/异常都不抛错。

纯 core，无 PyQt 依赖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import ir
from .wing_attributes import HELP as WING_HELP


# ─────────────────────────────────────────────────────────────────────────────
#  小工具
# ─────────────────────────────────────────────────────────────────────────────
def _md_escape_cell(s: Any) -> str:
    """转义将进入 Markdown 表格单元的文本（竖线/换行）。"""
    t = str(s)
    return t.replace("|", "\\|").replace("\n", " ")


def _join(items: List[str], sep: str = "、") -> str:
    return sep.join(str(x) for x in items if str(x) != "")


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    """生成 GitHub 风格 Markdown 表格。"""
    out = []
    out.append("| " + " | ".join(_md_escape_cell(h) for h in headers) + " |")
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for r in rows:
        out.append("| " + " | ".join(_md_escape_cell(c) for c in r) + " |")
    return "\n".join(out)


def _fmt_num(v: Any) -> str:
    """整数去掉小数点尾巴，否则保留一两位。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:g}"


# ─────────────────────────────────────────────────────────────────────────────
#  沙箱函数清单（优先实读 sandbox.safe_namespace，缺失则用文档化兜底）
# ─────────────────────────────────────────────────────────────────────────────
_FALLBACK_FUNCS = {
    "数学函数": [
        ("sin/cos/tan", "三角函数（弧度）"),
        ("asin/acos/atan", "反三角函数"),
        ("atan2(y, x)", "二参反正切，象限正确"),
        ("sqrt(x)", "平方根"),
        ("abs(x)", "绝对值"),
        ("min(...)/max(...)", "最小/最大值"),
        ("floor(x)/ceil(x)", "向下/向上取整"),
        ("round(x)", "四舍五入"),
        ("pow(x, y)", "幂"),
        ("exp(x)/log(x)", "指数/自然对数"),
    ],
    "常量": [
        ("pi", "圆周率 π"),
        ("tau", "2π"),
        ("e", "自然常数"),
    ],
    "编曲辅助": [
        ("clamp(v, lo=0, hi=255)", "夹取到区间（默认 0..255）"),
        ("lerp(a, b, t)", "线性插值 a→b"),
        ("smoothstep(a, b, x)", "平滑过渡（S 形）"),
        ("hsv(h, s, v)", "HSV→(r,g,b) 元组，h/s/v∈0..1"),
        ("rgb(r, g, b)", "组合三色（透传/夹取）"),
        ("noise(x)", "确定性平滑噪声（同 x 同值）"),
        ("rand(seed)", "确定性伪随机（同 seed 同值）"),
        ("tri(x)", "三角波 0..1，周期 1"),
        ("saw(x)", "锯齿波 0..1，周期 1"),
        ("sq(x)", "方波 0/1，周期 1"),
    ],
}


def _collect_sandbox_funcs() -> Tuple[Optional[List[str]], str]:
    """尝试从 sandbox.safe_namespace() 读取真实可用名。

    返回 (names_or_None, source_note)。names 为按名排序的字符串列表，
    若 sandbox 不可用则 names=None（调用方改用文档化兜底）。
    """
    try:
        from . import sandbox  # type: ignore
        ns = sandbox.safe_namespace()  # type: ignore[attr-defined]
        if isinstance(ns, dict):
            names = sorted(
                k for k in ns.keys()
                if isinstance(k, str) and not k.startswith("__")
            )
            if names:
                return names, "（实读自 `sandbox.safe_namespace()`）"
    except Exception:
        pass
    return None, "（sandbox 模块尚未就绪，下表为文档化约定清单）"


# ─────────────────────────────────────────────────────────────────────────────
#  项目信息抽取（全程容错）
# ─────────────────────────────────────────────────────────────────────────────
def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _def_name_map(project: Any) -> Dict[Any, str]:
    out: Dict[Any, str] = {}
    for fd in _get(project, "fixture_defs", []) or []:
        did = _get(fd, "id")
        nm = _get(fd, "name", "") or "?"
        if did is not None:
            out[did] = nm
    return out


def _SHAPE_ZH(shape: str) -> str:
    return {"ring": "环/圆", "line": "排/线", "area": "散布/面",
            "small": "小簇(<3)", "unknown": "未知"}.get(shape, shape)


def _project_channel_types(project: Any) -> Optional[List[str]]:
    """收集本项目 fixture_defs 里真实出现的通道类型（保持出现顺序、去重）。

    每个 channel 可能是字符串（type 名），或 dict（取 'type'/'name'），或对象
    （取 .type/.name）。读不出时返回 None，调用方据此降级到 ir.CHANNEL_TYPES。
    """
    if project is None:
        return None
    types: List[str] = []
    seen = set()
    try:
        for fd in _get(project, "fixture_defs", []) or []:
            chs = _get(fd, "channels", []) or []
            for c in chs:
                if isinstance(c, str):
                    t = c
                elif isinstance(c, dict):
                    t = c.get("type") or c.get("name")
                else:
                    t = _get(c, "type", None) or _get(c, "name", None)
                if t:
                    t = str(t)
                    if t not in seen:
                        seen.add(t)
                        types.append(t)
    except Exception:
        return None
    return types or None


def _norm_available_channels(project: Any) -> Optional[set]:
    """项目里可被解析器接受（能规范化）的通道规范名集合；无项目返回 None。"""
    pts = _project_channel_types(project)
    if not pts:
        return None
    out = set()
    for t in pts:
        try:
            c = ir.normalize_channel(t)
        except Exception:
            c = None
        if c:
            out.add(c)
    return out or None


# ─────────────────────────────────────────────────────────────────────────────
#  能力感知（capability-aware）：读真实配接，挑「确实有目标通道」的选择器
# ─────────────────────────────────────────────────────────────────────────────
#  目标：第 2 节概览与第 9 节每个 clip 示例里写的通道，所选设备必然真有该通道，
#  从而在任何真实工程上都能渲染出 >0 个 clip（绝不写 0 clip 的示例）。
_MAX_SEL = 8   # 全局序号示例的上限台数（控台风格示例保持精简、必不越界）


def _build_caps(project: Any) -> Dict[str, Any]:
    """汇总本项目的「选择能力」：每台设备真有哪些规范通道、编组成员通道交集、
    各通道在全局序里的 1 基索引。全程容错，project=None 时返回空能力（示例降级
    为通用全局序号写法，仍可解析）。

    返回 dict:
      n            : 全局设备总数
      fid_order    : 全局序 fixture id 列表（1 基对应 idx+1）
      idx_chans    : {全局#(1基) -> set(规范通道)}
      chan_index   : {规范通道 -> 有序[全局#...]}
      avail        : set(本项目出现过的规范通道)
      groups       : [(组名, [成员全局#...], set(成员通道交集)), ...]（按工程顺序）
    """
    caps: Dict[str, Any] = {
        "n": 0, "fid_order": [], "idx_chans": {}, "chan_index": {},
        "avail": set(), "groups": [],
    }
    if project is None:
        return caps
    # 配接 + 全局序（复用 renderer 的真源，防漂移）
    patch: Dict[str, Any] = {}
    fid_order: List[Any] = []
    try:
        from .renderer import build_project_index  # type: ignore
        pidx = build_project_index(project)
        fid_order = list(pidx.fixture_ids or [])
        patch = dict(pidx.patch or {})
    except Exception:
        patch = {}
        fid_order = []
    if not patch:
        try:
            from ..auto_arrange.voice_clip_renderer import build_patch  # type: ignore
            patch = build_patch(project) or {}
        except Exception:
            patch = {}
    if not fid_order:
        for fx in _get(project, "fixtures", []) or []:
            fid = _get(fx, "id")
            if fid is not None:
                fid_order.append(fid)

    caps["fid_order"] = fid_order
    caps["n"] = len(fid_order)

    # 每个全局# 的规范通道集合 + 反向索引
    idx_chans: Dict[int, set] = {}
    chan_index: Dict[str, List[int]] = {}
    avail: set = set()
    for g1, fid in enumerate(fid_order, start=1):
        pe = patch.get(fid) or {}
        cset = set()
        for raw in (pe.get("ch", {}) or {}):
            try:
                c = ir.normalize_channel(raw) or str(raw)
            except Exception:
                c = str(raw)
            cset.add(c)
        idx_chans[g1] = cset
        for c in cset:
            chan_index.setdefault(c, []).append(g1)
            avail.add(c)
    caps["idx_chans"] = idx_chans
    caps["chan_index"] = chan_index
    caps["avail"] = avail

    # 编组：名 + 成员全局# + 成员通道交集（只有交集含某通道，整组写该通道才人人有效）
    fid_to_g1 = {fid: g1 for g1, fid in enumerate(fid_order, start=1)}
    groups: List[Tuple[str, List[int], set]] = []
    for grp in _get(project, "fixture_groups", []) or []:
        nm = _get(grp, "name", "") or ""
        mids = _get(grp, "fixture_ids", []) or []
        member_g1 = [fid_to_g1[m] for m in mids if m in fid_to_g1]
        if not nm.strip() or not member_g1:
            continue
        inter: Optional[set] = None
        for g1 in member_g1:
            cs = idx_chans.get(g1, set())
            inter = set(cs) if inter is None else (inter & cs)
        groups.append((nm, member_g1, inter or set()))
    caps["groups"] = groups
    return caps


def _quote_group(nm: str) -> str:
    """组名含空格/特殊字符时加引号（双引号），否则原样。"""
    s = str(nm)
    if any(ch in s for ch in " \t\"'.,"):
        return '"' + s.replace('"', "'") + '"'
    return s


def _fmt_index_run(nums: List[int]) -> str:
    """把一串 1 基索引压成控台风格的选择器串：连续段用 `a thru b`，散点用逗号。
    例：[1,2,3,4] → '1 thru 4'；[1,3,5] → '1, 3, 5'；[1,2,3,7] → '1 thru 3, 7'。"""
    nums = sorted(set(int(x) for x in nums if int(x) >= 1))
    if not nums:
        return ""
    runs: List[Tuple[int, int]] = []
    s = p = nums[0]
    for v in nums[1:]:
        if v == p + 1:
            p = v
        else:
            runs.append((s, p)); s = p = v
    runs.append((s, p))
    parts = [f"{a} thru {b}" if b > a else f"{a}" for a, b in runs]
    return ", ".join(parts)


def _pick_role_channel(caps: Dict[str, Any], role: str) -> Optional[str]:
    """为一个角色挑一个本项目真实存在的规范通道；都不存在返回 None。
      intensity → pump / dimmer / valve
      color     → R/G/B（需三色齐全，返回 'RGB' 哨兵）
      position  → motor_x/motor_y（需双轴齐全，返回 'motor' 哨兵）
    project=None（avail 为空）时：intensity 退回 'dimmer'（最通用），其余 None。"""
    avail: set = caps.get("avail") or set()
    if not avail:
        return {"intensity": "dimmer"}.get(role)
    if role == "intensity":
        for c in ("pump", "dimmer", "valve"):
            if c in avail:
                return c
        return None
    if role == "color":
        return "RGB" if {"R", "G", "B"} <= avail else None
    if role == "position":
        return "motor" if {"motor_x", "motor_y"} <= avail else None
    return None


def _select_for(caps: Dict[str, Any], required: List[str]) -> Tuple[str, str]:
    """挑一个「成员确实都有 required 全部通道」的选择器。

    返回 (slct选择器串, 人类可读说明)。优先用首个满足条件的真实编组（成员通道
    交集含全部 required）；否则用全局序号——取前若干个「真有这些通道」的设备
    （`a thru b` 或逗号列表），数量上限 _MAX_SEL，且绝不超过 N。
    都挑不到时返回空全局序（调用方据此回退/省略该示例）。
    """
    req = [r for r in required if r]
    n = int(caps.get("n", 0) or 0)
    # 1) 真实编组：成员通道交集涵盖全部 required
    for nm, member_g1, inter in (caps.get("groups") or []):
        if req and not all(r in inter for r in req):
            continue
        if not req and not member_g1:
            continue
        return _quote_group(nm), f"编组「{nm}」"
    # 2) 全局序号：挑前 _MAX_SEL 个真有全部 required 的设备
    idx_chans: Dict[int, set] = caps.get("idx_chans") or {}
    if n <= 0:
        # 无项目信息：给通用全局序（仍可解析；无通道校验也不会越界因为渲染期忽略）
        return f"1 thru {_MAX_SEL}", "全局序号"
    if not req:
        picks = list(range(1, min(n, _MAX_SEL) + 1))
        return _fmt_index_run(picks), "全局序号"
    picks = [g1 for g1 in range(1, n + 1) if all(r in idx_chans.get(g1, set()) for r in req)]
    picks = picks[:_MAX_SEL]
    if picks:
        return _fmt_index_run(picks), "全局序号"
    return "", ""


def _role_channels(role: str, chan: str) -> List[str]:
    """把角色挑出的通道展开成「该示例会写入的真实通道列表」（用于挑选择器）。"""
    if role == "color" or chan == "RGB":
        return ["R", "G", "B"]
    if role == "position" or chan == "motor":
        return ["motor_x", "motor_y"]
    return [chan]


def _example_group_selector(project: Any) -> Tuple[str, str]:
    """（保留向后兼容）返回 (slct选择器串, 人类可读组名)。

    优先用项目里第一个非空编组的真实组名；无编组时退回全局序号选择器。
    """
    try:
        groups = _get(project, "fixture_groups", []) or []
    except Exception:
        groups = []
    for g in groups:
        nm = _get(g, "name", "") or ""
        ids = _get(g, "fixture_ids", []) or []
        if nm and ids:
            return _quote_group(nm), str(nm)
    return f"1 thru {_MAX_SEL}", ""


# ─────────────────────────────────────────────────────────────────────────────
#  各章节生成
# ─────────────────────────────────────────────────────────────────────────────
def _sec_role() -> str:
    return (
        "## 1. 角色与目标\n\n"
        "你现在是这个喷泉控制系统的**自动编曲程序员**。你的唯一工作：根据音乐/"
        "情绪/画面需求，写出一段「命令行代码编曲」DSL；系统会把它解析、渲染成"
        "时间线上的一个个 clip（片段）。\n\n"
        "这是**单向流程**：你写命令 → 系统生成 clip 落到时间线。你不需要、也无法"
        "回读舞台现状；你写什么，舞台就按什么播。\n\n"
        "记住两条铁律：\n\n"
        "1. **一个 `clip{ }` 块 = 时间线上的一个 clip**。块头给它命名、指定落在哪条"
        "轨道；块体描述它选哪些设备、每个通道怎么动、什么时候开始、多长、怎么淡入"
        "淡出、按什么方向/分组铺开。\n"
        "2. **一切必须确定性可复现**。同样的命令在任何机器上渲染出一模一样的帧。"
        "禁止真随机、禁止读时间/文件/网络。需要“随机感”请用 `rand(seed)` / `noise(x)`。\n"
    )


def _sec_changelog() -> str:
    """更新日志：放在最顶部（角色之后），让文档永远显示最近一批语法变更。"""
    return (
        "## 更新日志 (Changelog)\n\n"
        "> 语法每次升级都记录在此，最新在最上。读到一条不熟悉的能力，回这里对照即可。\n\n"
        "### v1.2 — 控台选择增强 / 多通道 / 默认块 / 能力选择 / 类型化单位 等\n\n"
        "- **多通道 fan-out**：`R,G,B: 表达式` —— 逗号列出多个通道，同一表达式同时写入"
        "它们（等价分别写 `R:` / `G:` / `B:`，见 4.1）。\n"
        "- **`slct all`**：一次选中本项目全部设备（别名 `all` / `全部` / `所有` / `全选`，见 3.6）。\n"
        "- **`slct type pump`**：选中配接里含某通道类型的全部设备（别名 `type` / `类型` / "
        "`按类型` / `kind`，见 3.6），适合「所有泵」「所有电机」按能力编排。\n"
        "- **`default { ... }` 默认块**：顶层块设公共属性（dur/fi/fo/dir/scope/wings…）作其后"
        "所有 clip 的默认；clip 自身属性覆盖之；多个 `default` 累积（后者覆盖前者，见 2.1）。\n"
        "- **def 里 `c.<参数名>`**：`c.spd` / `c.base` 直接读 `param`（等价 `c.p['spd']` / "
        "`c['spd']`），与标量裸名风格统一（见 4.2 / 6.5）。\n"
        "- **类型化单位**：时间字段（at/dur/fi/fo/pause/seq_delay）可带 `s`（秒）；计数/比例"
        "字段（sets/wings/block/batches/rate）**不可**带 `s`（`sets 2s` 报错，见 2.2 / 6 节）。\n"
        "- **track_light 阈值 `@`=`=`**：`pump@128` 与 `pump=128` 等价（`=` 是 `@` 的别名，见例6）。\n"
        "- **dur 默认值**：不写 `dur` 默认 4s（仅非致命提示）；为可读性建议显式写 `dur`。\n"
        "- **更严格的报错**：属性关键字误当通道（`dur: 8`）、整组后直接跟 step/count、`at +2s` "
        "相对偏移、clip 块头多余 token 等都会明确报错（见第 10 节）。\n\n"
        "### v1.1 — 控台步进选择 / 翼·块·翼方向 / 参数\n\n"
        "- 控台风格步进选择：`thru` / `step` / `count` / `from`（含中文别名，见 3.1）。\n"
        "- 翼 `wings`、块 `block`、翼方向 `wingdir`（空间分组，见 6.5）。\n"
        "- 命名参数 `param k=v`（标量里裸名用、def 里 `c.p['k']`，见 6.5）。\n\n"
        "### v1.0 — 基础语法\n\n"
        "- `clip{ }` 块 = 时间线上一个 clip。\n"
        "- 标量速写 `通道: 表达式` 与 `def effect(c):` 全式效果体（见第 4 节）。\n"
        "- 三种设备编号：全局序号 / 编组名 / 组内下标（见第 3 节）。\n"
        "- clip 全部右击可设置字段（时间/淡入淡出/方向/分组/坐标系… 见第 6 节）。\n"
    )


def _sec_grammar_overview(project: Any = None, caps: Optional[Dict[str, Any]] = None) -> str:
    # 能力感知：概览里的两个 clip 都挑「所选设备真有该通道」的写法，保证本项目可渲染
    if caps is None:
        caps = _build_caps(project)
    inten = _pick_role_channel(caps, "intensity") or "dimmer"
    sel1, _d1 = _select_for(caps, [inten])
    if not sel1:
        sel1 = f"1 thru {min(max(caps.get('n', 0) or _MAX_SEL, 1), _MAX_SEL)}"
    # 第二个 clip：有 RGB 用 R 演示表达式随 t 变化；否则用强度通道
    color = _pick_role_channel(caps, "color")
    if color:
        sel2, _d2 = _select_for(caps, ["R", "G", "B"])
        ch2 = "R"
    else:
        sel2, _d2 = _select_for(caps, [inten])
        ch2 = inten
    if not sel2:
        sel2 = sel1
    return (
        "## 2. 语法总览\n\n"
        "整段编曲就是若干个 `clip{ }` 块，顺序排列。基本骨架：\n\n"
        "```\n"
        "# 这是注释，# 之后到行尾都被忽略\n"
        "clip \"片段名\" -> track \"轨道名\" {\n"
        f"    slct {sel1};               # 选设备（这些设备真有 {inten} 通道）\n"
        "    at 0s; dur 4s;             # 起始 / 时长（秒，s 可省）\n"
        "    fi 0.5; fo 0.5;            # 淡入 / 淡出\n"
        f"    {inten}: 200;                 # 标量效果体：把 {inten} 通道写成 200\n"
        "}\n"
        "\n"
        "clip \"下一段\" {                 # 省略 -> track 时按通道类型给默认轨名\n"
        f"    slct {sel2};\n"
        "    dur 6s;                    # 没写 at，则接在上一条之后\n"
        f"    {ch2}: 128 + 127*sin(t);       # 表达式随时间 t 变化\n"
        "}\n"
        "```\n\n"
        "要点：\n\n"
        "- 块头：`clip \"标签\"` 必写；`-> track \"轨道名\"` 可选（决定落在哪条轨道，"
        "省略则按通道类型取默认轨名）。\n"
        "- 块体每一行是「关键字 + 值」或「通道: 表达式」，行末用 `;` 或换行分隔。\n"
        "- 数字可带单位 `s`（秒）：`at 2.5s` 与 `at 2.5` 等价。\n"
        "- 大小写不敏感，关键字中英别名通用（见下表）。\n"
        "- **写通道前先确认所选设备真有该通道**（见第 3、5、8 节）；设备没有的通道写了"
        "会被静默忽略，clip 可能渲染不出任何内容。\n"
        "- `#` 起行内注释。\n\n"
        "### 2.1 默认块 `default { ... }`（公共属性，省去逐 clip 重复）\n\n"
        "顶层写一个 `default { ... }` 块，把公共属性（`dur`/`fi`/`fo`/`dir`/`scope`/"
        "`wings`/`sets`… 任意调度属性，但**不含** `slct` 与通道/def 效果体）设为其**后续"
        "所有 clip 的默认**。每个 clip 自身显式写的属性会**覆盖**默认；多个 `default` 块"
        "按出现顺序**累积**（后者覆盖前者同名属性）。\n\n"
        "```\n"
        "default {                    # 之后每个 clip 默认 dur 6s、淡入淡出各 0.5s\n"
        "    dur 6s; fi 0.5; fo 0.5;\n"
        "    dir 从左到右;\n"
        "}\n"
        f"clip \"承默认A\" {{\n"
        f"    slct {sel1};\n"
        f"    {inten}: 200;            # 用默认 dur6 / fi / fo / dir\n"
        "}\n"
        f"clip \"覆盖B\" {{\n"
        f"    slct {sel1};\n"
        "    dur 3s;                  # 自身 dur 3s 覆盖默认\n"
        f"    {inten}: 120;\n"
        "}\n"
        "```\n\n"
        "### 2.2 单位规则（时间可带 `s`，计数/比例不可）\n\n"
        "- **时间字段**（`at` / `dur` / `fi` / `fo` / `pause` / `seq_delay`）可带 `s`（秒），"
        "`dur 6s` 与 `dur 6` 等价。\n"
        "- **计数 / 比例字段**（`sets` / `wings` / `block` / `batches` / `rate`）是纯数，"
        "**不可**带 `s`——`sets 2s` 会报错（请写 `sets 2`）。\n"
    )


def _select_ops_table() -> str:
    """从 ir.SELECT_OPS 程序化生成「选择器算子」别名表（永不与代码漂移）。"""
    label = {
        "from":  "起点（控台风格 from 式起始编号）",
        "thru":  "区间到（a thru b = a..b 闭区间）",
        "step":  "步进（相邻选中编号相差 N）",
        "count": "取个数（从起点按 step 取 N 个）",
    }
    order = ["from", "thru", "step", "count"]
    rows = []
    for op in order:
        aliases = ir.SELECT_OPS.get(op, [])
        en = [a for a in aliases if a.isascii()]
        zh = [a for a in aliases if not a.isascii()]
        rows.append([op, _join(en, " / "), _join(zh, " / "), label.get(op, "")])
    return _table(["规范算子", "英文别名", "中文别名", "含义"], rows)


def _sec_select(project: Any, profile: Any, def_names: Dict[Any, str],
                caps: Optional[Dict[str, Any]] = None) -> str:
    if caps is None:
        caps = _build_caps(project)
    n = int(caps.get("n", 0) or 0)
    # 真实组名（无则给示意名，并注明本项目暂无编组）
    groups = caps.get("groups") or []
    if groups:
        g_name = groups[0][0]
        g_name2 = groups[1][0] if len(groups) > 1 else g_name
        group_note = ""
    else:
        g_name = "环1"
        g_name2 = "外圈 灯"
        group_note = "（本项目暂无编组，下列组名 `环1`/`外圈 灯` 仅为格式示意；真实编组见第 8.2 节）"
    g_q = _quote_group(g_name)
    g2_q = _quote_group(g_name2)
    # 真实上界，用于「全局连续」示例不越界
    hi = min(n, 10) if n else 10
    # 3.6 type 示例：挑一个本项目真实存在的通道类型（强度优先），保证选得到设备
    type_ch = _pick_role_channel(caps, "intensity") or "pump"
    if (caps.get("avail") or set()) and type_ch not in (caps.get("avail") or set()):
        # 退而求其次，用 avail 里任意一个规范通道
        type_ch = sorted(caps.get("avail") or set())[0]
    # all/type 别名串（程序化自 ir，永不漂移）
    all_alias = _join([a for a in ir.SELECT_ALL_ALIASES], " / ")
    type_alias = _join([a for a in ir.SELECT_TYPE_ALIASES], " / ")
    return (
        "## 3. 设备选择（slct · 控台风格语法）\n\n"
        "`slct` 行决定这个 clip 控制哪些设备。语法对齐专业灯控台（专业控台风格），"
        "支持**区间 `thru`、步进 `step`、计数 `count`、起点 `from`**，可中英混写，逗号"
        "并列取并集。\n\n"
        "### 3.1 选择器算子（中英别名，程序化自引擎，永不漂移）\n\n"
        + _select_ops_table()
        + "\n\n> **step 语义**：`step N` = **选中的相邻编号相差 N**（不是“跳过 N 台”）。"
        "例如 `1 thru 10 step 3` → **1,4,7,10**。\n\n"
        "### 3.2 全局序号（按工程 `project.fixtures` 的 1 基顺序）\n\n"
        "```\n"
        f"slct 1 thru {hi};        # 连续：第 1..{hi} 台（紧凑写法 `slct 1-{hi}` 等价）\n"
        "slct 1 thru 10 step 3;  # 步进：选 1,4,7,10（相邻相差 3）\n"
        "slct from 1 step 3 count 4;  # from 式：从 1 起，步进 3，取 4 个 → 1,4,7,10\n"
        "slct 1,5,9 thru 12;     # 列表混用：第 1、第 5、第 9..12 台\n"
        "slct 1 thru 10, 20 thru 30;  # 多区间并集\n"
        "```\n\n"
        "三种写法选到的具体编号：\n\n"
        + _table(
            ["写法", "选中编号"],
            [
                ["`1 thru 10`", "1,2,3,4,5,6,7,8,9,10"],
                ["`1 thru 10 step 3`", "1,4,7,10"],
                ["`from 1 step 3 count 4`", "1,4,7,10（与上等价）"],
            ],
        )
        + "\n\n### 3.3 编组名（按工程里的编组整组选）"
        + (group_note + "\n\n" if group_note else "\n\n")
        + "```\n"
        f"slct {g_q};        # 选中整个「{g_name}」组\n"
        f"slct {g2_q};   # 组名含空格/特殊字符时加引号\n"
        "```\n\n"
        "### 3.4 组内下标（`组名.<编号表达式>`，1 基，按组内 `fixture_ids` 顺序）\n\n"
        "组内同样支持 `thru / step / count / from`：\n\n"
        "```\n"
        f"slct {g_q}.2-4;                  # 组内第 2..4 个成员（紧凑区间）\n"
        f"slct {g_q}.1 thru 8 step 2;      # 组内第 1,3,5,7 个（相邻相差 2）\n"
        f"slct {g2_q}.from 1 step 2 count 4;  # 组内从第1个起步进2取4个 → 1,3,5,7\n"
        "```\n\n"
        "### 3.5 中文别名（与英文完全等价）\n\n"
        "算子全部可用中文，方便直接用中文描述编排：\n\n"
        "```\n"
        "slct 1 至 10 间隔 3;          # 等价 1 thru 10 step 3 → 1,4,7,10\n"
        "slct 从 1 间隔 3 取 4;        # 等价 from 1 step 3 count 4 → 1,4,7,10\n"
        f"slct {g_q}.从 1 间隔 2 取 4;   # 组内中文写法\n"
        "```\n\n"
        + _table(
            ["中文写法", "等价英文", "选中编号"],
            [
                ["`从 1 间隔 3 取 4`", "`from 1 step 3 count 4`", "1,4,7,10"],
                ["`1 至 10 间隔 3`", "`1 thru 10 step 3`", "1,4,7,10"],
            ],
        )
        + "\n\n> 全部范围都是 **1 基闭区间**；多个 `slct` 项（逗号或多行）取并集；"
        "超出范围/区间写反（如 `8-3`）的部分会被忽略。\n"
        "### 3.6 整体选择：`slct all`（全部）/ `slct type <通道>`（按能力）\n\n"
        "两种**不依赖编号**的选择，渲染期才落到具体设备，跨工程通用：\n\n"
        "```\n"
        "slct all;              # 选中本项目全部设备\n"
        f"slct type {type_ch};         # 选中配接里含 {type_ch} 通道的全部设备\n"
        "```\n\n"
        "- **`slct all`**：当前项目所有 fixtures。别名：`" + all_alias + "`。\n"
        "- **`slct type <通道>`**：配接里**含该通道类型**的全部设备（如 `type pump` = 所有"
        "带泵的设备，`type motor_x` = 所有带水平电机的）。别名：`" + type_alias + "`。"
        "通道名用第 5 节任一别名。配合「写该通道」即可保证人人有效，非常适合"
        "「所有泵一起喷」「所有电机一起转」这类按能力编排。\n"
        "> all / type 之后不应再跟编号或算子（`all 1 thru 3` / `type pump step 2` 会报错）。\n"
        "> 本项目真实的全局序号表、编组表见第 8 节——**写通道前先确认目标设备真有该通道**。\n"
    )


def _sec_bodies() -> str:
    return (
        "## 4. 效果体两种写法\n\n"
        "效果体描述「每一帧、每一台设备的某个通道应该是多少」。两种写法：\n\n"
        "### 4.1 标量速写 `通道: 表达式`\n\n"
        "每帧对每台设备求值一次，结果写进该通道（自动夹取到 0..255）：\n\n"
        "```\n"
        "pump: 200;                       # 常数\n"
        "R:    128 + 127*sin(t*2*pi);     # 随时间起伏\n"
        "dimmer: 255 * i / max(n-1, 1);   # 按设备序号渐变\n"
        "```\n\n"
        "表达式里可用的变量：\n\n"
        + _table(
            ["变量", "含义"],
            [
                ["t", "本 clip 内时间（秒），0 → dur"],
                ["i", "当前设备在选中集合里的下标（0 基）"],
                ["n", "选中设备总数"],
                ["x, y", "当前设备在 TopView 的归一化坐标（约 -1..1）"],
                ["dur", "clip 时长（秒）"],
                ["fps", "渲染帧率"],
                ["（参数名）", "你在 params 里定义的参数，直接当变量用"],
            ],
        )
        + "\n\n"
        "**多通道 fan-out**：通道名可写成逗号列表，**同一表达式同时写入多个通道**——"
        "`R,G,B: 表达式` 等价于分别写 `R:` / `G:` / `B:` 三行同值。适合三色同步、"
        "多泵齐射：\n\n"
        "```\n"
        "R,G,B: 128 + 100*sin(t*pi);   # 三色同步呼吸（= 分别写 R/G/B 同值）\n"
        "pump,valve: 200;              # 泵与阀同时给 200\n"
        "```\n\n"
        "### 4.2 def 全式函数\n\n"
        "需要一次写多个通道、或写复杂逻辑时，用 `def effect(c):`，通过上下文对象 "
        "`c` 取值并 `c.set(通道, 值)`：\n\n"
        "```\n"
        "def effect(c):\n"
        "    h = (c.t / c.dur + c.i / max(c.n, 1)) % 1.0   # 随时间+设备的色相\n"
        "    r, g, b = hsv(h, 1.0, 1.0)\n"
        "    c.set('R', r); c.set('G', g); c.set('B', b)\n"
        "    c.set('pump', 128 + 127 * sin(c.t * 3))\n"
        "```\n\n"
        "`c` 上下文成员：\n\n"
        + _table(
            ["成员", "含义"],
            [
                ["c.t", "本 clip 内时间（秒）"],
                ["c.i", "当前设备下标（0 基）"],
                ["c.n", "选中设备总数"],
                ["c.x, c.y", "当前设备归一化坐标"],
                ["c.dur", "clip 时长"],
                ["c.fps", "帧率"],
                ["c.p['名']", "取参数（等价标量里的同名变量）"],
                ["c['名'] / c.名", "取参数的简写（`c.spd` 等价 `c.p['spd']`）"],
                ["c.set(通道, 值)", "写一个通道（值自动夹取 0..255）"],
            ],
        )
        + "\n\n"
        "> **参数三种等价写法**：`param spd=2` 定义后，标量里直接写 `spd`；def 里"
        " `c.p['spd']`、`c['spd']`、`c.spd` 三者完全等价（裸名风格统一）。\n"
        "> **确定性**：两种写法都只能用沙箱里的纯函数（第 7 节）。禁止 `import`、"
        "`open`、真随机、读时间。要随机就用 `rand(seed)`、`noise(x)`。\n"
    )


def _sec_channel_types(project: Any) -> str:
    """第 5 节：通道类型表（项目感知）。

    - 有 project：列出本工程 fixture_defs 里真实出现的全部通道类型（含
      function_1/function_2/placeholder 等不在 ir.CHANNEL_TYPES 的自定义通道），
      并对其中能匹配到 ir.CHANNEL_TYPES 的给出中英别名。明确告诉模型：本工程
      任意存在的通道类型都可按名写入（如 `function_1: 255`）。
    - 无 project：降级为 ir.CHANNEL_TYPES 全表，并加说明。
    """
    known = ir.CHANNEL_TYPES
    # 规范名 -> 别名串（用于查别名）
    alias_of: Dict[str, str] = {c: _join(al, " / ") for c, al in known.items()}

    proj_types = _project_channel_types(project)

    if proj_types:
        rows = []
        for t in proj_types:
            try:
                canon = ir.normalize_channel(t)
            except Exception:
                canon = None
            if canon and canon in alias_of:
                rows.append([t, alias_of[canon], "本项目设备有此通道（标准通道）"])
            else:
                rows.append([t, "—", "本项目设备的自定义通道，可按名写入"])
        # 同时补充本项目未出现、但引擎仍认识的标准通道别名，便于跨工程复用
        present_canon = set()
        for t in proj_types:
            try:
                c = ir.normalize_channel(t)
            except Exception:
                c = None
            if c:
                present_canon.add(c)
        extra = [[c, alias_of[c], "标准通道别名（本项目设备未必带）"]
                 for c in known if c not in present_canon]
        body = _table(["本项目通道", "别名（中/英）", "说明"], rows)
        if extra:
            body += ("\n\n其余引擎认识的标准通道别名（写了但设备没有则被忽略）：\n\n"
                     + _table(["规范通道", "别名（中/英）", "说明"], extra))
        return (
            "## 5. 通道类型表（本项目真实通道）\n\n"
            "效果体里的通道名用下表任一写法（大小写不敏感）。**本工程 fixtures 里存在"
            "的任意通道类型都可按名直接写入**，例如 `function_1: 255`、`pump: 200`；"
            "标准通道还可用中英别名：\n\n"
            + body
            + "\n\n> 设备没有的通道写了也无效（会被静默忽略），见第 10 节禁忌。\n"
            "> 第 8.1 节设备表的「通道类型」列与此表一致。\n"
        )

    # 降级：无 project
    rows = [[canon, _join(aliases, " / ")] for canon, aliases in known.items()]
    return (
        "## 5. 通道类型表\n\n"
        "> 当前未提供 project，下表为引擎内置的标准通道类型。打开具体工程后重新导出，"
        "本节会替换为该工程 fixtures 里真实存在的通道（可能含 function_1/function_2/"
        "placeholder 等自定义通道，它们同样可按名写入）。\n\n"
        + _table(["规范通道", "别名（中/英）"], rows)
        + "\n\n> 设备没有的通道写了也无效（会被忽略），见第 10 节禁忌。\n"
    )


def _sec_directions_table() -> List[List[str]]:
    rows = []
    for canon, (zh, en) in ir.DIRECTIONS.items():
        rows.append([
            en[0] if en else canon,        # 规范英文写法
            _join(zh, " / "),
            _join(en, " / "),
        ])
    return rows


def _sec_clip_settings() -> str:
    # 每个属性关键字：规范键 | 中英别名 | 含义 | 例
    meaning = {
        "slct": ("选设备（见第 3 节三种方案）", "slct 1-8;  /  slct 环1.2-4;"),
        "at": ("起始时间（秒）。不写则接在上一 clip 之后", "at 4s;"),
        "dur": ("时长（秒）", "dur 6s;"),
        "fi": ("淡入时长（秒）", "fi 0.5;"),
        "fo": ("淡出时长（秒）", "fo 0.5;"),
        "dir": ("铺开方向（见下方 16 方向表）", "dir 从左到右;  /  dir cw;"),
        "sets": ("把选中设备切成几个分组", "sets 4;"),
        "seq": ("分组依次执行（顺序模式）", "seq;"),
        "parallel": ("分组同时执行（默认）", "parallel;"),
        "pause": ("分组之间的停顿（秒）", "pause 0.3;"),
        "batches": ("把分组再并成几批；0=每组一批", "batches 2;"),
        "scope": ("坐标系：per_shape/global/follow", "scope global;"),
        **WING_HELP,
        "param": ("定义命名参数，可在表达式/ def 里当变量用（见 6.5）", "param amp=120 spd=2;"),
        "seq_delay": ("依次延迟（秒），淡入淡出错峰", "seq_delay 0.2;"),
        "fade_ratio": ("分通道过渡比（0..1，按通道）", "fade_ratio pump=0.8;"),
        "track_light": ("灯跟踪：让灯随泵/阀亮灭", "track_light pump;"),
        "motorphase": ("电机相位差（让数控错相）", "motorphase 1.0;"),
        "hold": ("结束保持：none/last_frame/last_cycle", "hold last_frame;"),
        "rate": ("播放倍速", "rate 2;"),
        "smooth": ("自动平滑预设", "smooth on;"),
        "rec_src": ("作为录制接收源", "rec_src on;"),
        "def": ("def 全式效果函数（见 4.2）", "def effect(c): ..."),
    }
    rows = []
    for canon, aliases in ir.KEYWORDS.items():
        mean, ex = meaning.get(canon, ("", ""))
        rows.append([canon, _join(aliases, " / "), mean, ex])

    dir_rows = _sec_directions_table()

    # scope / hold 子表
    scope_rows = [[c, _join(al, " / ")] for c, al in ir.SCOPE_ALIASES.items()]
    hold_rows = [[c, _join(al, " / ")] for c, al in ir.HOLD_ALIASES.items()]
    track_rows = [[c, _join(al, " / ")] for c, al in ir.TRACK_SOURCE_ALIASES.items()]

    # 6.5 翼/块/翼方向/参数（别名行全部程序化自 ir.KEYWORDS / ir.WING_DIR_ALIASES）
    wb_keys = ["wings", "block", "wingdir", "wing_mirror", "wing_reverse", "param"]
    wb_rows = [[k, _join(ir.KEYWORDS.get(k, []), " / ")] for k in wb_keys]
    wingdir_rows = [[canon, _join(al, " / ")] for canon, al in ir.WING_DIR_ALIASES.items()]

    return (
        "## 6. clip 全部可设置项\n\n"
        "块体里每行的引导关键字（规范键 + 中英别名）一览。除了 `slct` 与 通道/def "
        "效果体，其余都是「调度/过渡」属性：\n\n"
        + _table(["规范键", "别名（中/英）", "含义", "示例"], rows)
        + "\n\n> **单位规则**：时间字段（`at`/`dur`/`fi`/`fo`/`pause`/`seq_delay`）可带 `s`"
        "（秒）；计数/比例字段（`sets`/`wings`/`block`/`batches`/`rate`）是纯数，**不可**"
        "带 `s`（`sets 2s` 报错）。\n"
        "> **属性 vs 通道**：调度属性写 `关键字 值`（空格，无冒号），如 `dur 8`；带冒号的 "
        "`dur: 8` 会被当成通道误用而报错（冒号只用于「通道: 表达式」）。\n"
        "> **track_light 阈值**：`track_light pump@128` 与 `track_light pump=128` 等价"
        "（`=` 是 `@` 的别名）。\n\n"
        "### 6.1 方向 dir（16 种，一个不少）\n\n"
        + _table(["规范值", "中文别名", "英文别名"], dir_rows)
        + "\n\n### 6.2 坐标系 scope\n\n"
        + _table(["规范值", "别名"], scope_rows)
        + "\n\n- `per_shape`（默认）：每个选择组用自己的局部坐标系铺方向。\n"
        + "- `global`：所有设备放进同一个整体坐标系。\n"
        + "- `follow`：跟随全局灯控坐标系。\n"
        + "\n### 6.3 保持 hold\n\n"
        + _table(["规范值", "别名"], hold_rows)
        + "\n\n### 6.4 灯跟踪来源 track_light\n\n"
        + _table(["规范值", "别名"], track_rows)
        + "\n\n### 6.5 翼 / 块 / 翼方向 / 参数（wings / block / wingdir / param）\n\n"
        "这组是 **effect 引擎通用的空间分组**：渲染期把选中设备序列折成若干「虚拟"
        "同步单元」，从而让一段波形在空间上复制 / 阶梯展开。引导关键字（中英别名）：\n\n"
        + _table(["规范键", "别名（中/英）"], wb_rows)
        + "\n\n- **`wings N`（翼）**：把选中设备序列切成 N 段，**每段跑完全相同的波形**"
        "（例如 `wings 2` = 左右两组同步喷泉，像两把对称的扇子）。它改变效果代码里 "
        "`i`/`n` 的含义——`i` 变成**虚拟单元内的下标**，`n` 变成**单元数**，"
        "于是同一段波形在每个翼里重演一次。\n"
        "- **`block N`（块）**：相邻 N 台设备**共用同一个单元值**，形成阶梯（staircase）。"
        "也会把设备折进虚拟单元：每 N 台算一个单元，单元内取同值。`wings` 与 `block` "
        "可叠加。\n"
        "- **`wingdir <方向>`（翼内方向）**：决定每个翼内部波形铺开的方向，取值如下表"
        "（符号 `/ \\ < >` 与单词别名等价）：\n\n"
        + _table(["规范值", "别名（含符号）"], wingdir_rows)
        + "\n\n  `/`=低到高(low_to_high)、`\\`=高到低(high_to_low)、`<`=两端向中(high_low)、"
        "`>`=中向两端(low_high)。\n"
        "- **`param k=v ...`（参数）**：一次可设多组 `名=值`，定义后：在标量表达式里"
        "**直接当变量名 `k` 用**；在 `def` 代码里用 `c.p['k']`（或简写 `c['k']`）取。"
        "便于一处调参、多处复用，且保持确定性。\n\n"
        "用法片段（把这些行写进任意 `clip` 块体即可）：\n\n"
        "```\n"
        "wings 2;            # 切成两个同步翼，各跑同一波形\n"
        "block 3;            # 相邻 3 台共用一个阶梯值\n"
        "wingdir /;          # 翼内 低->高（也可写 \\ < > 或 低到高/中聚/中扩）\n"
        "param amp=120 spd=2;  # 定义参数 amp、spd\n"
        "pump: 128 + amp*sin(t*spd);   # 标量里直接用参数名\n"
        "```\n\n"
        "```\n"
        "def effect(c):\n"
        "    a = c['amp']                 # 等价 c.p['amp'] / c.amp\n"
        "    c.set('pump', 128 + a*sin(c.t*c.spd + c.i*0.5))  # c.spd 直读 param\n"
        "```\n"
        "\n> 完整的 wings+block+wingdir 与 param 综合示例见第 9 节例6 / 例7。\n"
    )


def _sec_funcs() -> str:
    names, note = _collect_sandbox_funcs()
    head = "## 7. 安全函数清单\n\n效果体里**只能**调用下列纯函数 " + note + "：\n\n"
    if names is not None:
        # 实读：直接列名（分块每行若干个）
        cols = 4
        rows = []
        for k in range(0, len(names), cols):
            rows.append(names[k:k + cols] + [""] * (cols - len(names[k:k + cols])))
        body = _table([f"可用名 {i+1}" for i in range(cols)], rows)
        return head + body + "\n"
    # 兜底：分类表
    parts = [head]
    for cat, items in _FALLBACK_FUNCS.items():
        parts.append(f"### {cat}\n\n" + _table(["名称", "说明"], [[a, b] for a, b in items]) + "\n")
    return "\n".join(parts)


def _layout_by_sheets(project: Any, def_names: Dict[Any, str]) -> str:
    """生成各 TopView 页的设备坐标表（相对坐标，辅助判定布局）。

    格式：每页一个小节，表格列：全局# · 设备类型 · x · y
    坐标值保留整数（工程单位，通常 px），用于判断相对位置关系。
    """
    tv = getattr(project, "topview_state", None) or {}
    pages = (tv.get("pages") or [])
    if not pages:
        return ""

    # 全局序号：fixture id → (global#, def_name)
    fixtures = getattr(project, "fixtures", None) or []
    fid_to_g1: Dict[Any, int] = {}
    fid_to_defname: Dict[Any, str] = {}
    for idx, fx in enumerate(fixtures, start=1):
        fid = _get(fx, "id")
        did = _get(fx, "def_id") or _get(fx, "fixture_def_id")
        if fid is not None:
            fid_to_g1[fid] = idx
            fid_to_defname[fid] = def_names.get(did, "?")

    parts = []
    for pg_idx, pg in enumerate(pages):
        pg_name = pg.get("name") or f"页{pg_idx + 1}"
        ff = pg.get("fixtures") or {}
        if not isinstance(ff, dict):
            continue
        # 只取本页真实摆放的设备（visible_fixture_ids 是权威列表）
        vis_ids = set(str(fid) for fid in (pg.get("visible_fixture_ids") or []))
        rows = []
        for fid, v in ff.items():
            if str(fid) not in vis_ids:
                continue  # 该页未摆放此设备
            if not isinstance(v, dict):
                continue
            x = v.get("x")
            y = v.get("y")
            if x is None or y is None:
                continue
            try:
                xi, yi = round(float(x)), round(float(y))
            except (TypeError, ValueError):
                continue
            g1 = fid_to_g1.get(fid)
            dname = fid_to_defname.get(fid, "?")
            if g1 is not None:
                rows.append((g1, dname, xi, yi))
        if not rows:
            continue
        rows.sort(key=lambda r: r[0])
        tbl = _table(
            ["全局#", "设备类型", "x（参考）", "y（参考）"],
            [[r[0], r[1], r[2], r[3]] for r in rows],
        )
        parts.append(f"#### TopView · {pg_name}\n\n{tbl}")

    if not parts:
        return ""
    return "\n\n".join(parts)


def _sec_project(project: Any, profile: Any, def_names: Dict[Any, str], patch: Dict) -> str:
    if project is None:
        return (
            "## 8. 本项目真实信息\n\n"
            "> 当前未提供 project 对象，**项目相关的设备/编组/配接表不可用**。"
            "请在打开具体工程后重新导出本文档以获得真实设备清单。前面的语法、关键字、"
            "函数与示例均与项目无关，可直接学习使用。\n"
        )

    parts = ["## 8. 本项目真实信息\n"]

    # 8.0 布局摘要（一行概览 + 各页 XY 坐标表）
    summary_text = ""
    if profile is not None:
        try:
            summary_text = profile.summary_text()
        except Exception:
            pass
    sheets_text = ""
    try:
        sheets_text = _layout_by_sheets(project, def_names)
    except Exception:
        pass
    if summary_text or sheets_text:
        sec = "### 8.0 布局摘要\n\n"
        if summary_text:
            sec += summary_text + "\n\n"
        if sheets_text:
            sec += (
                "各 TopView 页的设备相对坐标（画布单位，整数，**仅供相对位置参考**）如下，"
                "同一设备可出现在多页中——坐标反映该页的摆放位置，可据此判断布局关系"
                "（如同页 x 相近 = 横向并排，y 相近 = 纵向齐平）。"
                "坐标绝对值无实际物理意义，只用来区分相对远近：\n\n"
                + sheets_text
            )
        parts.append(sec)

    # 8.1 全局序号表
    fixtures = _get(project, "fixtures", []) or []
    total = len(fixtures)
    # 统计每类型计数
    type_count: Dict[str, int] = {}
    rows = []
    LIMIT = 40
    for idx, fx in enumerate(fixtures, start=1):
        fid = _get(fx, "id")
        did = (_get(fx, "def_id") or _get(fx, "fixture_def_id"))
        tname = def_names.get(did, "?")
        type_count[tname] = type_count.get(tname, 0) + 1
        if idx <= LIMIT:
            pe = patch.get(fid) if patch else None
            if pe:
                uni = pe.get("universe", "?")
                chmap = pe.get("ch", {}) or {}
                # 起始通道 = 最小 0-based addr + 1
                start = (min(chmap.values()) + 1) if chmap else "?"
                chtypes = _join(list(chmap.keys()), ",")
            else:
                uni = _get(fx, "universe", "?")
                start = _get(fx, "start_channel", "?")
                chtypes = "?"
            rows.append([idx, tname, uni, start, chtypes])

    parts.append(
        "### 8.1 全局序号表（`slct 序号`）\n\n"
        + _table(["全局#", "设备类型(def)", "Universe", "起始通道", "通道类型"], rows)
    )
    if total > LIMIT:
        cnt_rows = [[t, c] for t, c in sorted(type_count.items(), key=lambda kv: -kv[1])]
        parts.append(
            f"\n> 共 **{total}** 台设备，仅列出前 {LIMIT} 台；完整清单请在工程内查阅配接表。"
            "按类型汇总：\n\n"
            + _table(["设备类型(def)", "数量"], cnt_rows)
        )
    else:
        parts[-1] = parts[-1] + f"\n\n> 共 {total} 台设备。\n"

    # 8.2 编组表
    fixture_groups = _get(project, "fixture_groups", []) or []
    gp_by_name: Dict[str, Any] = {}
    if profile is not None:
        for g in getattr(profile, "groups", []) or []:
            gp_by_name[g.name] = g
    # 全局序号查找：fid -> 全局#
    fid_to_global = {}
    for idx, fx in enumerate(fixtures, start=1):
        fid_to_global[_get(fx, "id")] = idx

    grows = []
    for g in fixture_groups:
        gname = _get(g, "name", "")
        mids = _get(g, "fixture_ids", []) or []
        gp = gp_by_name.get(gname)
        shape = _SHAPE_ZH(getattr(gp, "shape", "unknown")) if gp else "未知"
        radius = _fmt_num(getattr(gp, "radius", 0.0)) if gp else "0"
        count = len(mids)
        # 前几个成员的全局序号
        firsts = [fid_to_global.get(m) for m in mids[:6] if fid_to_global.get(m)]
        firsts_s = _join([str(x) for x in firsts], ",")
        if count > 6:
            firsts_s += ",…"
        grows.append([gname, count, shape, radius, firsts_s])

    if grows:
        parts.append(
            "\n### 8.2 编组表（`slct 组名` / `slct 组名.下标`）\n\n"
            + _table(["组名", "成员数", "形态", "半径", "成员全局#(前几个)"], grows)
        )
    else:
        parts.append("\n### 8.2 编组表\n\n> 本项目暂无编组。\n")

    # 8.3 音频/BPM
    audio = _get(project, "audio_path", "") or ""
    bpm = _get(project, "bpm", None)
    aud_lines = ["\n### 8.3 音频信息\n"]
    aud_lines.append(f"- 音频文件：{audio if audio else '（未设置）'}")
    if bpm:
        aud_lines.append(f"- BPM：{_fmt_num(bpm)}（一拍 ≈ {_fmt_num(60.0/float(bpm))}s，方便对齐 at/dur）")
    else:
        aud_lines.append("- BPM：（未设置）")
    parts.append("\n".join(aud_lines) + "\n")

    return "\n".join(parts)


def _track_for(chan: str) -> str:
    """按通道给一个贴切的默认轨名（仅作示例标签，不影响渲染）。"""
    return {"pump": "泵", "valve": "阀", "dimmer": "灯",
            "R": "灯", "G": "灯", "B": "灯"}.get(chan, "命令")


def _sec_examples(project: Any, caps: Optional[Dict[str, Any]] = None) -> str:
    """第 9 节：示例（能力感知）。

    每个 fenced clip 示例都先用 _select_for 挑「所选设备确实有该通道」的选择器，
    并只用本项目真实存在、引擎能规范化的通道；某角色通道全项目缺失则改用可用通道
    或整例省略。保证每个示例在任何真实工程上都能解析并渲染出 >0 个 clip。
    """
    if caps is None:
        caps = _build_caps(project)
    n = int(caps.get("n", 0) or 0)

    inten = _pick_role_channel(caps, "intensity") or "dimmer"
    color = _pick_role_channel(caps, "color")     # 'RGB' or None
    pos = _pick_role_channel(caps, "position")    # 'motor' or None
    avail: set = caps.get("avail") or set()
    track_src = "pump" if "pump" in avail else ("valve" if "valve" in avail else None)

    # 各示例的选择器（保证成员真有对应通道）
    sel_int, d_int = _select_for(caps, [inten])
    if not sel_int:
        sel_int = f"1 thru {min(max(n, 1), _MAX_SEL)}"
        d_int = "全局序号"
    sel_rgb, d_rgb = _select_for(caps, ["R", "G", "B"]) if color else ("", "")
    sel_motor, d_motor = _select_for(caps, ["motor_x", "motor_y"]) if pos else ("", "")
    # 灯跟踪示例：必须同时写 source 通道(pump/valve)+被跟踪的灯，挑两者都有的设备
    if track_src:
        if color:
            sel_track, d_track = _select_for(caps, [track_src, "R", "G", "B"])
        else:
            sel_track, d_track = _select_for(caps, [track_src])
    else:
        sel_track, d_track = ("", "")

    desc = []
    if d_int:
        desc.append(f"强度示例选择器={d_int}")
    note = "（已按本项目真实配接挑选「确有该通道」的设备" + (
        "；无编组时用全局序号" if not (caps.get("groups")) else "") + "）"

    parts = [
        "## 9. 示例（简单 → 复杂，均可直接复制）\n",
        f"> 下列示例已针对本项目挑选了**确实拥有目标通道**的设备与真实通道{note}，"
        "复制即可解析与渲染（每个示例必渲染出 >0 个 clip）。换工程后重新导出即自动"
        "改用该工程的设备/通道。\n",
    ]

    # 例1 · 常数（强度通道）
    parts.append(
        "### 例1 · 单一常数喷泉（标量常数）\n\n"
        "```\n"
        f"clip \"全开\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    at 0s; dur 4s;\n"
        f"    {inten}: 220;\n"
        "}\n"
        "```\n"
    )

    # 例2 · 山形 + 淡入淡出 + 方向（标量 i/n）
    parts.append(
        "### 例2 · 标量「山形」+ 淡入淡出 + 方向\n\n"
        "山形：中间高两边低，用设备下标 i 做对称抛物线。\n\n"
        "```\n"
        f"clip \"山形水墙\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    at 4s; dur 6s;\n"
        "    fi 0.8; fo 0.8;\n"
        "    dir 中心向两边;\n"
        "    # i 居中归一化到 -1..1，再用 1-x^2 做山形\n"
        f"    {inten}: 255 * (1 - pow((2*i/max(n-1,1) - 1), 2));\n"
        "}\n"
        "```\n"
    )

    # 例3 · 呼吸（有 RGB 用彩灯；否则强度呼吸）
    if color:
        parts.append(
            "### 例3 · 标量呼吸彩灯（多通道标量）\n\n"
            "```\n"
            f"clip \"呼吸蓝\" -> track \"灯\" {{\n"
            f"    slct {sel_rgb};\n"
            "    dur 8s;\n"
            "    R: 0;\n"
            "    G: 60 + 60*sin(t*pi);\n"
            "    B: 128 + 127*sin(t*pi);\n"
            "}\n"
            "```\n"
        )
    else:
        parts.append(
            "### 例3 · 标量呼吸（本项目无 RGB，改用强度通道呼吸）\n\n"
            "```\n"
            f"clip \"呼吸\" -> track \"{_track_for(inten)}\" {{\n"
            f"    slct {sel_int};\n"
            "    dur 8s;\n"
            f"    {inten}: 128 + 127*sin(t*pi);\n"
            "}\n"
            "```\n"
        )

    # 例4 · def 全式 + 顺序分组 + 方向 + 坐标系 scope（彩虹/或强度波）
    if color:
        parts.append(
            "### 例4 · def 全式彩虹追逐 + 顺序分组(sets/seq/pause) + 方向 + scope\n\n"
            "```\n"
            f"clip \"彩虹追逐\" -> track \"灯\" {{\n"
            f"    slct {sel_rgb};\n"
            "    at 10s; dur 8s;\n"
            "    sets 4; seq; pause 0.1;   # 切 4 组，依次执行，组间停 0.1s\n"
            "    scope global;             # 所有设备放进同一整体坐标系\n"
            "    dir 从左到右;\n"
            "    def effect(c):\n"
            "        h = (c.t / c.dur + c.i / max(c.n, 1)) % 1.0\n"
            "        r, g, b = hsv(h, 1.0, 1.0)\n"
            "        c.set('R', r); c.set('G', g); c.set('B', b)\n"
            "}\n"
            "```\n"
        )
    else:
        parts.append(
            "### 例4 · def 全式波动 + 顺序分组(sets/seq/pause) + 方向 + scope\n\n"
            "```\n"
            f"clip \"波动追逐\" -> track \"{_track_for(inten)}\" {{\n"
            f"    slct {sel_int};\n"
            "    at 10s; dur 8s;\n"
            "    sets 4; seq; pause 0.1;   # 切 4 组，依次执行，组间停 0.1s\n"
            "    scope global;             # 所有设备放进同一整体坐标系\n"
            "    dir 从左到右;\n"
            "    def effect(c):\n"
            "        v = 128 + 120 * sin(c.t * 2 + c.i * 0.6)\n"
            f"        c.set('{inten}', v)\n"
            "}\n"
            "```\n"
        )

    # 例5 · 数控画圆（仅当 motor_x+motor_y 都在）
    if pos and sel_motor:
        # 圆 + 若有 pump 顺带写强度（仍只在确有 motor 的设备上）
        extra = ""
        if "pump" in avail:
            # 用既有 motor 又有 pump 的设备
            s2, _ = _select_for(caps, ["motor_x", "motor_y", "pump"])
            if s2:
                sel_motor = s2
                extra = "        c.set('pump', 180 + 60*sin(a*2))\n"
        parts.append(
            "### 例5 · 数控电机画圆（def 写 motor_x/motor_y）+ 末帧保持\n\n"
            "```\n"
            f"clip \"旋转水柱\" -> track \"电机\" {{\n"
            f"    slct {sel_motor};\n"
            "    dur 10s;\n"
            "    dir 顺时针;\n"
            "    hold last_frame;         # 结束停在最后一帧\n"
            "    def effect(c):\n"
            "        a = c.t / c.dur * 2 * pi + c.i * 2*pi / max(c.n,1)\n"
            "        c.set('motor_x', 128 + 100*cos(a))\n"
            "        c.set('motor_y', 128 + 100*sin(a))\n"
            + extra
            + "}\n"
            "```\n"
        )

    # 例6 · 灯跟踪 track_light（仅当有 pump/valve；同 clip 必写 source 通道）
    if track_src and sel_track:
        if color:
            body = ("    R: 0;\n"
                    "    G: 80;\n"
                    "    B: 200;                       # 被跟踪的灯\n"
                    f"    {track_src}: 180 + 60*sin(t*3);   # 必写 source 通道，灯随它亮灭\n")
        else:
            body = (f"    {track_src}: 180 + 60*sin(t*3);   # source 通道；灯随它亮灭\n")
        parts.append(
            "### 例6 · 灯跟踪 track_light（灯随泵/阀亮灭）\n\n"
            f"> 关键：开了 `track_light` 的 clip **必须同时写 `{track_src}`（source）通道**，"
            "否则灯会被跟踪逻辑压黑（引擎会警告并自动关闭跟踪）。\n\n"
            "```\n"
            f"clip \"跟随起伏\" -> track \"{_track_for(track_src)}\" {{\n"
            f"    slct {sel_track};\n"
            "    dur 8s;\n"
            f"    track_light {track_src};      # 灯随 {track_src} 亮灭\n"
            + body
            + "}\n"
            "```\n"
        )

    # 例7 · wings + block + wingdir 综合
    parts.append(
        "### 例7 · 翼 wings + 块 block + 翼方向 wingdir（两组同步扇形，块内阶梯）\n\n"
        "`wings 2` 把设备切成左右两个同步翼，`block 2` 让相邻两台共用一档，"
        "`wingdir /` 让每个翼内从低到高铺开（详见 6.5）：\n\n"
        "```\n"
        f"clip \"双扇阶梯\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    dur 6s;\n"
        "    wings 2;          # 两个同步翼\n"
        "    block 2;          # 相邻两台共用一个单元值\n"
        "    wingdir /;        # 翼内 低->高\n"
        "    # i/n 此时是「虚拟单元」下标/数：同一波形在每个翼里重演\n"
        f"    {inten}: 80 + 175 * (i / max(n-1, 1));\n"
        "}\n"
        "```\n"
    )

    # 例8 · param（一处调参，多处复用）
    parts.append(
        "### 例8 · 参数 param（一处调参，多处复用）\n\n"
        "`param` 定义的名字在标量里直接当变量、在 def 里用 `c['名']`：\n\n"
        "```\n"
        f"clip \"参数化波\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    dur 8s;\n"
        "    param amp=120 spd=2 base=128;   # 三个参数\n"
        f"    {inten}: base + amp * sin(t * spd);   # 标量里直接用\n"
        "}\n"
        "```\n\n"
        "在 def 里取参数（与上等价的写法）：\n\n"
        "```\n"
        f"clip \"参数化波2\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    dur 8s;\n"
        "    param amp=120 spd=2 base=128;\n"
        "    def effect(c):\n"
        f"        c.set('{inten}', c['base'] + c['amp'] * sin(c.t * c['spd']))\n"
        "}\n"
        "```\n"
    )

    # 例9 · 多通道 fan-out（同一表达式赋多个通道）
    if color:
        sel_fan, _ = _select_for(caps, ["R", "G", "B"])
        parts.append(
            "### 例9 · 多通道 fan-out（`R,G,B: 同一表达式`）\n\n"
            "逗号列出多个通道，同一表达式同时写入（= 分别写三行同值）：\n\n"
            "```\n"
            "clip \"三色同步呼吸\" -> track \"灯\" {\n"
            f"    slct {sel_fan};\n"
            "    dur 6s;\n"
            "    R,G,B: 128 + 100*sin(t*pi);   # 三色同值，整体明暗呼吸\n"
            "}\n"
            "```\n"
        )
    else:
        parts.append(
            "### 例9 · 多通道 fan-out（`通道,通道: 同一表达式`）\n\n"
            "逗号列出多个通道，同一表达式同时写入。本项目无 RGB，"
            "用强度通道演示（单通道时退化为普通标量）：\n\n"
            "```\n"
            f"clip \"齐射呼吸\" -> track \"{_track_for(inten)}\" {{\n"
            f"    slct {sel_int};\n"
            "    dur 6s;\n"
            f"    {inten}: 128 + 100*sin(t*pi);\n"
            "}\n"
            "```\n"
            "> 若设备同时有多个可写通道（如 `pump`+`valve`），可写 `pump,valve: 表达式` 让它们同步。\n"
        )

    # 例10 · slct all + slct type（整体/按能力选择）
    type_ch = inten if (not avail or inten in avail) else sorted(avail)[0]
    parts.append(
        "### 例10 · 整体选择 `slct all` / 按能力 `slct type`\n\n"
        "`all` 选全部设备；`type <通道>` 选「配接含该通道」的全部设备，跨工程通用：\n\n"
        "```\n"
        f"clip \"全场齐亮\" -> track \"{_track_for(inten)}\" {{\n"
        "    slct all;            # 本项目全部设备\n"
        "    dur 4s;\n"
        f"    {inten}: 200;\n"
        "}\n"
        "\n"
        f"clip \"所有{type_ch}\" -> track \"{_track_for(type_ch)}\" {{\n"
        f"    slct type {type_ch};      # 所有含 {type_ch} 通道的设备\n"
        "    dur 6s;\n"
        f"    {type_ch}: 128 + 120*sin(t*2);\n"
        "}\n"
        "```\n"
    )

    # 例11 · default 默认块（公共属性下沉，clip 自身覆盖）
    parts.append(
        "### 例11 · 默认块 `default`（公共属性下沉，clip 自身覆盖）\n\n"
        "顶层 `default{ }` 设公共属性作其后所有 clip 的默认；clip 显式写的覆盖默认：\n\n"
        "```\n"
        "default {                 # 后续每个 clip 默认 dur 6s、淡入淡出 0.4s\n"
        "    dur 6s; fi 0.4; fo 0.4;\n"
        "    dir 中心向两边;\n"
        "}\n"
        f"clip \"承默认\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        f"    {inten}: 200;          # 用默认 dur6 / fi0.4 / fo0.4 / dir\n"
        "}\n"
        f"clip \"覆盖时长\" -> track \"{_track_for(inten)}\" {{\n"
        f"    slct {sel_int};\n"
        "    dur 3s;                # 自身 dur 覆盖默认的 6s\n"
        f"    {inten}: 120;\n"
        "}\n"
        "```\n"
    )

    return "\n".join(parts)


def _sec_pitfalls() -> str:
    return (
        "## 10. 常见错误与禁忌\n\n"
        "- **漏闭合大括号**：每个 `clip{` 必须有配对的 `}`。\n"
        "- **未知方向**：`dir` 只接受第 6.1 节 16 种里的中英别名，拼错会报错。\n"
        "- **禁止非确定性**：不要用真随机 / 读时间 / 读文件。要随机感用 `rand(seed)`、"
        "`noise(x)`。\n"
        "- **禁止危险调用**：`import`、`open`、`eval`、文件/网络等一律禁用，沙箱会拒绝。\n"
        "- **数值越界无需手动夹**：写出的通道值自动夹到 0..255；但表达式里别用会爆的"
        "运算（如除 0），用 `max(分母,1)` 防护。\n"
        "- **通道与设备不匹配**：给没有该通道的设备写 `motor_x`/`R` 等会被静默忽略——"
        "先看第 5 节通道表和第 8 节设备类型，确认目标设备真有这个通道。\n"
        "- **slct 越界**：全局序号 / 组内下标都是 1 基，超出范围的部分会被忽略；范围写"
        "反（如 `8-3`）不会选到东西。\n"
        "- **忘了 dur**：没写 `dur` 会用默认时长(4s)；没写 `at` 会接在上一条之后——"
        "想并排同时播就显式写相同的 `at`。\n"
        "- **标量与 def 混写**：一个通道要么用 `通道: 表达式`，要么在 `def` 里 `c.set`，"
        "别对同一通道两头写。\n\n"
        "### 10.1 写给 AI 的高频坑（务必规避）\n\n"
        "- **属性关键字误当通道**：调度属性写成 `关键字 值`（空格、无冒号）。`dur: 8`、"
        "`dir: ltr` 会报错——冒号只用于「通道: 表达式」。正确：`dur 8;` `dir ltr;`。\n"
        "- **整组后不能直接跟 step/count**：`slct 环1 step 2` 报错（整组没有可步进的下标）。"
        "要组内步进请写 `slct 环1.1 thru N step S`（带 `.下标`）。\n"
        "- **`at +2s` 相对偏移暂不支持**：`at` 只接受绝对秒（`at 4s`）。想接在上一条之后，"
        "**直接省略 `at`** 即可自动顺延；不要写 `at +2s`。\n"
        "- **clip 块头多余 token 会报错**：块头只允许 `clip \"标签\" [-> track \"名\"]`。"
        "标签后写别的（如 `clip \"a\" 8`）会报错；轨道必须用 `-> track \"名\"`。\n"
        "- **计数/比例字段不可带单位**：`sets 2s` / `wings 3s` / `rate 2s` 都报错；"
        "只有时间字段能带 `s`（见 2.2）。\n"
        "- **不写 dur**：会用默认 4s（仅提示不报错）；为可读性与时间线对齐，建议显式写 `dur`。\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  公共 API
# ─────────────────────────────────────────────────────────────────────────────
def build_skill_markdown(project: Any) -> str:
    """生成完整的教学 Markdown 字符串。

    对缺失/不完整的 project 字段保持健壮：画像/配接构建包在 try/except 里，
    失败则优雅降级（无 project 时仍输出完整的语法/关键字/函数/示例章节，并标注
    项目相关设备表不可用）。
    """
    # 项目相关数据（容错）
    profile = None
    patch: Dict[str, Any] = {}
    def_names: Dict[Any, str] = {}
    if project is not None:
        try:
            def_names = _def_name_map(project)
        except Exception:
            def_names = {}
        try:
            from ..auto_arrange.project_profile import build_project_profile
            profile = build_project_profile(project)
        except Exception:
            profile = None
        try:
            from ..auto_arrange.voice_clip_renderer import build_patch
            patch = build_patch(project) or {}
        except Exception:
            patch = {}

    # 能力感知数据（一次构建，§2/§3/§9 共享，挑「确有该通道」的选择器）
    try:
        caps = _build_caps(project)
    except Exception:
        caps = _build_caps(None)

    name = _get(project, "name", "") or ""
    title = f"# 命令行代码编曲 · 编程手册" + (f"（项目：{name}）" if name else "")

    sections = [
        title,
        "",
        "> 本文档由系统从代码契约自动生成，关键字/方向/通道表与引擎保持同步。读完即可"
        "化身本喷泉系统的自动编曲程序员，直接产出合法的 clip 命令。",
        "",
        _sec_role(),
        _sec_changelog(),
        _sec_grammar_overview(project, caps),
        _sec_select(project, profile, def_names, caps),
        _sec_bodies(),
        _sec_channel_types(project),
        _sec_clip_settings(),
        _sec_funcs(),
        _sec_project(project, profile, def_names, patch),
        _sec_examples(project, caps),
        _sec_pitfalls(),
    ]
    return "\n\n".join(s for s in sections if s is not None)


def suggested_filename(project: Any) -> str:
    """返回 '编曲命令_<项目名>.md'（净化非法字符），无项目名时 '编曲命令.md'。

    仅文件名，不含路径。
    """
    name = _get(project, "name", "") or ""
    name = str(name).strip()
    if not name:
        return "编曲命令.md"
    # 净化 Windows 文件名非法字符
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad else ch for ch in name)
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        return "编曲命令.md"
    return f"编曲命令_{cleaned}.md"
