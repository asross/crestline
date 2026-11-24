# Standard benchmarks

`bench/std.py` and `.github/workflows/bench-std.yml` run crest against the
latest HiGHS release, SCIP and CBC on public test sets, on native GitHub
runners (x86_64 and arm64), to track Crestline against existing solvers.

## Sets

| suite | instances | what |
|---|---|---|
| `miplib` | 240 | the [MIPLIB 2017](https://miplib.zib.de) benchmark set |
| `miplib-easy` | 66 | those HiGHS 1.15.1 solves in under 60 s in the MIPFEAS logs (i9-285K) |
| `mipfeas` | 233 | MIPFEAS: the benchmark set less its 7 infeasible instances |
| `lp` | 249 | LP relaxations of the benchmark set (`solve_relaxation`), and 9 models of [Mittelmann's LP test set](https://plato.asu.edu/ftp/lptestset/) that are not MIPLIB relaxations |

Instances are downloaded at run time (miplib.zib.de, plato.asu.edu) and
kept in `actions/cache`; none are committed.

## Solvers

- **crest**, built from the given ref (`crest_ref`; x86-64-v3 on x86_64,
  as on AWS Lambda);
- **highs**: the static `highs` binary of the latest
  [HiGHS release](https://github.com/ERGO-Code/HiGHS/releases) (generic
  x86_64 build);
- **highs-src**: the same release built from source in the runner as the
  release binary is (static, gcc), plus crest's target CPU (x86-64-v3 on
  x86_64, the default on arm64) and LTO: against `highs` it measures the
  build flags, against `crest` the algorithm alone;
- **scip**: SCIP from the `pyscipopt` wheel (its bundled SCIP and SoPlex);
- **cbc**: Ubuntu 24.04's `coinor-cbc` (2.10), off by default (slow).

Default settings and relative gap 1e-4 (HiGHS's default; set for SCIP and
CBC). SCIP and CBC skip the LP suite (SCIP is not an LP code; CLP would
be the fair COIN-OR comparison). `options` passes HiGHS options to crest
and highs only, e.g. `solver=ipm` (IPX in both).

## Metrics

- **solved**: status optimal (MIP; LP also infeasible/unbounded) within the
  limit and not wrong;
- **sgm time**: shifted geometric mean of wall time (model read included)
  over all instances, unsolved at the limit; shift 10 s for MIP, 1 s for LP;
- **sgm primal integral** (MIP): the MIPFEAS score, primal gap to MIPLIB's
  known objective integrated over the limit and divided by it, 2 before the
  first solution; shift 0.001 (as the MIPFEAS study). Over the 233 instances
  with a known objective it is the MIPFEAS benchmark at that limit;
- **correctness**: every MIP result is checked against
  `miplib2017-v31.solu`. A status of infeasible on a feasible instance (or
  a solution of an infeasible one), a dual bound beyond the known objective,
  a primal value better than the optimum (tolerances 1e-6 relative on bounds, 1e-5 on the primal value), or
  "optimal" at a value off by more than the gap is WRONG, listed first in the
  report with crashes (a signal) and kills (past 1.5x the limit + 60 s). LP
  objectives of two solvers both optimal must agree to 1e-6 relative.

Each JSONL record (artifact `std-results`) has the instance, solver and
version, arch, CPU model, status, primal and dual bounds, wall time, the
iteration counts (simplex, IPM, crossover, PDLP), the
incumbent trajectory and the check. On each runner every instance runs
all solvers back to back (the order rotating), so they see the same CPU.

## Running

Actions, bench-std, "Run workflow": suite, solvers (`crest,highs,scip`),
time limit (300 s), threads (1), archs (`x64,arm64`), shards per arch (32),
crest ref, instance globs, options. Or push a branch `bench-std-run/*`
whose commit changes the defaults in the workflow's `env`. A run job takes
at most 6 h: about shards >= instances x solvers x limit / 4 h.

Locally:

    python3 bench/std.py run out.jsonl --suite miplib-easy \
        --solvers crest=target/release/crest,highs=path/to/highs,scip --tl 60
    python3 bench/std.py report out.jsonl

### More than 4 cores: spot EC2 (`archs=ec2`)

GitHub's free runners have 4 vCPUs (2 physical cores on x64). For 8+
threads, `bench/ec2_runner.sh up N` starts N spot Graviton instances
(c8g.2xlarge: 8 cores, about $0.10/h in us-east-2) as one-job runners;
then run bench-std or bench-dispatch with `archs=ec2` and `shards=N`.
Builds stay on GitHub's arm64 runners. Each instance terminates after its
job, and at most HOURS (6) after boot; `ls` and `down` list and terminate
leftovers. A full MIPLIB run of three solvers at 8 threads is about 40
instance-hours (about $4).

For 1- and 2-thread runs, `up N c8g.large` (2 cores, about $0.02/h) is
enough for dispatch, but not for MIPLIB: its 4 GB kills crest (exit -9,
reported as `error`) on bab2, bab6, square47, savsched1, neos-3402454-bohle,
neos-5114902-kasavu and both proteindesign models. Use c8g.xlarge (8 GB)
for MIPLIB. Spot capacity for one type can run out mid-launch
(InsufficientInstanceCapacity); `up 1` again in a retry loop.

Spot instances can also be reclaimed mid-run (12 of 32 c8g.2xlarge in one
8-thread run). On the two-minute notice the run step stops and the shard
uploads the results it has; rerun the instances still missing with `select`
and report on the concatenated JSONL files.

## Screen first, then the full run

A candidate change goes through two stages, and the full benchmark runs
only for one that passes the first:

| Stage | Dispatch (bench-dispatch) | Standard (bench-std) | Cost |
|---|---|---|---|
| Screen | `screen` input, or a `bench-screen/*` branch: 10 instances (the three Lambda ticks, `hard_10-03_1340`, `diverse_10-01_0052` and the five `dm_*` models most sensitive to past changes), 8 seeds, 120 s, 8 runners per arch | `miplib-easy`, crest against main only | about 1/5 of a full run |
| Full | all instances, 16 seeds, 180 s, 16 runners per arch, 1 and 2 threads (and 4 for changes to threads >= 3) | `miplib` (240), plus `lp` for LP changes | |

Before either, screen locally on a few models by instruction count
(`CRESTLINE_INSTR_CLOCK`). A screen that shows no gain, or a loss on any
motivating tick, ends the experiment: record it in the docs and move on.

## Fuzzing for wrong answers

`bench/fuzz.py` and `.github/workflows/fuzz.yml` look for wrong answers,
not speed: a wrong status, a dual bound past a feasible solution, or an
infeasible "solution". Each model is solved by crest twice (default
options, and one variant drawn from the model's key: presolve off, another
seed, 2 threads, symmetry off, or the C++ app's paths), by the HiGHS
release (default options, and the same variant: the *twin*) and by SCIP,
at `mip_rel_gap` 0 (small models) or 1e-6. Every solution file is checked
against the model (bounds, integrality, rows; absolute 1e-6, as HiGHS). A
solution counts as evidence against another solver only once polished:
its integers rounded and fixed, the rest re-solved as an LP to 1e-10.
Without that, most flags on badly scaled models are solutions feasible
only within tolerance (an integer at 4e-8 opening a big-M row, an
all-integer equality met to 2e-6).
A flag on crest that its twin does not share is **crest-only**: a port bug
or a path difference. A flag both share is inherited from HiGHS.

Sources (`--sources`, default `random:2000,check,miplib,dispatch:6`):
random small MIPs (knapsacks, set covers, big-M fixed charge, general MIPs
with coefficients over 1e-9..1e6, and general MIPs scaled like the dispatch
models), the `.mps` models of HiGHS's `check/instances`, the 249 MIPLIB
2017 collection instances tagged easy with at most 20k nonzeros, and
perturbed private dispatch models. A dispatch perturbation scales the costs
by 1 +- 3%, loosens bounds by 0-3%, lowers the initial SOC by 0-3% and, in
3 seeds of 4, truncates the horizon. It is made in the runner's temp
directory and never uploaded. Logs and results hold names, keys, options,
statuses, objectives, bounds, violation sizes and times only.

Run: Actions, fuzz, "Run workflow" (sources, shards, archs, crest ref), or
push a branch `fuzz-run/*`. The report (run summary, artifact
`fuzz-results`) counts solves and flags by source and lists the flagged
models, crest-only first. To reproduce one: `bench/fuzz.py make SOURCE KEY
model.mps` (a dispatch one stays local), then shrink it with
`bench/mipdd.sh`. Locally:

    python3 bench/fuzz.py run out.jsonl --crest target/release/crest \
        --highs path/to/highs --sources random:200 --tmp /tmp/fuzz
    python3 bench/fuzz.py report out.jsonl

**First campaign** (2026-11-02, crest main f2620a9; runs 41351599623
on x64 and local runs on arm64):

| source | models | crest solves | crest flagged | of which crest-only |
|---|---|---|---|---|
| random (runners) | 2000 | 4000 | 166 | 9 |
| random (local, polished evidence) | 3000 | 6000 | 187 | 0 |
| check/instances | 64 | 128 | 0 | 0 |
| MIPLIB collection, easy, <= 20k nonzeros | 245 | 490 | 7 | 3 |
| perturbed dispatch | 216 | 432 | 0 | 0 |

Triage: the random flags shared with the HiGHS release are an inherited
HiGHS weakness with tiny coefficients (docs/upstream.md); the crest-only
ones there are the same models on another search path (crest's FMA build
against the release's plain x86_64 build). MIPLIB: two instances whose
known optimum all three solvers miss by 7e-6 (MIPLIB's value), and two
crest-only wrong answers, reproduced on Linux and macOS with 16 seeds
(HiGHS release: 0 of 8 wrong) for maxgasflow (an unscaled LP called
infeasible, fixed: docs/performance.md, "IPX cleanup"), and
neos-3083784-nive, where crest's dual bounds of 1304-4980 cross MIPLIB's
optimum 0. That one is MIPLIB's: it lists no solution, SCIP 10 proves a
bound of 3446 in 30 min, and "optimal 0" (HiGHS and crest with presolve
off) uses a binary at 1 - 2.75e-10 in a row with a 1e10 big-M.

**Second campaign** (2026-11-24, crest main 31d56b6, run 45335365736 on
x64). The workflow takes the latest HiGHS release, by now 1.16.0, for the
twin and for `check/instances`, so a flag that crest shares with HiGHS
1.15.1 but not with 1.16.0 now counts as crest-only.

| source | models | crest solves | crest flagged | of which crest-only |
|---|---|---|---|---|
| random | 2000 | 4000 | 145 | 25 |
| check/instances | 74 | 148 | 5 | 5 |
| MIPLIB collection, easy, <= 20k nonzeros | 245 | 490 | 5 | 1 |
| perturbed dispatch | 216 | 432 | 0 | 0 |

Triage:
- check/instances: three reproducers of HiGHS bugs fixed after 1.15.1
  (`issue-2900` and `issue-3461`, wrong answers of
  `strengthenInequalities`; `issue-3166`, a crash in symmetry detection).
  Crest had all three, v0.1.3 included, and now follows the fixes
  (docs/upstream.md). FUZZ_AFTER
- MIPLIB: neos-3083784-nive, as before (MIPLIB's reference value).
- random: the tiny-coefficient models again (docs/upstream.md). On most
  of the crest-only ones HiGHS 1.16.0 and SCIP now agree with each other
  and with an exactly feasible point (keys 1095, 147, 1179: crest and
  1.15.1 say infeasible; 1730, 501: a wrong optimum), so 1.16.0 has
  moved here. Keys 1576 and 1179 are infeasible only in builds that fuse
  multiply-adds (crest's default; HiGHS 1.15.1 built by clang for arm64),
  not in 1.16.0 built the same way. FUZZ_BISECT
- perturbed dispatch: none.

## Baseline

2026-10-16, crest `bench-std` (main 48f6190 + this harness), HiGHS 1.15.1
release, SCIP 10.0 (pyscipopt 6.3); 1 thread, 300 s; GitHub runners:
arm64 Neoverse-N2, x64 a mix of AMD EPYC 7763/9V45/9V74 and Intel Xeon
8370C/6973P (all solvers of an instance on the same one). Runs
38939685629 (MIPLIB, 32 shards per arch), 38939643071 (LP, 16) and
38939646677 (LP with `solver=ipm`, 16).

**MIPLIB 2017 benchmark** (240 instances; primal integral over the 233
with a known objective, i.e. MIPFEAS at 300 s)

| arch | solver | solved | wrong | killed | sgm time (s, shift 10) | sgm primal integral | with a solution |
|---|---|---|---|---|---|---|---|
| x64 | crest | **93** | 0 | 0 | **141.8** | **0.0879** | 211 |
| x64 | HiGHS 1.15.1 | 87 | 0 | 2 | 150.6 | 0.1035 | 203 |
| x64 | SCIP 10.0 | 74 | 0 | 0 | 164.3 | 0.1461 | 197 |
| arm64 | crest | **92** | 0 | 0 | **144.3** | **0.0920** | 208 |
| arm64 | HiGHS 1.15.1 | 82 | 0 | 2 | 151.2 | 0.1059 | 204 |
| arm64 | SCIP 10.0 | 73 | 0 | 1 | 167.0 | 0.1522 | 196 |

No wrong answer and no crash for crest. HiGHS 1.15.1 overran the limit
past the kill (510 s) on germanrr and neos-5114902-kasavu (both arches),
SCIP on satellites2-40 (arm64). SCIP's incumbent on mas74 is 7e-6 below the
optimum (feasibility tolerance; the check allows 1e-5).

On instances both solve, crest takes 0.875 (x64) / 0.894 (arm64) of HiGHS
1.15.1's shifted time and 0.77 of SCIP's. Where crest is weakest:

- against HiGHS 1.15.1: neos-2978193-inde (3-4x, both arches), decomp2
  (1.7-3.6x), neos-873061 (unsolved; HiGHS 133-155 s), neos-950242 (1.3-1.45x),
  and on arm64 only gmu-35-50 and neos-933966 (unsolved; HiGHS 16 s and 33 s);
  (neos-933966 at 2+ threads: fixed 2026-11-03, see performance.md "Early sync")
- against SCIP: neos-787933 and graph20-20-1rand (SCIP 2-6 s, crest
  unsolved), fhnw-binpack4-4 (infeasible; SCIP proves it in 12-17 s),
  CMS750_4, istanbul-no-cutoff, rocI-4-11, fast0507 (unsolved), decomp2,
  piperout-27, cod105, pk1, enlight_hard (2-4x); neos-3004026-krka has no
  crest solution for most of the limit (primal integral 1.3-2.0 vs 0.3-0.45).

**LP** (MIPLIB benchmark relaxations + 10 Mittelmann LPs; L1_sixm250obs, a
fixed-format file neither reads, has since been dropped)

| arch | method | solver | solved | sgm time (s, shift 1) | crest/HiGHS on both solved |
|---|---|---|---|---|---|
| x64 | default (dual simplex) | crest | **231** | **2.91** | 0.926 |
| x64 | default (dual simplex) | HiGHS 1.15.1 | 228 | 3.22 | |
| x64 | `solver=ipm` (IPX) | crest | 242 | 3.04 | 1.029 |
| x64 | `solver=ipm` (IPX) | HiGHS 1.15.1 | **243** | **2.89** | |
| arm64 | default (dual simplex) | crest | 231 | **2.89** | 0.920 |
| arm64 | default (dual simplex) | HiGHS 1.15.1 | 231 | 3.22 | |
| arm64 | `solver=ipm` (IPX) | crest | 241 | 3.05 | 0.995 |
| arm64 | `solver=ipm` (IPX) | HiGHS 1.15.1 | **242** | **3.04** | |

The `solver=ipm` rows are from 2026-10-17 (crest `ipm`, run
39003533706; was 3.26 against 2.98 on x64, 3.24 against 3.14 on arm64,
before the IPX matching and bounds-check work of docs/performance.md).
What remains is floating-point paths: the release is a gcc build (no FMA
on x64), and crest built like it (`libstdcxx no_fma`, generic x86_64)
takes its path on 243 of 249 LPs. On crest's paths neos-631710 (3.8x on
x64), neos-4763324-toguru and neos-5049753-cuanza (2x) are slower, and
irish-electricity's relaxation ends "Unknown" (the dual simplex on the
original LP after postsolve stops with 3e-5 primal infeasibilities; the
C++ app of the pinned fork does the same, the no_fma build ends
"Optimal" like 1.15.1). Simplex: slowest
are physiciansched3-3 and ns1760995 relaxations (time limit vs 250-260 s on
arm64) and cod105 (1.1-1.4x).

## Threads

2026-11-02, the GitHub runners (4 vCPUs: arm64 Neoverse-N2, 4 cores; x64
EPYC/Xeon, 2 cores with 2 hyperthreads each). SCIP runs its default,
sequential (one thread whatever the setting), as reference.

**MIPLIB 2017 benchmark** (240, 300 s; solved / sgm time, shift 10 s;
runs 41313764455, 41351818355, 41351816521 with crest main)

| solver | arm64 1 | arm64 2 | arm64 4 | x64 1 | x64 2 | x64 4 |
|---|---|---|---|---|---|---|
| crest main | 94 / 142.6 | 94 / 143.2 | 94 / 142.4 | 94 / 141.0 | 98 / 140.5 | 94 / 141.3 |
| crest, 3+ thread changes below | | | **99 / 136.8** | | | **96 / 138.4** |
| HiGHS 1.15.1 release | 82 / 151.6 | 82 / 151.1 | 82 / 150.4 | 87 / 151.7 | 89 / 150.4 | 86 / 150.2 |
| HiGHS 1.15.1, crest's flags (`highs-src`) | | 89 / 149.5 | 88 / 148.8 | | 90 / 147.3 | 89 / 146.6 |
| SCIP 10.0 (sequential) | 74 / 167.1 | | | 73 / 164.9 | | |

Neither crest main nor HiGHS gets anything from more threads on MIPLIB:
at the default gap (1e-4) there is no concurrent helper (it needs
`mip_rel_gap >= 1e-3`), and the parallel tree search is off unless
`parallel=on`; the threads only race IPX at the root and detect symmetry.
Built with crest's flags (x86-64-v3 / LTO), HiGHS solves 1-7 more and is
1-2.5% faster; crest stays ahead by 5-8 solved and 4-5% in time. The
`highs-src` row at 1 thread was not run.

**Dispatch** (all 36 instances, 8 seeds, 180 s, gap 1%; solved of 288 /
sgm time, shift 1 s; runs 41314262664, 41314264650, 41314266781 with main)

| crest | arm64 1 | arm64 2 | arm64 4 | x64 1 | x64 2 | x64 4 |
|---|---|---|---|---|---|---|
| main | 259 / 10.5 | 278 / 6.7 | 276 / 6.7 | 259 / 10.8 | 278 / 6.9 | 278 / 6.3 |
| 3+ thread changes | (main's path) | (main's path) | **280 / 5.8** | (main's path) | (main's path) | 275 / 6.5 |

Speedup 1 -> 2 -> 4 threads, main: 1.57x, then none (arm64); 1.58x, then
noise (x64; the 2-thread and 4-thread rows of two runs differ by
6.3-7.1 s). Threads 3 and 4 were idle.

**From 3 threads on** (main since this section): a concurrent LNS helper
on each thread but the main solver's, at most the physical cores less one,
each with its own seed, sharing the incumbents and bounds of the one pool;
and `parallel=choose` (the default) turns the parallel tree search on. With
1 or 2 threads every path is main's (checked: equal logs at 1 thread, and
with `mip_deterministic` at 2; paired against main at 2 threads, run
41445118849: arm64 1.018 [0.996, 1.041], x64 1.004 [0.978, 1.031], the
usual spread of two-thread timing). Paired against main at 4 threads (run
41445115653, 288 pairs per arch, shifted time ratio, 95% interval):
arm64 **0.879 [0.847, 0.912]**, solved 280 = 280; x64 1.013 [0.989,
1.037], where the 2 physical cores leave one helper (main's helper count)
and only the tree search is new. MIPLIB at 4 threads: +4 solved, sgm
-4.1% (arm64) / -1.5% (x64), all from the tree search (run 41369267711).

Rejected or not done:

- Helpers on all logical CPUs: on the hyperthreaded x64 runners 3
  helpers made the `dm_*` screen models 1.18-1.29x slower (time to 1%
  1.19x; run 41315073863) while arm64 gained; hence the physical-core cap.
- Helpers alone (no parallel tree search) at 4 threads: arm64 0.892
  [0.861, 0.925] on dispatch (run 41369266138), nothing on MIPLIB; the
  tree search adds MIPLIB's gain and does not cost dispatch (screen
  41351769764: arm64 6.88 -> 6.85 s, x64 6.95 -> 7.08 s).
- Diversifying the helpers beyond their seeds (move types, LNS vs deep
  dive), a 3-way root race (dual, primal, IPX) and parallel strong
  branching or separation: not tried; the measurements above show the
  idle cores, not these, as the waste.

## Where crest trails HiGHS (2026-11-03)

From the MIPLIB runs at 1-4 threads (41313764455, 41351818355, the
3-thread run, 41351816521; shifted time ratio against `highs-src`, or
the release at 1 thread), the instances crest loses on every arch and
thread count, decomp2 and neos-933966 aside: tbfp-network 1.75 (the root
sub-MIP finds 27.25 where HiGHS's finds the optimum), sct2 1.67 (x64; 3-7x
the nodes), neos-3402294-bobin 1.67 (2-4 threads; one thread is 0.56-0.75),
triptim1 1.57 (root only), neos-3083819-nubu 1.44, neos-873061 1.37,
neos-950242 1.37 (crest's cut loop at the root after the early orbitope
fixing), n5-3 1.31, 30n20b8 1.28, mzzv11 1.19, cod105 1.16, nw04 1.15.
All but triptim1 and cod105 take other paths (nodes, root LP iterations,
heuristic luck). LP set: supportcase12's relaxation runs 5x HiGHS's
iterations at seed 0 (bitmap solve order; seeds 1-3 and `hyper_dfs` are
even), triptim1's root LP twice HiGHS's (below).

- **Factor build elimination over slices** (kept): HFactor's kernel
  elimination loop indexed four arrays with a bounds check each; zipped
  slices and unchecked marks, same arithmetic. cod105 relaxation (M1):
  1.23x -> 1.01x HiGHS 1.15.1's cycles on its path, crest's own path
  0.81 instructions; MIPLIB at 1 thread (run 41585275001 against
  41602570525): solved 94 -> 95 (arm64), 93 -> 93 (x64), cod105 0.96
  (arm64); dispatch at 2 threads (run 41585277141, 16 seeds): arm64
  1.002, x64 1.003, solved 557 -> 557 / 554 -> 558.
- **Root LP Devex switch only where the column FTRAN is sparse**
  (rejected): on triptim1 the switch (`mip_root_lp_devex_density`) doubles
  the root LP's iterations at every seed tried (147-161k against 70-74k);
  the column FTRAN density there is 0.36 against the dispatch ramp and
  lambda models' 0.02-0.08. Declining the switch once where it is over a
  quarter of the DSE density: triptim1 0.61, but neos-3402294-bobin (where
  the switch is crest's 1-thread win) went from 146-166 s to the time
  limit, satellites2-60-fs and istanbul-no-cutoff slower; MIPLIB 1 thread
  (run 41602573844) solved 94 -> 94 / 93 -> 92, both-solved 0.993 / 0.999;
  dispatch 2 threads (run 41583812888) 0.994 / 1.000. The density at the
  switch does not tell triptim1 from bobin.
- **Root sub-MIP (tbfp-network, decomp2, neos-960392)** (no porting
  divergence; robustness tweak rejected): the root RENS, `solveSubMip`
  and their limits match HiGHS 1.15.1 line for line, and crest built with
  the oracle features (`hyper_dfs highs_dual_proofs`, oracle options)
  takes HiGHS's root path on tbfp-network to the same RENS sub-MIP (169
  cuts, 57281 LP iterations), which finds the optimum 24.16 as HiGHS's
  does. Default crest leaves that path at the first root LP (16670
  against 19943 iterations) and its RENS finds 27.25. Seeds 0-3 at 1
  thread (runs 41758882473, 41758888443, 41758894196, 41758900101):
  tbfp-network crest 196 nodes at seed 0 but 0-1 node at seeds 1-3
  (115-241 s), HiGHS 1.15.1 (`highs-src`) 9, 9-13, 1 and 105-150 nodes
  (seed 3: time limit on x64); decomp2 crest 138 nodes at seed 0 only,
  HiGHS 1 node at all four; neos-960392 1 node for both everywhere.
  Seed 0 is crest's unlucky seed on both, not a port bug. A second root
  RENS when the first improved the incumbent without closing the gap
  (commit b2fb968, branch rens-retry): on decomp2 (M1, seeds 0-7) it
  fires at seeds 0, 1, 5 and finds only one more unit each (-142 ->
  -143, -138 -> -139, -135 -> -136); seeds 0 and 5 then solve faster
  (198G -> 38G, 183G -> 77G instructions) on a changed path, seed 1
  55G -> 58G; on tbfp-network it never fires (the first sub-MIP spends
  the heuristic LP budget). MIPLIB 1 thread, the 61 x64 / 21 arm64
  instances that ran before the run was cut short (run 41760495763
  against 41758842359): solved 25 -> 25 / 6 -> 6, sgm 151.7 -> 157.6 /
  138.8 -> 138.5, path changes both ways (neos-873061 204 -> 120 s,
  neos5 87 -> 159 s, markshare_4_0, gmu-35-40 slower).
- **Early symmetry sync only with orbitopes** (rejected): the early sync
  (static orbitopes, no helper) installs the symmetries before the root
  cut loop; generators without orbitopes then change the root path
  (neos-960392 at 1 thread: 151G instructions and 18770 LP iterations
  against 99G and 10921 with `mip_symmetry_static_orbitopes` off).
  Using the early symmetries only when orbitopes were found (commit
  22dfcff, branch early-sym-orbitopes): neos-960392 117G (the detection
  is still paid), 15.3 -> 11.9 s (arm64), neos-933966 unchanged (1
  node), neos-950242 unchanged (the same path with the option on or off
  at seed 0). But the early generators are what three of PR #61's wins
  came from: MIPLIB 1 thread (run 41758848594 against 41758842359)
  arm64 solved 95 -> 95, sgm 141.5 -> 142.5: neos-873061 170 s -> time
  limit, neos-911970 3.6 -> 9.5 s, ns1208400 29 -> 53 s (x64 the same
  three; x64 95 -> 97 is fast0507, neos-3216931-puriri and
  tbfp-network finishing inside the limit on faster CPUs, while arm64
  keeps their paths). No cheap test
  tells neos-960392 from those (set packing rows: 1660 of 4745 rows
  there).
- **Root LP Devex switch: back to DSE after as many Devex iterations
  as rows** (kept, PR devex-revert): the switch is not crest's
  1-thread win on bobin at every seed (switch off fails only seed 0;
  4 seeds x 2 archs, runs 41757368416: sgm 93 s 8/8 on, 165 s 6/8
  off; satellites2-60-fs 268 s 5/8 on, 234 s 8/8 off), and Devex is
  worse than DSE in bobin's root LP too (14k iterations against 523),
  so a revert measured against DSE's rate before the switch also hits
  bobin (3x the pre-switch iterations: bobin 93 -> 151 s, 8/8 -> 6/8,
  run 41758466364). Per row it separates: dispatch root LPs take
  0.5-1.2 iterations per row in all, triptim1's Devex ten. triptim1
  root (M1, 1 thread) 939G -> 663G instructions (seed 1: 905G ->
  599G); 4 seeds (run 41763339279) bobin 88 s 8/8, satellites2-60-fs
  263 s 7/8. MIPLIB 1 thread (run 41769183228 against 41769176714):
  the same path on every instance both solve except triptim1 (113 ->
  81 s arm64, 137 -> 93 s x64); solved 95 -> 95 (arm64), 98 -> 94 (x64,
  all near the limit on a slower runner: same-path time ratio 1.005);
  LP set (41769202066 against 41769195808) the same path everywhere.
  Dispatch 2 threads, 16 seeds (run 41769214331): arm64 t1 1.001
  [0.999, 1.004], solved 558 -> 558; x64 0.999 [0.995, 1.002], 555 ->
  556; the root LP traces of the dispatch models are unchanged (they
  never revert). The per-instance t1 outliers (ovn_1148_g105 1.013,
  dm_full_pert_s1_noramp 1.021) are inside a main-vs-main control's
  spread (run 41799886659: up to 1.014, diverse solved 13 -> 12, 12 ->
  10).
- **Second cost perturbation on a dual degenerate stall** (rejected,
  branch stall-reperturb): supportcase12's relaxation at seed 0 (409k
  iterations, seeds 1-3 69-93k) is not numerical trouble or cycling:
  88% of the dual steps are exactly zero (Harris ties within the dual
  tolerance), pivots |alpha| >= 0.03, no trouble flagged; the dual
  objective crawls (-14417 -> -14348 over 180k iterations) while basic
  values grow to 1e10, and seed 1 has the same plateau (2e9) but
  escapes sooner. The primal blow-up is a symptom, so a trigger on it
  would fire on the good seeds too. Tried instead: after 2x rows phase 2
  iterations without 0.1% dual objective progress, perturb the costs
  again, 10x. supportcase12-relax seeds 0-3: 409k/93k/86k/69k ->
  88k/76k/91k/90k iterations (run 41811161419); LP set (41769207971)
  37 instances change path, those 0.87 (arm64) / 0.91 (x64), e.g.
  neos-1354092 209 -> 19 s, mzzv11 31 -> 7 s, cygnet to optimal, but
  rail01 57 s -> time limit (both archs) and kasavu (arm64); MIPLIB
  (41769189285) 15 paths change, 1.01 / 1.02 net of runner speed,
  wachplan and 30n20b8 solved -> time limit on both archs, ns1208400
  29 -> 80 s, cod105 110 -> 150 s; dispatch neutral (it never fires
  there). Perturbing 10x from the start (`dual_simplex_cost_perturbation_multiplier
  = 10`) takes supportcase12 to 46-53k at all four seeds (run
  41811157564) but changes every LP; not evaluated further.
- **The MIP's LPs unscaled from 10000 rows** (kept, PR mip-lp-unscaled,
  `mip_lp_scaling`; docs/performance.md): rejected on the way there:
  equilibration that ignores entries below 1e-2..1e-6 of their row or
  column maximum, or below 1e-3..1e-7 absolute (HTF root LP 92-181G
  against 227G scaled and 43-81G unscaled); max-value scaling (93G);
  column factors capped so a bounded column keeps a scaled range of
  1e-4..1 (166-193G); `allowed_matrix_scale_factor` 3-14 (92-225G);
  dropping coefficients whose summed |a|(u-l) per row is below 1e-8..1e-6
  with the row bounds relaxed to keep the model valid (199-267G; at 1e-8
  the LP blew up); CHUZC dropping every growth-test failure at once from
  the first or the eighth pass (root LP 47.6k -> 58.0k iterations at seed
  0, 268-274G against 239-264G at seeds 1-3; moot unscaled, where no
  CHUZC call takes a second pass); and no gate (all MIPs unscaled):
  MIPLIB-easy 1 thread (run 41929828549) both-solved time 1.046 against
  scaled, and a wrong dual bound on neos-911970 at seed 0 (55.52 against
  the optimum 54.76; seeds 1-6 right; 107 rows, so the gate keeps it
  scaled).
- **Pure LPs unscaled** (`simplex_scale_strategy = 0` as the LP default;
  rejected): LP set at 1 thread (run 41929832084, arm64) solved 232 -> 228
  (one OOM kill), both-solved shifted time 1.030 in favour of scaling,
  despite big wins (supportcase12-relax 236 -> 6 s, kasavu 280 -> 11 s,
  mzzv11 30 -> 7 s); supportcase42-relax's objective moves at the 2e-6
  level. The MIP gate above keeps scaling for pure LPs.
- **Scaled fallback when the unscaled root LP runs away** (rejected, not
  built): the idea was to start the MIP's first root LP unscaled and
  re-solve it scaled past some multiple of the rows in iterations, to win
  back uccase12 (MIPLIB 1 thread 23 -> 161 s with unscaled LPs; 2
  threads 13 -> 103 s locally). Its in-MIP root LP iterations per
  presolved row (main 2c6c50e, M1, 1 thread), unscaled against scaled:
  uccase12 1.61 against 0.35 (142k iterations, 88k rows). But the
  dispatch models reach 0.41-1.01 unscaled (ovn_* 0.80-0.91, dm_small
  up to 1.01), the HTF models 0.63-0.73, and other MIPLIB models go
  higher in both settings: satellites2-60-fs 1.10 unscaled against 5.03
  scaled, physiciansched6-2 1.18 / 0.95, neos-933966 2.21 / 2.21,
  triptim1 7.30 / 6.63, neos-5052403-cygnet 12.1 / 15.7. A threshold
  that spares the dispatch models (above 1.01) fires on uccase12 only
  after 89k or more of its 142k iterations, at most a quarter of the
  waste saved, and it also fires on triptim1, cygnet and neos-933966,
  where a scaled re-solve costs as much again for nothing. The largest
  primal infeasibility sum during the unscaled solve does not separate
  them either (uccase12 4e12, g03_eta1_L133 2.5e9, lambda_080458 5e8,
  satellites2-60-fs 3e8). ovn_0716_g158 (1 thread t1 1.19 with unscaled
  LPs, run 41960555578): the ovn ticks are the one dispatch family
  whose root LP takes more iterations unscaled (0.80-0.91 against
  0.61-0.75 per row) and unscaled LPs lose on pure-LP instructions on
  two of the five (ovn_0636 1.06, ovn_0652 1.23).
