# Performance: Crestline against C++ HiGHS

Crestline makes the same floating-point operations in the same order as the
C++ HiGHS it was ported from, so on every case below both programs take the
same path: the same simplex iterations, nodes, objective and status. The
differences are in how fast each step runs.

## Method

`HIGHS_DIR=<HiGHS checkout> python3 bench/perf.py <C++ highs> <crest>
--reps 3 --miplib <dir of MIPLIB .mps.gz>`: single thread, each case run
alternately with both binaries, minimum CPU cycles of 3 runs
(`/usr/bin/time -l`) on an M1 MacBook (shared, so about ±2% is noise).
"Same path" compares iteration and node counts, objective and status.
C++: Release, clang (thin LTO). Crestline: `cargo build --release --bin
crest` (rustc 1.98, LTO). The cases are MIPLIB instances, Netlib LPs from
HiGHS's check/instances, LP relaxations of MIPLIB instances, and power
system dispatch MILPs.

## Current: crest against the C++ `highs` app (2026-10-10)

Every case same path.

| Group | Geomean crest / C++ cycles |
|---|---|
| MIP | 0.846 |
| LP dual simplex | 0.725 |
| LP primal simplex | 0.918 |
| IPM (IPX) | 0.924 |
| PDLP | 0.940 |
| Read model (time_limit 0) | 0.383 |
| **All** | **0.761** |

Reading co-100.mps.gz and cygnet.mps.gz takes 0.33 and 0.36 of the C++'s
cycles (flate2/miniz_oxide inflate is no slower than zlib through zstr).

Per case (2026-10-07, an earlier crest build that still linked part of
the C++; all 0.796 then, against 0.761 now):

| Group | Case | C++ Gcycles | crest Gcycles | crest / C++ | Same path |
|---|---|---|---|---|---|
| MIP | air05 | 73.20 | 68.02 | 0.929 | yes |
| MIP | neos17 | 17.43 | 15.34 | 0.880 | yes |
| MIP | nu25-pr12 | 7.95 | 5.93 | 0.746 | yes |
| MIP | neos-911970 | 20.67 | 18.86 | 0.912 | yes |
| MIP | dispatch lambda_080458 | 31.46 | 28.40 | 0.903 | yes |
| MIP | dispatch 3c1b60d6 root | 55.25 | 52.55 | 0.951 | yes |
| LP dual simplex | 25fv47 | 0.68 | 0.57 | 0.837 | yes |
| LP primal simplex | 25fv47 | 1.00 | 0.97 | 0.964 | yes |
| LP dual simplex | 80bau3b | 0.56 | 0.43 | 0.775 | yes |
| LP primal simplex | 80bau3b | 2.41 | 2.11 | 0.875 | yes |
| LP dual simplex | greenbea | 2.49 | 2.06 | 0.828 | yes |
| LP primal simplex | greenbea | 8.48 | 8.27 | 0.975 | yes |
| LP dual simplex | perold | 0.27 | 0.23 | 0.871 | yes |
| LP primal simplex | perold | 0.43 | 0.41 | 0.964 | yes |
| LP dual simplex | stair | 0.10 | 0.09 | 0.881 | yes |
| LP primal simplex | stair | 0.09 | 0.08 | 0.871 | yes |
| LP dual simplex | air04 relaxation | 3.22 | 2.57 | 0.797 | yes |
| LP dual simplex | rail507 relaxation | 16.85 | 11.99 | 0.711 | yes |
| LP dual simplex | co-100 relaxation | 17.27 | 12.16 | 0.704 | yes |
| LP dual simplex | dispatch 3c1b60d6 relaxation | 50.78 | 46.05 | 0.907 | yes |
| IPM (IPX) | greenbea | 1.93 | 2.05 | 1.066 | yes |
| IPM (IPX) | 80bau3b | 1.22 | 1.27 | 1.039 | yes |
| IPM (IPX) | rail507 relaxation | 16.75 | 15.65 | 0.934 | yes |
| IPM (IPX) | co-100 relaxation | 15.07 | 12.16 | 0.807 | yes |
| IPM (IPX) | dispatch 3c1b60d6 relaxation | 5.45 | 5.39 | 0.990 | yes |
| PDLP | 25fv47 | 2.56 | 2.44 | 0.953 | yes |
| PDLP | greenbea | 7.83 | 7.37 | 0.941 | yes |
| PDLP | stair | 0.83 | 0.78 | 0.939 | yes |
| Read model (time_limit 0) | co-100.mps.gz | 6.64 | 2.26 | 0.340 | yes |
| Read model (time_limit 0) | neos-5052403-cygnet.mps.gz | 7.98 | 2.50 | 0.313 | yes |
| Read model (time_limit 0) | dispatch 3c1b60d6.mps | 0.46 | 0.19 | 0.406 | yes |
| Read model (time_limit 0) | dispatch 3c1b60d6.lp | 0.68 | 0.32 | 0.464 | yes |

## Where the gains come from

All of these keep every result bit for bit.

