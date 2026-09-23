# -*- coding: utf-8 -*-
"""h3_resolution_selector 离线自测：只需要 ComfyUI 仓库根目录（comfy_api + torch），不起服务。

用法（仓库根目录）：
    python custom_nodes/comfyui-yyp-nodes/tests/test_h3_resolution_selector_offline.py

覆盖：两个节点共用的档位表（比例分组、组内按像素量升序、32 对齐、去重）、
schema 与表格一致、默认档=满血档、两种标签互为倒装、prompt 键名契约、
execute 覆盖全部档位、RESOLUTION_TABLE.md 满血档对得上。
"""
import importlib
import os
import re
import sys
import types

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(os.path.dirname(PACK_DIR))
sys.path.insert(0, REPO_DIR)

from comfy_api.latest import _io  # noqa: E402

pkg = types.ModuleType("yyp_nodes")
pkg.__path__ = [PACK_DIR]
sys.modules["yyp_nodes"] = pkg
m = importlib.import_module("yyp_nodes.h3_resolution_selector")

NODES = (("H3ResolutionPicker", m.H3ResolutionPicker, m.PICKER_OPTIONS),
         ("H3ResolutionSelector", m.H3ResolutionSelector, m.SELECTOR_OPTIONS))

print("== 档位表 ==")
assert list(m.RESOLUTION_GROUPS) == list(m.ASPECT_RATIOS), "每个比例都要有一组"
assert len(m.RESOLUTION_GROUPS) == len(m.NATIVE_SIZES) == len(m.ASPECT_RATIOS)
for aspect_ratio, sizes in m.RESOLUTION_GROUPS.items():
    pixels = [w * h for w, h in sizes]
    assert pixels == sorted(pixels), (aspect_ratio, "组内必须按像素量从小到大")
    assert len(sizes) == len(set(sizes)), (aspect_ratio, "重复尺寸必须去掉")
    assert len(sizes) in (len(m.TIER_MP) - 1, len(m.TIER_MP)), (aspect_ratio, len(sizes))
    for width, height in sizes:
        assert width % 32 == 0 and height % 32 == 0 and width > 0 and height > 0, (aspect_ratio, width, height)
print("  %d 个比例组，组内 %d~%d 档，全部 32 对齐且按像素量升序"
      % (len(m.RESOLUTION_GROUPS), min(len(s) for s in m.RESOLUTION_GROUPS.values()),
         max(len(s) for s in m.RESOLUTION_GROUPS.values())))

print("== 两个节点 schema ==")
assert m.GROUP_ORDER[0] == m.DEFAULT_ASPECT_RATIO == "16:9 (Widescreen)"
assert sorted(m.GROUP_ORDER) == sorted(m.ASPECT_RATIOS), "组顺序不能漏比例"
for node_id, node, options in NODES:
    schema = node.define_schema()
    assert schema.node_id == node_id and schema.display_name == m.NODE_DISPLAY_NAME_MAPPINGS[node_id]
    assert [o.display_name for o in schema.outputs] == ["width", "height"]
    (outer,) = [i for i in schema.inputs if i.id == "aspect_ratio"]
    assert [option.key for option in outer.options] == m.GROUP_ORDER, (node_id, "组顺序与 GROUP_ORDER 不一致")
    for option in outer.options:
        (inner,) = option.inputs
        assert inner.id == "resolution"
        assert inner.options == list(options[option.key]), (node_id, option.key)
        assert options[option.key][inner.default] == m.NATIVE_SIZES[option.key], (node_id, option.key, inner.default)
print("  %d 组 x 2 节点，每组默认满血档（如 %s）"
      % (len(m.GROUP_ORDER), m.PICKER_OPTIONS[m.DEFAULT_ASPECT_RATIO][next(
          l for l, s in m.PICKER_OPTIONS[m.DEFAULT_ASPECT_RATIO].items() if s == m.NATIVE_SIZES[m.DEFAULT_ASPECT_RATIO])]))

