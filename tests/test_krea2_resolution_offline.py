# -*- coding: utf-8 -*-
# ruff: noqa: T201
"""Krea 2 官方档分辨率选择器离线自测：官方输出表逐尺寸精确、2K档恰为 2× 线性、默认 1K档、execute 还原。
脚本式（包内惯例）：导入即跑断言。
"""
import importlib
import os
import sys
import types

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(os.path.dirname(PACK_DIR))
sys.path.insert(0, REPO_DIR)
pkg = types.ModuleType("yyp_nodes")
pkg.__path__ = [PACK_DIR]
sys.modules.setdefault("yyp_nodes", pkg)
m = importlib.import_module("yyp_nodes.krea2_resolution")

print("== 官方输出尺寸表逐尺寸精确 ==")
OFFICIAL_1K = {
    "1:1 (Square)": (1024, 1024),
    "4:3 (Standard)": (1184, 896),
    "3:2 (Photo)": (1248, 832),
    "16:9 (Widescreen)": (1376, 768),
    "2.35:1 (Cinemascope)": (1568, 672),
    "4:5 (Portrait)": (928, 1152),
    "2:3 (Portrait Photo)": (832, 1248),
    "9:16 (Portrait Widescreen)": (768, 1376),
}
assert m.NATIVE_SIZES == OFFICIAL_1K, m.NATIVE_SIZES
for aspect_ratio, native in OFFICIAL_1K.items():
    options = m.PICKER_OPTIONS[aspect_ratio]
    by_tier = {}
    for label, size in options.items():
        tier = label.split(" ")[-1]
        by_tier[tier] = size
        assert f"{size[0]}x{size[1]} (" in label, label
        assert size[0] % 16 == 0 and size[1] % 16 == 0, (aspect_ratio, size)  # 潜空间偶数网格
    assert by_tier["1K档"] == native, (aspect_ratio, by_tier)
    assert by_tier["2K档"] == (native[0] * 2, native[1] * 2), (aspect_ratio, by_tier)
print("  八比例 x 1K/2K 两档，2K档恰为 2× 线性，全部 16 对齐")

print("== 默认档 = 1K档（训练命中） ==")
assert m.GROUP_ORDER[0] == "16:9 (Widescreen)" and len(m.GROUP_ORDER) == 8
for aspect_ratio, options in m.PICKER_OPTIONS.items():
    default = f"{m.NATIVE_SIZES[aspect_ratio][0]}x{m.NATIVE_SIZES[aspect_ratio][1]} " \
              f"({m._megapixels(*m.NATIVE_SIZES[aspect_ratio])}) 1K档"
    assert options[default] == m.NATIVE_SIZES[aspect_ratio], (aspect_ratio, default)
print("  默认全部落在官方 1K 训练命中档（16:9 -> 1376x768）")

print("== schema 与 execute ==")
node = m.Krea2ResolutionPicker
schema = node.define_schema()
assert schema.node_id == "Krea2ResolutionPicker" and len(schema.outputs) == 2
got = node.execute({"aspect_ratio": "16:9 (Widescreen)", "resolution": "1376x768 (1.01MP) 1K档"})
assert tuple(got.result) == (1376, 768), got.result
got = node.execute({"aspect_ratio": "16:9 (Widescreen)", "resolution": "2752x1536 (4.03MP) 2K档"})
assert tuple(got.result) == (2752, 1536), got.result
print("  execute 两档还原正确")

print("\nALL PASS")
