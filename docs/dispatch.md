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
LNS and the usual root heuristics run). Neighbourhoods are smaller and are
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
  weights) is restored rather than refactorized.

Cut separation also skips an aggregation whose transformation has no
integer column at a fractional value, which cannot give a violated cut
(72% of the calls on the hardest tick).

## Options

| Option | Default |
|---|---|
| `mip_heuristic_run_graph_lns` | true |
| `mip_concurrent_helper` | true |
| `mip_concurrent_crossover` | false |
| `mip_root_lp_devex` | false (see [performance.md](./performance.md)) |
| `simplex_dse_exact_init_max_rows` | no limit (20000 in the MIP's LP relaxation) |
| `simplex_keep_random_vectors` | false (true in the MIP's LP relaxation) |
| `full_lp_kkt_check` | true (false in the MIP's LP relaxation) |

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
