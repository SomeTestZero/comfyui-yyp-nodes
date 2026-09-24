"""Krea 2 / Wan2.1 2× 放大解码桥接。

spacepxl/Wan2.1-VAE-upscale2x = 解码器微调（12 通道头 → pixel_shuffle(2) → 2× 图），
Krea 2 / Qwen-Image / Wan2.1 共用同一潜空间（latents_mean/std 相同），核心 ComfyUI
LATENT 采样结果可直接喂：去归一化 → 解码 → 2×。免重采样，Turbo 蒸馏档的正确放大姿势
（蒸馏模型抗部分重降噪，二采精修反而掉质）。

实现按 Eric_Krea2 的 _upscale_vae.decode_latents_with_upscale_vae 同构独立重写
（不跨包 import）；归一化常数取自 spacepxl 配置（Wan2.1 家族通用）。
"""
import torch
import torch.nn.functional as F

LATENTS_MEAN = [-0.7571, -0.7089, -0.9113, 0.1075, -0.1745, 0.9653, -0.1517, 1.5508,
                0.4134, -0.0715, 0.5517, -0.3632, -0.1922, -0.9497, 0.2503, -0.2921]
LATENTS_STD = [2.8184, 1.4541, 2.3275, 2.6558, 1.2196, 1.7708, 2.6052, 2.0743,
               3.2687, 2.1526, 2.8652, 1.5579, 1.6382, 1.1253, 2.8251, 1.9160]

_VAE_CACHE = {}


def _load_upscale_vae(model_path, subfolder, dtype):
    key = (model_path, subfolder, dtype)
    if key not in _VAE_CACHE:
        from diffusers import AutoencoderKLWan
        dt = {"bfloat16": torch.bfloat16, "float16": torch.float16,
              "float32": torch.float32}[dtype]
        kwargs = {"torch_dtype": dt}
        if subfolder.strip():
            kwargs["subfolder"] = subfolder.strip()
        vae = AutoencoderKLWan.from_pretrained(model_path, **kwargs).eval()
        _VAE_CACHE[key] = vae
    return _VAE_CACHE[key]


def _patch_cosine_blend(vae):
    """瓦片解码的线性拼合会留斜率断点（平滑渐变上的网格线），换 C1 连续的余弦拼合。"""
    if getattr(vae, "_cosine_blend_patched", False):
        return
    import math

    def _blend_v(self, a, b, blend_extent):
        blend_extent = min(a.shape[-2], b.shape[-2], blend_extent)
        for y in range(blend_extent):
            alpha = (1.0 - math.cos(math.pi * y / blend_extent)) / 2.0
            b[..., y, :] = a[..., -blend_extent + y, :] * (1 - alpha) + b[..., y, :] * alpha
        return b

    def _blend_h(self, a, b, blend_extent):
        blend_extent = min(a.shape[-1], b.shape[-1], blend_extent)
        for x in range(blend_extent):
            alpha = (1.0 - math.cos(math.pi * x / blend_extent)) / 2.0
            b[..., x] = a[..., -blend_extent + x, :] * (1 - alpha) + b[..., x, :] * alpha
        return b

    vae.blend_v = _blend_v.__get__(vae)
    vae.blend_h = _blend_h.__get__(vae)
    vae._cosine_blend_patched = True


def decode_2x(samples, vae):
    """归一化 z 潜空间 [B,16,h,w] → 2× 图 [B,2h·8,2w·8,3]（float 0~1）。"""
    device = next(vae.parameters()).device
    dtype = next(vae.parameters()).dtype
    b, c, h_lat, w_lat = samples.shape
    if h_lat > 128 or w_lat > 128:
        try:
            vae.enable_tiling()
        except Exception:
            pass
        _patch_cosine_blend(vae)
    else:
        vae.use_tiling = False

    mean = torch.tensor(LATENTS_MEAN).view(1, c, 1, 1).to(device=device, dtype=dtype)
    std = torch.tensor(LATENTS_STD).view(1, c, 1, 1).to(device=device, dtype=dtype)
    spatial = samples.unsqueeze(2).to(device=device, dtype=dtype) * std.unsqueeze(2) + mean.unsqueeze(2)
    with torch.no_grad():
        decoded = vae.decode(spatial, return_dict=False)[0]  # [B, 12, 1, H, W]
    image = F.pixel_shuffle(decoded.squeeze(2), 2)           # [B, 3, 2H, 2W]
    return ((image + 1.0) / 2.0).clamp(0, 1).permute(0, 2, 3, 1).float().cpu()


class Krea2Upscale2xDecode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT", {"forceInput": True,
                                  "tooltip": "核心采样器输出的 Krea2/Qwen-Image/Wan2.1 潜空间（16 通道归一化 z）"}),
            "model_path": ("STRING", {"default": "spacepxl/Wan2.1-VAE-upscale2x",
                                      "tooltip": "HF 模型 ID 或本地 diffusers 目录"}),
            "subfolder": ("STRING", {"default": "diffusers/Wan2.1_VAE_upscale2x_imageonly_real_v1",
                                     "tooltip": "含 config.json + 权重的子目录；已是目标目录则留空"}),
            "dtype": (["bfloat16", "float16", "float32"], {"default": "bfloat16"}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image_2x")
    FUNCTION = "decode"
    CATEGORY = "latent/upscaling"
    DESCRIPTION = ("Krea 2 专用 2× 放大解码（spacepxl Wan2.1-VAE-upscale2x）：潜空间直接解出 2× 图，"
                   "免重采样——Turbo 蒸馏档的正确放大姿势。输出尺寸 = 输入潜空间像素尺寸的 2 倍。")

    def decode(self, latent, model_path, subfolder, dtype):
        vae = _load_upscale_vae(model_path, subfolder, dtype)
        try:
            image = decode_2x(latent["samples"], vae)
        finally:
            vae.to("cpu")
            torch.cuda.empty_cache()
        print(f"[yyp] Krea2 2× 放大解码 -> {image.shape[1]}x{image.shape[2]}")
        return (image,)


NODE_CLASS_MAPPINGS = {"Krea2Upscale2xDecode": Krea2Upscale2xDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"Krea2Upscale2xDecode": "Krea 2 Upscale Decode (2x, from Comfy LATENT)"}
