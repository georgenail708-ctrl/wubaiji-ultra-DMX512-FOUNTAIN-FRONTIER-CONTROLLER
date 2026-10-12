# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_search.py — R183 W2-FX 搜索语法（纯函数，无 Qt）。

深查② §一 #4：``cat:电机 tag:快速 -strobe`` 前缀 token 分流。
  前缀：``cat:``（细分类 / 一级大类名 / 大类键）、``tag:``（标签子串）、
        ``name:``（仅名称）；``-`` 前缀 = 取反；无前缀 token = 全字段子串（旧行为）。

**旧语义只增不减**：调用方（``effect_categories.preset_matches_query``）先按旧
朴素 AND 整句试一次，命中即返回 —— 因此"以前能搜到的现在还一定搜得到"；
只有旧语义落空才走本模块的前缀/取反解释。

为什么独立模块：``preset_matches_query`` 的行数与 CC 都在门禁基线内零余量，
前缀词法（引号/取反/字段白名单）与"全库 hay 拼装"是两件事，分开后可单独定测。
"""
from __future__ import annotations

from typing import List, Tuple

#: 前缀 → 目标字段（未列出的前缀按普通词处理：``foo:bar`` 仍走全字段子串）
FIELD_PREFIXES = {
    "cat": "cat", "category": "cat", "分类": "cat",
    "tag": "tag", "tags": "tag", "标签": "tag",
    "name": "name", "名称": "name",
}
#: 字段名（供定测/诊断）
FIELDS = ("cat", "tag", "name")


def split_words(text: str) -> List[str]:
    """按空白分词；双引号包裹的片段保留空格（``"chase 追光"`` 算一个词）。"""
    words: List[str] = []
    buf: List[str] = []
    quoted = False
    for ch in str(text or ""):
        if ch == '"':
            quoted = not quoted
            continue
        if ch.isspace() and not quoted:
            if buf:
                words.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        words.append("".join(buf))
    return words


def parse_one(word: str) -> Tuple[str, str, bool]:
    """单个词 → ``(字段, 文本, 是否取反)``（字段不在白名单 → 空 = 全字段）。"""
    word = str(word or "")
    negated = word.startswith("-") and len(word) > 1
    body = word[1:] if negated else word
    head, sep, tail = body.partition(":")
    field = FIELD_PREFIXES.get(head.lower(), "") if (sep and tail) else ""
    if field:
        body = tail
    return field, body.lower(), negated


def parse_tokens(query) -> List[Tuple[str, str, bool]]:
    """查询 → ``[(字段, 文本, 取反)]``（字段 ∈ {"", "cat", "tag", "name"}）。"""
    out: List[Tuple[str, str, bool]] = []
    for word in split_words(str(query or "")):
        field, body, negated = parse_one(word)
        if body:
            out.append((field, body, negated))
    return out


def has_field_tokens(query) -> bool:
    """查询里是否含**字段前缀** token（判断"是否需要走新语法"）。"""
    return any(field for field, _body, _neg in parse_tokens(query))


def _hit(field: str, body: str, negated: bool, hay: dict) -> bool:
    """单 token 命中判定（负 token = 该字段不含此子串）。"""
    target = hay.get(field, "") if field in FIELDS else hay.get("all", "")
    found = body in target
    return (not found) if negated else found


def build_hay(hay_all: str, name: str, cat_texts, tags) -> dict:
    """把各字段拼成判定用的 {字段: 小写文本}（``all`` = 旧语义的全字段串）。"""
    return {
        "all": str(hay_all or "").lower(),
        "name": str(name or "").lower(),
        "tag": " ".join(str(t) for t in (tags or ())).lower(),
        "cat": group_labels(cat_texts),
    }


def matches(tokens, *, hay_all: str, name: str = "", cat_texts=(),
            tags=()) -> bool:
    """分词 AND 判定（纯函数）。

    - 无前缀：子串命中 ``hay_all``（旧语义，大小写不敏感）；
    - ``cat:``：子串命中"原始分类 + 规范分类 + 大类键 + 大类名"；
    - ``tag:``：子串命中该预设的标签串（内置与用户标签都在内）；
    - ``name:``：子串命中名称；取反则"不命中才算通过"。
    """
    hay = build_hay(hay_all, name, cat_texts, tags)
    return all(_hit(str(field or ""), str(body), bool(negated), hay)
               for field, body, negated in (tokens or ()) if body)


def group_labels(cat_texts) -> str:
    """分类文本 → 一级大类名 + 大类键（``cat:灯光`` 与 ``cat:light`` 都能命中）。"""
    try:
        from .effect_categories import top_group, top_group_key
    except Exception:                      # 循环 import 兜底：只按细分类文本匹配
        return " ".join(str(c or "") for c in (cat_texts or ())).lower()
    parts: List[str] = []
    for raw in (cat_texts or ()):
        parts.extend(_label_parts(raw, top_group, top_group_key))
    return " ".join(parts).lower()


def _label_parts(raw, top_group, top_group_key) -> List[str]:
    """单条分类文本的匹配面（原始 + 大类键 + 大类名）。"""
    text = str(raw or "")
    if not text:
        return []
    key = top_group_key(text)
    group = top_group(key) if key else None
    return [text, str(key or ""), getattr(group, "label", "") or ""]


__all__ = ["FIELDS", "FIELD_PREFIXES", "build_hay", "group_labels", "has_field_tokens",
           "matches", "parse_one", "parse_tokens", "split_words"]
