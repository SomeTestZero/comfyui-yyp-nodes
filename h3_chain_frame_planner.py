"""H3 chain frame planner: exact integer frame math for Motion-Context chained runs.

Everything on this path is an integer frame count. The three hard grids:
  - sampled lengths live on 17n+5 (24 fps),
  - a continuation segment delivers (sampled - overlap) frames after the trim,
  - frames % 3 == 1 lengths have zero audio seam lag (Motion-Context PR #7).

Perfectly aligned totals: first segment delivers 17n+5, continuations deliver
multiples of 17, so exactly deliverable totals are 17k+5. With audio_snap the
continuation gains are multiples of 51, so aligned totals are 22+51m. The node
snaps the requested seconds to the nearest aligned total, so the last segment
delivers exactly the remainder with zero over-delivery.
"""


FPS = 24
GRID_STEP, GRID_BASE = 17, 5     # valid sampled lengths: 17n + 5
MAX_SEGMENT = 362                # 15 s at 24 fps, H3's hard cap


def _snap_up(frames, audio_snap=False):
    """Smallest valid sampled length >= frames."""
    n = max(0, -(-(frames - GRID_BASE) // GRID_STEP))
    if audio_snap:
        # 17n+5 % 3 == 1  <=>  n % 3 == 1
        n += (1 - n) % 3
    return GRID_STEP * n + GRID_BASE


def _snap_down(frames, audio_snap=False):
    """Largest valid sampled length <= frames."""
    n = max(0, (frames - GRID_BASE) // GRID_STEP)
    if audio_snap:
        n -= (n - 1) % 3
    return GRID_STEP * n + GRID_BASE


def _snap_nearest(frames, audio_snap=False):
    """Closest valid sampled length, never above the hard cap."""
    ceiling = _snap_down(MAX_SEGMENT, audio_snap)
    lo = _snap_down(min(frames, ceiling), audio_snap)
    hi = min(_snap_up(frames, audio_snap), ceiling)
    return lo if (frames - lo) <= (hi - frames) else hi


def _snap_total(frames, audio_snap=False):
    """Nearest exactly deliverable total: 17k+5, or 22+51m with audio_snap."""
    if audio_snap:
        m = max(0, round((frames - 22) / 51))
        return 22 + 51 * m
    k = max(0, round((frames - GRID_BASE) / GRID_STEP))
    return GRID_STEP * k + GRID_BASE


def _segment(total, cap, overlap, audio_snap, k, n_segments):
    """(sampled frames, new frames) of segment k."""
    if n_segments == 1:
        seg = _snap_up(total, audio_snap)
        return seg, seg
    if k == 1:
        return cap, cap
    if k < n_segments:
        return cap, cap - overlap
    rest = total - cap - (n_segments - 2) * (cap - overlap)
    seg = _snap_up(rest + overlap, audio_snap)
    return seg, seg - overlap


class H3ChainFramePlanner:
    """Plan one segment of an H3 Motion-Context chain in exact frames.

    Input the target total duration in seconds; the node snaps it to the
    nearest perfectly aligned frame total and reports this segment's frames.
    The external driver sends clip_index (1-based) and loops until
    remaining_frames hits 0. segment_seconds is this segment's delivered
    (new-content) duration, for prompt/idea generators.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "total_seconds": ("FLOAT", {"default": 10.0, "min": 0.25, "max": 3600.0, "step": 0.04,
                                            "tooltip": "成片目标总时长（秒）。自动吸附到最近的完美对齐帧数（17k+5；audio_snap 时 22+51m）。"}),
                "clip_index": ("INT", {"default": 1, "min": 1, "max": 9999,
                                       "tooltip": "当前是第几段（1 起），与 Motion-Context Save Latent 的槽位一致。"}),
                "segment_seconds": ("FLOAT", {"default": 15.0, "min": 2.0, "max": 15.1, "step": 0.04,
                                               "tooltip": "单段采样时长上限（秒），就近吸附到 17n+5 帧网格（15.0 → 362 帧=15.08s）。显存旋钮。"}),
                "overlap": ("INT", {"default": 22, "min": 0, "max": 362,
                                    "tooltip": "MotionContext 的 context_length，续写段片头被裁掉的帧数。必须与 MC 节点一致。"}),
                "audio_snap": ("BOOLEAN", {"default": False,
                                           "tooltip": "开：段长限制在 frames≡1 (mod 3) 的档位（接缝音频零滞后），此时上限 328 帧=13.7s。"}),
            },
        }

    RETURN_TYPES = ("INT", "FLOAT", "INT", "FLOAT", "INT", "BOOLEAN", "INT", "INT", "STRING")
    RETURN_NAMES = ("segment_frames", "segment_seconds", "total_frames_aligned", "total_seconds_aligned",
                    "total_segments", "is_last", "delivered_after", "remaining_frames", "segment_durations")
    FUNCTION = "plan"
    CATEGORY = "utilities"

    def plan(self, total_seconds, clip_index, segment_seconds, overlap, audio_snap):
        cap = _snap_nearest(round(segment_seconds * FPS), audio_snap)
        if cap - overlap < GRID_STEP:
            raise ValueError("H3ChainFramePlanner: cap %d - overlap %d 不足一个网格步（17 帧）" % (cap, overlap))

        total = _snap_total(round(total_seconds * FPS), audio_snap)
        if total <= cap:
            total_segments = 1
        else:
            gain = cap - overlap
            total_segments = 1 + -(-(total - cap) // gain)
        if clip_index > total_segments:
            raise ValueError("H3ChainFramePlanner: clip_index %d 超出总段数 %d（计划 %d 帧/cap %d）"
                             % (clip_index, total_segments, total, cap))

        seg, new_frames = _segment(total, cap, overlap, audio_snap, clip_index, total_segments)
        delivered = sum(_segment(total, cap, overlap, audio_snap, k, total_segments)[1]
                        for k in range(1, clip_index + 1))
        durations = ", ".join("%.1f" % (_segment(total, cap, overlap, audio_snap, k, total_segments)[1] / FPS)
                              for k in range(1, total_segments + 1))

        remaining = max(0, total - delivered)
        is_last = clip_index == total_segments
        head = "单段" if total_segments == 1 else ("首段" if clip_index == 1 else ("末段" if is_last else "中段"))
        summary = ("段 %d/%d [%s] | 采样 %d 帧 | 新内容 %d 帧 (%.2fs) | 累计 %d/%d 帧 (%.2fs/%.2fs) | 单段上限 %d 帧 (%.2fs) | remaining_frames=%d"
                   % (clip_index, total_segments, head, seg, new_frames, new_frames / FPS,
                      delivered, total, delivered / FPS, total / FPS, cap, cap / FPS, remaining))
        return {"ui": {"text": [summary]},
                "result": (seg, new_frames / FPS, total, total / FPS,
                           total_segments, is_last, delivered, remaining, durations)}


NODE_CLASS_MAPPINGS = {"H3ChainFramePlanner": H3ChainFramePlanner}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ChainFramePlanner": "H3 Chain Frame Planner"}
