# Graph LNS

Graph LNS is Crestline's primal heuristic for scheduling MILPs with
time-indexed on/off decisions: unit commitment with storage, where each
period has switches, rates, chains of equalities that carry a state from
one period to the next, and ramp limits. The LP relaxations of such models
often have a tight bound but a misleading vertex: a fractional switch lets
a unit run part-way all the time, which no integer schedule can do. Branch
and bound from that vertex makes slow progress, and local search moving
one variable at a time is defeated by the equality chains of continuous
state and ramp variables. What finishes a solve at a loose gap (say
`mip_rel_gap = 0.01` within a few minutes) is a good incumbent.

The code is in `src/mip/lns.rs` (decision columns, neighbourhoods, move
choice, flip candidates) and `src/mip/graph_lns.rs` (the dives,
neighbourhood branch and bound and flip search), with the concurrent
helper in `src/mip/concurrent.rs`. It runs only for `mip_rel_gap >= 1e-3`,
so solves at the default gap are unaffected.

It was developed on a private suite of scheduling MIPs of one application
(36 models), run at `mip_rel_gap = 0.01` with a 180 s limit and 16 seeds
on x86-64 and arm64 runners (docs/benchmarks.md). The suite is not public:
this file gives aggregate results on it, and results by model on public
MIPLIB models.

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
  iterations, so that where neighbourhood LPs are expensive the root cuts
  come early.

**Deep search** (after the root cuts, before the sub-MIP heuristics, only
if the quick search improved the incumbent and brought it within three
times the target gap of the bound; otherwise the model does not suit graph
LNS and the usual root heuristics run; but see `mip_graph_lns_deep_early`
below). Neighbourhoods are smaller and are searched by a depth-first
branch and bound with a node limit (300 LP solves; a search that goes 100
without a new incumbent stops, see "A neighbourhood search that stalls"),
their size adapting to what that limit exhausts. A UCB bandit picks, among
four moves, the one closing the most gap per LP iteration:
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
over the model rows, without an LP. On the hardest model of the private
suite, 92% of flip LPs had been infeasible at about 2.5 simplex iterations
each, so the fixed cost of a solve was all they cost. A move ruled out
this way counts as a quarter of an LP solve in the flip search's budget. A
flip search resumes where the last one from the same incumbent stopped.

