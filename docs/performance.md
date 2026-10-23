# Performance: Crestline against C++ HiGHS

Crestline makes the same floating-point operations in the same order as the
C++ HiGHS it was ported from, so on every case below both programs take the
same path: the same simplex iterations, nodes, objective and status. The
differences are in how fast each step runs.

Since 2026-10-17 that holds for crest built with `--features hyper_dfs`
and run with `mip_root_lp_devex_density = inf` (see the root LP section
below); the default build takes other paths.

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
  and the crossover vertex suits the dives less. With that budget
  decoupled, see `mip_root_lp_ipx` below: still slower overall.
- Concurrent FTRANs per dual iteration: not tried; on Lambda the second
  vCPU, when there is one, runs the LNS helper thread.

### `mip_root_lp_devex` (off by default; replaced 2026-10-17 by `mip_root_lp_devex_density`, below)

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

## Graph LNS LP re-solves: pass of 2026-10-10 (later)

Profile (macOS `sample`, `hard_10-03_1340` to the end of its root, single
thread, `mip_rel_gap = 0.01`): graph LNS solves 6951 LPs of about 8.5
dual simplex iterations each. Per solve, on average, the dual values were
computed 2.75 times, the primal values 1.86 times, the factor's solve
error 2.7 times, the primal and dual infeasibility sums 3.7 and 4.7 times
and the costs set up 3.5 times; the matrix was scaled and unscaled once.
Only 66 of the 12366 rebuilds refactorized.

Landed (same paths: quick_check, the 100-node MIP log comparison, every
perf.py case):

| Change | Effect |
|---|---|
| The scaled matrix of the last solve is kept and swapped in while the matrix (compared bit for bit) and the scale factors are unchanged; the scale factors are powers of two, and the unscaled round trip is checked once | matrix (un)scaling 3.7% -> 0 (a 1% compare remains) |
| The factor's solve error is not recomputed when nothing it depends on has changed (the finished check right after the rebuild that ends a solve) | 18695 -> 12305 solve-error checks |
| The rebuild's infeasibility sums, read only by the log, are skipped without output | 25843 -> 13477 primal, 32535 -> 20170 dual infeasibility passes |
| No second unperturbed cost set-up after initialiseForSolve's; no recomputation of the dual values when no cost perturbation was made (solve start, cleanup) | 24453 -> 17927 cost set-ups |
| Cost and bound set-up, `HVec::tight` and computeDual's sums over slices (vectorized) | |
| Fix: the run that reads a model passed the Highs object to a clock callback that takes the engine (undefined behaviour, exposed by a layout change) | |

Instructions retired, deterministic (crest after / before, the cases of
the earlier pass): `hard_10-03` root 0.902, `diverse_10-01` root 0.903,
3c1b60d6 0.971, 080458 0.997, `pert_s1_ramp` 0.989, `randsoc_ramp` 0.989,
`medium_pert_s1_ramp` 0.974, `full_prices_noramp` 0.914; geometric mean
0.954. Cycles (minimum of 3 alternating runs, on a heavily shared machine,
so indicative): 0.959, 0.893, 0.964, 0.997, 0.946, 0.949, 0.977, 0.912;
geometric mean 0.949. Profile shares on the hard tick's root, before ->
after: graph LNS 86% -> 83%, rebuild 17.9% -> 16.8%, cleanup 4.3% ->
4.2%, scaling 3.7% -> 1.8% (mostly the compare), solve-error checks 6.0%
-> 4.8%, the dual iterations themselves 16% -> 21%.

Tried and rejected:
- Keeping the recorded dual values across an iterate restored after an
  infeasible LNS LP: only 186 restores, no measurable change.
- KKT check after a MIP LP by selects: within noise (-0.3% of samples).

### `mip_graph_lns_cost_perturbation`

Off, graph LNS's LP solves run the dual simplex without cost
perturbation. First measured on a heavily oversubscribed machine with
wall-clock limits (16 seeds): 0.888 of the instructions on the solved
dispatch runs, but the time-limited ticks looked worse (mean gap at 180 s
1.695% -> 1.745% on `hard_10-03_1340`, 1.381% -> 1.481% on
`diverse_10-01_0052`), so it first stayed on. Measured again without the
load below: it is now off by default.

### `mip_root_lp_ipx` and `mip_root_lp_effort_from_basis` (off by default; 2026-10-10)

**How the heuristics depend on the root LP's simplex iterations.** The
first root LP's iteration count is stored as `firstrootlpiters` and is
also nearly all of `total_lp_iterations` when the quick graph LNS search
starts. From them:
- quick graph LNS: at most `min(3 T + 1000, max(T + 1000, 20000))` LP
  iterations, `T = total_lp_iterations` (so "one root LP's worth" on
  large models; the 20000 floor binds below `T` = 19000, i.e. on every
  dispatch model but the ramp ones and lambda_080458);
- deep graph LNS (root and tree rounds): `10 T + 5000`, where `T` now
  also holds the cut loop and the quick search;
- the flip search: `max(5000, firstrootlpiters)` per call; the LPs of
  randomized rounding / tryRoundedPoint: `max(10000, 2 firstrootlpiters)`;
  node LPs: `max(10000, 10 avgrootlpiters, 1.5 firstrootlpiters)`;
- HiGHS's own heuristics (`moreHeuristicsAllowed`, `heuristic_effort`
  0.05 of `total_lp_iterations` plus 10000 at the root, a ratio of the
  tree's LP iterations later): the root LP is a small part of these.

An LP solved by IPX counts no simplex iterations at all (crossover's
pushes are not counted), so with IPX at the root `T` is about 0: the
quick search gets 1000 LP iterations (on `dm_full_pert_s1_ramp`: 0
neighbourhoods instead of the 2 dives that reach 1%), the flip and
rounding LPs their floors. On two threads, when IPX wins the race, the
dual simplex's iterations up to the interruption are counted instead,
so the budget there depends on the race's timing.

**The budget.** `mip_root_lp_effort_from_basis` counts the first root LP
as `E` = twice the basic structural columns of its optimal basis, however
it was solved (`firstrootlpiters = E`, and the graph LNS budgets add
`E` less the counted iterations to `total_lp_iterations`). A dual simplex
from the slack basis must bring each of these columns in: it took 1.0-2.4
iterations per column on the 25 dispatch LPs (1.0-1.2 without ramp
limits, 1.4-2.4 with them and on the lambda ticks) and 1.0-1.4 on MIPLIB
(supportcase10: 12). The IPX crossover basis has as many basic columns
(within 3% on every dispatch LP), so the count does not depend on the
method. Rejected: rows (simplex iterations per row range from 0.006 on
sorrell3 to 1.1), time or work measures (not deterministic), crossover or
IPM iterations (no fixed relation to simplex work).
`mip_root_lp_ipx` solves the first root LP of the main MIP with IPX and
crossover alone (no race), when it has at least 10000 nonzeros.

**Root LP alone** (instructions to the end of the first root LP, presolve
included, deterministic): IPX / dual simplex is 0.39-0.58 on the lambda
ticks, 0.40-0.75 on the full ramp models, 0.53-0.68 on four small models,
but 2.2-2.8 on every noramp model of medium or full size, 1.8 on
`hard_10-03`, 1.0 on `diverse_10-01`; MIPLIB: supportcase10 0.11,
app1-2 2.1, sorrell3 1.35, rd-rplusc-21 1.12. IPX wins exactly where the
dual simplex takes many iterations per basic column (dense B^-1).

**MIP** (dispatch suite, `mip_rel_gap = 0.01`, 180 s, seeds 0-15 at one
thread, 0-7 at two; instructions, geometric mean of per-instance
geometric means over runs all three configs solve, against (a) default;
the M1 was heavily oversubscribed by other benchmarks (load 20-190), so
the wall-clock limit cut runs short: the solved counts and the gaps at the
limit are noisy, configurations were interleaved so the load fell on them
alike):

| | (b) + effort from basis | (c) + IPX root + effort from basis |
|---|---|---|
| one thread, 23 models | 1.004 | 1.184 |
| ... lambda_080458, wind185, 3c1b60d6 | 0.997, 0.997, 0.999 | 0.444, 0.822, 0.927 |
| ... full / medium ramp models | 1.000-1.087 | 0.704-0.937 |
| ... 14 noramp models | 0.995-1.012 | 1.087-2.315 (all worse) |
| one thread, runs solved (of 400) | 347 (a: 344) | 342 |
| one thread, `hard_10-03` / `diverse_10-01` mean gap | 2.09% / 1.48% (a: 1.99% / 1.86%) | 1.97% / 1.60% |
| two threads, 23 models (instructions of both threads) | 1.020 | 1.032 (wall 1.288: (a) races IPX on the second core) |
| two threads, runs solved (of 200) | 184 (a: 184) | 184 |
| two threads, `hard_10-03` / `diverse_10-01` mean gap | 1.60% / 1.26% (a: 1.62% / 1.22%) | 1.63% / 1.22% |

