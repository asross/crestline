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
- A flip search polishes the incumbent. It runs first instead if some
  decision column shares no row with another integer column and the root
  dive left the incumbent over three target gaps from the bound (see
  "Isolated decision columns: the flip search first").
- On large models it spends at most about one root LP's worth of LP
  iterations, so that where neighbourhood LPs are expensive (ramping
  models) the root cuts come early.

**Deep search** (after the root cuts, before the sub-MIP heuristics, only
if the quick search improved the incumbent and brought it within three
times the target gap of the bound; otherwise the model does not suit graph
LNS and the usual root heuristics run; but see `mip_graph_lns_deep_early`
below). Neighbourhoods are smaller and are
searched by a depth-first branch and bound with a node limit (300 LP
solves), their size adapting to what that limit exhausts. A UCB bandit
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
| `mip_graph_lns_flip_first` | true: the quick search starts with its flip search when some decision column shares no row with another integer column and the root dive leaves a wide gap (false: it ends with it; see "Isolated decision columns: the flip search first") |
| `mip_graph_lns_flip_memory` | true: the flip search leaves a move that did not improve untried until a decision column in a row of one of its columns has changed (false: as the C++; see "The flip search's memory of failed moves") |
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

## Isolated decision columns: the flip search first (2026-11-23)

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
shares no row with another integer column. With such columns and an
incumbent over three target gaps from the bound after the root dive, the
quick search runs the flip search first (candidates in order of the gain
their reduced cost promises, each kept only if the LP improves). The
option `mip_graph_lns_flip_first` (on) is there for the oracle: two of
its check models (`egout`, a fixed-charge network, and a
semi-continuous test model) have such columns and change path, so
`bench/quick_check.sh` turns it off for the comparison with the C++
app. No model of the dispatch suite, the heater-HTF ticks or the
boilers-off gas models has such a column, so their paths are the same
(equal one-thread logs on those checked).

**Results** (M1, `CRESTLINE_INSTR_CLOCK=10`, so load-independent: 10G
instructions of the two threads count as a second, which is 0.42 s of
wall time on the idle machine; 2 threads, 1% gap, seeds 0-7; time to 1%
in those seconds, the first root LP being about 17 of them):

| | main | flip search first |
|---|---|---|
| boilers free, high price | 61 138 90 28 83 79 25 23 (shifted geometric mean 56.4) | 29 31 30 28 30 33 25 18 (27.6, 0.49 of main) |
| ... first incumbent / after the flip search (seed 0) | 13.5% | 13.5% / 0.90%, 18 LP solves |
| boilers free, lower price (1.7 target gaps after the root dive: the same path, equal logs at one thread) | 20 48 35 22 22 24 22 20 (25.6) | 20 42 34 22 33 24 31 21 (27.6) |

By the wall clock (same machine, lightly loaded, seeds 0-2) the
high-price model now takes 11.3, 15.1 and 13.5 s.

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
  LP solves: this is the lazy dive of "Isolated decision columns: the
  flip search first";
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
  lazy dives (see "Isolated decision columns: the flip search first").
- Sum-up rounding, time-ordered relax-and-fix, and block coordinate
  descent over banks, as constructions or moves on the gas-boiler model
  (see "Where the truth is on the gas-boiler model").
- `simplex_update_limit` 60 or 200 for the LNS LPs (more refactorization):
  the same progress per instruction on the gas-boiler model.
