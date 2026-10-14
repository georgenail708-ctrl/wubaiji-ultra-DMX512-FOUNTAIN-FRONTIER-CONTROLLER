"""General import-only world position through original parent transforms.

Reuse scipy Rotation and the existing scalar-last installation convention.
The object origin receives parent transforms, never its own rotation/scale.
"""
from functools import lru_cache

from scipy.spatial.transform import Rotation

from ..utils.fixture_installation import euler_quaternion

ZERO_GUID = '00000000-0000-0000-0000-000000000000'


@lru_cache(maxsize=2048)
def _rotation(quaternion, rx, ry, rz):
    return Rotation.from_quat(quaternion or euler_quaternion(rx, ry, rz))


def world_position(guid, fmap):
    node = fmap.get(guid)
    if node is None:
        return (0.0, 0.0, 0.0)
    point = (node.pos_x, node.pos_y, node.pos_z)
    parent, seen = node.parent_guid, {guid}
    while parent != ZERO_GUID and parent in fmap and parent not in seen:
        seen.add(parent)
        node = fmap[parent]
        scaled = tuple(value * scale for value, scale in zip(point, node.scale))
        rotated = _rotation(node.quaternion, node.rot_x, node.rot_y, node.rot_z).apply(scaled)
        point = tuple(float(value + offset) for value, offset in zip(
            rotated, (node.pos_x, node.pos_y, node.pos_z)))
        parent = node.parent_guid
    return point
