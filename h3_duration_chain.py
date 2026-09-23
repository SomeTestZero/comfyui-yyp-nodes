"""H3 Duration Chain: duration-driven auto-chain for Motion-Context long videos.

Loop driver: the Finish node requeues the current graph on ComfyUI's
prompt_queue (one prompt execution per clip), so per-clip VRAM peak stays at
single-clip level and every clip's MP4/latent lands on disk for resume/retry.
Segment math reuses h3_chain_frame_planner (17n+5 grid, audio_snap zero-seam).
The Motion Context core stays with the original NikoDemon80 pack -- the gate
nodes here delegate to it through the global node registry, so only one pack
ever patches H3 internals.

Modes: no audio input = duration-driven (total_seconds); audio wired =
audio-driven (total = audio length, per-clip chunk feeds the conditioning).
"""

import copy
import logging
import math
import os
import random
import re
import subprocess
import threading
import uuid
import wave

import folder_paths
import imageio_ffmpeg
import numpy as np
import server
import torch
from PIL import Image

_LOG = logging.getLogger("h3_duration_chain")
_CHAINS = {}
_LOCK = threading.Lock()

FPS = 24  # H3 native frame rate; the frame planner math is built on it


def _frame_math():
    from .h3_chain_frame_planner import _snap_total, _snap_nearest, _segment
    return _snap_total, _snap_nearest, _segment


def _chain_dir(chain_id):
    path = os.path.join(folder_paths.get_output_directory(),
                        "h3_duration_chain", chain_id)
    os.makedirs(path, exist_ok=True)
    return path


def _latent_prefix(chain_id):
    return "h3_duration_chain/%s/%s_latent" % (chain_id, chain_id)


def _latent_slot(chain_id, clip):
    return os.path.join(folder_paths.get_output_directory(),
                        "%s_%05d.safetensors" % (_latent_prefix(chain_id), clip))


def _clip_path(chain_id, clip):
    return os.path.join(_chain_dir(chain_id), "%s_clip_%03d.mp4" % (chain_id, clip))


def _last_frame_path(chain_id, clip):
    return os.path.join(_chain_dir(chain_id), "%s_clip_%03d_last.png" % (chain_id, clip))


def _node_class(class_type):
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if cls is None:
        raise RuntimeError(
            "h3_duration_chain: 需要已安装 ComfyUI-H3-Motion-Context 包"
            "（找不到节点 %s）" % class_type)
    return cls


def _current_prompt():
    running = server.PromptServer.instance.prompt_queue.currently_running
    if len(running) != 1:
        raise RuntimeError("h3_duration_chain: auto-chain requires one active prompt")
    return next(iter(running.values()))


# Sampler nodes whose per-run seed widget is named "seed" instead of "noise_seed".
# Deliberately class-scoped: LLM chain nodes also carry "seed" and must stay fixed
# so the beat sheet is stable across the whole chain.
_SEED_INPUT_SAMPLERS = {"SelfLiftAvatarH3Sampler", "SelfLiftAvatarImageSampler"}


def _requeue(randomize_seed):
    value = _current_prompt()
    if len(value) == 6:
        _, _, current, extra_data, outputs_to_execute, sensitive = value
    else:
        _, _, current, extra_data, outputs_to_execute = value
        sensitive = {}
    current = copy.deepcopy(current)
    for node in current.values():
        if node.get("class_type") == "H3DurationChainPlanner":
            node["inputs"]["reset"] = False
        if randomize_seed:
            noise = node.get("inputs", {}).get("noise_seed")
            if isinstance(noise, int):
                node["inputs"]["noise_seed"] = random.randint(0, 2**31 - 1)
            if node.get("class_type") in _SEED_INPUT_SAMPLERS:
                seed = node.get("inputs", {}).get("seed")
                if isinstance(seed, int) and not isinstance(seed, bool):
                    node["inputs"]["seed"] = random.randint(0, 2**31 - 1)
    number = -server.PromptServer.instance.number
    server.PromptServer.instance.number += 1
    prompt_id = str(uuid.uuid4())
    if len(value) == 6:
        server.PromptServer.instance.prompt_queue.put(
            (number, prompt_id, current, extra_data, outputs_to_execute, sensitive))
    else:
        server.PromptServer.instance.prompt_queue.put(
            (number, prompt_id, current, extra_data, outputs_to_execute))


def _parse_clip_prompt(style_prompt, clip_prompts, clip):
    text = (style_prompt or "").strip()
    block = ""
    current = None
    for line in (clip_prompts or "").splitlines():
        m = re.match(r"^\s*\[(\d+)\]\s*(.*)$", line)
        if m:
            current = int(m.group(1))
            if current == clip:
                block = m.group(2)
            continue
        if current == clip:
            block += "\n" + line
    block = block.strip()
    if not block:
        return text
    return (text + "\n" + block).strip()


