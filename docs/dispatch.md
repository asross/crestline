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
- A flip search polishes the incumbent.
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
  promising flip with an opposite flip in one of its rows (swaps);
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
- `mip_concurrent_crossover` (off by default): the two search independently
  until the helper crosses its incumbent with the main solver's: the
  integer columns where they agree are fixed and the rest solved as a
  sub-MIP.

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
