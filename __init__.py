from .free_models import NODE_CLASS_MAPPINGS as _FREE_C, NODE_DISPLAY_NAME_MAPPINGS as _FREE_D
from .sage3_attention import NODE_CLASS_MAPPINGS as _SAGE_C, NODE_DISPLAY_NAME_MAPPINGS as _SAGE_D
from .h3_resolution_selector import NODE_CLASS_MAPPINGS as _RES_C, NODE_DISPLAY_NAME_MAPPINGS as _RES_D
from .h3_chain_frame_planner import NODE_CLASS_MAPPINGS as _PLAN_C, NODE_DISPLAY_NAME_MAPPINGS as _PLAN_D
from .h3_duration_chain import NODE_CLASS_MAPPINGS as _CHAIN_C, NODE_DISPLAY_NAME_MAPPINGS as _CHAIN_D
from .h3_lbh_lift import NODE_CLASS_MAPPINGS as _LIFT_C, NODE_DISPLAY_NAME_MAPPINGS as _LIFT_D
from .h3_prompt_bank import NODE_CLASS_MAPPINGS as _BANK_C, NODE_DISPLAY_NAME_MAPPINGS as _BANK_D
from .h3_lowres_size import NODE_CLASS_MAPPINGS as _LRSZ_C, NODE_DISPLAY_NAME_MAPPINGS as _LRSZ_D
from .h3_resume_from_clean import NODE_CLASS_MAPPINGS as _RSMC_C, NODE_DISPLAY_NAME_MAPPINGS as _RSMC_D
from .h3_audio_lock import NODE_CLASS_MAPPINGS as _ALCK_C, NODE_DISPLAY_NAME_MAPPINGS as _ALCK_D
from .krea2_upscale2x import NODE_CLASS_MAPPINGS as _KR2X_C, NODE_DISPLAY_NAME_MAPPINGS as _KR2X_D
from .qwen_image_resolution import NODE_CLASS_MAPPINGS as _QWRS_C, NODE_DISPLAY_NAME_MAPPINGS as _QWRS_D
from . import log_capture  # noqa: F401 —— 副作用：装日志环形缓冲 + /h3studio/logs 端点

NODE_CLASS_MAPPINGS = {**_FREE_C, **_SAGE_C, **_RES_C, **_PLAN_C, **_CHAIN_C, **_LIFT_C, **_BANK_C, **_LRSZ_C, **_RSMC_C, **_KR2X_C, **_ALCK_C}
NODE_DISPLAY_NAME_MAPPINGS = {**_FREE_D, **_SAGE_D, **_RES_D, **_PLAN_D, **_CHAIN_D, **_LIFT_D, **_BANK_D, **_LRSZ_D, **_RSMC_D, **_KR2X_D, **_ALCK_D}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
