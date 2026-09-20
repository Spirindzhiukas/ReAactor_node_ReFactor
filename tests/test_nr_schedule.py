"""Tests for the NR Schedule system (parse/build/plan/bypass semantics).

Usage: python tests/test_nr_schedule.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from smoke_import import install_stubs  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")


def main():
    install_stubs()
    sys.path.insert(0, str(REPO))

    from ants.dlssnr import schedule as sc

    def DLSSNRScheduler_passes_default():
        """The passes widget default the UI (and a fresh node) starts from."""
        src = (REPO / "ants" / "dlssnr" / "scheduler_node.py").read_text()
        import re as _re
        m = _re.search(r'"passes": \("INT", \{"default": (\d+)', src)
        return int(m.group(1)) if m else -1

    # ---- defaults ----
    d = sc.default_schedule(2)
    check("default: 2 passes, styles cycle Cinematic -> Natural",
          d["passes"] == 2 and d["styles"] == ["Cinematic", "Natural"])
    show = sc.default_schedule(3)
    check("default: the 3-pass showcase plan is Cinematic -> Natural -> "
          "Default (and the scheduler node ships with passes = 3)",
          show["styles"] == ["Cinematic", "Natural", "Default"]
          and DLSSNRScheduler_passes_default() == 3)
    check("default: per-pass switches off, slots inherit main (-1)",
          not d["use_per_pass_settings"] and not d["use_per_pass_denoise"]
          and d["denoise_model_slots"] == [-1, -1])
    d8 = sc.default_schedule(99)
    check("default: pass count clamped to MAX_PASSES", d8["passes"] == sc.MAX_PASSES)
    check("default: settings carry the main-widget defaults",
          d["passes_settings"][0]["intensity"] == 1.0
          and d["passes_settings"][0]["local_structure"] == 1.0
          and d["passes_settings"][0]["auto_mask"] is False)

    # ---- parse: empty falls back, json round-trips ----
    p = sc.parse_schedule("", 3)
    check("parse: empty data -> default for the pass count",
          p["passes"] == 3
          and p["styles"] == ["Cinematic", "Natural", "Default"])
    import json
    payload = dict(d)
    p2 = sc.parse_schedule(json.dumps(payload), 2)
    check("parse: json round-trip preserves the plan", p2 == sc.default_schedule(2))

    # ---- parse: validation ----
    for name, raw in [
        ("garbage json", "{not json"),
        ("json array", "[1,2]"),
        ("bad version", json.dumps({**d, "version": 99})),
        ("zero passes", json.dumps({**d, "passes": 0})),
        ("unknown style", json.dumps({**d, "styles": ["Natural", "Noir"]})),
        ("settings not a list", json.dumps({**d, "use_per_pass_settings": True, "passes_settings": 5})),
        ("strengths not a list", json.dumps({**d, "use_per_pass_denoise": True, "denoise_strengths": "x"})),
        ("bad slot range", json.dumps({**d, "denoise_model_slots": [0, 9]})),
        ("nan pass setting", json.dumps({**d, "use_per_pass_settings": True,
                                         "passes_settings": [{"intensity": "loud"}, {}]})),
    ]:
        try:
            sc.parse_schedule(raw, 2)
            check(f"parse: {name} raises loudly", False)
        except sc.ScheduleError:
            check(f"parse: {name} raises loudly", True)

    p3 = sc.parse_schedule(json.dumps({**d, "passes": 99}), 2)
    check("parse: over-large pass count clamps, lists truncate",
          p3["passes"] == sc.MAX_PASSES and len(p3["styles"]) == sc.MAX_PASSES)
    p4 = sc.parse_schedule(json.dumps({**d, "use_per_pass_settings": True,
                                       "passes_settings": [{"intensity": 42.0}, {"skin_structure": -99}]}), 2)
    check("parse: numeric values clamp into widget ranges",
          p4["passes_settings"][0]["intensity"] == 2.0
          and p4["passes_settings"][1]["skin_structure"] == -1.0)

    # ---- build_pass_plan: bypass + fallback semantics ----
    main_settings = {"style": 0, **sc._default_pass_settings(), "nr_passes": 1}
    models = [object(), None, None, None]

    sched = sc.default_schedule(2)
    plan = sc.build_pass_plan(sched, models, main_settings=main_settings,
                              main_denoise_model="MAIN_MODEL", main_denoise_strength=0.7)
    check("plan: per-pass settings OFF -> main widgets drive every pass",
          all(spec["settings"]["intensity"] == main_settings["intensity"] for spec in plan))
    check("plan: styles cycle even when settings are global",
          [spec["style"] for spec in plan] == [sc.STYLES["Cinematic"], sc.STYLES["Natural"]])
    check("plan: main denoise model + strength inherited",
          all(spec["denoise_model"] == "MAIN_MODEL" and spec["denoise_strength"] == 0.7
              for spec in plan))

    sched2 = sc.parse_schedule(json.dumps({
        "version": 1, "passes": 2, "use_per_pass_settings": True,
        "passes_settings": [{"intensity": 1.7}, {"intensity": 0.3, "auto_mask": True}],
    }), 2)
    plan2 = sc.build_pass_plan(sched2, models, main_settings=main_settings,
                               main_denoise_model="MAIN_MODEL", main_denoise_strength=0.7)
    check("plan: per-pass settings ON -> main widgets completely bypassed",
          plan2[0]["settings"]["intensity"] == 1.7 and plan2[1]["settings"]["intensity"] == 0.3
          and plan2[0]["settings"]["local_structure"] == 1.0  # default fill
          and main_settings["intensity"] == 1.0)              # main untouched
    check("plan: auto_mask honored per pass", plan2[1]["settings"]["auto_mask"] is True
          and plan2[0]["settings"]["auto_mask"] is False)

    sched3 = sc.parse_schedule(json.dumps({
        "version": 1, "passes": 2, "use_per_pass_denoise": True,
        "denoise_strengths": [1.0, 0.25],
        "denoise_model_slots": [0, -1],
    }), 2)
    plan3 = sc.build_pass_plan(sched3, models, main_settings=main_settings,
                               main_denoise_model="MAIN_MODEL", main_denoise_strength=0.7)
    check("plan: slot 0 -> scheduler model, slot -1 -> main model",
          plan3[0]["denoise_model"] is models[0] and plan3[1]["denoise_model"] == "MAIN_MODEL")
    check("plan: per-pass strengths applied where set",
          plan3[0]["denoise_strength"] == 1.0 and plan3[1]["denoise_strength"] == 0.25)

    try:
        sc.build_pass_plan(sched3, [None, None, None, None], main_settings=main_settings,
                           main_denoise_model=None, main_denoise_strength=1.0)
        check("plan: unconnected chosen slot fails loudly naming the input", False)
    except sc.ScheduleError as exc:
        check("plan: unconnected chosen slot fails loudly naming the input",
              "[ANTs]" in str(exc) and "denoise_model" in str(exc))

    # ---- describe ----
    check("describe: one-line summary",
          sc.describe(sc.default_schedule(2)) == "2 pass(es): Cinematic -> Natural")
    check("describe: flags per-pass options",
          "per-pass settings" in sc.describe(sched2) and "per-pass denoise" in sc.describe(sched3))

    # ---- scheduler node end-to-end (stubbed runtime) ----
    from ants.dlssnr.scheduler_node import DLSSNRScheduler
    out = DLSSNRScheduler().build(2, False, False, "", None, None, None, None)
    bundle = out[0]
    check("scheduler node: returns schedule + model slots",
          bundle["schedule"]["passes"] == 2 and len(bundle["denoise_models"]) == 4
          and all(m is None for m in bundle["denoise_models"]))
    try:
        DLSSNRScheduler().build(2, False, True,
                                json.dumps({"version": 1, "passes": 1, "use_per_pass_denoise": True,
                                            "denoise_strengths": [1.0], "denoise_model_slots": [0]}),
                                None, None, None, None)
        check("scheduler node: validates slot connectivity early", False)
    except sc.ScheduleError:
        check("scheduler node: validates slot connectivity early", True)

    # ---- node surface: nr_passes gone, schedule socket present ----
    import importlib
    sys.path.insert(0, str(REPO.parent))
    pkg = importlib.import_module(REPO.name)
    NODE_CLASS_MAPPINGS = pkg.NODE_CLASS_MAPPINGS
    types_def = NODE_CLASS_MAPPINGS["ANTsDLSS5Enhancer"].INPUT_TYPES()
    check("enhancer: nr_passes removed, use_nr_schedule added",
          "nr_passes" not in types_def["required"]
          and "use_nr_schedule" in types_def["required"])
    check("enhancer: nr_schedule optional socket of type NR_SCHEDULE",
          types_def["optional"].get("nr_schedule") == ("NR_SCHEDULE",))
    sched_cls = NODE_CLASS_MAPPINGS["ANTsDLSSNRScheduler"]
    sched_types = sched_cls.INPUT_TYPES()
    check("scheduler: returns NR_SCHEDULE, has 4 model slots",
          sched_cls.RETURN_TYPES == ("NR_SCHEDULE",)
          and sched_types["optional"]["denoise_model_4"] == ("UPSCALE_MODEL",))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