In a two-seed pilot, IPX at the root without the new budget was 1.72:
decoupling the budget recovers most of the collapse (1.72 -> 1.24 on
those seeds), and (b) shows the budget itself is neutral with a simplex
root. What remains is the root LP: IPX is 2-3 times dearer than the dual
simplex on the noramp models.

MIPLIB (ex9, app1-2, sorrell3, supportcase10, rd-rplusc-21; 2 seeds,
300 s, `mip_rel_gap = 0.01`, one thread): (b) changes nothing; with (c)
supportcase10 ends at 81% and 94% (a: 100%, incumbent 21 against 70 on
seed 0), the others end as (a) (ex9 optimal, sorrell3 317%, app1-2 and
rd-rplusc-21 without a solution in all three).

**Choosing the method adaptively** (one thread): no feature known before
the solve separates the two groups (nonzeros per row: 3.4 noramp, 3.6-3.9
for both winners and losers). A probe does better: after a quarter of the
rows' worth of dual simplex iterations, the basic structural columns per
iteration are 0.79-0.86 on the models where IPX wins and 0.86-0.98 where
it loses (0.76-0.83 on four small models, either way). A rule "IPX if
that ratio is below 0.85 and the LP has at least 20000 rows" would pick
4 of the 6 large IPX winners, about 0.97 of the suite's instructions, but the
threshold sits within 0.01 of misclassified models, was fitted on these
same 25 instances, and misses supportcase10 (0.997 basic columns per
iteration at 3000 iterations). Not implemented; on two threads the race
already makes this choice.

So both options stay off: IPX for the root LP wins only on the ramp and
lambda models, and loses more on the rest. The effort-from-basis budget
is the right one to pair with any future non-simplex root (or the race),
and is neutral otherwise.

## Graph LNS cost perturbation: off by default (2026-10-10, evening)

### Method: paired runs without machine load

- **Runners:** `.github/workflows/bench-dispatch.yml` (run by hand, or by
  pushing a branch `bench-dispatch/*` whose commit sets the defaults)
  runs `bench/dispatch.py` on GitHub's x86_64 (`ubuntu-24.04`, crest
  built for x86-64-v3; AMD EPYC 7763/9V45/9V74, Xeon 8573C) and arm64
  (`ubuntu-24.04-arm`, Neoverse N2) runners, one solve at a time per
  4-vCPU runner, seeds x instances sharded over 16 runners per
  architecture. Every (instance, seed) runs all configs back to back on the
  same runner, so the pairs share a CPU model. The instances come from the
  private repo asross/crestline-dispatch (deploy key); the public logs
  print only names, objectives, gaps and times.
- **Locally:** `CRESTLINE_INSTR_CLOCK=R` (macOS) makes the run timers count
  R * 1e9 retired instructions as a second (`proc_pid_rusage`), so a
  one-thread run stops at the same point however loaded the machine is
  (two runs of `hard_10-03_1340` to a 20 s limit gave the same log line for
  line). R = 5 here: 900G instructions for a 180 s limit.
  Chosen over process CPU time, which still varies with cache and
  memory contention and with P/E core placement, and over a work-unit
  counter, which would need effort estimates everywhere time is read.
- **Measures** (`bench/dispatch.py report`): per instance, differences
  B - A paired by seed with 95% t-intervals; time as the geometric mean
  of B/A over runs that both solve (interval in log space); gap at the
  limit; and the mean primal gap over the limit (Berthold's primal
  integral divided by the limit: the incumbent's gap to the best
  objective any run found, 100% before the first incumbent), which credits
  finding good solutions sooner.

### Results (one thread, `mip_rel_gap = 0.01`, 180 s)

Off against on, paired by seed: 16 seeds, the hard ticks 32 on the
runners (16 on the M1).

| | x86_64 runners | arm64 runners | M1, instruction clock |
|---|---|---|---|
| solved dispatch models (23), time, geometric mean of per-instance ratios | 0.920 | 0.896 | 0.888 (instructions, earlier 16-seed run) |
| instances whose interval lies below 1 / above 1 | 7 / 0 | 6 / 0 | |
| largest point estimate | small_pert_s1_noramp 1.046 [0.83, 1.32] | small_windlull 1.043 [0.93, 1.17] | |
| runs solved in 180 s (of 400) | 372 -> 371 | 369 -> 371 | |
| `hard_10-03_1340`: gap at 180 s (on: 1.47%, 1.54%, 1.60%) | -0.051 +- 0.046 | -0.072 +- 0.108 | +0.005 +- 0.105 |
| `hard_10-03_1340`: mean primal gap (%) | -0.146 +- 0.103 | -0.344 +- 0.437 | -0.375 +- 0.189 |
| `diverse_10-01_0052`: gap at 180 s (on: 1.16%, 1.15%, 1.22%) | -0.014 +- 0.042 | +0.010 +- 0.039 | -0.013 +- 0.058 |
| `diverse_10-01_0052`: mean primal gap (%) | -0.010 +- 0.083 | -0.030 +- 0.050 | -0.123 +- 0.120 |

The ramp models gain most (`dm_full_randsoc_ramp` 0.747 and 0.691,
`dm_medium_pert_s1_ramp` 0.761 and 0.670, `dm_full_pert_s2_ramp` 0.837
and 0.805), and their mean primal gap drops by 0.8-2.7 points (the
incumbent comes earlier).

Two threads (16 seeds, `hard_10-03` and `diverse_10-01`): gap at 180 s
+0.016 +- 0.040 and -0.047 +- 0.052 (x86_64), -0.021 +- 0.036 and
+0.012 +- 0.023 (arm64); mean primal gap on `hard_10-03` -0.117 +- 0.180
and -0.248 +- 0.120; `diverse_10-01` solved in 7 -> 13 and 13 -> 11 of 16
runs. The 23 solved models on two threads (16 seeds, every run solves):
0.921 (x86_64) and 0.932 (arm64), 7 and 8 intervals below 1; one above,
`dm_small_randsoc_noramp` 1.053 [1.023, 1.084] on x86_64 (a 0.35 s
solve; 0.967 on arm64; one in 92 intervals at 95% is what chance gives);
`dm_full_randsoc_ramp` 0.559 and 0.563.

So the earlier "worse at the time limit" was the machine's load: without
it the hard ticks are even or slightly better, and no solved model
regresses.

### Why (instrumented, M1, instruction clock, 4 seeds)

Per graph LNS LP solve, on against off:

| Model | dual simplex iterations | solves with a primal simplex clean-up | clean-up iterations per solve | iterations with a zero dual step |
|---|---|---|---|---|
| `dm_full_pert_s1_ramp` | 78.8 -> 56.4 | 35% -> 0 | 19.4 -> 0 | 20% -> 50% |
| `dm_medium_pert_s1_ramp` | 40.0 -> 27.3 | 29% -> 9% (cost shifting) | 8.0 -> 0.4 | 13% -> 34% |
| 3c1b60d6, small_windlull | 22.5 -> 21.9, 6.0 -> 4.7 | 0 | 0 | 0 |
| `hard_10-03_1340` | 5.3 -> 6.0 | 0 | 0 | 0 |
| `diverse_10-01_0052` | 9.9 -> 10.0 | 0 | 0 | 0 |

