"""Free All Models (passthrough) — 透传节点：执行时卸载所有已加载/staged 模型。

用法：串在"不再需要模型"和"需要大量内存"的节点之间，例如
VAEDecode.IMAGE -> 本节点 -> TopazVideoUpscaleLocal.images。
数据依赖保证它一定在下游节点之前执行。
"""

import gc
import logging

import comfy.model_management as mm
from comfy_api.latest import io


class FreeModelsPassthrough(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="FreeModelsPassthrough",
            display_name="Free All Models (Passthrough)",
            category="utils",
            description="Passes the input through unchanged, unloading all loaded/staged models first (also frees the H3 local LLM via its unload hook). Wire it between the last model-using node and a memory-hungry model-free node (e.g. Topaz upscale). Nodes after it that need a model will trigger a reload.",
            inputs=[io.AnyType.Input("anything")],
            outputs=[io.AnyType.Output(display_name="passthrough")],
        )

    @classmethod
    def execute(cls, anything) -> io.NodeOutput:
        mm.unload_all_models()
        mm.soft_empty_cache()
        gc.collect()
        logging.info("FreeModelsPassthrough: all models unloaded.")
        return io.NodeOutput(anything)


NODE_CLASS_MAPPINGS = {
    "FreeModelsPassthrough": FreeModelsPassthrough,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FreeModelsPassthrough": "Free All Models (Passthrough)",
}