def _ffmpeg_exe():
    return imageio_ffmpeg.get_ffmpeg_exe()


def _write_wav(path, audio):
    waveform = audio["waveform"]
    sr = int(audio["sample_rate"])
    data = waveform[0].clamp(-1.0, 1.0).cpu().numpy()  # [C, T]
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[0])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.T.tobytes())


def _write_clip_mp4(path, images, audio, fps):
    frames = (images.clamp(0, 1) * 255.0).round().to(torch.uint8).cpu().numpy()
    n, h, w, _ = frames.shape
    wav_path = None
    args = [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
            "-r", "%.6f" % fps, "-i", "-"]
    if audio is not None:
        wav_path = path + ".tmp.wav"
        _write_wav(wav_path, audio)
        args += ["-i", wav_path]
    args += ["-map", "0:v"]
    if audio is not None:
        args += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
    try:
        proc = subprocess.run(args, input=frames.tobytes(), check=False,
                              capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError("h3_duration_chain: ffmpeg clip write failed: %s"
                               % proc.stderr.decode(errors="replace")[-500:])
    finally:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)


def _stitch_final(chain_id, n_segments, source_audio):
    chain_dir = _chain_dir(chain_id)
    clips = [p for p in (_clip_path(chain_id, k) for k in range(1, n_segments + 1))
             if os.path.isfile(p)]
    if not clips:
        raise RuntimeError("h3_duration_chain: no clip MP4 found to stitch")
    final_path = os.path.join(folder_paths.get_output_directory(),
                              "h3_duration_chain", "%s.mp4" % chain_id)
    list_path = os.path.join(chain_dir, "_concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clips:
            f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
    args = [_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path]
    if source_audio is not None:
        wav_path = os.path.join(chain_dir, "_source.wav")
        _write_wav(wav_path, source_audio)
        args += ["-i", wav_path, "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c", "copy"]
    args += ["-movflags", "+faststart", final_path]
    try:
        proc = subprocess.run(args, check=False, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError("h3_duration_chain: ffmpeg stitch failed: %s"
                               % proc.stderr.decode(errors="replace")[-500:])
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)
        if source_audio is not None:
            wav_path = os.path.join(chain_dir, "_source.wav")
            if os.path.exists(wav_path):
                os.remove(wav_path)
    return final_path, len(clips)


class H3DurationChainPlanner:
    """Plan + drive one clip per execution of a duration-based H3 chain.

    Duration mode (no audio): total_seconds snapped to the aligned grid.
    Audio mode (audio wired): total = audio duration, and the per-clip audio
    chunk covering this clip's global timeline slot is emitted.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "chain_id": ("STRING", {"default": "h3_chain",
                                        "tooltip": "链项目名。决定 latent 槽位、分段 MP4、最终文件名；换项目必须换名。"}),
                "total_seconds": ("FLOAT", {"default": 30.0, "min": 0.25, "max": 3600.0, "step": 0.04,
                                            "tooltip": "时长驱动模式的目标总时长（秒），吸附到对齐帧网格。接了 audio 后忽略（总长=音频时长）。"}),
                "segment_seconds": ("FLOAT", {"default": 15.0, "min": 2.0, "max": 15.1, "step": 0.04,
                                              "tooltip": "单段采样时长上限（15.0 → 362 帧=15.08s）。显存旋钮；audio_snap 开时上限 328 帧=13.7s。"}),
                "overlap": ("INT", {"default": 22, "min": 0, "max": 362,
                                    "tooltip": "MotionContext 的 context_length，续写段片头被裁掉的帧数。必须与 Context 门节点一致。"}),
                "audio_snap": ("BOOLEAN", {"default": True,
                                           "tooltip": "开：段长落在 frames≡1 (mod 3) 档位，接缝音频零滞后（长链推荐）。关：17n+5 任意档。"}),
                "start_clip": ("INT", {"default": 1, "min": 1, "max": 9999,
                                       "tooltip": "从第几段开始。断点续跑：保留 chain_id，设成下一段，reset 开。"}),
                "end_clip": ("INT", {"default": 0, "min": 0, "max": 9999,
                                     "tooltip": "处理到第几段为止，0 = 到链尾。"}),
                "reset": ("BOOLEAN", {"default": True,
                                      "tooltip": "开 = 新链/手动重跑；自动重排队时由 Finish 节点自动关。"}),
                "style_prompt": ("STRING", {"default": "", "multiline": True,
                                            "tooltip": "每段共享的风格/角色/镜头文本。留空则完全交给下游提示词链（如 BeatPicker）。"}),
                "clip_prompts": ("STRING", {"default": "", "multiline": True,
                                            "tooltip": "可选，逐段提示词：[1] 第一段…… [2] 第二段……（行首标记）。留空 = 每段只用 style_prompt。"}),
            },
            "optional": {
                "audio": ("AUDIO", {"tooltip": "接上即音频驱动模式：链总长=音频时长，输出当前段的时间轴对齐音频块（喂 Ref2VA 音频参考）。不接 = 纯时长驱动，H3 原生音频靠 latent 上下文自然续接。"}),
            },
        }

    RETURN_TYPES = ("H3_DURATION_CHAIN", "INT", "INT", "STRING", "STRING", "AUDIO", "STRING",
                    "FLOAT", "FLOAT", "INT", "STRING")
    RETURN_NAMES = ("chain_config", "length_frames", "clip_index", "latent_prefix",
                    "prompt", "audio", "info",
                    "segment_seconds", "total_seconds_aligned", "total_segments", "segment_durations")
    FUNCTION = "plan"
    CATEGORY = "utilities"

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("NaN")

    def plan(self, chain_id, total_seconds, segment_seconds, overlap, audio_snap,
             start_clip, end_clip, reset, style_prompt, clip_prompts, audio=None):
        _snap_total, _snap_nearest, _segment = _frame_math()
        chain_id = chain_id.strip() or "h3_chain"
        with _LOCK:
            st = _CHAINS.get(chain_id)
            if reset or st is None:
                cap = _snap_nearest(round(segment_seconds * FPS), audio_snap)
                if cap - overlap < 17:
                    raise ValueError("H3DurationChainPlanner: cap %d - overlap %d 不足一个网格步" % (cap, overlap))
                if audio is not None:
                    sr = int(audio["sample_rate"])
                    total_samples = int(audio["waveform"].shape[-1])
                    total = _snap_total(math.ceil(total_samples / sr * FPS), audio_snap)
                else:
                    sr, total_samples = 0, 0
                    total = _snap_total(round(total_seconds * FPS), audio_snap)
                if total <= cap:
                    n_segments = 1
                else:
                    gain = cap - overlap
                    n_segments = 1 + -(-(total - cap) // gain)
                st = {"cap": cap, "overlap": int(overlap), "audio_snap": bool(audio_snap),
                      "total": total, "n": n_segments, "clip": int(start_clip),
                      "end": min(end_clip, n_segments) if end_clip else n_segments,
                      "waveform": audio["waveform"][0].cpu() if audio is not None else None,
                      "sr": sr, "total_samples": total_samples}
                _CHAINS[chain_id] = st
                _LOG.info("h3_duration_chain: chain '%s' planned: total %d frames (%.2fs), %d segments, cap %d, overlap %d, audio_snap=%s",
                          chain_id, total, total / FPS, n_segments, cap, overlap, audio_snap)
            clip = int(st["clip"])
            if clip > st["end"]:
                raise RuntimeError("h3_duration_chain: 链 '%s' 已完成（%d/%d 段）。新跑请开 reset。"
                                   % (chain_id, st["end"], st["n"]))
            seg, new_frames = _segment(st["total"], st["cap"], st["overlap"],
                                       st["audio_snap"], clip, st["n"])
            delivered_before = sum(_segment(st["total"], st["cap"], st["overlap"],
                                            st["audio_snap"], k, st["n"])[1]
                                   for k in range(1, clip))
            is_last = clip == st["end"]
            prev_clip = clip - 1
            context_ok = prev_clip >= 1 and os.path.isfile(_latent_slot(chain_id, prev_clip))
            if prev_clip >= 1 and not context_ok:
                _LOG.warning("h3_duration_chain: clip %d 的前段 latent 槽位 %s 不存在，本段按无上下文直通",
                             clip, _latent_slot(chain_id, prev_clip))

            cfg = {"chain_id": chain_id, "clip": clip, "prev_clip": prev_clip,
                   "is_last": is_last, "n_segments": st["n"], "end": st["end"],
                   "seg_frames": seg, "new_frames": new_frames,
                   "delivered_before": delivered_before,
                   "total_frames": st["total"], "cap": st["cap"],
                   "overlap": st["overlap"], "audio_snap": st["audio_snap"],
                   "context_available": bool(context_ok),
                   "latent_prefix": _latent_prefix(chain_id),
                   "audio_driven": st["waveform"] is not None}

            audio_out = None
            if st["waveform"] is not None:
                sr = st["sr"]
                # head-anchored context: the sampled window starts `overlap`
                # frames before the delivered timeline position (clip > 1).
                start_frame = max(0, delivered_before - st["overlap"] if clip > 1 else 0)
                start = int(round(start_frame / FPS * sr))
                end = int(round((start_frame + seg) / FPS * sr))
                chunk = st["waveform"][..., start:min(end, st["total_samples"])].contiguous()
                if end > st["total_samples"]:
                    chunk = torch.nn.functional.pad(chunk, (0, end - st["total_samples"]))
                audio_out = {"waveform": chunk.unsqueeze(0), "sample_rate": sr}

            prompt = _parse_clip_prompt(style_prompt, clip_prompts, clip)
            durations = ", ".join("%.1f" % (_segment(st["total"], st["cap"], st["overlap"],
                                                      st["audio_snap"], k, st["n"])[1] / FPS)
                                  for k in range(1, st["n"] + 1))
            head = "单段" if st["n"] == 1 else ("首段" if clip == 1 else ("末段" if is_last else "中段"))
            info = ("段 %d/%d [%s] | 采样 %d 帧 | 新内容 %d 帧 (%.2fs) | 时间轴起点 %d 帧 (%.2fs) | 总长 %d 帧 (%.2fs) | 上下文 %s"
                    % (clip, st["n"], head, seg, new_frames, new_frames / FPS,
                       delivered_before, delivered_before / FPS, st["total"],
                       st["total"] / FPS, "latent 就位" if context_ok else ("无（首段）" if clip == 1 else "缺失，直通!")))
            _LOG.info("h3_duration_chain: %s | prompt: %s", info, prompt[:120])
            return {"ui": {"text": [info]},
                    "result": (cfg, seg, clip, cfg["latent_prefix"], prompt, audio_out, info,
                               new_frames / FPS, st["total"] / FPS, st["n"], durations)}


class H3DurationChainLoadLatent:
    """Clip-aware gate over the original pack's Load Latent: clip 1 (or a
    missing previous slot) yields None instead of a foreign latent."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "chain_config": ("H3_DURATION_CHAIN",),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "load"
    CATEGORY = "conditioning/minimax"

    def load(self, chain_config):
        prev = int(chain_config["prev_clip"])
        if prev < 1:
            return (None,)
        slot = _latent_slot(chain_config["chain_id"], prev)
        if not os.path.isfile(slot):
            _LOG.warning("h3_duration_chain: 前段 latent 缺失，clip %d 无上下文", chain_config["clip"])
            return (None,)
        cls = _node_class("MiniMaxH3MotionContextLoadLatent")
        return cls().load(latent_path=slot, clip_index=prev)


