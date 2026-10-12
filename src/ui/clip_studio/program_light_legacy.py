"""R36 legacy Studio ownership; the program field takes precedence."""
from ...core.program_lighting import has_program_light_state


def studio_owns_light(project):
    return not has_program_light_state(project)
