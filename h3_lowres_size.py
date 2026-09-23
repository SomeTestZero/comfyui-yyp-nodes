"""H3 低清尺寸换算：按 selflift 的 lowres_scale 公式从最终分辨率推导低清分辨率。

两阶段方案（低清底片 → LBH 提升 → 精修）的参数化节点：最终分辨率由
Resolution Picker 单一来源给定，本节点按 selflift 内部同一公式换算低清
latent 网格（round-to-even），避免手填低清宽高导致与 selflift 口径漂移。

selflift 公式（nodes.py，H/W 为 latent 网格行/列）:
    h = max(2, round(H * lowres_scale / 2) * 2)
    w = max(2, round(W * lowres_scale / 2) * 2)
"""


class H3LowResSize:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "width": ("INT", {"forceInput": True, "tooltip": "最终分辨率宽（接 Resolution Picker）"}),
            "height": ("INT", {"forceInput": True, "tooltip": "最终分辨率高（接 Resolution Picker）"}),
            "lowres_scale": ("FLOAT", {"default": 0.6, "min": 0.25, "max": 1.0, "step": 0.05,
                                       "tooltip": "低清线性缩放系数（selflift 口径，论文值 0.5）"}),
        }}

    RETURN_TYPES = ("INT", "INT")
    RETURN_NAMES = ("low_width", "low_height")
    FUNCTION = "convert"
    CATEGORY = "utilities"
    DESCRIPTION = ("按 selflift 的 lowres_scale 公式把最终分辨率换算成低清分辨率"
                   "（latent 网格 round-to-even，×16 还原像素）。")

    def convert(self, width, height, lowres_scale):
        if width % 16 or height % 16:
            raise ValueError(
                "H3LowResSize: 宽高必须是 16 的倍数（H3 latent 网格要求），收到 %dx%d"
                % (width, height))
        H, W = height // 16, width // 16
        h = max(2, round(H * lowres_scale / 2) * 2)
        w = max(2, round(W * lowres_scale / 2) * 2)
        return (w * 16, h * 16)


NODE_CLASS_MAPPINGS = {"H3LowResSize": H3LowResSize}
NODE_DISPLAY_NAME_MAPPINGS = {"H3LowResSize": "H3 LowRes Size (selflift 口径低清换算)"}
