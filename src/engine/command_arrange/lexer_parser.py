"""
command_arrange.lexer_parser — 命令行代码编曲 DSL 的解析器

把用户 / LLM 写的 clip{ } 文本块解析成 ir 里的中间表示（IR）。

设计要点：
  * 单一真源是 ir.py —— 所有关键字 / 方向 / 通道 / scope / hold / 灯跟踪来源
    的规范化全部走 ir.normalize_*，本文件**不**硬编码任何别名表。
  * 容错解析：遇到坏行收集 ir.ParseError(line, col, message) 但继续往下解析，
    返回 ir.ParseResult(clips=[...], errors=[...])，部分成功也照常返回。
  * 行号 1-based，尽量精确（ClipStmt.src_line / ChannelBody.src_line）。
  * 不求值任何表达式 / def 代码，只保存原始源串到 ChannelBody.code。

纯 Python，无任何 PyQt 依赖。
"""
from __future__ import annotations

import re
import textwrap
from typing import Any, List, Optional, Tuple

from . import ir, wing_attributes


# ─────────────────────────────────────────────────────────────────────────────
#  小工具
# ─────────────────────────────────────────────────────────────────────────────
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _strip_comment(line: str) -> str:
    """去掉行尾 / 整行注释（# 开头）。

    注意：`#FFAA00` 形式的十六进制颜色不是注释；引号内的 # 也不是注释。
    扫描时跟踪引号状态，并对每个 # 判断其后是否紧跟合法颜色，是则跳过。
    """
    in_q: Optional[str] = None
    i = 0
    out_end = len(line)
    while i < len(line):
        ch = line[i]
        if in_q is not None:
            if ch == in_q:
                in_q = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_q = ch
            i += 1
            continue
        if ch == "#":
            m = _HEX_COLOR_RE.match(line, i)
            if m:
                i = m.end()
                continue
            out_end = i
            break
        i += 1
    return line[:out_end].rstrip()


def _leading_ws(s: str) -> int:
    return len(s) - len(s.lstrip(" \t"))


def parse_num(s: str) -> float:
    """解析纯数值(计数/比例类)。**不**剥尾随 's'——'2s' 在此为非法(#3 类型化单位)。
    仅去掉尾随的 ',;' 分隔符。失败抛 ValueError。"""
    s = s.strip().rstrip(",;")
    return float(s)


def parse_duration(s: str) -> float:
    """解析时间数值,允许可选尾随 's'(秒)。用于 at/dur/fi/fo/pause/seq_delay。失败抛 ValueError。"""
    s = s.strip().rstrip(",;")
    if s and s[-1] in "sS" and any(c.isdigit() for c in s[:-1]):
        s = s[:-1]
    return float(s)


def _is_num(s: str) -> bool:
    try:
        parse_num(s)
        return True
    except ValueError:
        return False


def _is_duration(s: str) -> bool:
    try:
        parse_duration(s)
        return True
    except ValueError:
        return False


def _read_string_token(s: str) -> Tuple[str, str]:
    """读取一个字符串记号：带引号则到匹配引号，否则取单个非空白记号。

    返回 (token, 剩余串)。
    """
    s = s.strip()
    if not s:
        return "", ""
    if s[0] in ("'", '"'):
        q = s[0]
        end = s.find(q, 1)
        if end == -1:
            return s[1:], ""
        return s[1:end], s[end + 1:].strip()
    m = re.match(r"\S+", s)
    if not m:
        return "", s
    return m.group(0), s[m.end():].strip()


def _split_commas_respect_quotes(s: str) -> List[str]:
    """按逗号切分，但引号内的逗号不切。"""
    out: List[str] = []
    buf = ""
    q: Optional[str] = None
    for ch in s:
        if q is not None:
            buf += ch
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch
            buf += ch
            continue
        if ch == ",":
            out.append(buf)
            buf = ""
            continue
        buf += ch
    out.append(buf)
    return out


def _split_semicolons(s: str) -> List[str]:
    """按 ';' 切分，但引号内的 ';' 不切（#17，与逗号切分同款引号处理）。"""
    out: List[str] = []
    buf = ""
    q: Optional[str] = None
    for ch in s:
        if q is not None:
            buf += ch
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch; buf += ch; continue
        if ch == ";":
            out.append(buf); buf = ""; continue
        buf += ch
    out.append(buf)
    return out


def _split_first_semicolon(s: str) -> Tuple[str, str]:
    """在第一个「引号外」的 ';' 处切成 (前, 后)。无则 (s, '')。用于标量体: 表达式; 属性…"""
    q: Optional[str] = None
    for idx, ch in enumerate(s):
        if q is not None:
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch; continue
        if ch == ";":
            return s[:idx], s[idx + 1:]
    return s, ""


