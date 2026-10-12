"""Windows 防火墙规则探测（Art-Net 6454 / sACN 5568 的查询与解析）。

R182 P0-2（审计实锤）：原实现跑 ``netsh ... show rule name=all verbose`` 后按
英文标签（``Rule Name:`` / ``Enabled:`` / ``yes`` / ``Direction:`` /
``LocalPort:``）逐行解析。中文 Windows 输出的是 ``规则名称:`` / ``已启用: 是`` /
``方向: 入`` / ``本地端口:`` → 一行都匹配不上 → 规则表恒为空 → 诊断页把
"防火墙已放行"误报成 FAIL。

修法（语言无关，两级）：

1. **首选 PowerShell 定向查询**：``Get-NetFirewallRule -DisplayName <名>`` 只查
   本软件自己创建的两条规则（数千条全表遍历在本机实测 ~92s，定向查询 ~3s），
   输出 ``ConvertTo-Json`` → 字段名是我们自己构造的 ASCII 键，与系统语言无关；
   规则名走 base64(UTF-16LE) ``-EncodedCommand`` 传递，避免中文/空格被 shell 拆词。
2. **回退 netsh 定向查询**：``netsh advfirewall firewall show rule
   name="<名>" verbose``（仅两条规则，不做全表扫描），解析同时接受中英双标签
   （见 :func:`_LABEL_ALIASES`），"已启用"的"是"/"yes"/"True" 都算启用。

``parse_netsh_output`` 保留（既有英文 fixture 测试契约），但新增中英双标签支持；
``get_firewall_rules`` 改为调用 :func:`probe_dmx_firewall_rules` 的定向流程。

调用方：``src/network/network_diagnostics.py``（NetworkDiagnostics）。
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: netsh / PowerShell 子进程超时（原 netsh 全表查询 10s 语义保留给回退路径；
#: 定向查询规则条数固定为个位数，取 8s 足够且不会拖死诊断线程）。
_NETSH_TIMEOUT_SEC = 8
_POWERSHELL_TIMEOUT_SEC = 20

#: 规则名前缀（新版 + 历史品牌名）：新名 = ``<前缀> DMX UDP <In|Out>``。
#: 与 ``network_diagnostics.RULE_NAMES`` 同源——诊断模块把自己的 RULE_NAMES
#: 展开后显式传入探测函数，本元组仅为无参调用时的默认值。
RULE_NAME_PREFIXES: Tuple[str, ...] = ("雾白纪 ULTRA", "雾白纪", "冬极白星", "Qascade")


def rule_display_names(prefixes: Sequence[str] = RULE_NAME_PREFIXES) -> Tuple[str, ...]:
    """前缀表 → 完整规则名表（In/Out 成对）。"""
    return tuple(
        f"{p} DMX UDP {direction}"
        for p in prefixes
        for direction in ("In", "Out")
    )


#: 本软件创建的规则名（默认查询表，顺序 = 前缀顺序 × 方向）。
RULE_DISPLAY_NAMES: Tuple[str, ...] = rule_display_names()

#: netsh 标签别名：中文/英文 + 其他常见本地化版面（键统一小写去空格比较）。
_LABEL_ALIASES = {
    "enabled": ("已启用", "enabled"),
    "direction": ("方向", "direction"),
    "protocol": ("协议", "protocol"),
    "localport": ("本地端口", "localport"),
    "name": ("规则名称", "rulename", "name"),
    "action": ("操作", "action"),
}

#: 值为"真"的文本（中英双语；"yes"/"是"/"true"/"1"）。
_TRUE_VALUES = frozenset({"yes", "是", "true", "1", "启用", "已启用"})

#: 规范键 → 旧解析器使用的英文键（保持 ``parse_rule_dict`` 可复用）。
_ENGLISH_KEYS = {
    "enabled": "Enabled",
    "direction": "Direction",
    "protocol": "Protocol",
    "localport": "LocalPort",
}

#: netsh 方向值 → 内部 "in"/"out"（截断 3 字符的旧语义：inbound→inb 保留给
#: 旧解析器；此处新增的是**显式方向归一化**，中英一致映射到规范值）。
_DIRECTION_IN = frozenset({"in", "inb", "inbound", "入", "入站", "入站规则"})
_DIRECTION_OUT = frozenset({"out", "outb", "outbound", "出", "出站", "出站规则"})


@dataclass
class FirewallRule:
    """Windows firewall rule info"""
    name: str
    enabled: bool
    direction: str  # "in" or "out"
    protocol: str
    local_ports: List[str]


def parse_rule_dict(d: Dict[str, str]) -> FirewallRule:
    """Parse netsh rule dict to FirewallRule"""
    return FirewallRule(
        name=d.get("name", d.get("Rule Name", "")),
        enabled=d.get("Enabled", "").lower() == "yes",
        direction=d.get("Direction", "").lower()[:3],  # "in" or "out"
        protocol=d.get("Protocol", ""),
        local_ports=d.get("LocalPort", "").split(",") if d.get("LocalPort") else [],
    )


def normalize_direction(raw: str) -> str:
    """方向值归一化为 ``"in"`` / ``"out"``（中英双语 + 未知返回小写原文）。

    中文 netsh 输出 ``方向: 入``、PowerShell 输出 ``Inbound``；旧英文 netsh
    输出 ``Inbound``（截断 3 字符 → ``inb``）。三者都归一到 ``in``。
    """
    v = str(raw or "").strip().lower()
    if v in _DIRECTION_IN:
        return "in"
    if v in _DIRECTION_OUT:
        return "out"
    return v


def normalize_local_ports(raw) -> List[str]:
    """本地端口归一化：支持 ``"6454,5568"`` / ``["6454", "5568"]`` / ``"Any"``。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        for item in raw:
            out.extend(normalize_local_ports(item))
        return out
    text = str(raw).strip()
    if not text or text.lower() == "any":
        return []
    return [p.strip() for p in text.split(",") if p.strip()]


