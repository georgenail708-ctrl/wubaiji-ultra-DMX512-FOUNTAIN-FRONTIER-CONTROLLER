"""Wing DSL adapters: parse metadata and render through the public phase model."""
from . import ir
from ...utils.wing_block import build_wing_block_units

KEYS = {"wings", "block", "wingdir", "wing_mirror", "wing_reverse"}
_TRUE = {"on", "true", "1", "开", "yes"}
_FALSE = {"off", "false", "0", "关", "no"}
HELP = {
    "wings": ("翼：把选中序列切成 N 段，每段跑同一波形（见 6.5）", "wings 2;"),
    "block": ("块：相邻 N 台共用一个单元值，阶梯式（见 6.5）", "block 3;"),
    "wingdir": ("翼内方向：/ \\ < >（见 6.5）", "wingdir /;"),
    "wing_mirror": ("相邻翼交替反射；旧工程默认为 off", "wing_mirror on;"),
    "wing_reverse": ("倒转每翼取值顺序；可与反射叠加", "wing_reverse on;"),
}


def parse(clip, key, tokens, index, *, line, errors, number):
    if key in ("wings", "block"):
        value, index = number(tokens, index, line, errors, key)
        if value is not None:
            setattr(clip, "wings" if key == "wings" else "block_size", max(1, int(value)))
        return index
    if key == "wingdir":
        return _direction(clip, tokens, index, line, errors)
    return _flag(clip, key, tokens, index, context=(line, errors))


def _direction(clip, tokens, index, line, errors):
    if index >= len(tokens):
        errors.append(ir.ParseError(line, 1, "wingdir 缺少方向值"))
        return index
    direction = ir.normalize_wing_dir(tokens[index])
    if direction is None:
        errors.append(ir.ParseError(line, 1, f"未知翼方向: {tokens[index]}（可用 / \\ < > 或 low_to_high 等）"))
    else:
        clip.wing_dir = direction
    return index + 1


def _flag(clip, key, tokens, index, *, context):
    line, errors = context
    if index >= len(tokens) or tokens[index].lower() not in _TRUE | _FALSE:
        errors.append(ir.ParseError(line, 1, f"{key} 需要 on/off"))
        return index
    setattr(clip, key, tokens[index].lower() in _TRUE)
    return index + 1


def units(statement, members):
    wings = max(1, int(getattr(statement, "wings", 1) or 1))
    block = max(1, int(getattr(statement, "block_size", 1) or 1))
    direction = ir.normalize_wing_dir(getattr(statement, "wing_dir", "low_to_high")) or "low_to_high"
    mirror = bool(getattr(statement, "wing_mirror", False))
    reverse = bool(getattr(statement, "wing_reverse", False))
    if wings > 1 or block > 1 or mirror or reverse:
        grouped = build_wing_block_units(members, wings, block, direction, mirror=mirror, reverse=reverse)
        if grouped:
            return grouped
    return [[member] for member in members]
