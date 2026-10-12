"""Qt-native plan-view camera and nozzle-direction adapter for final output.

Directly reuses QMatrix4x4 perspective/lookAt/rotate/mapVector; no GL context.
Qt v6.11.0 math3d API; adoption: r185-basic-closeout-preview.md.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QMatrix4x4, QVector3D, QVector4D


def fixture_direction(output):
    return fixture_pose(output)[1]


def fixture_pose(output):
    transform = QMatrix4x4()
    profile = output.fixture.profile
    rx, ry, rz = profile.get("rotation", output.fixture.rotation)
    transform.rotate(ry, 0, 1, 0)
    transform.rotate(rx, 1, 0, 0)
    transform.rotate(rz, 0, 0, 1)
    anchor = QVector3D(*profile.get("anchor", (0, 0, 0)))
    for kind, motion, actual, value in output.movements:
        axis = QVector3D(*motion["axis"])
        if motion["role"] == "rotate" and motion["unit"] == "deg":
            transform.rotate(actual, axis)
    direction = transform.mapVector(QVector3D(0, 1, 0)).normalized()
    return ((anchor.x(), anchor.y(), anchor.z()),
            (direction.x(), direction.y(), direction.z()))


class PreviewCamera:
    def __init__(self):
        self.center = QVector3D()
        self.span = 1000.0
        self.zoom = 1.0
        self.pan = QPointF()
        self.matrix = QMatrix4x4()
        self.width, self.height = 1, 1

    def fit(self, outputs, selected=()):
        chosen = [x for x in outputs if x.fixture.fixture_id in selected] if selected else outputs
        if not chosen:
            chosen = outputs
        points = [self.position(x) for x in chosen]
        if points:
            minima = [min(p[i] for p in points) for i in range(3)]
            maxima = [max(p[i] for p in points) for i in range(3)]
            self.center = QVector3D(*[(a + b) / 2 for a, b in zip(minima, maxima)])
            self.span = max(300.0, *(b - a for a, b in zip(minima, maxima))) * 1.25
        self.pan = QPointF()
        self.zoom = 1.0

    def position(self, output):
        x, y = output.fixture.position_2d
        return x, 0.0, y

    def configure(self, width, height):
        self.width, self.height = max(1, width), max(1, height)
        aspect = self.width / self.height
        projection, view = QMatrix4x4(), QMatrix4x4()
        distance = self.span / self.zoom
        view.lookAt(self.center + QVector3D(0, distance, 0), self.center, QVector3D(0, 0, -1))
        half = distance / 2
        projection.ortho(-half * aspect, half * aspect, -half, half, 0.1, distance * 3)
        self.matrix = projection * view

    def project(self, point):
        clip = self.matrix.map(QVector4D(*point, 1))
        if clip.w() <= 0:
            return None
        x, y, z = clip.x() / clip.w(), clip.y() / clip.w(), clip.z() / clip.w()
        if z < -1 or z > 1:
            return None
        return QPointF((x + 1) * self.width / 2 + self.pan.x(),
                       (1 - y) * self.height / 2 + self.pan.y())

    def drag(self, delta, *, pan=False):
        self.pan += delta

    def wheel(self, steps):
        self.zoom = max(0.05, min(30.0, self.zoom * (1.15 ** steps)))
