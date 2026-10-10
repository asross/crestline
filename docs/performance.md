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

Every case same path. After the dispatch pass below (`perf.py --reps 3`,
M1; the earlier 2026-10-10 figure, all 0.761, is within noise of this).

| Group | Geomean crest / C++ cycles |
|---|---|
| MIP | 0.846 |
| LP dual simplex | 0.730 |
| LP primal simplex | 0.916 |
| IPM (IPX) | 0.929 |
| PDLP | 0.950 |
| Read model (time_limit 0) | 0.383 |
| **All** | **0.764** |

Reading co-100.mps.gz and cygnet.mps.gz takes 0.33 and 0.36 of the C++'s
cycles (flate2/miniz_oxide inflate is no slower than zlib through zstr).

| Group | Case | C++ Gcycles | crest Gcycles | crest / C++ | Same path |
|---|---|---|---|---|---|
| MIP | air05 | 40.63 | 34.96 | 0.860 | yes |
| MIP | neos17 | 9.97 | 8.53 | 0.856 | yes |
| MIP | nu25-pr12 | 3.87 | 2.86 | 0.740 | yes |
| MIP | neos-911970 | 11.60 | 9.90 | 0.854 | yes |
| MIP | dispatch lambda_080458 | 15.99 | 13.71 | 0.857 | yes |
| MIP | dispatch 3c1b60d6 root | 28.76 | 26.46 | 0.920 | yes |
| LP dual simplex | 25fv47 | 0.39 | 0.31 | 0.788 | yes |
| LP primal simplex | 25fv47 | 0.59 | 0.57 | 0.969 | yes |
| LP dual simplex | 80bau3b | 0.31 | 0.22 | 0.718 | yes |
| LP primal simplex | 80bau3b | 1.38 | 1.14 | 0.825 | yes |
| LP dual simplex | greenbea | 1.40 | 1.05 | 0.752 | yes |
| LP primal simplex | greenbea | 4.90 | 4.74 | 0.967 | yes |
| LP dual simplex | perold | 0.16 | 0.13 | 0.805 | yes |
| LP primal simplex | perold | 0.25 | 0.24 | 0.966 | yes |
| LP dual simplex | stair | 0.05 | 0.05 | 0.827 | yes |
| LP primal simplex | stair | 0.05 | 0.04 | 0.862 | yes |
| LP dual simplex | air04 relaxation | 2.09 | 1.47 | 0.702 | yes |
| LP dual simplex | rail507 relaxation | 10.80 | 6.07 | 0.562 | yes |
| LP dual simplex | co-100 relaxation | 8.03 | 5.04 | 0.627 | yes |
| LP dual simplex | dispatch 3c1b60d6 relaxation | 26.48 | 22.27 | 0.841 | yes |
| IPM (IPX) | greenbea | 1.20 | 1.23 | 1.023 | yes |
| IPM (IPX) | 80bau3b | 0.76 | 0.72 | 0.947 | yes |
| IPM (IPX) | rail507 relaxation | 10.31 | 9.66 | 0.936 | yes |
| IPM (IPX) | co-100 relaxation | 15.78 | 13.07 | 0.828 | yes |
| IPM (IPX) | dispatch 3c1b60d6 relaxation | 5.40 | 4.99 | 0.923 | yes |
| PDLP | 25fv47 | 1.57 | 1.41 | 0.894 | yes |
| PDLP | greenbea | 5.44 | 5.41 | 0.995 | yes |
| PDLP | stair | 0.55 | 0.53 | 0.964 | yes |
| Read model (time_limit 0) | co-100.mps.gz | 4.26 | 1.40 | 0.328 | yes |
| Read model (time_limit 0) | neos-5052403-cygnet.mps.gz | 7.83 | 2.84 | 0.363 | yes |
| Read model (time_limit 0) | dispatch 3c1b60d6.mps | 0.45 | 0.19 | 0.416 | yes |
| Read model (time_limit 0) | dispatch 3c1b60d6.lp | 0.71 | 0.31 | 0.436 | yes |

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

## Dispatch MILPs: profile and pass of 2026-10-10

Profiled with macOS `sample` (1 ms, call trees) and a SIGPROF PC sampler,
single thread, `mip_rel_gap = 0.01`:

- **LP-heavy solves** (the root LP of `dm_full_pert_s1_ramp`, the
  3c1b60d6 root): the factor solves are about half of the time (dense
  FTRAN by U 15-21%, hyper-sparse solves 11-21%, BTRAN 8-13%, FTRAN by L
  5-7%), then `Dual::iterate`'s own work, PRICE by row and `re_index`.
  The factors are very sparse (on `hard_10-03`, L and U hold about 12k
  and 46k entries for 27k rows) while B^-1 is dense, so the solves are
  bound by loads, branch mispredictions (columns of 1-3 entries) and the
  pivot division, not by arithmetic.
