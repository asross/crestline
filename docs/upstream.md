# Draft: bug report for HiGHS (not filed)

## MIP: dual proofs drop tiny coefficients without their bound term, giving invalid conflicts and wrong optima

**Version.** HiGHS master at 73cac48c53 (2026-08-19, 1.15.1), file
`highs/mip/HighsLpRelaxation.cpp`.

**Symptom.** On traininstance6 (MIPLIB 2017, optimum 28290), a
HiGHS-derived solver with the same code reports "optimal" with a dual bound
above the optimum. It reported 28350 with `random_seed = 9`, and 28380 and
29090 on a nearby search path. It depends on the path: most seeds are
right.

**Cause.** Both proofs are built as the aggregate `a = c - y^T A` (bound
proofs) or `a = y^T A` (infeasibility proofs, from the dual ray), and then
`sum_j a_j x_j <= rhs` is used as a valid inequality:

- `computeDualProof`, line 880:
  `if (std::fabs(val) <= mipsolver.options_mip_->small_matrix_value) continue;`
- `storeDualInfProof`, line 993:
  `if (std::fabs(val) <= mipsolver.mipdata_->epsilon) continue;`

These lines drop entries `a_j` with `|a_j| <= 1e-9` from the inequality.
Unlike the entries below feastol a few lines later, they do not move
`a_j * bound_j` to the right-hand side. The inequality left over is implied
only when `|a_j| * |bound_j|` is negligible next to the slack of the proof,
and that does not hold for columns with large bounds.

What we observed: an infeasible strong-branching LP gave a ray proof with
47 coefficients of about 1.1e-6 and right-hand side 5.10e-5. One dropped
entry lay on an integer column with a bound of about 29000. Dropping it
tightened the proof by about 3e-5. The optimal solution has activity
5.21e-5 on the proof, so the proof cut it off. `generate_conflict` turned
the proof into a conflict cut that excludes every optimum. After the next
restart, that cut was part of the model, whose optimum was then 31780.

**Why it went unnoticed.** `checkDualProof()` exists, but its result is
ignored at line 1278: `if (true || checkDualProof()) return
Status::kInfeasible;`. The violation here is also tiny in absolute terms
(1e-6), so only checking the cuts against a known optimum (the
`debugSolution` machinery, or our equivalent) shows it.

**Fix.** Treat tiny entries like the entries below feastol: move them to
the right-hand side with their global bound, and give up on the proof if
that bound is infinite. In both functions:

```cpp
if (val == 0.0) continue;  // was: fabs(val) <= small_matrix_value / epsilon
bool removeValue = std::fabs(val) <= mipsolver.mipdata_->feastol;
```

(Skipping rows with tiny weights when `y^T A` is formed, as
`storeDualInfProof` also does, is valid: it is just another nonnegative
combination of the rows. Only dropping a column term without its bound is
not.)

**Cost.** Measured in Crestline (a Rust port of HiGHS's MIP solver) on
MIPLIB 2017 "benchmark", 240 instances, 300 s, 1 thread:

| Arch | Solved | Shifted geometric mean |
|---|---|---|
| x64 | 95 -> 90 | 140.6 -> 143.4 s |
| arm64 | 94 -> 92 | 144.2 -> 145.5 s |

No wrong results with the fix. With the fix, traininstance6 gives 28290
on every seed we tried. The extra time is the price of valid proofs: the
old ones were stronger only because they were invalid.

