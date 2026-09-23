"""H3 两阶段续接：把提升后的干净高分辨率 latent 变成合法的 σ_resume 噪声态。

两阶段方案（低清底片 → LBH 提升 → 精修）中，精修 SCA 的 sigmas 从 σ_step 起
步，其 latent_image 必须是 σ_step 的噪声态；而 Lift 输出的是干净估计。本节点
按 selflift-Avatar 过渡段（nodes.py transition）同一套数学完成换装：

    state = model_sampling.noise_scaling(sigma_k, noise, z0)     # σ_k 重加噪
    state = state + (state - z0) * ((sigma_next - sigma_k)/sigma_k)  # 解析 Euler 补区间
    resume = process_latent_out(inverse_noise_scaling(sigma_next, state))

sigma_k/sigma_next 取自输入 sigmas 张量的末两个元素（SplitSigmas.high 的
[-2]/[-1]，边界与 low 共享，无缺口）。所有流（视频/音频）同 danced，噪声
逐流独立种子。重跑安全：同 seed 同输入 → 同输出。
"""

import comfy.sample


class H3ResumeFromClean:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL", {"tooltip": "精修链同一模型，取 model_sampling 做噪声缩放约定"}),
            "clean_latent": ("LATENT", {"tooltip": "Lift 输出的干净高分辨率 AV latent（模型空间）"}),
            "sigmas": ("SIGMAS", {"tooltip": "接 SplitSigmas 的 high 输出；取末两个 σ 作 σ_k/σ_next"}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                             "control_after_generate": True, "tooltip": "重加噪种子（固定即可复现）"}),
        }}

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("resume_latent",)
    FUNCTION = "resume"
    CATEGORY = "utilities"
    DESCRIPTION = ("把干净高分辨率 latent 重加噪成 σ_resume 噪声态，供精修 SCA 续接。"
                   "数学与 selflift-Avatar 过渡段一致。")

    def resume(self, model, clean_latent, sigmas, seed):
        if sigmas is None or sigmas.numel() < 2:
            raise ValueError("H3ResumeFromClean: sigmas 至少需要两个元素（σ_k, σ_next）")
        sigma_k = sigmas[-2]
        sigma_next = sigmas[-1]
        ms = model.get_model_object("model_sampling")

        samples = clean_latent["samples"]
        if isinstance(samples, list):
            streams, nested = samples, True
        elif samples.is_nested:
            streams, nested = list(samples.unbind()), True
        else:
            streams, nested = [samples], False

        out = []
        for i, z0 in enumerate(streams):
            noise = comfy.sample.prepare_noise(z0, (int(seed) + i) % (1 << 64), None).to(z0)
            state = ms.noise_scaling(sigma_k, noise, z0)
            step = ((sigma_next - sigma_k) / sigma_k).to(device=state.device, dtype=state.dtype)
            state = state + (state - z0.to(state)) * step
            state = ms.inverse_noise_scaling(sigma_next, state)
            out.append(model.model.process_latent_out(state))

        packed = out[0] if not nested else _nested(out)
        res = clean_latent.copy()
        res["samples"] = packed
        return (res,)


def _nested(streams):
    import comfy.nested_tensor
    return comfy.nested_tensor.NestedTensor(streams)


NODE_CLASS_MAPPINGS = {"H3ResumeFromClean": H3ResumeFromClean}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ResumeFromClean": "H3 Resume From Clean (两阶段续接)"}
