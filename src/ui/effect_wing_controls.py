"""Universal wing metadata; widgets and values still use Effect's ParamSpec."""


def append_specs(preset, existing):
    from .effect_engine import ParamSpec
    specs = [
        ParamSpec("wings", "翼", "int", 1, 1, 32, 1, unit="个",
                  tooltip="把设备序列切成若干翼，每翼运行完全一致的波形。"),
        ParamSpec("block_size", "块", "int", 1, 1, 64, 1, unit="台",
                  tooltip="相邻N台共用一个值，形成阶梯效果。"),
        ParamSpec("wing_mirror", "反射对称", "bool", False, tooltip="相邻翼交替反射"),
        ParamSpec("wing_reverse", "倒转", "bool", False, tooltip="倒转每翼的取值顺序"),
    ]
    preset.params.extend(spec for spec in specs if spec.key not in existing)
