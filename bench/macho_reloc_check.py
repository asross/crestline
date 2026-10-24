#!/usr/bin/env python3
"""Checks the macOS linker's work on crest: every PAGEOFF12 relocation with
an ADDEND in the (LTO) object must be encoded as (target + addend) & 0xfff
in the linked arm64 instruction. ld-prime of the Xcode 15.0 Command Line
Tools (ld 1015.7) dropped such an addend (docs/performance.md,
mip_deterministic), which crashed crest or could make it compute wrongly.

  RUSTFLAGS="-C save-temps" CARGO_TARGET_DIR=target-st cargo build --release --bin crest
  bench/macho_reloc_check.py target-st/release/deps/crest-*-cgu.0.rcgu.o target-st/release/crest

Prints the mismatches and a summary; exit status 1 if any."""
import re, struct, subprocess, sys, bisect
obj, binf = sys.argv[1:3]
def syms(f):
    out = {}
    for l in subprocess.run(['nm', '-n', f], capture_output=True, text=True).stdout.splitlines():
        p = l.split()
        if len(p) == 3:
            out.setdefault(p[2], int(p[0], 16))
    return out
so, sb = syms(obj), syms(binf)
# object text symbols sorted (all, to bound each function), mapped by those
# also in the binary
allo = sorted(set(so.values()))
otext = sorted((a, n) for n, a in so.items() if n in sb)
starts = [a for a, _ in otext]
# relocations of __text
rel = subprocess.run(['objdump', '-r', obj], capture_output=True, text=True).stdout
sec = None; addend = {}; sites = []
for l in rel.splitlines():
    if l.startswith('RELOCATION RECORDS FOR'):
        sec = l
        continue
    if '[__text]' not in (sec or ''):
        continue
    m = re.match(r'([0-9a-f]+)\s+(ARM64_RELOC_\w+)\s+(\S+)', l.strip())
    if not m:
        continue
    off, ty, val = int(m.group(1), 16), m.group(2), m.group(3)
    if ty == 'ARM64_RELOC_ADDEND':
        addend[off] = int(val, 16)
    elif ty == 'ARM64_RELOC_PAGEOFF12' and off in addend:
        sites.append((off, val, addend[off]))
# binary text mapping
lc = subprocess.run(['otool', '-l', binf], capture_output=True, text=True).stdout
m = re.search(r'sectname __text\n\s+segname __TEXT\n\s+addr (0x[0-9a-f]+)\n\s+size (0x[0-9a-f]+)\n\s+offset (\d+)', lc)
taddr, tsize, toff = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3))
data = open(binf, 'rb').read()
bad = ok = skip = 0
for off, target, add in sites:
    k = bisect.bisect_right(starts, off) - 1
    if k < 0 or target not in sb:
        skip += 1; continue
    fa, fn = otext[k]
    # the next object symbol after fa must lie beyond off (same function)
    j = bisect.bisect_right(allo, fa)
    if j < len(allo) and allo[j] <= off:
        skip += 1; continue
    va = sb[fn] + (off - fa)
    if not (taddr <= va < taddr + tsize):
        skip += 1; continue
    ins = struct.unpack('<I', data[toff + va - taddr: toff + va - taddr + 4])[0]
    lo = (sb[target] + add) & 0xfff
    if (ins & 0x7f800000) == 0x11000000:  # ADD/SUB immediate
        imm, want = (ins >> 10) & 0xfff, lo
    elif (ins & 0x3b000000) == 0x39000000:  # LDR/STR unsigned offset
        size = ins >> 30
        if (ins >> 26) & 1 and (ins >> 23) & 1:  # 128-bit SIMD
            size = 4
        imm, want = (ins >> 10) & 0xfff, lo >> size
    else:
        skip += 1; continue
    if imm != want:
        bad += 1
        print(f'MISMATCH {fn} +{off - fa:#x} (bin {va:#x}): {ins:08x} imm {imm} want {want} ({target}+{add:#x})')
    else:
        ok += 1
print(f'ok {ok} bad {bad} skipped {skip} of {len(sites)}')
sys.exit(1 if bad else 0)
