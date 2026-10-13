#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 引擎导出名混淆生成器（P2a 架构隐藏）。

问题：ce_engine_avx2/sse2.dll 以语义名导出 200 个函数
（ce_mix_clips/ce_full_mix/ce_artnet_pack_batch/...），dumpbin /exports
即得整张引擎 API 架构图。

方案：构建期用 /FI 强制包含 ce_export_renames.h（#define 语义名→不透明名），
对声明与全部内部引用做一致重命名；Python 侧由 qascade_ce/ce_api_map.py
把语义名翻译回不透明名再绑定。导出表只剩 200 个乱码名。

范围：CPU 双变体（ce_engine.c + ce_route.c + ce_pool.c，193+7 个导出）。
GPU 家族（~11 个 ce_gpu_* 导出）不在本期范围（backend/gpu_bake 直连、
量小、名称信息量低——见方案文档残余清单）。

工件为**入库静态物**（不随构建随机化）：avx2/sse2/opencl/stub 分属不同
构建脚本、不同机器，随机化会导致映射失配；不透明名本身已是随机值，
静态化无安全损失。想换一批名字：--regen 后重编全部 CPU DLL。

用法：
    venv\\Scripts\\python tools\\ce_export_rename.py           # 缺失才生成
    venv\\Scripts\\python tools\\ce_export_rename.py --regen   # 强制换新名
工件：
    render_lab/csrc/ce_export_renames.h    （C 侧 /FI 强制包含）
    render_lab/qascade_ce/ce_api_map.py    （Python 侧映射表）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSRC = ROOT / "render_lab" / "csrc"
RENAMES_H = CSRC / "ce_export_renames.h"
API_MAP_PY = ROOT / "render_lab" / "qascade_ce" / "ce_api_map.py"

# CPU 引擎导出声明的源文件（ce_route.c 被 ce_engine.c #include）
# ★ 2026-09-19: 文本合入的内核 `.c.inc`（被 ce_engine.c #include）此前漏扫 →
#   T13/T14 这类「整块内核写在 .inc 里」的导出以**语义名**落进 DLL,
#   破坏 P2a 混淆基线（实测 ce_mtx_pattern/ce_mtx_particle/ce_eff_write_* 漏网）。
#   本表即扫描真源：新增 .inc 内核必须同步登记。
EXPORT_SOURCES = ("ce_engine.c", "ce_route.c", "ce_pool.c",
                  "_ce_mtx_kernel.c.inc", "_ce_eff_kernel.c.inc",
                  "_ce_linker_kernel.c.inc", "ce_prepared_frame.inc",
                  "ce_output_domain_ownership.inc", "ce_output_domain_inferred.inc",
                  "ce_output_domain_compact.inc", "ce_output_domain_pump.inc",
                  "ce_output_domain_color.inc",
                  "ce_output_domain_xy.inc", "ce_audio_envelope.inc",
                  "ce_audio_chain.inc", "ce_audio_scope.inc", "ce_source_motion_curve.inc",
                  "ce_source_motion_direction.inc", "ce_source_motion_positions.inc", "ce_source_motion_raw.inc",
                  "ce_source_motion_clock.inc", "ce_render_timing.inc", "ce_source_motion_geometry_base.inc",
                  "ce_source_motion_geometry_fit.inc", "ce_source_motion_geometry_rank.inc",
                  "ce_source_motion_geometry_segments.inc", "ce_output_filter.inc", "ce_output_filter_plain.inc",
                  "ce_legacy_direction_prepare.inc", "ce_curve_lut.inc",
                  "ce_trajectory_api.inc", "ce_output_mask_count.inc",
                  "ce_curve_scalar_api.inc", "ce_programmer_row_api.inc",
                  "ce_programmer_library_api.inc", "ce_programmer_library_sort_api.inc", "ce_cold_clip_change_times.inc",
                  "ce_filter_prepare.inc", "ce_fade_target.inc",
                  "ce_audio_prepare_types.inc", "ce_audio_prepare_api.inc",
                  "ce_lt_prepare_types.inc", "ce_lt_prepare_api.inc")

_DECL_RE = re.compile(r"^\s*CE_API\s+(?:[\w\*]+\s+)*?\**(\w+)\s*\(", re.M)


