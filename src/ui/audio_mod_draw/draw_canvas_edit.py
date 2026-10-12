# -*- coding: utf-8 -*-
"""Draw 画布编辑操作（R182）—— 参数循环/目标勾选（纯函数，无 Qt 依赖）。

从 `draw_canvas.py` 抽出（文件行数门禁）：画布交互只留命中与状态机，
值操作在此实现（画布与测试共用同一实现）。
"""
from __future__ import annotations


def materialize_params(node) -> dict:
    """旧档卡（无 params）→ 现场展开等价参数（R182 审计问题5 修法的公共件；
    清查②③#4 把双击 cycle 路径也接上——修前双击波形静默丢 wave_freq/amount）。"""
    params = node.get("params")
    if isinstance(params, dict):
        return params
    params = {
        "signal": "beat_strength",
        "wave": str(node.get("wave", "") or ""),
        # freq 钳制=滑杆域 0.25..8（清查③②C：旧档 >8 首拖静默跳变消除）
        "freq": max(0.25, min(8.0, float(node.get("wave_freq", 2.0) or 2.0))),
        "depth": max(0.0, min(1.0, float(node.get("amount", 1.0) or 1.0))),
        "attack": 0.05, "release": 0.2, "phase": 0.0, "invert": 0.0,
        "threshold": 0.0, "smoothing": 0.0, "freq_unit": "beat",
        "random_mode": "", "threshold_close": 0.0, "hold_ms": 0.0,
        "spread_deg": 0.0,
    }
    node["params"] = params
    return params


def cycle_control(node, key: str) -> None:
    """就地循环步进一个控件值（波形/深度/频率/起应/落应）。

    ★ R183 清查②③#4（P1）：旧档卡（无 params）先 materialize 再改（见
    `materialize_params`——R182 审计问题5 在双击路径复发，实测变调+变响）。
    """
    params = materialize_params(node)
    if key == "wave":
        from .draw_cards import WAVE_CHOICES
        vals = [v for _l, v in WAVE_CHOICES]
        cur = str(params.get("wave", "") or "")
        i = vals.index(cur) if cur in vals else 0
        params["wave"] = vals[(i + 1) % len(vals)]
        node["wave"] = params["wave"]
        # ★ 清查⑤①#6（P3）：materialize 后镜像 freq/amount（修前 cycle 路径
        #   不镜像，撤销一条"波形步进"连频率一起回滚到 materialize 前旧值）
        node["wave_freq"] = float(params.get("freq", 2.0))
        node["amount"] = float(params.get("depth", 1.0))
    elif key == "depth":
        cur = float(params.get("depth", 1.0))
        params["depth"] = 0.0 if cur >= 1.0 else min(1.0, round(cur + 0.25, 2))
        node["amount"] = params["depth"]
    elif key == "freq":
        cur = float(params.get("freq", 2.0))
        params["freq"] = 0.25 if cur >= 8.0 else round(cur + 1.0, 2)
        node["wave_freq"] = params["freq"]
    elif key in ("threshold", "smoothing"):
        # R183 M2 上卡参数：0..1 循环步进（默认 0=关）
        cur = float(params.get(key, 0.0))
        params[key] = 0.0 if cur >= 1.0 else round(min(1.0, cur + 0.25), 2)
    else:
        cur = float(params.get(key, 0.05))
        params[key] = 0.0 if cur >= 1.5 else round(cur + 0.25, 2)


def toggle_target(node, ct: str) -> None:
    """目标通道勾选切换（点 chip 即改；空目标卡可被编译丢弃）。"""
    chips = [str(c) for c in (node.get("ch_types") or [])]
    ct = str(ct)
    if ct in chips:
        chips.remove(ct)
    else:
        chips.append(ct)
    node["ch_types"] = chips
    node["ch_type"] = chips[0] if chips else ""


def zoom_at(scale: float, factor: float, sx: float, sy: float,
            *, ox: float, oy: float):
    """锚定屏幕点的缩放：返回 (新scale, 新offset_x, 新offset_y)；无变化 → None。"""
    new = max(0.1, min(6.0, float(scale) * float(factor)))
    if new == scale:
        return None
    wx = (sx - ox) / scale
    wy = (sy - oy) / scale
    return (new, sx - wx * new, sy - wy * new)


def maybe_overflow_menu_at(canvas, graph, wp, ev) -> bool:
    """+N 溢出徽标命中判定+弹菜单（清查③②P3 + ⑤②#2）。返回是否命中消费。

    需持 ev（globalPosition），故留在 mousePressEvent 调用而非下沉 _press_slider；
    hit_node 判定一并内收（压宿主 CC）。
    """
    from . import draw_graph as _dg
    node = _dg.hit_node(graph, wp.x(), wp.y())
    if node is None or str(node.get("kind", "")) != "rule":
        return False
    tag = _dg.overflow_tag_rect(node)
    if tag is None or not (tag[0] <= wp.x() <= tag[2] and tag[1] <= wp.y() <= tag[3]):
        return False
    show_all_targets_menu(canvas, node, ev)
    return True


def show_all_targets_menu(canvas, node, ev) -> None:
    """+N 溢出徽标 → 全部目标勾选菜单（不受 MAX_TARGET_CHIPS 截断；清查③②P3）。

    ★ 清查⑤②#2/#3/#4（P1/P2）：修前三连死——① ev.globalPos() 在 PyQt6
      QMouseEvent 不存在（AttributeError 菜单不弹）；② canvas.snap_node 非
      方法（AttributeError 勾选静默无效）；③"全部移除"只移除首项且 N 项各
      push 一条撤销步。全部照 wave 菜单既有口径修正。
    """
    from PyQt6.QtWidgets import QMenu
    from .draw_canvas_undo import snap_node
    menu = QMenu(canvas)
    current = [str(c) for c in (node.get("ch_types") or [])]

    def _set_chips(chips) -> None:
        old = snap_node(node)
        node["ch_types"] = [str(c) for c in chips]
        node["ch_type"] = node["ch_types"][0] if node["ch_types"] else ""
        canvas._undo_push_node_state(str(node.get("id", "")), old, "改目标")
        canvas.graph_changed.emit()
        canvas.update()

    for ct in sorted(set(current)):
        act = menu.addAction(ct)
        act.setCheckable(True)
        act.setChecked(True)
        act.triggered.connect(
            lambda _c=False, k=ct: _set_chips(
                [c for c in node.get("ch_types") or [] if c != k]))
    menu.addSeparator()
    # 清空全部 = 单命令（快照一次，ch_types=[]；不逐项 toggle 防 N 条撤销步）
    act_all = menu.addAction("全部移除")
    act_all.triggered.connect(lambda: _set_chips([]))
    # 追加：从全部已知通道类型里勾选新目标（修前菜单只能移除不能新增）
    from .draw_cards import DEFAULT_CH_TYPES
    known = dict.fromkeys((*getattr(canvas, "_target_types", ()), *DEFAULT_CH_TYPES))
    missing = [c for c in known if c not in current]
    if missing:
        add_menu = menu.addMenu("添加目标")
        for ct in missing:
            add_menu.addAction(ct, lambda k=ct: _set_chips(
                list(node.get("ch_types") or []) + [k]))
    menu.exec(ev.globalPosition().toPoint())
