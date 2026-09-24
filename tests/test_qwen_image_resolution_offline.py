# -*- coding: utf-8 -*-
# ruff: noqa: T201
"""Qwen 官方档分辨率选择器离线自测：官方表逐尺寸精确、半档恰为减半、默认=原生档、execute 还原。
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
m = importlib.import_module("yyp_nodes.qwen_image_resolution")

print("== 官方推荐表逐尺寸精确 ==")
OFFICIAL = {
    "1:1 (Square)": (2048, 2048),
    "4:3 (Standard)": (2400, 1792),
    "3:4 (Portrait Standard)": (1792, 2400),
    "3:2 (Photo)": (2528, 1696),
    "2:3 (Portrait Photo)": (1696, 2528),
    "16:9 (Widescreen)": (2752, 1536),
    "9:16 (Portrait Widescreen)": (1536, 2752),
}
assert m.NATIVE_SIZES == OFFICIAL, m.NATIVE_SIZES
assert set(m.RESOLUTION_GROUPS) == set(OFFICIAL)
for aspect_ratio, (width, height) in OFFICIAL.items():
    sizes = m.RESOLUTION_GROUPS[aspect_ratio]
    assert sizes == [(width // 2, height // 2), (width, height)], (aspect_ratio, sizes)
    for w, h in sizes:
        assert (w // 8) % 2 == 0 and (h // 8) % 2 == 0, (aspect_ratio, w, h)  # 潜空间 2× 要偶数网格
print("  七比例 x 原生/半两档，全部 16 对齐且潜空间偶数网格")

print("== 默认档 = 原生档，标签互指 ==")
assert m.GROUP_ORDER[0] == "16:9 (Widescreen)" and len(m.GROUP_ORDER) == 7
for aspect_ratio, options in m.PICKER_OPTIONS.items():
    native = m.NATIVE_SIZES[aspect_ratio]
    for label, size in options.items():
        assert f"{size[0]}x{size[1]} (" in label, label
        assert ("原生档" in label) == (size == native), label
    default = next(l for l, s in options.items() if s == native)
    assert options[default] == native
print("  默认全部落在官方原生档（16:9 -> 2752x1536）")

print("== schema 与 execute ==")
node = m.QwenImageResolutionPicker
schema = node.define_schema()
assert schema.node_id == "QwenImageResolutionPicker"
assert len(schema.outputs) == 2
got = node.execute({"aspect_ratio": "16:9 (Widescreen)", "resolution": "2752x1536 (4.03MP) 原生档"})
assert tuple(got.result) == (2752, 1536), got.result
got = node.execute({"aspect_ratio": "16:9 (Widescreen)", "resolution": "1376x768 (1.01MP) 半档"})
assert tuple(got.result) == (1376, 768), got.result
print("  execute 两档还原正确")

print("\nALL PASS")