- **The saving** is on the ramp models: removing the perturbation at the
  end leaves dual infeasibilities in a third of the LNS LPs, which the
  primal simplex then cleans up (a quarter of graph LNS's iterations), and
  the perturbed solves themselves take more dual iterations. Without
  perturbation half the iterations are dual degenerate, but there are
  fewer of them.
- **The hard ticks:** (a) no degenerate stalls: zero-step iterations stay
  under 1%, and the longest LNS LP is about as long either way (1946 and
  1991 iterations). (b) no loss of diversity: neighbourhoods improving the
  incumbent 11/31 -> 15/39 per run on `hard_10-03`, 14/50 -> 14/44 on
  `diverse_10-01`; graph LNS incumbents 45.5 -> 46.0 and 70.5 -> 57.8
  (the final gaps even). The LPs cost the same, the paths diverge: (c)
  noise, which the earlier wall-clock measurement on a loaded machine
  mistook for a loss.

Tried (as options, runners, 16 seeds) and rejected:
- A smaller perturbation (multiplier 0.05 in graph LNS): 0.976 (x86_64)
  and 0.948 (arm64), with `dm_medium_prices_noramp` 1.119 [1.034, 1.212]
  on x86_64: less than off and one regression.
- Keeping the perturbation but no primal clean-up
  (`max_dual_simplex_cleanup_level = 0` in graph LNS): the solve's
  unscaled refinement then re-solves the LP to remove the remaining dual
  infeasibilities, so the work is not saved.
- Perturbing only after a stall: no stalls to react to (above).

The C++ HiGHS perturbs these LPs: `bench/quick_check.sh` and
`bench/perf.py` run crest with `mip_graph_lns_cost_perturbation = true`
against the C++ app.

## Rust-specific pass of 2026-10-10 (bounds checks, PGO, FMA on x86-64-v3)

**Profile** (SIGPROF PC sampler, line level, M1): on the LP relaxation
of 3c1b60d6, `HFactor::ftran` (the dense U solve inlined) 25%,
`solve_hyper` 18%, `Dual::iterate` 7%, `ftran_l` 7%, PRICE by row 7%,
`update_primal` 4%, `btran` 4%; on lambda_080458 (MIP) the same kernels
plus feasibility jump 5%; on air05, cut separation is about 40%
(`aggr_add_row` 9%, `cmir_efficacy` 9%, gfk splay 4%).

**Codegen.** The factor solves were already unchecked (`get_unchecked`
under the documented HFactor invariant): the dense U loop is about 20
instructions per position (a gather, the |x| > tiny branch, the pivot
division, the fused axpy) with one predictable bounds check left. The
samples sit on the gather, the division and the axpy's loads and stores,
so the solves are bound by memory and mispredictions, not by checks.
What was left: per-edge checks and an `end[hi]` reload in the
hyper-sparse DFS, five checks per entry in `update_primal`, three in
`update_dual`, two in CHUZR.

### Same paths (every result bit for bit)

| Change | Effect |
|---|---|
| `solve_hyper`: per-position arrays sliced to the number of positions (one check covers all), the column end kept in a register during the DFS | instructions: 3c1b60d6 relaxation 0.975, lambda_080458 0.970, dm_full_prices root 0.973 |
| `update_primal` dense/sparse loops over arrays sliced to num_row (no checks in the dense loop); `choose_normal`, `update_dual` sliced to a common length | a further 0.979-0.997 (a closure version was outlined by LLVM: +1-3% instructions; a macro keeps it inline) |
| `HighsLpAggregator::addRow` by the `double` overload of `add`, as the C++ (the `CDouble` one added a zero low part per entry) | air05 at 200 nodes 0.973 instructions |
| `bench/pgo_build.sh`: profile-guided build, trained on check/instances LPs (simplex, IPX, PDLP) and MIPs plus given models | see below |

The branch against main (same paths), CPU time: GitHub x86_64
(x86-64-v3) all 0.990 (MIP 0.989, LP dual 0.987, IPX 0.977), arm64
0.996; M1 cycles 0.998 (the M1 was shared with load 100+ for this pass,
so its cycle counts are about ±3%; instructions on the dispatch cases
0.956-0.974). The bounds-check work is worth about 1% in time: the checks
were predictable.

PGO against the plain build (same paths): GitHub x86_64 all 0.978 (MIP
0.977, LP dual 0.979, IPX 0.972, read 0.826, PDLP 1.055), arm64 0.972
(MIP 0.973, LP dual 0.963); M1 0.964 and the fixed-work dispatch cases
0.991. It needs training runs at build time, so it is a script, not the
default build.

### Numerics: fused multiply-adds on x86-64-v3 (kept, default)

`mul_add_c` now fuses wherever the CPU has FMA: aarch64 as before, and
x86_64 built with `target-feature=fma` (x86-64-v3, the Lambda build). The
x86-64-v3 build then takes the arm64 build's paths: under Rosetta it
matches the clang arm64 C++ app on all of quick_check (73 solves, 166
command lines). `no_fma` keeps HiGHS's unfused arithmetic (gcc
`-ffp-contract=off`, the default x86_64 C++ build), and the CI oracle
builds with it, so it is unchanged. Fused against no_fma (other paths,
perf.py, GitHub): x86_64 all 0.966 (MIP 0.950, LP 1.00, IPX 0.858), arm64
0.956 (MIP 0.936, LP 0.99, IPX 0.840); the LP cases are even, the MIP
and IPX gains are path differences as much as arithmetic.

**Dispatch suite, paired on GitHub runners** (`bench-dispatch.yml`,
main against this branch, 25 models x 16 seeds, 1 thread, 180 s, run
38082983814): arm64 (same paths) time 0.984 over the 24 instances solved
by both, every gap the same, primal integral slightly lower everywhere;
x86_64 (x86-64-v3, other paths through FMA) time 0.967, solved 371 -> 370
runs (`diverse_10-01` 3 -> 2 of 16 within the limit), end gaps within
noise (hard_10-03 +0.016 ± 0.078%, primal integral -0.196 ± 0.141%).

### Numerics: tried and rejected

- Global contraction (`-C llvm-args=-fp-contract=fast`, which rustc
  honours): it would fuse inside `CDouble`'s Veltkamp split and error-free
  products, which need the unfused rounding; and on arm64 it adds only
  ~140 fused sites to the ~1640 the port has already.
- Reciprocal U pivots (multiply by stored 1/pivot instead of dividing in
  the U solves): the 25 dispatch LP relaxations took 1.014 of the cycles,
  0.998 per simplex iteration: the division is not on the critical path.
- Partial sums (four or two accumulators) in `HVec::norm2` (DSE weights),
  `update_dual`'s objective change, `cmir_efficacy`'s violation and norm
  and column-wise PRICE: LP relaxations 0.976 cycles (0.990 per
  iteration); dispatch suite (16 seeds, 180 s, instructions, geometric mean
  over seeds then instances) 0.975 over 23 solved models, 363 against 362
  optimal runs, but `hard_10-03` mean gap 1.771% -> 1.883% and MIPLIB (8
  instances, 4 seeds) 1.22, air05 1.25 over 6 seeds (5 slower; not the
  cmir or PRICE sums). Not a robust gain, so not kept.

## Graph LNS on unit commitment models: deep search gate, pool move, crossover (2026-10-16)

From the MIPFEAS study (MIPLIB 2017 unit commitment models; see
[dispatch.md](./dispatch.md)): one thread keeps `uccase9` at its
randomized-rounding incumbent (63% gap, optimum 10993) for 180 s, while
two threads reach about 12-17k, because the helper runs the deep graph LNS
search ungated. Cause: the quick search's root dive used its whole LP
budget (the dive did not check it), so the quick search tried no
neighbourhood, did not improve, and the deep search's gate ("quick search
improved and gap within 3x the target") stayed shut; the root cut loop
then takes the rest of the time.

Options off by default. Method as above (`bench-dispatch`
workflow: 25 dispatch models plus `miplib/*.mps.gz` = uccase9, unitcal_7,
irish-electricity, buildingenergy; 16 seeds, `mip_rel_gap = 0.01`,
180 s, paired on one runner; local MIPLIB checks with the instruction
clock, R = 8).

**`mip_graph_lns_deep_early`** (one thread; a helper does this anyway):
the quick search's dives stop at its LP budget; if the quick search did
not improve the incumbent and the gap is above 3x the target, the deep
search runs before the root cuts on one root LP's worth of LP iterations
(at least 5000), and again while a round closes 1% of the gap.

| early against base, one thread, 16 seeds | x86_64 | arm64 |
|---|---|---|
| solved runs (of 464) | 389 -> 389 | 387 -> 387 |
| time, geometric mean over solved models | 1.000 | 0.999 |
| `dm_full_pert_s1_ramp` (the dive cap) | 0.982 [0.971, 0.994] | 0.978 [0.967, 0.989] |
| `hard_10-03` gap at 180 s / mean primal gap | +0.003 +- 0.006 / -0.011 +- 0.046 | -0.005 +- 0.011 / -0.010 +- 0.020 |
| `diverse_10-01` gap / mean primal gap | -0.001 +- 0.003 / -0.000 +- 0.022 | +0.001 +- 0.001 / +0.008 +- 0.019 |
| uccase9 gap (base 41.0% / 47.5%) | -5.2 +- 8.6 | -6.3 +- 8.7 |
| uccase9 mean primal gap | -6.1 +- 7.0 | -6.0 +- 6.9 |
| irish-electricity, buildingenergy, unitcal_7 | even | even |

On the dispatch models it fires on no seed (the quick search improves
there); the changes there come from the dive cap, which only shortens
dives (`dm_full_pert_s1_ramp` 2% faster). On uccase9 it fires on 2-3 of
16 seeds, and those go from about 30000 to 13149-13303 (the optimum is
10993); on the others the quick search improved the incumbent a little,
so the gate stays shut and the run ends at 27000-32500 as before (or near
11000 where the tree finds it). Two variants that also fire then were
tried locally and rejected on MIPLIB (47 instances, seed 0, 120 s,
instruction clock): firing whenever the gap is above 3x the target fires
on 23 (rail507 and neos-957323 no longer solved, sp150x300d 6.1 times
the instructions), and firing when the quick search closed under a tenth
of the gap fires on 9 (sp150x300d 6.1, beasleyC3 1.72, mzzv11 1.44).

MIPLIB (47 instances, seed 0, 120 s, instruction clock): it fires on 4;
air05 and app1-1 solve with 1.13 and 1.41 the instructions, qap10 1.01,
cvs16r128-89 ends at 29.35% instead of 27.96%. Local M1 on uccase9 (seed
0-3): 29842 -> 11904-13375; thor50dday (seed 0): 59092 -> 57298.

