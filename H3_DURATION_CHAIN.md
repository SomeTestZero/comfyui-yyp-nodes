# H3 Duration Chain 交接文档

> 位置：`custom_nodes/comfyui-yyp-nodes/h3_duration_chain.py`（自写，2026-08-26 建）
> 一句话：**时长驱动（可选音频驱动）的 H3 长视频自动续链**——点一次运行，自己把 N 段跑完并拼成整条。
> 参照对象：Ltamann Auto-Chain addon 的"服务器端自我重排队"模式；帧数学复用本包 `h3_chain_frame_planner.py`。

---

## 1. 解决什么问题

H3 单次生成上限 15s。长视频靠 Motion Context 链接多段。此前本地工作流用 easy-forLoop
在图内循环：显存累积、失败全丢、clip_index 手动注入。本包把循环改成**服务器端自我重排队**：

```
用户点一次运行
  → 本次 prompt 执行只生成"当前段"
  → Finish 节点存盘本段 MP4/末帧 PNG/latent 槽位
  → Finish 把当前图 deepcopy、planner 的 reset 置 False、塞回 prompt_queue
  → 队列执行下一段 … 直到末段
  → 末段 ffmpeg concat 出最终 MP4，不再重排
```

收益：每段独立 prompt 执行 → **16G 显存峰值 = 单段水平**；每段产物落盘 → 崩溃/重渲只影响单段；
编号槽位 retry-safe（重渲覆盖自己的槽位，不会污染下一段的上下文）。

## 2. 五个节点

| 节点（class id） | 职责 | 关键输入 | 关键输出 |
|---|---|---|---|
| `H3DurationChainPlanner` | 链控制器：分段数学 + 状态机 + 逐段提示词 + 音频块 | total_seconds / segment_seconds / overlap / audio_snap / reset / 可选 audio | chain_config, length_frames, clip_index, latent_prefix, prompt, audio, segment_durations 等 |
| `H3DurationChainLoadLatent` | 前段 latent 加载门 | chain_config | LATENT（首段/缺槽位 → None） |
| `H3DurationChainContext` | Motion Context 门 | conditioning, vae, latent, chain_config, context_length | conditioning, trim_frames（无上下文 → 直通 + trim 0） |
| `H3DurationChainFrameRef` | 逐段参考图 | initial_image, mode（last_frame/original） | IMAGE（续写段 = 上段末帧 PNG） |
| `H3DurationChainFinish` | 存盘 + 重排/拼接（OUTPUT_NODE） | chain_config, images, 可选 audio/source_audio | 无（写文件） |

**两种驱动模式**（Planner 的可选 audio 输入决定）：
- 不接 audio = **时长驱动**：总长 = total_seconds；H3 原生音频靠成对 AV latent 上下文自然续接，不需要任何输入音频
- 接上 audio = **音频驱动**（对口型/配乐）：总长 = 音频时长；每段输出按全局时间轴切的音频块
  （窗口 = 采样窗，含 overlap 头部），喂 Ref2VA 音频参考；Finish 支持用源音轨覆盖最终片

## 3. 关键机制（改代码前必读）

### 3.1 状态机
- `_CHAINS`（模块级 dict，进程内存活）以 chain_id 为键存：分段计划、当前 clip、音频波形（音频模式）
- Planner `reset=True`（手动运行的默认值）→ 重建计划、clip=start_clip；自动重排的拷贝里 reset 被 Finish 改为 False
- Finish 在每段成功后 `st["clip"] += 1` 再重排；**末段拼接后把 clip 置为 end+1** → 再执行 Planner 会响亮报错"链已完成"（防呆）
- 断点续跑 / 单段重渲：同 chain_id + start_clip=目标段 + reset 开

### 3.2 帧数学（复用 h3_chain_frame_planner，勿复制）
- 采样段长 ∈ 17n+5 网格；audio_snap 开时限到 frames≡1 (mod 3) 档（接缝音频零滞后，此时单段上限 328 帧=13.7s）
- 总长吸附到精确交付总量（17k+5 / 22+51m），末段只采所需（比旧工作流"每段都采满 cap"省时间）
- 续写段交付 = 采样 - overlap；全局时间轴起点 = 前序各段交付之和；**音频块窗口左端 = 起点 - overlap**（head 锚定几何，别改回不带 overlap 的版本）

