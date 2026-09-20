"""NR Schedule — multi-pass neural-reconstruction plans for the DLSS5 node.

Why this exists: the engine's ``nr_passes`` field simply repeats the SAME
neural-reconstruction evaluation N times. The owner's experiments (chaining
node instances that each ran one pass with a different NR style) showed that
*varied* passes — e.g. Cinematic → Natural → Default (the owner's showcase
plan, and the scheduler's default) — beat a
monolithic ``nr_passes = 4`` by a wide margin. Schedules replace the raw
repeat counter with an explicit, per-pass plan:

- pass count + a style per pass (cycled, not repeated),
- optional per-pass full control (intensity, local tone/structure, skin
  structure, color strength, tone preservation, face-skin/grain protection,
  auto-mask) that completely bypasses the main node's widgets,
- optional per-pass pre-SR denoise strength and even a dedicated denoise
  model per pass (falling back to the main node's denoise model),
- RenoDX-inspired HDR Colour Bridge settings stay global on the main node
  and are applied once, after the final pass.

The schedule is produced by the ANTs⚡DLSS NR Scheduler node. Its JS UI
builds the dynamic per-pass rows and serializes them into the
``schedule_data`` string; the Python side here is the single source of
truth: it parses, validates, clamps, and — when the JS never ran (API
workflows, headless) — synthesizes a sensible default plan. Pure functions
only, numpy-free, fully unit-testable.

Future DLSS styles/modes: add them to ``STYLES`` (one place); the scheduler
UI, the parser validation and the main node all derive from this registry,
so a future engine build that grows new NR styles is a one-line change.
"""

import json

SCHEDULE_FORMAT_VERSION = 1
MAX_PASSES = 8

# Future-proofing: the single source of truth for NR styles. Values are the
# engine ABI ints (RenderParameters.style). New engine styles land here.
STYLES = {"Default": 0, "Natural": 1, "Cinematic": 2}

# The default plan varies styles instead of repeating one (the whole point
# of schedules over nr_passes). The owner's showcase cycle: 3 passes run
# Cinematic -> Natural -> Default, i.e. the strongest look first, then a
# natural pass, then a clean finish - and the scheduler node ships with
# passes = 3 so a freshly dropped node shows exactly that.
STYLE_CYCLE_DEFAULT = ("Cinematic", "Natural", "Default")

# Per-pass control keys (main-node widgets they bypass when active)
PER_PASS_KEYS = (
    "intensity", "local_tone", "local_structure", "skin_structure",
    "color_strength", "tone_preservation", "face_skin_protection",
    "grain_preservation", "auto_mask",
)

# value clamps (mirror the main node's widget ranges)
PER_PASS_CLAMPS = {
    "intensity": (0.0, 2.0),
    "local_tone": (0.0, 2.0),
    "local_structure": (0.0, 2.0),
    "skin_structure": (-1.0, 2.0),
    "color_strength": (0.0, 1.0),
    "tone_preservation": (0.0, 1.0),
    "face_skin_protection": (0.0, 1.0),
    "grain_preservation": (0.0, 1.0),
}

DENOISE_MODEL_SLOTS = 4  # scheduler optional inputs: denoise_model, _2, _3, _4


class ScheduleError(RuntimeError):
    """Loud, owner-style schedule failure ([ANTs] prefix added at raise)."""


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _default_pass_settings():
    """The main node's widget defaults, used when per-pass settings are off."""
    return {
        "intensity": 1.0, "local_tone": 0.0, "local_structure": 1.0,
        "skin_structure": 0.5, "color_strength": 0.5,
        "tone_preservation": 0.5, "face_skin_protection": 0.0,
        "grain_preservation": 0.0, "auto_mask": False,
    }


def default_schedule(passes: int, use_per_pass_settings: bool = False,
                     use_per_pass_denoise: bool = False) -> dict:
    """A canonical schedule synthesized from just the pass count."""
    passes = int(_clamp(int(passes), 1, MAX_PASSES))
    styles = [STYLE_CYCLE_DEFAULT[i % len(STYLE_CYCLE_DEFAULT)] for i in range(passes)]
    return {
        "version": SCHEDULE_FORMAT_VERSION,
        "passes": passes,
        "use_per_pass_settings": bool(use_per_pass_settings),
        "use_per_pass_denoise": bool(use_per_pass_denoise),
        "styles": styles,
        "passes_settings": [_default_pass_settings() for _ in range(passes)],
        "denoise_strengths": [1.0] * passes,
        # -1 = inherit the main node's denoise_model; 0..3 = scheduler slot
        "denoise_model_slots": [-1] * passes,
    }


