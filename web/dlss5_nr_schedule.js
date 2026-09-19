/** ANTs⚡DLSS NR Scheduler + ANTs⚡DLSS5 Frame Enhancer — frontend companions.
 *
 * Scheduler: renders the dynamic per-pass rows (style always; full per-pass
 * settings and per-pass denoise when enabled) and serializes them into the
 * `schedule_data` widget. The Python side re-validates everything, so this
 * UI is a convenience — API/headless workflows can also paste the JSON by
 * hand or leave it empty for the default plan.
 *
 * Enhancer: when a scheduler is connected, its state greys out and marks
 * the widgets the schedule bypasses (per-pass settings mode). Both sides
 * talk through pure graph state (node.properties + widget values), so the
 * sync works even when nothing is executing.
 *
 * Convention note: ComfyUI serves custom-node frontends from the pack's
 * `web` folder (WEB_DIRECTORY = "./web"), not a "js" folder.
 */
import { app } from "../../scripts/app.js";

const SCHED_CLASS = "ANTsDLSSNRScheduler";
const ENH_CLASS = "ANTsDLSS5Enhancer";

const STYLES = ["Default", "Nature", "Cinematic"];
const STYLE_CYCLE_DEFAULT = ["Nature", "Cinematic"];

// [widget key, human label, min, max, step, default]
const PER_PASS_NUMERIC = [
    ["intensity", "Intensity", 0.0, 2.0, 0.05, 1.0],
    ["local_tone", "Local Tone", 0.0, 2.0, 0.05, 0.0],
    ["local_structure", "Local Structure", 0.0, 2.0, 0.05, 1.0],
    ["skin_structure", "Skin Structure", -1.0, 2.0, 0.05, 0.5],
    ["color_strength", "Color Strength", 0.0, 1.0, 0.05, 0.5],
    ["tone_preservation", "Tone Preservation", 0.0, 1.0, 0.05, 0.5],
    ["face_skin_protection", "Face Skin Protection", 0.0, 1.0, 0.05, 0.0],
    ["grain_preservation", "Grain Preservation", 0.0, 1.0, 0.05, 0.0],
];
const DENOISE_SLOT_LABELS = ["Main node's model", "Slot 1", "Slot 2", "Slot 3", "Slot 4"];

// Enhancer widgets the schedule bypasses when per-pass settings are ON
const ENH_CONTROLLED = [
    "style", "intensity", "local_tone", "local_structure", "skin_structure",
    "color_strength", "tone_preservation", "face_skin_protection",
    "grain_preservation", "auto_mask", "pre_denoise_strength",
];

const MAX_PASSES = 8;

function widgetByName(node, name) {
    return (node.widgets || []).find((w) => w.name === name) || null;
}

function parseScheduleData(raw) {
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : null;
    } catch (e) {
        return null;
    }
}

/* ----------------------------- scheduler UI ----------------------------- */

function serializeScheduler(node) {
    const passesW = widgetByName(node, "passes");
    const ppSettingsW = widgetByName(node, "use_per_pass_settings");
    const ppDenoiseW = widgetByName(node, "use_per_pass_denoise");
    const dataW = widgetByName(node, "schedule_data");
    if (!passesW || !dataW) return;

    const passes = Math.max(1, Math.min(MAX_PASSES, passesW.value | 0));
    const perSettings = !!(ppSettingsW && ppSettingsW.value);
    const perDenoise = !!(ppDenoiseW && ppDenoiseW.value);

    const passState = node.__antsPasses || [];
    const styles = [];
    const settings = [];
    const strengths = [];
    const slots = [];
    for (let i = 0; i < passes; i++) {
        const st = passState[i] || {};
        styles.push(st.style && STYLES.includes(st.style.value) ? st.style.value : STYLE_CYCLE_DEFAULT[i % 2]);
        const vals = {};
        for (const [key] of PER_PASS_NUMERIC) {
            const w = st.fields ? st.fields[key] : null;
            vals[key] = typeof (w && w.value) === "number" ? w.value : null;
        }
        settings.push(vals);
        strengths.push(typeof (st.strength && st.strength.value) === "number" ? st.strength.value : 1.0);
        slots.push(st.slot && typeof st.slot.value === "string"
            ? Math.max(-1, DENOISE_SLOT_LABELS.indexOf(st.slot.value) - 1)
            : -1);
    }

    dataW.value = JSON.stringify({
        version: 1,
        passes,
        use_per_pass_settings: perSettings,
        use_per_pass_denoise: perDenoise,
        styles,
        passes_settings: settings,
        denoise_strengths: strengths,
        denoise_model_slots: slots,
    });

    // public state for downstream enhancers (pure graph state, no execution)
    node.properties = node.properties || {};
    node.properties.ants_schedule = { passes, use_per_pass_settings: perSettings, use_per_pass_denoise: perDenoise };
    refreshConnectedEnhancers(node);
    app.graph.setDirtyCanvas(true, true);
}

