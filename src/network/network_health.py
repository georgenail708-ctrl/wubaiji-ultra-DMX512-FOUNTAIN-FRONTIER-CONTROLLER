# -*- coding: utf-8 -*-
"""N6 多播(IGMP)建议 + N7 网络健康评分（R183 W2 新模块）。

搬出动机：`network_diagnostics.py` 逼近 500 行门禁，且 N6/N7 都是**纯函数**
（输入 DiagnosticResult → 写建议/评分），与探测链路（防火墙/端口/网卡）无关。

N6（E1.31 附录运维口径）：输出模式为 multicast 时，多播会泛洪到
交换机所有端口——必须开 IGMP snooping 并保证每个 VLAN 有一个 querier。

N7：对既有四项诊断（防火墙 30 / 端口 30 / IP 冲突 20 / 网卡 20）加权算 0-100
分，并给出逐项扣分明细；`health_grade` 把分数翻成"能不能开演"的人话。
"""
from __future__ import annotations

from typing import List, Optional

#: 四项权重（合计 100）
HEALTH_WEIGHTS = (
    ("firewall", "防火墙", 30),
    ("ports", "端口占用", 30),
    ("conflicts", "IP 冲突", 20),
    ("adapters", "网卡", 20),
)

#: 多播建议文案（每条独立可测；顺序稳定）
MULTICAST_RECOMMENDATIONS = (
    "输出为多播(sACN)：请在交换机上开启 IGMP snooping，否则多播会泛洪到所有端口",
    "每个 VLAN 至少配置一个 IGMP querier（多数接入交换机默认没有，需指定）",
    "多域高带宽场景可改用单播/定向广播，降低交换机多播表压力",
)


def recommend_for_multicast(result) -> int:
    """output_mode=multicast → 追加 IGMP 建议；返回追加条数（非多播 = 0）。"""
    if str(getattr(result, "output_mode", "") or "").lower() != "multicast":
        return 0
    result.recommendations.extend(MULTICAST_RECOMMENDATIONS)
    return len(MULTICAST_RECOMMENDATIONS)


def _status_value(status) -> str:
    return str(getattr(status, "value", status) or "").lower()


def compute_health_score(result) -> int:
    """N7：0-100 健康评分（写回 result.health_score/health_deductions）。

    每项按权重计入，得分 = 权重 × 状态系数：pass=1.0 / warn=0.5 / fail=0.0 /
    unknown=0.5（未知不冤枉也不放行——既不判失败也不当通过）。PASS 项不产生
    扣分条目，明细只列真实失分原因（tooltip 用）。
    """
    statuses = {
        "firewall": getattr(result, "firewall_status", None),
        "ports": getattr(result, "port_status", None),
        "conflicts": getattr(result, "conflict_status", None),
        "adapters": getattr(result, "adapter_status", None),
    }
    score = 0.0
    deductions: List[str] = []
    for key, label, weight in HEALTH_WEIGHTS:
        state = _status_value(statuses.get(key))
        factor = {"pass": 1.0, "warn": 0.5, "fail": 0.0}.get(state, 0.5)
        lost = weight * (1.0 - factor)
        score += weight * factor
        if lost > 1e-9:
            word = {"warn": "警告", "fail": "失败"}.get(state, "未知")
            deductions.append(f"{label} {word} -{lost:.0f} 分")
    result.health_score = int(round(max(0.0, min(100.0, score))))
    result.health_deductions = deductions
    return result.health_score


def health_grade(score: Optional[int]) -> str:
    """评分 → 演出可用性短语（外场新人也能判断"能不能开演"）。"""
    if score is None:
        return "未评分"
    if score >= 90:
        return "可以开演"
    if score >= 70:
        return "可用，有风险项"
    if score >= 40:
        return "风险较高，建议先排除扣分项"
    return "不建议开演"


def health_summary_line(result) -> str:
    """一行健康摘要（评分 + 等级 + 扣分明细），无评分 → 空串。

    扣分明细用「；」连接，作为**既有建议区**的可见摘要；结构化明细仍在
    `result.health_deductions`（网络页可另做 tooltip，两条通道同源）。
    """
    score = getattr(result, "health_score", None)
    if score is None:
        return ""
    line = f"网络健康评分 {int(score)}/100 · {health_grade(int(score))}"
    details = getattr(result, "health_deductions", None) or []
    if details:
        line += "（扣分：" + "；".join(details) + "）"
    return line


def append_health_summary(result) -> None:
    """把健康摘要追加到建议列表末尾（空评分不追加，逐字零影响）。"""
    line = health_summary_line(result)
    if line:
        result.recommendations.append(line)


__all__ = ["HEALTH_WEIGHTS", "MULTICAST_RECOMMENDATIONS",
           "append_health_summary", "compute_health_score", "health_grade",
           "health_summary_line", "recommend_for_multicast"]