### 3.3 与原包（NikoDemon80 Motion Context）的关系
- **补丁只有原包一个主人**：本包不打任何 H3 补丁，Context/LoadLatent 门通过 `nodes.NODE_CLASS_MAPPINGS`
  运行时查找原包节点类并委托调用（查找在执行时而非 import 时，规避 custom-node 加载顺序循环）
- 原包节点 `MiniMaxH3MotionContext` 无上下文输入时会 raise，所以首段必须走本包的门做直通
- 原包 LoadLatent `clip_index=0` 会捞"最新文件"（跨链污染），本包的门在 prev<1 或槽位缺失时返回 None
- **MC v0.4.0 后本包自动继承两个机制（8-28 核实，委托链传导）**：① 删运行时补丁后 keyframe 交给 ComfyUI 0.34.0 原生布局放置，本链已自动跑在原生内部锚定上；② v0.4.0 显式 merge conditioning 里已有的 AddGuide 锚点（钉入头定开头、锚点定结尾）——当前 demo 图未用 AddGuide，若要启用：在 Ref2VA 之后、Context 门之前接 `MiniMaxH3AddGuide`，锚点必须落在钉入头（overlap 帧）之后，否则被 MC 丢弃（head 内已由钉入头决定）
- **铁律不变**：不要再装第三个链接类包（tritant/LeonQ8 等都改同一处 H3 内部）

### 3.4 文件布局（全部在 ComfyUI output 目录下）
```
output/h3_duration_chain/<chain_id>/<chain_id>_clip_001.mp4        # 分段成片
output/h3_duration_chain/<chain_id>/<chain_id>_clip_001_last.png   # 末帧（下段 last_frame 参考）
output/h3_duration_chain/<chain_id>/<chain_id>_latent_00001.safetensors  # MC 上下文槽位（原包 SaveLatent 写）
output/h3_duration_chain/<chain_id>.mp4                            # 最终拼接片
```

### 3.5 重排队细节（抄自 Ltamann，已验证）
- `_requeue()`：`prompt_queue.currently_running` 取当前 prompt → deepcopy → Planner 节点 reset=False
  → 可选换种子（Finish 的 `randomize_seed_per_clip`；动 RandomNoise 的 `noise_seed` + **白名单采样器的 `seed`**
  （`_SEED_INPUT_SAMPLERS`：SelfLiftAvatarH3Sampler/SelfLiftAvatarImageSampler，2026-09-18 加，
  供 selflift 变体工作流每段换种子）；不碰 LLM 节点的 seed——Idea/Enhancer/Translator 靠 seed 不变保持全链同一张节拍表）
  → 以新 prompt_id 放回队列。兼容 5/6 元素 prompt 元组
- **2026-08-28 两连修（均有回归用例，见 test_h3_duration_chain_offline.py 的 _requeue 段）**：
  ① 旧版把所有 `seed` 输入一起随机 → LLM seed 也被换，每段重生不同故事、BeatPicker 跨故事取拍且不报错。
  ② 变异块曾被错误嵌进 5 元组分支：6 元组队列（现版本 ComfyUI 默认）路径完全不变异，且放回未经 deepcopy
     的原始 prompt → 无限重跑 clip 1（日志特征：每次 "queued clip 2" 后紧跟 "planned ... 段 1/4 [首段]"），
     整条链表现为"每次结果都一样"。现两条路径均深拷贝 + 变异生效。
- LoadLatent 门（2026-08-28 修复）：委托原包时传**槽位绝对路径** `_latent_slot(chain_id, prev)`，
  不再传 Planner 的 latent_prefix——原包 `_resolve_latent_path` 只接受"文件或文件夹"，
  prefix 形态（`h3_chain_latent`，无编号后缀）会在 clip 2 报
  `neither a file nor a folder`。门不再有 latent_prefix 输入（Planner 的 latent_prefix 现在只喂 SaveLatent）
