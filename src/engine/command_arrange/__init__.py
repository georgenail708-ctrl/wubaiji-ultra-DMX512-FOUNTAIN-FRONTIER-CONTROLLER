"""command_arrange — 命令行代码编曲（人/AI 写命令 → 解析 → 生成 clip 到时间线）。

单向流水：parse(text) → [ClipStmt] → render(clips, project) → {track:[Clip]}。
纯 core 模块（ir/lexer_parser/sandbox/renderer/skill_export），UI 在 ui 层。
"""
from __future__ import annotations

from .ir import (
    ClipStmt, ChannelBody, Selector, SelectorTerm,
    ParseError, ParseResult, CommandParseException,
    DIRECTIONS, CHANNEL_TYPES, KEYWORDS,
    normalize_direction, normalize_channel, normalize_keyword,
    normalize_scope, normalize_hold, normalize_track_source, normalize_wing_dir,
    BODY_SCALAR, BODY_DEF,
    SELECT_GLOBAL, SELECT_GROUP, SELECT_GROUP_INDEX,
)

__all__ = [
    "ClipStmt", "ChannelBody", "Selector", "SelectorTerm",
    "ParseError", "ParseResult", "CommandParseException",
    "DIRECTIONS", "CHANNEL_TYPES", "KEYWORDS",
    "normalize_direction", "normalize_channel", "normalize_keyword",
    "normalize_scope", "normalize_hold", "normalize_track_source", "normalize_wing_dir",
    "BODY_SCALAR", "BODY_DEF",
    "SELECT_GLOBAL", "SELECT_GROUP", "SELECT_GROUP_INDEX",
    "parse", "parse_or_raise", "render", "build_skill_markdown", "suggested_filename",
]


def parse(text):
    from .lexer_parser import parse as _p
    return _p(text)


def parse_or_raise(text):
    from .lexer_parser import parse_or_raise as _p
    return _p(text)


def render(clips_or_result, project, fps=40):
    from .renderer import render as _r
    return _r(clips_or_result, project, fps=fps)


def build_skill_markdown(project):
    from .skill_export import build_skill_markdown as _b
    return _b(project)


def suggested_filename(project):
    from .skill_export import suggested_filename as _s
    return _s(project)
