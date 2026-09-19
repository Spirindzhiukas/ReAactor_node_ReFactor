"""ANTs⚡DLSS NR Scheduler — produces NR_SCHEDULE plans for the DLSS5 node.

The schedule itself is defined in ``schedule.py`` (pure, validated, loud).
This node packages it as a typed socket output. Its JS companion
(``web/dlss5_nr_schedule.js``) renders the dynamic per-pass rows and writes
the serialized plan into the ``schedule_data`` widget; the Python side here
never trusts the UI — it re-validates everything and synthesizes defaults
when the JS never ran (API workflows, headless runs).

Denoise models: the optional inputs are fixed slots (JS shows/hides them
based on the pass count). Passes that do not pick a slot inherit the main
node's ``denoise_model`` at execution time — the owner-specified default.
"""

from ..log import logger
from . import schedule as _schedule


class DLSSNRScheduler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "passes": ("INT", {"default": 2, "min": 1, "max": _schedule.MAX_PASSES,
                                   "tooltip": "Number of neural-reconstruction passes per frame "
                                              "(applied in order, each pass re-processes the previous pass output)."}),
                "use_per_pass_settings": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON",
                                                      "tooltip": "Per-pass full control (intensity, tone/structure, skin, color "
                                                                 "strength, protections, auto-mask). When ON, the main node's "
                                                                 "matching widgets are completely bypassed."}),
                "use_per_pass_denoise": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON",
                                                     "tooltip": "Per-pass pre-SR denoise strength and model slots. Passes without a "
                                                                "slot inherit the main node's denoise_model."}),
                "schedule_data": ("STRING", {"default": "",
                                             "tooltip": "Serialized schedule (written by the node's dynamic UI). Leave empty "
                                                        "for the default plan: styles cycle Nature/Cinematic over the pass count."}),
            },
            "optional": {
                "denoise_model": ("UPSCALE_MODEL",),
                "denoise_model_2": ("UPSCALE_MODEL",),
                "denoise_model_3": ("UPSCALE_MODEL",),
                "denoise_model_4": ("UPSCALE_MODEL",),
            },
        }

    RETURN_TYPES = ("NR_SCHEDULE",)
    RETURN_NAMES = ("nr_schedule",)
    FUNCTION = "build"
    CATEGORY = "ANTs"

    DESCRIPTION = (
        "Builds a multi-pass NR plan for the ANTs DLSS5 Frame Enhancer: a style "
        "per pass (default cycle: Nature/Cinematic — the owner-validated pattern "
        "that beats monolithic nr_passes), optional per-pass full settings and "
        "per-pass pre-SR denoise models. Slots left unconnected inherit the "
        "main node's denoise model and strength."
    )

    def build(self, passes, use_per_pass_settings, use_per_pass_denoise, schedule_data,
              denoise_model=None, denoise_model_2=None, denoise_model_3=None,
              denoise_model_4=None):
        parsed = _schedule.parse_schedule(schedule_data, passes,
                                          use_per_pass_settings, use_per_pass_denoise)
        models = [denoise_model, denoise_model_2, denoise_model_3, denoise_model_4]
        # validate early and loudly: a pass pointing at an unconnected slot
        # fails here, at the scheduler, not deep inside the enhancer
        _schedule.build_pass_plan(parsed, models,
                                  main_settings=_schedule._default_pass_settings(),
                                  main_denoise_model=None,
                                  main_denoise_strength=1.0)
        logger.status(f"ANTs DLSS NR Scheduler: {_schedule.describe(parsed)}")
        return ({"schedule": parsed, "denoise_models": models},)