**Tried and rejected (code removed): a pool recombination move.** A
fifth deep-search move in the style of ReXi crossover: free the decision
columns where one of the last four incumbents disagrees with the current
one (a random part if more than the move's size, grown by breadth-first
search if fewer), fix the rest, search by branch and bound. Against base,
16 seeds, x86_64 / arm64: one thread, solved 390 -> 389 / 387 -> 385,
time 1.016 / 1.009, `diverse_10-01` solved 4 -> 2 / 3 -> 1, `hard_10-03`
gap +0.009 +- 0.059 / +0.026 +- 0.058; two threads, time 1.002 / 1.005,
uccase9 mean primal gap +1.3 / +1.0 points. The incumbents of a run differ
in a few columns (each came from a neighbourhood of the last), so the move
is mostly a BFS grown from recent changes, which the BFS moves already
cover.

**`mip_concurrent_crossover`** (existing, two threads): 1.116 (x86_64) and
1.126 (arm64) of the time on the solved models (3c1b60d6 1.58 and 1.59,
`dm_small_pert_s1_*` 1.45-1.57), gaps at the limit even, uccase9's mean
primal gap +1.7 and +2.9 points. Stays off.

## MIP infrastructure outside the simplex: pass of 2026-10-16

**Profile** (macOS `sample` call trees and a SIGPROF PC sampler with the
link register, line tables; single thread, MIPLIB set at 30 s each, plus
dispatch ticks). Shares are of each instance's samples:

- *Symmetry detection* (neos-4722843-widden: 65 s of a 120 s run, 39%
  of the profile): partition refinement probed a hash table of vertex
  hashes in every sort comparison (23%), and the leave graphs were hash
  sets of edges (7%).
- *Clique partition of the objective* (MIP setup; s100: 90% of a 100 s
  run, the MIP never started): a hash query per pair of candidate
  literals, 1.8e9 queries for 39 partitions of 10-20k literals.
- *Mod-k separation*: the GFk solve's splay-tree lookups while
  enumerating basic solutions (qap10 47%, cvs16r128-89 41%, cod105
  24%, air04 29% of modk); c-MIR efficacy evaluations (air04 29% of
  modk; not changed, see below).
- *Graph LNS decision columns*: scanned every row of every integer
  column, quadratic in the row length (rail507 9%).
- *Presolve*: dominated columns re-traversed the same few long rows'
  splay trees for every column (eilA101-2: 37% of a presolve that did not
  finish in 60 s); strengthenInequalities re-sorted all indices after each
  cover step (s100 10%); probing on co-100 is 76% of its 3.1T-instruction
  presolve, mostly activity updates in `do_change_bound` (57%) after
  clique implications.
- *Domain propagation* on the dispatch ticks: 11% of the hard tick's
  root, nearly all in graph LNS's flip-search screening, spread over the
  activity update and row propagation loops (no single hot line).

**Landed** (every change keeps the paths: quick_check, presolved models
compared byte for byte on 68 instances, MIP logs at 100 nodes on 64
MIPLIB and dispatch instances; instructions retired, M1, base = main):

| Change | Effect |
|---|---|
| Symmetry: vertex hashes in a dense array, leave graphs as sorted per-cell edge lists, the sparse-hash monomial of each cell once | neos-4722843-widden (30 nodes) 0.729 with the rest; detection 65 s -> ~13 s |
| Parallel rows/columns: the sparse-hash monomials once per index | small |
| Clique partition: a literal's neighbours from its cliques' entries via literal positions when cheaper than a query per candidate; candidates inside one clique of the literal need no search; same neighbours and query count | s100 setup about 150 s -> 15 s (first solution 61 s -> 31 s with the next) |
| GFk solve: basic solutions from sorted copies of the factor rows (the trees are rebuilt by the next fromCSC, so splaying them was wasted) | qap10 0.797, cvs16r128-89 0.794, neos-933966 0.857, air04 0.927 (100-200 nodes) |
| Graph LNS decision columns in linear time (per-row integrality flag, count of implied columns) | rail507 (200 nodes) 0.845 |
| Dominated columns: stored row positions cached per row with a matrix version | eilA101-2 presolve (no probing) 64 s -> 39 s |
| strengthenInequalities: only the cover's and the slack's reduced costs re-sorted, merged into the rest (a strict total order, so the same order) | presolve s100 0.577 (with the next), neos-957323 0.858 |
| CDouble two-product by one FMA where the CPU has it (arm64, x86-64-v3; Dekker's product elsewhere): both are the exact rounding error, so the same values | presolve 0.87-1.00, MIP 0.90-1.00 (ex9 0.897) |

Totals against main (instructions, same paths): widden 0.729, rail507
0.845, air04 0.927, qap10 0.797, neos-957323 0.933, cvs16r128-89 0.794,
neos-933966 0.857, the hard dispatch tick's root 0.981; s100 presolve
0.577; the dispatch models at 100 nodes 0.99-1.00 (they are LP-bound).

**Runners** (`bench-dispatch`, main against this branch, 25 dispatch
models x 4 seeds, one thread, 180 s; run 38997905789): every one of the
100 pairs per architecture ends with the same primal and dual bound
(x86_64 built for x86-64-v3, so the FMA two-product is checked there
too; the pairs that differ in node count are the time-limited hard ticks,
which get further in 180 s). Time, geometric mean over the solved models:
x86_64 0.994, arm64 0.992; largest ratio lambda_080458 1.013
[1.003, 1.024] on x86_64 (0.990 on arm64), smallest
dm_small_randsoc_noramp 0.958 / 0.970. The dispatch models are LP-bound,
so the gains are small there.

**Tried and rejected:**
- c-MIR efficacy (`cmir_efficacy`) with the norm update by a select
  instead of the data-dependent branch: +5% instructions on air04, cycles
  even. The sum is a sequential chain of fused multiply-adds, so it cannot
  be split without changing results.
- A 4-entry most-recent cache of stored rows in dominated columns: no
  hits on eilA101-2 (columns alternate over all 100 rows); kept per row.

**Not done (leads):** dominated columns still scan the whole stored row
per column (eilA101-2: the rest of its presolve time is that scan and
probing); probing's activity updates after clique implications (co-100);
the flip search's screening refixes the open columns for every move
(order-dependent, so path-changing); symmetry `compare_current_graph`
(6% of widden) and partition refinement's `partition` (3%). Lead
instances that are LP-bound (neos-2978193-inde: 77% in node LP solves)
or have no symmetry found (fhnw-binpack4-48) are not helped by this pass.

### Follow-up (2026-10-17)

- **Dominated columns, landed (same paths):** the row cache also keeps
  each entry's column, value, integrality and signature, so the scan of
  a long row reads them in sequence (valid while neither the matrix nor
  rowpositions changes; a nested reduction switches to live reads).
  1.3e9 entries are scanned on eilA101-2, half of them reach the
  signature test and 0.04% pass it: the scan is gather-bound. eilA101-2
  presolve without probing 18.8 s -> 7.9 s (instructions 1.05);
  presolved models identical on 71 instances.
- **Probing on co-100, rejected:** the activity update's bound difference
  times the coefficient by a two-product alone when the difference is
  exact (it nearly always is; bit-identical, unit-tested): co-100 presolve
  1.022 of the instructions, 0.964 of the cycles (runs side by side, so
  within noise). Two-sums and the double-double comparisons of the
  activity update are the rest of that time.
- **fhnw-binpack4-48:** not a symmetry-detection cost. Its model is a
  2D placement with big-M disjunctions over items of different sizes, so
  the colour refinement leaves no cell of two or more columns and
  detection stops before searching (no log line). Proving infeasibility
  as SCIP does would need another technique, not a faster detection.
## IPX against the HiGHS 1.15.1 release (2026-10-17)

bench-std's LP suite with `solver=ipm` (docs/benchmarks.md, run
38939646677) had crest behind the release binary: sgm 3.26 s against
2.98 (x64) and 3.24 against 3.14 (arm64), with neos-631710 4.9 times
slower on x64, neos-5049753-cuanza and neos-4763324-toguru about 2,
neos-4532248-waihi and chromaticindex512-7 about 1.6, and
irish-electricity's relaxation ending "Unknown" where the release says
"Optimal".

**Cause: floating-point paths, plus a small per-operation cost.**
- Not HiPO: the release's MIT static build (the one bench-std runs) is
  made without it (`HIPO` undefined in its HConfig.h), so `solver=ipm` is
  IPX there too. The Apache build has HiPO and runs it for `solver=ipm`.
- Not upstream or fork changes: IPX and basiclu in the pinned fork are
  v1.15.1's (the fork only added the Rust hooks).
