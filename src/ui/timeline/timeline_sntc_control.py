"""Optional SNTC control routing through the sole existing controller.

Ordinary bodies are preserved in their *_local methods. When synchronization is
active, the resident CE clock admits and applies local actions under one lock.
"""


def _bridge(controller):
    bridge = vars(controller).get('_sntc_bridge')
    if bridge is not None:
        from ...engine.timeline_sntc_source_guard import provider_current
        engine = vars(bridge).get('_engine')
        if engine is None or not provider_current(engine,bridge) or vars(bridge).get('_controller') is not controller:
            raise RuntimeError('Foreign external timeline control provider')
    return bridge


def perform_local(controller, method_name, *args):
    bridge = _bridge(controller)
    original = getattr(controller, method_name)
    if bridge is None:
        original(*args)
        return True
    return bridge.local_action(lambda: original(*args))


def perform_pointer(ruler, original, *args):
    controller = vars(ruler).get('_sntc_controller')
    bridge = _bridge(controller) if controller is not None else None
    if bridge is None:
        original(*args)
        return True
    return bridge.local_action(lambda: original(*args))


def external_tick(controller):
    bridge = _bridge(controller)
    return bridge.controller_tick() if bridge is not None else False


def release_external(controller):
    """Explicit project/scene/application release retires input before Stop."""
    bridge = _bridge(controller)
    if bridge is not None:
        bridge.set_enabled(False)