def _parse_one_range(tok: str) -> Tuple[int, int]:
    tok = tok.strip()
    if "-" in tok.lstrip("-"):  # 内部有 '-'(非纯负号)→ 区间
        # 用最后一个非首位的 '-' 作分隔，禁止前导负号
        if tok.startswith("-"):
            raise ValueError(f"编号不能为负: {tok}")
        a, b = tok.split("-", 1)
        return (int(a.strip()), int(b.strip()))
    if tok.startswith("-"):
        raise ValueError(f"编号不能为负: {tok}")
    v = int(tok)
    return (v, v)


def _validate_range(lo: int, hi: int, lineno: int, errors: List[ir.ParseError], ctx: str) -> bool:
    """校验区间合法:>=1 且 lo<=hi。非法记错误并返回 False。"""
    if lo < 1 or hi < 1:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}编号必须 ≥1: {lo}-{hi}"))
        return False
    if lo > hi:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}编号区间方向颠倒(应 小-大): {lo}-{hi}"))
        return False
    return True


def _parse_range_list(s: str, lineno: int, errors: List[ir.ParseError], ctx: str = "") -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            lo, hi = _parse_one_range(piece)
        except ValueError:
            errors.append(ir.ParseError(lineno, 1, f"无效的编号范围: {piece}"))
            continue
        if _validate_range(lo, hi, lineno, errors, ctx):
            ranges.append((lo, hi))
    return ranges


# ─────────────────────────────────────────────────────────────────────────────
#  选择器解析（控台风格：global / group / group_index + thru/step/count/from）
# ─────────────────────────────────────────────────────────────────────────────
_GLOBAL_NUM_RE = re.compile(r"^\d+(\s*-\s*\d+)?$")
# 一个选择表达式的首记号是否数字(全局)或 from(控台起点)
_NUM_TOK_RE = re.compile(r"^\d+$")


def _parse_step_expr(tokens: List[str], lineno: int, errors: List[ir.ParseError],
                     kind: str, group_ref: str, ctx: str) -> Optional[ir.SelectorTerm]:
    """把一串记号解析成一个步进/计数 SelectorTerm。

    支持形态(记号已按空白切好)：
      <a>                      单个 / 单区间(a 可为 'lo-hi')
      <a> thru <b>             区间 a..b
      [from] <a> [thru <b>] [step <s>] [count <c>]
      <lo-hi> [step <s>] [count <c>]
    step=相邻选中相差(默认1)；count=取个数(默认不限)。
    """
    i = 0
    n = len(tokens)
    start = end = 0
    step = 1
    count = 0
    ranges: List[Tuple[int, int]] = []
    from_form = False

    # 可选前导 from
    if i < n and ir.normalize_select_op(tokens[i]) == "from":
        from_form = True
        i += 1

    # 首值：数字 或 lo-hi 紧凑区间
    if i >= n:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}选择缺少起始编号"))
        return None
    first = tokens[i]
    try:
        lo, hi = _parse_one_range(first)
    except ValueError:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}无效编号: {first}"))
        return None
    i += 1
    if lo != hi:               # 紧凑区间 lo-hi
        start, end = lo, hi
    else:
        start = lo

    # 后续算子：thru / step / count（顺序任意，可缺省）
    while i < n:
        op = ir.normalize_select_op(tokens[i])
        if op is None:
            errors.append(ir.ParseError(lineno, 1, f"{ctx}选择中未知记号: {tokens[i]}"))
            return None
        i += 1
        if i >= n or not _NUM_TOK_RE.match(tokens[i]):
            errors.append(ir.ParseError(lineno, 1, f"{ctx}{op} 后缺少数字"))
            return None
        val = int(tokens[i]); i += 1
        if op == "thru":
            end = val
        elif op == "step":
            if val < 1:
                errors.append(ir.ParseError(lineno, 1, f"{ctx}step 必须 ≥1: {val}"))
                return None
            step = val
        elif op == "count":
            if val < 1:
                errors.append(ir.ParseError(lineno, 1, f"{ctx}count 必须 ≥1: {val}"))
                return None
            count = val
        elif op == "from":
            errors.append(ir.ParseError(lineno, 1, f"{ctx}from 只能在最前"))
            return None

    # 校验起点
    if start < 1:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}起始编号必须 ≥1: {start}"))
        return None
    if end and end < start:
        errors.append(ir.ParseError(lineno, 1, f"{ctx}区间方向颠倒(应 小→大): {start}-{end}"))
        return None

    term = ir.SelectorTerm(kind=kind, group_ref=group_ref, step=step, count=count)
    if from_form or count:
        # from 式：start + step + count(+可选 end)
        term.start = start
        term.end = end
    else:
        # 区间式：start..end(无 end 则单个)
        term.ranges = [(start, end if end else start)]
    return term


