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

## Screen first, then the full run

A candidate change goes through two stages, and the full benchmark runs
only for one that passes the first:

| Stage | Dispatch (bench-dispatch) | Standard (bench-std) | Cost |
|---|---|---|---|
| Screen | `screen` input, or a `bench-screen/*` branch: 10 instances (the three Lambda ticks, `hard_10-03_1340`, `diverse_10-01_0052` and the five `dm_*` models most sensitive to past changes), 8 seeds, 120 s, 8 runners per arch | `miplib-easy`, crest against main only | about 1/5 of a full run |
| Full | all 25 instances, 16 seeds, 180 s, 16 runners per arch, 1 and 2 threads | `miplib` (240), plus `lp` for LP changes | |

Before either, screen locally on a few models by instruction count
(`CRESTLINE_INSTR_CLOCK`). A screen that shows no gain, or a loss on any
motivating tick, ends the experiment: record it in the docs and move on.

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
