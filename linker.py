#!/usr/bin/env python3
"""linker.py — Static linker simulator.

Resolves symbols across object files, performs relocation, merges
sections (.text, .data, .bss), builds a symbol table, and detects
undefined/duplicate symbol errors. Like a minimal ld.

One file. Zero deps. Does one thing well.
"""

import sys
from dataclasses import dataclass, field


@dataclass
class Symbol:
    name: str
    section: str  # '.text', '.data', '.bss', 'UNDEF', 'ABS'
    offset: int = 0
    size: int = 0
    is_global: bool = True
    defined: bool = True
    value: int = 0  # resolved address


@dataclass
class Relocation:
    offset: int         # where in section to patch
    symbol: str         # symbol name to resolve
    type: str = 'ABS32' # ABS32, REL32, ABS64
    addend: int = 0


@dataclass
class Section:
    name: str
    data: bytearray = field(default_factory=bytearray)
    align: int = 4
    relocations: list[Relocation] = field(default_factory=list)


@dataclass
class ObjectFile:
    name: str
    sections: dict[str, Section] = field(default_factory=dict)
    symbols: list[Symbol] = field(default_factory=list)

    def add_section(self, name: str, data: bytes = b'', align: int = 4) -> Section:
        sec = Section(name, bytearray(data), align)
        self.sections[name] = sec
        return sec

    def add_symbol(self, name: str, section: str, offset: int = 0,
                   is_global: bool = True, size: int = 0) -> Symbol:
        sym = Symbol(name, section, offset, size, is_global, section != 'UNDEF')
        self.symbols.append(sym)
        return sym

    def add_reloc(self, section: str, offset: int, symbol: str,
                  rtype: str = 'ABS32', addend: int = 0):
        self.sections[section].relocations.append(
            Relocation(offset, symbol, rtype, addend))


@dataclass
class LinkedOutput:
    sections: dict[str, bytearray]
    symbols: dict[str, int]  # name → address
    entry: int = 0
    base: int = 0x400000


class Linker:
    """Static linker: merge sections, resolve symbols, apply relocations."""

    def __init__(self, base_addr: int = 0x400000):
        self.base = base_addr
        self.objects: list[ObjectFile] = []

    def add(self, obj: ObjectFile):
        self.objects.append(obj)

    def link(self, entry: str = 'main') -> LinkedOutput:
        # Phase 1: Merge sections
        merged: dict[str, bytearray] = {}
        section_bases: dict[str, int] = {}
        # Track where each object's section lands in merged output
        obj_section_offsets: list[dict[str, int]] = []

        current_addr = self.base
        for sec_name in ['.text', '.data', '.bss']:
            section_bases[sec_name] = current_addr
            merged[sec_name] = bytearray()
            for obj in self.objects:
                if sec_name not in obj.sections:
                    continue
                sec = obj.sections[sec_name]
                # Align
                pad = (sec.align - len(merged[sec_name]) % sec.align) % sec.align
                merged[sec_name].extend(b'\x00' * pad)
            current_addr += max(len(merged.get(sec_name, b'')), 0)

        # Re-merge with offset tracking
        merged = {}
        obj_section_offsets = []
        current_addr = self.base
        for sec_name in ['.text', '.data', '.bss']:
            section_bases[sec_name] = current_addr
            merged[sec_name] = bytearray()
            for i, obj in enumerate(self.objects):
                if i >= len(obj_section_offsets):
                    obj_section_offsets.append({})
                if sec_name not in obj.sections:
                    continue
                sec = obj.sections[sec_name]
                pad = (sec.align - len(merged[sec_name]) % sec.align) % sec.align
                merged[sec_name].extend(b'\x00' * pad)
                obj_section_offsets[i][sec_name] = len(merged[sec_name])
                merged[sec_name].extend(sec.data)
            current_addr = section_bases[sec_name] + len(merged[sec_name])

        # Phase 2: Build global symbol table
        global_syms: dict[str, int] = {}
        for i, obj in enumerate(self.objects):
            for sym in obj.symbols:
                if not sym.is_global or not sym.defined:
                    continue
                addr = section_bases.get(sym.section, 0) + obj_section_offsets[i].get(sym.section, 0) + sym.offset
                if sym.name in global_syms:
                    raise LinkError(f"Duplicate symbol: {sym.name} (in {obj.name})")
                global_syms[sym.name] = addr

        # Phase 3: Check for undefined symbols
        for obj in self.objects:
            for sym in obj.symbols:
                if not sym.defined and sym.name not in global_syms:
                    raise LinkError(f"Undefined symbol: {sym.name} (referenced in {obj.name})")

        # Phase 4: Apply relocations
        for i, obj in enumerate(self.objects):
            for sec_name, sec in obj.sections.items():
                if sec_name not in merged:
                    continue
                base_offset = obj_section_offsets[i].get(sec_name, 0)
                for reloc in sec.relocations:
                    target_addr = global_syms.get(reloc.symbol)
                    if target_addr is None:
                        raise LinkError(f"Undefined relocation target: {reloc.symbol}")
                    patch_offset = base_offset + reloc.offset
                    if reloc.type == 'ABS32':
                        val = (target_addr + reloc.addend) & 0xFFFFFFFF
                        merged[sec_name][patch_offset:patch_offset+4] = val.to_bytes(4, 'little')
                    elif reloc.type == 'REL32':
                        pc = section_bases[sec_name] + patch_offset + 4
                        val = (target_addr + reloc.addend - pc) & 0xFFFFFFFF
                        merged[sec_name][patch_offset:patch_offset+4] = val.to_bytes(4, 'little')

        entry_addr = global_syms.get(entry, self.base)
        return LinkedOutput(merged, global_syms, entry_addr, self.base)


