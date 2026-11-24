# Graph LNS and dispatch MILPs

Crestline was built for power system dispatch MILPs: unit commitment with
storage, where each period has on/off switches, charge/discharge rates,
state-of-charge chains and ramp limits. Their LP relaxations often have a
tight bound but a misleading vertex: a fractional switch lets a unit run
part-way all the time, which no integer schedule can do. Branch and bound
from that vertex makes slow progress, and local search moving one variable
at a time is defeated by the equality chains of continuous state-of-charge
and ramp variables. What finishes a solve at a loose gap (production runs
`mip_rel_gap = 0.01` with a 180 s limit) is a good incumbent.

Graph LNS is a primal heuristic for such models. It is in
`src/mip/lns.rs` (decision columns, neighbourhoods, move choice, flip
candidates) and `src/mip/graph_lns.rs` (the dives, neighbourhood branch and
bound and flip search), with the concurrent helper in
`src/mip/concurrent.rs`. It runs only for `mip_rel_gap >= 1e-3`, so solves
at the default gap are unaffected.

## The method

It works on a copy of the LP relaxation and a domain, with warm LP
re-solves.

**Decision columns.** An integer column whose every row has coefficient
±1 on it, only integral columns, integral coefficients and integral finite
sides is integral at every LP vertex once the other integers are fixed (for
example, |x_t - x_{t-1}| start-up indicators). Such columns are not fixed
or branched on: fixing them first would stop the search from branching on
the columns that determine them.

**Quick search** (after the first root LP):
- A dive from the LP point: fix the most integral decision columns toward
  it, a chunk per LP solve, each chunk going up to the first rounding that
  propagation rules out. A chunk whose LP is infeasible is undone together
  with the simplex iterate, so the dive goes on from the LP before it.
- Neighbourhoods: a breadth-first search over the variable/constraint
  graph from a seed column collects about 400 decision columns (on
  time-indexed models, a time window); the rest are fixed to the incumbent,
  and if the neighbourhood LP (warm from the last LP solved, not the root
  basis) can beat the incumbent, it is dived.
- A flip search polishes the incumbent. It runs first instead if the
  root dive left the incumbent within three target gaps of the bound, or
  if some decision column shares no row with another integer column (see
  "The flip search first").
- On large models it spends at most about one root LP's worth of LP
  iterations, so that where neighbourhood LPs are expensive (ramping
  models) the root cuts come early.

