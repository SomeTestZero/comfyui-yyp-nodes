# -*- coding: utf-8 -*-
# ruff: noqa: T201
"""Krea2 2× 放大解码桥接的离线测试：去归一化、12 通道头 pixel_shuffle(2)、尺寸簿记。
用鸭子类型 stub VAE 代替真实权重（真实加载走 hub，不进离线测试）。"""
import importlib
import os
import sys
import types

import torch

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("yyp_nodes")
pkg.__path__ = [PACK_DIR]
sys.modules.setdefault("yyp_nodes", pkg)
m = importlib.import_module("yyp_nodes.krea2_upscale2x")


class StubVAE:
    """decode 直出 [B,12,1,H,W]（模拟 12 通道头微调）；记录输入供去归一化断言。"""

    def __init__(self):
        self.seen = None
        self.use_tiling = False
        self._param = torch.zeros(1)

    def parameters(self):
        return iter([self._param])

    def enable_tiling(self):
        self.use_tiling = True

    def decode(self, spatial, return_dict=False):
        self.seen = spatial.detach().clone()
        b, _c, _one, h, w = spatial.shape
        return (torch.zeros(b, 12, 1, h * 8, w * 8),)


def _denorm_and_shape():
    vae = StubVAE()
    samples = torch.zeros(1, 16, 3, 4)  # 归一化 z 全零 → 去归一化后应恰为 mean
    image = m.decode_2x(samples, vae)
    assert image.shape == (1, 48, 64, 3), image.shape  # 2× 于常规 24×32
    seen = vae.seen[0, :, 0]
    mean = torch.tensor(m.LATENTS_MEAN).view(16, 1, 1)
    assert torch.allclose(seen, mean.expand(16, 3, 4), atol=1e-5), "去归一化应还原 latents_mean"
    assert 0.0 <= float(image.min()) and float(image.max()) <= 1.0


def _tiling_policy():
    vae = StubVAE()
    m.decode_2x(torch.zeros(1, 16, 2, 2), vae)
    assert vae.use_tiling is False
    vae = StubVAE()
    m.decode_2x(torch.zeros(1, 16, 130, 4), vae)
    assert vae.use_tiling is True and getattr(vae, "_cosine_blend_patched", False)


def _node_contract():
    assert m.NODE_CLASS_MAPPINGS["Krea2Upscale2xDecode"] is m.Krea2Upscale2xDecode
    it = m.Krea2Upscale2xDecode.INPUT_TYPES()
    assert it["required"]["latent"][0] == "LATENT"
    assert m.Krea2Upscale2xDecode.RETURN_TYPES == ("IMAGE",)


_denorm_and_shape()
_tiling_policy()
_node_contract()

if __name__ == "__main__":
    pass
    print("ALL KREA2 UPSCALE2X OFFLINE TESTS PASSED")