- Paths. The release is built by gcc: on x86_64 without FMA instructions
  (generic x86_64), on aarch64 with gcc's contraction; crest fuses where
  clang does on arm64, also on x86-64-v3. IPX amplifies the last-bit
  differences into other iteration counts, other crossover work and, on
  toguru, another outcome (the release's IPM stops at "no progress" after
  48 iterations and the simplex finishes fast; crest's goes on for 135).
  Built like the release (`--features "libstdcxx no_fma"`, generic
  x86_64), crest takes the release's path on 243 of the 249 LPs (run
  38997054427, x64), solves the same 243 (irish-electricity "Optimal"),
  and takes 1.023 of its shifted time. Against that build, the default
  x86-64-v3 crest takes the median instance in the same time (1.002,
  normalised by the release on the same runner) but has a heavy tail:
  neos-631710 4.2 (166200 primal crossover pushes after 22 IPM iterations,
  against 12905 crossover iterations after 25), toguru 2.0, cuanza 1.9,
  chromaticindex512-7 1.5, waihi and roi5alpha10n8 1.4; sgm 1.038.
- Per operation, on the same path: 1.023 (x64), most on crossover-heavy
  LPs (savsched1, neos-3025225, cuanza, waihi: 1.15-1.20), where basiclu's
  hyper-sparse solves (DFS and triangular solve) and the starting-basis
  matching ran with bounds checks the C code does not have (M1: 1.22 times
  the C++'s instructions on savsched1, 1.27 on chromaticindex512-7).

**irish-electricity "Unknown".** On crest's path (and the C++ app's, same
path) IPX's crossover ends "imprecise", the simplex clean-up of the
presolved LP is optimal, and after postsolve the dual simplex on the
original LP stops twice at "possibly dual unbounded" without a proof of
infeasibility (the step is made taboo), returning with 5 primal
infeasibilities (max 3e-5) and status Unknown, which the final KKT check
keeps. On the release's path the same clean-up ends within tolerances and
the KKT check turns Unknown into Optimal. That is HiGHS behaviour on an
unlucky path, not a port difference (the no_fma build ends Optimal like
the release); left as is.

**Landed (same results bit for bit).**
- The starting-basis matching (`guess_basis`, and `symbolic_invert`'s):
  cs_augment searches the whole alternating tree again for every column
  that cannot be matched. A failed search's tree is closed (every row next
  to its columns is matched inside it), so no later augmenting path can
  use it: it is now marked dead and skipped. Same matching (a test
  compares it with plain cs_augment on random matrices). It was 40% of
  chromaticindex512-7's IPX time.
- Unchecked indexing (debug-asserted) in the matching, basiclu's `dfs` and
  `solve_triangular`, and the normal-matrix product, under the invariants
  of the model (row indices checked at load) and of the factorization.

M1 (loaded, one run each, IPX with crossover, cycles / instructions of
this branch against main, every case same path): rail507 1.001 / 0.986,
co-100 0.997 / 0.976, neos-1354092 0.886 / 0.870, neos-960392 0.894 /
0.879, app1-2 0.984 / 0.843, unitcal_7 0.904 / 0.897, neos-3988577-wolgan
0.921 / 0.880, chromaticindex512-7 0.633 / 0.750, dispatch 3c1b60d6
relaxation 0.971 / 0.946.

GitHub runners, bench-std LP suite with `solver=ipm`, this branch against
main built the same way (x86-64-v3 on x64), all of an instance on one
runner (run 38999699398): shifted time 0.976 (x64) and 0.981 (arm64) over
the 242 and 241 LPs both solve, every result the same; chromaticindex1024-7
and 512-7 0.64-0.71, neos-631710 0.69 (x64) and 0.81 (arm64),
gfd-schedulen180f7d50m30k18 0.84, nothing slower than 1.03. Dispatch
suite on two threads (IPX in the root race; one thread never calls it),
main against this branch, 8 seeds, 180 s (run 39001306423): time 1.003
(arm64) and 1.008 (x64) over 24 instances, solved 190 -> 190 and 189 ->
190, gaps and primal integrals within noise (the ramp and lambda models,
where IPX wins the race, 0.95-0.99; dm_small_windlull 1.28 [0.91, 1.79]
and 1.08 [0.93, 1.27] is the race's timing noise).

Against the release afterwards (run 39003533706, same suite): x64 sgm
3.04 s against 2.89, 1.029 on the 242 both solve (was 1.062); arm64 3.05
against 3.04, 0.995 (was 1.015). The rest is the path tail above
(neos-631710 3.8 on x64, toguru and cuanza about 2 on both arches) and
irish-electricity.

**Rejected / not done.**
- Building crest unfused on x64 to follow the release's paths: the median
  LP is unchanged (1.002), the tail is a handful of LPs either way, and
  fused arithmetic is worth 0.95 on MIP and 0.86 on IPX in perf.py (see
  the Rust-specific pass); not a robust gain.
- More unchecked indexing in the crossover's ratio tests and updates:
  under 3% of savsched1's samples (the most crossover-bound LP), left.

**HiPO: not ported.** The release's Apache build (HiPO for `solver=ipm`,
falling back to IPX when it fails) against its MIT build (IPX), bench-std
LP suite, one thread, 300 s, run 38997266525 (stopped part way: 121 LPs
on x64, 33 on arm64): HiPO takes 1.42 (x64) and 1.27 (arm64) of IPX's
shifted time, median 1.13 and 1.17; it is faster on 15 of 121 and 6 of 33
(rmatr200-p5 0.3, roi5alpha10n8 and bab2/bab6 about 0.5) and far slower on
others (sorrell3 12, ns1116954 10-14, physiciansched3-3 6.5, qap15 4). On
x64 it ends without a status on 3 LPs and at the limit on 1 that IPX
solves (IPX times out on 1 that HiPO solves). A port would be HiPO's 11600
lines of C++ plus METIS (8200 lines of C), AMD (3600) and RCM (900), about
twice IPX (13000), and dense kernels that need tuning per arch. On the
dispatch models IPX runs only in the two-thread root race (crossover
included, which HiPO uses too), and wins it only on the lambda and ramp
models (see `mip_root_lp_ipx` above); one-thread solves never call it. So
a faster IPM would move a fraction of the root LP on a few models, and
HiPO is not faster on average.

## Root LP: Devex switch, bitmap sparse solves, CHUZR (2026-10-17)

### Where the root LP's time goes (M1, one thread, main)

First root LP of the MIP, after presolve; time per phase by timers in
`Dual::iterate`, samples by macOS `sample`.