def _split_group_prefix(tok: str) -> Tuple[Optional[str], str]:
    """从一个选择表达式记号串里剥离「组名.」前缀。

    返回 (group_ref 或 None, 余下表达式串)。
    - "外圈 灯".1 thru 8   → ("外圈 灯", "1 thru 8")
    - 环1.2-4              → ("环1", "2-4")
    - 1 thru 10            → (None, "1 thru 10")
    """
    tok = tok.strip()
    if not tok:
        return None, ""
    if tok[0] in ("'", '"'):
        q = tok[0]
        endq = tok.find(q, 1)
        if endq == -1:
            return tok[1:].strip(), ""
        name = tok[1:endq]
        rest = tok[endq + 1:].strip()
        if rest.startswith("."):
            return name, rest[1:].strip()
        return name, rest   # 引号组名后无 .：整组(rest 应为空)
    # 无引号：找首个 '.'，但前缀须含非数字(否则是 1.5 这类被误写的浮点)
    if "." in tok:
        head = tok.split(".", 1)[0].strip()
        if head and not head.replace("-", "").isdigit():
            name, _, rest = tok.partition(".")
            return name.strip(), rest.strip()
    return None, tok


def parse_selector(rest: str, lineno: int, errors: List[ir.ParseError]) -> List[ir.SelectorTerm]:
    """解析 slct 之后的选择器串 → 若干 SelectorTerm（控台风格）。

    逗号分隔多个选择表达式，每个表达式可为：
      全局：  1 / 1-10 / 1 thru 10 step 3 / from 1 step 3 count 4
      整组：  环1 / "外圈 灯"
      组内：  环1.2-4 / "外圈 灯".1 thru 8 step 2 / 环1.from 1 step 2 count 4
    step=相邻选中编号相差 N；count=取个数。
    """
    terms: List[ir.SelectorTerm] = []
    for piece in _split_commas_respect_quotes(rest):
        piece = piece.strip()
        if not piece:
            continue
        group_ref, expr = _split_group_prefix(piece)
        if group_ref is not None:
            expr = expr.strip()
            if expr == "":
                # 整组
                terms.append(ir.SelectorTerm(kind=ir.SELECT_GROUP, group_ref=group_ref))
                continue
            toks = expr.split()
            # 组内下标(可带 thru/step/count)
            term = _parse_step_expr(toks, lineno, errors, ir.SELECT_GROUP_INDEX, group_ref, "组内")
            if term is not None:
                terms.append(term)
            continue
        # 无组前缀：all / type X / 全局数字表达式 / 纯组名
        toks = expr.split()
        head = toks[0] if toks else ""
        # #14: slct all → 全部设备
        if ir.is_select_all_token(head):
            if len(toks) > 1:
                errors.append(ir.ParseError(lineno, 1, f"all 之后不应有内容: {' '.join(toks[1:])[:30]}"))
            terms.append(ir.SelectorTerm(kind=ir.SELECT_ALL))
            continue
        # #14: slct type pump → 配接含该通道类型的全部设备
        if ir.is_select_type_token(head):
            if len(toks) < 2:
                errors.append(ir.ParseError(lineno, 1, "type 后缺少通道类型(如 type pump)"))
                continue
            tref = ir.normalize_channel(toks[1]) or toks[1]
            if len(toks) > 2:
                errors.append(ir.ParseError(lineno, 1, f"type {toks[1]} 之后不应有内容: {' '.join(toks[2:])[:30]}"))
            terms.append(ir.SelectorTerm(kind=ir.SELECT_TYPE, type_ref=tref))
            continue
        if _NUM_TOK_RE.match(head) or "-" in head and head.split("-")[0].isdigit() \
                or ir.normalize_select_op(head) == "from":
            term = _parse_step_expr(toks, lineno, errors, ir.SELECT_GLOBAL, "", "全局")
            if term is not None:
                terms.append(term)
        else:
            # 纯组名(无引号、无下标)。#2: 若后面跟了 thru/step/count/from,几乎肯定是
            # 想对整组做步进——但整组没有可步进的下标,报错而非把算子静默吞进组名。
            op_pos = next((k for k in range(1, len(toks))
                           if ir.normalize_select_op(toks[k]) is not None), -1)
            if op_pos != -1:
                gname = " ".join(toks[:op_pos]) or expr
                errors.append(ir.ParseError(
                    lineno, 1,
                    f"整组 '{gname}' 不能直接跟 {toks[op_pos]};组内步进请写 `{gname}.1 thru N step S`"))
                # 仍按整组选中,让用户至少选到设备
                terms.append(ir.SelectorTerm(kind=ir.SELECT_GROUP, group_ref=gname))
            else:
                terms.append(ir.SelectorTerm(kind=ir.SELECT_GROUP, group_ref=expr))
    return terms


