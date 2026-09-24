"""Qwen-Image 2.1 官方推荐分辨率直选。

官方 README「Supported Aspect Ratios」推荐档（native 2K = 面积 ≈2048²，各比例 ≈4.2MP 甜区）：
1:1=2048x2048、4:3=2400x1792、3:4=1792x2400、3:2=2528x1696、2:3=1696x2528、
16:9=2752x1536、9:16=1536x2752。每比例两档：原生档（官方推荐，默认）+ 半档（精确减半，
二段放大第一段起稿用——半档 ×2 恰回原生档）。
"""
from comfy_api.latest import io

NATIVE_SIZES = {
    "16:9 (Widescreen)": (2752, 1536),
    "9:16 (Portrait Widescreen)": (1536, 2752),
    "1:1 (Square)": (2048, 2048),
    "3:2 (Photo)": (2528, 1696),
    "2:3 (Portrait Photo)": (1696, 2528),
    "4:3 (Standard)": (2400, 1792),
    "3:4 (Portrait Standard)": (1792, 2400),
}


def _megapixels(width, height):
    return f"{round(width * height / 1048576, 2):g}MP"


def _label(size, is_native):
    width, height = size
    return f"{width}x{height} ({_megapixels(width, height)}) {'原生档' if is_native else '半档'}"


RESOLUTION_GROUPS = {
    aspect_ratio: sorted({(width // 2, height // 2), (width, height)},
                         key=lambda size: size[0] * size[1])
    for aspect_ratio, (width, height) in NATIVE_SIZES.items()
}

PICKER_OPTIONS = {
    aspect_ratio: {_label(size, size == NATIVE_SIZES[aspect_ratio]): size for size in sizes}
    for aspect_ratio, sizes in RESOLUTION_GROUPS.items()
}

GROUP_ORDER = list(NATIVE_SIZES)  # 16:9 置顶（默认组），其余按官方表序


def _aspect_ratio_input():
    return io.DynamicCombo.Input(
        "aspect_ratio",
        tooltip="宽高比（Qwen-Image 2.1 官方推荐表七档）；决定 resolution 下拉的档位。",
        options=[
            io.DynamicCombo.Option(aspect_ratio, [
                io.Combo.Input(
                    "resolution", options=list(PICKER_OPTIONS[aspect_ratio]),
                    tooltip="原生档 = 官方推荐尺寸（native 2K 甜区）；半档 = 精确减半（起稿/二段放大第一段用，×2 恰回原生档）。",
                    default=next(label for label, size in PICKER_OPTIONS[aspect_ratio].items()
                                 if size == NATIVE_SIZES[aspect_ratio])),
            ])
            for aspect_ratio in GROUP_ORDER
        ])


class QwenImageResolutionPicker(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="QwenImageResolutionPicker",
            display_name="Resolution Picker (Qwen Image 官方档)",
            category="utilities",
            description="Qwen-Image 2.1 官方推荐分辨率直选（README Supported Aspect Ratios 原表）：先选宽高比，再选原生档/半档，输出 width/height。",
            inputs=[_aspect_ratio_input()],
            outputs=[io.Int.Output("width"), io.Int.Output("height")],
        )

    @classmethod
    def execute(cls, aspect_ratio: dict) -> io.NodeOutput:
        return io.NodeOutput(*PICKER_OPTIONS[aspect_ratio["aspect_ratio"]][aspect_ratio["resolution"]])


NODE_CLASS_MAPPINGS = {"QwenImageResolutionPicker": QwenImageResolutionPicker}
NODE_DISPLAY_NAME_MAPPINGS = {"QwenImageResolutionPicker": "Resolution Picker (Qwen Image 官方档)"}