- **Model readers.** A byte-slice tokenizer without per-token allocation
  reads MPS and LP files 2-3x faster. The relaxation cases of co-100 and
  rail507 include their read; their simplex share alone is about 0.95-1.0.
- **File writers.** Solution, basis, MPS and LP writers format each number
  once (`{:.*e}`, no locale or stream machinery) and write 64 KB blocks.
  Wall time of writeModel / writeSolution / writeBasis, best of 3, seconds
  C++ -> Crestline; solutions are of the LP relaxation:

  | model | .mps | .lp | raw | pretty | glpsol raw | glpsol pretty | basis |
  |---|---|---|---|---|---|---|---|
  | dispatch 080458 | 0.386 -> 0.220 | 0.506 -> 0.251 | 0.458 -> 0.095 | 0.464 -> 0.189 | 0.220 -> 0.061 | 0.514 -> 0.190 | 0.112 -> 0.050 |
  | blp-ar98 | 0.255 -> 0.181 | 0.327 -> 0.230 | 0.086 -> 0.015 | 0.081 -> 0.032 | 0.031 -> 0.013 | 0.107 -> 0.069 | 0.011 -> 0.004 |
  | 30n20b8 | 0.147 -> 0.078 | 0.202 -> 0.125 | 0.063 -> 0.007 | 0.097 -> 0.024 | 0.017 -> 0.006 | 0.131 -> 0.025 | 0.016 -> 0.004 |
  | 80bau3b | 0.029 -> 0.022 | 0.048 -> 0.018 | 0.050 -> 0.008 | 0.054 -> 0.013 | 0.042 -> 0.008 | 0.085 -> 0.016 | 0.007 -> 0.003 |

- **Simplex kernels.** A pass guided by a sampling profiler on the dispatch
  LPs, keeping the same floating-point operations in the same order
  (shares of profile samples of one run each):

  | Change | Effect |
  |---|---|
  | `re_index` by blocks of 4, unchecked writes | relaxation 0.986 cycles, -3.5% instructions |
  | `getValueScale` max in 4 lanes (NaN: serial) | `Dual::iterate` 2.35% -> 1.28% |
  | primal infeasibility by selects | `update_primal` 5.13% -> 4.10% |
  | CHUZR tests merit before infeasibility (one branch) | `choose_normal` 4.59% -> 3.75% |
  | CHUZC candidate test by a select | `choose_possible` 3.54% -> 2.48% |
  | dense PRICE result indexed by selects | `price_by_row_with_switch` 2.16% -> 1.25% |
  | per-thread pool for work vector buffers | hard dispatch tick: madvise 5.01% -> 1.92% |
  | `factorSolveError` keeps its work arrays | hard dispatch tick: madvise 1.67% -> 0.25% |

  Together: LP dual simplex 0.924, MIP 0.976, all cases 0.971 of the cycles
  before the pass.
- **Feasibility jump.** CSR in both directions; a variable's value, jump
  move and good-set slot in one struct, so updating a neighbour's move
  touches one cache line; no allocation per jump value; the scores of a
  constraint's old and new left-hand side computed once. Cycles of
  feasibility jump alone (with minus without, node limit 1, min of 3), C++
  -> Crestline: neos17 2.19G -> 1.42G, dispatch 080458 2.22G -> 1.70G,
  gen-ip002 0.38G -> 0.22G, gen-ip054 0.20G -> 0.13G, markshare2 0.26G ->
  0.19G.
- **Presolve.** Probing and enumeration without per-probe call overhead:
  presolve on dispatch 080458 takes 0.915 of the cycles it did before.
- **One LTO unit.** Everything from the model reader to the MIP search is
  one optimized Rust program, with no calls across a language boundary.

Tried and rejected (slower or no change): branchless factor solves (+7%
cycles; the zero/nonzero branch there is mostly predictable); software
prefetch in the dense triangular solves; caching the column end in the
hyper-sparse DFS; skipping zero terms of `update_dual`'s objective sum;
selects in the DSE weight update; a vectorized dense path for
`update_primal`; IPX maxvolume `find_largest` by vectorized passes.

## Other platforms

- **gcc / libstdc++.** With the `libstdcxx` and `no_fma` features, Crestline
  follows a C++ build made with gcc and `-ffp-contract=off` (libstdc++'s
  heap and partial_sort tie orders, std::tuple hashing, unordered_multimap
  insertion order and uniform_int_distribution differ from libc++'s).
  Against such a build on the M1 (2026-10-06, heavily loaded machine, one
  rep): all 0.824, MIP 0.868, every case same path.
- **x86_64.** Fused multiply-adds that clang makes on arm64 are mirrored
  only on aarch64, so x86_64 builds take the x86_64 C++ paths. Under Rosetta
  2 (translated code, indicative only): every case same path, all 0.775.
  On native x86_64 (AWS Lambda, gcc builds, a dispatch MILP suite, with the
  port then part Rust and part C++): 0.945
  of the C++'s time at one thread and 0.957 at two, 18 of 20 solves
  bit-identical at the time of that measurement (2026-10-06, before the
  later MIP ports).
- **CI** checks crest against a gcc build of the C++ app on Linux x86_64
  on every push (see the README).
