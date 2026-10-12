# -*- coding: utf-8 -*-
"""★ R183 T6：跑灯序列的**调度台驱动**（计时器 + 点亮/释放 + 菜单）。

分工：状态机在 `processing/dispatch/chase_runner.ChaseState`（纯逻辑、可单测）；
本模块只做"把状态机接到调度台"的三件事：
  ① 一个 `ChasePlayer(QObject)`：定时推进 + 每步"放上一步、点亮下一步"；
  ② 面板方法（`_chase_start/_chase_stop/_chase_toggle/_chase_configure`）以
     **模块级函数 + setattr 装配**挂上（不往 DispatchPanel 类体堆方法，CLAUDE §10.1）；
  ③ 右键菜单项（"跑灯序列…"）。

为什么用 `QTimer` 而不是 `QThread`：步进动作全是 UI 线程的既有 recall/release
通路（面板定时器、NM 写层），跨线程只会引入锁；同款跑灯状态机 也跑在
MasterTimer 单线程里（`chaserrunner.cpp`），语义同构。

**与既有"释放/回放"记账的接线**（关键正确性点）：每步都走
`panel._deactivate_scene(prev)` → `panel._recall_ids([next])` → `_note_scene_activation`，
与用户手点场景完全同路 —— 于是"依次恢复性缓存"、"优先级 stamp 台账"、
"淡化斜坡"三套既有机制全部自动生效，跑灯不需要第二套记账。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QTimer

from ...processing.dispatch.chase_runner import (FINISHED, ChaseState,
                                                  normalize_order)

_log = logging.getLogger(__name__)

#: 面板上持有全部播放器的属性名（列表，便于 teardown 一并停）
_ATTR = "_chase_players"


def _players_of(panel) -> list:
    """取播放器列表（取不到 → 空列表）。

    ★ 销毁期防御：`destroyed` 回调里面板 C++ 对象已析构，`getattr` 会抛
    `RuntimeError: wrapped C/C++ object ... has been deleted` —— 这里吞掉并给空
    列表（`teardown_chase` 另有"捕获列表"路径，不依赖本函数）。
    """
    try:
        out = getattr(panel, _ATTR, None)
    except Exception:
        return []
    if not isinstance(out, list):
        out = []
        try:
            panel.__dict__[_ATTR] = out
        except Exception:
            return out
    return out


class ChasePlayer(QObject):
    """一个序列卡一个播放器：`start()` 起步、`stop()` 收尾（放掉当前步）。"""

    def __init__(self, panel, cache_id: str, chase: dict, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self._cid = str(cache_id or "")
        self._chase = dict(chase or {})         # 归一化后的配置（判配置变更用）
        self._state = ChaseState(steps=chase.get("steps") or [],
                                 order=chase.get("order") or "loop")
        self._secs = float(chase.get("step_seconds", 1.0) or 1.0)
        self._timer = QTimer(self)
        self._timer.setInterval(max(50, int(self._secs * 1000.0)))
        self._timer.timeout.connect(self._tick)
        #: 当前步的场景 id（stop 时释放它）
        self._current = None

    def config_key(self) -> tuple:
        """配置指纹（步列表 + 顺序 + 步长）：变了就必须**重建**播放器。

        ★ 踩坑（自测抓到）：配置改动后复用旧播放器 → 它仍按**旧步列表**跑，
        用户看到"改了序列但跑的还是老的"。播放器持有 `ChaseState`（含游标与
        洗牌表），无法热改，故指纹不同即丢弃重建。
        """
        steps = tuple(str(s) for s in (self._chase.get("steps") or []))
        return (steps, str(self._chase.get("order") or "loop"),
                round(float(self._chase.get("step_seconds", 1.0) or 1.0), 4))

    # ── 属性 ────────────────────────────────────────────────────────────
    def cache_id(self) -> str:
        return self._cid

    def is_running(self) -> bool:
        return bool(self._timer.isActive())

    def current_scene(self):
        return self._current

    # ── 起停 ────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """从头起步（重复调用 = 重跑）。返回是否真的在跑。"""
        if not self._state.steps:
            return False
        self._state.reset()
        self._timer.start()
        self._tick()                       # 立即点亮第一步（不等第一个周期）
        return True

    def stop(self) -> None:
        """停表并**释放当前步**（不释放的话最后一步会永远亮着）。"""
        self._timer.stop()
        cur = self._current
        self._current = None
        self._state.reset()
        if cur:
            try:
                panel_stop_scene(self._panel, cur)
            except Exception:
                _log.debug("跑灯收尾释放失败 cid=%s（该步可能仍亮）", cur, exc_info=True)

    # ── 步进 ────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        idx = self._state.next_index()
        if idx == FINISHED:
            self.stop()                    # SingleShot 跑完 / 单步 PingPong → 自停
            try:
                self._panel._status.setText("跑灯序列已跑完（SingleShot）")
            except Exception:
                pass
            return
        steps = self._state.steps
        if not (0 <= idx < len(steps)):
            self.stop()
            return
        nxt = steps[idx]
        cur = self._current
        self._current = nxt
        if cur and cur != nxt:
            try:
                panel_stop_scene(self._panel, cur)
            except Exception:
                _log.debug("跑灯步进释放失败 cid=%s", cur, exc_info=True)
        try:
            panel_play_scene(self._panel, nxt)
        except Exception:
            _log.debug("跑灯步进点亮失败 cid=%s", nxt, exc_info=True)


# ── 面板侧动作（模块级；由 `_install_chase_handlers` 挂到面板类）─────────────
def panel_play_scene(panel, cid: str) -> None:
    """点亮一个场景（与左键点卡完全同路：淡入 → 调用 → 登记顺序 → 高亮）。"""
    from .scene_release import _note_scene_activation
    from .store_area_ops import _cache_of, _fade_in_scene
    c = _cache_of(cid)
    if c is not None:
        _fade_in_scene(panel, c)
    panel._recall_ids([str(cid)])
    _note_scene_activation(panel, str(cid))
    try:
        active = set(panel.list.active_ids() or ())
        active.add(str(cid))
        panel.list.set_active(active)
    except Exception:
        _log.debug("跑灯高亮登记失败 cid=%s", cid, exc_info=True)


def panel_stop_scene(panel, cid: str) -> None:
    """释放一个场景（与右键"关闭"同路，但**不带**"未激活不误灭现场"守卫 ——
    跑灯驱动的场景必然由自己点亮）。"""
    from .scene_release import _deactivate_scene
    _deactivate_scene(panel, str(cid))


def _chase_of(panel, cid: str):
    """取该场景的跑灯配置（非序列卡 → None）。"""
    from .store_area_ops import _cache_of
    c = _cache_of(cid)
    if c is None:
        return None
    chase = dict(getattr(c, "chase", {}) or {})
    return chase if chase.get("steps") else None


def _find_player(panel, cid: str):
    for p in list(_players_of(panel)):
        try:
            if p.cache_id() == str(cid):
                return p
        except Exception:
            continue
    return None


def _player_for(panel, cid: str, chase: dict):
    """取该场景的播放器：**配置指纹一致**才复用，否则重建（见 `config_key`）。"""
    fresh = ChasePlayer(panel, str(cid), chase)     # 只为算指纹，不入列
    want = fresh.config_key()
    fresh.deleteLater()
    old = _find_player(panel, str(cid))
    if old is not None:
        try:
            if old.config_key() == want:
                return old
        except Exception:
            pass
        old.stop()
        try:
            _players_of(panel).remove(old)
            old.deleteLater()
        except Exception:
            _log.debug("旧跑灯播放器回收失败", exc_info=True)
    player = ChasePlayer(panel, str(cid), chase)
    _players_of(panel).append(player)
    return player


def _chase_start(panel, cid) -> bool:
    """启动/重跑某序列卡。返回是否在跑（配置变了会重建播放器）。"""
    chase = _chase_of(panel, str(cid))
    if chase is None:
        return False
    player = _player_for(panel, str(cid), chase)
    ok = bool(player.start())
    try:
        panel._status.setText(
            f"跑灯已启动：{len(chase['steps'])} 步 · 顺序 {chase['order']} · "
            f"每步 {chase['step_seconds']:.2f}s")
    except Exception:
        _log.debug("跑灯状态栏文案失败", exc_info=True)
    return ok


def _chase_stop(panel, cid=None) -> None:
    """停一个（`cid` 给定）或全部序列；并放掉当前步。"""
    if cid is not None:
        p = _find_player(panel, str(cid))
        if p is not None:
            p.stop()
    else:
        for p in list(_players_of(panel)):
            try:
                p.stop()
            except Exception:
                _log.debug("停跑灯失败", exc_info=True)
    try:
        panel._status.setText("跑灯已停止（当前步已释放）")
    except Exception:
        pass


def _chase_toggle(panel, cid) -> None:
    """点序列卡：在跑 → 停；没跑 → 起（与 latch 开关同一手感）。"""
    p = _find_player(panel, str(cid))
    if p is not None and p.is_running():
        _chase_stop(panel, cid)
        return
    _chase_start(panel, cid)


def _chase_is_running(panel, cid) -> bool:
    p = _find_player(panel, str(cid))
    return bool(p is not None and p.is_running())


def _chase_configure(panel, cid) -> None:
    """右键「跑灯序列…」：编辑步列表（多行文本，每行一个场景名或 id）+ 顺序 + 步长。

    为什么用"多行文本"而不是拖拽建序列：与项目既有编辑习惯一致（关联器/编曲均
    有文本式入口），且一次能改整条链；拖拽建链是后续增强项（见任务书"未完成"）。

    三段各自外提（`_ask_steps` / `_ask_order` / `_ask_seconds`）—— 三个对话框
    的内联写法会把本函数推过架构门禁 CC/行数上限，拆出后每段独立可测。
    """
    from .store_area_ops import _cache_of
    c = _cache_of(str(cid))    # 清查⑧A P0-1 附带：修前双参调用 → TypeError
    if c is None:
        return
    curve = dict(getattr(c, "chase", {}) or {})
    steps = _ask_steps(panel, curve)
    if steps is None:
        return
    order = _ask_order(panel, curve)
    if order is None:
        return
    secs = _ask_seconds(panel, curve)
    if secs is None:
        return
    _chase_write(panel, str(cid),
                 {"steps": steps, "order": order, "step_seconds": secs})


def _scene_names() -> tuple:
    """(id → 名称, 名称 → id) 两张表（步列表允许"名称或 id"两种写法）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        caches = get_effect_cache_store().list_caches()
    except Exception:
        return {}, {}
    name_of = {str(x.id): str(x.name) for x in caches}
    id_of = {}
    for x in caches:
        id_of.setdefault(str(x.name), str(x.id))
    return name_of, id_of