function rebuildSchedulerUI(node) {
    const passesW = widgetByName(node, "passes");
    const ppSettingsW = widgetByName(node, "use_per_pass_settings");
    const ppDenoiseW = widgetByName(node, "use_per_pass_denoise");
    if (!passesW || !ppSettingsW || !ppDenoiseW) return;

    // drop previously generated widgets
    if (Array.isArray(node.__antsPasses)) {
        for (const st of node.__antsPasses) {
            for (const w of [st.style, st.strength, st.slot, ...Object.values(st.fields || {})]) {
                if (!w) continue;
                const i = node.widgets.indexOf(w);
                if (i >= 0) node.widgets.splice(i, 1);
            }
        }
    }
    node.__antsPasses = [];

    const passes = Math.max(1, Math.min(MAX_PASSES, passesW.value | 0));
    const perSettings = !!ppSettingsW.value;
    const perDenoise = !!ppDenoiseW.value;
    const prev = parseScheduleData(widgetByName(node, "schedule_data")?.value);

    for (let i = 0; i < passes; i++) {
        const state = { fields: {} };
        const prevSettings = prev && Array.isArray(prev.passes_settings) ? prev.passes_settings[i] : null;

        const styleDefault =
            prev && Array.isArray(prev.styles) && STYLES.includes(prev.styles[i])
                ? prev.styles[i]
                : STYLE_CYCLE_DEFAULT[i % 2];
        state.style = node.addWidget("combo", `Style · pass ${i + 1}`, styleDefault,
            () => serializeScheduler(node), { values: STYLES });
        node.__antsPasses.push(state);

        if (perSettings) {
            for (const [key, label, min, max, step, def] of PER_PASS_NUMERIC) {
                const value = prevSettings && typeof prevSettings[key] === "number" ? prevSettings[key] : def;
                state.fields[key] = node.addWidget("number", `${label} · pass ${i + 1}`, value,
                    () => serializeScheduler(node), { min, max, step });
            }
            state.fields.auto_mask = node.addWidget("toggle", `Auto Mask · pass ${i + 1}`,
                prevSettings ? !!prevSettings.auto_mask : false,
                () => serializeScheduler(node));
        }
        if (perDenoise) {
            const prevStrength = prev && Array.isArray(prev.denoise_strengths) ? prev.denoise_strengths[i] : 1.0;
            state.strength = node.addWidget("number", `Denoise strength · pass ${i + 1}`,
                typeof prevStrength === "number" ? prevStrength : 1.0,
                () => serializeScheduler(node), { min: 0.0, max: 1.0, step: 0.05 });
            const prevSlot = prev && Array.isArray(prev.denoise_model_slots) ? prev.denoise_model_slots[i] : -1;
            state.slot = node.addWidget("combo", `Denoise model · pass ${i + 1}`,
                DENOISE_SLOT_LABELS[(prevSlot | 0) + 1] || DENOISE_SLOT_LABELS[0],
                () => serializeScheduler(node), { values: DENOISE_SLOT_LABELS });
        }
    }

    // hide/show the four denoise model input slots with the plan shape
    (node.inputs || []).forEach((inp) => {
        const m = inp.name.match(/^denoise_model(?:_(\d))?$/);
        if (!m) return;
        const slotIndex = m[1] ? parseInt(m[1], 10) - 1 : 0;
        inp.hidden = !(perDenoise && passes > slotIndex);
    });

    serializeScheduler(node);
}

