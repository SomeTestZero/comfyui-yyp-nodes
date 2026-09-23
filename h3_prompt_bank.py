"""PromptBank: 跨循环的提示词存取（两阶段方案专用）。

Loop1 生成的增强提示词按段号存入内存银行，Loop2 按段号取回——保证精修链
的条件与生成链逐字一致（LLM 二次调用必然漂移，不可接受）。纯内存字典，
单次运行内有效；同前缀同槽位重复写入即覆盖（重跑安全）。
"""


_BANK = {}


class PromptBankSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"forceInput": True, "tooltip": "要存入的提示词（透传输出）"}),
            "bank_prefix": ("STRING", {"default": "twophase_prompt", "tooltip": "银行前缀，与 Load 端一致"}),
            "bank_index": ("INT", {"default": 1, "min": 1, "max": 9999, "tooltip": "段号（1 起）"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "save"
    CATEGORY = "utilities"

    def save(self, text, bank_prefix, bank_index):
        _BANK[(bank_prefix, int(bank_index))] = text
        return (text,)


class PromptBankLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "bank_prefix": ("STRING", {"default": "twophase_prompt", "tooltip": "银行前缀，与 Save 端一致"}),
            "bank_index": ("INT", {"default": 1, "min": 1, "max": 9999, "tooltip": "段号（1 起）"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "load"
    CATEGORY = "utilities"

    def load(self, bank_prefix, bank_index):
        key = (bank_prefix, int(bank_index))
        if key not in _BANK:
            raise ValueError(
                "PromptBankLoad: 银行里没有 %s 段 %d 的提示词——先跑方案A（低清链）再跑本节点所在的图"
                % (bank_prefix, int(bank_index)))
        return (_BANK[key],)


NODE_CLASS_MAPPINGS = {
    "PromptBankSave": PromptBankSave,
    "PromptBankLoad": PromptBankLoad,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptBankSave": "Prompt Bank Save (提示词存入)",
    "PromptBankLoad": "Prompt Bank Load (提示词取出)",
}
