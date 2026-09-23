# comfyui-yyp-nodes

自用 ComfyUI 杂项节点合集，git clone 到 `custom_nodes/` 即可使用。

## 节点列表

| 节点 ID | 显示名 | 模块 | 功能 |
|---|---|---|---|
| `FreeModelsPassthrough` | Free All Models (Passthrough) | `free_models.py` | 透传任意类型数据，执行时先卸载所有已加载/staged 模型并清缓存。串在"不再需要模型"和"吃内存的无模型节点"（如 Topaz 放大）之间，靠数据依赖保证顺序 |
| `Sage3AttentionPatch` | SageAttention3 Attention (FP4) | `sage3_attention.py` | 将扩散模型的注意力替换为 SageAttention3（Blackwell FP4）。需要 `sageattn3` 包；masked 或不支持的 shape 自动回退 pytorch attention。未安装时直接报错 |
| `H3ResolutionPicker` | Resolution Picker (H3 尺寸直选版) | `h3_resolution_selector.py` | 先选宽高比（16:9 / 1:1 / 3:2 …，16:9 置顶），再在该比例的档位里选尺寸，选项形如 `1344x768 (0.98MP)`；组内按像素量从小到大，每组默认满血档；超过官方画布（短边 768 + 面积上限 768×1344）的档位带 `⚠超画布` |
| `H3ResolutionSelector` | Resolution Selector (H3 精度版) | `h3_resolution_selector.py` | 与 Picker 同一套档位，反过来按像素量选：选项形如 `0.98MP (1344x768)`，超画布的同样带 `⚠超画布`。两者都只有「比例 + 档位」两个下拉，输出 width/height，详见 [RESOLUTION_TABLE.md](RESOLUTION_TABLE.md) |

## 说明

- 各节点在合并进本包之前曾以散文件形式单独存在，节点 ID 未变，旧工作流可直接兼容。
- `H3ResolutionPicker` / `H3ResolutionSelector` 用 `io.DynamicCombo`（ComfyUI 原生「选项决定后续输入」机制，同内置 Save Image (Advanced) 的 format），prompt 里嵌套项的键是 `aspect_ratio.resolution`。Selector 旧版的 `preset` / `megapixels` / `multiple` 三个输入已删除，老工作流里的这两个值需要重新在下拉里选一次。
- `free_models.py` 使用 `comfy_api.latest` 的 io 节点风格（`define_schema` / `execute`），需要较新的 ComfyUI 前端/后端。

## WebUI 前端补丁

`web/local_queue_fix.js`（前端扩展，随本包 `WEB_DIRECTORY` 自动加载，不含节点）。

修两个同源问题：**跑完一张图后点运行没反应、必须 F5**；**弹
`提示执行失败 / Error: 用户未认证`**。

原因在前端：`ComfyApp.queuePrompt` 把"提交任务"和 ComfyUI 账号的工作区鉴权绑在同一条路径上，
且这些 await 没有超时——

- `await waitForWorkspaceSwitch()` / `await getWorkspaceAuthToken()` 在 `try/finally` **之外**：
  只要挂住，`app.processingQueue` 就永久停在 `true`，之后每次点运行都被开头那句
  `if (this.processingQueue) return false` 静默吃掉（只能 F5）；
- cloud 不可达时（如直连 `*.googleapis.com` 超时）表现为两种：① 弹"用户未认证"并清空队列；
  ② 按钮变哑巴。上游 main / v1.55.9 结构一致未修（同类反馈见
  Comfy-Org/ComfyUI_frontend issue #14389）。

本地服务端不需要 cloud token，所以补丁直接绕开，不动服务端：

1. **cloud 请求超时**：`window.fetch` 对 `*.comfy.org` / `*.googleapis.com` /
   `*.firebaseapp.com` / `*.googleusercontent.com` 的单次请求加 10s 上限；
   本地 127.0.0.1 请求、以及调用方自带 `signal` 的请求完全不动。
2. **工作区 token 占位放行**：先取缓存；拿不到时最多等 2.5s，然后给占位值
   （仅当存在活动工作区、原本会被那条 fail-closed 守卫拦下时），本地排队照常提交；
   工作区切换等待同样最多 2.5s，超时/失败不再拖住排队。
3. **看门狗**：单次提交超过 30s 未返回时复位 `app.processingQueue` 并清掉堆积的点击，
   按钮不会变哑巴（极小概率下迟到的请求会让服务端多收一次，队列面板删掉即可）。

不产生任何弹窗、toast；cloud 部署（`*.comfy.org`）自动跳过。

- 验证：浏览器 Console 敲 `window.__yypLocalQueueFix` →
  `{installed: true, storesPatched: true, stubs: 0, watchdogHits: 0, fetchPatched: true}`；
  `stubs` = "cloud 拿不到 token 时放行"的次数（即以前会弹框的那些时刻，正常网络下始终为 0）。
- 离线自测：`node tests/test_local_queue_fix.mjs`（模拟浏览器环境，22 项断言，不联网）。
- 生效：修改/新增 `web/` 下的文件后重启 ComfyUI（web 目录在启动时登记），浏览器 `Ctrl+F5` 强刷。
- 降级：补丁依赖前端内部命名（Pinia store `teamWorkspace` / `auth` / `workspaceAuth`，
  方法 `getWorkspaceAuthToken` / `waitForWorkspaceSwitch` / `getWorkspaceToken`，已在 1.51.10
  与 v1.55.9 核对一致）。前端改名后补丁自动降级（只剩 fetch 超时 + 看门狗，
  `storesPatched` 停在 false），按新名字更新本文件即可。