def _ask_steps(panel, curve: dict):
    """问步列表 → id 列表（取消 → None；空文本 → 空列表 = 清空序列）。"""
    from PyQt6.QtWidgets import QInputDialog
    name_of, id_of = _scene_names()
    default = "\n".join(name_of.get(s, s) for s in (curve.get("steps") or []))
    text, ok = QInputDialog.getMultiLineText(
        panel, "跑灯序列 · 步骤", "每行一个场景（名称或 id，按顺序跑）：", default)
    if not ok:
        return None
    steps = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        sid = s if s in name_of else id_of.get(s, "")
        if sid and sid not in steps:
            steps.append(sid)
    return steps


def _ask_order(panel, curve: dict):
    """问 RunOrder（四值）→ 短名（取消 → None）。"""
    from PyQt6.QtWidgets import QInputDialog
    labels = ["loop 循环", "single 单次", "pingpong 往复", "random 随机"]
    keys = ["loop", "single", "pingpong", "random"]
    cur = normalize_order(curve.get("order", "loop"))
    order, ok = QInputDialog.getItem(panel, "跑灯序列 · 顺序",
                                     "RunOrder（四值）：", labels,
                                     max(0, keys.index(cur)), False)
    return str(order).split()[0] if ok else None


def _ask_seconds(panel, curve: dict):
    """问每步时长（秒；取消 → None）。"""
    from PyQt6.QtWidgets import QInputDialog
    secs, ok = QInputDialog.getDouble(
        panel, "跑灯序列 · 步长", "每步时长（秒）：",
        float(curve.get("step_seconds", 1.0) or 1.0), 0.05, 3600.0, 2)
    return float(secs) if ok else None


