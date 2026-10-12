"""R36 节目灯光状态查询；节目字段是唯一持久真源，None为未迁移旧档。"""


def active_program(project):
    """只读取当前明确节目，空状态不能误取第一个节目。"""
    program_id = str(getattr(project, "current_program_id", "") or "")
    if not program_id:
        return None
    return next((p for p in getattr(project, "programs", [])
                 if str(p.id) == program_id), None)


def active_light_enabled(project):
    """新节目bool，旧未迁移/无节目None；由原会话门控承担旧兼容。"""
    value = getattr(active_program(project), "light_enabled", None)
    return value if isinstance(value, bool) else None


def has_program_light_state(project):
    """已明确节目字段的项目不再让旧Studio卡回写全局门控。"""
    return any(isinstance(getattr(p, "light_enabled", None), bool)
               for p in getattr(project, "programs", []))
