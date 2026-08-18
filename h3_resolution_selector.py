import math

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

PRESETS = {
    "满血 native (短边768)": None,  # per-aspect NATIVE_MP
    "均衡 balanced (0.72MP)": 0.72,
    "轻量 light (0.5MP)": 0.5,
    "预览 preview (0.4MP)": 0.4,
    "自定义 custom": "custom",
}


class H3ResolutionSelector:
    """Core ResolutionSelector with H3 presets: native 768-short-edge per aspect ratio, 0.01 MP precision, multiple=32."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (list(ASPECT_RATIOS.keys()), {"default": "16:9 (Widescreen)"}),
                "preset": (list(PRESETS.keys()), {"default": "满血 native (短边768)"}),
                "megapixels": ("FLOAT", {"default": 0.98, "min": 0.1, "max": 16.0, "step": 0.01,
                                         "tooltip": "Only used when preset = 自定义 custom. 0.98 MP at 16:9 gives H3's native 1344x768."}),
                "multiple": ("INT", {"default": 32, "min": 8, "max": 128, "step": 4}),
            },
        }

    RETURN_TYPES = ("INT", "INT")
    RETURN_NAMES = ("width", "height")
    FUNCTION = "calc"
    CATEGORY = "utilities"

    def calc(self, aspect_ratio, preset, megapixels, multiple):
        mp = PRESETS[preset]
        if mp == "custom":
            mp = megapixels
        elif mp is None:
            mp = NATIVE_MP[aspect_ratio]
        w_ratio, h_ratio = ASPECT_RATIOS[aspect_ratio]
        scale = math.sqrt(mp * 1024 * 1024 / (w_ratio * h_ratio))
        width = round(w_ratio * scale / multiple) * multiple
        height = round(h_ratio * scale / multiple) * multiple
        return (width, height)


NODE_CLASS_MAPPINGS = {
    "H3ResolutionSelector": H3ResolutionSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ResolutionSelector": "Resolution Selector (H3 精度版)",
}
