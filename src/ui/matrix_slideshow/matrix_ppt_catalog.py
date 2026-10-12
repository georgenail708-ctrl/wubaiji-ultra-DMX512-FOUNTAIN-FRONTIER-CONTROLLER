"""R32 new-sequence catalog; legacy names/generators and stored frames stay intact.

Exact aliases: r189-library-ppt.md and probe_r189_ppt_library.py.
stripes.js (Apache-2.0) demonstrates parameterized pattern families;
reuse the repository's generators and Qt widgets rather than copying JS.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .matrix_slide_engine import BUILTIN_PRESETS, BUILTIN_SEQUENCES, SPACE_PPT_PRESETS, EXTENDED_PPT_PRESETS
from .matrix_ppt_detail_sequences import (
    DETAIL_SEQUENCE_GENERATORS, ECHO_PHRASE_SEQUENCE_ID, GATHER_RELEASE_SEQUENCE_ID,
    GRAY_TRAIL_SEQUENCE_ID, SOFT_PEAK_SEQUENCE_ID,
)
from .matrix_ppt_composition_sequences import (
    DUAL_CLOCK_ID, OVERLAP_WEAVE_ID, EUCLIDEAN_PHRASE_ID, DAMPED_BOUNCE_ID,
)

# 6 fixture counts x 6 representative parameter sets; output fields exactly equal.
# These keys are hidden only from NEW creation. Legacy generate(key) stays callable.
EXACT_ALIASES = {
    "蛇形双": "对跑", "向两边旗帜": "放射跑", "中心放射": "放射跑",
    "双山型": "三角跑", "间1跑": "矩形反跑", "奇偶交替": "矩形反跑",
    "前后交替": "旗帜跑", "三组轮换": "间2跑", "四组轮换": "间3跑",
    "蛇形跑": "Z字跑", "菱形展开": "十字展开", "双螺旋反": "双螺旋",
    "窄波浪": "正跑双", "宽波浪": "正跑半", "螺旋宽": "正跑半",
    "五组轮换": "间4跑", "棋盘追逐": "奇偶追逐",
    "空间八轴花瓣": "空间中心绽放", "空间扇区镜像": "空间嵌套边框",
    "空间环形开合": "空间X镜像波", "空间边缘呼吸": "空间对角镜像",
    "空间中心呼吸": "空间反对角镜像", "空间左右对拍": "空间沙漏对称",
    "空间上下对拍": "空间分裂镜面", "空间棋盘镜像": "空间四象限同步",
    "空间风车四叶": "空间四角回声", "空间风车八叶": "空间四源干涉",
    "空间波纹叠影": "空间多瓣脉冲", "空间涟漪交叉": "空间万花轴线",
    "空间窗花开合": "空间曼陀罗瓣", "空间窗花追逐": "空间花冠波纹",
    "空间双心扩散": "空间同心十字", "空间双心收缩": "空间旋向条纹",
    "空间边角穿梭": "空间径向点阵", "空间对称雨幕": "空间菱形棋盘",
    "空间对称火花": "空间呼吸砖格", "空间十字扫掠": "空间水平镜像波",
    "空间双环呼吸": "空间垂直镜像波",
    "非对称斜向百叶": "非对称单边扫线", "非对称边缘绕行": "非对称右偏追逐",
    "非对称中心偏移": "非对称左偏追逐", "非对称双速追逐": "非对称错位阶梯",
    "非对称碎片滑行": "非对称断点跑", "非对称黑洞吸入": "非对称蛇形缺口",
    "非对称纵向错层": "非对称边角流动", "非对称波峰漂移": "非对称孤岛闪",
    "非对称岛链跑": "非对称交错窗格", "非对称单翼展开": "非对称折返走廊",
    "非对称双翼错开": "非对称斜雨扫", "非对称逆时针门": "非对称锯齿流",
    "非对称顺时针门": "非对称错拍波", "非对称脉冲断续": "非对称半场推进",
    "非对称扇形扫": "非对称偏心放射", "非对称镜外镜": "非对称偏心收缩",
    "形状Z形折返": "形状圆到方", "形状V形翻转": "形状方到菱",
    "形状X形扫": "形状三角展开", "形状双环互换": "形状十字收缩",
    "形状中心空洞": "形状星芒旋转", "形状边框侵入": "形状环形开口",
    "形状斜切方块": "形状月牙漂移", "形状阶梯折叠": "形状沙漏翻转",
    "形状多边变换": "形状箭头推进", "形状波形边界": "形状箭头回退",
    "形状碎片成形": "形状门框开合", "形状成形碎裂": "形状窗格翻页",
    "形状岛屿合并": "形状皇冠起伏", "形状桥梁延展": "形状心形脉冲",
    "形状迷宫打开": "形状八瓣花", "形状双塔升降": "形状四叶风车",
    "形状山峰迁移": "形状六边波", "形状瀑布轮廓": "形状井字裂变",
    "形状皇冠闪": "形状回字扩散", "形状星门": "形状S形游走",
    "节奏拍点填充": "节奏四拍跑", "节奏拍点清空": "节奏八拍跑",
    "节奏上升句": "节奏切分闪", "节奏下降句": "节奏反拍闪",
    "节奏循环句": "节奏重拍全开", "节奏断奏句": "节奏弱拍半开",
    "节奏连奏句": "节奏三连追逐", "节奏同步开合": "节奏摆动跑",
    "节奏错拍开合": "节奏鼓点爆闪", "节奏半场对答": "节奏低频脉冲",
    "节奏四段对答": "节奏高频碎闪", "节奏分组切片": "节奏停顿推进",
    "节奏闪烁尾迹": "节奏快慢交替", "节奏重音涟漪": "节奏一强三弱",
    "节奏静默爆发": "节奏二强二弱", "节奏渐密": "节奏问答跑",
    "节奏渐疏": "节奏左右问答", "节奏三段爆点": "节奏前后问答",
    "节奏环形鼓点": "节奏中心回应", "节奏舞台收束": "节奏边缘回应",
}

CATEGORY_MAP = {
    "开合类": "基础开合", "渐变类": "基础开合",
    "跑泉类": "追逐波浪", "波浪类": "追逐波浪", "山型/锯齿": "追逐波浪",
    "矩形/旗帜": "分组节拍", "间隔类": "分组节拍", "特殊类": "分组节拍",
    "空间编组": "分组节拍", "节奏编排": "分组节拍",
    "对称空间": "平面图形", "形状变形": "平面图形", "点阵变形": "点阵拼块",
    "粒子物理": "粒子流动", "非对称运动": "斜向偏心", "高级序列": "轨迹扩散",
}
CATEGORIES = tuple(dict.fromkeys(CATEGORY_MAP.values()))

# Correct misleading display names without reassigning a legacy ID or its output.
LABELS = {
    GRAY_TRAIL_SEQUENCE_ID: "灰度余辉",
    SOFT_PEAK_SEQUENCE_ID: "柔峰推进", GATHER_RELEASE_SEQUENCE_ID: "蓄势齐放",
    ECHO_PHRASE_SEQUENCE_ID: "回声对答",
    DUAL_CLOCK_ID: "双速交汇", OVERLAP_WEAVE_ID: "三层交叠",
    EUCLIDEAN_PHRASE_ID: "均布切分", DAMPED_BOUNCE_ID: "弹落收束",
    "矩形反跑": "奇偶交替", "旗帜跑": "前后交替", "间2跑": "三组轮换",
    "间3跑": "四组轮换", "间4跑": "五组轮换", "Z字跑": "单点往返",
    "正跑半": "半幅追逐", "正跑双": "双点追逐",
    "形状月牙漂移": "圆环呼吸", "形状心形脉冲": "心形定格",
}
SUMMARIES = {
    "基础开合": "按设备顺序逐步开合，适合起落与收尾。",
    "追逐波浪": "亮区沿设备顺序移动，适合追逐与铺排。",
    "分组节拍": "设备分批轮换或闪烁，适合分组对答。",
    "平面图形": "按近方格编组形成平面图案；密度决定同时开启数量。",
    "点阵拼块": "按近方格编组拼接图案；密度决定同时开启数量。",
    "粒子流动": "点群沿近方格编组流动；密度决定点群覆盖数量。",
    "斜向偏心": "斜向或偏心亮区移动；密度决定同时开启数量。",
    "轨迹扩散": "按设备顺序展开、折返或扩散。",
}
DETAILS = {
    DUAL_CLOCK_ID: "两亮团按独立速度交汇；密度控制宽度。",
    OVERLAP_WEAVE_ID: "三分区交叠呼吸；密度控制覆盖与交叠。",
    EUCLIDEAN_PHRASE_ID: "强弱落点均布于每节；密度控制阵列覆盖。",
    DAMPED_BOUNCE_ID: "落点逐弱收拢、间歇逐长；密度控制击点宽。",
    GRAY_TRAIL_SEQUENCE_ID: "亮头依次前进，尾迹逐台变暗；密度控制尾宽。",
    SOFT_PEAK_SEQUENCE_ID: "对称柔峰逐团推进；密度控制峰宽。",
    GATHER_RELEASE_SEQUENCE_ID: "灰度蓄势后齐放、余音与留白。",
    ECHO_PHRASE_SEQUENCE_ID: "主区重音后副区弱回应，再共响留白。",
    "矩形反跑": "相邻设备分成奇偶两批，交替开启。",
    "旗帜跑": "前半与后半设备交替开启。",
    "间2跑": "每隔两台一批，三批轮换开启。",
    "间3跑": "每隔三台一批，四批轮换开启。",
    "间4跑": "每隔四台一批，五批轮换开启。",
    "Z字跑": "单点从头到尾再折返。",
    "正跑半": "约一半设备形成亮区，依次向前移动。",
    "正跑双": "相邻两台同时开启，依次向前移动。",
    "形状月牙漂移": "圆环半径往复变化，密度控制覆盖数量。",
    "形状心形脉冲": "固定心形亮区，密度控制覆盖数量。",
}


@dataclass(frozen=True)
class SequenceEntry:
    key: str
    label: str
    category: str
    summary: str
    density_supported: bool
    aliases: tuple[str, ...] = ()


def canonical_key(name: str) -> str:
    return EXACT_ALIASES.get(name, name)


def selected_sequence_key(combo) -> str:
    """Stable catalog data, with the historical text-only combo fallback."""
    return combo.currentData() or combo.currentText()


def snapshot_sequence_edit(panel) -> None:
    from ...core.project_manager import DOMAIN_MATRIX
    pm = getattr(panel, "_pm", None)
    if pm is None or pm.project is None:
        return
    pm.project.matrix_slideshow_state = panel.save_state()
    pm._snapshot_scoped(domain=DOMAIN_MATRIX)


def store_sequence_edit(panel) -> None:
    pm = getattr(panel, "_pm", None)
    if pm is not None and pm.project is not None:
        pm.project.matrix_slideshow_state = panel.save_state()


def _entry(name: str, source_category: str) -> SequenceEntry:
    category = {SOFT_PEAK_SEQUENCE_ID: "追逐波浪", ECHO_PHRASE_SEQUENCE_ID: "分组节拍",
                DUAL_CLOCK_ID: "追逐波浪", OVERLAP_WEAVE_ID: "基础开合",
                EUCLIDEAN_PHRASE_ID: "分组节拍", DAMPED_BOUNCE_ID: "轨迹扩散"}.get(
        name, CATEGORY_MAP[source_category])
    density = name in SPACE_PPT_PRESETS or name in EXTENDED_PPT_PRESETS or name in DETAIL_SEQUENCE_GENERATORS
    aliases = tuple(alias for alias, base in EXACT_ALIASES.items() if base == name)
    label = LABELS.get(name, name)
    if name not in LABELS:
        for prefix in ("空间", "非对称", "粒子", "形状", "点阵", "节奏"):
            label = label.removeprefix(prefix)
    return SequenceEntry(name, label, category, DETAILS.get(name, SUMMARIES[category]), density, aliases)


@lru_cache(maxsize=1)
def entries() -> tuple[SequenceEntry, ...]:
    return tuple(_entry(name, category) for category, names in BUILTIN_PRESETS.items()
                 for name in names if name in BUILTIN_SEQUENCES and name not in EXACT_ALIASES)


def query_entries(category: str | None = None, query: str = "") -> tuple[SequenceEntry, ...]:
    """A typed query searches every category and historical alias, without loading."""
    needle = str(query).strip().casefold()
    selected = (entry for entry in entries() if needle or not category or entry.category == category)
    return tuple(entry for entry in selected if not needle or needle in " ".join(
        (entry.key, entry.label, entry.summary, *entry.aliases)).casefold())


def entry_for_key(key: str) -> SequenceEntry | None:
    resolved = canonical_key(key)
    return next((entry for entry in entries() if entry.key == resolved), None)
