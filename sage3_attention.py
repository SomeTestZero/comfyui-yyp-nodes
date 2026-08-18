import logging

from comfy.ldm.modules.attention import attention3_sage, SAGE_ATTENTION3_IS_AVAILABLE


def _sage3_override(func, q, k, v, heads, **kwargs):
    return attention3_sage(q, k, v, heads, **kwargs)


class Sage3AttentionPatch:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"model": ("MODEL",)}}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "model_patches"
    DESCRIPTION = "Run the diffusion model's attention with SageAttention3 (Blackwell FP4). Requires the sageattn3 package; falls back to pytorch attention for masked or unsupported shapes."

    def patch(self, model):
        if not SAGE_ATTENTION3_IS_AVAILABLE:
            raise RuntimeError("sageattn3 is not installed, cannot apply SageAttention3 patch.")
        m = model.clone()
        transformer_options = m.model_options.setdefault("transformer_options", {})
        transformer_options["optimized_attention_override"] = _sage3_override
        logging.info("Applied SageAttention3 (FP4) attention patch.")
        return (m,)


NODE_CLASS_MAPPINGS = {
    "Sage3AttentionPatch": Sage3AttentionPatch,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Sage3AttentionPatch": "SageAttention3 Attention (FP4)",
}