**Stopping.** The quick search stops after 5 neighbourhoods that do not
close 5% of the gap, the deep search after 10 that do not close 1% of what
separates the incumbent from the target gap; both also stop on an LP
iteration budget or when the incumbent reaches the target gap. If the deep
search pays, further rounds follow at the root (see "Further deep searches
at the root") and alternate with the tree search, where strong branching
then starts with a smaller budget (10000 LP iterations rather than
100000).

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
  the tree: with one thread a gap that wide sends the time to those, and
  on one model they found nothing in 180 s while every incumbent came
  from the helper.
- `mip_concurrent_crossover` (off by default): the two search independently
  until the helper crosses its incumbent with the main solver's: the
  integer columns where they agree are fixed and the rest solved as a
  sub-MIP. On two threads it took 1.12 times the time on the solved models
  of the private suite.

From three threads on there is a helper on each thread but the main
solver's, at most the physical cores less one, each with its own seed
(docs/benchmarks.md).

The helper starts its root LP from the main solver's basis, so its own
first root LP count is about 0; it takes the main solver's count instead,
since the LNS LP iteration limits and the deep-search and flip budgets
scale with it. Before (2026-11-20) a helper on a model whose root LP takes
about 11k iterations ran every neighbourhood LP at the 1000-iteration
floor, where they all stopped, and found nothing in 180 s.

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

## Rules added since the first version

Each of these is an option (see "Options"); with all of them off, and the
C++ app's setting of the others, graph LNS takes the path of the C++
implementation it was ported from, which the CI oracle checks.

### When the quick search never searched (`mip_graph_lns_deep_early`, off)

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

Results (docs/performance.md): no change on the private suite on either
architecture (it does not fire there); on uccase9 it fires on 2-3 of 16
seeds, which end near 13200 instead of near 30000; on 4 of 47 MIPLIB
instances it costs 1.01-1.41 times the instructions. So it stays off: the
gain is real but narrow, and gates that fire on more uccase9 seeds cost
too much on general MIP.

### The flip search first (`mip_graph_lns_flip_first`)

The quick search starts with its flip search, rather than ending with it,
in two cases:
- The root dive left the incumbent within three target gaps of the bound.
  What is missing is then mostly single columns rounded the wrong way,
  which a flip puts right at an LP solve each (first improvement, in order
  of the gain the reduced costs promise), where a neighbourhood costs an
  LP of some hundred iterations and a dive. Often the gap closes there.
- Some decision column shares no row with another integer column (an
  on/off column tied to the rest of the model only through a continuous
  column). No breadth-first neighbourhood reaches such a column from the
  columns around it. On a model with such columns the root dive rounds
  each of them down wherever the LP has it under one half, at a high cost
  elsewhere; each one's flip improves on its own, and 18 LP solves took a
  13% gap to under 1%, where the neighbourhoods had used up the budget on
  the switches around them.

With a wider gap and no such column the neighbourhoods go first: the
flips first reach a good incumbent sooner there but the target gap no
sooner, and on the hardest model of the private suite they leave a
slightly worse incumbent at the time limit.

### The flip search's memory of failed moves (`mip_graph_lns_flip_memory`)

A move that did not improve is not tried again until a decision column in
a row of one of its columns has changed (the don't-look bits of local
search). After each improving move the search starts again from the best
candidate, and without the memory most of what it then tries are the
moves that have just failed: on one model 15000 of 16000 tries in a
solve, of which 3 improved. A plain memory that is forgotten once no move
is left to try converges faster to worse local optima and was not kept.

On the private suite, against both rules off (16 seeds): the memory alone
0.948 of the time at one thread and 0.963 / 0.953 at two (arm64 /
x86-64, as in every such pair below); the flip-first rule alone 0.950 and
0.970 / 0.968; both 0.832 and 0.926 / 0.909, with 9 more runs solved at
one thread and better incumbents at the limit.

### LPs that stall, and the end of a search's LP budget

Graph LNS solves its LPs without dual cost perturbation
(`mip_graph_lns_cost_perturbation` = false). A degenerate re-solve can
then stall at the per-LP iteration limit (a quarter of the first root
LP's iterations), after which the LP relaxation fell back on a full IPX
solve, 2.6 s each on one model. Now a stalled LP is first solved again
with cost perturbation, and once a search has used up its LP iteration
budget no further LP (or IPX solve) is started for it. On the model where
it was found: 0.75 of the time; the private suite: neutral, 2 to 4 more
runs solved.

### A neighbourhood search that stalls (`mip_graph_lns_stall_nodes`, 100)

A neighbourhood's depth-first search that has gone 100 LP solves without
a new incumbent stops. A neighbourhood that pays does so early: on the
hard models of the private suite the first incumbent of a search comes
within 100 solves in nine of ten, while 26-46% of all solves went to
searches that ran to the 300-node limit without one. A limit of 50 gives
the same time and fewer runs solved; a node limit of 1000 with the stall
rule was no better.

### A gap that is nearly closed (`mip_heuristic_min_gap_ratio`, 1.1)

With `mip_rel_gap` of at least 0.001, the root reduced cost, RENS and
RINS heuristics do not run while the gap is at most 1.1 times the target
gap: a sub-MIP then costs more than the little that is left to close.

On the private suite (16 seeds, time against both rules off): the stall
rule 0.913 / 0.921 at two threads with 4 and 5 more runs solved, 0.948 at
one thread but 6 fewer solved; the gap ratio 0.972 / 0.994 at two
threads, 0.978 and 5 more solved at one; both 0.910 / 0.915 at two
threads and 0.923 with as many solved at one. No sub-MIP heuristics at
all: 0.977 / 0.991, so the rule keeps what they are worth. MIPLIB (the 66
models HiGHS solves in under a minute, at a 1% gap): the same answers,
time 0.993.

### Further deep searches at the root (`mip_graph_lns_deep_rounds`)

After its deep search at the root, graph LNS runs further deep searches
(up to 50) while the gap is open and each closes 1% of it, with or
without a helper, before the sub-MIP heuristics and the tree. Those found
nothing on the hard models of the private suite: in slow two-thread runs
they took half of the main solver's time, while the incumbents came from
the deep searches, and the main solver's one deep search ended early
because the helper found the improvements first.

On the private suite (16 seeds): 0.947 of the time at one thread, with
541 -> 547 runs solved (models solved within the first deep search keep
their path); 0.993 / 0.989 at two threads, 558 -> 559 / 560. MIPLIB at a
1% gap: 64 of 66 models take the same path.

### The path separator's limits

HiGHS's path separator aggregates rows along paths of continuous columns
and hands each aggregation to the cut generator. Two limits:
- A work limit per call: 60 aggregated row entries per LP nonzero. A call
  that reaches it stops, and the separator stays off from then on, except
  at the root while the gap to an incumbent is over three target gaps,
  where it is called again from the row where it stopped. On the private
  suite a call passes at most 41 entries per nonzero, so nothing changes
  there; on
  models with dense coupling rows it passed 190-250, about 4 s a call and
  nearly all of each root cut round, for dense cuts that slow the LP and
  the search. Past the limit at every root round, or with no incumbent,
  such models were 1.2-2.5 times slower.
- A length limit: a path stops once its aggregation has more entries than
  100 plus 20 average LP rows (in the spirit of SCIP's density limit on
  aggregations). On models with dense coupling rows the aggregations
  through them (30-130 average rows long) took 99% of a call's work and
  gave no cut (the cuts came from aggregations under 100 plus 16 average
  rows); with the limit a call there costs 1-3% as much.

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
  of one model after single fixings;
- after an infeasible LNS LP, the previous simplex iterate (factor and edge
  weights) is restored rather than refactorized;
- the scaled matrix is kept between solves while the matrix and scale
  factors are unchanged, the factor's solve error is not recomputed for
  an unchanged factor, and passes whose results only feed the log are
  skipped without output;
- the dual simplex's clock for refactorization keeps running over
  rebuilds that do not refactor (`mip_lp_refactor_clock`).

See docs/performance.md for each. Cut separation also skips an
aggregation whose transformation has no integer column at a fractional
value, which cannot give a violated cut (72% of the calls on the hardest
model).

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
| `mip_graph_lns_flip_first` | true: the quick search starts with its flip search when the root dive leaves the incumbent within three target gaps of the bound, or when some decision column shares no row with another integer column (false: it ends with it, as the C++) |
| `mip_graph_lns_flip_memory` | true: the flip search leaves a move that did not improve untried until a decision column in a row of one of its columns has changed (false: as the C++) |
| `mip_graph_lns_stall_nodes` | 100: graph LNS stops a neighbourhood's tree search that has gone this many LP solves without a new incumbent (0: only at the node limit, as the C++) |
| `mip_heuristic_min_gap_ratio` | 1.1: with `mip_rel_gap` of at least 0.001, no root reduced cost, RENS or RINS heuristic while the gap is at most this many times the target gap (0: no such rule, as HiGHS) |
| `mip_graph_lns_deep_rounds` | true: after its deep search at the root, graph LNS runs further deep searches while the gap is open and each closes 1% of it (false: one deep search, then the sub-MIP heuristics and the tree) |
| `mip_lp_refactor_clock` | true: the MIP's LP relaxation is solved with `simplex_refactor_clock_over_rebuilds` (false: as in HiGHS; see [performance.md](./performance.md)) |
| `simplex_refactor_clock_over_rebuilds` | false (true in the MIP's LP relaxation): the dual simplex's clock for refactorization keeps running over rebuilds that do not refactor |
| `simplex_dse_exact_init_max_rows` | no limit (20000 in the MIP's LP relaxation) |
| `simplex_keep_random_vectors` | false (true in the MIP's LP relaxation) |
| `full_lp_kkt_check` | true (false in the MIP's LP relaxation) |

To compare options or builds: the `bench-paired` workflow (paired A/B
runs on x86_64 and arm64 runners, `bench/paired.py`; docs/benchmarks.md).

## Results on public models

`uccase9`, `unitcal_7`, `irish-electricity` and `buildingenergy` (MIPLIB
2017; the `bench-paired` workflow downloads them for the instance glob
`miplib/*.mps.gz`) are unit commitment and energy models with features
the private suite lacks. On the runners (one thread, 1%, 180 s, 16 seeds,
median over seeds, x86_64 / arm64):

| | first solution | gap at 180 s | two threads: gap |
|---|---|---|---|
| `unitcal_7` | 5.9 / 6.8 s | 0.27% (solved) | 0.28% / 0.30% |
| `uccase9` | 9.0 / 8.9 s | 61% / 63% | 11.2% / 11.1% |
| `irish-electricity` | 72 / 90 s | 3.39% | 2.94% / 2.95% |
| `buildingenergy` | 134 / 149 s | 1.48% / 1.52% | 1.52% / 1.54% |

- `uccase9`: see `mip_graph_lns_deep_early` above (on two threads the
  helper's ungated deep search does the work).
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

General MIP is not slowed: on a 31-instance MIPLIB regression set (300 s,
2 seeds, shifted geometric mean of instructions over runs both solve),
0.919 at the default gap (graph LNS off) and 0.916 at `mip_rel_gap =
0.01`, against HiGHS 1.15.1.

## Results on the private suite

The first version (the C++ implementation of the same algorithms, which
Crestline follows iteration for iteration with the rules above off),
single thread, `mip_rel_gap = 0.01`, against HiGHS 1.15.1 on the 23 models
the suite then had:

| 23 models | HiGHS 1.15.1 | with graph LNS |
|---|---|---|
| reach 1% within the 300 s (wall) limit | 14 | 23 |
| total CPU time | over 2264 s | 313 s |

- Every model HiGHS 1.15.1 solves is at least 4.5 times faster (9.2 times
  at the median).
- The hardest of them over 16 random seeds: all reach 1%, median 160G
  instructions (81G to 231G); HiGHS 1.15.1 reaches 1% in 300 s with none.
- Held-out set (two copies of each model with every distinct objective
  coefficient scaled by its own lognormal(0, 0.15) factor): 46 of 46 reach
  1% in 300 s against 41 of 46, with 0.108 of the instructions (geometric
  mean).
- Two threads: the main thread retires 0.70 (geometric mean, 46 runs) of
  the single-thread solve's instructions.

Since then (36 models, 16 seeds, 180 s; the main that became v0.1.4
against v0.1.3, that is the rules of 2026-11 above and the
refactorization clock): 0.773 / 0.772 of the time at two threads (arm64 /
x86-64), with 557 -> 560 and 556 -> 556 of 576 runs solved; 0.707 at one
thread, 526 -> 547 solved; no model slower than 1.02.

Not every model is limited by its incumbent. On one, the root bound after
cuts and the best solution known are 1.13% apart: certifying 1% needs a
better bound, not a better incumbent, and no change to the search reaches
it in 180 s. On the models still slow, the bound at the end is 0.83-0.96%
from the best solution known, so the incumbent has to come within
0.04-0.17% of it; a tenth of a percent of bound is worth more there than
tuning the search.

## Tried and rejected

- Devex pricing for the LNS LPs: cheaper per iteration on dense LPs, but
  twice the instructions on the hardest model (kept DSE weights pay).
- Neighbourhood LPs started from the incumbent with the primal simplex:
  twice the iterations of the dual simplex from the last LP's basis.
  Started from the basis of the last incumbent's LP: rejected too.
- In a dive, fixing a conflicting rounding the other way, or leaving it
  out, instead of undoing the chunk: 14-16% longer on the suite.
- A second presolve pass, or a forced restart after the root: one root LP
  gets cheaper, but aggregation rewires the neighbourhoods and the suite
  gets slower.
- No dual simplex cost perturbation on re-solves: chaotic on MIPLIB
  (mzzv42z +49%).
- Checking the factorization's accuracy only every 50 updates: changes
  search paths and drops a safeguard.
- RINS at the root after a stalled deep search; fix-and-optimize over time
  windows as a 180 s heuristic.
- A pool recombination move for the deep search (ReXi-style crossover of
  the last four incumbents): slightly slower, fewer runs solved.
- Time-window neighbourhoods, free or pinned; a bandit over the LNS LPs'
  cost perturbation; time-ordered fix-propagate with backtracking at the
  root: each rejected on the runners.
- BFS edges from each decision column that shares no row with another
  integer column to its nearest decision columns: 0 of 6 seeds of one
  model reached 1% (1 of 6 before).
- Valid inequalities from the state chains as root cuts (measured
  offline, not built): no root bound to gain.
- A smaller share of LP iterations for the tree search between graph LNS
  rounds; skipping the sub-MIP heuristics while graph LNS works; the set
  where the incumbent disagrees with the LP as a dived or searched
  neighbourhood: no change, or failed the screen.
- A trust region sub-MIP around a MIP start that cannot be completed: 2.4
  to 3.5 times the time to 1% of the best known.
- A Lagrangian decomposition by blocks of units: a better bound on some
  models (0.1-0.5%), which given as rows is worth up to 0.44 of the time
  on one of them, but minutes of CPU per evaluation and no usable
  schedules.
- Per-period integer count columns of the switches (a general disjunction
  to branch on): presolve substitutes them out; kept, the bound does not
  move and the incumbents get 4-8% worse.
- Seeding neighbourhoods by reduced-cost gain summed over short-row
  chains: fewer runs solved, no two-thread gain.
- Rounding zero-cost on/off columns up in the dives, probing them, and
  lazy dives; sum-up rounding, time-ordered relax-and-fix, and block
  coordinate descent over blocks of units, as constructions or moves.
- `simplex_update_limit` 60 or 200 for the LNS LPs (more
  refactorization): the same progress per instruction.
- The deep search starting each call with its flip search while that
  makes progress: of five models three slower (1.1-1.4) and two faster
  (0.9).
- The flip search trying partners beyond a candidate's first three once
  those are known to fail: time 0.97-1.01 on the 18 harder models at two
  threads, time to within 1% of the best known 1.00-1.02.
- The flip search run to exhaustion when it goes first, instead of 300
  solves: diminishing returns, and the dives that follow improve less.