class H3DurationChainContext:
    """Gate over the original Motion Context node: passthrough on clip 1 /
    missing context, otherwise delegate (single patch owner stays the
    original pack)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "vae": ("VAE",),
                "latent": ("LATENT",),
                "chain_config": ("H3_DURATION_CHAIN",),
                "context_length": (["22", "5", "39", "56"], {
                    "default": "22",
                    "tooltip": "与原包 Motion Context 相同；必须与 Planner 的 overlap 一致。"}),
                "audio_context_length": ("INT", {"default": 24, "min": 0, "max": 240,
                                                 "tooltip": "尾部音频钉住长度（帧）。3 的倍数对齐 40Hz 音频网格；24 = 最后一秒。"}),
            },
            "optional": {
                "context_latent": ("LATENT", {"tooltip": "接 H3 Duration Chain Load Latent。首段/缺失时自动直通。"}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "INT")
    RETURN_NAMES = ("conditioning", "trim_frames")
    FUNCTION = "apply"
    CATEGORY = "conditioning/minimax"

    def apply(self, conditioning, vae, latent, chain_config, context_length,
              audio_context_length=24, context_latent=None):
        if context_latent is None or not chain_config.get("context_available"):
            _LOG.info("h3_duration_chain: clip %d 无上下文直通（trim 0）", chain_config["clip"])
            return (conditioning, 0)
        cls = _node_class("MiniMaxH3MotionContext")
        return cls().apply(conditioning, vae, latent, context_length,
                           audio_context_length=audio_context_length,
                           context_latent=context_latent)


class H3DurationChainFrameRef:
    """Per-clip reference image: clip 1 / original mode = initial image;
    last_frame mode = previous clip's final frame PNG saved by Finish."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "chain_config": ("H3_DURATION_CHAIN",),
            "initial_image": ("IMAGE",),
            "mode": (["last_frame", "original"], {
                "default": "last_frame",
                "tooltip": "last_frame：续写段用上段末帧（角色/场景连续性，推荐）；original：每段都用初始图。"}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "pick"
    CATEGORY = "conditioning/minimax"

    def pick(self, chain_config, initial_image, mode):
        clip = int(chain_config["clip"])
        if mode == "original" or clip == 1:
            return (initial_image,)
        path = _last_frame_path(chain_config["chain_id"], int(chain_config["prev_clip"]))
        if not os.path.isfile(path):
            _LOG.warning("h3_duration_chain: 末帧参考 %s 缺失，回退初始图", path)
            return (initial_image,)
        img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255.0
        return (torch.from_numpy(img)[None, ...],)


class H3DurationChainFinish:
    """Save this clip's MP4 + last frame, requeue the graph for the next
    clip, and stitch the final MP4 when the chain ends."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "chain_config": ("H3_DURATION_CHAIN",),
                "images": ("IMAGE", {"tooltip": "本段解码并 Trim 后的帧（接 Trim 的 images）。"}),
                "randomize_seed_per_clip": ("BOOLEAN", {"default": True,
                                                        "tooltip": "每段换噪声种子（动 RandomNoise 的 noise_seed 与 SelfLift 采样器的 seed）。LLM 节点的 seed 不受影响，节拍表全链一致。关掉则全链同一种子。"}),
                "delete_completed_latents": ("BOOLEAN", {"default": False,
                                                         "tooltip": "最终拼接成功后删除本链的 latent 槽位文件。关掉可留作续跑/重渲。"}),
            },
            "optional": {
                "audio": ("AUDIO", {"tooltip": "本段 Trim 后的音频（接 Trim 的 audio）。H3 原生音轨进分段及最终 MP4。"}),
                "source_audio": ("AUDIO", {"tooltip": "音频驱动模式专用：接原始完整音频，最终片用源音轨覆盖（对口型交付）。"}),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "finish"
    OUTPUT_NODE = True
    CATEGORY = "video"

    def finish(self, chain_config, images, randomize_seed_per_clip,
               delete_completed_latents, audio=None, source_audio=None):
        cfg = chain_config
        chain_id, clip = cfg["chain_id"], int(cfg["clip"])
        clip_path = _clip_path(chain_id, clip)
        _write_clip_mp4(clip_path, images, audio, FPS)
        last = images[-1].clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy()
        Image.fromarray(last).save(_last_frame_path(chain_id, clip))
        _LOG.info("h3_duration_chain: clip %d saved -> %s", clip, clip_path)

        if not cfg["is_last"]:
            with _LOCK:
                st = _CHAINS.get(chain_id)
                if st is not None:
                    st["clip"] = clip + 1
            _requeue(randomize_seed_per_clip)
            _LOG.info("h3_duration_chain: queued clip %d", clip + 1)
            return {"ui": {"text": ["clip %d saved, queued clip %d" % (clip, clip + 1)]}}

        final_path, n_clips = _stitch_final(chain_id, int(cfg["end"]),
                                            source_audio if cfg.get("audio_driven") else None)
        with _LOCK:
            st = _CHAINS.get(chain_id)
            if st is not None:
                st["clip"] = int(cfg["end"]) + 1  # 链已完结；再跑需开 reset
                st.pop("waveform", None)  # 源音频不再被读；reset 重跑会从节点输入重新存入
        if delete_completed_latents:
            for k in range(1, int(cfg["end"]) + 1):
                slot = _latent_slot(chain_id, k)
                if os.path.isfile(slot):
                    os.remove(slot)
        _LOG.info("h3_duration_chain: chain '%s' done: %d clips -> %s", chain_id, n_clips, final_path)
        return {"ui": {"text": ["chain done: %d clips stitched -> %s" % (n_clips, final_path)]}}


NODE_CLASS_MAPPINGS = {
    "H3DurationChainPlanner": H3DurationChainPlanner,
    "H3DurationChainLoadLatent": H3DurationChainLoadLatent,
    "H3DurationChainContext": H3DurationChainContext,
    "H3DurationChainFrameRef": H3DurationChainFrameRef,
    "H3DurationChainFinish": H3DurationChainFinish,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3DurationChainPlanner": "H3 Duration Chain Planner",
    "H3DurationChainLoadLatent": "H3 Duration Chain Load Latent",
    "H3DurationChainContext": "H3 Duration Chain Context",
    "H3DurationChainFrameRef": "H3 Duration Chain Frame Ref",
    "H3DurationChainFinish": "H3 Duration Chain Finish",
}
