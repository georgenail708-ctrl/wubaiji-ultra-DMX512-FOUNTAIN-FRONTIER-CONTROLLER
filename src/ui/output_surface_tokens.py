"""Explicit neutral resources for actual light/pixel/motion output domains.

Original output backdrop/grid/readout contrast survives chrome theme migration.
These are presentation colors, never RGB fixture values or mask semantics.
"""
from .qascade_theme import (  # noqa: F401
    OUTPUT_BG_ROOT, OUTPUT_BG_DEEP, OUTPUT_BG_EDITOR, OUTPUT_BG_PANEL,
    OUTPUT_BG_PANEL_SUB, OUTPUT_BG_BOX, OUTPUT_BG_INPUT, OUTPUT_BG_INPUT_EDIT,
    OUTPUT_BG_BUTTON, OUTPUT_BORDER_NORMAL, OUTPUT_TEXT_PRIMARY,
    OUTPUT_TEXT_SECONDARY, OUTPUT_TEXT_DISABLED, OUTPUT_TEXT_SELECTED_FG,
)
