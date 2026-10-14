"""主窗关闭时先停止生产者，避免 Qt 析构后后台任务继续写入。"""
from PyQt6.QtCore import QTimer
from .route_animator.runner_lifecycle import stop_window_runner


def _stop_if_present(owner, method_name: str) -> None:
    """组合现有生命周期接口；异常必须保留，不能伪装停止成功。"""
    method = getattr(owner, method_name, None)
    if callable(method):
        method()


def stop_window_linker(window, *, clear_state: bool = False) -> bool:
    """Stop only the existing Linker owned by this window's network manager."""
    from ..processing import track_linker_engine
    engine = track_linker_engine._engine_instance
    nm = getattr(window, "_nm", None)
    if engine is None or nm is None or engine._nm is not nm:
        return False
    panel = getattr(window, "_processor_widget", None)
    if getattr(panel, "_engine", None) is engine:
        panel._on_enable_toggled(False)
    else:
        engine.set_enabled(False)
    if clear_state:
        engine.from_dict({})
    return True


def shutdown_window_workers(window) -> None:
    """完成 bake→gen→引擎收尾，再收时间线、面板与音频所有者。"""
    sntc_error = None
    try:
        _stop_if_present(getattr(window, "_sntc_bridge", None), "force_close")
    except Exception as error:
        sntc_error = error
    _stop_if_present(getattr(window, "_timeline_baker", None), "shutdown")
    engine = getattr(window, "_engine", None)
    _stop_if_present(engine, "stop_gen_thread")
    _stop_if_present(engine, "stop")

    timeline = getattr(window, "_timeline_widget", None)
    _stop_if_present(getattr(timeline, "ctrl", None), "pause")
    _stop_if_present(timeline, "audio_stop")
    _stop_if_present(window, "_pause_live_effect_outputs")
    _stop_if_present(getattr(window, "_programmer_widget", None), "clear_live_output")
    stop_window_runner(window)
    matrix = getattr(window, "_matrix_pattern_widget", None)
    _stop_if_present(getattr(matrix, "_pixel_engine", None), "stop")
    _stop_if_present(getattr(matrix, "_engine", None), "stop")
    # ★ R193/HF-01：网络页轮询 timer/socket 收口（config.shutdown 幂等且
    #   已级联 _discovery_panel；原先全仓无调用方）
    _stop_if_present(getattr(window, "_network_widget", None), "shutdown")

    from . import audio_driver
    _stop_if_present(audio_driver._global_analyzer, "stop")
    for timer in window.findChildren(QTimer):
        timer.stop()
    from ..engine.timeline_cache_lifecycle import retire_engine_frame_stores
    retire_engine_frame_stores(engine)
    if sntc_error is not None:
        raise sntc_error
