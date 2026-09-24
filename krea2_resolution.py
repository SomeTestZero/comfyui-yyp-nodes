"""Krea 2 官方训练命中分辨率直选。

官方口径三层：① 预训练课程 256→512→1024（krea-ai 技术报告）——训练命中 = 1K 档；
② 官方输出尺寸表（krea.ai 文档 Medium/Large/Turbo 一致）：1:1=1024²、4:3=1184x896、
3:2=1248x832、16:9=1376x768、2.35:1=1568x672、4:5=928x1152、2:3=832x1248、9:16=768x1376；
③ OSS README「can generate from 1k ~ 2k」+ Turbo 官方示例 2048²——2K 档 = 各尺寸 2× 线性。

档位两档：1K档（训练命中/官方输出表，默认）、2K档（官方可用上限）。
"""
from comfy_api.latest import io

NATIVE_SIZES = {
    "16:9 (Widescreen)": (1376, 768),
    "9:16 (Portrait Widescreen)": (768, 1376),
    "1:1 (Square)": (1024, 1024),
    "4:3 (Standard)": (1184, 896),
    "3:2 (Photo)": (1248, 832),
    "2.35:1 (Cinemascope)": (1568, 672),
    "4:5 (Portrait)": (928, 1152),
    "2:3 (Portrait Photo)": (832, 1248),
}

TIERS = {"1K档": 1, "2K档": 2}


def _megapixels(width, height):
    return f"{round(width * height / 1048576, 2):g}MP"


def _label(size, tier):
    width, height = size
    return f"{width}x{height} ({_megapixels(width, height)}) {tier}"


def _sizes(native):
    return {tier: (native[0] * scale, native[1] * scale) for tier, scale in TIERS.items()}


PICKER_OPTIONS = {
    aspect_ratio: {_label(size, tier): size for tier, size in _sizes(native).items()}
    for aspect_ratio, native in NATIVE_SIZES.items()
}

GROUP_ORDER = list(NATIVE_SIZES)  # 16:9 置顶（默认组），其余按官方表序


def _aspect_ratio_input():
    return io.DynamicCombo.Input(
        "aspect_ratio",
        tooltip="宽高比（Krea 2 官方输出尺寸表八档）；决定 resolution 下拉的档位。",
        options=[
            io.DynamicCombo.Option(aspect_ratio, [
                io.Combo.Input(
                    "resolution", options=list(PICKER_OPTIONS[aspect_ratio]),
                    tooltip="1K档 = 官方输出表尺寸（预训练课程命中 256→512→1024）；2K档 = 官方可用上限（OSS README 1k~2k、Turbo 示例 2048²），= 1K档 ×2 线性。",
                    default=_label(NATIVE_SIZES[aspect_ratio], "1K档")),
            ])
            for aspect_ratio in GROUP_ORDER
        ])


class Krea2ResolutionPicker(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Krea2ResolutionPicker",
            display_name="Resolution Picker (Krea 2 官方档)",
            category="utilities",
            description="Krea 2 官方训练命中分辨率直选（官方输出尺寸表八档）：先选宽高比，再选 1K档（训练命中）/2K档（官方上限），输出 width/height。",
            inputs=[_aspect_ratio_input()],
            outputs=[io.Int.Output("width"), io.Int.Output("height")],
        )

    @classmethod
    def execute(cls, aspect_ratio: dict) -> io.NodeOutput:
        return io.NodeOutput(*PICKER_OPTIONS[aspect_ratio["aspect_ratio"]][aspect_ratio["resolution"]])


NODE_CLASS_MAPPINGS = {"Krea2ResolutionPicker": Krea2ResolutionPicker}
NODE_DISPLAY_NAME_MAPPINGS = {"Krea2ResolutionPicker": "Resolution Picker (Krea 2 官方档)"}
