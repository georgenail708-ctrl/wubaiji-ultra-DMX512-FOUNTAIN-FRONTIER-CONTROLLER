"""Return the existing NM mixer when its sole generator owner leaves.

The normal generator remains the serial render/mix owner. Waiting or a failed
Clip render must not prevent unrelated live Effect layers from being mixed.
No output layers, project origins, native caches or safety rules change here.
"""
from __future__ import annotations

import logging
import threading


log = logging.getLogger(__name__)


def log_frame_failure(key, error, message):
    # The existing clock backoff throttle already supplies bounded signatures
    # and suppression counts. Import only on failure after PlaybackEngine loads.
    from .playback_engine import _GEN_BACKOFF_THROTTLE, format_suppressed
    allowed, suppressed = _GEN_BACKOFF_THROTTLE.allow(
        key, (type(error).__name__, str(error)))
    if allowed:
        log.exception('%s%s', message, format_suppressed(suppressed))


def resume_existing_worker(network):
    if network is not None and hasattr(network, 'resume_worker_mix'):
        try:
            network.resume_worker_mix()
        except Exception:
            log.exception('生成线程退出后恢复现有输出 worker 失败')


def run_owned_generator(engine, network):
    try:
        engine._gen_loop()
    finally:
        # This runs before Thread.is_alive() becomes false. A replacement can
        # only pass the existing active/zombie guards after ownership returns.
        resume_existing_worker(network)


def start_owned_generator(engine):
    network = engine._nm
    engine._gen_stop.clear()
    thread = None
    try:
        if network is not None and hasattr(network, 'suspend_worker_mix'):
            network.suspend_worker_mix()
        thread = threading.Thread(target=run_owned_generator, args=(engine, network),
                                  name='qascade-gen', daemon=True)
        engine._gen_thread = thread
        thread.start()
    except BaseException:
        # A Thread that did enter its target owns the finally above. Do not
        # return mixing concurrently if a startup adapter raises after entry.
        if thread is None or not thread.is_alive():
            engine._gen_thread = None
            resume_existing_worker(network)
        raise


def return_worker_after_stop(thread, network):
    # join(timeout) can leave a live zombie inside its final render/mix. Keep
    # the worker suspended until that original owner reaches its finally.
    if not thread.is_alive():
        resume_existing_worker(network)


def mix_existing_network(engine):
    network = getattr(engine, '_nm', None)
    if network is not None and hasattr(network, '_mix_and_send'):
        try:
            network._mix_and_send()
        except Exception as error:
            log_frame_failure('gen-mix', error, '生成线程混合发送失败')


def render_and_mix(engine, position):
    try:
        engine._render_position(float(position), layer_name=engine._gen_layer_name)
    except Exception as error:
        log_frame_failure('gen-render', error, '生成线程 Clip render 失败，保留既有层并继续混音')
    if engine._gen_stop.is_set():
        return False
    mix_existing_network(engine)
    return True


def mix_while_waiting(engine):
    if engine._gen_stop.is_set():
        return True
    mix_existing_network(engine)
    return engine._gen_stop.wait(max(.001, float(getattr(engine, '_gen_interval_sec', .023))))
