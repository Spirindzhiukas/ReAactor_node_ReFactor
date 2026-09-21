"""Tests for the DLSS-NR hybrid: HDR Colour Bridge math + models/DLSS discovery.

Usage: python tests/test_dlssnr_bridge.py
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from smoke_import import install_stubs  # noqa: E402

PASS = 0
FAIL = 0


def synth_pe(path, names):
    """Minimal PE32+ exporting `names` - same shape as the rig's DLLs.

    Used to pin the read-only export reader (ants/dlssnr/peexports.py) and the
    CUDA-capable-engine preference without loading anything.
    """
    import struct

    buf = bytearray(0x2400)
    put = lambda off, data: buf.__setitem__(slice(off, off + len(data)), data)  # noqa: E731
    n = len(names)
    put(0, b"MZ")
    put(0x3C, struct.pack("<I", 0x80))
    put(0x80, b"PE\x00\x00")
    put(0x84, struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022))
    opt = 0x98
    put(opt, struct.pack("<HBBIIIIIQII", 0x20B, 14, 0, 0x1400, 0, 0, 0x1000,
                         0x1000, 0x400000, 0x1000, 0x200))
    put(opt + 112, struct.pack("<II", 0x1000, 0x400))          # export dir
    # VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData
    put(opt + 240, b".rdata\x00\x00" + struct.pack("<IIII", 0x2000, 0x1000,
                                                   0x2000, 0x400) + b"\x00" * 20)
    # section: RVA 0x1000 -> raw 0x400
    def rva(r):
        return 0x400 + (r - 0x1000)

    put(rva(0x1000), struct.pack("<IIHHIIIIIII", 0, 0, 1, 0, 0, 0x1040, n, n,
                                 0x1180, 0x1080, 0x10A0))
    put(rva(0x1040), b"engine.dll\x00")
    put(rva(0x1080),
        b"".join(struct.pack("<I", 0x1300 + i * 40) for i in range(n)))
    put(rva(0x1180), struct.pack("<" + "I" * n, *[0x1500 + i * 0x10 for i in range(n)]))
    put(rva(0x10A0), struct.pack("<" + "H" * n, *range(n)))
    for i, name in enumerate(names):
        put(rva(0x1300 + i * 40), name.encode() + b"\x00")
    with open(path, "wb") as handle:
        handle.write(bytes(buf))


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

    from ants.dlssnr.hdr_bridge import (
        anchored_bridge,
        apply_bridge,
        classic_bridge,
        classic_bridge as cb,
    )
    from ants.dlssnr import discovery

    from ants.dlssnr.hdr_bridge import (
        DIFFUSE_WHITE_NITS_DEFAULT,
        PAPER_WHITE_SCALE_DEFAULT,
    )
    check("defaults: owner-tuned 220 nits / 1.0 scale",
          DIFFUSE_WHITE_NITS_DEFAULT == 220.0 and PAPER_WHITE_SCALE_DEFAULT == 1.0)

    rng = np.random.RandomState(7)
    frame = rng.rand(64, 64, 3).astype(np.float32) * 0.8
    check("classic: neutral at owner defaults (gain 1, knee 1 => identity)",
          np.allclose(classic_bridge(frame), frame, atol=1e-5))

    # ---- Classic bridge ----
    out = classic_bridge(frame)
    check("classic: finite [0,1] output", out.shape == frame.shape and np.isfinite(out).all()
          and out.min() >= 0 and out.max() <= 1)
    check("classic: zero transfer strength = pass-through",
          np.allclose(classic_bridge(frame, transfer_strength=0.0), frame))
    check("classic: paper-white gain brightens midtones",
          classic_bridge(frame, paper_white_scale=2.537).mean() > frame.mean())
    check("classic: shoulder keeps highlights <= 1", classic_bridge(frame * 0 + 1.0).max() <= 1.0)
    check("classic: diffuse white scales the knee",
          not np.allclose(classic_bridge(frame, diffuse_white_nits=120.0),
                          classic_bridge(frame, diffuse_white_nits=400.0)))
    check("classic: color strength changes chroma",
          not np.allclose(classic_bridge(frame, color_strength=0.2),
                          classic_bridge(frame, color_strength=1.8)))
    check("classic: transfer strength 0.5 sits between 0 and 1",
          abs(classic_bridge(frame, transfer_strength=0.5).mean()
              - (frame.mean() + classic_bridge(frame).mean()) / 2) < 0.05)

    # ---- Anchored bridge ----
    out_a = anchored_bridge(frame)
    check("anchored: finite [0,1] output", np.isfinite(out_a).all() and out_a.max() <= 1.0)
    check("anchored: pass-through at zero strength",
          np.allclose(anchored_bridge(frame, transfer_strength=0.0), frame))
    dark = (frame * 0.05).astype(np.float32)
    check("anchored: dark frame with lever 0 == classic (same knee, nothing measured above it)",
          np.allclose(anchored_bridge(dark, black_lever=0.0), cb(dark), atol=2e-3))
    # bright frame + paper-white gain 2: classic's fixed knee saturates every
    # gain-lifted highlight at the sRGB ceiling; the anchored knee rides up
    # with the measured highlight, so far fewer pixels clip
    bright = frame.copy()
    bright[:3, :] = 1.0
    clipped = lambda img: int((img >= 1.0 - 1e-6).sum())
    check("anchored: highlight-anchored knee clips fewer highlight pixels than classic",
          clipped(anchored_bridge(bright, paper_white_scale=2.0, black_lever=0.0))
          < clipped(cb(bright, paper_white_scale=2.0)))
    bl0 = anchored_bridge(frame, paper_white_scale=2.0, black_lever=0.0)
    bl1 = anchored_bridge(frame, paper_white_scale=2.0, black_lever=1.0)
    shadow_mask = frame.mean(axis=2) < 0.1
    check("anchored: black lever restores shadows (dark pixels closer to input)",
          abs(bl1[..., 0][shadow_mask].mean() - frame[..., 0][shadow_mask].mean())
          < abs(bl0[..., 0][shadow_mask].mean() - frame[..., 0][shadow_mask].mean()))

    # ---- GPU acceleration decision (pure) ----
    from ants.dlssnr.node import GPU_AUTO, GPU_FORCE, GPU_OFF, decide_cuda_acceleration
    ok, _ = decide_cuda_acceleration(GPU_AUTO, True, True)
    check("cuda decision: auto + torch cuda + engine ok -> CUDA", ok)
    ok, why = decide_cuda_acceleration(GPU_AUTO, True, False, "engine has no "
                                       "dlss5nr_process_cuda_v6")
    check("cuda: the refusal names the engine's own reason (the rig log used "
          "to print a bare '(False)')",
          "dlss5nr_process_cuda_v6" in why)
    check("cuda decision: engine lacking CUDA -> CPU with reason", not ok and "CUDA interop" in why)
    ok, why = decide_cuda_acceleration(GPU_FORCE, False, True)
    check("cuda decision: force gpu without torch cuda -> CPU", not ok and "no CUDA device" in why)
    ok, why = decide_cuda_acceleration(GPU_OFF, True, True)
    check("cuda decision: explicit CPU mode -> CPU", not ok and "CPU mode" in why)
    ok, _ = decide_cuda_acceleration(GPU_FORCE, True, True)
    check("cuda decision: force gpu available -> CUDA", ok)

    # ---- pre-SR denoise blend (pure) ----
    from ants.dlssnr.node import blend_frames
    orig = frame.copy()
    denoised = np.clip(frame + 0.2, 0.0, 1.0)
    check("blend: amount 1 == processed", np.allclose(blend_frames(orig, denoised, 1.0), denoised))
    check("blend: amount 0 == original", np.allclose(blend_frames(orig, denoised, 0.0), orig))
    check("blend: 0.5 sits halfway",
          np.allclose(blend_frames(orig, denoised, 0.5), (orig + denoised) / 2, atol=1e-6))

    # ---- native output smoke test + the soak instrument (pure, GPU-free) ----
    # Owner run 01:50 proved the native host works (three prompts, styles 0/1/2,
    # hr=1 from CreateFeature and EvaluateFeature). A smoke test on a synthetic
    # image is what keeps it honest without a GPU: the payload round-trips, a
    # real change passes, a no-op and a NaN do not.
    from ants.dlssnr.node import (PRE_DENOISE_OFF, PRE_DENOISE_SR,
                                  ReFactorDLSS5Enhancer, _byte_stats, _delta_text,
                                  bit_depth_line, engine_output_verdict,
                                  native_output_verdict, nr_fp16_enabled,
                                  rgba_bytes_from_rgb8, session_cache_enabled,
                                  soak_enabled, soak_line, sr_accum_enabled,
                                  sr_output_verdict)
    import os as _os
    import pathlib as _pl
    rng = np.random.default_rng(7)
    h, w = 24, 32
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[:, :, 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    rgb[:, :, 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    rgb[4:12, 4:12, 2] = rng.integers(0, 256, size=(8, 8), dtype=np.uint8)
    payload = rgba_bytes_from_rgb8(rgb)
    check("smoke: a synthetic image becomes the RGBA8 payload the host uploads "
          "(right size, opaque alpha)",
          len(payload) == h * w * 4 and payload[3::4] == b"\xff" * (h * w))
    check("smoke: the payload decodes back to the same pixels",
          np.array_equal(np.frombuffer(payload, np.uint8).reshape(h, w, 4)[:, :, :3],
                         rgb))
    changed = rgba_bytes_from_rgb8(np.clip(rgb.astype(np.int16) + 8, 0, 255)
                                   .astype(np.uint8))
    check("smoke: the verdict passes a real change, warns on a no-op and fails "
          "on non-finite output",
          changed != payload
          and native_output_verdict(False, 0)[0] == "ok"
          and native_output_verdict(True, 0)[0] == "warning"
          and native_output_verdict(False, 3)[0] == "error"
          and "byte-identical" in native_output_verdict(True, 0)[1]
          and "non-finite" in native_output_verdict(False, 3)[1])
    # ---- rig run 32: the SR pass's own output + the engine's black frame ----
    # The SR pass answered hr=0x1 while the frame the user saw was black, and
    # the engine (dlss5nr_process_cuda_v6) threw a C++ exception that its own
    # handler swallowed - the destination (torch.empty) was never written and
    # nothing was reported. Both blind spots now have a verdict, and an
    # all-black output from a non-black input is an ERROR, never a black frame
    # with no message.
    black = b"\x00" * (h * w * 4)
    check("smoke: the SR output verdict fails LOUDLY on an all-black output "
          "from a non-black input (the frame handed to the engine is black "
          "whatever the engine does), warns on a byte-identical no-op and "
          "passes a real image with its byte statistics",
          sr_output_verdict(payload, black)[0] == "error"
          and "ALL-BLACK" in sr_output_verdict(payload, black)[1]
          and "sr_strength 0" in sr_output_verdict(payload, black)[1]
          and sr_output_verdict(payload, payload)[0] == "warning"
          and "BYTE-IDENTICAL" in sr_output_verdict(payload, payload)[1]
          and sr_output_verdict(payload, changed)[0] == "info"
          and "differs from the input" in sr_output_verdict(payload, changed)[1][0]
          and sr_output_verdict(black, black)[0] == "warning")
    # ---- rig run 33: "the SR output is very weak" - in numbers, not words ----
    # The owner's reading of the 1:1 DLAA pass was "very weak / barely
    # noticeable". The verdict now carries the measurement (mean |delta| in
    # 0..255 units, share of changed channels, alpha excluded because it is
    # constant), so the next run says HOW weak instead of reproducing an
    # impression.
    check("smoke: the SR verdict carries NUMBERS - mean |delta| over RGB with "
          "the alpha excluded, and the share of channels the pass moved",
          _byte_stats(payload, payload) == (0.0, 0.0, "RGB (alpha excluded)")
          and _byte_stats(payload, changed)[0] > 0.0
          and _byte_stats(payload, changed)[1] > 0.0
          and _byte_stats(payload, changed)[2] == "RGB (alpha excluded)"
          and "mean |delta|" in _delta_text(_byte_stats(payload, changed))
          and "mean |delta|" in sr_output_verdict(payload, changed)[1][0]
          and "mean |delta|" in sr_output_verdict(payload, payload)[1]
          and _byte_stats(payload, payload[:-4]) is None
          and _delta_text(None) == "")
    # ---- the accumulation experiment: is the weakness structural? ----------
    # DLAA's denoise is temporal and the SR stage resets every pass, so the
    # pass can only resolve lightly. ANTS_SR_ACCUM=1 keeps the history across
    # the passes of ONE image (only its first pass resets) - the measurement
    # the next rig run can act on. Opt-in, and the counter is per image.
    _os.environ["ANTS_SR_ACCUM"] = "1"
    _armed = sr_accum_enabled()
    _bare = ReFactorDLSS5Enhancer.__new__(ReFactorDLSS5Enhancer)
    _bare._sr_accum = 0
    _seq = [_bare._sr_reset_for(True), _bare._sr_reset_for(True),
            _bare._sr_reset_for(True)]
    _bare._sr_accum = 0                      # a new image starts over
    _seq_continuous = [_bare._sr_reset_for(True), _bare._sr_reset_for(True)]
    del _os.environ["ANTS_SR_ACCUM"]
    _bare._sr_accum = 0
    _off_seq = [_bare._sr_reset_for(True), _bare._sr_reset_for(False),
                _bare._sr_reset_for(True)]
    # ---- rig-33 bit-depth audit: what precision does a run actually use? ----
    # The owner's question ("a 16-bit image comes in - what comes out?") has to
    # be answerable from the console, and the answer must follow the settings.
    _os.environ["ANTS_NR_RGBA8"] = "1"
    _byte_route = nr_fp16_enabled()
    del _os.environ["ANTS_NR_RGBA8"]
    check("bits: the native NR stage keeps the frame in the RGBA16F domain by "
          "default (the surfaces' own domain) and ANTS_NR_RGBA8=1 restores the "
          "rig-proven 8-bit payload for an A/B",
          nr_fp16_enabled() and not _byte_route
          and "ANTS_NR_RGBA8" in _pl.Path(REPO / "ants" / "dlssnr" / "node.py"
                                          ).read_text())
    _bits_src = _pl.Path(REPO / "ants" / "dlssnr" / "node.py").read_text()
    _native_line = bit_depth_line(True, False, False, True)
    _byte_line = bit_depth_line(True, False, False, False)
    _cuda_line = bit_depth_line(False, True, True, True)
    _host_line = bit_depth_line(False, False, False, True)
    check("bits: the bit-depth line names the precision of every stage the run "
          "uses - native float16 vs the A/B byte payload, the legacy engine's "
          "float32 paths, and the SR stage's own 8-bit textures",
          "float16 payload" in _native_line
          and "RGBA16F" in _native_line
          and "8-bit payload" in _byte_line
          and "ANTS_NR_RGBA8=1" in _byte_line
          and "float32 in/out" in _cuda_line and "CUDA" in _cuda_line
          and "host-staging" in _host_line
          and "8-bit" in _cuda_line and "pre-denoise SR stage RGBA8" in _cuda_line
          and "no pre-denoise SR stage" in _native_line
          and "float32 [0,1]" in _host_line)
    check("bits: the node emits that line once per run, before any frame is "
          "processed, and the native branch routes through evaluate_frame with "
          "the byte route one knob away",
          "logger.status(\"%s\", bit_depth_line(" in _bits_src
          and "self._bit_depth_logged = True" in _bits_src
          and "sess.evaluate_frame(" in _bits_src
          and "fp16 = nr_fp16_enabled()" in _bits_src
          and "sess.evaluate(\n" in _bits_src
          and "sess.last_output_bytes" in _bits_src)
    check("smoke: ANTS_SR_ACCUM=1 is opt-in, keeps history only for the LATER "
          "passes of one image (the first keeps temporal_history), and does "
          "nothing at all when unset",
          not sr_accum_enabled()
          and _armed
          and _seq == [True, False, False]
          and _seq_continuous == [True, False]
          and _off_seq == [True, False, True]
          and _bare._sr_accum == 0     # unset: the counter is never touched
          and "ANTS_SR_ACCUM" in _pl.Path(REPO / "ants" / "dlssnr" / "node.py"
                                          ).read_text())
    cxx = ("[ANTs] C++ exception 0xE06D7363 (magic 0x19930520) [in-flight "
           "call: dlss5nr_process_cuda_v6]")
    check("smoke: the legacy engine verdict fails LOUDLY when the destination "
          "came back all-black for a non-black source - naming the swallowed "
          "C++ throw and the one-process/two-NGX-clients combination, with the "
          "engine switch, pre_denoise_mode OFF and sr_strength 0 as the ways "
          "out",
          engine_output_verdict(True, True, cxx)[0] == "error"
          and "never written" in engine_output_verdict(True, True, cxx)[1]
          and "C++ exception" in engine_output_verdict(True, True, cxx)[1]
          and "ANTs native NGX" in engine_output_verdict(True, True, cxx)[1]
          and "ONE context per process" in engine_output_verdict(True, True,
                                                                 cxx)[1])
    check("smoke: a black output from a black input is NOT an error (nothing "
          "to report), and a recovered C++ throw with a written frame is a "
          "warning that carries the exception line",
          engine_output_verdict(False, True, None)[0] is None
          and engine_output_verdict(True, False, cxx)[0] == "warning"
          and engine_output_verdict(False, True, cxx)[0] == "warning"
          and "came back written" in engine_output_verdict(False, True,
                                                           cxx)[1])
    src_node = (REPO / "ants" / "dlssnr" / "node.py").read_text()
    src_core = (REPO / "ants" / "dlssnr" / "core.py").read_text()
    check("rig 32: the engine's own error buffer is kept even when the engine "
          "reports SUCCESS (it swallowed a C++ throw and returned fine - the "
          "buffer is the only place its complaint survives), and it is carried "
          "into the loud verdict",
          src_core.count("self.last_error = err_msg") == 2
          and 'self.last_error = ""' in src_core
          and 'getattr(self.manager, "last_error", "")' in src_node
          and "the engine's own error buffer" in src_node)
    check("rig 32: the SR session can be created BEFORE the legacy engine's "
          "own NGX init (ANTS_LEGACY_SR_FIRST=1) - the one ordering of the two "
          "NGX clients in one process that has never run - and the switch is "
          "off by default, legacy-only, and cannot fire with the stage off",
          "ANTS_LEGACY_SR_FIRST" in src_node
          and 'os.environ.get("ANTS_LEGACY_SR_FIRST") != "1"' in src_node
          and "engine != ENGINE_LEGACY or mode == PRE_DENOISE_OFF" in src_node
          and src_node.index("self._prime_sr_before_engine(")
              < src_node.index("self.load_bridge(nr_dll_version, engine)"))
    from ants.dlsssr import crashlog as _crashlog
    before = _crashlog.cxx_serial()
    check("smoke: the crash box exposes a monotonic C++-throw counter, so a "
          "caller can tell whether the engine threw DURING its own call",
          isinstance(before, int)
          and _crashlog.cxx_serial() == before
          and callable(_crashlog.last_cxx_report))
    check("smoke: a saturated readback warns, a normal one does not (the host "
          "clamps silently, so range is only visible before the clamp)",
          native_output_verdict(False, 0, 50, 100)[0] == "warning"
          and "outside [0, 1]" in native_output_verdict(False, 0, 50, 100)[1]
          and native_output_verdict(False, 0, 1, 100000)[0] == "ok")
    from ants.dlsssr.nr import fp16_anomalies
    probe = np.array([0.5, 1.5, np.nan, np.inf, -0.2, 0.0],
                     dtype=np.float16).tobytes()
    check("smoke: the RGBA16F readback scan counts NaN/Inf and out-of-range "
          "values - the two things the RGBA8 conversion would hide",
          fp16_anomalies(probe) == (2, 2, 6))

    # ---- the same instrument, wired into the native path ----
    root = _pl.Path(__file__).resolve().parents[1]
    node_src = (root / "ants" / "dlssnr" / "node.py").read_text()
    nr_src = (root / "ants" / "dlsssr" / "nr.py").read_text()
    check("native: the smoke scan runs only where the engine can damage the "
          "frame - the RGBA16F readback, asked for on the first frame",
          "def fp16_anomalies(payload)" in nr_src
          and "fp16_anomalies(raw) if check_anomalies" in nr_src
          and "last_output_anomalies" in nr_src
          and "check_anomalies=not self._native_checked" in node_src)
    _os.environ.pop("ANTS_NR_SESSION_CACHE", None)
    off = session_cache_enabled()
    _os.environ["ANTS_NR_SESSION_CACHE"] = "1"
    on = session_cache_enabled()
    _os.environ.pop("ANTS_NR_SESSION_CACHE", None)
    _os.environ["ANTS_NR_SOAK"] = "0"
    soak_off = soak_enabled()
    _os.environ.pop("ANTS_NR_SOAK", None)
    check("session cache + soak are opt-in knobs, default OFF (the run that "
          "made the native path work used per-prompt init)",
          off is False and on is True and soak_off is False)
    check("soak: the line never raises and names what it measured",
          soak_line(7).startswith("[ANTs] soak:") and "frames 7" in soak_line(7))
    check("native: the output check and the soak line are wired into the "
          "evaluate path, and a closed session is evicted from the cache",
          "self._check_native_output(same, sess)" in node_src
          and "soak_line(" in node_src
          and "def _check_native_output" in node_src
          and "def _drop_session" in node_src
          and "def _forget_cached" in node_src
          and node_src.count("self._forget_cached(self.native_session)") == 2
          and "_NR_SESSION_CACHE[key] = (self.native_session, self.native_gpu)"
              in node_src
          and "def session_cache_enabled" in node_src)

    # ---- pre-denoise: the SR stage runs on EVERY engine ----
    from ants.dlssnr.node import (PRE_DENOISE_MODEL, PRE_DENOISE_OFF,
                                  PRE_DENOISE_SR, pre_denoise_action)
    check("pre-denoise: SR mode is the stage by itself (no model), OFF and a "
          "zero strength switch it off, model mode needs the wire",
          pre_denoise_action(PRE_DENOISE_SR, 1.0, False) == "sr"
          and pre_denoise_action(PRE_DENOISE_SR, 0.0, False) is None
          and pre_denoise_action(PRE_DENOISE_MODEL, 1.0, False) is None
          and pre_denoise_action(PRE_DENOISE_MODEL, 1.0, True) == "model"
          and pre_denoise_action(PRE_DENOISE_OFF, 1.0, True) is None)
    check("pre-denoise: the SR stage is wired into all three engine paths "
          "(native, legacy CUDA, legacy host) and no longer claims to need the "
          "native engine",
          "SR pre-denoise needs the native NGX engine" not in node_src
          and "_sr_denoise_frame(\n" in node_src
          and "frame, self._sr_reset_for(do_reset)," in node_src
          and "device=cuda_dev, report=True" in node_src
          and "_sr_denoise_np(\n" in node_src
          and "frame_np, self._sr_reset_for(do_reset)," in node_src
          and "def _sr_denoise_np" in node_src
          and node_src.count("self._sr_reset_for(do_reset)") == 3
          and "sr_stage=pre_denoise_mode == PRE_DENOISE_SR" in node_src)

    # ---- dispatch ----
    check("apply_bridge dispatch + off",
          np.allclose(apply_bridge(frame, "off"), frame)
          and np.allclose(apply_bridge(frame, "nonsense"), frame)
          and np.allclose(apply_bridge(frame, "classic"), cb(frame)))

    # ---- discovery: models/DLSS layout, ANY filenames ----
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "DLSS")
        v1 = os.path.join(root, "dlssnr_testA")
        os.makedirs(v1)
        # arbitrary filenames: OreX-style single bridge + nvidia runtime renamed
        open(os.path.join(v1, "my_bridge.dll"), "wb").write(b"x")
        open(os.path.join(v1, "runtime_thing.dll"), "wb").write(b"x")
        saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
        discovery.DLSS_ROOT = root
        discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "legacy_dlssnr")
        discovery.PACKAGE_DLL_DIR = os.path.join(td, "nope")
        try:
            sets = discovery.discover_dll_sets()
            check("discovery: dlssnr_<name> folder found with ANY filenames",
                  any(s["name"] == "dlssnr_testA" and s["complete"] and len(s["dlls"]) == 2
                      for s in sets))
            check("discovery: nvngx hint detected by name",
                  any(s.get("has_nvngx") is False for s in sets))
            check("discovery: resolve by set name",
                  discovery.resolve_dll_dir("dlssnr_testA") == v1)
            check("discovery: combo = auto + names + refresh",
                  discovery.combo_choices() == ["auto", "dlssnr_testA", "refresh"])

            # flat files in models/DLSS = an unnamed set
            open(os.path.join(root, "loose_engine.dll"), "wb").write(b"x")
            sets2 = discovery.discover_dll_sets()
            check("discovery: flat models/DLSS dlls form an unnamed set",
                  any(s["name"] == "(models/DLSS)" for s in sets2))
            open(os.path.join(root, "loose_engine.dll"), "wb").close()
            os.remove(os.path.join(root, "loose_engine.dll"))

            # category subfolders: models/DLSS/<NR|SR|FG>/<version>/
            nr_v = os.path.join(root, "NR", "nvngx_v1")
            sr_v = os.path.join(root, "SR", "dlss_310_5")
            fg_v = os.path.join(root, "FG", "fg_330")
            for d in (nr_v, sr_v, fg_v):
                os.makedirs(d)
                open(os.path.join(d, "x.dll"), "wb").write(b"x")
            nr_sets = discovery.discover_dll_sets("NR")
            check("discovery: NR category set found via NR/<version>",
                  any(x["name"] == "nvngx_v1" and x["category"] == "NR" for x in nr_sets))
            check("discovery: NR selector excludes SR/FG sets",
                  not any(x["name"] in ("dlss_310_5", "fg_330") for x in nr_sets))
            sr_sets = discovery.discover_dll_sets("SR")
            check("discovery: SR category discovered for the future SR selector",
                  any(x["name"] == "dlss_310_5" and x["category"] == "SR" for x in sr_sets)
                  and not any(x["name"] == "nvngx_v1" for x in sr_sets))
            check("discovery: no filter returns every category",
                  {"nvngx_v1", "dlss_310_5", "fg_330"} <=
                  {x["name"] for x in discovery.discover_dll_sets()})
            check("discovery: NR combo has no SR entries",
                  "dlss_310_5" not in discovery.combo_choices("NR"))

            # flat dll named like an NR runtime -> label carries it
            open(os.path.join(root, "nvngx_dlssnr_RenoDX_friendly.dll"), "wb").write(b"x")
            sets3 = discovery.discover_dll_sets()
            check("discovery: flat set label names the NR runtime found inside",
                  any(x["name"] == "(models/DLSS - nvngx_dlssnr_RenoDX_friendly)"
                      for x in sets3))

            # flat dll directly inside a category folder
            open(os.path.join(root, "SR", "nvngx_dlss.dll"), "wb").write(b"x")
            check("discovery: flat files inside a category folder form that category's set",
                  any(x["name"] == "(SR)" and x["category"] == "SR"
                      for x in discovery.discover_dll_sets("SR")))

            # helper stash (owner's "Merserk's_DLLS" spelling included):
            # excluded from the NR selector, exposed via helper_dll_dirs()
            stash = os.path.join(root, "Merserk's_DLLS")
            os.makedirs(stash)
            open(os.path.join(stash, "neuroframe_engine.dll"), "wb").write(b"x")
            open(os.path.join(stash, "neuroframe_caller.dll"), "wb").write(b"x")
            check("discovery: helper stash is NOT an NR selector entry",
                  not any("Merserk" in x["name"] for x in discovery.discover_dll_sets("NR")))
            saved_helpers = discovery.PACKAGE_DLL_DIR
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "no_pkg")
            try:
                dirs = discovery.helper_dll_dirs()
                check("discovery: helper stash discovered by name",
                      any("Merserk" in d for d in dirs))
            finally:
                discovery.PACKAGE_DLL_DIR = saved_helpers

            # a generic subfolder WITHOUT an NR runtime -> OTHER, not in NR selector
            other_v = os.path.join(root, "misc", )
            os.makedirs(other_v)
            open(os.path.join(other_v, "something.dll"), "wb").write(b"x")
            check("discovery: generic folder without NR runtime -> OTHER",
                  any(x["name"] == "misc" and x["category"] == "OTHER"
                      for x in discovery.discover_dll_sets())
                  and not any(x["name"] == "misc" for x in discovery.discover_dll_sets("NR")))
        finally:
            discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    # ---- helper stash + staging: the pair lives in ONE folder ----
    import shutil as _shutil
    root = tempfile.mkdtemp()
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    discovery.DLSS_ROOT = root
    discovery.LEGACY_DLSSNR_PATH = os.path.join(root, "_nolegacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(root, "_nopkg")
    try:
        for cat in ("NR", "SR", "FG"):
            os.makedirs(os.path.join(root, cat))
            open(os.path.join(root, cat, "neuroframe_caller.dll"), "wb").write(b"c" * 8)
            open(os.path.join(root, cat, "neuroframe_engine.dll"), "wb").write(b"e" * 8)
        open(os.path.join(root, "NR", "nvngx_dlssnr.dll"), "wb").write(b"n" * 16)
        open(os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"), "wb").write(b"a" * 32)
        os.makedirs(os.path.join(root, "HELPERS"))            # empty stash
        os.makedirs(os.path.join(root, "Merserk_DLLS"))
        for name in ("neuroframe_caller.dll", "neuroframe_engine.dll"):
            open(os.path.join(root, "Merserk_DLLS", name), "wb").write(b"h" * 4)

        check("stash: an EMPTY helper folder never wins over a populated one",
              discovery.helper_dll_dirs()[0].endswith("Merserk_DLLS"))
        # both populated: Merserk_DLLS still wins (owner's one-home ruling)
        open(os.path.join(root, "HELPERS", "neuroframe_caller.dll"), "wb").write(b"x")
        check("stash: Merserk_DLLS beats a populated HELPERS folder",
              discovery.helper_dll_dirs()[0].endswith("Merserk_DLLS"))
        check("stash: helper_stash_dir points at the populated stash",
              (discovery.helper_stash_dir() or "").endswith("Merserk_DLLS"))
        check("stash: helper-named files are recognised",
              discovery._is_helper_dll_name("neuroframe_caller.dll")
              and not discovery._is_helper_dll_name("nvngx_dlssnr.dll"))

        stage = discovery.stage_legacy_runtime(
            os.path.join(root, "NR", "nvngx_dlssnr.dll"))
        staged_files = sorted(os.listdir(stage))
        check("stage: canonical name keeps the folder IN PLACE",
              os.path.normcase(stage) == os.path.normcase(os.path.join(root, "NR")))
        stage2 = discovery.stage_legacy_runtime(
            os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"))
        staged_files = sorted(os.listdir(stage2))
        check("stage: only the runtime + the helper pair are copied",
              staged_files == ["neuroframe_caller.dll", "neuroframe_engine.dll",
                               "nvngx_dlssnr.dll"], )
        check("stage: the aliased runtime is the selected BYTES",
              open(os.path.join(stage2, "nvngx_dlssnr.dll"), "rb").read() == b"a" * 32)
        check("stage: folder is content-addressed <stem>-<size>",
              os.path.basename(stage2) == "nvngx_dlssnr_alt-32")

        # no stash at all -> old behaviour, loudly
        _shutil.rmtree(os.path.join(root, "Merserk_DLLS"))
        os.remove(os.path.join(root, "HELPERS", "neuroframe_caller.dll"))
        os.makedirs(os.path.join(root, "SR", "set"))
        open(os.path.join(root, "SR", "set", "nvngx_dlssnr_b.dll"), "wb").write(b"b" * 4)
        open(os.path.join(root, "SR", "set", "some_other.dll"), "wb").write(b"o" * 4)
        stage3 = discovery.stage_legacy_runtime(
            os.path.join(root, "SR", "set", "nvngx_dlssnr_b.dll"))
        check("stage: no stash -> compat sibling copy (all siblings, loudly)",
              sorted(os.listdir(stage3)) == ["nvngx_dlssnr.dll",
                                             "nvngx_dlssnr_b.dll",
                                             "some_other.dll"]
              and open(os.path.join(stage3, "nvngx_dlssnr.dll"), "rb").read() == b"b" * 4)

        # auto selection never returns a helper dll (both NR candidates are
        # unversioned here, so the file date decides - and either way it is an
        # nvngx_dlssnr runtime, not the helper pair)
        picked = discovery.resolve_nr_runtime_path("auto")
        check("auto: picks an nvngx_dlssnr* file, never the helper pair",
              os.path.basename(picked) in ("nvngx_dlssnr.dll",
                                           "nvngx_dlssnr_alt.dll"))
        _shutil.rmtree(os.path.join(root, "Merserk_DLLS"), ignore_errors=True)
        os.remove(os.path.join(root, "NR", "nvngx_dlssnr.dll"))
        os.remove(os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"))
        try:
            discovery.resolve_nr_runtime_path("auto")
            check("auto: only helper dlls left -> loud [ANTs] error", False)
        except RuntimeError as exc:
            check("auto: only helper dlls left -> loud [ANTs] error",
                  "[ANTs]" in str(exc) and "Merserk_DLLS" in str(exc))
        # a non-canonical name that is not a helper is still usable (with a warning)
        open(os.path.join(root, "NR", "dlssnr_custom.dll"), "wb").write(b"q" * 4)
        check("auto: non-canonical non-helper dll is used by guess",
              discovery.resolve_nr_runtime_path("auto")
              .endswith("dlssnr_custom.dll"))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved

    # ---- no sets at all -> the loud owner-specified error ----
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
    discovery.DLSS_ROOT = os.path.join(tempfile.gettempdir(), "definitely_missing_dlss")
    discovery.LEGACY_DLSSNR_PATH = os.path.join(tempfile.gettempdir(), "definitely_missing_legacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(tempfile.gettempdir(), "definitely_missing_pkg")
    try:
        try:
            discovery.default_dll_dir()
            check("discovery: loud error when empty", False)
        except RuntimeError as e:
            check("discovery: loud error names dlssnr_<version_name> + any-filenames rule",
                  "dlssnr_<version_name>" in str(e) and "ANY .dll filenames" in str(e))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    # ---- per-category selectors (owner restructure) ----
    import importlib as _il
    import pathlib as _pl
    sys.path.insert(0, str(REPO.parent))
    _pkg = _il.import_module(REPO.name)
    types_def = _pkg.NODE_CLASS_MAPPINGS["ANTsDLSS5Enhancer"].INPUT_TYPES()
    req = types_def["required"]
    check("dlss5: dll_version replaced by per-category selectors",
          "dll_version" not in req
          and "nr_dll_version" in req and "sr_dll_version" in req
          and "fg_dll_version" in req)
    check("dlss5: SR model preset defaults to L (newest highest quality)",
          req["sr_model"][1]["default"] == "L - Transformer II Quality"
          and "L - Transformer II Quality" in req["sr_model"][0])
    check("dlss5: NR preset widget present, defaults to driver Default",
          req["nr_model_preset"][0][0] == "Default"
          and req["nr_model_preset"][1]["default"] == "Default")
    check("dlss5: pre_denoise_mode present, DEFAULTS to Denoise Model (rig run 33: "
          "the model is the stage that denoises), model choice named like the input",
          req["pre_denoise_mode"][1]["default"] == "Denoise Model"
          and req["pre_denoise_mode"][0][0] == "Denoise Model"
          and "SR (DLSS denoise)" in req["pre_denoise_mode"][0]
          and "Denoise Model" in req["pre_denoise_mode"][0])
    node_src = _pl.Path(REPO / "ants" / "dlssnr" / "node.py").read_text()
    check("dlss5: the DEFAULT pre-denoise mode falls back to OFF without a model "
          "(owner, rig run 33) - once per run, in words, and in the enhance default",
          "pre_denoise_mode=PRE_DENOISE_MODEL" in node_src
          and node_src.count("falling back to OFF") == 2
          and "but nothing is " in node_src
          and "has a model (denoise_model is empty and the schedule " in node_src
          and "def sr_accum_enabled" in node_src)
    check("dlss5: NR + SR session factories share _ensure_native_gpu (run-8 ordering fix)",
          node_src.count("self._ensure_native_gpu()") >= 2
          and "def _ensure_native_gpu" in node_src
          and node_src.count("if self.native_gpu is None:") == 1)
    check("dlss5: no 'refresh' entries in the category combos",
          all("refresh" not in req[w][0]
              for w in ("nr_dll_version", "sr_dll_version", "fg_dll_version")))

    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
    root = tempfile.mkdtemp(prefix="ants_cat_")
    discovery.DLSS_ROOT = root
    discovery.LEGACY_DLSSNR_PATH = os.path.join(root, "missing_legacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(root, "missing_pkg")
    try:
        os.makedirs(os.path.join(root, "NR"))
        open(os.path.join(root, "NR", "nvngx_dlssnr_RenoDX.dll"), "wb").write(b"x")
        open(os.path.join(root, "NR", "nvngx_dlssnr_320.dll"), "wb").write(b"x")
        os.makedirs(os.path.join(root, "SR", "310.9.1"))
        open(os.path.join(root, "SR", "310.9.1", "nvngx_dlss.dll"), "wb").write(b"c")
        open(os.path.join(root, "SR", "nvngx_dlss.dll"), "wb").write(b"a" * 3)
        open(os.path.join(root, "SR", "nvngx_dlss_310.9.1.dll"), "wb").write(b"b" * 5)
        check("discovery: flat NR dlls listed individually",
              discovery.category_choices("NR") == ["auto", "nvngx_dlssnr_320.dll",
                                                   "nvngx_dlssnr_RenoDX.dll"])
        check("discovery: flat SR dlls listed individually (owner duplicate test)",
              set(discovery.category_choices("SR")) == {"auto", "310.9.1",
                                                        "nvngx_dlss.dll",
                                                        "nvngx_dlss_310.9.1.dll"})
        check("naming: the SR selector lists the NEWEST build first",
              discovery.category_choices("SR")[1] == "nvngx_dlss_310.9.1.dll"
              and discovery.category_choices("SR")[-1] == "nvngx_dlss.dll")
        check("discovery: SR version subfolder + FG reserved-empty",
              "310.9.1" in discovery.category_choices("SR")
              and discovery.category_choices("FG") == ["auto"])
        check("discovery: NR runtime path resolves a chosen flat dll",
              discovery.resolve_nr_runtime_path("nvngx_dlssnr_320.dll")
              == os.path.join(root, "NR", "nvngx_dlssnr_320.dll"))
        check("discovery: NR auto -> first flat dll",
              discovery.resolve_nr_runtime_path("auto")
              == os.path.join(root, "NR", "nvngx_dlssnr_320.dll"))
        # staging: a same-named dll passes through; others are copied to the
        # writable cache as nvngx_dlss.dll (what the NGX core searches for)
        from ants.dlsssr.discovery import stage_sr_dll
        direct = stage_sr_dll(os.path.join(root, "SR", "nvngx_dlss.dll"))
        check("staging: nvngx_dlss.dll passes through to its own dir",
              direct == os.path.join(root, "SR"))
        staged = stage_sr_dll(os.path.join(root, "SR", "nvngx_dlss_310.9.1.dll"))
        check("staging: renamed build staged as nvngx_dlss.dll in a writable dir",
              os.path.isfile(os.path.join(staged, "nvngx_dlss.dll"))
              and os.path.getsize(os.path.join(staged, "nvngx_dlss.dll")) == 5
              and "sr_staged" in staged)
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    # ---- regression: pre-denoise frames must reach the dlls C-contiguous.
    # A planar movedim view (tiled upscale mirror) handed to the raw pointer
    # produced the rig's "9 gray tiles" output: planar RGB decoded as
    # interleaved (3 wrapped bands per plane, R then G then B). ----
    node_src = (REPO / "ants" / "dlssnr" / "node.py").read_text()
    core_src = (REPO / "ants" / "dlssnr" / "core.py").read_text()
    check("denoise: blend_frames contiguous-izes the processed view",
          "processed = processed.contiguous()" in node_src
          and "np.ascontiguousarray(processed)" in node_src)
    check("denoise: _pre_denoise_frame returns a contiguous tensor",
          ".to(device=frame.device, dtype=torch.float32).contiguous()" in node_src)
    check("denoise: CUDA legacy path does not empty_like a movedim view",
          "dest = torch.empty(tuple(frame.shape)" in node_src
          and "torch.empty_like(frame)" not in node_src)
    check("denoise: CUDA legacy path contiguousizes frame before data_ptr",
          "if not frame.is_contiguous():" in node_src)
    check("process_host normalizes a non-contiguous source before ctypes",
          "np.ascontiguousarray(source, dtype=np.float32)" in core_src)
    check("process_host rejects a non-contiguous destination loudly",
          "destination buffer must be a" in core_src)

    # ---- THE BUILD NAMING RULE (owner directive 2026-09-20) ----
    # The node loads the NEWEST build it can find, version read from the file
    # name; an explicit pick always wins. See ants/dlsssr/versions.py.
    from ants.dlsssr import versions
    check("naming: a date in the name is the version",
          versions.build_version("nvngx_dlssnr_2026-09-14.dll") == (2, 2026, 9, 14)
          and versions.build_version("nvngx_dlssnr_20260914.dll") == (2, 2026, 9, 14)
          and versions.build_version("nvngx_dlssnr_2026_09_14_renodx4000.dll")
          == (2, 2026, 9, 14))
    check("naming: NVIDIA-style dotted versions and bare build numbers work",
          versions.build_version("nvngx_dlss_310.9.1.dll") == (1, 310, 9, 1)
          and versions.build_version("nvngx_dlssnr_v10.0.dll") == (1, 10, 0)
          and versions.build_version("nvngx_dlssnr_2.dll") == (0, 2))
    check("naming: hardware/vendor tags are never mistaken for a version",
          versions.build_version(
              "nvngx_dlssnr_RenoDX_4000_series_friendly.dll") == (-1,)
          and versions.build_version("nvngx_dlssnr_4090.dll") == (-1,)
          and versions.build_version("nvngx_dlssnr.dll") == (-1,))
    check("naming: date > dotted version > bare number > no version",
          versions.build_version("x_2026-09-14.dll")
          > versions.build_version("x_310.9.1.dll")
          > versions.build_version("x_2.dll")
          > versions.build_version("x.dll"))
    check("naming: free text after the version does not change the order",
          versions.build_version("nvngx_dlssnr_2026-09-14_beta2.dll")
          == versions.build_version("nvngx_dlssnr_2026-09-14.dll"))
    check("naming: describe() names what was read out of the file name",
          versions.describe("nvngx_dlssnr_2026-09-14.dll") == "date 2026-09-14"
          and versions.describe("nvngx_dlss_310.9.1.dll") == "version 310.9.1"
          and "no version" in versions.describe("nvngx_dlssnr.dll"))

    # ---- read-only export reader + the CUDA-capable engine preference ----
    # Rig 2026-09-20 18:16: the node fell back to CPU staging on a 4090
    # ("engine lacks CUDA interop") - a 20-25x slowdown. The zero-copy path
    # needs an engine exporting dlss5nr_process_cuda_v6, so the pack now looks
    # at EXPORT TABLES (never loading anything) and prefers such a build.
    import tempfile as _tf
    from ants.dlssnr import peexports

    _pe_dir = _tf.mkdtemp()
    _plain = os.path.join(_pe_dir, "plain_engine.dll")
    _cuda = os.path.join(_pe_dir, "cuda_engine.dll")
    synth_pe(_plain, ["dlss5nr_init", "dlss5nr_process_v6"])
    synth_pe(_cuda, ["dlss5nr_init", "dlss5nr_process_cuda_v6",
                     "dlss5nr_cuda_supported"])
    check("peexports: export names are read from a PE without loading it",
          peexports.export_names(_cuda) == {"dlss5nr_init",
                                            "dlss5nr_process_cuda_v6",
                                            "dlss5nr_cuda_supported"}
          and peexports.export_names(_plain) == {"dlss5nr_init",
                                                 "dlss5nr_process_v6"})
    check("peexports: the CUDA entry-point pair is detected",
          peexports.has_exports(_cuda, peexports.CUDA_ENTRYPOINTS)
          and not peexports.has_exports(_plain, peexports.CUDA_ENTRYPOINTS))
    hostile = os.path.join(_pe_dir, "hostile.dll")
    with open(hostile, "wb") as handle:
        handle.write(b"MZ" + os.urandom(64))          # truncated header
    with open(os.path.join(_pe_dir, "notpe.dll"), "wb") as handle:
        handle.write(b"^^ not an image ^^")
    check("peexports: a hostile or truncated image yields NO names instead of "
          "faulting (run 28's lesson: an AV in a raw deref is fatal)",
          peexports.export_names(hostile) == set()
          and peexports.export_names(os.path.join(_pe_dir, "notpe.dll")) == set()
          and peexports.export_names(os.path.join(_pe_dir, "missing.dll")) == set())

    from ants.dlssnr.core import DLSSStandaloneManager
    _two = _tf.mkdtemp()
    synth_pe(os.path.join(_two, "a_neuroframe_engine.dll"),
             ["dlss5nr_init", "dlss5nr_process_v6"])          # plain, sorts first
    synth_pe(os.path.join(_two, "z_neuroframe_engine.dll"),
             ["dlss5nr_init", "dlss5nr_process_cuda_v6",
              "dlss5nr_cuda_supported"])                       # CUDA-capable
    check("core: find_engine_dll prefers the CUDA-capable engine over the "
          "alphabetically-first one (that is the 20-25x path)",
          DLSSStandaloneManager.find_engine_dll(_two)
          .endswith("z_neuroframe_engine.dll"))

    saved = (discovery.DLSS_ROOT, discovery.PACKAGE_DLL_DIR)
    try:
        _stash_root = _tf.mkdtemp()
        discovery.DLSS_ROOT = _stash_root
        discovery.PACKAGE_DLL_DIR = os.path.join(_stash_root, "_nopkg")
        for folder, engine, exports in (
                ("Merserk_DLLS", "neuroframe_engine.dll",
                 ["dlss5nr_init", "dlss5nr_process_v6"]),          # plain
                ("HELPERS", "neuroframe_engine.dll",
                 ["dlss5nr_init", "dlss5nr_process_cuda_v6",
                  "dlss5nr_cuda_supported"])):                    # CUDA-capable
            os.makedirs(os.path.join(_stash_root, folder))
            synth_pe(os.path.join(_stash_root, folder, engine), exports)
            open(os.path.join(_stash_root, folder, "neuroframe_caller.dll"),
                 "wb").write(b"caller")
        chosen, why = discovery.choose_helper_stash()
        check("staging: the CUDA-capable helper stash wins even when it is the "
              "lower-priority folder (the rig regression: a plain engine in "
              "Merserk_DLLS must not shadow a capable one elsewhere)",
              chosen.endswith("HELPERS") and "CUDA" in why)
        # ... and when NO stash is capable, the normal order is kept, loudly
        os.remove(os.path.join(_stash_root, "HELPERS", "neuroframe_engine.dll"))
        chosen, why = discovery.choose_helper_stash()
        check("staging: with no capable engine anywhere the normal order wins "
              "and the reason says why the CPU path is used",
              chosen.endswith("Merserk_DLLS") and "no CUDA" in why)
    finally:
        discovery.DLSS_ROOT, discovery.PACKAGE_DLL_DIR = saved

    # ---- provenance is INFORMATIONAL, never a gate (owner, 2026-09-20) ----
    # The official DLSS 5 NR runtime targets RTX 50-series; on RTX 30/40 the
    # community RenoDX-derived builds are the ONLY working option, so `auto`
    # must treat them as normal. History (runs 14-19 killed the process, run
    # 30 threw a catchable exception) lives in the module header, not in the
    # selection path.
    check("provenance: a RenoDX-derived name is recognised (and credited)",
          discovery.is_community_build(
              r"C:\m\DLSS\NR\nvngx_dlssnr_RenoDX_4000_series_friendly.dll")
          and not discovery.is_community_build(
              r"C:\m\DLSS\NR\nvngx_dlssnr_2026-09-14.dll")
          and discovery.COMMUNITY_BUILD_MARKERS == ("renodx",))
    check("provenance: the note names the RTX reality and the authors",
          "clshortfuse" in discovery.provenance_note(
              r"C:\m\DLSS\NR\x_renodx.dll")
          and "Merserk" in discovery.provenance_note(
              r"C:\m\DLSS\NR\x_renodx.dll")
          and "RTX 30/40" in discovery.provenance_note(
              r"C:\m\DLSS\NR\x_renodx.dll")
          and discovery.provenance_note(r"C:\m\DLSS\NR\plain.dll") == "")

    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    try:
        with tempfile.TemporaryDirectory() as td:
            discovery.DLSS_ROOT = td
            discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "_nolegacy")
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "_nopkg")
            nr_dir = os.path.join(td, "NR")
            os.makedirs(nr_dir)
            # a community build with the NEWEST version in its name: auto must
            # take it - no name is treated as second-class
            open(os.path.join(nr_dir, "nvngx_dlssnr_2026-01-01.dll"),
                 "wb").write(b"older-build")
            open(os.path.join(nr_dir, "nvngx_dlssnr_2026-09-14_renodx4000.dll"),
                 "wb").write(b"newest-community-build")
            check("auto: the newest build wins even when it is a community "
                  "RenoDX build (no name-based ranking)",
                  discovery.resolve_nr_runtime_path("auto")
                  .endswith("nvngx_dlssnr_2026-09-14_renodx4000.dll"))
            check("auto: an explicit pick resolves exactly",
                  discovery.resolve_nr_runtime_path(
                      "nvngx_dlssnr_2026-01-01.dll")
                  .endswith("nvngx_dlssnr_2026-01-01.dll"))
            check("auto: no skip_known_bad / risk parameter is left in the API",
                  "skip_known_bad" not in
                  (REPO / "ants" / "dlssnr" / "discovery.py").read_text())
            # a set FOLDER resolves to its newest build, not to its first file
            set_dir = os.path.join(nr_dir, "2026-05")
            os.makedirs(set_dir)
            open(os.path.join(set_dir, "nvngx_dlssnr_2026-01-01.dll"),
                 "wb").write(b"set-old")
            open(os.path.join(set_dir, "nvngx_dlssnr_2026-05-05_renodx.dll"),
                 "wb").write(b"set-new")
            from ants.dlsssr.discovery import find_nr_runtime_dll
            check("naming: a set folder resolves to its NEWEST runtime",
                  find_nr_runtime_dll(set_dir)
                  .endswith("nvngx_dlssnr_2026-05-05_renodx.dll"))
            check("naming: an explicit set pick resolves to its newest runtime",
                  discovery.resolve_nr_runtime_path("2026-05")
                  .endswith("nvngx_dlssnr_2026-05-05_renodx.dll"))
            check("naming: newest_first ranks by version, then file date",
                  versions.newest_first(["nvngx_dlssnr_2026-01-01.dll",
                                         "nvngx_dlssnr_310.9.1.dll",
                                         "nvngx_dlssnr.dll"])
                  == ["nvngx_dlssnr_2026-01-01.dll", "nvngx_dlssnr_310.9.1.dll",
                      "nvngx_dlssnr.dll"])
            check("naming: the widget says the rule out loud (newest first, "
                  "auto loads the newest, explicit pick wins)",
                  "NEWEST FIRST" in (REPO / "ants" / "dlssnr" / "node.py").read_text()
                  and "naming rule" in (REPO / "ants" / "dlssnr" / "node.py").read_text()
                  and "docs/MODELS_DLSS_LAYOUT.md" in (REPO / "ants" / "dlssnr" / "node.py").read_text())
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved
    check("node: the legacy engine's LUID failure is explained (wedged device "
          "after a removal -> restart; on a fresh process the multi-GPU CUDA "
          "bug + the launch flags; otherwise the helper pair, plus the "
          "collector inventory as the next step)",
          "by LUID" in node_src and "restart ComfyUI" in node_src
          and "HELPER / ENGINE INVENTORY" in node_src
          and "--cuda-device" in node_src
          and "--disable-pinned-memory" in node_src
          and "15255" in node_src
          and "D3D12 host adapter" in node_src)
    check("node: a removed device drops the GPU context AND the session (the "
          "next frame rebuilds both), instead of cascading on a dead device",
          "REMOVED" in node_src and "_close_native()" in node_src
          and "fresh device" in node_src)
    check("native: the engine prints the provenance note as STATUS, not a "
          "scary warning, and never refuses the community builds",
          "provenance_note" in node_src
          and "logger.status" in node_src
          and "force-terminator" not in node_src
          and "ANTS_ALLOW_KNOWN_BAD_NR" not in node_src)
    check("discovery: the provenance header keeps the credit and the history "
          "(RenoDX/clshortfuse, Merserk, runs 14-19 vs run 30)",
          all(token in (REPO / "ants" / "dlssnr" / "discovery.py").read_text()
              for token in ("clshortfuse", "Merserk", "runs 14-19",
                            "Run 30", "RTX 30/40")))

    # ---- duplicate detection is by CONTENT, and never changes selection ----
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    try:
        with tempfile.TemporaryDirectory() as td:
            discovery.DLSS_ROOT = td
            discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "_nolegacy")
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "_nopkg")
            nr_dir = os.path.join(td, "NR")
            os.makedirs(nr_dir)
            body = b"one-build-two-names" * 4096      # the owner's own setup
            open(os.path.join(nr_dir, "nvngx_dlssnr.dll"), "wb").write(body)
            open(os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_4000_series_friendly.dll"),
                 "wb").write(body)
            check("duplicates: same_bytes() resolves identical files",
                  discovery.same_bytes(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"),
                      os.path.join(nr_dir,
                                   "nvngx_dlssnr_RenoDX_4000_series_friendly.dll"))
                  and not discovery.same_bytes(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"),
                      os.path.join(nr_dir, "missing.dll")))
            check("duplicates: the twin is found by content, whatever the names",
                  discovery.identical_sibling(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"))
                  == "nvngx_dlssnr_RenoDX_4000_series_friendly.dll")
            versioned = os.path.join(nr_dir, "nvngx_dlssnr_2025-01-01.dll")
            open(versioned, "wb").write(b"a-build-with-a-version")
            check("duplicates: selection follows the naming rule alone (the "
                  "versioned build wins, no name-based ranking)",
                  discovery.resolve_nr_runtime_path("auto").endswith(
                      "nvngx_dlssnr_2025-01-01.dll"))
            os.remove(versioned)
            picked = discovery.resolve_nr_runtime_path("auto")
            check("duplicates: with only the two identical names left, auto "
                  "picks one (never refuses, never warns about risk)",
                  os.path.basename(picked) in (
                      "nvngx_dlssnr.dll",
                      "nvngx_dlssnr_RenoDX_4000_series_friendly.dll"))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
