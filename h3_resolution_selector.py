import math

from comfy_api.latest import io

ASPECT_RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}

# MP that yields a 768 short edge (H3 native canvas), 21:9 capped at the 768x1344 area limit
NATIVE_MP = {
    "1:1 (Square)": 0.56,
    "2:3 (Portrait Photo)": 0.87,
    "3:2 (Photo)": 0.87,
    "3:4 (Portrait Standard)": 0.78,
    "4:3 (Standard)": 0.78,
    "9:16 (Portrait Widescreen)": 0.98,
    "16:9 (Widescreen)": 0.98,
    "21:9 (Ultrawide)": 0.98,
}

# Load tiers in absolute MP (1MP = 2^20 pixels); None = the per-aspect native canvas (768 short edge).
# 高质 0.85 / 均衡 0.7 / 轻量 0.55 / 预览 0.4, then 外推 960 (16:9 -> 1728x960), 外推 1088 (1920x1088), 外推 2K (2560x1440)
TIER_MP = [None, 0.85, 0.7, 0.55, 0.4, 1.58, 1.99, 3.52]

# H3 official canvas: 768 short edge capped at a 768x1344 area (same limits as comfy_extras/nodes_minimax_h3.py)
MAX_CANVAS_PIXELS = 768 * 1344
OVER_CANVAS = " ⚠超画布"


def _snap(w_ratio, h_ratio, mp):
    """Ideal MP target for an aspect ratio, snapped to the 32-aligned width/height H3 runs."""
    scale = math.sqrt(mp * 1024 * 1024 / (w_ratio * h_ratio))
    return round(w_ratio * scale / 32) * 32, round(h_ratio * scale / 32) * 32


def _megapixels(width, height):
    # 1MP = 2^20 pixels here: H3's native 1344x768 reads as the familiar 0.98MP
    return f"{round(width * height / 1048576, 2):g}MP"


def _over_canvas(width, height):
    """Mark tiers past the official canvas (768 short edge / 768x1344 area) - they are extrapolation, 二采 用."""
    return OVER_CANVAS if width * height > MAX_CANVAS_PIXELS else ""


def _label_by_resolution(width, height):
    return f"{width}x{height} ({_megapixels(width, height)}){_over_canvas(width, height)}"


def _label_by_megapixels(width, height):
    return f"{_megapixels(width, height)} ({width}x{height}){_over_canvas(width, height)}"


def _resolution_groups():
    """Every tier of every aspect ratio: {aspect_ratio: [(width, height), ...]}, small to large, duplicates dropped."""
    groups = {}
    for aspect_ratio, (w_ratio, h_ratio) in ASPECT_RATIOS.items():
        sizes = {_snap(w_ratio, h_ratio, NATIVE_MP[aspect_ratio] if mp is None else mp) for mp in TIER_MP}
        groups[aspect_ratio] = sorted(sizes, key=lambda size: size[0] * size[1])
    return groups


def _canvas_options(label):
    """{aspect_ratio: {label: (width, height)}} - one node's dropdown wording over the shared tier table."""
    return {aspect_ratio: {label(*size): size for size in sizes} for aspect_ratio, sizes in RESOLUTION_GROUPS.items()}


RESOLUTION_GROUPS = _resolution_groups()
NATIVE_SIZES = {aspect_ratio: _snap(w_ratio, h_ratio, NATIVE_MP[aspect_ratio])
                for aspect_ratio, (w_ratio, h_ratio) in ASPECT_RATIOS.items()}
PICKER_OPTIONS = _canvas_options(_label_by_resolution)
SELECTOR_OPTIONS = _canvas_options(_label_by_megapixels)

# 16:9 first (also the group a fresh node starts on), the rest by native canvas pixel count
DEFAULT_ASPECT_RATIO = "16:9 (Widescreen)"
GROUP_ORDER = [DEFAULT_ASPECT_RATIO] + sorted(
    (aspect_ratio for aspect_ratio in ASPECT_RATIOS if aspect_ratio != DEFAULT_ASPECT_RATIO),
    key=lambda aspect_ratio: NATIVE_SIZES[aspect_ratio][0] * NATIVE_SIZES[aspect_ratio][1])


def _aspect_ratio_input(options):
    """Both nodes pick the same way: the aspect ratio group, then a tier inside it (default = that ratio's native canvas)."""
    return io.DynamicCombo.Input(
        "aspect_ratio",
        tooltip="Aspect ratio group; it decides which sizes the resolution input offers.",
        options=[
            io.DynamicCombo.Option(aspect_ratio, [
                io.Combo.Input("resolution", options=list(options[aspect_ratio]),
                               tooltip="带 ⚠ 的档位超出 H3 官方画布（短边 768、面积上限 768x1344 ≈ 1.03MP、32 对齐），属于外推；一采用满血或 0.85 档，这些留给二采。",
                               default=next(label for label, size in options[aspect_ratio].items()
                                            if size == NATIVE_SIZES[aspect_ratio])),
            ])
            for aspect_ratio in GROUP_ORDER
        ])


class H3ResolutionPicker(io.ComfyNode):
    """Pick the H3 canvas by size: every option is "<width>x<height> (<MP>)"."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ResolutionPicker",
            display_name="Resolution Picker (H3 尺寸直选版)",
            category="utilities",
            description="Pick the aspect ratio, then the exact canvas in that ratio; each option shows its approximate megapixels.",
            inputs=[_aspect_ratio_input(PICKER_OPTIONS)],
            outputs=[io.Int.Output("width"), io.Int.Output("height")],
        )

    @classmethod
    def execute(cls, aspect_ratio: dict) -> io.NodeOutput:
        return io.NodeOutput(*PICKER_OPTIONS[aspect_ratio["aspect_ratio"]][aspect_ratio["resolution"]])


class H3ResolutionSelector(io.ComfyNode):
    """Same tiers as H3ResolutionPicker, but picked by load: every option is "<MP> (<width>x<height>)"."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ResolutionSelector",
            display_name="Resolution Selector (H3 精度版)",
            category="utilities",
            description="Pick the aspect ratio, then the tier by megapixels; each option shows the canvas it lands on.",
            inputs=[_aspect_ratio_input(SELECTOR_OPTIONS)],
            outputs=[io.Int.Output("width"), io.Int.Output("height")],
        )

    @classmethod
    def execute(cls, aspect_ratio: dict) -> io.NodeOutput:
        return io.NodeOutput(*SELECTOR_OPTIONS[aspect_ratio["aspect_ratio"]][aspect_ratio["resolution"]])


NODE_CLASS_MAPPINGS = {
    "H3ResolutionPicker": H3ResolutionPicker,
    "H3ResolutionSelector": H3ResolutionSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ResolutionPicker": "Resolution Picker (H3 尺寸直选版)",
    "H3ResolutionSelector": "Resolution Selector (H3 精度版)",
}
