"""
matrix_slideshow — 矩阵 ON/OFF PPT 编辑器包
"""
from .matrix_slideshow_panel import MatrixSlideshowPanel
from .matrix_slide_engine import (
    MatrixSlide, MatrixSlideshowConfig, MatrixSlideEngine,
    TRANS_MODES, PLAY_MODES, BUILTIN_SEQUENCES,
    CH_PUMP, CH_VALVE, CH_DIMMER,
)
from .matrix_pixel_engine import MatrixPixelEngine, PIXEL_MODES

__all__ = [
    "MatrixSlideshowPanel",
    "MatrixSlide", "MatrixSlideshowConfig", "MatrixSlideEngine",
    "TRANS_MODES", "PLAY_MODES", "BUILTIN_SEQUENCES",
    "CH_PUMP", "CH_VALVE", "CH_DIMMER",
    "MatrixPixelEngine", "PIXEL_MODES",
]
