# -*- coding: utf-8 -*-
"""h3_duration_chain 离线冒烟测试：stub 掉 folder_paths/server，不需要 ComfyUI 运行时。

用法（仓库根目录）：
    python custom_nodes/comfyui-yyp-nodes/tests/test_h3_duration_chain_offline.py

覆盖：时长驱动 3 段链状态机、真实 ffmpeg 分段出片与拼接、完成后守卫、断点续跑、
音频驱动模式的段数与音频块窗口。
"""
import importlib
import os
import sys
import tempfile
import types

import torch

# --- stub ComfyUI 运行时依赖（必须在 import 被测模块前完成） ---
TMP = tempfile.mkdtemp()
fp = types.ModuleType("folder_paths")
fp.get_output_directory = lambda: TMP
sys.modules["folder_paths"] = fp
sv = types.ModuleType("server")
sv.PromptServer = object
sys.modules["server"] = sv

# --- 以包方式加载（h3_duration_chain 内有相对导入） ---
PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("yyp_nodes")
pkg.__path__ = [PACK_DIR]
sys.modules["yyp_nodes"] = pkg
m = importlib.import_module("yyp_nodes.h3_duration_chain")

REAL_REQUEUE = m._requeue
m._requeue = lambda rs: REQUEUED.append(rs)  # 截获重排队，不进真实队列
REQUEUED = []

planner = m.H3DurationChainPlanner()
finisher = m.H3DurationChainFinish()
IMAGES = torch.rand(22, 64, 64, 3)
AUD = {"waveform": torch.zeros(1, 2, 32000), "sample_rate": 32000}


def run_chain(chain_id, total, seg, reset_first=True, audio=None, start_clip=1):
    """逐段跑完一条链，返回最后一段的 cfg。"""
    cfg, step = None, 0
    while True:
        step += 1
        reset = (step == 1) and reset_first
        r = planner.plan(chain_id, total, seg, 22, True, start_clip if reset else 1,
                         0, reset, "", "[1] 开场\n[2] 发展\n[3] 收尾", audio=audio)
        cfg = r["result"][0]
        aud_out = r["result"][5]
        print("  clip=%d seg=%d帧 prev=%d last=%s 音频块=%s"
              % (cfg["clip"], cfg["seg_frames"], cfg["prev_clip"], cfg["is_last"],
                 "%.2fs" % (aud_out["waveform"].shape[-1] / aud_out["sample_rate"])
                 if aud_out else "-"))
        finisher.finish(cfg, IMAGES, True, False,
                        audio=AUD if audio is None else aud_out)
        if cfg["is_last"]:
            return cfg
        assert step < 20, "段数超限，状态机可能有死循环"


print("== 时长驱动 10s/5s（audio_snap）= ")
cfg = run_chain("t_dur", 10.0, 5.0)
assert cfg["n_segments"] == 2 and cfg["seg_frames"] == 124, cfg
assert len(REQUEUED) == 1, REQUEUED  # 2 段链只重排 1 次
final = os.path.join(TMP, "h3_duration_chain", "t_dur.mp4")
assert os.path.isfile(final) and os.path.getsize(final) > 0
print("  最终拼接 OK:", final)

print("== 完成后守卫 ==")
try:
    planner.plan("t_dur", 10.0, 5.0, 22, True, 1, 0, False, "", "", audio=None)
    raise AssertionError("完成后未报错")
except RuntimeError:
    print("  OK：完成后拒绝继续")

print("== 音频驱动 50s（总长应取音频，4 段）==")
src = {"waveform": torch.randn(1, 2, 50 * 32000), "sample_rate": 32000}
cfg = run_chain("t_aud", 999.0, 15.0, audio=src)
assert cfg["n_segments"] == 4 and cfg["audio_driven"], cfg

print("== 断点续跑（start_clip=2）==")
r = planner.plan("t_aud", 999.0, 15.0, 22, True, 2, 0, True, "", "", audio=src)
assert r["result"][0]["clip"] == 2 and r["result"][0]["prev_clip"] == 1
print("  OK")

print("== _requeue 变异（5/6 元组路径回归）==")
class _FakeQ:
    def __init__(self):
        self.currently_running = {}
        self.put_items = []
    def put(self, item):
        self.put_items.append(item)
class _FakeServer:
    pass
fake_q = _FakeQ()
fake_srv = _FakeServer()
fake_srv.prompt_queue = fake_q
fake_srv.number = 100
sv.PromptServer = _FakeServer
sv.PromptServer.instance = fake_srv
PROMPT = {"908": {"class_type": "H3DurationChainPlanner", "inputs": {"reset": True}},
          "129": {"class_type": "RandomNoise", "inputs": {"noise_seed": 12345}}}
for n_fields in (6, 5):
    base = (-1, "pid-old", PROMPT, {"client_id": "x"}, ["912"])
    item = base + ({},) if n_fields == 6 else base
    fake_q.currently_running = {"cur": item}
    del fake_q.put_items[:]
    REAL_REQUEUE(True)
    assert len(fake_q.put_items) == 1, fake_q.put_items
    queued_item = fake_q.put_items[0]
    assert len(queued_item) == n_fields, (n_fields, len(queued_item))
    queued = queued_item[2]
    assert queued is not PROMPT, "requeue 必须放回深拷贝，不得共享运行中的 prompt"
    assert queued["908"]["inputs"]["reset"] is False, "reset 必须被改为 False"
    assert queued["129"]["inputs"]["noise_seed"] != 12345, "noise_seed 必须被随机化"
print("  OK：5/6 元组路径均为深拷贝 + reset=False + noise_seed 随机化")

print("\nALL PASS")