# ─────────────────────────────────────────────────────────────────────────────
#  属性段解析（一个 ';' 分隔的段里可能含多个空格分隔的属性）
# ─────────────────────────────────────────────────────────────────────────────
_TRACK_LIGHT_ATK = {"atk", "attack", "攻击", "起音"}
_TRACK_LIGHT_REL = {"rel", "release", "释放"}
_TRACK_LIGHT_COLOR = {"color", "col", "颜色"}
_REC_SRC_TRUE = {"on", "true", "1", "开", "yes"}
_REC_SRC_FALSE = {"off", "false", "0", "关", "no"}


def _take_num(toks: List[str], i: int, lineno: int, errors: List[ir.ParseError], name: str,
              unit: bool = False) -> Tuple[Optional[float], int]:
    """取一个数值。unit=True 走 parse_duration(允许 's');否则 parse_num(拒绝 's')。
    #9: 若下一个 token 本身是关键字,视为缺值并报错(防把后续关键字吞成参数)。"""
    if i >= len(toks):
        errors.append(ir.ParseError(lineno, 1, f"{name} 缺少数值"))
        return None, i
    tok = toks[i]
    if ir.normalize_keyword(tok) is not None:
        errors.append(ir.ParseError(lineno, 1, f"{name} 缺少数值(后面是关键字 {tok})"))
        return None, i
    try:
        v = parse_duration(tok) if unit else parse_num(tok)
        return v, i + 1
    except ValueError:
        hint = "(计数/比例字段不可带 's')" if (not unit and tok.strip().lower().endswith("s")) else ""
        errors.append(ir.ParseError(lineno, 1, f"{name} 数值无效: {tok}{hint}"))
        return None, i + 1


def _parse_track_light(toks: List[str], i: int, clip: ir.ClipStmt, lineno: int, errors: List[ir.ParseError]) -> int:
    if i >= len(toks):
        errors.append(ir.ParseError(lineno, 1, "track_light 缺少 source@threshold"))
        return i
    spec = toks[i]
    i += 1
    # #8: 接受 src@thr 或 src=thr(= 作 @ 的别名,与 param/fade_ratio 的 = 统一)
    parts = re.split(r"[@=]", spec, 1)
    src_part = parts[0]
    sep = len(parts) > 1
    thr = parts[1] if len(parts) > 1 else ""
    src = ir.normalize_track_source(src_part)
    if src is None:
        errors.append(ir.ParseError(lineno, 1, f"未知灯跟踪来源: {src_part}"))
        clip.track_light_source = src_part.strip() or "pump"
    else:
        clip.track_light_source = src
    if sep and thr.strip():
        try:
            clip.track_light_threshold = int(parse_num(thr))
        except ValueError:
            errors.append(ir.ParseError(lineno, 1, f"track_light 阈值无效: {thr}"))
    clip.track_light_enabled = True
    while i < len(toks):
        sub = toks[i].lower()
        if sub in _TRACK_LIGHT_ATK:
            if i + 1 < len(toks) and _is_num(toks[i + 1]):
                clip.track_light_attack = parse_num(toks[i + 1])
                i += 2
            else:
                errors.append(ir.ParseError(lineno, 1, "track_light atk 缺少数值"))
                i += 1
                break
        elif sub in _TRACK_LIGHT_REL:
            if i + 1 < len(toks) and _is_num(toks[i + 1]):
                clip.track_light_release = parse_num(toks[i + 1])
                i += 2
            else:
                errors.append(ir.ParseError(lineno, 1, "track_light rel 缺少数值"))
                i += 1
                break
        elif sub in _TRACK_LIGHT_COLOR:
            if i + 1 < len(toks):
                clip.track_light_color = toks[i + 1]
                i += 2
            else:
                errors.append(ir.ParseError(lineno, 1, "track_light color 缺少颜色值"))
                i += 1
                break
        else:
            break
    return i