class LinkError(Exception):
    pass


def demo():
    print("=== Static Linker ===\n")

    # Object file 1: main.o
    main_o = ObjectFile("main.o")
    text = main_o.add_section('.text', bytes(20))
    main_o.add_symbol('main', '.text', 0)
    main_o.add_symbol('printf', 'UNDEF', is_global=True)
    main_o.add_reloc('.text', 8, 'printf', 'REL32')
    main_o.add_reloc('.text', 14, 'helper', 'REL32')

    # Object file 2: helper.o
    helper_o = ObjectFile("helper.o")
    text2 = helper_o.add_section('.text', bytes(16))
    data2 = helper_o.add_section('.data', b'Hello, World!\x00')
    helper_o.add_symbol('helper', '.text', 0)
    helper_o.add_symbol('greeting', '.data', 0)
    helper_o.add_symbol('printf', 'UNDEF', is_global=True)

    # "libc": printf.o
    libc = ObjectFile("printf.o")
    libc.add_section('.text', bytes(32))
    libc.add_symbol('printf', '.text', 0)

    linker = Linker(0x400000)
    linker.add(main_o)
    linker.add(helper_o)
    linker.add(libc)

    output = linker.link('main')

    print(f"Entry point: 0x{output.entry:x}")
    print(f"\nSymbols:")
    for name, addr in sorted(output.symbols.items(), key=lambda x: x[1]):
        print(f"  0x{addr:08x}  {name}")
    print(f"\nSections:")
    for name, data in output.sections.items():
        print(f"  {name:8s}  {len(data):5d} bytes  base=0x{output.base:x}")


if __name__ == '__main__':
    if '--test' in sys.argv:
        # Basic linking
        a = ObjectFile("a.o")
        a.add_section('.text', bytes(8))
        a.add_symbol('main', '.text', 0)
        a.add_symbol('foo', 'UNDEF')
        a.add_reloc('.text', 0, 'foo', 'ABS32')

        b = ObjectFile("b.o")
        b.add_section('.text', bytes(8))
        b.add_symbol('foo', '.text', 0)

        l = Linker(0x1000)
        l.add(a); l.add(b)
        out = l.link('main')
        assert 'main' in out.symbols
        assert 'foo' in out.symbols
        assert out.entry == out.symbols['main']
        # Relocation applied
        patched = int.from_bytes(out.sections['.text'][0:4], 'little')
        assert patched == out.symbols['foo']
        # Duplicate symbol
        c = ObjectFile("c.o")
        c.add_section('.text', bytes(4))
        c.add_symbol('foo', '.text', 0)
        l2 = Linker(); l2.add(b); l2.add(c)
        try: l2.link(); assert False
        except LinkError: pass
        # Undefined symbol
        d = ObjectFile("d.o")
        d.add_section('.text', bytes(4))
        d.add_symbol('missing', 'UNDEF')
        l3 = Linker(); l3.add(d)
        try: l3.link(); assert False
        except LinkError: pass
        print("All tests passed ✓")
    else:
        demo()