| Model | rows | iterations | per row | us / iteration | DSE FTRAN | FTRAN (col + BFRT) | CHUZR + BTRAN | PRICE + CHUZC | rebuild |
|---|---|---|---|---|---|---|---|---|---|
| full noramp (8 models) | 19.6-29.0k | 10.0-16.4k | 0.51-0.57 | 30-54 | 28-39% | 15-19% | 9-10% | 4% | 12-17% |
| medium noramp (4) | 13.5-15.6k | 6.5-8.1k | 0.47-0.52 | 21-31 | 24-31% | 14-19% | 9% | 3-4% | 15-16% |
| `hard_10-03` / `diverse_10-01` | 25.2k / 25.4k | 12.0k / 12.5k | 0.48 / 0.49 | 67 / 138 | 33% / 31% | 29% / 31% | 9% / 13% | 4% / 6% | 9% / 6% |
| full ramp (3) | 26.1-29.0k | 21.3-28.7k | 0.73-1.10 | 195-281 | 29-34% | 11-13% | 15-18% | 13-14% | 6-7% |
| medium ramp | 14.2k | 10.1k | 0.71 | 108 | 32% | 15% | 17% | 13% | 8% |
| lambda ticks (3) | 23.4-23.6k | 14.5-18.3k | 0.62-0.78 | 145-221 | 26-34% | 19-25% | 16-21% | 10-17% | 6-8% |
| small (5) | 5.2-6.1k | 3.3-6.3k | 0.55-1.19 | 23-72 | 25-27% | 17-22% | 10-20% | 5-11% | 10-14% |
| buildingenergy | 268k | 228.5k | 0.85 | 565 | 2% (HiGHS's costly-DSE switch to Devex fires) | 53% | 22% | 4% | 12% |

**Iterations or their cost?** Iterations per row vary 2x across the
suite (0.47-1.19), the cost of an iteration 10x (21-281 us at similar
sizes): the slow models are slow per iteration. On the ramp and lambda
models B^-1 is dense (running densities: DSE FTRAN results 0.7-0.85 at
their peak, BTRAN 0.2-0.26, PRICE 0.4), so every solve is a dense one, and
the DSE FTRAN, on a dense row of B^-1, is the dearest (29-34%).
buildingenergy is the other way round: sparse results (column 2%, row
0.1%), but its hyper-sparse U solve visits 4 positions for each nonzero
it finds (the others cancel to zero), 43% of its samples, and CHUZR's
infeasibility list (45-58k rows, a third of them infeasible) 20%;
factorizations (44 ms each, every ~1700 iterations) 12%. So the work was
on cheaper iterations.

### Landed

| Change | Paths | Root LP effect (M1) |
|---|---|---|
| `mip_root_lp_devex_density` = 0.7: the first root LP (no basis, not a sub-MIP, at least 10000 rows; also the dual simplex of the two-thread race) switches from DSE to Devex once the running density of its DSE FTRAN results passes 0.7; later LPs start DSE from the weights it leaves (as after HiGHS's own costly-DSE switch) | changed where it fires: the ramp and lambda models of the suite (on MIPLIB, 33 of 47 instances at seed 0 without the row gate) | instructions to the end of the root LP: full ramp 0.49-0.60, lambda 0.52-0.70, medium ramp 0.73; the rest unchanged |
| Hyper-sparse triangular solves (FTRAN/BTRAN, L and U) in the dense solve's order: the positions to visit are a bitmap, scanned a word at a time, set from the nonzeros as they are found; same results as the dense solve bit for bit (checked against a dense reference on every solve of several dispatch, MIPLIB and Netlib models). Cargo feature `hyper_dfs` keeps HiGHS's depth-first search, for the CI oracle | changed (sums in another order than HiGHS's DFS) | buildingenergy 128.4 s -> 89.3 s (U solve per call 91 -> 31 us); dispatch root LP time 0.92 geometric mean (0.74-1.14; iteration counts move by -16% to +14%) |
| CHUZR visits only the slots (rows, or list positions) that may hold a primal infeasibility: a bitmap superset kept by the primal updates and the infeasibility list, cleared as the scan finds rows feasible, rebuilt when unsure (rebuild, dense column, taboo rows) | same (iteration counts identical on all 25 dispatch root LPs; asserted against the plain scan) | buildingenergy 72.3 s -> 69.8 s, 0.92 instructions; dispatch even |

buildingenergy's root LP on the M1: 128.4 s (main) -> 69.8 s, 565 -> 277
us per iteration (252k iterations instead of 229k: another path).

### MIP: paired runs (`bench-dispatch`, 16 seeds, 180 s, `mip_rel_gap = 0.01`, 25 dispatch models + 4 MIPLIB UC models)

Time: geometric mean of per-instance ratios over runs both solve; pg:
mean primal gap over the limit (percentage points). Head = this branch;
headinf = this branch with `mip_root_lp_devex_density = inf`. Runs
38997056005 (one thread), 38997060596 (two threads), at threshold 0.7
before the 10000-row gate (the gate only returns the five small models to
their headinf behaviour, below).

| one thread | x86_64 | arm64 |
|---|---|---|
| head / main, solved models | 0.914 | 0.884 |
| solved runs (of 464) | 390 -> 390 | 387 -> 388 |
| full ramp / lambda models | 0.74-0.91 / 0.62-0.84 | 0.75-0.91 / 0.68-0.84 |
| full noramp models | 0.84-0.94 | 0.84-0.93 |
| `hard_10-03` gap / pg | -0.009 +- 0.089 / -0.25 +- 0.15 | +0.007 +- 0.083 / -0.30 +- 0.12 |
| `diverse_10-01` gap / pg | -0.05 +- 0.06 / -0.27 +- 0.16 | -0.06 +- 0.07 / -0.27 +- 0.17 |
| buildingenergy gap / pg | -0.25 +- 0.17 / -22.1 +- 4.5 | -0.29 +- 0.19 / -23.2 +- 3.9 |
| irish-electricity, uccase9 | even | even |
| unitcal_7 (solved, 0.37% gap) | 1.37 [0.99, 1.89] (bitmap path; switch 1.00) | 1.35 [0.97, 1.86] |
| switch alone (head / headinf), ramp + lambda | 0.75-1.02 | 0.80-1.04 |
| switch alone, small models (before the gate) | 0.91-1.22 (windlull 1.22 [0.93, 1.60]) | 0.90-1.22 |

| two threads | x86_64 | arm64 |
|---|---|---|
| head / main, solved models | 0.931 | 0.931 |
| solved runs | 393 -> 393 | 396 -> 396 |
| switch alone (head / headinf) | 1.007 | 1.004 |
| buildingenergy gap / pg | -0.30 +- 0.19 / -26.2 +- 4.6 | -0.43 +- 0.19 / -25.3 +- 4.6 |
| unitcal_7 | 0.875 [0.82, 0.93] | 0.796 [0.65, 0.97] |
| worst model | `diverse_10-01` 0.96 (8 solved) | `diverse_10-01` 1.31 [0.88, 1.96] (12 solved) |

buildingenergy: the root LP ends 40-60 s earlier on the runners, so its
first incumbent comes earlier (mean primal gap down by 22-26 points) and
the gap at 180 s is 0.25-0.43 points lower.

MIPLIB sanity (47 instances, seed 0, 120 s, instruction clock R = 8, M1):
head (switch at 0.3) against main, 0.988 geometric mean over the 19
solved by both, solved 20 -> 21; the switch at 0.7 (no row gate) against
off: 0.976, solved 21 -> 22 (ns1208400 lost, cod105 and neos-933966
gained). perf.py (main -> head, M1 cycles): LP dual simplex 0.972, primal
0.933, IPX 0.992, MIP 0.952, all 0.971; dispatch 3c1b60d6 relaxation
0.666.

### Confirmation on the final head (10000-row gate), and diverse_10-01

16 seeds, all 29 models, head against main: one thread 0.915 (x86_64) and
0.882 (arm64), solved 387 -> 387 and 387 -> 388, dm_small_windlull 0.99 /
0.97 (the gate); two threads 0.939 and 0.929, solved 396 -> 395 and 398 ->
395. The two-thread drop is diverse_10-01 (12 -> 10, 14 -> 11 solved at 16
seeds), so it was rerun at 32 seeds with the parts separated: A = main,
B = the switch alone (HiGHS's DFS order, `hyper_dfs`), C = the dense-order
solves alone (`mip_root_lp_devex_density = inf`), D = both (the default).

| 32 seeds | | diverse solved | diverse gap (B-A) | diverse pg | hard_10-03 pg |
|---|---|---|---|---|---|
| one thread, x86_64 | B / C / D | 7 / 8 / 9 (A: 7) | +0.00 / -0.05 / -0.06 +- 0.05 | -0.03 / -0.28 / -0.28 | -0.01 / -0.34 / -0.33 |
| one thread, arm64 | B / C / D | 4 / 7 / 8 (A: 4) | -0.00 / -0.05 / -0.06 +- 0.05 | +0.02 / -0.24 / -0.23 | -0.03 / -0.38 / -0.35 |
| two threads, x86_64 | B / C / D | 21 / 20 / 20 (A: 19) | -0.01 / +0.01 / +0.01 +- 0.02 | +0.00 / -0.04 / -0.04 | -0.01 / -0.35 / -0.35 |
| two threads, arm64 | B / C / D | 24 / 26 / 24 (A: 18) | -0.00 / -0.01 / +0.01 +- 0.02 | +0.02 / -0.09 / -0.10 | +0.04 / -0.35 / -0.35 |

So the 16-seed drop was path luck; the dense-order solves are what improve
the two hard ticks (their LNS LPs), the switch does not fire on them.
Cost per hyper-sparse solve on diverse (1 thread, 60 s, instruction
clock): bitmap 0.34 / 3.6 / 26 us against DFS 0.40 / 4.6 / 40 us for
results of under 16 / under 256 / more nonzeros, so the O(rows/64) scan of
the bitmap does not cost on its many tiny solves. unitcal_7 (MIPLIB) one
thread 1.35-1.39 [0.98, 1.92] (solve order; B&B path), two threads 0.79-0.81.

### Tried and rejected

- Switching at density 0.3: fired on 19 of 25 dispatch models, including
  every full noramp model, `hard_10-03`, `diverse_10-01`, irish-electricity
  and uccase9 (late in the solve, where DSE costs little): one thread,
  switch alone, `dm_full_pert_s2_noramp` 1.15-1.17 [1.03, 1.33],
  `dm_small_windlull` 1.39-1.40, irish-electricity primal gap +9.5-10.3
  points, uccase9 gap +21 +- 14 (arm64), `diverse_10-01` solved 4 -> 1.
  The density at which each model first passes 0.2-0.6 put 0.7 between
  the noramp models (peak 0.18-0.48) and the ramp and lambda models
  (0.73-0.85).
- Devex from the start of the root LP (`mip_root_lp_devex`, removed):
  root LP instructions 0.42-1.10 (`hard_10-03` 1.10); after it the cut
  loop's near-optimal re-solves run Devex too (no DSE weights). The switch
  keeps DSE where it is cheap and leaves weights for the later solves.
- Packing U's start, end, pivot index and pivot value into one record per
  position for the depth-first search (a shadow copy, rebuilt on change):
  slower; the bitmap solve removes the visits instead.
- Refactorizing more often to keep the FT-updated U small (buildingenergy:
  U solve 52 us just after a refactorization, 100-170 us after 1200-2000
  updates): a factorization costs 44 ms, more than the solves save.
- Not tried: a faster factorization kernel (12% on buildingenergy), a
  second thread for PRICE or concurrent FTRANs (on two threads the second
  core races IPX on this LP and then runs the LNS helper), crash bases and
  dispatch-specific presolve (the slow models are slow per iteration, not
  by iteration count).

## Cut-based conflict analysis: tried and removed (2026-10-18)

Mexi, Serrano, Berthold, Gleixner and Nordström (arXiv:2410.15110,
INFORMS JoC 2025; on by default in SCIP 10) learn linear constraints
instead of bound disjunctions. Implemented as `mip_conflict_linear`
(branch `conflict-lin`, 2043f3b, ~230 lines in mip/conflict.rs): from the
row that detected the infeasibility, or the LP's dual proof, resolve the
latest bound change the row needs with the linear row that propagated it
(model row, cut, objective cutoff row, conflict of binaries), applying
the cMIR reduction of the reason (Proposition 2) when the plain
combination is not infeasible before that change, up to the first UIP;
the learned row, coefficient-tightened, goes to the cut pool as a
propagated conflict. HiGHS's graph-based conflicts and its proof cuts
(generateConflict) were kept, as in SCIP.

Two bugs in the first version gave wrong answers on 19 MIPLIB instances
(both arches): clique-table reasons are not the two-literal clique (the
reason literal reaches the fixed column through column substitutions),
and a combination that cancels to rounding noise was rescaled to a
garbage row. Without clique reasons and with cancellation below 1e-6 of
the terms' scale rejected, MIPLIB had no wrong answers.

**MIPLIB 2017 benchmark** (`bench-std`, 240 instances, one thread, 300 s,
crest against crestcl = crest with the option; shifted geometric mean,
shift 10 s; ratios over the instances both solve, 95% t-interval):

| arch | solved off -> on | sgm time off / on | time on/off, both solved | nodes on/off, both solved |
|---|---|---|---|---|
| arm64 | 95 -> 93 | 143.6 / 143.1 | 0.998 [0.953, 1.045] (89) | 0.937 [0.805, 1.092] |
| x64 | 96 -> 96 | 141.5 / 141.3 | 1.000 [0.956, 1.046] (91) | 0.939 [0.808, 1.090] |

**Dispatch** (`bench-dispatch`, 25 models, 16 seeds, 180 s, gap 0.01,
geometric mean of the per-model time ratios on/off over the 24 models
with solved pairs):

| threads | arm64 | x64 | solved (arm64; x64) |
|---|---|---|---|
| 1 | 0.991 | 0.986 | 373 -> 373; 373 -> 373 |
| 2 | 1.003 | 0.992 | 381 -> 383; 378 -> 381 |

No model improved beyond its interval. The hard tick at one thread
0.919 [0.845, 1.001] (arm64), 0.920 [0.844, 1.002] (x64); none at two
threads (0.998, 1.009). `hard_10-03` end gap -0.03 +- 0.10 (one thread),
-0.03 +- 0.06 (two): no effect. Regressions: `dm_full_randsoc_ramp`, one
thread, primal integral +0.10 +- 0.06 (arm64) and +0.115 +- 0.06 (x64)
of 4.2; `dm_full_pert_s2_ramp`, two threads, end gap +0.026 +- 0.024 and
primal integral +0.073 +- 0.054 (arm64); the small models +0.5-1% time.

Why nothing for dispatch: the dispatch models search almost no tree.
`hard_10-03` processes 2 nodes in 90 s and the hard tick 1, so conflict
analysis of the main search hardly runs; their bound is the root's, not
the tree's (bound-limited means root-bound-limited here). What changed
came from sub-MIPs (graph LNS, RINS), where it only moved paths. On
MIPLIB, 6% fewer nodes (not significant) and no time gain. (Counted on
neos-911970 before the clique fix: a learned row from 12-40% of the
analyses; the rest stopped at once at a first UIP, a non-linear reason,
or a failed reduction.) Not tried: the mixed-binary
reduction of Section 6 (resolving non-relaxable continuous columns out of
the reason first), the remaining blocker on the continuous-heavy models.
The bench tooling kept: `bench/std.py` solvers `crestX` with options
`crestX:K=V` (A/B of a crest option in `bench-std`).

## IPX cleanup: primal phase 1 when the original LP ends Unknown (2026-10-17)

irish-electricity's LP relaxation with `solver = ipm` ended with status
Unknown (the `no_fma` build: Optimal). After IPX, crossover (imprecise)
and postsolve, the original LP has 3-7 basic variables infeasible by 5e-7
to 3e-5 (tolerance 1e-7). The dual simplex cleanup, scaled and then
unscaled, picks each such row, finds no entering column in the ratio test
(possibly dual unbounded), cannot prove infeasibility and makes the row
taboo, so it ends with status Unknown. Falling back to the primal simplex
as it stands does not help: since the squared infeasibility (9e-10) is
below the tolerance, it forces phase 2, shifts the five bounds, finds
itself optimal, removes the shifts and hands the same infeasibilities back
to the dual cleanup.

Fix: when the dual simplex on the unscaled LP ends Unknown with primal
infeasibilities, `solveLpSimplex` runs the primal simplex from that basis
with the forced phase 2 off, so it does a phase 1 (no bound perturbation:
the basis is near-optimal). irish-electricity: Optimal after 47 more
iterations, objective 2546254.5632 (dual simplex alone: 2546254.5633), max
primal infeasibility 8.8e-8. Only that failure takes the new path, so every
solve that ended otherwise is unchanged (the CI oracle matches the C++
app but for one IIS command line: on klein1 an LP of HiGHS's elasticity
filter ends Unknown, so HiGHS reports a reducible IIS; crest now finds an
irreducible one of 50 rows, marked as an expected difference). Rejected: accepting such a solution as optimal at a relative
tolerance (the residuals are absolute, and 300x the tolerance); the primal
simplex with its forced phase 2 (above).
## MIPLIB instances where crest loses (2026-10-17)

From the bench-std baseline (docs/benchmarks.md): the instances where
crest trails SCIP 10 or HiGHS 1.15.1, run locally (M1, one thread,
300 s, seed 0 unless stated) with crest, the HiGHS 1.15.1 release binary
and SCIP 10 (pyscipopt; logs and statistics).

| instance | crest | HiGHS 1.15.1 | SCIP 10 | cause |
|---|---|---|---|---|
| graph20-20-1rand | time limit | time limit | 5.9 s | symmetry: SCIP fixes 666 columns in presolve (packing orbitope 52 x 37, its triangle) and solves a 340-column model; crest found the orbitope but only fixed below branched rows |
| neos-933966 (arm64) | time limit (120 s) | 33 s | | symmetry: packing orbitopes (as above) |
| decomp2 | 37 s | 2.8 s | 3.1 s | seed luck: seeds 1-6 2.8-37 s (HiGHS 2.5-5.3 s); bound at the root, first good incumbent from a sub-MIP |
| neos-2978193-inde | 70 s | 18 s | time limit | seed luck: seeds 1-3 8-17 s (HiGHS 20-38 s) |
| neos-873061 | time limit | 129 s | time limit | seed luck: seeds 1-3 solve in 129-198 s (HiGHS: 116 s, two time limits) |
| neos-787933 | time limit | time limit | 1.7 s | presolve: SCIP's knapsack handler adds 61944 two-variable set partitioning rows and setppc aggregates them (1764 columns left; crest 63708) |
| enlight_hard | 9.6 s | 10.6 s | 0.03 s | SCIP recognizes XOR rows and solves them by Gaussian elimination |
| fast0507 | time limit | time limit | 248 s | presolve: SCIP's dominated columns fix 42291 (crest 36058), 20676 columns left (crest 26114) |
| fhnw-binpack4-4 | time limit (infeasible) | time limit | 21 s | tree search: SCIP needs 29753 nodes with 28195 propagation conflicts and node cuts (47k aggregation cuts applied); crest explores 355k nodes; without crest's tree restarts no change |
| CMS750_4, rocI-4-11, istanbul-no-cutoff, pk1, cod105, piperout-27 | 35-300 s | as crest (28-300 s) | 17-157 s | tree search (HiGHS and crest alike): no single component; mip_allow_restart off changes nothing material |

So the losses against HiGHS 1.15.1 are mostly seed variance (crest's tail
is longer on decomp2), and against SCIP the largest single cause that
fits HiGHS's design is symmetry handling at the root.

**Landed: `mip_symmetry_static_orbitopes` (default true).** An orbitope
with set packing (or negated packing) rows is handled as the packing
orbitope of those rows in the static order of their indices: right after
the first root LP the triangle above the diagonal is fixed on the global
domain (as SCIP's orbitope presolving), and nodes apply orbitopal fixing
in the same order (HiGHS uses the order of the branching rows, which is
incompatible with root fixings). Sorting the columns of a submatrix of
the orbitope is a valid reduction on its own. To fix early, symmetry
detection is finished after the first root LP (it was finished after the
root cut loop), so its task is now spawned after the analytic centre's.
With more than one worker the detection runs on another thread and is
finished where it was (after the root cut loop; syncing early would wait
for it), and the triangle is fixed then. Off, crest is as HiGHS;
bench/quick_check.sh and perf.py set it off for the CI oracle.

Local (M1, 120 s, 55 MIPLIB instances, seed 0): only instances with
symmetry change path; same optimal values everywhere; graph20-20-1rand
solved in 118-167 s (was unsolved at 300 s), neos-933966 17 s (unsolved
at 120 s), piperout-27 37.8 -> 24.3 s, ns1208400 43.9 -> 34.2 s,
neos-911970 6.1 -> 4.3 s; neos-957323 34.7 -> 38.0 s.

**Runners.** bench-std MIPLIB (one thread, 300 s, run 39021753437,
against the baseline run 38939685629, whose crest takes the same paths
as main):

| arch | solved | sgm time (s) | wrong |
|---|---|---|---|
| x64 | 92 -> 93 | 143.9 -> 143.7 | 0 |
| arm64 | 91 -> 93 | 146.4 -> 143.2 | 0 |

graph20-20-1rand (x64 242 s, arm64 193 s) and neos-933966 (24 s, 19 s)
are newly solved on both; fast0507 on x64. The other x64 changes
(k1mushroom and neos-3004026-krka unsolved, eil33-2, radiationm18-12-05,
swath3, neos-957323 slower, supportcase33 faster) have the same node
counts as the baseline and ran on other CPU models (the x64 runners mix
EPYC 7763, 9V45, 9V74 and Xeon 6973P): load, not path; on arm64 those
instances are even.

bench-dispatch (16 seeds, 180 s; runs 39021754772 and 39021756191):
one thread, solved 371 -> 371 on both arches, time 0.999 (arm64) and
1.002 (x64), no interval above 1. The dispatch ticks have a full
orbitope (1120 columns on dm_full_pert_s2_ramp) but no packing rows,
so their one-thread paths do not change. Two threads with the early
sync: solved 381 -> 382 / 377 -> 382, time 1.008 / 1.000, but
dm_full_pert_s2_ramp 1.032 [1.022, 1.042] on x64 (primal integral +0.08
points): the early sync waited for the detection running on the second
core and changed which task the helper took. Since then the early sync
is one-thread only (and never in the concurrent LNS helper), so
two-thread runs schedule as main. Rerun after rebasing on main (run
39094216794, two threads, 16 seeds): solved 379 -> 379 (arm64) and 377
-> 378 (x64), time 0.992 and 1.011, dm_full_pert_s2_ramp 1.008
[1.000, 1.016] on both, the largest interval lambda wind185 1.015
[1.005, 1.025] on x64; the dispatch orbitopes have no packing rows, so
these two-thread differences are scheduling noise.

## Cheap B&B tweaks and implied integers from the literature survey (2026-10-18)

From the survey notes (crestline-bench/lit/core.md). Each was an option
on its own branch, screened locally (M1, one thread, 120 s, 56 MIPLIB
and target instances, main and variant side by side) and, if
promising, on bench-std (240 MIPLIB, one thread, 300 s, option off and
on at the same commit).

| change | screen (time ratio, shifted geometric mean; solved) | runners | verdict |
|---|---|---|---|
| strong branching lookahead 8 (stop after 8 strong-branched candidates without a better score; arXiv:2312.07041) | 1.012 (1.011 without neos-2978193-inde); 23 -> 23; binkar10_1 35.6 -> 53.9 s, enlight_hard 11.1 -> 23.0, irp 25.6 -> 37.5 | not run | rejected: HiGHS's budget on strong branching iterations already bounds it, and stopping early loses the better candidates |
| network implied integers in presolve (+-1 continuous columns, at most two per column, rows signed by union-find; arXiv:2504.07209) | 1.002 without neos-2978193-inde (93 -> 14 s with: all 20736 continuous columns become implied integer); 23 -> 23 | bench-std runs 39078267364 / 39078268540: x64 solved 90 -> 89, sgm 144.3 -> 146.4; arm64 91 -> 88, 145.5 -> 147.1; exp-1-500-5-5 2-3 s -> time limit, net12 and neos-3216931-puriri lose their solves, rmatr100-p10 2.5x; neos-2978193-inde 70-79 -> 12-13 s | rejected: implied integers change cut generation (c-MIR) and heuristics on network-flow models, and that costs more than it gains |
| Gomory cut history in the branching score (weight 1e-4; arXiv:2306.06050) | 0.989 without neos-2978193-inde; 22 -> 22. Runners, miplib-easy (runs 39097406609 / 39097407930): solved 64 -> 65 on both arches, sgm 28.9 -> 25.3 s (x64) and 27.3 -> 25.1 s (arm64); dispatch screen (run 39113570874): time 1.000 (arm64), 1.003 (x64), no motivating tick worse | full MIPLIB (runs 39115313907 / 39115315327): x64 solved 92 -> 90, sgm 144.5 -> 145.1; arm64 92 -> 91, 146.1 -> 144.2; and a WRONG result on both arches: traininstance6 ends "optimal" at 28380 with dual bound 28379, beyond the known optimum 28290 (option off: 28290) | rejected: no gain over the full set; the wrong bound on traininstance6 is a latent bug in another component that this path exposes, to be shrunk with bench/mipdd.sh |
| separator backoff (a separator none of whose cuts entered an LP over 3 runs is skipped for 2..64 calls; arXiv:2311.05650) | 0.994 without neos-2978193-inde; 24 -> 24 (markshare_4_0 119 -> 83 s, neos-911970 8.4 -> 5.4 s) | not run (screen gain within noise) | rejected |

HiGHS already detects implied integers column by column (a continuous
column in an integral equation, or, dually, with integral rows and
bounds): on MIPLIB the network rule added such columns on 10 of 72
presolved models (neos-2978193-inde, binkar10_1, markshare2/4_0,
neos-911970, ns1208400 among them; none on the dispatch models).

## Wrong optimum on traininstance6: dual proofs dropped tiny coefficients (2026-10-23)

Found by the Gomory-history experiment above, then on main itself:
traininstance6 (MIPLIB 2017, optimum 28290) ended "optimal" at 28380
(Gomory branch, seed 0), 29090 (Gomory branch, seed 1) and 28350 (main,
random_seed 9); main's seeds 0-8 were right.

Diagnosis (seed 1, the 77 s case):
- dumping the presolved model at each restart: the model after the
  first restart still has optimum 28290 (crest and SCIP), the model with
  the cuts in the LP at the second restart has 31780: a cut was invalid;
- checking each cut added to the pool against an optimum of the
  restart-1 model (rounded to integers, exactly feasible): the first
  violating cut is a conflict from generate_conflict, called by
  add_infeasible_conflict after an infeasible strong-branching LP;
- the dual infeasibility proof behind it, sum a_j x_j <= 5.10e-5 with 47
  coefficients of about 1.1e-6, is violated by that optimum (5.21e-5):
  storeDualInfProof (and computeDualProof) drop coefficients below
  epsilon (1e-9) instead of moving them to the right-hand side with their
  bound. One of them sits on a column with bound ~29000, so dropping it
  strengthens the proof by ~3e-5; the cover cut derived from the proof
  then excludes every optimum. (HiGHS has the same code; its
  checkDualProof is disabled.)

Fix: `mip_dual_proof_tiny_bound_terms` (default true): tiny entries move
to the right-hand side with their global bound, as the entries below
feastol already did (no proof if that bound is infinite). False is
HiGHS's behaviour, for the CI oracle. traininstance6 with the fix: 28290
on main seeds 0-3 and 9, and on the Gomory branch's seeds 0 and 1.

Screens: dispatch (run 39985670699) time 0.995 on both arches, no
motivating tick worse; miplib-easy, off against on at the same commit
(runs 39985674652 / 39985678322): x64 solved 66 -> 64 (neos-1456979 289 s
and tr12-30 193 s now at the 300 s limit), sgm 24.6 -> 23.8 s; arm64
66 -> 66, 24.1 -> 24.1 s; no wrong result.

## Other platforms

- **gcc / libstdc++.** With the `libstdcxx` and `no_fma` features, Crestline
  follows a C++ build made with gcc and `-ffp-contract=off` (libstdc++'s
  heap and partial_sort tie orders, std::tuple hashing, unordered_multimap
  insertion order and uniform_int_distribution differ from libc++'s).
  Against such a build on the M1 (2026-10-06, heavily loaded machine, one
  rep): all 0.824, MIP 0.868, every case same path.
- **x86_64.** Fused multiply-adds that clang makes on arm64 are mirrored
  on aarch64 and (since the 2026-10-10 Rust pass) on x86_64 built with
  FMA (x86-64-v3); the generic x86_64 build and `no_fma` take the x86_64
  C++ paths. Under Rosetta
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
  every case same path (both with `no_fma`; the CI oracle checks the v3
  build too). The default x86-64-v3 build fuses: see the Rust-specific
  pass above. On GitHub's arm64 runner (`ubuntu-24.04-arm`), crest
  against the same gcc C++ build: all 0.775 (MIP 0.805, LP dual 0.760).
- **CI** checks crest against a gcc build of the C++ app on Linux x86_64
  on every push (see the README).