def _chase_write(panel, cid: str, chase: dict) -> None:
    """写库（经 `EffectCache.from_dict` 的同一套校验，坏数据不可能入库）+ 标脏刷新。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        from ...processing.dispatch.effect_cache import _as_chase
        store = get_effect_cache_store()
        c = store.get(str(cid))
        if c is None:
            return
        c.chase = _as_chase(chase)
        if not c.chase:                    # 步列表被清空 = 退回普通场景卡
            _chase_stop(panel, str(cid))
        panel.refresh_saved()
        panel.cacheChanged.emit()
        panel._status.setText(
            f"跑灯序列已保存：{len(c.chase.get('steps', []))} 步 · "
            f"{c.chase.get('order', 'loop')} · "
            f"{float(c.chase.get('step_seconds', 1.0)):.2f}s/步"
            if c.chase else "已清空跑灯序列（该卡回到普通场景）")
    except Exception:
        _log.debug("跑灯序列写库失败 cid=%s", cid, exc_info=True)


def teardown_chase(panel_or_players) -> None:
    """面板销毁/换工程：停掉全部跑灯（否则死面板仍被计时器点亮 = 幽灵出光）。

    ★ 与 `store_area_ops._teardown_captured` 同款：`destroyed` 触发时面板的 C++
    对象**已析构**，任何 `getattr(panel, …)` 都会 `RuntimeError`（Qt 事件循环里炸
    出来会让用例直接失败）。故装配时用 `partial` **捕获播放器列表**，销毁回调只
    操作这份列表，绝不回查已删的 `self`。
    """
    players = panel_or_players
    if not isinstance(players, list):
        try:
            players = _players_of(panel_or_players)
        except Exception:
            players = []
    for p in list(players):
        try:
            p.stop()
            p._timer.stop()
            p.deleteLater()
        except Exception:
            _log.debug("跑灯 teardown 失败", exc_info=True)
    try:
        players.clear()
    except Exception:
        _log.debug("跑灯列表清空失败", exc_info=True)


#: 挂到面板类上的动作（`_chase_is_running` 是查询口，`_chase_write` 是程序化写库入口）。
#: ★ `_chase_write` 在 `dispatch_undo.MUTATING_HANDLERS` 里**刻意缺席**：它的 UI
#:   入口 `_chase_configure` 自带"改前压快照"包装，两个都包会为同一次用户动作压
#:   两条条目（第一条等价于"没改" → 用户按 Ctrl+Z 觉得"没反应"，R100 的老坑）。
CHASE_HANDLERS = (_chase_start, _chase_stop, _chase_toggle, _chase_configure,
                  _chase_is_running, _chase_write)


def _install_chase_handlers(cls) -> None:
    """把上面四个动作挂到面板类上（幂等）。"""
    for fn in CHASE_HANDLERS:
        setattr(cls, fn.__name__, fn)


__all__ = ["ChasePlayer", "CHASE_HANDLERS", "_install_chase_handlers",
           "teardown_chase", "_chase_start", "_chase_stop", "_chase_toggle",
           "_chase_configure", "_chase_is_running", "panel_play_scene",
           "panel_stop_scene"]