- **LNS-heavy solves** (`hard_10-03_1340` to the end of its root, 53 s):
  80% is graph LNS, which solves about 6950 LPs of about 6 simplex
  iterations each. `Dual::iterate` is only 21% of the time: the rest of an
  LP solve is fixed cost, spread over many O(rows + columns) passes:
  rebuild (16.5%: recomputing duals and primals, factor accuracy check),
  `cleanup` (4.5%), scaling and unscaling the matrix (3.4%), the KKT check
  (3.2%), infeasibility and objective sums, bound and cost set-up (1-2.5%
  each). Domain propagation is 8% and cut separation 7.5%.

Landed (same paths; cycles, minimum of 3, crest after / before):

| Change | Effect |
|---|---|
| matrix scaling and unscaling by vector, without per-entry branches | `hard_10-03` root 0.991 (with the next) |
| KKT check after a MIP LP: columns and rows in separate loops | (above) |
| `vector_hash`: whole 8-byte pairs without a `memmove` call per pair | `hard_10-03` root 0.985 in all; other dispatch cases 0.99-1.00 |
| x86-64-v3 build (README) | 0.965 of the generic x86_64 build on native x86_64 (below) |

Tried and rejected on the dispatch models:
- Pivot divisions as multiplications when the pivot is a power of two
  (exact, so the same results): +1-3% cycles, the extra branch costs more
  than the division saves.
- Selects instead of branches on the basic/nonbasic flag in the
  infeasibility sums, on bound types in the cost perturbation, and
  bounds-check-free work bound set-up: no measurable change.
- (Path-changing) hyper-sparse/dense switch thresholds of the factor
  solves scaled by 0.5, 0.7 or 1.5: LP relaxation cycles over the 25
  dispatch LPs 1.010, 1.042, 0.979 (noise: the iteration counts move by
  as much).
- (Path-changing) IPX with crossover for the first root LP on one thread
  (`mip_lp_solver = ipm`, or a sequential version of the IPX race): 2.7
  times faster LP on `dm_full_pert_s1_ramp`, but the MIP is slower (1.31
  geometric mean on the suite, seed 0): the quick LNS search budgets its LP
  iterations by the root LP's simplex iterations, which IPX does not have,
  and the crossover vertex suits the dives less.
- Concurrent FTRANs per dual iteration: not tried; on Lambda the second
  vCPU, when there is one, runs the LNS helper thread.

### `mip_root_lp_devex` (off by default)

Devex pricing instead of dual steepest edge for the first root LP of the
MIP (not sub-MIPs), when it has at least 20000 rows (the MIP's LPs get no
exact DSE weights above that size anyway) and IPX does not race it (one
thread). Later solves use DSE. Dense B^-1 makes the DSE FTRAN expensive:
the LP relaxations of the 25 dispatch models take 0.724 of the cycles
(0.32-0.52 on the ramp models, 1.37 on lambda_080458).

Evidence (16 seeds, one thread, `mip_rel_gap = 0.01`, 180 s; instructions,
geometric mean over seeds of on / off, every run of both solved):

| Instances | Effect |
|---|---|
| 12 dispatch models of at least 20000 rows that solve | 0.854 (lambda_080458 0.67, wind185 0.71, ramp models 0.73-0.95, 3c1b60d6 0.91; worst: randsoc_noramp 1.12) |
| 11 smaller dispatch models | unaffected (with Devex there: 0.97 overall, small_windlull 1.63) |
| `hard_10-03_1340`, `diverse_10-01_0052` (time limit) | mean gap 1.592% -> 1.632% and 1.198% -> 1.231% (mean incumbent 0.04% and 0.03% worse; seed spread is 0.2%) |
| MIPLIB with >= 20000 rows, 2 seeds, 300 s | ex9 even; app1-2, sorrell3, supportcase10 even; rd-rplusc-21 finds no solution in 300 s on either seed (off: 99.94% gap on both; its root LP is 700 iterations, so the change is B&B chaos) |

The suite of solved dispatch models gets clearly faster, but the
time-limited ticks, where the incumbent at 180 s matters, do not, and one
MIPLIB instance lost its incumbent: so the option stays off.

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
- **Native x86_64** (2026-10-10, GitHub Actions `ubuntu-24.04` runner,
  `.github/workflows/bench-x86.yml`: CPU time, minimum of 3; crest with
  `libstdcxx no_fma` against the oracle's gcc build of the C++ app, every
  case same path): MIP 0.779, LP dual simplex 0.812, LP primal simplex
  0.942, IPX 0.942, PDLP 0.676, read 0.330, all 0.776. Built for
  x86-64-v3 (AVX2, as on Lambda) against the generic x86_64 build: MIP
  0.957, LP dual simplex 0.952, LP primal 0.961, IPX 0.975, all 0.965,
  every case same path (Rust never fuses multiply-adds, so the target
  changes no result; the CI oracle checks the v3 build too).
- **CI** checks crest against a gcc build of the C++ app on Linux x86_64
  on every push (see the README).
