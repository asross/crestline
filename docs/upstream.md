# HiGHS: dual proofs that drop tiny coefficients (fixed upstream)

**Status.** Fixed in HiGHS by
[PR #3223](https://github.com/ERGO-Code/HiGHS/pull/3223) ("Fix
traininstance6 bug", Mark Turner), merged into `latest` on 2026-10-20 and
not yet in a release (1.15.1 has the bug). Crest follows that fix by
default; the cargo feature `highs_dual_proofs` keeps 1.15.1's behaviour
for the CI oracle. We found the same bug independently and had drafted a
report; it is no longer needed.

## The bug (HiGHS 1.15.1)

`computeDualProof` and `storeDualInfProof` in
`highs/mip/HighsLpRelaxation.cpp` build a proof `sum_j a_j x_j <= rhs`
(`a = c - y^T A` from the LP duals, or `a = y^T A` from a dual ray) and
skipped every entry with `|a_j| <= 1e-9`. Unlike the entries below
feastol a few lines later, they did not move `a_j * bound_j` to the
right-hand side, so with large bounds the proof was strengthened past
validity. `checkDualProof()` would not catch it: its result is ignored
(`if (true || checkDualProof())`).

What we saw in crest: on traininstance6 (MIPLIB 2017, optimum 28290) a
strong-branching ray proof with 47 coefficients of about 1.1e-6 and
right-hand side 5.1e-5 dropped an entry on an integer column with a bound
of about 29000. That tightened it by about 3e-5 and cut off the optimum;
the conflict derived from it ended in "optimal" 28350 (seed 9), and 28380
or 29090 on nearby paths. HiGHS 1.15.1 itself gave 28290 on seeds 0-11:
the code was the same, but we had no HiGHS run that ended wrong. The PR
reports occasional failures on traininstance6 and traininstance2.

## The upstream fix

- `HighsDomain::termIsZero(val, lb, ub, epsilon)`: an entry is dropped only
  if `|val| <= epsilon` and `|val| * max(|lb|, |ub|) <= epsilon`; the rest
  take the feastol path to the right-hand side (no proof if the bound is
  infinite). Also used in `HighsTransformedLp::transform`/`untransform`.
- Exact (`HighsCDouble`) products in the proof sums, the ray aggregation,
  `tightenCoefficients`, objective propagation and
  `extractCliquesFromCut`. The PR notes that this alone fixed the case it
  isolated.
- [#2874](https://github.com/ERGO-Code/HiGHS/issues/2874) problem 4: bounds
  derived in `extractCliquesFromCut` use the tolerance
  `max(feastol, epsilon / |coefficient|)`.

Our own earlier fix (move every nonzero entry to the right-hand side)
was stricter and cost about 1% of MIPLIB time; crest now uses the
upstream version so that the oracle can follow HiGHS once it is released.

## Other HiGHS fixes made after 1.15.1 that crest follows (2026-11-24)

The fuzz workflow compares against the latest HiGHS release and its
`check/instances`. With 1.16.0 that brought three reproducers of bugs
fixed upstream after 1.15.1, which crest (a port of 1.15.1) still had,
v0.1.3 included. Crest now follows the fixes by default; the oracle's
build (`highs_dual_proofs`) keeps 1.15.1's code.

| check instance | crest before | now | the HiGHS fix |
|---|---|---|---|
| `issue-2900` | optimal -0 (wrong) | optimal 294856559.369 | [PR #3234](https://github.com/ERGO-Code/HiGHS/pull/3234): `HPresolve::strengthenInequalities` keeps its reduced costs, upper bounds and steps in double-double arithmetic; with doubles a row with large coefficients lost its right-hand side |
| `issue-3461` | optimal 23138 (wrong) | optimal 21166 | [PR #3468](https://github.com/ERGO-Code/HiGHS/pull/3468): in the same function, a term whose largest contribution is within the feasibility tolerance (and a cover's slack that small) is counted in the row's violation instead of being dropped; dropped, the strengthened row cut off the optimum |
| `issue-3166` | panic (a cell end before its start) | solves | [PR #3182](https://github.com/ERGO-Code/HiGHS/pull/3182): `HighsSymmetryDetection::partitionRefinement` removes the cell records and certificate entries of the splits it undoes when it prunes |

## Draft, not filed: bounds and substitutions through tiny coefficients cut off feasible points

**Version.** HiGHS 1.15.1 (`highs/mip/HighsDomain.cpp`,
`highs/presolve/HPresolve.cpp`); the same code in Crestline, which gives
the same answers.

**Symptom.** Differential fuzzing (docs/benchmarks.md, "Fuzzing for wrong
answers") of small random MIPs whose rows mix coefficients of 1e-9..1e-6
with ones of 1e2..1e6 finds models that HiGHS calls infeasible, or solves
to a wrong optimum, with default options, where a solution exists that
satisfies every row exactly (checked by fixing its integers and
re-solving the LP to 1e-10, or by enumeration). In 3000 random models
(500 of each family, 2 option settings each): 74, 88 and 22 flagged
solves in the three tiny-coefficient families; none in knapsacks and set
covers; 5 in the family with moderate coefficients (3e-3..1e2), all of them
feasible only within tolerance (in exact arithmetic the other solver's
point is infeasible). Two shrunk cases:

- `bench/fuzz.py make random 23` (9 integer columns, 22 rows): HiGHS says
  infeasible, with and without presolve; optimum 45.2259.
- `bench/fuzz.py make random 1784` (fixed charge, big-M rows with M up to
  7e5, three rows with a coefficient of 2e-8..4e-8): HiGHS says optimal at
  533.74; an exactly feasible solution has 150.29. Shrunk to 14 rows, 19
  free columns: HiGHS 302.23, optimum 140.01 (enumerating the 4 binaries).
  `presolve_rule_off = 4096` (the aggregator) or zeroing the 3.7e-8 entry
  removes the error.

**Cause (random 23).** Shrunk to 3 rows and 2 free integer columns, presolve
off: row `r15` is `-2.085e-8 c1 - 29.04 c4 + 1.299 c7 + ... >= -290.96`,
tight at the feasible point (c4, c7) = (11, 22), its maximal activity
short of the side by 1.9e-13 (roundoff). Propagating it, `HighsDomain`
derives `c1 <= -1.9e-13 / 2.085e-8 = -9e-6` and rounds that with feastol
in the column's space: `floor(-9e-6 + 1e-6) = -1 < 0 = lb(c1)`, a
conflict. A point that violates the row by 1.9e-13 is feasible (the
tolerance is 1e-6 on the row), but dividing by the tiny coefficient turns
that into a bound violation of 9e-6 on the column. The column-space
tolerance covers the row tolerance only for |a| >= 1.
`HPresolve::updateColImpliedBounds` does the same (`c1 <= -8.2e-6`), and so
does the bound transfer of `doubletonEq` through a coefficient of 9.4e-8.

**Fix (sketch, not applied).** Derive implied bounds from the side relaxed
by feastol when |a| < 1, so that no point violating the row by at most
feastol is cut off. In `propagateRowUpper/Lower` and
`updateColImpliedBounds` this fixes random 23 (45.2259 with and without
presolve) and 7 of 11 wrong answers on 200 random models, but changes
search paths, and 2 models then meet the same pattern elsewhere (the
doubleton bound transfer; a solution rejected for an absolute row
violation of 2.4e-6 on a row with side 8e5). Each place that divides a
bound out of a row would need it.

**Not seen on the private suite** (docs/benchmarks.md): its coefficients
lie in [5.7e-3, 1.1e2] and its sides below 2e3 (no wrong answer in 216
perturbed models of it), so Crestline leaves this as HiGHS has it.

## Draft, not filed: objective propagation loops forever on an integer column with fractional fixed bounds

**Version.** HiGHS 1.15.1 (`HighsDomain::ObjectivePropagation::propagate`,
`HighsDomain::checkChangeBound`). `latest` has the same propagation code
but no longer meets the case: since
[PR #3272](https://github.com/ERGO-Code/HiGHS/pull/3272) (issue #3271)
its presolve, which runs for MIPs even with `presolve = off`, rounds the
bounds of integer columns (`HPresolve::checkOriginalModelBounds`).

**Symptom.** `highs --presolve off` on a model with an integer column
fixed at 4.5391e-9 that has a positive cost (Crestline's
`tests/objprop_noop_loop.rs` has a 3-column one) runs forever at 100% CPU
once feasibility jump finds an incumbent; no time limit stops it.

**Cause.** The objective propagation's loop over continuous-style columns
computes an upper bound of 0.0999 for the column; `adjustedUb` rounds it
to 0 and accepts it (0 < ub = 4.5e-9); `changeBound` finds 0 within
feastol below lb, snaps it back to lb = ub and returns without a change,
yet `checkChangeBound` returns true. The loop counts that as a bound
change and repeats with the same capacity, forever.

**Crestline's fix.** `check_change_bound` returns whether the domain
changed (its change stack grew, or it became infeasible) rather than
whether the bound was accepted. The objective propagation is the only
caller that reads the result, so runs that never meet such a no-op are
unchanged.

## Draft, not filed: the root node popped from a queue left without active nodes

**Version.** HiGHS 1.15.1 (`HighsMipSolver::run`, the search loop).

**Cause.** The loop checks `nodequeue.empty()` (no active node) at its
top, then calls `queryExternalSolution` and, on its first pass
(`root_node`), installs `nodequeue.popBestBoundNode()` unconditionally.
An external solution taken there (`kCallbackMipUserSolution`) goes
through `addIncumbent`: the new optimality limit and `performBounding`
can move the only open node to the suboptimal set (its bound within the
gap) or prune it. `popBestBoundNode` then unlinks `lowerMin`, -1: out of
bounds. Only a user callback reaches it in HiGHS; we did not run it there.

**What Crestline saw.** Its concurrent LNS helpers' solutions are taken at
the same place (`sync_concurrent_lns`): from 3 threads on, 1 solve in
64-128 of one model of the private suite ended in "pop from an empty
node queue", the queue holding one suboptimal node.

**Crestline's fix.** The root-node install checks the loop condition again
and leaves the loop when no active node remains; every other run is
unchanged.
