"""Generates the caller-validation shim as an x64 PE DLL — in pure Python.

NVIDIA's NR runtime checks which module NGX calls RETURN into and rejects
callers it does not accept (the community-documented ``0xBAD00002`` result;
the check is satisfied by a thin helper module). This module emits a small
position-independent DLL whose exports are thunks: ``fwd_set_slots`` parks
the real NGX function addresses in .data slots, and ``fwd_create`` /
``fwd_evaluate`` / ``fwd_release`` forward to the parked target with a real
``call`` (never a tail ``jmp``), so the return address on the stack points
back into this image and the caller check passes.

The caller parks a (possibly different) target before every call: slot 0 is
re-pointed at the intended function, then any thunk may be invoked with that
call's signature. Register arguments (rcx/rdx/r8/r9) forward for free;
stack arguments are copied from the caller's frame into the inner call's
slots (copying more than the inner call reads is harmless — the extras land
in shadow space).

``fwd_init_ext`` additionally REORDERS the last two arguments: the snippet
builds of ``nvngx_dlssnr.dll`` take
``Init_Ext(appId, appDataPath, device, FeatureCommonInfo*, sdkVersion)`` — the
opposite of the public SDK/header order used by the driver core, and the
reason every working host of this runtime wraps its init call in a helper
(the community caller shims do the same reorder). Doing the swap IN the shim
keeps the return address inside this image, so the caller check still passes
when the real order is used.

Technique reference: HicirTech/DLSS-Video-Transcoder (no license; the shim
concept is credited there); the snippet Init_Ext argument order and the shim
FILE NAME are credited to the community caller shims (ComfyUI-DLSS5-NR /
DLSS5-Video, MIT). The bytes below are written from the public x64 instruction
encodings and the documented PE/COFF layout.

File name (``ANTS_NR_SHIM_NAME``): the ecosystem's working caller shim is
deliberately NOT called ``nvngx.dll`` - it ships as ``nvngx.dll_comfy.dll``,
and the bare ``nvngx.dll`` name only survives in those hosts as a legacy
fallback. Loading a module under the exact ``nvngx.dll`` name hijacks a real
NVIDIA module name inside the process (any later ``LoadLibrary("nvngx.dll")``
binds to this 5-export stub), so the default here keeps the ecosystem's
``nvngx.dll`` PREFIX with our own suffix. ``ANTS_NR_SHIM_NAME=nvngx.dll``
restores the historical geometry for A/B runs.
"""

import os
import struct

# Ecosystem-matching caller-shim module name (see the module docstring):
# same "nvngx.dll" prefix the working shims use, without taking the exact
# name of a real NVIDIA module.
DEFAULT_SHIM_NAME = "nvngx.dll_ants.dll"


def shim_name():
    """Shim file/module name (ANTS_NR_SHIM_NAME overrides it)."""
    return os.environ.get("ANTS_NR_SHIM_NAME") or DEFAULT_SHIM_NAME


IMAGE_DOS_SIGNATURE = 0x5A4D
IMAGE_NT_SIGNATURE = 0x00004550
SECTION_ALIGNMENT = 0x1000
FILE_ALIGNMENT = 0x200
DEFAULT_IMAGE_BASE = 0x180000000

FORWARDER_EXPORTS = ("fwd_create", "fwd_evaluate", "fwd_init_ext",
                     "fwd_release", "fwd_set_slots")