def _validate_pass_settings(entry, where):
    if not isinstance(entry, dict):
        raise ScheduleError(f"{where}: expected an object, got {type(entry).__name__}")
    out = _default_pass_settings()
    for key in PER_PASS_KEYS:
        if key not in entry or entry[key] is None:
            continue
        value = entry[key]
        if key == "auto_mask":
            out[key] = bool(value)
        else:
            lo, hi = PER_PASS_CLAMPS[key]
            try:
                out[key] = float(_clamp(float(value), lo, hi))
            except (TypeError, ValueError):
                raise ScheduleError(f"{where}: '{key}' is not a number ({value!r})")
    return out


def parse_schedule(data, passes: int, use_per_pass_settings: bool = False,
                   use_per_pass_denoise: bool = False) -> dict:
    """Parse/validate a schedule from the JS-serialized string (or dict).

    Falls back to a synthesized default when ``data`` is empty (JS never
    ran). Unknown fields are ignored (forward compat); wrong types and
    out-of-range values are either clamped (numbers) or raise loudly
    (structural garbage).
    """
    if data is None or (isinstance(data, str) and not data.strip()):
        return default_schedule(passes, use_per_pass_settings, use_per_pass_denoise)

    if isinstance(data, str):
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ScheduleError(
                f"NR Schedule data is not valid JSON (column {exc.pos}): {exc.msg}. "
                "Re-open the ANTs DLSS NR Scheduler node so its UI regenerates the plan.")
        if not isinstance(payload, dict):
            raise ScheduleError("NR Schedule data must be a JSON object.")
    elif isinstance(data, dict):
        payload = dict(data)
    else:
        raise ScheduleError(f"NR Schedule data has unsupported type {type(data).__name__}.")

    version = int(payload.get("version", SCHEDULE_FORMAT_VERSION))
    if version != SCHEDULE_FORMAT_VERSION:
        raise ScheduleError(
            f"NR Schedule format version {version} is not supported (expected "
            f"{SCHEDULE_FORMAT_VERSION}). Update the nodepack or re-save the scheduler node.")

    try:
        count = int(payload.get("passes", passes))
    except (TypeError, ValueError):
        raise ScheduleError("NR Schedule 'passes' is not an integer.")
    if count < 1:
        raise ScheduleError(f"NR Schedule must contain at least 1 pass (got {count}).")
    count = int(_clamp(count, 1, MAX_PASSES))

    settings_on = bool(payload.get("use_per_pass_settings", use_per_pass_settings))
    denoise_on = bool(payload.get("use_per_pass_denoise", use_per_pass_denoise))

    styles_in = payload.get("styles")
    if styles_in is None:
        styles = [STYLE_CYCLE_DEFAULT[i % len(STYLE_CYCLE_DEFAULT)] for i in range(count)]
    else:
        if not isinstance(styles_in, list):
            raise ScheduleError("NR Schedule 'styles' must be a list.")
        styles = []
        for i, style in enumerate(styles_in[:count]):
            if style not in STYLES:
                raise ScheduleError(
                    f"NR Schedule pass {i + 1}: unknown style {style!r}. "
                    f"Known styles: {sorted(STYLES)}.")
            styles.append(style)
        while len(styles) < count:  # tolerate short lists (pad the cycle)
            styles.append(STYLE_CYCLE_DEFAULT[len(styles) % len(STYLE_CYCLE_DEFAULT)])

    settings_in = payload.get("passes_settings")
    if settings_on:
        if not isinstance(settings_in, list):
            raise ScheduleError("NR Schedule 'passes_settings' must be a list when "
                                "per-pass settings are enabled.")
        passes_settings = [_validate_pass_settings(e, f"pass {i + 1}")
                           for i, e in enumerate(settings_in[:count])]
        while len(passes_settings) < count:
            passes_settings.append(_default_pass_settings())
    else:
        passes_settings = [_default_pass_settings() for _ in range(count)]

    strengths_in = payload.get("denoise_strengths")
    if denoise_on:
        if not isinstance(strengths_in, list):
            raise ScheduleError("NR Schedule 'denoise_strengths' must be a list when "
                                "per-pass denoise is enabled.")
        strengths = []
        for i, value in enumerate(strengths_in[:count]):
            try:
                strengths.append(float(_clamp(float(value), 0.0, 1.0)))
            except (TypeError, ValueError):
                raise ScheduleError(f"NR Schedule pass {i + 1}: denoise strength "
                                    f"is not a number ({value!r}).")
        while len(strengths) < count:
            strengths.append(1.0)
    else:
        strengths = [1.0] * count

    slots_in = payload.get("denoise_model_slots")
    if slots_in is None:
        slots = [-1] * count
    else:
        if not isinstance(slots_in, list):
            raise ScheduleError("NR Schedule 'denoise_model_slots' must be a list.")
        slots = []
        for i, value in enumerate(slots_in[:count]):
            try:
                slot = int(value)
            except (TypeError, ValueError):
                raise ScheduleError(f"NR Schedule pass {i + 1}: denoise model slot "
                                    f"is not an integer ({value!r}).")
            if slot < -1 or slot >= DENOISE_MODEL_SLOTS:
                raise ScheduleError(
                    f"NR Schedule pass {i + 1}: denoise model slot {slot} out of range "
                    f"(-1 = main node's model, 0..{DENOISE_MODEL_SLOTS - 1} = scheduler slots).")
            slots.append(slot)
        while len(slots) < count:
            slots.append(-1)

    return {
        "version": SCHEDULE_FORMAT_VERSION,
        "passes": count,
        "use_per_pass_settings": settings_on,
        "use_per_pass_denoise": denoise_on,
        "styles": styles,
        "passes_settings": passes_settings,
        "denoise_strengths": strengths,
        "denoise_model_slots": slots,
    }