def _is_enabled(raw) -> bool:
    """启用判定：中英"真"值集合 + bool/数字直通。"""
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    return text in _TRUE_VALUES


def _label_key(line: str) -> Optional[Tuple[str, str]]:
    """``"本地端口: 6454"`` → ``("localport", "6454")``；非键值行返回 None。"""
    if ":" in line:
        sep = ":"
    elif "：" in line:
        sep = "："          # 中文全角冒号（部分本地化版面）
    else:
        return None
    key, _, val = line.partition(sep)
    k = key.strip().lower().replace(" ", "")
    for canon, aliases in _LABEL_ALIASES.items():
        if k in aliases:
            return canon, val.strip()
    return None


def _is_noise_line(line: str) -> bool:
    """netsh 输出中的非字段行（空行 / 分隔线 / 结束语「确定。」）。"""
    if not line:
        return True
    if line.startswith("---"):
        return True
    return line.endswith("。") and ":" not in line and "：" not in line


def _rule_from_netsh_fields(current: Dict[str, str]) -> FirewallRule:
    """字段字典 -> FirewallRule（中英键双查；缺键走空默认）。"""
    return FirewallRule(
        name=current.get("规则名称",
                         current.get("Rule Name", current.get("name", ""))),
        enabled=_is_enabled(current.get("已启用", current.get("Enabled", ""))),
        direction=normalize_direction(
            current.get("方向", current.get("Direction", ""))),
        protocol=current.get("协议", current.get("Protocol", "")),
        local_ports=normalize_local_ports(
            current.get("本地端口", current.get("LocalPort", ""))),
    )


def parse_netsh_output(output: str) -> List[FirewallRule]:
    """解析 netsh ``show rule`` 输出（中英双标签）。

    既有英文契约保持（``parse_rule_dict`` 的英文键语义不变）；新增中文标签
    识别与方向归一化——中文 Windows 下旧解析器恒返回空表（P0-2 误报根因）。
    """
    rules: List[FirewallRule] = []
    current: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if _is_noise_line(line):
            continue
        pair = _label_key(line)
        if pair is None:
            continue
        key, val = pair
        if key == "name":
            if current:
                rules.append(_rule_from_netsh_fields(current))
            current = {"name": val, "规则名称": val, "Rule Name": val}
            continue
        current[key] = val
        # 兼容旧解析器：同时写入英文驼峰键（parse_rule_dict 契约）。
        current[_ENGLISH_KEYS.get(key, key)] = val
    if current:
        rules.append(_rule_from_netsh_fields(current))
    return rules


def _rule_from_json_item(item: Dict) -> FirewallRule:
    """PowerShell JSON 单条记录 -> FirewallRule（键由脚本自身定义，语言无关）。"""
    return FirewallRule(
        name=str(item.get("name", "") or ""),
        enabled=_is_enabled(item.get("enabled", "")),
        direction=normalize_direction(item.get("direction", "")),
        protocol=str(item.get("protocol", "") or ""),
        local_ports=normalize_local_ports(item.get("ports", "")),
    )


def parse_powershell_json(text: str) -> List[FirewallRule]:
    """解析 ``Get-NetFirewallRule | ConvertTo-Json`` 输出（语言无关路径）。

    输入为空/``[]``/非法 JSON → 空表（不抛）。单条记录 PowerShell 会输出
    对象而非数组，此处一并接受。
    """
    payload = (text or "").strip()
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        log.debug("防火墙 PowerShell 输出非 JSON: %r", payload[:200])
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [_rule_from_json_item(item) for item in data
            if isinstance(item, dict)]


#: PowerShell 探测脚本：仅定向查询固定规则名（不做全表遍历，实测 ~3s vs ~92s）。
_POWERSHELL_SCRIPT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
    "$ProgressPreference='SilentlyContinue'\n"
    "$names = @({names})\n"
    "$out = @()\n"
    "foreach ($n in $names) {{\n"
    "  $rules = Get-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue\n"
    "  foreach ($r in $rules) {{\n"
    "    $pf = $r | Get-NetFirewallPortFilter\n"
    "    $out += [pscustomobject]@{{ name=$r.DisplayName;"
    " enabled=[string]$r.Enabled; direction=[string]$r.Direction;"
    " protocol=[string]$pf.Protocol; ports=@($pf.LocalPort) -join ',' }}\n"
    "  }}\n"
    "}}\n"
    "if ($out.Count -gt 0) {{ ConvertTo-Json -Compress -InputObject @($out) }}"
    " else {{ '[]' }}\n"
)


