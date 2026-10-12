"""Optional AM23 state fields on the original normalization/signature contract."""
from .audio_mod_processor_schema import normalize_processors, processor_key
from .audio_mod_lifetime import timeline_gated


def processor_fields(raw, result):
    if timeline_gated(raw):
        result["timeline_gated"] = True
    if "processors" in raw or "processor_mode" in raw:
        result["processor_mode"] = "chain" if raw.get("processor_mode") == "chain" else "legacy"
        result["processors"] = normalize_processors(raw.get("processors"))
    return result


def processor_rule_key(state, original):
    if timeline_gated(state):
        original = (*original, ("timeline_gated", True))
    if "processors" not in state and "processor_mode" not in state:
        return original
    return (*original, state.get("processor_mode", "legacy"), processor_key(state.get("processors")))
