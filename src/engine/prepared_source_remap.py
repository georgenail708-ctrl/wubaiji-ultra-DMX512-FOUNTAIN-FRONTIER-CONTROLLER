"""Choose the mounted source renderer while retaining its original closure."""
def bind_renderer(engine, original, clip, context):
    native = getattr(engine, "_ce_source_remap", None)
    if native is None:
        return original
    def render(copy_map, seed_fallback=False):
        return native(clip, copy_map, dict(context, seed=int(seed_fallback), fallback=original))
    return render