def _powershell_command(names: Sequence[str]) -> List[str]:
    """构造 ``powershell -EncodedCommand`` 调用串（中文/空格安全）。"""
    quoted = ",".join("'" + str(n).replace("'", "''") + "'" for n in names)
    script = _POWERSHELL_SCRIPT.format(names=quoted)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
    ]


def _run_powershell(names: Sequence[str]) -> str:
    """跑 PowerShell 定向查询（无结果/不可用时抛异常，由调用方回退 netsh）。"""
    return subprocess.check_output(
        _powershell_command(names), stderr=subprocess.DEVNULL,
        timeout=_POWERSHELL_TIMEOUT_SEC,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8", errors="replace",
    )


def _run_netsh_named(name: str) -> str:
    """跑 ``netsh advfirewall firewall show rule name=<name> verbose``（定向查询）。

    修前 diagnostics 走 ``name=all`` 全表扫描；这里改为逐条定向查询，名字个数
    固定（8 条），不随系统规则总数膨胀。

    ★ 引号纪律（本机实测）：**subprocess argv 列表形式下不能内嵌引号**——
    Windows 无 shell 参与，``name="Foo Bar"`` 会把引号当规则名的一部分 →
    netsh 报"没有与指定标准相匹配的规则"(rc=1)。带引号的 ``name="..."``
    只用于 cmd.exe 命令串路径（一键放行的 UAC 链与剪贴板文本）。
    """
    cmd = [
        "netsh", "advfirewall", "firewall", "show", "rule",
        f"name={name}", "verbose",
    ]
    return subprocess.check_output(
        cmd, stderr=subprocess.STDOUT, timeout=_NETSH_TIMEOUT_SEC,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8", errors="replace",
    )


def probe_dmx_firewall_rules_ex(
    names: Sequence[str] = RULE_DISPLAY_NAMES,
) -> "Tuple[List[FirewallRule], bool]":
    """探测 DMX 防火墙规则 → ``(rules, probe_ok)``（R182 审计修：三态）。

    `probe_ok=False` = **探测链整体不可用**（PowerShell 与 netsh 全部异常/
    超时）——与「探测成功但确实无规则」严格区分：修前两者都返回空表，
    诊断层把"查不了"误报成 FAIL（假阴性，误导用户反复点放行）。
    """
    try:
        text = _run_powershell(names)
        rules = parse_powershell_json(text)
        return rules, True          # 解析成功即为探测可用（空=确实无规则）
    except Exception as e:
        log.debug("PowerShell 防火墙探测不可用，回退 netsh: %s", e)

    rules: List[FirewallRule] = []
    any_ok = False
    for name in names:
        try:
            parsed = parse_netsh_output(_run_netsh_named(name))
        except subprocess.TimeoutExpired:
            log.warning("防火墙规则查询超时: %s", name)
            continue
        except Exception as e:
            log.debug("防火墙规则查询失败 (%s): %s", name, e)
            continue
        any_ok = True               # 至少一条查询真正执行完
        rules.extend(parsed)
    return rules, any_ok


def probe_dmx_firewall_rules(
    names: Sequence[str] = RULE_DISPLAY_NAMES,
) -> List[FirewallRule]:
    """探测本软件 DMX 防火墙规则（兼容包装：仅返回规则表）。

    三态版本见 :func:`probe_dmx_firewall_rules_ex`（诊断层用它区分
    "无规则"与"探测不可用"）。
    """
    return probe_dmx_firewall_rules_ex(names)[0]


def _run_netsh_show_rules() -> str:
    """旧全表查询（仅测试/兼容保留，诊断主路径不再使用）。

    命令串 / 超时 / 编码参数与拆分前完全一致；异常一律上抛给
    :func:`get_firewall_rules` 的既有 except 链处理。
    """
    cmd = [
        "netsh", "advfirewall", "firewall", "show", "rule",
        "name=all", "verbose"
    ]
    return subprocess.check_output(
        cmd, stderr=subprocess.STDOUT, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
        encoding="utf-8", errors="replace"
    )


def get_firewall_rules() -> List[FirewallRule]:
    """Query Windows firewall DMX rules (targeted probe, R182 P0-2)."""
    try:
        return probe_dmx_firewall_rules()
    except Exception as e:
        log.warning("Failed to query firewall rules: %s", e, exc_info=True)
        return []


def rule_allows(rules: Iterable[FirewallRule], direction: str, port: str) -> bool:
    """是否存在"启用 + 指定方向 + 覆盖该端口"的规则（原内联 ``any(...)`` 判定）。"""
    return any(
        r for r in rules
        if r.enabled and r.direction == direction
        and port in r.local_ports
    )