def parse_attr_segment(seg: str, clip: ir.ClipStmt, lineno: int, errors: List[ir.ParseError]) -> None:
    """解析一个 ';' 段：把首词按关键字消费其值，可连续多组。"""
    toks = seg.split()
    i = 0
    n = len(toks)
    while i < n:
        w = toks[i]
        kw = ir.normalize_keyword(w)
        if kw is None:
            errors.append(ir.ParseError(lineno, 1, f"未知关键字: {w}"))
            i += 1
            continue
        i += 1

        if kw == "slct":
            rest = " ".join(toks[i:])
            i = n
            clip.selector.terms.extend(parse_selector(rest, lineno, errors))
        elif kw == "at":
            # #15a: 相对偏移 '+2s' 暂不支持,显式报错而非静默当绝对值
            if i < n and toks[i].lstrip().startswith("+"):
                errors.append(ir.ParseError(lineno, 1, "at 暂不支持相对偏移(+Ns);请用绝对秒,或省略 at 自动接在上一条之后"))
                i += 1
            else:
                v, i = _take_num(toks, i, lineno, errors, "at", unit=True)
                if v is not None:
                    clip.at = v
                    clip.at_explicit = True
        elif kw == "dur":
            v, i = _take_num(toks, i, lineno, errors, "dur", unit=True)
            if v is not None:
                clip.dur = v
                clip.dur_explicit = True
        elif kw == "fi":
            v, i = _take_num(toks, i, lineno, errors, "fi", unit=True)
            if v is not None:
                clip.fade_in = v
        elif kw == "fo":
            v, i = _take_num(toks, i, lineno, errors, "fo", unit=True)
            if v is not None:
                clip.fade_out = v
        elif kw == "dir":
            if i >= n:
                errors.append(ir.ParseError(lineno, 1, "dir 缺少方向值"))
            else:
                d = ir.normalize_direction(toks[i])
                if d is None:
                    errors.append(ir.ParseError(lineno, 1, f"未知方向: {toks[i]}"))
                else:
                    clip.direction = d
                i += 1
        elif kw == "sets":
            v, i = _take_num(toks, i, lineno, errors, "sets")
            if v is not None:
                clip.sets = int(v)
        elif kw == "seq":
            clip.sequence_mode = "sequential"
        elif kw == "parallel":
            clip.sequence_mode = "parallel"
        elif kw == "pause":
            v, i = _take_num(toks, i, lineno, errors, "pause", unit=True)
            if v is not None:
                clip.pause = v
        elif kw == "batches":
            v, i = _take_num(toks, i, lineno, errors, "batches")
            if v is not None:
                clip.batches = int(v)
        elif kw == "scope":
            if i >= n:
                errors.append(ir.ParseError(lineno, 1, "scope 缺少坐标系值"))
            else:
                sc = ir.normalize_scope(toks[i])
                if sc is None:
                    errors.append(ir.ParseError(lineno, 1, f"未知坐标系: {toks[i]}"))
                else:
                    clip.scope = sc
                i += 1
        elif kw in wing_attributes.KEYS:
            i = wing_attributes.parse(clip, kw, toks, i, line=lineno, errors=errors, number=_take_num)
        elif kw == "param":
            matched = False
            while i < n and "=" in toks[i]:
                k2, _, v2 = toks[i].partition("=")
                k2 = k2.strip()
                if not k2:
                    errors.append(ir.ParseError(lineno, 1, f"param 缺少键名: {toks[i]}"))
                    i += 1
                    matched = True
                    continue
                # 值优先数值，否则按字符串存
                try:
                    pv: Any = parse_num(v2)
                except ValueError:
                    pv = v2.strip()
                clip.params[k2] = pv
                i += 1
                matched = True
            if not matched:
                errors.append(ir.ParseError(lineno, 1, "param 缺少 键=值"))
        elif kw == "seq_delay":
            nums: List[float] = []
            while i < n and _is_duration(toks[i]) and len(nums) < 2:
                nums.append(parse_duration(toks[i]))
                i += 1
            if not nums:
                errors.append(ir.ParseError(lineno, 1, "seq_delay 缺少数值"))
            else:
                clip.seq_delay_in = nums[0]
                clip.seq_delay_out = nums[1] if len(nums) > 1 else nums[0]
        elif kw == "fade_ratio":
            matched = False
            while i < n and "=" in toks[i]:
                k2, _, v2 = toks[i].partition("=")
                ch = ir.normalize_channel(k2) or k2.strip()
                try:
                    fv = parse_num(v2)
                except ValueError:
                    errors.append(ir.ParseError(lineno, 1, f"fade_ratio 数值无效: {toks[i]}"))
                    i += 1
                    matched = True
                    continue
                clip.fade_ratio_in[ch] = fv
                clip.fade_ratio_out[ch] = fv
                i += 1
                matched = True
            if not matched:
                errors.append(ir.ParseError(lineno, 1, "fade_ratio 缺少 通道=值"))
        elif kw == "track_light":
            i = _parse_track_light(toks, i, clip, lineno, errors)
        elif kw == "motorphase":
            v, i = _take_num(toks, i, lineno, errors, "motorphase")
            if v is not None:
                clip.motor_phase_enabled = True
                clip.motor_phase_amount = v
        elif kw == "hold":
            if i >= n:
                errors.append(ir.ParseError(lineno, 1, "hold 缺少模式值"))
            else:
                h = ir.normalize_hold(toks[i])
                if h is None:
                    errors.append(ir.ParseError(lineno, 1, f"未知保持模式: {toks[i]}"))
                else:
                    clip.hold_mode = h
                i += 1
        elif kw == "rate":
            v, i = _take_num(toks, i, lineno, errors, "rate")
            if v is not None:
                clip.rate = v
        elif kw == "smooth":
            if i >= n:
                errors.append(ir.ParseError(lineno, 1, "smooth 缺少预设名"))
            else:
                clip.smooth_preset = toks[i]
                i += 1
        elif kw == "rec_src":
            if i < n and toks[i].lower() in (_REC_SRC_TRUE | _REC_SRC_FALSE):
                clip.rec_src_enabled = toks[i].lower() in _REC_SRC_TRUE
                i += 1
            else:
                clip.rec_src_enabled = True
        elif kw == "def":
            errors.append(ir.ParseError(lineno, 1, "def 必须独占一行（不可与属性同段）"))
            i = n
        else:
            errors.append(ir.ParseError(lineno, 1, f"未处理的关键字: {w}"))


