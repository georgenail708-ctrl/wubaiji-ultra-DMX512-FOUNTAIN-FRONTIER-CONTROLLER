"""设备组成员与母序的公共边界；零 Qt、零状态，复用原 Effect 保序交集。

fixture_ids 是成员；base_order 只给顺序/母集合追溯，不能产生额外成员。
实时选择已经有明确顺序时不传 base。显式空成员始终为空。
"""
from __future__ import annotations


def ordered_members(ids, base=()) -> list[str]:
    """母序内的成员优先，其余按成员原序补齐，规范为字符串并去重。"""
    members = dict.fromkeys(str(fid) for fid in (ids or []) if str(fid))
    ordered = dict.fromkeys(str(fid) for fid in (base or []) if str(fid) in members)
    ordered.update(members)
    return list(ordered)
