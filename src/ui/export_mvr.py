# -*- coding: utf-8 -*-
"""Qascade → MVR (My Virtual Rig) 导出器。

MVR 是 DIN SPEC 15800 开放标准（灯光行业通用），支持 MVR 的主流灯光软件
主流灯光软件均可导入。格式 = ZIP(.mvr) 内含
GeneralSceneDescription.xml（GDTF 设备引用 + 矩阵变换 + DMX 地址）。

本模块从 Qascade 的 Project（FixtureDefinition + FixtureInstance）生成
最小合法 MVR——包含 Fixture 名称、DMX 地址、2D 位置（画布 cm→mm）和
占位 GDTF 引用（<设备类型名>.gdtf）。能以此完成设备布局 + 配接导入。

坐标系：
  Qascade TopView：pos_x/pos_y 单位=画布单位（通常≈cm），pos_z=高度(cm)
  MVR：毫米（mm），右手坐标系：X=右，Y=高度（Qascade pos_z）, Z=深度（Qascade pos_y）
  变换：x_mm = pos_x × 10，y_mm = pos_z × 10（高度），z_mm = pos_y × 10（深度）
"""
from __future__ import annotations

import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from ..core.models import FixtureDefinition

# 注(2026-08-14 对抗验证纠错): MVR/DIN SPEC 15800 规范不定义任何命名空间
# (libMVRgdtf 根节点仅 verMajor/verMinor/provider/providerVersion), 故不设 xmlns。
GDTF_GENERIC = "Generic@Generic Fixture"
CANVAS_CM_TO_MM = 10.0     # 1 Qascade canvas unit ≈ 1 cm → 10 mm


def _matrix3x4(pos_x_mm: float, pos_y_mm: float, pos_z_mm: float) -> str:
    """4 行 3 列变换矩阵字符串（MVR矩阵格式）。

    行 1-3：旋转（单位矩阵=无旋转），行 4：平移 (X,Y,Z in mm)。
    """
    return (
        f"{{1,0,0}},"
        f"{{0,1,0}},"
        f"{{0,0,1}},"
        f"{{{pos_x_mm:.1f},{pos_y_mm:.1f},{pos_z_mm:.1f}}}"
    )


def _dmx_absolute(universe_1based: int, channel_1based: int) -> int:
    """MVR 绝对 DMX 地址（break 0，0-based）。"""
    return (max(1, universe_1based) - 1) * 512 + (max(1, channel_1based) - 1)


def _make_uuid() -> str:
    return str(uuid.uuid4()).upper()


def build_gsd(project) -> str:
    """将 project 里的 FixtureDefinition + FixtureInstance 转成 GSD XML 字符串。"""
    root = ET.Element("GeneralSceneDescription")
    root.set("verMajor", "1")
    root.set("verMinor", "6")
    root.set("provider", "Qascade")
    root.set("providerVersion", "1.0")

    # 工程信息
    user_data = ET.SubElement(root, "UserData")
    ET.SubElement(user_data, "Data", key="CreatedBy", value="Qascade")
    ET.SubElement(user_data, "Data", key="Source", value="Qascade MVR Export")

    scenes = ET.SubElement(root, "Scenes")
    scene = ET.SubElement(scenes, "Scene")
    scene.set("name", getattr(project, "name", "Qascade Scene"))
    scene.set("uuid", _make_uuid())

    layers = ET.SubElement(scene, "Layers")

    # FixtureDefinition ID → 名称映射
    def_map: Dict[str, "FixtureDefinition"] = {}
    for fd in getattr(project, "fixture_defs", []):
        def_map[fd.id] = fd

    # 按设备类型分层，每类一个 Layer
    layer_by_def: Dict[str, ET.Element] = {}

    for fi in getattr(project, "fixtures", []):
        fd = def_map.get(getattr(fi, "def_id", ""))
        type_name = fd.name if fd else "Unknown"
        if type_name not in layer_by_def:
            layer = ET.SubElement(layers, "Layer")
            layer.set("name", type_name)
            layer.set("uuid", _make_uuid())
            ET.SubElement(layer, "ChildList")
            layer_by_def[type_name] = layer
        child_list = layer_by_def[type_name].find("ChildList")

        # 坐标：画布 cm → mm
        px = float(getattr(fi, "pos_x", 0.0) or 0.0) * CANVAS_CM_TO_MM
        py = float(getattr(fi, "pos_y", 0.0) or 0.0) * CANVAS_CM_TO_MM
        pz = float(getattr(fi, "pos_z", 0.0) or 0.0) * CANVAS_CM_TO_MM   # 高度

        # Fixture 元素
        fix = ET.SubElement(child_list, "Fixture")
        fix.set("name", getattr(fi, "name", type_name))
        fix.set("uuid", _make_uuid())

        # Matrix（position）：Qascade pos_y→MVR Z(深度)，pos_z(高度)→MVR Y
        mat = ET.SubElement(fix, "Matrix")
        mat.text = _matrix3x4(px, pz, py)

        # GDTF reference（用设备类型名作为虚拟文件名）
        safe = type_name.replace(" ", "_").replace("/", "_")
        ET.SubElement(fix, "GDTFSpec").text = f"{safe}.gdtf"
        ET.SubElement(fix, "GDTFMode").text = "Mode 1"

        # DMX address
        uni = int(getattr(fi, "universe", 1) or 1)
        ch = int(getattr(fi, "start_channel", 1) or 1)
        addresses = ET.SubElement(fix, "Addresses")
        addr = ET.SubElement(addresses, "Address")
        addr.set("break", "0")
        addr.text = str(_dmx_absolute(uni, ch))

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode")


def export_mvr(project, path: str) -> Tuple[bool, str]:
    """将 project 导出为 .mvr 文件。

    返回 (success, message)。
    """
    if not getattr(project, "fixtures", None):
        return False, "工程中没有设备实例，无法导出 MVR。"
    try:
        gsd = build_gsd(project)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("GeneralSceneDescription.xml", gsd.encode("utf-8"))
        n = sum(1 for _ in getattr(project, "fixtures", []))
        return True, f"已导出 {n} 台设备到 {path}"
    except Exception as e:
        return False, f"MVR 导出失败：{e}"