print("== 两种标签互为倒装 ==")
for aspect_ratio, sizes in m.RESOLUTION_GROUPS.items():
    picker = m.PICKER_OPTIONS[aspect_ratio]
    selector = m.SELECTOR_OPTIONS[aspect_ratio]
    assert list(picker.values()) == list(selector.values()) == sizes, aspect_ratio
    for (p_label, p_size), (s_label, s_size) in zip(picker.items(), selector.items()):
        assert p_size == s_size
        assert re.fullmatch(r"\d+x\d+ \([\d.]+MP\)( ⚠超画布)?", p_label), p_label
        assert re.fullmatch(r"[\d.]+MP \(\d+x\d+\)( ⚠超画布)?", s_label), s_label
        assert p_label.startswith(f"{p_size[0]}x{p_size[1]} "), p_label
        assert f"({p_size[0]}x{p_size[1]})" in s_label, s_label
        assert p_label.split("(")[1].split(")")[0] == s_label.split(" ")[0], (p_label, s_label)
print("  同尺寸同 MP 文本，Picker 是「尺寸 (MP)」、Selector 是「MP (尺寸)」，超画布的带 ⚠")

print("== 超画布标注 ==")
# 官方画布：短边 768 + 面积上限 768x1344（= comfy_extras/nodes_minimax_h3.py 的 MAX_PIXELS）
for node_id, node, options in NODES:
    marked = [l for group in options.values() for l, size in group.items() if l.endswith(m.OVER_CANVAS)]
    assert marked, node_id
    for aspect_ratio, group in options.items():
        for label, size in group.items():
            over = size[0] * size[1] > m.MAX_CANVAS_PIXELS
            assert label.endswith(m.OVER_CANVAS) == over, (node_id, aspect_ratio, label, over)
    (outer,) = [i for i in node.define_schema().inputs if i.id == "aspect_ratio"]
    for option in outer.options:
        assert not option.inputs[0].default.endswith(m.OVER_CANVAS), (node_id, option.key, "默认档不能是外推档")
print("  规定内档位无标注，外推档（1.58 / 1.99 / 3.52MP）带 ⚠，默认档全部在画布内")

print("== prompt 键名契约 ==")
# 前端提交的键是 \"aspect_ratio.resolution\"（嵌套路径），后端按它展开成 execute 的 dict
for node_id, node, options in NODES:
    live = {"aspect_ratio": "16:9 (Widescreen)", "aspect_ratio.resolution": "x"}
    expanded, _, v3_data = _io.get_finalized_class_inputs(node.INPUT_TYPES(), live)
    assert "aspect_ratio.resolution" in expanded["required"], (node_id, list(expanded["required"]))
    assert v3_data["dynamic_paths"]["aspect_ratio.resolution"] == "aspect_ratio.resolution"
print("  嵌套键与 dynamic_paths 与后端 get_finalized_class_inputs 一致")

print("== execute ==")
count = 0
for node_id, node, options in NODES:
    for aspect_ratio, group in options.items():
        for label, size in group.items():
            got = node.execute({"aspect_ratio": aspect_ratio, "resolution": label})
            assert tuple(got.result) == size, (node_id, aspect_ratio, label, got.result)
            count += 1
print("  两个节点 x 全部 %d 项输入都能还原成 (width, height)" % (count // 2))

print("== 满血档（RESOLUTION_TABLE.md）==")
native = {"16:9 (Widescreen)": (1344, 768), "9:16 (Portrait Widescreen)": (768, 1344),
          "3:2 (Photo)": (1184, 768), "2:3 (Portrait Photo)": (768, 1184),
          "4:3 (Standard)": (1056, 768), "3:4 (Portrait Standard)": (768, 1056),
          "1:1 (Square)": (768, 768), "21:9 (Ultrawide)": (1536, 672)}
for aspect_ratio, size in native.items():
    assert m.NATIVE_SIZES[aspect_ratio] == size, (aspect_ratio, size)
    assert size in m.RESOLUTION_GROUPS[aspect_ratio], (aspect_ratio, size)
print("  8 个比例的原生短边 768 档与速查表一致")

print("== 标签取整 ==")
assert m._label_by_resolution(1344, 768) == "1344x768 (0.98MP)"
assert m._label_by_megapixels(1344, 768) == "0.98MP (1344x768)"
assert m._label_by_megapixels(1152, 640) == "0.7MP (1152x640)"
assert m._label_by_megapixels(864, 480) == "0.4MP (864x480)"
assert m._label_by_megapixels(2560, 1440) == "3.52MP (2560x1440) ⚠超画布"
assert m._label_by_resolution(1728, 960) == "1728x960 (1.58MP) ⚠超画布"
assert m._over_canvas(1536, 672) == "", "21:9 满血档正好压在面积上限上，不该标"
print("  OK")

print("\nALL PASS")