# ─────────────────────────────────────────────────────────────────────────────
#  单个 clip 块解析
# ─────────────────────────────────────────────────────────────────────────────
_SCALAR_RE = re.compile(r"^([^\s:]+)\s*:(.*)$")
_CLIP_HEAD_RE = re.compile(r"^\s*clip\b", re.IGNORECASE)
_TRACK_RE = re.compile(r"^(->)?\s*track\b", re.IGNORECASE)
_DEFAULT_HEAD_RE = re.compile(r"^\s*(default|默认|缺省)\b", re.IGNORECASE)

# default{} 可继承的属性字段(不含 selector/bodies/label/track_name/src_line/at*)。
# 每个 clip 在解析自身行之前先用 default 累积值 seed，自身显式行随后覆盖。
_SEED_FIELDS = (
    "dur", "dur_explicit", "fade_in", "fade_out", "direction",
    "sets", "sequence_mode", "pause", "pauses", "batches", "scope",
    "wings", "block_size", "wing_dir", "wing_mirror", "wing_reverse", "seq_delay_in", "seq_delay_out",
    "fade_ratio_in", "fade_ratio_out",
    "track_light_enabled", "track_light_source", "track_light_threshold",
    "track_light_attack", "track_light_release", "track_light_color",
    "motor_phase_enabled", "motor_phase_amount",
    "hold_mode", "rate", "smooth_preset", "rec_src_enabled",
)


def _apply_seed(clip: "ir.ClipStmt", seed: dict) -> None:
    """把 default 累积属性注入到一个新 clip(在其自身行解析之前)。"""
    import copy as _copy
    for k, v in seed.items():
        if k in ("params",):
            merged = dict(v or {}); merged.update(clip.params or {})
            clip.params = merged
        elif isinstance(v, (list, dict)):
            setattr(clip, k, _copy.deepcopy(v))
        else:
            setattr(clip, k, v)


def _collect_seed(default_clip: "ir.ClipStmt") -> dict:
    """从一个 default{} 解析出的 ClipStmt 收集「与全新默认不同」的属性 → seed dict。"""
    import copy as _copy
    base = ir.ClipStmt()
    seed: dict = {}
    for f in _SEED_FIELDS:
        cur = getattr(default_clip, f)
        if cur != getattr(base, f):
            seed[f] = _copy.deepcopy(cur) if isinstance(cur, (list, dict)) else cur
    if default_clip.params:
        seed["params"] = dict(default_clip.params)
    return seed