def build_pass_plan(schedule: dict, scheduler_models, main_settings: dict,
                    main_denoise_model, main_denoise_strength: float,
                    sr_stage: bool = False):
    """Resolve a canonical schedule into concrete per-pass engine-call specs.

    ``scheduler_models`` is the scheduler node's optional-input list (slots
    0..3). Bypass semantics (owner spec): when the schedule carries per-pass
    settings, the main node's corresponding widgets are IGNORED entirely for
    every pass; when it does not, the main widgets drive every pass and only
    the style cycles. Denoise falls back to the main node's model/strength
    whenever the schedule does not override it for that pass. Missing model
    for a chosen slot fails loudly, naming the empty scheduler input.

    ``sr_stage`` says the pre-denoise stage is the 1:1 DLAA SR host, which
    needs NO model - the strength is then the on/off gate (the enhancer passes
    ``pre_denoise_mode == PRE_DENOISE_SR``), so the main widget value survives
    instead of collapsing to 0 for a missing model.
    """
    count = schedule["passes"]
    plan = []
    for i in range(count):
        if schedule["use_per_pass_settings"]:
            merged = {k: schedule["passes_settings"][i][k] for k in PER_PASS_KEYS}
        else:
            merged = {k: main_settings[k] for k in PER_PASS_KEYS}

        slot = schedule["denoise_model_slots"][i]
        if slot >= 0:
            model = scheduler_models[slot]
            if model is None:
                raise ScheduleError(
                    f"[ANTs] NR Schedule pass {i + 1} uses denoise model slot {slot + 1}, "
                    f"but the scheduler's denoise_model_{'' if slot == 0 else slot + 1} "
                    "input is not connected. Connect a model there (ANTs Upscale Model "
                    "Loader) or set the pass back to the main node's model.")
        else:
            model = main_denoise_model

        if schedule["use_per_pass_denoise"]:
            strength = schedule["denoise_strengths"][i]
        else:
            strength = main_denoise_strength

        plan.append({
            "style": STYLES[schedule["styles"][i]],
            "settings": merged,
            "denoise_model": model,
            "denoise_strength": float(strength)
            if (model is not None or sr_stage) else 0.0,
        })
    return plan


def describe(schedule: dict) -> str:
    """One-line human summary for the log."""
    styles = " -> ".join(schedule["styles"])
    flags = []
    if schedule["use_per_pass_settings"]:
        flags.append("per-pass settings")
    if schedule["use_per_pass_denoise"]:
        flags.append("per-pass denoise")
    suffix = f" ({', '.join(flags)})" if flags else ""
    return f"{schedule['passes']} pass(es): {styles}{suffix}"
