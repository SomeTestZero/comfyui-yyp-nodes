# comfyui-yyp-nodes

自用 ComfyUI 杂项节点合集，git clone 到 `custom_nodes/` 即可使用。

## 节点列表

| 节点 ID | 显示名 | 模块 | 功能 |
|---|---|---|---|
| `FreeModelsPassthrough` | Free All Models (Passthrough) | `free_models.py` | 透传任意类型数据，执行时先卸载所有已加载/staged 模型并清缓存。串在"不再需要模型"和"吃内存的无模型节点"（如 Topaz 放大）之间，靠数据依赖保证顺序 |
| `Sage3AttentionPatch` | SageAttention3 Attention (FP4) | `sage3_attention.py` | 将扩散模型的注意力替换为 SageAttention3（Blackwell FP4）。需要 `sageattn3` 包；masked 或不支持的 shape 自动回退 pytorch attention。未安装时直接报错 |
| `H3ResolutionSelector` | Resolution Selector (H3 精度版) | `h3_resolution_selector.py` | 按宽高比 + 预设（H3 原生短边 768 / 0.72MP / 0.5MP / 自定义）输出对齐到倍数的 width/height，详见 [RESOLUTION_TABLE.md](RESOLUTION_TABLE.md) |

## 说明

- 各节点在合并进本包之前曾以散文件形式单独存在，节点 ID 未变，旧工作流可直接兼容。
- `free_models.py` 使用 `comfy_api.latest` 的 io 节点风格（`define_schema` / `execute`），需要较新的 ComfyUI 前端/后端。
