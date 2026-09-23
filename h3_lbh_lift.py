"""H3 latent lift: standalone wrapper around the SelfLift LBH learned upscaler.

两阶段方案（方案A/B）的桥接节点：把方案A 低清底片的干净 AV latent（σ=0，
含钉扎头）提升到目标全分辨率 latent 网格，供方案B 的 SIGMAS 切片精修使用。
视频流走 LBH 学习提升（scale embedding 支持任意/非均匀目标尺寸），音频流
原样透传（音频与视频分辨率无关）。

运行时懒加载 selflift-Avatar 包的 h3_upscaler 模块（优先复用 ComfyUI 已加
载的实例，保持与 fork 实现同步）；未加载时按文件路径加载并 stub 掉其相对
导入的 diagnostics。ComfyUI 本体不被修改。
"""

import importlib.util
import os
import sys
import types

import comfy.model_management
import comfy.nested_tensor
import folder_paths
from nodes import MAX_RESOLUTION

_FOLDER = "latent_upscale_models"
_DEFAULT_UPSCALER = "minimax_h3_latent_upscaler_3d_bf16.safetensors"
_CACHE = {}


def _streams(samples):
    if isinstance(samples, list):
        # MC LoadLatent 的 AV 多流格式（[video, audio]，CPU）：视频流在首位，
        # 输出按 NestedTensor 重打包为模型原生采样格式
        return samples, True
    if samples.is_nested:
        return list(samples.unbind()), True
    return [samples], False


def _pack(streams, nested):
    if nested:
        return comfy.nested_tensor.NestedTensor(streams)
    return streams[0]


def _load_h3_upscaler():
    """优先取 ComfyUI 已加载的 selflift-Avatar.h3_upscaler；否则按路径加载。"""
    if "mod" in _CACHE:
        return _CACHE["mod"]
    for name, mod in list(sys.modules.items()):
        if "selflift" not in name.lower():
            continue
        up = getattr(mod, "h3_upscaler", None)
        if up is not None:
            _CACHE["mod"] = up
            return up
    for name, mod in list(sys.modules.items()):
        if name.lower().endswith(".h3_upscaler") and "selflift" in name.lower():
            _CACHE["mod"] = mod
            return mod
    base = None
    for base_dir in folder_paths.get_folder_paths("custom_nodes"):
        cand = os.path.join(base_dir, "selflift-Avatar")
        if os.path.isfile(os.path.join(cand, "h3_upscaler.py")):
            base = cand
            break
    if base is None:
        raise ImportError(
            "h3_lbh_lift: 未找到 selflift-Avatar 包（h3_upscaler.py），"
            "两阶段提升节点依赖其 LBH upscaler 实现")
    pkg_name = "yyp_selflift_bridge"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [base]
        sys.modules[pkg_name] = pkg
        diag = types.ModuleType(pkg_name + ".diagnostics")
        diag.log_memory = lambda *a, **k: None
        sys.modules[pkg_name + ".diagnostics"] = diag
        spec = importlib.util.spec_from_file_location(
            pkg_name + ".h3_upscaler", os.path.join(base, "h3_upscaler.py"))
        up = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = up
        spec.loader.exec_module(up)
    _CACHE["mod"] = sys.modules[pkg_name + ".h3_upscaler"]
    return _CACHE["mod"]


def _upscaler_options():
    opts = []
    try:
        for base in folder_paths.get_folder_paths(_FOLDER):
            if os.path.isdir(base):
                for f in sorted(os.listdir(base)):
                    if f.endswith(".safetensors"):
                        opts.append(f)
    except Exception:
        pass
    if _DEFAULT_UPSCALER not in opts:
        opts.insert(0, _DEFAULT_UPSCALER)
    return opts


class H3LatentLift:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL", {"tooltip": "任意 H3 model patcher（接精修链同一模型），仅用于取 latent_format 做模型空间↔VAE 空间换算"}),
            "latent": ("LATENT", {"tooltip": "方案A 低清底片的 AV latent（σ=0，含钉扎头；由 Motion Context Save Latent 存出）"}),
            "upscaler_model": (_upscaler_options(), {"tooltip": "LBH 学习提升模型（models/latent_upscale_models/）"}),
            "target_width": ("INT", {"default": 1344, "min": 64, "max": MAX_RESOLUTION, "step": 16,
                                     "tooltip": "目标全分辨率像素宽（latent 网格 = 宽/16）"}),
            "target_height": ("INT", {"default": 768, "min": 64, "max": MAX_RESOLUTION, "step": 16,
                                      "tooltip": "目标全分辨率像素高（latent 网格 = 高/16）"}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "lift"
    CATEGORY = "utilities"
    DESCRIPTION = ("把低清干净 AV latent 提升到目标 latent 网格（视频流走 LBH 学习提升，"
                   "音频流原样透传）。配合 SIGMAS 切片 + SamplerCustomAdvanced 做"
                   "两阶段方案的逐段精修。")

    def lift(self, model, latent, upscaler_model, target_width, target_height):
        up = _load_h3_upscaler()
        lf = model.get_model_object("latent_format")
        samples = latent["samples"]
        streams, nested = _streams(samples)
        video = streams[0]
        z_vae = lf.process_out(video.float())
        out_hw = (target_height // 16, target_width // 16)
        z = up.learned_latent_lift(z_vae, out_hw, upscaler_model, force_unload=True)
        z_model = lf.process_in(z)
        out_streams = [z_model.to(device=video.device, dtype=video.dtype)] + list(streams[1:])
        out = latent.copy()
        out["samples"] = _pack(out_streams, nested)
        return (out,)


NODE_CLASS_MAPPINGS = {"H3LatentLift": H3LatentLift}
NODE_DISPLAY_NAME_MAPPINGS = {"H3LatentLift": "H3 Latent Lift (LBH 两阶段提升)"}