def _export_names() -> list[str]:
    names: set[str] = set()
    for fname in EXPORT_SOURCES:
        text = (CSRC / fname).read_text(encoding="utf-8", errors="replace")
        for m in _DECL_RE.finditer(text):
            line = text[max(0, m.start()):m.end()]
            if "#define" in text[max(0, m.start() - 10):m.start()]:
                continue
            names.add(m.group(1))
    # 剔除宏名本身等误报（CE_API 定义行已被 #define 前缀剔除）
    names.discard("CE_API")
    return sorted(names)


def _gen() -> None:
    names = _export_names()
    if not names:
        raise SystemExit("未提取到任何 CE_API 导出名——正则与源码形态不匹配")
    import secrets
    opaque = {}
    for n in names:
        opaque[n] = "ce_" + secrets.token_hex(5)

    RENAMES_H.write_text(
        "/* 自动生成（tools/ce_export_rename.py）——语义导出名→不透明名。\n"
        " * 经 /FI 强制包含，对声明与内部引用一致重命名。\n"
        " * 与 qascade_ce/ce_api_map.py 同源；换名：--regen 后重编全部 CPU DLL。 */\n"
        "#ifndef CE_EXPORT_RENAMES_H\n"
        "#define CE_EXPORT_RENAMES_H\n\n"
        + "\n".join(f"#define {n} {o}" for n, o in opaque.items())
        + "\n\n#endif /* CE_EXPORT_RENAMES_H */\n",
        encoding="utf-8",
    )
    API_MAP_PY.write_text(
        '"""自动生成（tools/ce_export_rename.py）——语义名→DLL 实际导出名。\n\n'
        "DLL 导出已混淆为不透明名（csrc/ce_export_renames.h 同源）；\n"
        'ctypes_api._bind 先查本表再回退语义名（兼容未改名 DLL）。勿手改。"""\n'
        "CE_API_MAP = {\n"
        + "\n".join(f'    "{n}": "{o}",' for n, o in opaque.items())
        + "\n}\n",
        encoding="utf-8",
    )
    print(f"[rename] {len(names)} 个导出名已混淆（{RENAMES_H.name} + "
          f"{API_MAP_PY.name}）")


def _existing_mapping() -> dict[str, str]:
    import ast
    module = ast.parse(API_MAP_PY.read_text("utf-8"))
    assignment = next(node for node in module.body if isinstance(node, ast.Assign))
    mapping = ast.literal_eval(assignment.value)
    header = RENAMES_H.read_text("utf-8")
    pairs = dict(re.findall(r"^#define (ce_\w+) (ce_\w+)$", header, re.M))
    if mapping != pairs or len(set(mapping.values())) != len(mapping):
        raise SystemExit("现有 CE 映射与头文件不一致；保持原工件并停止")
    return mapping


def _append_missing() -> None:
    """Preserve every shipped opaque pair while registering only new exports."""
    import secrets
    mapping = _existing_mapping()
    missing = sorted(set(_export_names()) - mapping.keys())
    if not missing:
        print("[rename] 原静态映射完整，保留全部导出名")
        return
    occupied = set(mapping.values())
    additions = {}
    for name in missing:
        opaque = "ce_" + secrets.token_hex(5)
        while opaque in occupied:
            opaque = "ce_" + secrets.token_hex(5)
        occupied.add(opaque)
        additions[name] = opaque
    header = RENAMES_H.read_text("utf-8")
    closing = "#endif /* CE_EXPORT_RENAMES_H */"
    if header.count(closing) != 1:
        raise SystemExit("现有 CE header 结束位置不唯一；保持原工件并停止")
    new_defines = "\n".join(f"#define {n} {o}" for n, o in additions.items())
    source = API_MAP_PY.read_text("utf-8")
    end = source.rfind("}")
    if end < 0:
        raise SystemExit("现有 CE map 结束位置不存在；保持原工件并停止")
    new_pairs = "\n".join(f'    "{n}": "{o}",' for n, o in additions.items())
    RENAMES_H.write_text(header.replace(closing, new_defines + "\n\n" + closing), "utf-8")
    API_MAP_PY.write_text(source[:end] + new_pairs + "\n" + source[end:], "utf-8")
    print(f"[rename] 保留 {len(mapping)} 既有映射，只追加 {len(additions)} 新 API")


def main() -> None:
    regen = "--regen" in sys.argv
    if not regen and RENAMES_H.exists() and API_MAP_PY.exists():
        _append_missing()
        return
    _gen()


if __name__ == "__main__":
    main()