- `outputs_to_execute` 沿用当前 prompt 的，Finish 是 OUTPUT_NODE 必然在内

## 4. Demo 工作流

`user/default/workflows/video_minimax_h3_r2v_DurationChain_demo.json`
（由 `my_temp_test/make_duration_chain_demo.py` 从 `_MotionContext.json` 手术生成，原文件未动）

- 烟测参数：总长 10s / 单段 5s / overlap 22 / audio_snap 开 → 2 段（各采 124 帧=5.17s，交付 5.2s+4.2s）
- 手术内容：删 forLoop 圈 11 节点；新增 900-904 五节点；Ref2VA.length / BeatPicker.clip_index /
  SaveLatent.prefix+clip_index / IdeaGenerator.beat_durations 全部改接 Planner；BasicGuider/Trim 改接 Context 门；
  FrameRef 占 Ref2VA 的 ref_image_2 空槽
- LLM 提示词链（IdeaGenerator→BeatPicker→Enhancer）零改动兼容：Planner 输出里特意保留了
  segment_seconds / total_seconds_aligned / total_segments / segment_durations 四个输出

## 4.1 SelfLift 变体工作流（2026-09-18）

`user/default/workflows/video_minimax_h3_r2v_参考生成_selflift-Avatar_MC续链长视频.json`
（以 demo 为骨架手术生成：采样链换成 SelfLiftAvatarH3Sampler + 官方 BlockSparseAttention(sol-attn)，
删 SamplerCustomAdvanced/BasicGuider/RandomNoise/KJ LowVRAM/ScheduledSolAttentionPatch；
Spectrum 的 offline_smoothing_replay 必须 false（与 SelfLift 回调计数冲突）；
分辨率 1344×768、segment_seconds 15（audio_snap 吸附 328 帧/段）、res_multistep（v0.1.4 起支持）；
FrameRef last_frame 钉入 + trim 不变；ref_image_2 重复占槽已断开）

- 用法：改 PrimitiveFloat（total_seconds）→ 点一次运行，Finish 自动逐段重排队，末段自动 concat
- SelfLift 采样器的 `seed` 由 `_requeue` 白名单机制每段换新（见 §3.5）；widget 保持 fixed
- 单镜头连贯性测试：IdeaGenerator 提示词与 Planner style_prompt 均已写死"单一连续镜头不切镜"约束

## 5. 测试与排错

离线测试（不需要 ComfyUI 运行时，stub folder_paths/server）：
```bash
python custom_nodes/comfyui-yyp-nodes/tests/test_h3_duration_chain_offline.py
```
覆盖：时长驱动 3 段链状态机 + 真实 ffmpeg 出片拼接 + 完成后守卫 + 断点续跑 + 音频驱动窗口。

线上日志关键字：`h3_duration_chain`（INFO 级，段计划/存盘/重排/拼接各一条）。

| 症状 | 排查 |
|---|---|
| 第二段没自动排上 | 控制台有无 `queued clip N`；Finish 是否在图里且被连线触发；`currently_running` 必须恰好 1 个 prompt |
| 首段就报错 nothing to pin | 检查是否把原包 MC 节点直接接进了链——必须用本包 Context 门 |
| 续写段画面突变 | 前段 latent 槽位是否真存在（Planner 会 warn 缺失并直通）；分辨率中途不可变 |
| 最终片音画不齐 | audio_snap 必须开；检查分段 MP4 各自是否正常再怀疑 concat |
| 拼接失败 | `-c copy` 要求各段编码参数一致（都是本包同一个 ffmpeg 调用产的，正常不会不一致） |

## 6. 已知限制（v1 有意为之）

- 分段参考图只有 last_frame/original 两档，无逐段自定义参考图（需要的话上 AIMixer Director / tritant Extender）
- 链内分辨率/模型/采样栈必须全程一致（latent 不可缩放，原包 MC 会硬检查）
- 拼接用 `-c copy` 无损但要求同参数编码；最终片想再压一道自己过 Topaz/ffmpeg
- 时长驱动模式下 Planner 的 audio 输出为 None，别把空输出接进需要 AUDIO 的节点