def _parse_clip(lines: List[str], start: int, errors: List[ir.ParseError],
                seed: Optional[dict] = None, is_default: bool = False) -> Tuple[Optional[ir.ClipStmt], int]:
    """从 lines[start]（'clip'/'default' 行）解析一个块，返回 (ClipStmt|None, 下一行索引)。

    seed: 由 default{} 块累积的默认属性字典；在解析本块自身行之前先注入(本块显式行覆盖默认)。
    is_default: True 表示这是个 default{} 块(无 label/selector 语义，仅收集属性作后续默认)。
    """
    n = len(lines)
    head_re = _DEFAULT_HEAD_RE if is_default else _CLIP_HEAD_RE

    # —— 收集块头（直到出现 '{'，'{' 可在本行或后续行）——
    hdr_parts: List[str] = []
    brace_line = -1
    remainder = ""
    j = start
    while j < n:
        s = _strip_comment(lines[j])
        # 防吞噬:见 '{' 之前先查下一个 clip/default 头(j>start)。若先撞上,说明本块
        # 漏写 '{',即便那行自带 '{' 也不能当作本块的左括号——否则会把下一块整段吞掉。
        if j > start and (_CLIP_HEAD_RE.match(s) or _DEFAULT_HEAD_RE.match(s)):
            errors.append(ir.ParseError(start + 1, 1, "缺少左花括号 '{'（已在下一个块前中止）"))
            return None, j
        if "{" in s:
            before, _, after = s.partition("{")
            hdr_parts.append(before)
            remainder = after.strip()
            brace_line = j
            break
        hdr_parts.append(s)
        j += 1

    if brace_line < 0:
        errors.append(ir.ParseError(start + 1, 1, "缺少左花括号 '{'"))
        return None, n

    header_text = " ".join(p.strip() for p in hdr_parts).strip()

    label = ""
    track_name = ""
    if is_default:
        # default 块头不应带 label/track；多余内容报错
        m = head_re.match(header_text)
        leftover = header_text[m.end():].strip() if m else header_text
        if leftover:
            errors.append(ir.ParseError(start + 1, 1, f"default 块头不应有内容: {leftover[:30]}"))
    else:
        # —— 解析块头：clip <label> [-> track <name>] ——
        m = _CLIP_HEAD_RE.match(header_text)
        ht = header_text[m.end():].strip() if m else header_text
        label, ht = _read_string_token(ht)
        mm = _TRACK_RE.match(ht)
        if mm:
            rest = ht[mm.end():].strip()
            track_name, leftover = _read_string_token(rest)
            # #7: '-> track' 出现但没给名字 → 报错
            if not track_name:
                errors.append(ir.ParseError(start + 1, 1, "track 后缺少轨道名(用 -> track \"名\")"))
            elif leftover.strip():
                errors.append(ir.ParseError(start + 1, 1, f"clip 块头轨道名后有多余内容: {leftover.strip()[:30]}"))
        elif ht.strip():
            # #7: label 之后、又不是 '-> track' 的多余 token → 报错
            errors.append(ir.ParseError(start + 1, 1, f"clip 块头有多余内容: {ht.strip()[:30]}（轨道请用 -> track \"名\"）"))

    clip = ir.ClipStmt(label=label, track_name=track_name, src_line=start + 1)
    # #16: 注入 default{} 累积的默认属性(本块显式行随后覆盖)
    if seed:
        _apply_seed(clip, seed)

    # —— 块体行按需取(O(1) 索引,不materialize到文档末尾)——
    # 性能关键(#perf): 旧实现对每个 clip 都把 brace_line+1..n 全量塞进 entries 列表,
    # 导致 parse() 整体 O(n²)(N clip × 文档尾长度) → 大脚本每次敲键 300ms+ 卡顿。
    # 改为按需计算第 p 个块体行,每个 clip 只扫到自己的 '}',整体 O(n)。
    has_rem = bool(remainder)
    rem_off = 1 if has_rem else 0
    m_entries = rem_off + (n - brace_line - 1)

    def _entry(idx: int) -> Tuple[int, int, str]:
        if has_rem and idx == 0:
            return (brace_line, brace_line + 1, remainder)
        k = brace_line + 1 + (idx - rem_off)
        return (k, k + 1, lines[k])

    closed = False
    close_global = n - 1
    p = 0
    while p < m_entries:
        gidx, lineno, raw = _entry(p)
        s = _strip_comment(raw)
        content = s.strip()
        if content == "":
            p += 1
            continue

        # 整行右花括号
        if content == "}":
            closed = True
            close_global = gidx
            p += 1
            break

        toks_c = content.split()

        # def 全式效果体：收集本行 + 后续更深缩进行
        if toks_c and toks_c[0].lower() == "def":
            def_indent = _leading_ws(raw)
            body_raw = [raw.rstrip("\n")]
            q = p + 1
            pending: List[str] = []
            while q < m_entries:
                _, _, r2 = _entry(q)
                if r2.strip() == "":
                    pending.append(r2.rstrip("\n"))
                    q += 1
                    continue
                if _leading_ws(r2) > def_indent:
                    body_raw.extend(pending)
                    pending = []
                    body_raw.append(r2.rstrip("\n"))
                    q += 1
                else:
                    break
            code = textwrap.dedent("\n".join(body_raw))
            clip.bodies.append(ir.ChannelBody(kind=ir.BODY_DEF, channel="", code=code, src_line=lineno))
            p = q
            continue

        # 行尾右花括号（与属性同行）。仅当 '}' 是**未匹配的多余闭括号**时才当块结束，
        # 否则它属于表达式本身（如标量体 `R: {1:2}` 的字典字面量），保留不动。
        close_after = False
        if content.endswith("}") and content.count("}") > content.count("{"):
            content = content[:-1].rstrip()
            close_after = True
            close_global = gidx
            if content == "":
                closed = True
                p += 1
                break

        # 标量通道体：<channel>: <expr>  (channel 可为逗号列表 R,G,B 做 fan-out)
        msc = _SCALAR_RE.match(content)
        if msc and ir.normalize_keyword(msc.group(1)) != "def":
            chan_raw = msc.group(1)
            # #1: 若冒号前是「已知关键字」且不是「已知通道」,几乎肯定是误加冒号的属性
            #     (如 `dur: 8` / `dir: ltr`),报错而非建一个假通道把属性吞掉。
            kw_hit = ir.normalize_keyword(chan_raw)
            if kw_hit is not None and ir.normalize_channel(chan_raw) is None:
                errors.append(ir.ParseError(
                    lineno, 1,
                    f"'{chan_raw}' 是属性关键字,不能用作通道(去掉冒号:写 `{chan_raw} 值`,不是 `{chan_raw}: 值`)"))
                if close_after:
                    closed = True; p += 1; break
                p += 1; continue
            # #17: 按引号安全地在第一个 ';' 处切分(引号内的 ';' 不切)。
            #      标量表达式不可能含裸 ';',故 ';' 一律视作分隔符;其后当作同行额外属性段。
            expr_full = msc.group(2)
            expr, tail = _split_first_semicolon(expr_full)
            expr = expr.strip()
            # #6: 通道名支持逗号列表 → 同一表达式 fan-out 到多个通道(R,G,B: 同值)
            chan_tokens = [c.strip() for c in chan_raw.split(",") if c.strip()]
            if not chan_tokens:
                errors.append(ir.ParseError(lineno, 1, "通道名为空"))
            else:
                for ct in chan_tokens:
                    # 通道名 project-aware:已知别名规范化,未知保留原名(渲染期对配接校验)。
                    ch = ir.normalize_channel(ct) or ct
                    clip.bodies.append(ir.ChannelBody(kind=ir.BODY_SCALAR, channel=ch, code=expr, src_line=lineno))
            # 表达式后若还有 ';' 分隔的属性段,逐段解析(如 `pump: 200; dir ltr`)
            if tail.strip():
                for seg in _split_semicolons(tail):
                    if seg.strip():
                        parse_attr_segment(seg, clip, lineno, errors)
            if close_after:
                closed = True
                p += 1
                break
            p += 1
            continue

        # 否则是属性行（可能含多个 ';' 段，引号安全）
        for seg in _split_semicolons(content):
            if seg.strip():
                parse_attr_segment(seg, clip, lineno, errors)

        if close_after:
            closed = True
            p += 1
            break
        p += 1

    if not closed:
        errors.append(ir.ParseError(clip.src_line, 1, "缺少右花括号 '}'"))
        return clip, n

    return clip, close_global + 1


