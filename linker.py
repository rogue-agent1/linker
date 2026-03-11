#!/usr/bin/env python3
"""Linker — symbol resolution, relocation, and section merging.

One file. Zero deps. Does one thing well.

Simulates the core linking process: parse object files, resolve symbols,
merge sections, apply relocations. How ld/lld turn .o files into executables.
"""
import sys
from collections import defaultdict

class Symbol:
    __slots__ = ('name', 'section', 'offset', 'size', 'binding', 'defined')
    def __init__(self, name, section="", offset=0, size=0, binding="global", defined=True):
        self.name = name
        self.section = section
        self.offset = offset
        self.size = size
        self.binding = binding  # global, local, weak
        self.defined = defined
    def __repr__(self):
        d = "DEF" if self.defined else "UND"
        return f"{self.name}({d}, {self.section}+0x{self.offset:x})"

class Relocation:
    __slots__ = ('offset', 'symbol', 'type', 'addend')
    def __init__(self, offset, symbol, rtype="R_X86_64_PC32", addend=0):
        self.offset = offset
        self.symbol = symbol
        self.type = rtype
        self.addend = addend

class Section:
    __slots__ = ('name', 'data', 'align', 'flags')
    def __init__(self, name, data=b'', align=1, flags=""):
        self.name = name
        self.data = data
        self.align = align
        self.flags = flags

class ObjectFile:
    def __init__(self, name):
        self.name = name
        self.sections = {}
        self.symbols = []
        self.relocations = defaultdict(list)  # section -> [Relocation]

    def add_section(self, name, data, align=1, flags="AX"):
        self.sections[name] = Section(name, data, align, flags)

    def add_symbol(self, name, section="", offset=0, binding="global", defined=True):
        sym = Symbol(name, section, offset, binding=binding, defined=defined)
        self.symbols.append(sym)
        return sym

    def add_relocation(self, section, offset, symbol, rtype="R_X86_64_PC32"):
        self.relocations[section].append(Relocation(offset, symbol, rtype))

class Linker:
    def __init__(self):
        self.objects = []
        self.global_symbols = {}  # name -> (object, Symbol)
        self.merged_sections = defaultdict(bytearray)
        self.section_offsets = {}  # (obj_name, section) -> offset in merged
        self.output_symbols = {}
        self.errors = []

    def add_object(self, obj):
        self.objects.append(obj)

    def resolve_symbols(self):
        """Phase 1: Build global symbol table."""
        undefined = set()
        for obj in self.objects:
            for sym in obj.symbols:
                if not sym.defined:
                    undefined.add(sym.name)
                    continue
                if sym.binding == "local":
                    continue
                if sym.name in self.global_symbols:
                    existing_obj, existing = self.global_symbols[sym.name]
                    if existing.binding == "weak" and sym.binding == "global":
                        self.global_symbols[sym.name] = (obj, sym)
                    elif sym.binding != "weak":
                        self.errors.append(f"duplicate symbol: {sym.name} (in {existing_obj.name} and {obj.name})")
                else:
                    self.global_symbols[sym.name] = (obj, sym)
        # Check for unresolved
        for name in undefined:
            if name not in self.global_symbols:
                self.errors.append(f"undefined symbol: {name}")
        return len(self.errors) == 0

    def merge_sections(self):
        """Phase 2: Merge sections from all objects."""
        for obj in self.objects:
            for sec_name, section in obj.sections.items():
                merged = self.merged_sections[sec_name]
                # Align
                padding = (section.align - len(merged) % section.align) % section.align
                merged.extend(b'\x00' * padding)
                self.section_offsets[(obj.name, sec_name)] = len(merged)
                merged.extend(section.data)

    def relocate(self):
        """Phase 3: Apply relocations."""
        relocations_applied = 0
        for obj in self.objects:
            for sec_name, relocs in obj.relocations.items():
                base = self.section_offsets.get((obj.name, sec_name), 0)
                for reloc in relocs:
                    if reloc.symbol not in self.global_symbols:
                        continue
                    target_obj, target_sym = self.global_symbols[reloc.symbol]
                    target_base = self.section_offsets.get((target_obj.name, target_sym.section), 0)
                    target_addr = target_base + target_sym.offset
                    patch_offset = base + reloc.offset
                    # Simplified: just store the resolved address
                    self.output_symbols[reloc.symbol] = target_addr
                    relocations_applied += 1
        return relocations_applied

    def link(self):
        print("Phase 1: Symbol resolution")
        ok = self.resolve_symbols()
        for e in self.errors:
            print(f"  ERROR: {e}")
        if not ok:
            return False
        print(f"  {len(self.global_symbols)} global symbols resolved ✓")

        print("Phase 2: Section merging")
        self.merge_sections()
        for name, data in self.merged_sections.items():
            print(f"  {name}: {len(data)} bytes")

        print("Phase 3: Relocation")
        n = self.relocate()
        print(f"  {n} relocations applied ✓")
        return True

def main():
    # Create object files
    main_o = ObjectFile("main.o")
    main_o.add_section(".text", b'\x55\x48\x89\xe5' + b'\xe8\x00\x00\x00\x00' + b'\xc3', align=16, flags="AX")
    main_o.add_symbol("main", ".text", 0)
    main_o.add_symbol("printf", defined=False)
    main_o.add_relocation(".text", 5, "printf")

    lib_o = ObjectFile("lib.o")
    lib_o.add_section(".text", b'\x55\x48\x89\xe5' + b'\x31\xc0\xc3', align=16, flags="AX")
    lib_o.add_section(".data", b'\x48\x65\x6c\x6c\x6f\x00', align=8, flags="WA")
    lib_o.add_symbol("printf", ".text", 0)
    lib_o.add_symbol("message", ".data", 0)

    utils_o = ObjectFile("utils.o")
    utils_o.add_section(".text", b'\x55\x48\x89\xe5\xc3', align=16)
    utils_o.add_symbol("helper", ".text", 0, binding="weak")

    print("=== Linker Simulation ===\n")
    linker = Linker()
    linker.add_object(main_o)
    linker.add_object(lib_o)
    linker.add_object(utils_o)
    
    ok = linker.link()
    print(f"\nLink {'succeeded' if ok else 'FAILED'}")
    print(f"\nSymbol table:")
    for name, (obj, sym) in sorted(linker.global_symbols.items()):
        addr = linker.output_symbols.get(name, linker.section_offsets.get((obj.name, sym.section), 0) + sym.offset)
        print(f"  0x{addr:08x} {sym.binding:6s} {name}")

if __name__ == "__main__":
    main()