**Deep search** (after the root cuts, before the sub-MIP heuristics, only
if the quick search improved the incumbent and brought it within three
times the target gap of the bound; otherwise the model does not suit graph
LNS and the usual root heuristics run; but see `mip_graph_lns_deep_early`
below). Neighbourhoods are smaller and are
searched by a depth-first branch and bound with a node limit (300 LP
solves; a search that goes 100 without a new incumbent stops, see "A
neighbourhood search that stalls"), their size adapting to what that
limit exhausts. A UCB bandit
picks, among four moves, the one closing the most gap per LP iteration:
- BFS over all rows, searched by branch and bound;
- BFS over rows with at most four decision columns (chains, such as one
  unit over time), searched by branch and bound;
- flip search: with all decision columns fixed at the incumbent, flip
  binaries in order of the gain promised by their reduced cost, and pair a
  promising flip with an opposite flip in one of its rows (swaps); a move
  that did not improve waits until a column next to it changes (see "The
  flip search's memory of failed moves");
- BFS over all rows, dived.

Seeds are columns with a promising reduced cost at the incumbent, columns
where the incumbent disagrees with the LP solution, or random.

**Propagation screen.** The flip search first propagates a move's fixings
over the model rows, without an LP. On the hardest dispatch tick, 92% of
flip LPs had been infeasible at about 2.5 simplex iterations each, so the
fixed cost of a solve was all they cost. A move ruled out this way counts
as a quarter of an LP solve in the flip search's budget. A flip search
resumes where the last one from the same incumbent stopped.

**Stopping.** The quick search stops after 5 neighbourhoods that do not
close 5% of the gap, the deep search after 10 that do not close 1% of what
separates the incumbent from the target gap; both also stop on an LP
iteration budget or when the incumbent reaches the target gap. If the deep
search pays, further rounds alternate with the tree search, where strong
branching then starts with a smaller budget (10000 LP iterations rather
than 100000).

## A second core

With `threads` other than 1 and graph LNS active, `mip_concurrent_helper`
(on by default) uses a second thread, which makes the solve
non-deterministic:
- IPX with crossover races the dual simplex on the first root LP (models
  with at least 10000 nonzeros); the first to finish interrupts the other.
  The log says which won ("Root LP: IPX won the race on two threads").
- A helper thread runs its own root cuts and graph LNS on a copy of the
  presolved model with its own seed, exchanging incumbents with the main
  solver; the main solver takes the helper's bound while at the root and
  its cuts once the helper's cut loop is done ("Concurrent LNS helper
  thread started").
- While the gap after the root cuts is over three target gaps, the main
  solver runs the deep search too, rather than its sub-MIP heuristics and
  the tree (see "With a helper and a wide gap, the main solver joins the
  deep search").
- `mip_concurrent_crossover` (off by default): the two search independently
  until the helper crosses its incumbent with the main solver's: the
  integer columns where they agree are fixed and the rest solved as a
  sub-MIP.

The helper starts its root LP from the main solver's basis, so its own
first root LP count is about 0; it takes the main solver's count instead,
since the LNS LP iteration limits and the deep-search and flip budgets scale
with it. Before (2026-11-20) a helper on a model whose root LP takes ~11k
iterations ran every neighbourhood LP at the 1000-iteration floor, where
they all stopped, and found nothing in 180 s; on a gas-boiler dispatch
model at 2 threads, 1% gap, 180 s this took the runs reaching the gap from
1 of 8 seeds to 5 of 8.

The time limit reaches every solver of the solve: a sub-MIP (at any depth,
in the main solver or a helper) gets the smaller of its own budget and its
parent's remaining time, and a helper the time left when it starts. When
the main solver ends it stops its helpers and waits for them, so the stop
also ends the helpers' sub-MIPs (at their next limit check), and presolve,
which reads the clock only every 1024 reductions in HiGHS, also reads it
every 1024 limit checks and inside the cover loop of coefficient
strengthening, where a single row can take millions of rounds. Before
this (2026-11-02), two solves at 2 threads ran far past their limits (one
for 2.5 h of a 180 s limit), last seen busy in presolve of sub-MIPs nested
four deep.

## When the quick search never searched (`mip_graph_lns_deep_early`)

The deep search's gate reads "the quick search did not improve" as "the
model does not suit graph LNS". That holds only if the quick search tried
neighbourhoods. On `uccase9` (MIPLIB 2017, unit commitment) its root dive
from the LP point uses up the whole quick budget (the dive was not
checked against it), so not one neighbourhood is tried, the gate stays
shut, and one thread keeps the randomized-rounding incumbent (63% gap) for
180 s while the root cut loop runs. With the option:
- the quick search's dives stop at its LP iteration budget, like its
  neighbourhoods;
- if the quick search did not improve the incumbent and the gap is
  above three times the target, the main solver runs the deep search
  right away (before the root cuts) on one root LP's worth of LP
  iterations (at least 5000), and again while a round closes 1% of the
  gap. A helper thread, when there is one, runs the deep search anyway,
  so the option does nothing there.

Results (see [performance.md](./performance.md)): no change on the
dispatch suite on either architecture (it does not fire there; the dive
cap makes `dm_full_pert_s1_ramp` about 2% faster); on uccase9 it fires on
2-3 of 16 seeds, which end near 13200 instead of near 30000; on 4 of 47
MIPLIB instances it costs 1.01-1.41 times the instructions. So it stays
off: the gain is real but narrow, and gates that fire on more uccase9
seeds cost too much on general MIP.

## MIPLIB unit commitment models

`uccase9`, `unitcal_7`, `irish-electricity` and `buildingenergy` (MIPLIB
2017, public; the `bench-dispatch` workflow downloads them for the
instance glob `miplib/*.mps.gz`) are unit commitment and energy models
with features the dispatch ticks lack. The MIPFEAS notes had crest find
no solution in 180 s on the last two; that was the loaded M1. On the
runners (one thread, 1%, 180 s, 16 seeds, median over seeds, x86_64 /
arm64):

| | first solution | gap at 180 s | two threads: gap |
|---|---|---|---|
| `unitcal_7` | 5.9 / 6.8 s | 0.27% (solved) | 0.28% / 0.30% |
| `uccase9` | 9.0 / 8.9 s | 61% / 63% | 11.2% / 11.1% |
| `irish-electricity` | 72 / 90 s | 3.39% | 2.94% / 2.95% |
| `buildingenergy` | 134 / 149 s | 1.48% / 1.52% | 1.52% / 1.54% |

- `uccase9`: see above (on two threads the helper's ungated deep search
  does the work).
- `irish-electricity`: graph LNS never starts: its root dive fails (a
  rounding and its opposite both infeasible, after 7.4k LP iterations),
  and randomized rounding and feasibility jump find nothing, so the first
  solution comes from RENS after the root cut loop (about 16 rounds).
  HiGHS 1.12 found its first the same way (57 s on a Core Ultra 9).
- `buildingenergy`: the root LP is most of the time: 241k dual simplex
  iterations (1155G instructions on the M1; HiGHS 1.15.1 takes 245k
  iterations and 104 s on a Core Ultra 9). IPX is far worse here (9
  interior point iterations in 2000G instructions, mostly its starting
  basis), as is the primal simplex. No cheap fix.

## Cheaper LP re-solves

Graph LNS and branch and bound solve many LPs of a few iterations each, so
the fixed cost of a solve dominates. In the MIP's LP relaxation (all
iteration counts unchanged):
- dual steepest-edge weights are kept over basis changes and cut additions
  for LPs above 20000 rows (`simplex_dse_exact_init_max_rows`) instead of
  being recomputed by one BTRAN per row;
- the simplex random vectors are kept (`simplex_keep_random_vectors`) and
  only the absolute primal and dual infeasibilities are assessed after a
  solve (`full_lp_kkt_check = false`);
- after bound changes alone, the dual values of the last dual simplex solve
  are kept if it ended optimal without perturbed or shifted costs (basis
  and costs checked by hash): 4.9% fewer instructions over 600 re-solves
  of a dispatch tick after single fixings;
- after an infeasible LNS LP, the previous simplex iterate (factor and edge
  weights) is restored rather than refactorized;
- the scaled matrix is kept between solves while the matrix and scale
  factors are unchanged, the factor's solve error is not recomputed for
  an unchanged factor, and passes whose results only feed the log are
  skipped without output (see [performance.md](./performance.md)).

Cut separation also skips an aggregation whose transformation has no
integer column at a fractional value, which cannot give a violated cut
(72% of the calls on the hardest tick).

## Options

| Option | Default |
|---|---|
| `mip_heuristic_run_graph_lns` | true |
| `mip_concurrent_helper` | true |
| `mip_concurrent_crossover` | false |
| `mip_root_lp_devex_density` | 0.7: the first root LP switches from DSE to Devex once its DSE FTRAN results are this dense (inf: never; see [performance.md](./performance.md)) |
| `mip_graph_lns_cost_perturbation` | false: graph LNS's LP solves without dual cost perturbation (true as in HiGHS; see [performance.md](./performance.md)) |
| `mip_root_lp_ipx` | false (see [performance.md](./performance.md)) |
| `mip_root_lp_effort_from_basis` | false (see [performance.md](./performance.md)) |
| `mip_graph_lns_deep_early` | false (see above and [performance.md](./performance.md)) |
| `mip_symmetry_static_orbitopes` | true (packing orbitopes in a static order; see [performance.md](./performance.md)) |
| `mip_lp_scaling` | false: the LPs of MIPs of at least 10000 rows (before presolve) are solved unscaled (true: scaled, as in HiGHS; see [performance.md](./performance.md)) |
| `mip_graph_lns_flip_first` | true: the quick search starts with its flip search when the root dive leaves the incumbent within three target gaps of the bound, or when some decision column shares no row with another integer column (false: it ends with it, as the C++; see "The flip search first") |
| `mip_graph_lns_flip_memory` | true: the flip search leaves a move that did not improve untried until a decision column in a row of one of its columns has changed (false: as the C++; see "The flip search's memory of failed moves") |
| `mip_graph_lns_stall_nodes` | 100: graph LNS stops a neighbourhood's tree search that has gone this many LP solves without a new incumbent (0: only at the node limit, as the C++; see "A neighbourhood search that stalls") |
| `mip_heuristic_min_gap_ratio` | 1.1: with `mip_rel_gap` of at least 0.001, no root reduced cost, RENS or RINS heuristic while the gap is at most this many times the target gap (0: no such rule, as HiGHS; see "A gap that is nearly closed") |
| `mip_graph_lns_deep_rounds` | true: after its deep search at the root, graph LNS runs further deep searches while the gap is open and each closes 1% of it (false: one deep search, then the sub-MIP heuristics and the tree) |
| `mip_lp_refactor_clock` | true: the MIP's LP relaxation is solved with `simplex_refactor_clock_over_rebuilds` (false: as in HiGHS; see [performance.md](./performance.md)) |
| `simplex_refactor_clock_over_rebuilds` | false (true in the MIP's LP relaxation): the dual simplex's clock for refactorization keeps running over rebuilds that do not refactor |
| `simplex_dse_exact_init_max_rows` | no limit (20000 in the MIP's LP relaxation) |
| `simplex_keep_random_vectors` | false (true in the MIP's LP relaxation) |
| `full_lp_kkt_check` | true (false in the MIP's LP relaxation) |

To compare options or builds on these models: the `bench-dispatch`
workflow (paired A/B runs on x86_64 and arm64 runners, `bench/dispatch.py`;
see "Method: paired runs without machine load" in
[performance.md](./performance.md)).

## Results

These were measured on the C++ implementation of the same algorithms,
before the port; Crestline follows it iteration for iteration (see
[performance.md](./performance.md): about 0.85 of its MIP cycles). Single
thread, `mip_rel_gap = 0.01`, against HiGHS 1.15.1, on a suite of 23
dispatch MILPs. CPU times were measured on a busy machine; compare them
rather than reading them as absolute.

| Dispatch suite (23 instances) | HiGHS 1.15.1 | with graph LNS |
|---|---|---|
| reach 1% within the 300 s (wall) limit | 14 | 23 |
| total CPU time | over 2264 s | 313 s |
| hardest tick (3c1b60d6) | time limit, gap 2.03% | 26 s |
| `_wind185` | time limit | 9.7 s |
| `dm_full_pert_s1_ramp` | time limit, 9.3% | 45 s |
| `dm_full_randsoc_ramp` | time limit, no solution | 57 s |
| `lambda_..._080458` | 107 s | 17 s |
| `dm_small_pert_s1_ramp` | 106 s | 4.4 s |

- Every instance HiGHS 1.15.1 solves is at least 4.5 times faster (9.2
  times at the median).
- The hardest tick over 16 random seeds: all reach 1%, median 160G
  instructions (81G to 231G); HiGHS 1.15.1 reaches 1% in 300 s with none.
- Held-out set (two copies of each instance with every distinct objective
  coefficient scaled by its own lognormal(0, 0.15) factor): 46 of 46 reach
  1% in 300 s against 41 of 46, with 0.108 of the instructions (geometric
  mean).
- Two threads: the main thread retires 0.70 (geometric mean, 46 runs) of
  the single-thread solve's instructions.
- General MIP is not slowed: on a 31-instance MIPLIB regression set
  (300 s, 2 seeds, shifted geometric mean of instructions over runs both
  solve), 0.919 at the default gap (graph LNS off) and 0.916 at
  `mip_rel_gap = 0.01`.

Not every tick is primal-limited. On one (`hard_10-03_1340`) the root
bound after cuts and the best solution known are 1.13% apart: certifying
1% needs a better bound, not a better incumbent, and no change to the
search reaches it in 180 s. There the incumbent at the time limit is what
counts.

## Time-window neighbourhoods (rejected, 2026-10-17)

SCIP's GINS rolling-horizon mode and Kempke et al. (arXiv:2502.19012) walk
a time-indexed model in contiguous windows. Tried here as
`mip_graph_lns_time_windows` (removed), with the time axis found from the
matrix alone: the levels of a breadth-first search over the
variable/constraint graph from a pseudo-peripheral column (George-Liu
restarts). On the dispatch models, these levels are the periods. On
`dm_full_pert_s1_ramp` there are 59 levels for 56 steps, and level L
holds the switches of period 56 - L. The hard ticks have 60 levels; on
3c1b60d6 there are 99, because a tail of 38 levels has 2 columns each.
Windows of the decision columns, ordered by level, were swept with half
overlap from a random start. They were two more moves for the deep
search's bandit (searched by branch and bound, and dived), and every
other neighbourhood of the quick search.
- mode 1: the window's decision columns free, as in the other
  neighbourhoods (continuous columns stay free everywhere);
- mode 2: also every column outside the window's levels fixed at the
  incumbent, so the chains are pinned at both ends.

Runners, one thread, 16 seeds, 180 s, against main (2fd8779):

| | x86_64 | arm64 |
|---|---|---|
| mode 1: solved models, time (geometric mean) | 1.013 | 1.006 |
| ... `dm_small_pert_s1_ramp` | 1.491 [1.128, 1.971] | 1.483 [1.134, 1.939] |
| ... `dm_medium_randsoc_noramp` | 0.886 [0.823, 0.954] | 0.877 [0.816, 0.944] |
| ... `dm_full_pert_s1_ramp` | 0.941 [0.892, 0.993] | 0.945 [0.894, 0.999] |
| ... runs solved (of 400) | 373 -> 371 | 372 -> 371 |
| ... `hard_10-03` gap at 180 s / mean primal gap | +0.012 ± 0.072 / -0.003 ± 0.088 | +0.011 ± 0.073 / +0.030 ± 0.091 |
| ... `diverse_10-01` gap / mean primal gap | +0.024 ± 0.039 / +0.023 ± 0.046 | +0.010 ± 0.046 / +0.033 ± 0.038 |
| mode 2: solved models, time | 1.070 | 1.066 |
| ... 3c1b60d6 | 1.338 [1.011, 1.770] | 1.360 [1.016, 1.820] |
| ... `diverse_10-01` gap | +0.085 ± 0.063 | +0.074 ± 0.066 |

Likely why (not instrumented further): a breadth-first neighbourhood from a seed already covers the
periods around it, since each period's rows link all its units at
distance one and the chains link neighbouring periods. So a window is
much like the BFS neighbourhoods. What it lacks is their seeds (reduced
cost promise, disagreement with the LP), and the windows' share of the
budget went to blind sweeps. Pinning the continuous columns makes each
window LP a smaller problem, but the next neighbourhood's LP then has to
leave the pinned point (16k dual simplex iterations after one pinned
window on `dm_full_pert_s1_ramp`, against about 3-5k), and the restricted
windows improve less. Two threads were not run: the one-thread result
already fails the acceptance rule (a regression on one model on both
architectures, no gain on the time-limited ticks).

## A perturbation bandit (rejected, 2026-10-17)

The deep search already schedules its four moves with a deterministic
bandit (UCB on the gap closed per LP iteration, an exponential average,
effort counted in LP iterations, not time), as Hendel (MPC 2022),
Chmiela et al. (CPAIOR 2023) and Balans (IJCAI 2025) propose. The
missing arm was `mip_graph_lns_cost_perturbation`. That option is not a
neighbourhood: it switches the dual simplex cost perturbation of graph
LNS's LP solves. Tried as `mip_graph_lns_bandit_perturbation` (removed):
a second two-armed UCB, with the same reward, chooses for each move
whether its LPs are perturbed.

Runners, one thread, 16 seeds, 180 s, against main (2fd8779):

| | x86_64 | arm64 |
|---|---|---|
| solved models, time (geometric mean) | 1.014 | 1.031 |
| intervals below 1 / above 1 (of 24) | 0 / 3 (all <= 1.008) | 0 / 3 (all <= 1.012) |
| runs solved (of 400) | 371 -> 373 | 372 -> 371 |
| `hard_10-03` gap at 180 s / mean primal gap | +0.017 ± 0.045 / +0.011 ± 0.052 | +0.030 ± 0.058 / +0.027 ± 0.082 |
| `diverse_10-01` gap / mean primal gap | +0.007 ± 0.044 / +0.007 ± 0.032 | +0.008 ± 0.046 / -0.018 ± 0.031 |

The off arm is already the right one on every model (see
[performance.md](./performance.md): no model is worse without the
perturbation), so the bandit can only pay for exploring the perturbed
arm. A run has about 30-50 deep moves, too few for the arms to
separate. Two threads were not run.

## Time-ordered fix-propagate-repair (rejected, 2026-10-17)

Salvagnin, Roberti and Fischetti (MPC 2025) fix the integers one at a
time with propagation, backtracking and a WalkSAT repair. Spoorendonk
(arXiv:2609.22938) ported FPR, Feasibility Jump, LocalMIP and Scylla into
HiGHS 1.15.1. Their FPR found the first solution on 16 of 233 MIPFEAS
instances and the best at 600 s on 2. Scylla and the LP-guided FPR dive
never gave an accepted incumbent in full runs, so they ship disabled.
Crestline's pinned HiGHS has Feasibility Jump only. Graph LNS's root dive
is already an LP-guided fix-and-propagate dive, so it was not redone.
LocalMIP and WalkSAT-style repair were left out: their single-variable
moves are what the equality chains defeat. What was tried
(`mip_heuristic_run_fpr`, removed) is the LP-free kernel in its
depth-first mode:
- after randomized rounding at the first root LP, fix the integer
  columns in time order (the BFS levels of a pseudo-peripheral column,
  from the matrix, not names), so that propagation runs the chains
  forward like a simulation;
- fix each to the rounded LP value (lock-roundable ones the safe way);
- if propagation rules a fixing out, try the other value, else flip the
  latest decision with an untried alternative (at most 2n + 100
  fixings);
- an LP then sets the continuous columns.

Local probe (M1, seed 0, the 25 dispatch models): all integers fixed on
12. The final LP is feasible on 7 (the 4 small models among them, and the
3 lambda ticks). On the 5 ramp/medium models where every integer was
fixed, the LP is infeasible: bound propagation over the ramp and state of
charge rows misses what the LP sees. On the other 13, the budget ran out
backtracking. Where it succeeds, graph LNS beats its point within a
second (3c1b60d6: FPR 3.49% at 6.1 s, the graph LNS dive 2.17% at 6.5 s).
On MIPLIB's unit commitment models it succeeds on uccase9 (32897,
against 29842 from randomized rounding) and fails on irish-electricity,
unitcal_7 and buildingenergy.

Runners, one thread, 16 seeds, 180 s, against main (2fd8779):

| | x86_64 | arm64 |
|---|---|---|
| solved models, time (geometric mean) | 1.041 | 1.045 |
| intervals above 1 (of 24) | 22 | 23 |
| ... the small models | 1.17-1.31 | 1.18-1.31 |
| runs solved (of 400) | 371 -> 370 | 372 -> 372 |
| `hard_10-03` gap at 180 s / mean primal gap | +0.001 ± 0.003 / +0.004 ± 0.024 | +0.008 ± 0.013 / +0.042 ± 0.030 |
| `diverse_10-01` gap / mean primal gap | +0.001 ± 0.003 / +0.141 ± 0.031 | -0.001 ± 0.001 / +0.188 ± 0.022 |

The paths after it are the same (the gaps at 180 s do not change). What
it adds is its own time, before graph LNS starts. Two threads were not
run.

## Storage cuts (measured, not implemented, 2026-10-17)

Elgersma et al. (arXiv:2411.17484) and Morales (arXiv:2506.21470) give
inequalities for storage that stop the LP from charging and discharging
at once, plus multi-period trajectory inequalities.

**The structure is not theirs.** In the dispatch models a storage module
has `soc(t+1) = l soc(t) - e discharge(t) + a switch(bank, t)`, with `l`
close to 1 (0.998) and `e` = 0.5 or 1. Discharge is continuous and
bounded, between two fractions of the state of charge. Charging is the
bank's binary switch at a fixed rate, shared by the 4-8 modules of the
bank. There is no charge/discharge mode binary, and no row forbids
charging while discharging: the MIP allows it, so cuts that exclude it
would be invalid here. What could still apply is the multi-period part:
capacity-limited trajectories of a binary, fixed-rate charge.

**Detection from the matrix works.** It needs no names:
- a state column is a continuous column in exactly two equality rows,
  with opposite signs, neither row holding more than 4 such columns
  (which rules out balance rows such as the heat balance);
- a recurrence row holds two state columns of opposite sign;
- chains link recurrence rows through their state columns.

This finds 201-202 chains of 54-55 rows (the full horizon) on
`hard_10-03`, `dm_full_pert_s1_ramp` and 3c1b60d6, with the charge
binary in 51-60% of their rows. On uccase9 and unitcal_7 it finds
nothing (no misfire).

**No root bound to gain.** The test, done offline: the bank-level and
multi-period aggregations of the recurrence rows (sums over a bank's
modules, and over windows of 1, 2, 4, 8 and 16 periods; about 7000 rows)
were added to the model as rows. Crest's path aggregation and cMIR
separation start from every row, so any MIR cut on those aggregations is
within its reach. Root dual bound (node limit 1, M1, mean of seeds 0-2;
the per-seed spread in brackets):

| | original | with the aggregations |
|---|---|---|
| `hard_10-03` (best known -248801) | -251629 [145] | -251663 [81] |
| `diverse_10-01` | -250235 [81] | -250185 [78] |
| `dm_full_pert_s1_ramp` | -323487 [166] | -323541 [0] |
| `dm_medium_prices_noramp` | -150644 [42] | -150646 [98] |

Every change is within the seed spread (the largest, +0.02% on
`diverse_10-01`), against a root gap of about 1.1% on the bound-limited
ticks. Adding bank and module windows together (45k rows) left
`hard_10-03` at -251744 on seed 0. So no separator was written, and no
runner time was spent.

## Warm starts (`mip_start_trust_radius`, rejected, 2026-10-17)

Recurring dispatch solves could start from the previous tick's plan,
shifted one period. Today a start is handled as in HiGHS
(completeSolutionFromDiscreteAssignment): a feasible one becomes the
incumbent, otherwise its integers are fixed and the rest solved (an LP, or
a sub-MIP of `mip_max_start_nodes` nodes), and if that is infeasible the
start is discarded. A shifted plan often breaks one link of a
state-of-charge chain, so it is discarded exactly when it is nearly right.

Tried: `mip_start_trust_radius` (off at -1; 0: max(10, 2% of the start's
binaries)). When the start was not completed, after the root LP a sub-MIP
of the model plus one local branching row (at most k of the start's
binaries changed, the others free) ran to its first solution, with k
doubled up to three times and each sub-MIP capped at the time spent so far
(presolve off, so the root basis is kept). Until there was an incumbent the
deep graph LNS centred its neighbourhoods on the start.

It repaired most starts (for example 8 of 3111 binaries changed on
`dm_full_pert_s2_ramp` from `pert_s1`), but the sub-MIP costs about a root
solve with cuts, and graph LNS from the LP point finds a better incumbent
than the repaired start in a fraction of that. It never helped, so it was
removed.

Measured with the `bench-dispatch` workflow (input `starts`; the start
files are made in the runner and never uploaded): 180 s, 1% gap, 16 seeds,
x86_64 and arm64. Starts: **sibling**, the best solution (0.1%, 60 s) of
the `pert_s1` variant of the same size and ramp given to `pert_s2`,
`prices`, `randsoc`, `hardpert` and `windlull`, and `lambda_..._142050`
and `_wind185` to each other (15 instances); **shift**, each `dm_*`
instance's own best solution shifted one period, with the last period and
`soc(..._0)` left out (20 instances). Every run of every arm reached 1%.
t1: geometric mean ratio of the time to within 1% of the best known
(95% interval); pg: mean primal gap over the limit, % (difference ± 95%
half-width).

| start, threads | arch | today / no start: t1 | pg | trust region / today: t1 | pg |
|---|---|---|---|---|---|
| sibling, 1 | x86_64 | 0.809 [0.732, 0.895] | -0.45 ± 0.17 | 2.458 [2.301, 2.626] | +2.37 ± 0.50 |
| sibling, 1 | arm64 | 0.819 [0.744, 0.900] | -0.40 ± 0.15 | 2.417 [2.258, 2.588] | +2.30 ± 0.46 |
| sibling, 2 | x86_64 | 0.843 [0.777, 0.914] | -0.22 ± 0.08 | 2.546 [2.389, 2.713] | +1.73 ± 0.26 |
| sibling, 2 | arm64 | 0.851 [0.787, 0.919] | -0.23 ± 0.09 | 2.516 [2.357, 2.686] | +1.88 ± 0.27 |
| shift, 1 | x86_64 | 0.994 [0.991, 0.997] | -0.00 ± 0.00 | 3.246 [3.158, 3.335] | +3.79 ± 0.62 |
| shift, 1 | arm64 | 0.992 [0.987, 0.997] | -0.01 ± 0.00 | 3.268 [3.162, 3.378] | +3.76 ± 0.57 |
| shift, 2 | x86_64 | 0.988 [0.983, 0.994] | -0.01 ± 0.00 | 3.405 [3.357, 3.454] | +2.59 ± 0.28 |
| shift, 2 | arm64 | 0.997 [0.993, 1.000] | -0.01 ± 0.00 | 3.460 [3.402, 3.519] | +2.58 ± 0.26 |

The gap at the limit did not change in any comparison (every
difference within ±0.003%).

- Today's handling already pays on siblings: where the fixed problem
  is feasible (`dm_small_prices`, `dm_full_pert_s2_ramp`, `_wind185`)
  the start is completed and the trust region does not run (t1 ratio
  1.00). Shifted starts are always discarded (their fixed problem is
  infeasible in presolve), so today equals no start.
- Wherever the trust region runs it is slower on every instance: 1.7-4.1
  times the time to 1% of the best (the hard tick `lambda_..._142050`
  from `_wind185`: 1.83 / 1.76, its start 193-196 binaries from feasible,
  found only at the fourth radius).
- The self-shift proxy also shifts decisions against unshifted prices,
  so it overstates how far a real previous tick is. Whether real
  consecutive ticks are closer is still open: `dispatch.py starts`
  and the `@` start configs are kept for that test.

## Where the gap is on the time-limited ticks (2026-10-18)

`hard_10-03_1340` and `diverse_10-01_0052` end at 180 s above 1%.
- **CPLEX** (180 s, one thread, from its logs): on `hard_10-03` its
  bound at 180 s (-251669) is weaker than crest's root bound (-251629);
  on `diverse_10-01` its tree moves its bound from -250665 to -250094,
  past crest's root (-250235). Its incumbents are -247957 (1.50%) and
  -246893 (1.30%).
- **References:** crest at `mip_rel_gap = 0.001` (graph LNS on), two
  threads, 2 h, seeds 0-3, arm64 runners. Every run still improved its
  incumbent after 40 min (the last improvement came at 2600-7000 s).
  At `mip_rel_gap = 1e-4` graph LNS is off and the incumbents after 2 h
  are worse than one-thread 1% runs at 180 s, so references need the
  looser gap.

| | `hard_10-03` | `diverse_10-01` |
|---|---|---|
| best solution in 2 h | -248935 | -248063 |
| best bound in 2 h | -251040 | -249717 |
| crest, one thread, 180 s: root bound / incumbent (median, 16 seeds, M1) | -251629 / -247777 | -250235 / -247295 |
| ... incumbent part of the gap, at least | 0.47% | 0.31% |
| ... bound part, at least | 0.24% | 0.21% |
| ... not yet attributed (the optimum lies in between) | 0.85% | 0.67% |
| two threads, 180 s: incumbent (4 seeds) | -248107 to -248502 | -247475 to -247802 |

1% needs the bound within 1% of the incumbent. On `diverse_10-01` the
root bound is enough for any incumbent at or below -247757: the best
solution known is 0.12% beyond that, so this tick is primal-limited. On
`hard_10-03` even the best solution known needs a bound of -251424, 205
above the root bound, so it needs both.

**Who finds the incumbents** (one thread, 180 s, 16 seeds per tick,
instruction clock, logs at `log_dev_level = 3`). Graph LNS finds every
incumbent; branch and bound and the other root heuristics find none (one
shifting solution on `diverse_10-01`). Shares of the objective gained
after the first incumbent (the root dive's, at about 6 s):

| | `hard_10-03` | `diverse_10-01` |
|---|---|---|
| quick search dives | 46% | 56% |
| deep search at the root: flips / short rows / BFS | 25% / 13% / 9% | 25% / 8% / 5% |
| graph LNS rounds during the tree search | 2% | 1% |
| found after 60 s | 20% | 9% |
| last improvement (median) | 148 s | 140 s |

There are 1-3 deep searches per run (median 2): the first, at the root,
takes most of the time; the tree search then gets about one turn (seed 0
of `hard_10-03`: 104 nodes from 133 s to 164 s, 23k strong branching LP
iterations, no incumbent, 0.008% of bound), and a second round found 13
improvements in the last 16 s.

**A smaller tree share** (`mip_graph_lns_tree_share`, removed): the tree
search's LP iterations before the next graph LNS round, per LP iteration
of the last round (1 as before). Screen (bench-dispatch run 39097153028:
the 10 screen models, 8 seeds, 120 s, one thread), against main:

| | x86_64 | arm64 |
|---|---|---|
| share 0: solved models, time | 1.001 | 1.003 |
| share 0.25: solved models, time | 1.004 | 1.004 |
| `hard_10-03` gap at 120 s, share 0 / 0.25 | +0.021 ± 0.050 / +0.010 ± 0.055 | +0.019 ± 0.048 / +0.009 ± 0.056 |
| `diverse_10-01` gap, share 0 / 0.25 | +0.019 ± 0.075 / +0.002 ± 0.042 | +0.025 ± 0.080 / +0.010 ± 0.044 |

No change: with one or two tree turns per run, the share moves little
time. The time that matters is the first deep search's, and the speed of
graph LNS itself.

## Sub-MIP heuristics alongside graph LNS (rejected, 2026-10-22)

**Profile** (macOS `sample`, `diverse_10-01`, one thread, seed 0, 180 s):
graph LNS 20% of the samples; branch and bound with its strong
branching 23%; root separation 7%; and 26% in sub-MIPs, the root
reduced-cost and RENS sub-MIPs after the deep search and RINS after the
tree's dives (nested: the sub-MIPs run their own). Graph LNS itself has
no sub-MIP: its neighbourhoods are re-solves of a copy of the LP
relaxation. The heuristic effort budget, which graph LNS's LP iterations
count against, lets the sub-MIPs run only where the deep search stopped
early: 4 of the 32 one-thread runs of the attribution set, where they took
21-65 s and found nothing (no sub-MIP incumbent in any run). On two
threads the main solver runs them every time (23 s and 57 s on seed 0).

**`mip_graph_lns_skip_root_submips`** (removed): no root reduced-cost,
RENS or tree RINS sub-MIPs while the deep search's root round paid or a
concurrent LNS helper runs.
- Local, one thread, instruction clock, 8 seeds of the two ticks, 4 of
  the five sensitive models and the Lambda ticks: identical except
  `hard_10-03` seed 2 (-247098 -> -247266).
- Screen (runs 39116283472 and 39116288052; 10 models, 8 seeds, 120 s):

| | x86_64 | arm64 |
|---|---|---|
| one thread: solved models, time | 1.006 | 0.999 |
| ... `hard_10-03` / `diverse_10-01` gap | -0.009 ± 0.021 / -0.001 ± 0.003 | -0.018 ± 0.041 / -0.003 ± 0.006 |
| two threads: solved models, time | 1.037 | 0.996 |
| ... `diverse_10-01` reaching 1% (of 8) | 2 -> 2 | 4 -> 6 |
| ... `diverse_10-01` gap | -0.014 ± 0.081 | +0.001 ± 0.028 |
| ... `dm_full_randsoc_ramp` time / mean primal gap | 1.026 [1.008, 1.044] / +0.104 ± 0.081 | 1.002 / -0.018 ± 0.092 |
| ... `dm_full_pert_s1_ramp` mean primal gap | +0.104 ± 0.062 | +0.011 ± 0.025 |

- `miplib-easy` at `mip_rel_gap = 0.01` (runs 39116318778, 39116323127):
  64 of 66 identical; neos-1456979 solved on one x86 runner; nu25-pr12
  ends at 54380 against an optimum of 53905 (bench-std's check prints
  WRONG: within 1% of its dual bound 53845, so a 1% solve may stop
  there; main found 53905 at the root, presumably by one of the skipped
  sub-MIPs, not checked).

No gain on the motivating ticks, small losses on two sensitive models on
two threads, and a worse answer on a MIPLIB model: no full run.

## Graph LNS's own cost, and warm-started neighbourhood LPs (2026-10-23)

**Where graph LNS's time goes** (the profile above, `diverse_10-01`, the
graph LNS share of the samples, inclusive):

| | share |
|---|---|
| the LP re-solves (`LpRelax::resolve_lp`) | 91% |
| ... dual simplex iterations | 33% |
| ... rebuilds (`compute_primal` 15% and `compute_dual` 10% in all, the factor's solve error check 4%) | 20% |
| ... the KKT check after a solve (basic infeasibilities) | 4.7% |
| domain propagation (fixings, flip screening) | 4.6% |
| copies, allocation, neighbourhood and candidate building | < 1% |

The rebuilds already reuse fresh duals and primal values where the
values cannot change; what is left recomputes them (after bound changes,
at the end of a solve, or to decide on refactorization), and skipping it
would change paths. The largest piece outside the simplex, the KKT check,
is about 1.5% of a run. No same-path saving here is worth more than 1-2%.

**The first LP of each neighbourhood** takes 23% of graph LNS's LP
iterations on `hard_10-03` and 19% on `diverse_10-01` (6 and 3 logs; 970
and 280 iterations per BFS neighbourhood searched by branch and bound,
420-450 per dived one, 60-70 per short-row one).

**`mip_graph_lns_incumbent_basis`** (removed; implementation at tag `exp/lns-incumbent-basis`): each neighbourhood LP starts
from the basis of the LP that gave the last incumbent of the graph LNS
call (`setBasis`, so one refactorization), instead of the last LP
solved. On `hard_10-03` seed 0 the first LPs take 227 iterations on
average instead of 314 (median 61 instead of 127), and 52 neighbourhoods
are searched instead of 41. Local screen (M1, instruction clock), against
main:

| | gap at 180 s, B - A | runs reaching 1% |
|---|---|---|
| one thread, `diverse_10-01` (8 seeds) | +0.044 ± 0.073 | 1 -> 0 |
| one thread, `hard_10-03` (8 seeds) | +0.043 ± 0.151 | 0 -> 0 |
| two threads, `diverse_10-01` (4 seeds) | -0.020 ± 0.095 | 2 -> 2 |
| two threads, `hard_10-03` (4 seeds) | +0.008 ± 0.133 | 0 -> 0 |

On the five sensitive models and the Lambda ticks (4 seeds), time
0.977 (geometric mean; per model 0.79-1.19, every interval across 1).
Cheaper first LPs buy more neighbourhoods, not better ones: no gain, so
no runner screen.

## What a better incumbent looks like (oracle analysis, 2026-10-23)

180 s one-thread incumbents (seeds 0-3, M1, instruction clock) against
a 2 h two-thread reference of each tick (-248499 on `hard_10-03`,
-248178 on `diverse_10-01`, the best known for the latter), compared on the
original model's decision binaries (integer columns not implied by the
others, as graph LNS defines them). Aggregates over the 4 seeds:

| | `hard_10-03` | `diverse_10-01` |
|---|---|---|
| decision binaries that differ (of 1750 / 1718) | 205-212 | 106-229 |
| ... of them bank charge switches | all but 0-1 | all but 0-2 |
| clusters (moved columns within graph distance 4) | 3-4 | 3-8 |
| largest cluster: size / periods / banks touched (of 28) | 168-182 / last 28 periods / 21-22 | 73-148 / last 16-21 periods / 18-22 |
| moved switches per bank (of 56) | 1-19 | 1-26 |
| best BFS neighbourhood (all rows) per cluster, size 64 / 200 / 400: share of the move covered | 21-26% / 50-56% / 80-82% | 25-43% / 57-82% / 89-100% |
| one BFS from the best seed, size 1000 | 87-92% | 95-97% |
| short-row BFS | as all rows (within 3 points) | as all rows |

**The move does not decompose.** Hybrids of the 180 s incumbent with the
reference's switches over one window of periods (lengths 4, 8, 14 and
28, every position; the other decision columns fixed at the incumbent,
an LP for the rest) are infeasible in 48 of 50 windows on each tick, and
none improves; the reference's switches of one bank over the whole
horizon are infeasible for 21-23 of 28 banks, and none improves (seed
0). A bank's charging at one period moves its state of charge in every
later period (discharge is bounded below and above by fractions of the
state of charge), and the per-period electricity and charging limits
couple the banks. So the improvement is one coordinated move of about
200 switches over half the horizon and most banks; flips, swaps
and small neighbourhoods cannot reach it through feasible steps.

**What contains it** is the LP disagreement: the decision columns where
the incumbent is more than 0.1 from the LP relaxation's point (no cuts)
number 403-519 (23-30% of them) and contain 83-89% of the move (seeds
0-1); at the usual 0.5 (RINS, graph LNS's disagreement seeds) only
46-54%.

**`mip_graph_lns_lp_disagreement`** (removed; implementation at tag
`exp/lns-lp-disagreement`): that set as a neighbourhood (the rest fixed at
the incumbent), a fifth move of the deep search and every other quick
neighbourhood, dived (1) or searched by the 300-node branch and bound
(2). Seed 0 of both ticks: 0 improvements in 26 tries (377-479 columns),
and the incumbents at 180 s are worse than main's (the move takes the
budget of the others). The set holds the move, but finding it inside
400 binaries needs a real search; a sub-MIP over it is close to RINS,
which ran 20-65 s on the tree without an incumbent (see above).

## Option sensitivity, and seed racing (2026-10-24)

**Options, one at a time.** Tuning half: the 10 screen models plus
`dm_full_coarse24_noramp`, `dm_full_prices_noramp` and
`dm_medium_prices_noramp`. x64, 8 seeds, 120 s, 1% gap, against main
(runs 40119254074 for 1 thread, 40119263372 for 2 threads). Shifted
time ratios (shift 1 s):

| Change | 1 thread | 2 threads |
|---|---|---|
| `mip_heuristic_run_feasibility_jump=false` | **0.981** | **0.968** |
| `mip_lp_age_limit` 20 / 5 | 0.994 / 0.999 | 0.992 / 0.981 |
| `mip_detect_symmetry=false` | 0.997 | 0.974 |
| `mip_allow_cut_separation_at_nodes=false` | 0.997 | 0.980 |
| `mip_graph_lns_deep_early=true` | 0.998 | 0.984 |
| `mip_allow_restart=false` | 0.999 | 0.977 |
| `mip_heuristic_effort` 0.025 / 0.1 / 0.2 | 1.001 / 1.000 / 0.999 | 0.972 / 0.988 / 0.996 |
| RINS, RENS or root reduced cost off; `mip_pscost_minreliable` 4 / 16 | 1.000-1.001 | 0.981-0.988 |
| `mip_heuristic_run_shifting=true` | 1.010 | 1.100 |
| `mip_heuristic_run_zi_round=true` | 1.021 | 1.018 |
| `mip_graph_lns_cost_perturbation=true` | 1.083 | 1.100 |
| `mip_concurrent_helper=false` | | 1.411 |

At 1 thread every option but those last few is within 0.6% of main:
the defaults sit at a flat optimum for dispatch. At 2 threads most
changes read about 2% better, because `diverse_10-01_0052` comes out
10-27% faster under almost every one of them. That says main's 8 seeds
there were an unlucky draw, not that 14 different options all help.

**Feasibility jump off, held out.** The other 12 models, 16 seeds,
180 s, both arches (runs 40129977225 for 1 thread, 40129979380 for
2 threads): 0.968 (x64) and 0.955 (arm64) at 1 thread, 0.968 and 0.948
at 2 threads, solved 192 -> 192 everywhere, no model worse than 1.03.
On these models FJ never finds a solution and takes about 0.1 s of a
1.5-2.6 s solve. On miplib-easy (run 40129981188) it is needed:
solved 66 -> 65, sgm 24.4 -> 26.6 s (arm64) and 24.8 -> 26.9 s (x64);
ns1116954 goes from 2 s to the limit, and markshare_4_0 and
neos-4413714-turia are 2.5-3.2x slower. The integer share of the
columns does not separate the two kinds of model (supportcase7 is 0.3%
integer and needs FJ, decomp2 is all integer and is better off without
it). So the default stays on, and the dispatch application should
set `mip_heuristic_run_feasibility_jump=false` itself.

**Seed racing.** From the full runs of 2026-10-24 (main, 25 models,
16 seeds, 180 s), keeping the best of k independent runs (x64; arm64
within 0.2 s):

| Configuration | Cores | sgm time (s) | Solved of 25 (mean) |
|---|---|---|---|
| 1 thread | 1 | 7.2 | 23.19 |
| best of 2 one-thread runs | 2 | 6.4 | 23.35 |
| best of 4 one-thread runs | 4 | 5.8 | 23.61 |
| 2 threads | 2 | 5.3 | 23.69 |
| best of 2 two-thread runs | 4 | 4.8 | 23.92 |

The helper thread uses a second core better than a seed race does, and
`hard_10-03_1340` reaches 1% in none of these.

## Per-bank Lagrangian decomposition (measured, not implemented, 2026-11-01)

The idea: dualize the rows that link banks, so that the rest splits into
one small MIP per bank. If those MIPs lack the integrality property, the
Lagrangian dual beats the LP bound (it equals the LP over each bank's
convex hull). The block solutions could also seed a heuristic that
switches many banks at once. Prototyped offline in Python (highspy, HiGHS
1.x MIP for the blocks).

**Structure (from the names).** On `hard_10-03_1340`: 28 bank blocks, one
system block (columns and rows touching no bank), and 110 coupling rows,
two equality rows per period over 55 periods. Every other row touches a
single block.

**Gate: no integrality property.** At the root LP's duals for the 110
rows, each bank block solved as an LP and as a MIP. The LP values sum
back to the LP bound exactly. 19-23 of the 28 bank MIPs are worse than their
LPs, so the Lagrangian bound L(y_LP) is above the LP bound:

| | LP bound | L(y_LP) | crest bound at 180 s, 1% gap, 1 / 2 threads |
|---|---|---|---|
| `hard_10-03_1340` | -253985 | -251234 | -251530 / -251515 |
| `diverse_10-01_0052` | -251794 | -249946 | -250205 / -250182 |
| `tick_1018_0340` | -379801 | -374947 | -376799 / -376684 |
| `ovn_0636_g147` | -194058 | -193863 | -193000 / -192979 |

(L is the sum of the block MIPs' dual bounds, so valid even where a block
hit its time limit.) It beats crest's 180 s bound on three of the four,
by 0.1% on `hard_10-03` and `diverse_10-01` and 0.5% on `tick_1018_0340`,
and is 0.45% weaker on `ovn_0636_g147`, where crest's cuts gain more than
the per-bank hulls do. With crest's own 180 s incumbents, the better of
the two bounds would certify `diverse_10-01` at one thread (0.998%,
against 1.10%) and `tick_1018_0340` at two threads (0.93%, against
1.40%); `hard_10-03` stays above 1% (1.20% and 1.28%).

**The dual does not climb from there.** A subgradient method (Polyak
steps toward the best known value, block MIPs capped at 20 s) ran 6
iterations on `hard_10-03` without improving on L(y_LP); the noise of
time-capped block MIPs is larger than its steps.

**Too slow for 180 s.** The block MIPs are not easy: on `hard_10-03` they
take 1-120 s each, and one evaluation of L costs 6-20 CPU minutes on the
M1 (three evaluations in parallel). Capping the blocks gives up the
bound: at 5 s per block L(y_LP) is -251762 and at 20 s -251527, both
weaker than crest's. A bank block holds several continuous storage
modules, so a dynamic program over one state does not apply.

**Primal side: no go.** At y_LP the blocks charge independently, and
their switch patterns do not fit together:
- all switches fixed at the block solutions: infeasible;
- only the switches the blocks leave off fixed (the rest free), solved
  for 60 s: -244456, against crest's -248252 at 180 s;
- crest from the block solution as a start (1% gap, one thread, 180 s):
  it repairs the start to -244877 at 57 s and ends at -246977, against
  -248252 without it; the time spent also costs bound (-251973).
Near optimal multipliers many banks are indifferent between charging
and not, so the block solutions break ties arbitrarily; recovering a
primal schedule from them is the coordination problem graph LNS already
works on.

**What building it would take.** A per-bank hull bound pays on the
bound-limited ticks only if each bank's MIP solves in about a second.
That needs a specialized block solver (not a general MIP per bank per
iteration), then separating the hull's cuts into crest's root (Lagrangian
or Benders-style block cuts) so they combine with crest's own cuts
rather than replacing them (on `ovn_0636_g147` crest's cuts are worth
more). Not attempted.

## Per-period count columns (rejected, 2026-11-02)

The idea: the LP spreads a few banks' worth of "on" over many fractional
banks, so branching on the count N_t = sum_b switch(b_t) (a general
disjunction) might split it better than any single binary. Every bank
stays; for each period t the model gets a general integer column
count_switch(t) in [0, #banks] and the row count_switch(t) - sum_b
switch(b_t) = 0, objective 0 (a stdlib Python transform of the MPS, by
column names; the `lambda_*` models have anonymous names and got none).
Variant: also the count of switch_change. The transform and a `counts=`
config key of bench/dispatch.py were in commit 83efb0d and are removed.

**Presolve removes the counts.** A count is a column singleton in an
equation whose bounds the row implies: free column substitution and the
aggregator take it out, and the presolved model is the original one
(`hard_10-03_1340`: 25207 rows, 18958 columns either way). Adding counts
to the model as given is a no-op. To keep them, presolve_rule_off = 4352
(free column substitution and aggregator off); the screen then has three
configs: base, keep (the rule switch alone) and counts (counts and the
rule switch).

**Screen** (10 screen instances plus `tick_1018_0340` and
`tick_1018_0420`, 8 seeds, 120 s, 96 pairs per architecture; sgm: shifted
(10 s) geometric mean time ratio against base, the limit if unsolved; gap:
mean final gap % of pairs not both solved):

| threads | arch | keep: solved, sgm, gap | counts: solved, sgm, gap |
|---|---|---|---|
| 1 | arm64 | 67 -> 64, 1.078, 1.27 -> 1.38 | 67 -> 40, 1.887, 1.14 -> 4.18 |
| 1 | x64 | 67 -> 65, 1.080, 1.28 -> 1.39 | 67 -> 40, 1.911, 1.15 -> 4.15 |
| 2 | arm64 | 83 -> 81, 1.070, 1.17 -> 1.24 | 83 -> 57, 1.849, 1.06 -> 6.47 |
| 2 | x64 | 79 -> 74, 1.074, 1.14 -> 1.21 | 79 -> 55, 1.851, 1.05 -> 5.92 |

On the motivating ticks at 2 threads (arm64; mean of 8 seeds):

| instance | base primal / dual | counts primal / dual | solved base -> counts |
|---|---|---|---|
| `hard_10-03_1340` | -248247 / -251528 | -235972 / -251584 | 0 -> 0 |
| `tick_1018_0340` | -372956 / -376664 | -357737 / -376628 | 8 -> 0 |
| `tick_1018_0420` | -374328 / -378054 | -344671 / -378093 | 8 -> 0 |

The dual bound does not move (within 0.03%); the incumbents get 4-8%
worse, so the gap grows from about 1% to 3-10%. Locally (`dm_small_pert_s1_ramp`,
2 threads) counts with the rule switch took 26 s against 5 s, and the
switch_change variant over 60 s at 1 thread, so it was not screened.
The rule switch alone (keep) costs 7-8%. No full run.
For the application (Antora): do not add count columns to the model.

## Reduced-cost block seeds (rejected, 2026-11-02)

Graph LNS already seeds a neighbourhood at a column with a promising
reduced cost at the incumbent (the gain of flipping it alone, from the
neighbourhood LPs, deep search only). Single flips are rarely feasible on
dispatch (switch / switch_change chains, state-of-charge links), so the
candidate scored a column by the signed reduced-cost gain summed over it
and the decision columns of its short rows (at most four decision
columns: the chains), also refreshed from the flip search's LP with every
decision column fixed at the incumbent (the fixed-integer LP). Variant
`b1`: in the deep search; `b2`: in the quick search too.

Local (instruction clock, 60 s, seeds 0-1, 8 models): no gain; on
`tick_1018_0340` `b1` ended 0.20 points of gap worse (mean primal gap
+0.066), `b2` reached 1% of the best known 1.03x later over all pairs.

Screen (runs 41448246022, 41448249444; 10 instances, 8 seeds, 120 s), and
the two ticks (41448265268, 41448268659; 8 seeds, 120 s):

| threads | arch | variant | solved | time ratio | t1 ratio | primal gap B-A |
|---|---|---|---|---|---|---|
| 1 | arm64 | b1 | 67 -> 65 | 0.960 | 1.001 | +0.007 ± 0.017 |
| 1 | arm64 | b2 | 67 -> 64 | 0.960 | 1.010 | +0.010 ± 0.024 |
| 1 | x64 | b1 | 66 -> 65 | 0.964 | 1.003 | +0.011 ± 0.016 |
| 1 | x64 | b2 | 66 -> 64 | 0.957 | 1.006 | +0.002 ± 0.030 |
| 2 | arm64 | b1 | 68 -> 67 | 1.021 | 1.003 | -0.001 ± 0.013 |
| 2 | arm64 | b2 | 68 -> 68 | 0.992 | 1.011 | -0.012 ± 0.015 |
| 2 | x64 | b1 | 66 -> 67 | 1.012 | 1.000 | -0.012 ± 0.014 |
| 2 | x64 | b2 | 66 -> 67 | 1.043 | 1.007 | -0.018 ± 0.015 |

On the ticks (`tick_1018_0340`, `tick_1018_0420`): at 1 thread neither
is solved by base; `b2` reaches 1% of the best known on `tick_1018_0340`
1.32-1.34x later. At 2 threads on x64, `b1` solves 14 of 16 (base 16),
`b2` 15. The time ratio on solved runs at 1 thread is better, at 2
threads worse; the solved counts fall and t1 does not move: no gain.

## The path separator's work limit at the root (2026-11-21)

**The case.** A gas-boiler dispatch model (an unserved-steam slack
at a high price, the boilers held off) ends at
180 s, 2 threads, with a gap of 16-28%. CPLEX (4 threads) is at 8.5%
after 300 s; neither reaches 2%. The incumbents agree (crest and CPLEX
within 0.3% of each other after 300 s), the bounds do not: crest's root
bound is 2.4% of the objective weaker than CPLEX's after its root cuts,
and the tree moves it by 0.3% in 180 s.

**Diagnosis.** The root LP bounds agree. CPLEX's root cuts move the
bound by 3.7% of the objective (mostly MIR, then flow, cover, zero-half
and Gomory cuts); SCIP's root (22 rounds, one thread, 410 s) by 5.3%
(mostly c-MIR, Gomory and flow cover); crest's by 1.5%. Crest's path separator
(c-MIR on aggregated paths) has a work limit (60 aggregated row entries
per LP nonzero, for the heater-HTF models' dense rows), and stopping at it
switched the separator off for good. On this model a full call passes
250 entries per nonzero (dense balance rows, 3-4 s a call), so the
separator stopped in the first round, a seventh of the way through the
rows; the other root rounds found 1-12 cuts each. Without the limit the
root bound ends 2.8% of the objective stronger (0.5% past CPLEX's root),
but the root takes 147 s.

**The change.** A call that stops at the limit records the row it stopped
at, and at the root, while the gap to the incumbent is over three target
gaps, the next call continues from there (so every row is visited over a
few rounds, each call at the limit's cost). In the tree, or once the gap
is narrower, or with no incumbent yet, the separator stays off as before.
Models whose calls stay under the limit (the dispatch suite: at most 41
per nonzero) take the same path as before.

**Screen** (M3 Pro, shared and loaded, so noisy; 2 threads, 1% gap,
180 s, seeds 0-3, base and candidate interleaved; times in s, TL = time
limit with the gap):

| model | base | candidate |
|---|---|---|
| gas boilers off, high price: gap | 25.2 / 15.7 / 25.6 / 28.2% | 22.4 / 11.9 / 13.4 / 24.2% |
| ... dual bound at 180 s, change | | 3-4% of the objective stronger |
| gas boilers free, high price | 161 / 92 / 115 / 135 | TL(1.04%) / 104 / 96 / 135 |
| gas boilers off, lower price | 101 / 53 / 125 / 128 | 113 / 51 / 125 / 166 |
| gas boilers free, lower price | 33 / 89 / 68 / 39 | 32 / 90 / 68 / 65 |
| heater-HTF ticks (4): shifted geometric mean ratio | | 0.918, 0.883, 1.000, 1.226 |

On the twins and the heater-HTF ticks the gate stays shut, so the
differences there are timing noise (same paths). The 180 s bound on the
high-price model is close to CPLEX's 300 s bound and SCIP's root (within
1% of the objective); 1% stays out of reach: a 40 min run ends at 7%, with the incumbent
still improving and the bound flat.

**Variants rejected on the way** (same screen, against base):
- Continuing past the limit at every root round (no gap gate): heater-HTF
  ticks 1.9-2.5 times slower on three of four (one of them 0 of 4 solved
  against 4), the slow
  extra rounds holding back the incumbent; the gas models gain (boilers
  free, high price: 4 of 4 solved against 2, 0.80).
- Continuing while each sweep over the rows raises the bound by a quarter,
  or half, of the target gap, and with no incumbent: heater-HTF 1.4-2.3
  times slower (the first sweeps there pass the test); boilers free, high
  price 0.29-0.60, the lower-price twins 0.24-0.97.
- The gap gate open when there is no incumbent yet: heater-HTF 1.2-1.7
  times slower (the first two or three rounds run before the first
  incumbent); boilers free, high price 0.60.

## The path separator's aggregation length limit (2026-11-22)

**Profile.** A full path separator call on the gas-boiler model (boilers
off, high price; work limit off, one thread) takes 2.1-2.3 s; on a
heater-HTF tick 3.1-3.5 s. 73-85% of it is the cut generator
(bound substitution 25-39%, c-MIR scale search 17-40%, preprocessing
10-14%), the rest is the aggregation, the column scan and path mixing.
The aggregations come in two sizes: under 64 entries, and 30-130 times
an average LP row (each path that reaches a dense coupling row). The
long ones are 99% of the entries passed to the cut generator and gave
no cut in any call; every c-MIR cut came from the short ones. Across
the dispatch suite and the heater-HTF ticks the cuts came from
aggregations of at most 100 + 16 average rows (the MIPLIB checks have
longer ones, but also longer average rows).

**The change.** A path stops once its aggregation has more than
100 + 20 average LP rows of entries (in the spirit of SCIP's density
limit on aggregations, but relative to the model's row length). A full
call on those models then costs 1-3% as much (gas-boiler model: 0.029 s
against 2.1-2.3 s; heater-HTF tick: 0.070 s against 3.1-3.5 s, one
thread), stays far under the work limit and so runs to the end every
round, at the root and in the tree: the work limit and the root
continuation of PR #82 no longer come into play there. With the long
aggregations gone the cut generator is a fifth of the call, so speeding
it up (exact shortcuts in the c-MIR scale search were the candidate)
no longer pays.

**Screen** (M3 Pro, shared and loaded; 2 threads, 1% gap, 180 s, seeds
0-3, base and candidate interleaved; shifted geometric mean ratio,
solved counts):

| model | ratio | solved base / candidate |
|---|---|---|
| gas boilers off, high price | (all TL) | gap 21.2% / 18.4% mean; bound 0.8% of the objective stronger |
| gas boilers off, lower price | 0.509 | 3 / 4 |
| gas boilers free, high price | 0.898 | 1 / 2 |
| gas boilers free, lower price | 1.042 | 4 / 4 |
| heater-HTF ticks (4) | 0.819, 0.916, 0.965, 0.973 | 16 / 16 |
| dispatch suite (8) | 0.912 geomean | 26 / 25 |

On the suite: `ovn_0636_g147` 0.747, `diverse_10-01_0052` 0.840 (2 to 1
solved; it is the noisy one, A/A 1.15), `dm_full_pert_s1_ramp` 0.888,
`lambda_20260930_142050_3c1b60d6` 0.919, `tick_1018_0340` 0.945,
`hard_10-03_1340` 1.000 (final gap 1.31% to 1.28% mean), the rest within
2%. (The Mac slept during the first suite screen; the four models with
runs past the time limit were rerun.) MIPLIB checks (8 models, one
thread): the same node counts on all eight, the limit never binds there.

## The flip search first (2026-11-23)

### Isolated decision columns

**The case.** The gas-boiler dispatch model with the boilers left to the
solver and the high unserved-steam price: CPLEX (4 threads) reaches 1% in
49 s; crest (2 threads) took 23-138 s over 8 seeds, and before the
helper's budgets were fixed it missed 1% in 180 s. The bounds agree; the
incumbents lag.

**Diagnosis.** A boiler's on/off column shares no row with another
integer column: it is tied to the plant only through its continuous fire,
which is zero when off and between its low and full fire when on. So:
- The LP fires boilers below their low fire (free steam, and the on/off
  column takes the fraction that allows it) in five steps of six. The
  root dive rounds to the nearest, which switches a boiler off wherever
  the LP used less than half its low fire: the first incumbent is 13.5%
  from the bound, with unserved load at the high price.
- No breadth-first neighbourhood reaches a boiler column from the banks'
  switches, and the quick search's own flip search only runs at its end,
  if its LP budget lasts. Here the neighbourhood dives used the budget
  up on the banks' switches around boilers left off, so the boilers
  were left to the helper's deep search, which came to its flips after
  its other moves.
- Each missing boiler is an improving flip by itself.

**The change.** `has_isolated_cols` (lns.rs): some decision column
shares no row with another integer column. With such columns the quick
search runs the flip search first (candidates in order of the gain their
reduced cost promises, each kept only if the LP improves). At first
(#85) only with an incumbent over three target gaps from the bound after
the root dive, which left the lower-price twin as it was; now at any gap
still open. The option `mip_graph_lns_flip_first` (on) is there for the
oracle: two of its check models (`egout`, a fixed-charge network, and a
semi-continuous test model) have such columns and change path, so
`bench/quick_check.sh` turns it off for the comparison with the C++
app. No model of the dispatch suite, the heater-HTF ticks or the
boilers-off gas models has such a column.

**Results** (M1, `CRESTLINE_INSTR_CLOCK=10`, so load-independent: 10G
instructions of the two threads count as a second, which is 0.42 s of
wall time on the idle machine; 2 threads, 1% gap, seeds 0-7; time to 1%
in those seconds, the first root LP being about 17 of them):

| | before | flip search first over three target gaps (#85) | at any gap, with the flip search's memory |
|---|---|---|---|
| boilers free, high price | 61 138 90 28 83 79 25 23 (shifted geometric mean 56.4) | 29 31 30 28 30 33 25 18 (27.6, 0.49) | 30 31 30 28 30 34 23 18 (27.7) |
| ... first incumbent / after the flip search (seed 0) | 13.5% | 13.5% / 0.90%, 18 LP solves | |
| boilers free, lower price (1.7 target gaps after the root dive) | 20 48 35 22 22 24 22 20 (25.6) | 20 42 34 22 33 24 31 21 (27.6; rerun with #86: 20 41 27 21 29 32 29 19, 26.6) | 16 16 16 17 22 18 17 16 (17.2, 0.65 of the rerun) |

By the wall clock (same machine, lightly loaded, seeds 0-2) the
high-price model takes 11.3, 15.1 and 13.5 s. The memory by itself
changes neither (27.0 and 25.4).

**Tried instead, and rejected:**
- *Rounding the boilers on in the dives.* A zero-cost column with no
  other integer column in its rows (the on/off column) takes its LP
  value from the continuous columns it switches, anywhere on a zero-cost
  face, so "nearest" says little; SCIP's vectorlength diving rounds
  zero-cost columns up (Berthold's thesis), and unit commitment
  heuristics commit what the LP uses. Tried as: up when in use (the
  integer in the column's LP-feasible interval if there is one, else
  away from its capacity row, which is the one with the larger
  coefficient). The first incumbent is then 1.1-1.3% from the bound, and
  the high-price model reaches 1% in 26-72 s (34.7, 0.62 of main). But
  the dive's LP re-solves after switching the boilers on take 8000 more
  simplex iterations, which the lower-price twin does not get back:
  29-60 s (37.7, 1.47 of main). The LP cannot tell the two apart (same
  LP solution: the unserved slack is nonbasic in both, and the first
  order cost of either rounding is zero), so the rounding cannot be
  right for both; the flip search can, since it solves the LP.
- *Deciding each boiler last in the dive by solving both LPs:* the
  probes take 110 simplex iterations each even with the banks fixed
  (16000-20000 in the root dive), and the dive then fails.
- *The main solver joining the helper's deep search while the gap is
  wide* (see "With a helper and a wide gap, the main solver joins the
  deep search") does not help here by itself: 22-161 s (54.9).
- *A lazy dive* (integral candidates left free for the LP to move, 1-16
  fractional ones fixed per LP solve, as SCIP's fractional diving): on
  the boilers-off model the first incumbent's gap goes from 101% to
  32-49%, for 4 times the dive's LP iterations, but at 180 s nothing is
  left of it (mean incumbent within 0.5% of main's over 4 seeds), and
  the boilers-free high-price model gets worse (1 of 4 seeds reaches 1%,
  against 4 of 4).

### A dive that ends near the target

**The rule.** The quick search also runs the flip search first when the
root dive leaves the incumbent within three target gaps of the bound
(and the gap is still open). What is missing is then mostly single
columns rounded the wrong way, which a flip puts right at an LP solve
each, where a neighbourhood costs an LP of some hundred simplex
iterations and a dive; often the gap closes there
(`dm_full_coarse24_noramp`, seed 0, two threads: 1.45% after the dive,
1.11% after nine LP solves of flips, and solved with the bound's next
move, where two neighbourhoods took a third longer). On seed 0, 26 of
the 36 dispatch models end their root dive that near, three of them at
2.99-3.00 target gaps (`ovn_0716_g158`, `dm_small_prices_noramp`,
`diverse_10-01_0052`), so not on every seed.

**Results.** Paired on the runners (bench-dispatch, 180 s, seeds 0-15,
36 models, 576 pairs per architecture), four configurations of one
build: `mip_graph_lns_flip_first` and `mip_graph_lns_flip_memory` off
(which is main before the two on these models), each on, and both on.
Time to 1% as the geometric mean over the models of the ratio to the
first; solved runs of 576:

| | one thread (arm64) | two threads (arm64 / x86-64) |
|---|---|---|
| this rule | 0.950; 524 -> 526 | 0.970 / 0.968; 553 -> 555 / 556 -> 554 |
| the memory | 0.948; 524 -> 534 | 0.963 / 0.953; 553 -> 556 / 556 -> 556 |
| both | 0.832; 524 -> 533 | 0.926 / 0.909; 553 -> 557 / 556 -> 556 |
| both against the memory | 0.876 | 0.959 / 0.952 |
| ... primal gap at the end, both against neither | -0.007 ± 0.006 | -0.026 ± 0.006 / -0.024 ± 0.005 |

The two help each other: with the memory the flip search's 300 solves go
to moves not yet tried, and close the gap more often by themselves. Both
against neither, by model (one thread; two threads arm64 / x86-64):
`dm_full_coarse24_noramp` 0.43; 0.62 / 0.56, `dm_medium_randsoc_noramp`
0.50; 0.87 / 0.82, `dm_full_pert_s1_noramp` 0.55; 0.91 / 0.88,
`dm_full_pert_s1_ramp` 0.66; 0.87 / 0.86, `dm_medium_pert_s1_noramp`
0.72; 0.81 / 0.78, `dm_medium_prices_noramp` 0.79; 0.72 / 0.67,
`dm_medium_pert_s1_ramp` 0.81; 0.79 / 0.77, `tick_1025_0028` 0.69; 0.76
/ 0.72, `ovn_1148_g105` 0.61; 0.83 / 0.78. `diverse_10-01_0052` is
solved in 2 of 16 runs at one thread (4 before; the memory alone the
same) and in 13 / 12 at two (9 / 12). Slower at two threads:
`dm_full_prices_noramp` 1.07 [1.01, 1.15] / 1.06 (a 2 s model) and, not
significantly, `lambda_20260930_142050_3c1b60d6` 1.04 [0.79, 1.37] /
1.10 (0.87 at one thread). `hard_10-03_1340` ends its dive over three
target gaps away and keeps its path but for the memory: final gap
-0.025 ± 0.082 at one thread, +0.009 ± 0.039 / +0.001 ± 0.040 at two.

The related models (M1, `CRESTLINE_INSTR_CLOCK=24`, two threads, 4
seeds, both against main): the four heater-HTF ticks 27.9 against 27.8
in the sum of their shifted geometric means, the boilers-off gas model
8.9 against 9.3 at the lower price and, at the high price, never solved,
a mean incumbent 0.3% better (within the seeds' spread).

### After a wider gap: tried, not kept

The flip search first after any root dive that leaves the gap open, or
only after one that leaves over three target gaps, reaches a good
incumbent sooner on the models that end their dive far away, but the
target gap no sooner, and it costs the hardest tick a little.

| against | | time to 1% | time to within 1% of the best known | solved |
|---|---|---|---|---|
| main | first over three target gaps, any model; two threads, seeds 0-15, two runs | 0.989 / 0.984 and 0.997 / 0.996 | 0.935 / 0.927 and 0.933 / 0.929 | 554 -> 557 / 556 -> 557 and 556 -> 558 / 556 -> 557 |
| main | ... one thread, seeds 0-15 | 0.988 / 0.991 | 0.935 / 0.933 | 524 -> 519 / 525 -> 521 |
| main | ... seeds 16-31, the 9 models over three target gaps: two threads; one thread | 1.017 / 0.966; 1.032 / 1.027 | 0.80 / 0.78; 0.81 / 0.79 | 122 -> 125 / 121 -> 124; 88 -> 91 / 90 -> 92 |
| this rule with the memory | first at any gap; two threads, seeds 0-15, the 14 models where the two differ | 0.989 / 0.989 | 0.753 / 0.710 | 201 -> 205 / 205 -> 204 |

- `hard_10-03_1340` (never solved in 180 s) ends with a larger gap in
  every one of these comparisons: +0.013 ± 0.033, +0.042 ± 0.036, +0.041
  ± 0.037 and +0.006 ± 0.057 at two threads on seeds 0-15, +0.039 ±
  0.055 and +0.031 ± 0.045 on seeds 16-31, +0.04 to +0.08 (± 0.11) at
  one thread, and +0.029 ± 0.035 and +0.035 ± 0.042 with the memory.
  About 0.03 points of gap, from the incumbent.
- The solve time moves both ways by model and consistently so:
  `ovn_0652_g157` 0.65-0.78 in all four two-thread comparisons,
  `ovn_0636_g147` 1.01-1.29 and `ovn_0700_g157` 0.99-1.20.
- Why the neighbourhoods do better from the dive's own point: after the
  flips have taken the single-column gains, a neighbourhood's dive
  improves the incumbent less often and by less, so fewer of them count
  as progress and the quick search stalls earlier
  (`lambda_20260930_142050_3c1b60d6`, which ends its dive at 2.2 target
  gaps, two seeds: 2 of 5 and 2 of 7 neighbourhoods improved after the
  flips, 6 of 12 and 3 of 10 without, and the quick search ended at
  1.38% and 1.27% from the bound against 1.24% and 1.15%). Where the
  flips close the gap by themselves that does not matter, which is the
  near case.

## Where the truth is on the gas-boiler model (2026-11-22)

The gas-boiler model with the boilers held off and the high unserved-steam
price, after the aggregation length limit (2 threads, 1% gap, 180 s):
over 22 seeds the gap at 180 s is 7.0-15.7% (mean 9.8%). The 180 s bound
varies by 1.0% of the objective across seeds, the incumbent by 8%.

**Long runs.** Crest at 4 threads, gap 0.1%, 90 min: 5.9% at 17 min, then
four restarts, each raising the bound (the first by 0.6% of the objective
at its root), and 3.1% at the end. Its bound is 2.6% of the objective
stronger than the mean 180 s bound, its incumbent 3.7% better than the
mean 180 s incumbent. SCIP (one thread, 35 min, 300 nodes) had a bound
0.6% of the objective weaker than that crest run at the same time, and no
incumbent within 9%. So the optimum is somewhere in a 3.1% window, and no
solver here proves 1% in 90 minutes; at 180 s both sides fall short (the
bound by at least 2.6% of the objective, the incumbent by 0.4% at the
best seed, 3.7% on average).

**Why the bound is weak.** The boilers are held off, so the integer
columns are the banks' charging switches. 93% of the difference between
the root LP and a good incumbent is the unserved-steam term: in the LP
the banks are charged a little more evenly, and the plant discharges
slightly more in each step of the lull. The two plans' per-bank states
of charge agree to within a few percent. Each step's shortfall is small,
but the objective is a small difference of large terms (the unserved
steam costs about 1.5 times the objective's magnitude), so 1% of the
objective is about 0.6% of the unserved steam. No cut family targets
this; CPLEX's and SCIP's root bounds are weaker than crest's.

**Measured, not kept:**
- The main solver's deep graph LNS with a helper running even when the
  gap after the root is over three target gaps: first measured by wall
  clock on a loaded machine, where per-seed noise swamped it; measured
  again by instruction count and kept (see the next section).
- More root cut rounds (stall limit 30 rounds instead of 3), one thread:
  root bound 0.5% of the objective stronger, 10 s more root; well short
  of the 2.6% needed, not screened.

**Why no bound closes it (2026-11-23).** The banks charge by on/off
switches, a step at a time, and a module's steam output is the smaller of
a cap and a fraction of its state of charge. The LP holds a bank exactly
at a limit (a module's state-of-charge cap before the lull, the knee of
its output during it) with a fractional switch, a duty cycle within the
step; an integer plan has to switch whole steps on and off around the
limit, overshooting or falling short each time. This is the relaxed
control with a state-constrained arc of mixed-integer optimal control,
whose integer approximations chatter and whose integrality gap is
proportional to the step width (Sager, Bock and Diehl, Math. Program.
133, 2012; Sager, Jung and Kirches, Math. Methods Oper. Res. 73, 2011).
The shortfall is small in energy, but at this price 1% of the objective
is one part in ten thousand of the steam demand over the horizon.
Measured against the best solution known (from the 90 min run):

| bound | above the best known |
|---|---|
| LP relaxation | 11.5% |
| crest, root cuts, 2 threads | 5.3% |
| per-bank Lagrangian dual, solved by column generation (the LP over each bank's convex hull; a block MIP per bank per iteration, 53 iterations, 17 min on 5 cores) | 4.3-4.5% |
| SCIP 10, one thread, 68 min, 4700 nodes (its incumbent 2.6% worse than the best known) | 4.6% |
| crest, 4 threads, 90 min | 3.1% |

- So even every bank's convex hull, which no cut family on a bank's rows
  can beat (path or lot-sizing cuts on longer aggregations of a module's
  chain included), leaves over four target gaps. What is left is between
  banks: each step's charging limit is a knapsack over the banks'
  different powers, and the plans the blocks prefer do not fit together.
- The restricted master solved as an integer program (one generated
  schedule per bank, 740 columns): its optimum is the best solution
  known, whose columns were in it; the generated columns add nothing.
- For the application: the gap is the price of whole-step switching. With
  the switches of the hourly steps relaxed to duty cycles and only the
  sub-hourly first steps binary, the model reaches 1% in 9 s and its
  plan is 10.2% better than the best all-binary plan known; binary
  for the first third of the steps, 1% in 180 s and 5.2% better.

**Incumbents: tried offline, not kept.** From the root LP (Python
prototypes, an LP for the continuous columns once the switches are
fixed; first incumbent of the root dive for comparison: 45% worse than
the best known):
- sum-up rounding of each bank's switches (cumulative rounding along
  time, thresholds 0.5 to 1): infeasible at every threshold, the banks'
  pulses colliding in the per-step charging limit;
- relax-and-fix in time order, a period per LP solve, to the nearest:
  66% worse than the best known (a period rounded at once overdraws its
  charging limit, and the LP pays with the electric boiler);
- one column per LP solve, integral ones left free: 20% worse (least
  fractional first) and 17% worse (earliest period first), for 550-650
  LP solves: this is the lazy dive of "The flip search first";
- block coordinate descent from an early incumbent, one bank (or two) at
  a time solved exactly with every other bank's columns, continuous ones
  included, held: stalls 12% from the best known with single banks and
  4.6% with pairs, where graph LNS, on the full LP, ends 1-4% from it.

## With a helper and a wide gap, the main solver joins the deep search (2026-11-23)

**The case.** The gas-boiler model with the boilers held off and the high
price (above): no bound closes it, so what 180 s can deliver is the
incumbent, and at 2 threads the incumbents came from one thread. With the
gap after the root over three target gaps, the main solver does not run
the deep search (one thread's rule: a gap that wide after the quick
search means graph LNS does not suit the model). Its time went to RENS (a
45 s nest of sub-MIPs, their root LPs solved from scratch) and 6-14 nodes
of strong branching, which found nothing, while the helper's deep search
found every incumbent and was still descending at 150 s (90% of its time
in LP re-solves, about 150 neighbourhood nodes a second on this LP).

**The change.** After the root cuts, with a helper and a gap over three
target gaps, the main solver runs the deep search too, in rounds for as
long as a round (with what the helper found meanwhile) closes 1% of the
gap and the gap stays that wide. Both search from the best incumbent of
either, each with its own seed. No option. With one thread nothing
changes.

**Results.** Local (M1, instruction clock, so load-independent; 2
threads, 1% gap), the boilers-off high-price model over 8 seeds, at two
budgets: 1800G instructions, which is 75 s of the idle M1's two threads,
and 4320G, its 180 s. Production's hardware is slower than the M1, so its
180 s lies between the two.

| | main | with the change |
|---|---|---|
| 75 s budget: gap, mean (range) | 10.7% (7.5-14.8) | 8.0% (6.8-10.6) |
| ... incumbent against the best known | 4.5% worse (1.6-8.1) | 2.0% worse (0.9-3.9) |
| 180 s budget: gap, mean (range) | 7.8% (6.4-9.8) | 7.4% (5.9-9.3) |
| ... incumbent against the best known | 2.0% worse (0.7-3.4) | 1.6% worse (0.2-3.3) |

So the change brings the incumbents of 180 s forward to 75 s; at 180 s
on a fast machine one thread's search has flattened anyway and the gain
is small. (CPLEX, 4 threads, 300 s on an M3 Pro: 8.5%.) The lower-price
twin and the four heater-HTF ticks (4 seeds each, the 75 s budget) take
the same time (ratios 0.97-1.06, the gate not firing), as do the 8
dispatch screen models (4 seeds: ratios 0.91-1.19, each within its
seeds' spread).

Paired against main on the runners (bench-dispatch run 45112620802: 2
threads, 180 s, 16 seeds, the 36 dispatch models, 576 pairs per
architecture):

| | arm64 | x86_64 |
|---|---|---|
| runs solved | 554 -> 553 | 555 -> 556 |
| time on runs both solve (geometric mean over models) | 0.998 | 0.998 |
| time to within 1% of the best known | 0.999 [0.995, 1.003] | 0.996 [0.992, 1.000] |
| mean primal gap, B - A | -0.001 ± 0.003 | -0.002 ± 0.003 |
| `diverse_10-01_0052` solved | 10 -> 9 | 11 -> 12 |
| `ovn_1148_g105` time (the one interval off 1 by over 1%) | 1.095 [1.010, 1.187] | 1.044 [0.916, 1.190] |

On these models the gap after the root cuts is rarely over three target
gaps, so the change seldom acts.

## The flip search's memory of failed moves (2026-11-23)

**Where its solves went.** The flip search is first improvement: the
candidates in order of the gain their reduced costs promise, each as a
single flip and with up to three partners, a propagation screen before
each LP solve. After every improving move it starts again from the best
candidate. On an overnight tick (`ovn_0636_g147`, one thread, one seed) it
tried 16000 moves in the solve. 15000 of them had failed before with no
decision column changed since in a row of one of their columns, and 3 of
those improved. Most repeats are stopped by the screen, which counts as a
quarter of an LP solve in the search's budget, so a search of 300 solves
got through the first 26 to 130 of its 350 candidates and used 15 to 50
LP solves.

**The change** (`mip_graph_lns_flip_memory`, on; `lns::FlipMemory`). The
don't-look bits of local search (Bentley's 2-opt): a move that did not
improve (its columns, each with the value it leaves) is not tried again
until a decision column in a row of one of its columns has changed,
whether by the search's own moves or by another move or solver (the
incumbent's decision columns are compared with those the last search
left). The search is exhausted when no move it may try improves.

**A plain memory is worse.** Tried first: every move that failed is
skipped until none is left to try, then all are forgotten. The search
reaches a local optimum sooner, and a worse one (M1, instruction clock,
one thread, 4 seeds, shifted geometric mean of the time to 1%:
`ovn_0636_g147` 53 to 118, `ovn_0716_g158` 39 to 126 with 2 of 4 at the
limit, `ovn_1148_g105` 36 to 69). A move that failed can improve once a
flip next to it is made; the search without memory tries it again at
once, as the best candidate still, and the plain memory only after the
moves around it have been made in another order. The rule on the rows
keeps the first and drops the rest.

**Results.** Paired on the runners (bench-dispatch, 180 s, seeds 0-15,
the 36 models; the base has `mip_graph_lns_flip_memory` and
`mip_graph_lns_flip_first` off, which is main on these models, none
having an isolated decision column):

| | one thread (arm64) | two threads (arm64 / x86-64) |
|---|---|---|
| time to 1%, geometric mean over the models | 0.948 | 0.963 / 0.953 |
| solved, of 576 | 524 -> 534 | 553 -> 556 / 556 -> 556 |
| time to within 1% of the best known | 0.972 [0.960, 0.983] | 0.990 [0.984, 0.996] / 0.990 [0.984, 0.996] |
| primal gap at the end, difference | -0.009 ± 0.005 | -0.007 ± 0.004 / -0.007 ± 0.003 |

By model (time ratio with its 95% interval over the seeds, one thread;
then two threads, arm64 / x86-64):

| | one thread | two threads |
|---|---|---|
| `ovn_1148_g105` | 0.60 [0.46, 0.79] | 0.85 / 0.76 |
| `tick_1025_0028` | 0.66 [0.54, 0.82] | 0.79 / 0.80 |
| `tick_1025_0004` | 0.75 [0.64, 0.87] | 0.83 / 0.86 |
| `ovn_0716_g158` | 0.81 [0.57, 1.13] | 0.82 / 0.73 |
| `ovn_0636_g147`, `ovn_0700_g157` | 0.84, 0.84 | 1.01 / 1.05, 0.96 / 0.83 |
| `tick_1023_2236` | 0.87 [0.81, 0.92] | 0.87 / 0.82 |
| `lambda_20260930_142050_3c1b60d6` | 0.88 [0.78, 0.99] | 0.95 / 1.01 |
| `tick_1015_1901` | 0.95 [0.91, 1.00] | 0.91 / 0.92 |
| `tick_1018_0340` | solved 4 -> 10 of 16 | 0.82 / 0.93 |
| `tick_1018_0420` | solved 5 -> 11 | 0.96 / 0.93 |
| `diverse_10-01_0052` | solved 4 -> 2 | solved 9 -> 12 / 12 -> 12 |
| `hard_10-03_1340` (never solved) | gap -0.026 ± 0.082 | gap -0.003 ± 0.046 / -0.011 ± 0.041 |

The models whose quick search closes the gap with its neighbourhoods
(`dm_full_*`, `dm_medium_*`) are within 1%: the flip search hardly runs
there. The public check models (`check/instances`, 1% gap, one thread)
end with the same status as before.

## Graph LNS LPs that stall, and the end of a search's LP budget (2026-11-23)

**The case.** The gas-boiler model with the boilers left to the solver,
high price (two threads): one or two of the flip search's LP re-solves
per solve run to the per-LP iteration limit (a quarter of the first root
LP's count, about 1.5 s here) and are then solved again from scratch by
the interior point method, 2.6 s each: 2.6-5.0 s of solves of 11-13 s
(seeds 0-3).

**Why.** Graph LNS's LPs run the dual simplex without cost perturbation
(`mip_graph_lns_cost_perturbation` off: cheaper on the ordinary
re-solve, see [performance.md](./performance.md)), and without it a
degenerate re-solve can stall. `HighsLpRelaxation::run` answers an
iteration limit with the basis of a full interior point solve (IPX with
presolve, then the simplex from its basis). With the perturbation on for
every LP the same four seeds take 8.1-10.8 s (one such solve left on two
of them).

The same answer met a search's own budget: graph LNS caps each LP at
what is left of the search's LP iterations, so the last LPs of a search
that runs out stop at that cap, each then solved by interior point for
nothing (`ovn_0636_g147`, two threads, seed 0: three in a row, 0.6 s
each, at the end of the quick search).

**The change** (in `GraphLns::resolve`; only without
`mip_graph_lns_cost_perturbation`, so the C++ path is as it was):
- an LP that stops at the per-LP limit goes on with HiGHS's cost
  perturbation, and only if it stops again is it solved by interior
  point;
- an LP that stops because the search's budget ends is left there, and
  once the budget is used up no LP is solved: the dive, the
  neighbourhood's tree search and the flip search stop.

**Results.** The dispatch suite, paired against main on the runners
(bench-dispatch, 180 s, seeds 0-15, 36 models, 576 pairs per
architecture): the paths change only where a limit was met.

| | one thread (arm64) | two threads (arm64 / x86-64) |
|---|---|---|
| time to 1%, geometric mean over the models | 0.996 | 1.000 / 0.977 |
| solved, of 576 | 533 -> 535 | 555 -> 557 / 555 -> 559 |
| time to within 1% of the best known | 0.999 [0.997, 1.001] | 0.998 [0.995, 1.001] / 0.996 [0.991, 1.001] |
| `hard_10-03_1340` gap at the limit | -0.046 ± 0.039 | -0.011 ± 0.028 / +0.001 ± 0.038 |

By model at two threads the solved ones are within their seeds' spread
(`ovn_0652_g157` 0.84 [0.73, 0.96] and `tick_1018_0340` 0.87 [0.78,
0.97] on x86-64, `ovn_0716_g158` 1.15 [1.02, 1.29] on arm64, neither on
the other architecture); `diverse_10-01_0052` is solved in 13 and 15 of
16 runs (11 and 11).

The gas-boiler model, boilers free (M1, `CRESTLINE_INSTR_CLOCK=10`, two
threads, seeds 0-7, time to 1%; the first root LP is about 17):

| | before | after |
|---|---|---|
| high price | 30 31 30 28 30 34 23 18 (shifted geometric mean 27.7) | 20 22 20 20 22 24 20 19 (20.8, 0.75) |
| lower price | 16 16 16 17 22 18 17 16 (17.2) | 16 17 16 17 16 20 19 16 (17.1) |

The heater-HTF ticks and the boilers-off models are unchanged (4 seeds,
`CRESTLINE_INSTR_CLOCK=24`: 28.1 against 27.8 in the sum over the four
HTF ticks, 9.0 against 9.1 on the boilers-off model at the lower price).

**`mip_root_lp_ipx` by itself** (off by default) left the first root
LP's iteration count at the crossover's few hundred, and the graph LNS
budgets that scale with it near zero: every LP of the root dive stopped
at a limit of one iteration and was solved by interior point, 60 s
without an incumbent on `dm_full_pert_s1_ramp` (3.4 s by default). The
count is now the estimated effort whenever IPX alone solves the root LP
(as `mip_root_lp_effort_from_basis` does): 4.3 s.

## A neighbourhood search that stalls (2026-11-23)

**Where the nodes went.** On the ticks whose bound is ready long before
the incumbent (two threads, seed 0, both solvers' searches), the deep
search's gains come from its branch-and-bound neighbourhoods, 0.6 s
each at the node limit of 300:

| | searches | with a new incumbent | the first one within 25 / 100 nodes | searches at the node limit without one | their share of all nodes |
|---|---|---|---|---|---|
| `tick_1018_0340` | 75 | 40 | 22 / 38 | 12 | 26% |
| `diverse_10-01_0052` (60 s) | 95 | 24 | 11 / 18 | 24 | 46% |

A neighbourhood that pays does so early, and goes on paying (2.2-2.5
incumbents per successful search, the last one anywhere up to the
limit).

**The change** (`mip_graph_lns_stall_nodes`, 100): a search stops once
it has gone that many LP solves without a new incumbent. In 60 s that is
twice the neighbourhoods searched and 18-45% fewer nodes per incumbent
(`diverse_10-01_0052` 301 to 165, `tick_1018_0340` 125 to 102).

**Results.** Paired on the runners (bench-dispatch, 180 s, seeds 0-15,
36 models, 576 pairs per architecture), six configurations of one build:
the two options of this and the next section off (which is main), each
on, both on, this one at 50, and the next one at a ratio that never lets
the sub-MIP heuristics run. Time to 1% as the geometric mean over the
models of the ratio to the first; solved runs of 576:

| | two threads (arm64 / x86-64) | one thread (arm64) |
|---|---|---|
| stall at 100 | 0.913 / 0.921; 556 -> 560 / 553 -> 558 | 0.948; 535 -> 529 |
| stall at 50 | 0.900 / 0.924; 556 -> 555 / 553 -> 554 | 0.948; 535 -> 525 |
| the gap ratio 1.1 (next section) | 0.972 / 0.994; 556 -> 557 / 553 -> 556 | 0.978; 535 -> 540 |
| no sub-MIP heuristics at all | 0.977 / 0.991; 556 -> 556 / 553 -> 557 | 0.980; 535 -> 540 |
| stall at 100 and the gap ratio 1.1 | 0.910 / 0.915; 556 -> 559 / 553 -> 555 | 0.923; 535 -> 535 |
| ... primal gap at the end, difference | -0.003 ± 0.005 / -0.006 ± 0.003 | +0.000 ± 0.005 |

Stall at 100 by model, two threads: the overnight ticks 0.68-0.88,
`tick_1018_0340` 0.70 / 0.80, `tick_1018_0420` 0.77 / 0.87,
`tick_1025_0004` 0.79 / 0.76, `tick_1025_0028` 0.81 / 0.82,
`dm_small_windlull_noramp` 0.71 / 0.69; `diverse_10-01_0052` solved in
15 and 14 of 16 runs (12 and 9), and `hard_10-03_1340` once (never
before). At 50 the time is the same and fewer runs are solved. The
models that the quick search solves are unchanged. At one thread the
rule is faster on the same ticks (`ovn_0652_g157` 0.66,
`tick_1018_0420` 0.72, `tick_1025_0004` 0.84, `lambda_20260930_142050_3c1b60d6`
0.89) and the other overnight ticks are within their seeds' spread
(1.08-1.37 with intervals from 0.7 to 2.1), but 6 fewer runs reach 1%
in 180 s; the gap ratio of the next section gives those back.

On the MIPLIB models that HiGHS solves in under a minute (62, 1% gap,
one thread, x86-64; both options on against both off): the same
objective on every model, shifted geometric mean time 0.993.

Not kept: a node limit of 1000 with the same rule (4 seeds of six ticks,
local: equal or slower).

## A gap that is nearly closed (2026-11-23)

**The case.** `dm_small_windlull_noramp` (two threads, seed 0): 1.03%
after the root at 2.1 s, then 7.3 s in the root reduced cost and RENS
sub-MIPs (34 of them with their own inside, ten deep; no incumbent from
any), then 9 nodes of the tree move the bound the 0.03% that was
missing. A sub-MIP of this model takes over a second (its presolve, root
LP, cuts, feasibility jump and analytic centre), three times the model's
own root LP.

**The change** (`mip_heuristic_min_gap_ratio`, 1.1): while the gap is at
most that many times the target gap, the solver runs no root reduced
cost, RENS or RINS heuristic (`more_heuristics_allowed`): it goes to the
tree, and graph LNS keeps its own schedule. Only for a target gap of at
least 0.1% (`mip_rel_gap`), as graph LNS itself: that is where it was
measured.

**Results.** In the table of the section before: 0.972 / 0.994 by
itself at two threads, with `dm_small_windlull_noramp` at 0.74 / 0.75
(0.49 / 0.48 with both options) and `diverse_10-01_0052` solved in 13
and 12 of 16 runs (12 and 9); `ovn_0636_g147` 1.09 [0.91, 1.31] / 1.11
[1.02, 1.21], which goes with both options (0.78 / 0.86). Without the
sub-MIP heuristics altogether the time is the same (0.977 / 0.991): on
this suite they cost where the gap is nearly closed, and what they find
elsewhere is not missed; they stay for the models graph LNS does not
suit. At one thread the ratio by itself solves 5 more runs
(`tick_1018_0340` in 13 of 16 against 11, `tick_1018_0420` in 12 against
10) and takes `dm_small_windlull_noramp` to 0.51; the stall rule by
itself solves 6 fewer there (`tick_1018_0340` in 8, `ovn_0716_g158` in
14 of 16), and the two together as many as main, in 0.92 of its time.

## Main against v0.1.3 (2026-11-24)

PRs #82 to #93 together (the path separator's limits, the flip search
first and its memory, the main solver in the deep search, the stall
handling, the stall rule and the gap ratio, the refactorization clock,
the further deep searches), main against the tag on the runners
(bench-dispatch runs 45332205943 and 45332223172; 16 seeds, 36 models,
180 s, 576 pairs per architecture):

| | two threads (arm64 / x86-64) | one thread (arm64) |
|---|---|---|
| time to 1%, geometric mean over the models | 0.773 / 0.772 | 0.707 |
| solved runs of 576 | 557 -> 560 / 556 -> 556 | 526 -> 547 |
| time to within 1% of the best known | 0.898 / 0.894 | 0.919 |
| primal gap over the run, difference | -0.068 ± 0.009 / -0.067 ± 0.009 | -0.053 ± 0.011 |

By model at two threads: `tick_1018_0340` 0.49 / 0.49, `tick_1018_0420`
0.43 / 0.53, `dm_small_windlull_noramp` 0.46 / 0.46, the five `ovn_*`
0.57-0.74, `lambda_20260930_142050_3c1b60d6` 0.63 / 0.61, the
`tick_1025_*` 0.58-0.76, `dm_full_coarse24_noramp` 0.61 / 0.55, the
`dm_medium_*` 0.69-1.01; none is slower than 1.02
(`dm_full_prices_noramp` 1.02 / 0.98). The runs not solved at two
threads are `hard_10-03_1340` (none of 16 on either) and 0 / 4 of
`diverse_10-01_0052`. At one thread `tick_1018_0340` is solved in 14
runs of 16 (7 at v0.1.3) and `tick_1018_0420` in 16 (7), in half the
time on the runs both solve; `diverse_10-01_0052` in 5 (1).

(Before the last two changes, runs 45278857339 and 45278860587: 0.829 /
0.819 at two threads, 0.776 at one.)

## What the bound leaves of the 1% (2026-11-23)

**The measurement.** Main after PR #90 on the runners (the two-thread
arm64 runs above; the best known is the best incumbent of any run of the
two comparisons):

| | time to 1% (s, median) | the final bound, from the best known | incumbent within 1% of the best known after (s, median) |
|---|---|---|---|
| `tick_1018_0420` | 47 | 0.96% | 3.0 |
| `tick_1018_0340` | 52 | 0.92% | 3.8 |
| `diverse_10-01_0052` | 104 | 0.93% | 4.9 |
| `hard_10-03_1340` | never | 1.08% | 4.8 |
| the five `ovn_*` | 11-20 | 0.87-0.90% | 3.2-6.9 |
| `tick_1015_1901`, `tick_1023_2236`, the `tick_1025_*` | 7-10 | 0.84-0.89% | 2.2-4.1 |
| `lambda_20260930_142050_3c1b60d6` | 10 | 0.83% | 2.6 |

On every tick that takes more than a few seconds the bound ends
0.83-0.96% from the best plan known. Reaching 1% then needs an incumbent
within 0.04-0.17% of it; the incumbents are within 1% of the best known
after 2-7 s, and the rest of each solve goes into that last fraction.
There, a bound 0.1% of the objective tighter is worth more than any
change to the search. On `tick_1018_0340` the median incumbent would
close 1% at 40 s with a bound of -376126, at 20 s with -375336, at 12 s
with -374660; the bound ends at -376519.

**Per-bank bounds, with crest as the block solver** (the decomposition of
2026-11-01, `tick_1018_0340`). Without the names: dropping the rows with
more than 12 entries (110 of them, two per period) leaves one block per
bank (55 on/off binaries and one to nine modules) and a remainder
without integer columns. At the root LP's duals for the dropped rows,
each block is solved as a MIP (crest, one thread, M1); L is the sum of
the blocks' dual bounds, by the time allowed per block:

| time per block (s) | 0.1 | 1 | 2 | 5 | 10 | 30 |
|---|---|---|---|---|---|---|
| L | -376661 | -376428 | -376139 | -375749 | -375305 | -374789 |
| CPU time in all (s) | 2.3 | 20 | 37 | 74 | 123 | 208 |

(The LP bound is -379801, crest's root bound after its cuts -376583 on
seed 0.) A block's root cuts give what the model's root cuts give. What
goes past them is each block's tree: up to 6600 nodes, and the largest
block is still 1.3% open after 30 s. The bound that would stop the solve
at 20 s costs two minutes of CPU, so, as on `hard_10-03_1340` then: too
slow.

The same on four more ticks (30 s per block; crest's bound is the median
at the end of the two-thread runs above):

| | L | CPU time in all (s) | crest's bound |
|---|---|---|---|
| `tick_1025_0004` | -370861 | 125 | -372035 |
| `hard_10-03_1340` | -251240 | 130 | -251453 |
| `diverse_10-01_0052` | -249958 | 40 | -250166 |
| `ovn_0636_g147` | -193870 | 36 | -192965 |

So the per-bank bound is 0.3% of the objective past crest's on
`tick_1025_0004`, 0.09% on `hard_10-03_1340` and `diverse_10-01_0052`,
and behind crest's cuts on the overnight tick. On `tick_1025_0004` it
equals crest's bound at 0.1 s per block and is 0.13% past it at 2 s per
block (31 s of CPU, for a solve of 8 s); on `diverse_10-01_0052` it
passes crest's between 5 and 10 s per block (32-41 s of CPU, for a solve
of 100 s); `hard_10-03_1340` would need -250990 or better for its 180 s
incumbents.

**What the bound would be worth.** The 22 inequalities "this block's
objective at these prices is at least its bound" (the 30 s bounds),
added to the model as rows: 21, 11, 18 and 10 s to 1% (seeds 0-3, two
threads, M1) against 29, 57, 20 and 38 s, with a root LP that the 22
dense rows make slower. Crest's cuts add nothing on top of those rows
(the bound stays at L: at one vector of prices the rows make a whole
face optimal, and a cut that removes a vertex of it moves nothing).

**Where the gap comes from.** In a block's LP the switch sits at a
constant fraction over many periods, a duty cycle that holds the
modules' state of charge where the prices want it, at no switching cost;
a plan has to switch, pay for each change, and miss the level. A
relaxation that takes each module with its bank's switch but leaves the
switching cost outside gives the LP bound back (a constant fraction is
an average of on/off patterns). With the switching cost inside each
module's set (split equally among the modules), the modules' bounds, by
a subgradient method over the prices that tie their copies of the switch
together, reach all of what the bank's MIP adds to its LP on a bank of
one module, 92% on one of three and 45% on one of two (300 iterations
each).

A reformulation does not get there either. Splitting each module's
state of charge and discharge by the switch's state, and carrying the two
parts from period to period by the flows on-on, on-off, off-on and
off-off (the switching cost on the two that change), is exact for a
plan and tighter than the model's rows for a fraction; its LP bound is
13-22% of the way from the block's LP to its MIP on five blocks (one to
nine modules), less than the root cuts already cover. The flows can
carry any state of charge between none and the limit, so a long on
stretch costs the relaxation little.

**What it would take.** A module with its bank's switch is a
one-dimensional state (its state of charge) under a binary control with
a cost per change: its best plan at given prices is a dynamic program
over time with a piecewise linear value function, exact and far cheaper
than a tree. A bound from that needs the detection of the chains (as for
the storage cuts above), the dynamic program, an ascent over the prices
of the copies, and a way into the root (rows as above, or a bound next
to the LP's). Not built: it is specific to this family of models, and of
the suite only the `tick_1018_*` would gain much (the per-bank bounds
are 0.5% past crest's there, 0.1% on `hard_10-03_1340` and
`diverse_10-01_0052`, and behind crest's cuts on `ovn_0636_g147`).

For the application: on these ticks the plan after 10 s is within
0.2-0.6% of the best known. The 1% that takes the rest of the time is
mostly the bound's distance from the optimum, not the plan's.

## Further deep searches at the root (`mip_graph_lns_deep_rounds`, 2026-11-24)

**The case.** Where the main solver's thread spends a two-thread solve
of `tick_1018_0340` (M1, sampled over the whole solve):

| | a slow run (51 s) | a fast run (25 s) |
|---|---|---|
| graph LNS | 27% | 73% |
| sub-MIP heuristics (root reduced cost, RENS, RINS) | 50% | 0% |
| the tree | 8% | 0% |

After its cut rounds the main solver runs one deep search. The helper
runs them in a loop and finds most improvements first, so the main
solver's own neighbourhoods stall and its search ends early; what
follows are the sub-MIP heuristics and the tree, which find nothing on
these models (every incumbent is graph LNS's, 2026-10-18) while the gap
is still open. The sub-MIPs' analytic centre LPs also run IPX on a third
thread of the pool. With one thread the order is the same: one deep
search, the sub-MIP heuristics, a turn of the tree, and only then graph
LNS again.

**The change.** After the deep search at the root, while the target gap
is not reached and the last round (with what a helper found meanwhile)
closed 1% of the gap, another deep search, up to 50: the rule of PR #86
for a wide gap with a helper, now for any open gap and without a helper
too. When a round does not pay, the sub-MIP heuristics and the tree
follow as before. `mip_graph_lns_deep_rounds = false` is the old path
(the oracle's setting).

**Local** (M1, instruction clock). Two threads, 8 seeds:
`tick_1018_0340` 33.9 -> 28.7 s and `tick_1018_0420` 33.9 -> 29.5 s
(shifted geometric means); the heater-HTF and gas-boiler models are
unchanged (4 seeds each; they solve before the rule acts). One thread, 8
harder models, 4 seeds: 21 of the 32 pairs are the same run (solved
within the first deep search), 10 of the other 11 are faster
(`ovn_0636_g147` 140 -> 28 s, `tick_1018_0420` 158 -> 67 s,
`tick_1018_0340` 169 -> 96 s), over all 0.865 [0.768, 0.975].

**Runners** (bench-dispatch, 16 seeds, 36 models, 180 s, 576 pairs per
architecture; run 45321925536 at two threads against main, run
45324817093 at one thread against the option off):

| | two threads (arm64 / x86-64) | one thread (arm64) |
|---|---|---|
| time to 1%, geometric mean over the models | 0.993 / 0.989 | 0.947 |
| solved runs of 576 | 558 -> 559 / 558 -> 560 | 541 -> 547 |
| time to within 1% of the best known | 0.997 [0.994, 1.000] / 0.998 [0.996, 1.001] | 1.001 [0.999, 1.003] |

At two threads it acts on the slowest solved ticks: `tick_1018_0340`
0.82 [0.71, 0.95] / 0.94 [0.87, 1.02], `tick_1018_0420` 0.86 [0.76,
0.98] / 0.89 [0.82, 0.96], `ovn_0652_g157` 1.00 / 0.82 [0.70, 0.97],
and `diverse_10-01_0052` is solved in 15 runs of 16 instead of 14 and
13. `dm_small_windlull_noramp` is at 1.06 [0.98, 1.14] / 1.06 [0.95,
1.17] (a 3.4 s model that the bound closes); the rest are within 4%.
At one thread the models that solve within the first deep search take
the same path. The others: `tick_1018_0340` 0.73 [0.58, 0.91] with 14
runs solved against 12, `tick_1018_0420` 0.73 [0.59, 0.92] with 16
against 13, the five `ovn_*` 0.76-0.94 (`ovn_0636_g147` and
`ovn_0652_g157` in 16 runs against 15), `diverse_10-01_0052` 0.63 [0.49,
0.81] on the runs both solve (5 against 6 of 16).

MIPLIB (bench-std run 45325591560: the 66 models HiGHS solves in under
a minute, `mip_rel_gap = 0.01`, one thread, arm64, against the option
off): the same node count and objective on 64 models; on `nu25-pr12`
and `mc11` another incumbent within the 1% (at the root, in the same
time).

## Tried and rejected

- Devex pricing for the LNS LPs: cheaper per iteration on dense ramp LPs,
  but twice the instructions on the hardest tick (kept DSE weights pay).
- Neighbourhood LPs started from the incumbent with the primal simplex:
  twice the iterations of the dual simplex from the last LP's basis.
- In a dive, fixing a conflicting rounding the other way, or leaving it
  out, instead of undoing the chunk: 14-16% longer on the suite.
- A second presolve pass, or a forced restart after the root: the root LP
  of `hard_10-03_1340` gets cheaper, but aggregation rewires the
  neighbourhoods and the suite gets slower.
- No dual simplex cost perturbation on re-solves: chaotic on MIPLIB
  (mzzv42z +49%).
- Checking the factorization's accuracy only every 50 updates: changes
  search paths and drops a safeguard.
- RINS at the root after a stalled deep search; fix-and-optimize over time
  windows as a 180 s heuristic.
- A pool recombination move for the deep search (ReXi-style crossover of
  the last four incumbents): slightly slower, fewer solved
  `diverse_10-01` runs (see [performance.md](./performance.md)).
- `mip_concurrent_crossover` on two threads: 1.12x the time on the solved
  dispatch models.
- Time-window neighbourhoods, free or pinned (see above).
- BFS edges from each decision column that shares no row with another
  integer column (on/off columns linked only through continuous flows) to
  its nearest decision columns: 0 of 6 seeds of the gas-boiler model
  reached 1% (1 of 6 before).
- A bandit over the LNS LPs' cost perturbation (see above).
- Time-ordered fix-propagate with backtracking at the root (see above).
- Storage valid inequalities as root cuts (measured; see above).
- A smaller share of LP iterations for the tree search between graph LNS
  rounds (see above).
- Skipping the sub-MIP heuristics while graph LNS works (see above).
- Neighbourhood LPs from the basis of the last incumbent's LP (see above).
- The LP disagreement set as a dived or searched neighbourhood (see above).
- A trust region sub-MIP around a MIP start that cannot be completed
  (`mip_start_trust_radius`): 2.4-3.5 times the time to 1% of the best
  known (see "Warm starts" above).
- A per-bank Lagrangian decomposition: a better bound on three of four
  ticks but 6-20 CPU minutes per evaluation, and no usable schedules (see
  above).
- Per-period integer count columns of the switches (a general disjunction
  to branch on): presolve substitutes them out; kept (presolve_rule_off),
  the bound does not move and the incumbents get 4-8% worse (see above).
- Seeding neighbourhoods by reduced-cost gain summed over short-row
  chains (see above): fewer solved runs, no 2-thread gain.
- The path separator past its work limit at every root round, or while
  each sweep over the rows gains a share of the target gap, or with no
  incumbent: the heater-HTF ticks 1.2-2.5 times slower (see "The path
  separator's work limit at the root").
- Rounding zero-cost on/off columns up in the dives, probing them, and
  lazy dives (see "The flip search first").
- Sum-up rounding, time-ordered relax-and-fix, and block coordinate
  descent over banks, as constructions or moves on the gas-boiler model
  (see "Where the truth is on the gas-boiler model").
- `simplex_update_limit` 60 or 200 for the LNS LPs (more refactorization):
  the same progress per instruction on the gas-boiler model.
- The flip search first after a root dive that leaves a wide gap, on
  models without isolated decision columns (see "The flip search
  first").
- A plain memory of the flip search's failed moves, forgotten when none
  is left to try (see "The flip search's memory of failed moves").
- The deep search starting each call with its flip search while that
  makes progress, rather than with its two branch-and-bound
  neighbourhoods: of the five overnight ticks three slower (1.1-1.4) and
  two faster (0.9), over 4 seeds at one thread (M1, instruction clock).
- The flip search trying partners beyond a candidate's first three once
  those are known to fail (the first 3 of its first 8 or 20 not in the
  memory): time 0.98 / 0.98 and 0.97 / 1.01 on the 18 harder models at
  two threads, time to within 1% of the best known 1.00-1.02.
- No sub-MIP heuristics in the main solver at all, again (see "A gap that
  is nearly closed").
- The per-bank Lagrangian bound with crest as the block solver, as rows
  in the root: worth 0.44 of the time on `tick_1018_0340` once given,
  two minutes of CPU to compute (see "What the bound leaves of the 1%").