def _align(value, to):
    return -(-value // to) * to


class _TextBuilder:
    """Emits x64 machine code; resolves RIP-relative references to .data slots."""

    def __init__(self, text_rva, slot_rva):
        self.text_rva = text_rva
        self.slot_rva = slot_rva
        self.bytes = bytearray()

    @property
    def rva(self):
        return self.text_rva + len(self.bytes)

    def push(self, *values):
        for v in values:
            self.bytes.append(v & 0xFF)

    def _disp32(self, value):
        self.push(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF)

    def _rip_store(self, prefix, slot):
        """mov [rip+disp], <reg> — prefix carries the ModRM (e.g. 48 89 0D = rcx)."""
        self.push(*prefix)
        rip_after = self.text_rva + len(self.bytes) + 4
        self._disp32(self.slot_rva[slot] - rip_after)

    def emit_dll_main(self):
        """BOOL DllMain -> TRUE."""
        at = self.rva
        self.push(0xB8, 0x01, 0x00, 0x00, 0x00)  # mov eax, 1
        self.push(0xC3)                          # ret
        return at

    def emit_set_slots(self):
        """fwd_set_slots(rcx=create, rdx=evaluate, r8=release)."""
        at = self.rva
        self._rip_store((0x48, 0x89, 0x0D), 0)   # mov [rip+s0], rcx
        self._rip_store((0x48, 0x89, 0x15), 1)   # mov [rip+s1], rdx
        self._rip_store((0x4C, 0x89, 0x05), 2)   # mov [rip+s2], r8
        self.push(0xC3)                          # ret
        return at

    @staticmethod
    def frame_for(stack_args):
        frame = 0x20 + stack_args * 8 + 8
        if frame % 16 != 8:
            frame += 8
        return frame

    def _emit_prolog(self, frame, stack_args):
        self.push(0x48, 0x83, 0xEC, frame & 0xFF)             # sub rsp, frame
        for i in range(stack_args):
            # mov rax, [rsp + frame + 0x28 + i*8]   (caller's arg 5+i)
            self.push(0x48, 0x8B, 0x84, 0x24)
            self._disp32(frame + 0x28 + i * 8)
            # mov [rsp + 0x20 + i*8], rax           (inner stack slot i)
            self.push(0x48, 0x89, 0x84, 0x24)
            self._disp32(0x20 + i * 8)

    def _emit_epilog(self, frame):
        self.push(0x48, 0x83, 0xC4, frame & 0xFF)              # add rsp, frame
        self.push(0xC3)                                        # ret

    def _emit_indirect_call(self, slot):
        self.push(0xFF, 0x15)                                  # call [rip+disp]
        rip_after = self.text_rva + len(self.bytes) + 4
        self._disp32(self.slot_rva[slot] - rip_after)

    def emit_call_thunk(self, slot, stack_args=3):
        """Reserve shadow space, copy up to `stack_args` stack arguments, call
        the target parked in `slot` (return address stays in this image)."""
        at = self.rva
        frame = self.frame_for(stack_args)
        self._emit_prolog(frame, stack_args)
        self._emit_indirect_call(slot)
        self._emit_epilog(frame)
        return at

    def emit_init_ext_thunk(self, slot, stack_args=3):
        """fwd_init_ext: same thunk, but the last two arguments are swapped.

        Caller (public/header order)   : (... device, version, common_info)
        Snippet (nvngx_dlssnr build)   : (... device, common_info, version)

        `version` arrives in r9, `common_info` as the first stack argument.
        Save r9, load the stack argument into r9, and park the saved version
        into the inner call's first stack slot.
        """
        at = self.rva
        frame = self.frame_for(stack_args)
        self.push(0x4D, 0x89, 0xCB)                            # mov r11, r9
        self._emit_prolog(frame, stack_args)
        # mov rax, [rsp+frame+0x28]  (caller's arg 5 = common_info)
        self.push(0x48, 0x8B, 0x84, 0x24)
        self._disp32(frame + 0x28)
        self.push(0x4C, 0x8B, 0xC8)                            # mov r9, rax
        self.push(0x4C, 0x89, 0x5C, 0x24, 0x20)                # mov [rsp+0x20], r11
        self._emit_indirect_call(slot)
        self._emit_epilog(frame)
        return at

    def pad_to(self, multiple):
        while len(self.bytes) % multiple != 0:
            self.bytes.append(0)


def build_shim_dll(image_base=DEFAULT_IMAGE_BASE, dll_name="nvngx.dll"):
    """Emit the shim PE bytes. Returns (bytes, {export_name: rva})."""
    text_rva = 0x1000
    data_rva = 0x2000
    reloc_rva = 0x3000
    slot_rva = [data_rva, data_rva + 8, data_rva + 16]

    text = _TextBuilder(text_rva, slot_rva)
    dll_main_rva = text.emit_dll_main()
    thunks = []                                # (rva, size) for .pdata
    def emit_thunk(rva_holder, fn):
        rva_holder.append(fn())
        thunks.append((rva_holder[-1], text.rva - rva_holder[-1]))

    _create, _evaluate, _release, _init_ext = [], [], [], []
    emit_thunk(_create, lambda: text.emit_call_thunk(0, 3))
    emit_thunk(_evaluate, lambda: text.emit_call_thunk(1, 3))
    emit_thunk(_release, lambda: text.emit_call_thunk(2, 3))
    emit_thunk(_init_ext, lambda: text.emit_init_ext_thunk(0, 3))
    create_rva, evaluate_rva, release_rva, init_ext_rva = (
        _create[0], _evaluate[0], _release[0], _init_ext[0])
    set_slots_rva = text.emit_set_slots()
    # All thunks share one UNWIND_INFO: identical prologs ("sub rsp, 0x48"
    # = UWOP_ALLOC_SMALL). set_slots is a leaf and needs none.
    text.pad_to(16)

    eat_rva = {"fwd_create": create_rva, "fwd_evaluate": evaluate_rva,
               "fwd_release": release_rva, "fwd_init_ext": init_ext_rva,
               "fwd_set_slots": set_slots_rva}
    names = sorted(FORWARDER_EXPORTS)          # GetProcAddress binary-searches
    count = len(names)

    export_dir_rva = text_rva + len(text.bytes)
    eat_table_rva = export_dir_rva + 40
    name_ptr_rva = eat_table_rva + count * 4
    ordinals_rva = name_ptr_rva + count * 4
    strings_rva = ordinals_rva + count * 2

    strings = bytearray()
    dll_name_offset = len(strings)
    strings += dll_name.encode() + b"\x00"
    name_offsets = {}
    for name in names:
        name_offsets[name] = len(strings)
        strings += name.encode() + b"\x00"

    export_end_rva = strings_rva + len(strings)
    # Unwind metadata: the call thunks are non-leaf (they CALL the parked
    # NGX function), so exception unwinding THROUGH them needs UNWIND_INFO +
    # RUNTIME_FUNCTION entries - without them a C++ exception that escapes
    # the runtime cannot unwind our frame and the process dies with no
    # traceback (rig run 14 signature). Every thunk has the same prolog
    # ("sub rsp, 0x38/0x48" = UWOP_ALLOC_SMALL), so they share one record.
    unwind_rva = _align(export_end_rva, 4)
    pdata_rva = _align(unwind_rva + 6, 4)
    pdata_end_rva = pdata_rva + len(thunks) * 12
    text_virtual_size = pdata_end_rva - text_rva

    text_bytes = bytearray(text_virtual_size)
    text_bytes[:len(text.bytes)] = text.bytes

    def unwind_bytes(rva):
        # UNWIND_INFO (byte0 = Version | Flags<<3), one unwind code for the
        # 4-byte prolog, no frame pointer:
        #   UNWIND_CODE[0] = CodeOffset 4, (UWOP_ALLOC_SMALL << 4) | OpInfo
        # NOTE: the opcode lives in the HIGH nibble (UNWIND_CODE byte 2 =
        # UnwindOp:4 | OpInfo:4). An earlier revision shifted by 3, which
        # encoded UWOP_ALLOC_LARGE with a garbage extra slot - a malformed
        # record turns any exception crossing the thunk into the run-14
        # "silent death with no traceback" signature.
        frame = _TextBuilder.frame_for(3)
        info = frame // 8 - 1                        # UWOP_ALLOC_SMALL units
        struct.pack_into("<BBBB", text_bytes, rva - text_rva, 0x01, 4, 1, 0)
        struct.pack_into("<BB", text_bytes, rva - text_rva + 4, 4,
                         (2 << 4) | (info & 0x0F))

    unwind_bytes(unwind_rva)
    for i, (fn_rva, fn_size) in enumerate(thunks):
        base = pdata_rva + i * 12
        struct.pack_into("<III", text_bytes, base - text_rva,
                         fn_rva, fn_rva + max(fn_size, 8), unwind_rva)

    def w32(rva, value):
        struct.pack_into("<I", text_bytes, rva - text_rva, value & 0xFFFFFFFF)

    def w16(rva, value):
        struct.pack_into("<H", text_bytes, rva - text_rva, value & 0xFFFF)

    # IMAGE_EXPORT_DIRECTORY
    w32(export_dir_rva + 12, strings_rva + dll_name_offset)  # Name
    w32(export_dir_rva + 16, 1)                              # Base
    w32(export_dir_rva + 20, count)                          # NumberOfFunctions
    w32(export_dir_rva + 24, count)                          # NumberOfNames
    w32(export_dir_rva + 28, eat_table_rva)                  # AddressOfFunctions
    w32(export_dir_rva + 32, name_ptr_rva)                   # AddressOfNames
    w32(export_dir_rva + 36, ordinals_rva)                   # AddressOfNameOrdinals
    for i, name in enumerate(names):
        w32(eat_table_rva + i * 4, eat_rva[name])
        w32(name_ptr_rva + i * 4, strings_rva + name_offsets[name])
        w16(ordinals_rva + i * 2, i)
    text_bytes[strings_rva - text_rva:strings_rva - text_rva + len(strings)] = strings

    data_bytes = bytearray(24)                 # three zero-initialised slots
    reloc_bytes = bytearray(8)                 # header-only block for DYNAMIC_BASE
    struct.pack_into("<II", reloc_bytes, 0, text_rva, 8)

    header_size = _align(64 + 4 + 20 + 240 + 3 * 40, FILE_ALIGNMENT)
    text_raw = _align(len(text_bytes), FILE_ALIGNMENT)
    data_raw = _align(len(data_bytes), FILE_ALIGNMENT)
    reloc_raw = _align(len(reloc_bytes), FILE_ALIGNMENT)
    text_ptr = header_size
    data_ptr = text_ptr + text_raw
    reloc_ptr = data_ptr + data_raw
    size_of_image = _align(reloc_rva + len(reloc_bytes), SECTION_ALIGNMENT)

    file = bytearray(reloc_ptr + reloc_raw)
    u8 = lambda o, v: file.__setitem__(o, v & 0xFF)
    u16 = lambda o, v: struct.pack_into("<H", file, o, v & 0xFFFF)
    u32 = lambda o, v: struct.pack_into("<I", file, o, v & 0xFFFFFFFF)
    u64 = lambda o, v: struct.pack_into("<Q", file, o, v & 0xFFFFFFFFFFFFFFFF)

    # DOS header
    u16(0, IMAGE_DOS_SIGNATURE)
    u32(0x3C, 0x40)                            # e_lfanew
    # PE signature + COFF
    pe = 0x40
    u32(pe, IMAGE_NT_SIGNATURE)
    coff = pe + 4
    u16(coff + 0, 0x8664)                      # Machine = AMD64
    u16(coff + 2, 3)                           # NumberOfSections
    u16(coff + 16, 240)                        # SizeOfOptionalHeader
    u16(coff + 18, 0x2022)                     # EXECUTABLE | LARGE_ADDRESS_AWARE | DLL
    # Optional header (PE32+)
    opt = coff + 20
    u16(opt + 0, 0x20B)
    u8(opt + 2, 14)                            # linker major
    u32(opt + 4, text_raw)                     # SizeOfCode
    u32(opt + 8, data_raw + reloc_raw)         # SizeOfInitializedData
    u32(opt + 16, dll_main_rva)                # AddressOfEntryPoint
    u32(opt + 20, text_rva)                    # BaseOfCode
    u64(opt + 24, image_base)
    u32(opt + 32, SECTION_ALIGNMENT)
    u32(opt + 36, FILE_ALIGNMENT)
    u16(opt + 40, 6)                           # MajorOSVersion
    u16(opt + 48, 6)                           # MajorSubsystemVersion
    u32(opt + 56, size_of_image)
    u32(opt + 60, header_size)
    u16(opt + 68, 3)                           # Subsystem CUI
    u16(opt + 70, 0x140)                       # DYNAMIC_BASE | NX_COMPAT
    u64(opt + 72, 0x100000)                    # StackReserve
    u64(opt + 80, 0x1000)                      # StackCommit
    u64(opt + 88, 0x100000)                    # HeapReserve
    u64(opt + 96, 0x1000)                      # HeapCommit
    u32(opt + 108, 16)                         # NumberOfRvaAndSizes
    dirs = opt + 112
    u32(dirs + 0, export_dir_rva)              # Export table
    u32(dirs + 4, export_end_rva - export_dir_rva)
    u32(dirs + 3 * 8, pdata_rva)               # Exception table (unwindable)
    u32(dirs + 3 * 8 + 4, len(thunks) * 12)
    u32(dirs + 5 * 8, reloc_rva)               # Base relocation table
    u32(dirs + 5 * 8 + 4, len(reloc_bytes))

    # Section headers
    section_base = opt + 240

    def section(index, name, vsize, vaddr, raw_size, raw_ptr, chars):
        base = section_base + index * 40
        for i in range(8):
            u8(base + i, ord(name[i]) if i < len(name) else 0)
        u32(base + 8, vsize)
        u32(base + 12, vaddr)
        u32(base + 16, raw_size)
        u32(base + 20, raw_ptr)
        u32(base + 36, chars)

    section(0, ".text", len(text_bytes), text_rva, text_raw, text_ptr, 0x60000020)
    section(1, ".data", len(data_bytes), data_rva, data_raw, data_ptr, 0xC0000040)
    section(2, ".reloc", len(reloc_bytes), reloc_rva, reloc_raw, reloc_ptr, 0x42000040)

    file[text_ptr:text_ptr + len(text_bytes)] = text_bytes
    file[data_ptr:data_ptr + len(data_bytes)] = data_bytes
    file[reloc_ptr:reloc_ptr + len(reloc_bytes)] = reloc_bytes

    exports = {name: eat_rva[name] for name in FORWARDER_EXPORTS}
    return bytes(file), exports


def write_shim(directory, dll_name=None):
    """Write the shim into `directory` (idempotent) and return its path.

    `dll_name` defaults to ``shim_name()`` (ANTS_NR_SHIM_NAME or the
    ecosystem-matching default) - the PE's export-directory name follows the
    file name so the loader's module name and the image agree.
    """
    if dll_name is None:
        dll_name = shim_name()
    import os
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, dll_name)
    payload, _ = build_shim_dll(dll_name=dll_name)
    marker = path + ".ants_shim"
    want = payload
    if not os.path.exists(path) or not os.path.exists(marker):
        with open(path, "wb") as f:
            f.write(want)
        with open(marker, "w") as f:
            f.write("ANTs pure-Python NGX host caller-check shim\n")
    elif open(path, "rb").read() != want:
        with open(path, "wb") as f:
            f.write(want)
    return path