# ─────────────────────────────────────────────────────────────────────────────
#  顶层 API
# ─────────────────────────────────────────────────────────────────────────────
def parse(text: str) -> ir.ParseResult:
    """容错解析整段文本，返回 ParseResult(clips, errors, warnings)。

    支持顶层 `default { ... }` 块(#16):其属性作为后续 clip 的默认，clip 自身行覆盖。
    多个 default 块按出现顺序累积(后者覆盖前者同名属性)。
    """
    if text is None:
        return ir.ParseResult(clips=[], errors=[], warnings=[])
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    n = len(lines)

    clips: List[ir.ClipStmt] = []
    errors: List[ir.ParseError] = []
    warnings: List[ir.ParseError] = []
    seed: dict = {}

    i = 0
    while i < n:
        raw = lines[i]
        content = _strip_comment(raw).strip()
        if content == "":
            i += 1
            continue
        first = content.split()[0]
        if _DEFAULT_HEAD_RE.match(content):
            dflt, nxt = _parse_clip(lines, i, errors, seed=None, is_default=True)
            if dflt is not None:
                # 累积到 seed(后续 default 覆盖前者)
                new_seed = _collect_seed(dflt)
                if "params" in new_seed:
                    merged_p = dict(seed.get("params", {})); merged_p.update(new_seed["params"])
                    seed.update(new_seed); seed["params"] = merged_p
                else:
                    seed.update(new_seed)
            i = nxt if nxt > i else i + 1
        elif first.lower() == "clip":
            clip, nxt = _parse_clip(lines, i, errors, seed=seed)
            if clip is not None:
                clips.append(clip)
                # #21: dur 未显式给出 → 非致命提示(默认 4s),除非是纯保持型(hold!=none 且无 body)
                if not clip.dur_explicit and not (clip.hold_mode != "none" and not clip.bodies):
                    warnings.append(ir.ParseError(clip.src_line, 1,
                        f"clip「{clip.label or '?'}」未写 dur，默认 {clip.dur:g}s"))
            i = nxt if nxt > i else i + 1
        else:
            errors.append(ir.ParseError(i + 1, 1, f"块外多余内容: {content[:40]}"))
            i += 1

    return ir.ParseResult(clips=clips, errors=errors, warnings=warnings)


def parse_or_raise(text: str) -> List[ir.ClipStmt]:
    """严格解析：有任何错误则抛 ir.CommandParseException，否则返回 clip 列表。"""
    result = parse(text)
    if result.errors:
        raise ir.CommandParseException(result.errors)
    return result.clips