/* ----------------------------- enhancer side ---------------------------- */

function upstreamScheduler(node) {
    const input = (node.inputs || []).find((i) => i.name === "nr_schedule" && i.link != null);
    if (!input) return null;
    const link = app.graph.links[input.link];
    if (!link) return null;
    const origin = app.graph.getNodeById(link.origin_id);
    return origin && origin.comfyClass === SCHED_CLASS ? origin : null;
}

function schedulerState(schedNode) {
    if (!schedNode) return null;
    if (schedNode.properties && schedNode.properties.ants_schedule) {
        return schedNode.properties.ants_schedule;
    }
    const dataW = widgetByName(schedNode, "schedule_data");
    const parsed = parseScheduleData(dataW && dataW.value);
    if (parsed) {
        return {
            passes: parsed.passes,
            use_per_pass_settings: parsed.use_per_pass_settings,
            use_per_pass_denoise: parsed.use_per_pass_denoise,
        };
    }
    return null;
}

function refreshEnhancerControls(node) {
    const state = schedulerState(upstreamScheduler(node));
    const bypass = !!(state && state.use_per_pass_settings);
    let touched = false;
    for (const name of ENH_CONTROLLED) {
        const w = widgetByName(node, name);
        if (!w) continue;
        if (w.__antsLabel === undefined) w.__antsLabel = w.label ?? name;
        const next = bypass ? `${w.__antsLabel} ⛓` : w.__antsLabel;
        if (w.disabled !== bypass || w.label !== next) touched = true;
        w.disabled = bypass;
        w.label = next;
    }
    if (touched) app.graph.setDirtyCanvas(true, true);
}

function refreshConnectedEnhancers(schedNode) {
    for (const output of schedNode.outputs || []) {
        for (const linkId of output.links || []) {
            const link = app.graph.links[linkId];
            if (!link) continue;
            const target = app.graph.getNodeById(link.target_id);
            if (target && target.comfyClass === ENH_CLASS) refreshEnhancerControls(target);
        }
    }
}

/* ------------------------------ registration ---------------------------- */

app.registerExtension({
    name: "ANTs.DLSS5.NRSchedule",
    nodeCreated(node) {
        if (node.comfyClass === SCHED_CLASS) {
            for (const name of ["passes", "use_per_pass_settings", "use_per_pass_denoise"]) {
                const w = widgetByName(node, name);
                if (!w) continue;
                const original = w.callback;
                w.callback = function (...args) {
                    if (original) original.apply(this, args);
                    rebuildSchedulerUI(node);
                };
            }
            // initial build (also covers workflow loads via onConfigure below)
            setTimeout(() => rebuildSchedulerUI(node), 0);
            const originalConfigure = node.onConfigure;
            node.onConfigure = function (...args) {
                if (originalConfigure) originalConfigure.apply(this, args);
                setTimeout(() => rebuildSchedulerUI(node), 0);
            };
        }
        if (node.comfyClass === ENH_CLASS) {
            setTimeout(() => refreshEnhancerControls(node), 0);
            const originalConnections = node.onConnectionsChange;
            node.onConnectionsChange = function (...args) {
                if (originalConnections) originalConnections.apply(this, args);
                setTimeout(() => refreshEnhancerControls(node), 0);
            };
            const originalConfigure = node.onConfigure;
            node.onConfigure = function (...args) {
                if (originalConfigure) originalConfigure.apply(this, args);
                setTimeout(() => refreshEnhancerControls(node), 0);
            };
        }
    },
});
