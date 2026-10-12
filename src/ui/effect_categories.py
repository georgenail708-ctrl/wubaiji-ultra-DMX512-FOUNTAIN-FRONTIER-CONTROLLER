# -*- coding: utf-8 -*-
"""src/ui/effect_categories.py — 效果库两级分类映射与检索（R180 分类优化）。

痛点：细分类已膨胀到 23 个（含 4 个未入 CATEGORY_ORDER 的游离类），旧 UI
把它们塞进一个按 Unicode 码点排序的下拉 —— 顺序近似随机、一屏看不完。

本模块在**不动** ``preset.category``（细分类，已写进预设元数据、
``EffectNode.category``、场景存档 —— 改动即破坏旧工程兼容）的前提下叠加
一级大类：每个规范细分类恰好归入一个大类；未知/未来新分类经前缀兜底落
「其它」，保证"每个已注册效果恰有一个一级归属"的完备性不变量
（tests/test_r180_effect_cats.py 对该不变量做全库断言）。

归并原则（按真实内容，见 stats：1220 在库预设）：
  灯光 143 / 通用 366 / 音频 94 / 电机 383 / 水景 161 / 矩阵 73 / 其它（自定义）
  「泵/矩阵」目标通道是 pump/valve（泵类设备的矩阵编排）→ 归水景而非矩阵。
  ★ R182：旧「编排·高级」大类已并入「通用」（label 不再造新词）——用户原话
    "我要精简，不是找不到分类"：一级大类数不变（7），但词表全部复用既有分类名。
  游离细分类（不在 effect_engine.CATEGORY_ORDER 里）不归堆到末尾，按 `_fine_display_order`
  的**语义位次**插入（泵/矩阵 收在水景尾、矩阵三兄弟收在末尾），见 FINE 排序表。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .effect_engine import (
    CATEGORY_ORDER,
)
from .effect_catalog_curation import display_category as normalize_effect_category
from .effect_catalog_curation import catalog_filter, display_name

#: 一级「最近使用」伪大类键（不进 TOP_GROUPS，由 UI 单独置顶）
TOP_RECENT = "recent"
#: ★ R183 批 A 一级「收藏」伪大类键（与 TOP_RECENT 同机制：不进 TOP_GROUPS，
#:   由 UI 单独置顶、零计数不显示；数据源是用户目录 JSON 的 favorites 表）
TOP_FAV = "fav"
#: 「其它」大类键：所有未能映射的细分类的最终归属（计数为 0 时 UI 不显示）
TOP_OTHER = "other"
#: 最近使用分组的列表头文本（与 effect_browser.CAT_COLORS 键一致）
RECENT_HEADER = "最近使用"
#: 收藏分组的列表头文本
FAV_HEADER = "收藏"
#: 用户自定义预设的落库分类（CATEGORY_ORDER 既有细分类，「其它」大类显式成员；
#: 此前在库计数恒为 0、UI 不显示，R183 批 A 起由用户另存填满）
USER_CATEGORY = "自定义/我的效果"


@dataclass(frozen=True)
class EffectTopGroup:
    """一级大类：细分类集合 + 展示元数据。

    ``color`` 复用效果库既有分类色值（见 effect_browser.CAT_COLORS）——
    不引入新色值，header 竖条与旧分类色同一色系。
    """

    key: str
    label: str
    icon: str          # tabler 图标名（icon_assets/outline 下已核实存在）
    color: str
    members: Tuple[str, ...]


TOP_GROUPS: Tuple[EffectTopGroup, ...] = (
    EffectTopGroup("light", "灯光", "bulb", "#FFCC44",
                   ("灯光/颜色", "灯光/明暗", "灯光/跑灯", "灯光/矩阵")),
    EffectTopGroup("motion", "通用", "wave-sine", "#44AAFF",
                   ("通用/波形", "通用/跑灯", "通用/渐变",
                    "通用/随机", "通用/变速", "通用/级联",
                    # ★ R182：原「编排·高级」两个细分类并入「通用」（不再造新词）
                    "通用/编排", "通用/高级")),
    EffectTopGroup("audio", "音频", "music", "#FFDD44",
                   ("通用/音频", "音频反应")),
    EffectTopGroup("motor", "电机", "propeller", "#44CCFF",
                   ("电机/编组", "电机/序列", "电机/单体", "电机/矩阵")),
    EffectTopGroup("water", "水景", "droplets", "#FF6688",
                   ("水景/泵", "水景/阀", "水景/编程秀", "泵/矩阵")),
    EffectTopGroup("matrix", "矩阵", "grid-dots", "#00B4D8",
                   ("Matrix", "矩阵/空间编组", "图案/序列")),
    # ★ R182：显式列出「自定义/我的效果」（此前靠前缀兜底无名份）——
    #   该细分类当前在库计数为 0，UI 按既有规则不显示零计数大类。
    EffectTopGroup(TOP_OTHER, "其它", "dots", "#AAAAAA", ("自定义/我的效果",)),
)

_TOP_BY_KEY: Dict[str, EffectTopGroup] = {g.key: g for g in TOP_GROUPS}
_FINE_TO_TOP: Dict[str, str] = {cat: g.key for g in TOP_GROUPS for cat in g.members}
_LABEL_ORDER: Dict[str, int] = {g.label: i for i, g in enumerate(TOP_GROUPS)}

#: 游离细分类（不在 effect_engine.CATEGORY_ORDER 里的 4 个）的**语义位次**表。
#: R182：它们过去被 effect_category_sort_key 统统一脚踢到末尾按 Unicode 排
#: （Matrix/图案/序列 排在水景前后无明显规律）。这里显式表：
#:   · 锚点插入——「泵/矩阵」插在「水景/编程秀」之后（属水景域的矩阵编排）；
#:   · 尾段顺序——矩阵域三条按"空间编组 → Matrix → 图案/序列"阅读顺序。
_FINE_ANCHOR_AFTER: Tuple[Tuple[str, str], ...] = (
    ("泵/矩阵", "水景/编程秀"),
)
_FINE_TAIL_ORDER: Tuple[str, ...] = ("矩阵/空间编组", "Matrix", "图案/序列")


def _build_fine_display_order() -> List[str]:
    """规范序 + 游离类语义插入后的二级细分类显示序（未知分类兜底排最后）。"""
    order = list(CATEGORY_ORDER)
    for cat, after in _FINE_ANCHOR_AFTER:
        try:
            order.insert(order.index(after) + 1, cat)
        except ValueError:      # 锚点被改名/删掉 → 追加，不丢分类
            order.append(cat)
    for cat in _FINE_TAIL_ORDER:
        if cat not in order:
            order.append(cat)
    return order


_FINE_DISPLAY_ORDER: List[str] = _build_fine_display_order()
_FINE_ORDER_INDEX: Dict[str, int] = {c: i for i, c in enumerate(_FINE_DISPLAY_ORDER)}


def fine_category_sort_key(category: str) -> tuple:
    """二级细分类显示排序（含游离类的语义位次）；未知分类排最后。"""
    cat = normalize_effect_category(category)
    idx = _FINE_ORDER_INDEX.get(cat)
    if idx is not None:
        return (0, idx, cat)
    return (1, len(_FINE_DISPLAY_ORDER), cat)

#: 显式表未命中时的前缀兜底（顺序即优先级：泵/阀先于矩阵，泵类矩阵归水景）。
_TOP_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("灯光", "light"),
    ("电机", "motor"),
    ("音频", "audio"),
    ("水景", "water"),
    ("泵", "water"),
    ("阀", "water"),
    ("矩阵", "matrix"),
    ("图案", "matrix"),
    ("通用", "motion"),
)


def top_group_key(category: str) -> str:
    """细分类（任意原始写法）→ 一级大类键；未知分类兜底 TOP_OTHER。"""
    cat = normalize_effect_category(category)
    key = _FINE_TO_TOP.get(cat)
    if key:
        return key
    if cat.lower().startswith("matrix"):
        return "matrix"
    for prefix, group_key in _TOP_PREFIXES:
        if cat.startswith(prefix):
            return group_key
    return TOP_OTHER


def top_group(key: str) -> Optional[EffectTopGroup]:
    """大类键 → 定义对象（含 TOP_RECENT/空键在内的非法键返回 None）。"""
    return _TOP_BY_KEY.get(key)


def top_label_for_category(category: str) -> str:
    """细分类 → 所属一级大类显示名（用于列表一级分组头）。"""
    grp = _TOP_BY_KEY.get(top_group_key(category))
    return grp.label if grp is not None else str(category or "")


def top_group_sort_key(label: str) -> tuple:
    """一级分组头排序：按 TOP_GROUPS 声明顺序，未知标签排最后。"""
    return (_LABEL_ORDER.get(label, len(TOP_GROUPS)), str(label))


def sub_label(category: str) -> str:
    """二级 chip 显示名：去掉一级前缀（"灯光/颜色"→"颜色"；无斜杠取全名）。

    ★ R182 消歧（用户实报"水景下显示裸『矩阵』"）：跨域细分类的尾段本身就是
      另一个域的域名时（"泵/矩阵"→ 裸"矩阵"），保留全名不切前缀——否则在
      水景组里出现一个叫"矩阵"的 chip，用户无从判断它属于哪个域。
    """
    cat = normalize_effect_category(category)
    if "/" not in cat:
        return cat
    tail = cat.split("/", 1)[1]
    if "矩阵" in tail:
        return cat
    return tail


def group_counts(presets: Iterable) -> Dict[str, int]:
    """全库各一级大类的预设计数（含 TOP_OTHER）。"""
    counts = {g.key: 0 for g in TOP_GROUPS}
    for p in presets:
        counts[top_group_key(getattr(p, "category", ""))] += 1
    return counts


def sub_counts(presets: Iterable, key: str) -> List[Tuple[str, int]]:
    """某一级大类下的二级细分类计数，按 ``fine_category_sort_key`` 语义位次排序。"""
    counter: Dict[str, int] = {}
    for p in presets:
        if top_group_key(getattr(p, "category", "")) != key:
            continue
        cat = normalize_effect_category(getattr(p, "category", ""))
        counter[cat] = counter.get(cat, 0) + 1
    return sorted(counter.items(), key=lambda kv: fine_category_sort_key(kv[0]))


def _extra_tags(preset) -> List[str]:
    """用户标签（★ R183 W2-FX #3）：预设 id → ``effect_browser_w2_store`` 标签表。

    取不到（无档/异常/非预设对象）→ 空表 —— 搜索命中绝不因此失败。
    与内置 ``preset.tags`` 合并后进检索 hay，故"给预设打了标签就能立刻搜到"。
    """
    try:
        from . import effect_browser_w2_store as _w2store
        return [str(t) for t in _w2store.user_tags(getattr(preset, "id", "")) if str(t)]
    except Exception:
        return []


def _preset_search_hay(preset) -> str:
    """全字段检索 hay（名称/ID/描述/规范分类/原始分类/大类名/标签）。

    抽出为独立函数：``preset_matches_query`` 行数与 CC 都在门禁基线内零余量，
    新增功能一律走本函数；``effect_browser_w2_proxy`` 的代理过滤复用同一拼装。
    """
    raw_cat = str(getattr(preset, "category", "") or "")
    norm_cat = normalize_effect_category(raw_cat)
    grp = _TOP_BY_KEY.get(top_group_key(raw_cat))
    return " ".join(str(x) for x in (
        getattr(preset, "id", ""),
        f"{display_name(preset)} {getattr(preset, 'name', '')}",
        getattr(preset, "description", ""),
        norm_cat,
        norm_cat.replace("/", ""),
        raw_cat,
        grp.label if grp is not None else "",
        " ".join(str(t) for t in (getattr(preset, "tags", []) or [])),
        " ".join(_extra_tags(preset)),
    )).lower()


def preset_matches_query(preset, query: str) -> bool:
    """搜索命中判定（纯函数）：空白分词 AND 匹配名称/ID/描述/标签/
    规范细分类/原始分类/一级大类名；分类字符串去斜杠后再拼一份，
    使「灯光颜色」也能命中「灯光/颜色」。

    ★ R183 W2-FX #4：在旧语义之上叠加**前缀 token** 语法
      （``cat:电机 tag:快速 -strobe``，解析见 ``effect_browser_w2_search``）。
      **旧朴素 AND 优先**：先按旧语义整句试一次，命中即返回 —— 因此"以前能搜到
      现在还一定搜得到"（命中集只增不减）；旧语义落空才按前缀/取反解释。
    """
    text = str(query or "").strip()
    if not text:
        return True
    hay = _preset_search_hay(preset)
    if all(tok in hay for tok in text.lower().split()):
        return True
    return _matches_prefixed(preset, text, hay)


def _matches_prefixed(preset, text: str, hay: str) -> bool:
    """旧语义落空后的前缀/取反解释（任何异常 → False = 与旧语义一致的不命中）。"""
    try:
        from .effect_browser_w2_search import matches, parse_tokens
        parsed = parse_tokens(text)
        if not parsed:
            return False
        return matches(
            parsed, hay_all=hay,
            name=str(getattr(preset, "name", "") or ""),
            cat_texts=(str(getattr(preset, "category", "") or ""),),
            tags=tuple(_extra_tags(preset)) + tuple(
                str(t) for t in (getattr(preset, "tags", []) or ())))
    except Exception:
        return False


def order_recent(presets: Sequence, history_ids: Sequence) -> List:
    """按最近使用序返回子集（仅含库中仍存在的 id，历史序、去重）。"""
    by_id = {getattr(p, "id", ""): p for p in presets}
    out: List = []
    seen = set()
    for pid in history_ids or []:
        pid = str(pid)
        preset = by_id.get(pid)
        if preset is not None and pid not in seen:
            seen.add(pid)
            out.append(preset)
    return out


def order_favorites(presets: Sequence, favorite_ids: Sequence) -> List:
    """按收藏序返回子集（仅含库中仍存在的 id，收藏序、去重）。

    ★ R183 批 A：与 ``order_recent`` 完全同构 —— 收藏是**用户态**，不进
    ``preset.category``（改分类即破坏旧工程兼容），只做一层 id 过滤。
    已下架/已删除的收藏 id 静默丢弃（库里没有就不显示，绝不占位空行）。
    """
    return order_recent(presets, favorite_ids)



def _header_fine(preset) -> str:
    return normalize_effect_category(getattr(preset, "category", ""))


def _header_top(preset) -> str:
    return top_label_for_category(getattr(preset, "category", ""))


def _header_recent(_preset) -> str:
    return RECENT_HEADER


def _header_fav(_preset) -> str:
    return FAV_HEADER


def plan_preset_rows(presets, top: str = "", sub: str = "",
                     query: str = "", history=None, *, favorites=None):
    """筛选 + 分组规划（纯函数）：返回 ``(presets, group_fn, sort_key)``。

    - ``query`` 非空：跨全部大类检索（命中自动展开分组），分组头 = 一级大类。
    - ``top == TOP_RECENT``：按 history 序的最近使用，单段头。
    - ``top == TOP_FAV``（★ R183 批 A）：按 ``favorites`` 序的收藏子集，单段头。
    - ``top`` 为真实大类：限该类，分组头 = 二级细分类（``sub`` 再精筛）。
    - ``top == ""``：全库按一级大类分组；``sub`` 兼容单独细分类过滤。

    ``favorites`` 用**仅关键字**传入：位置参数已满 5 个（门禁 §10.2）。
    """
    cat_of, sub = catalog_filter(sub)
    if query:
        out = [p for p in presets if preset_matches_query(p, query)]
        out.sort(key=lambda p: fine_category_sort_key(cat_of(p)))
        return out, _header_top, top_group_sort_key
    if top == TOP_RECENT:
        return order_recent(presets, history), _header_recent, None
    if top == TOP_FAV:
        return order_favorites(presets, favorites), _header_fav, None
    if top:
        out = [p for p in presets
               if top_group_key(getattr(p, "category", "")) == top]
        if sub:
            out = [p for p in out if cat_of(p) == sub]
        return out, _header_fine, fine_category_sort_key
    out = list(presets)
    if sub:
        out = [p for p in out if cat_of(p) == sub]
    out.sort(key=lambda p: fine_category_sort_key(cat_of(p)))
    return out, _header_top, top_group_sort_key
