# Crestline

This MIP solver is based heavily on [HiGHS](https://github.com/ERGO-Code/HiGHS) but ported to Rust and augmented with [graph-based Large Neighborhood Search](./docs/dispatch.md). It also includes a number of additional optimizations discussed [here](./docs/performance.md); standard benchmarks against HiGHS and SCIP are in [docs/benchmarks.md](./docs/benchmarks.md).

As of this writing, it was programmed entirely by Claude Opus 5.5 with minimal guidance from me (@asross). My motivations in requesting it were:

1. to solve real-world, highly constrained battery dispatch problems I found even commercial solvers like CPLEX and Gurobi had trouble with (not amenable to local search, with optima too far from those of their LP relaxations for branch-and-bound to progress efficiently)
1. to close some of the performance gap between open-source and commercial solvers on such problems

See below for instructions, additional details, and performance comparisons from Opus.

## Build

    cargo build --release

The `crest` binary lands in `target/release/crest`. For x86_64 machines
with AVX2 and FMA (AWS Lambda, most servers since 2015), build for
x86-64-v3:

    RUSTFLAGS="-C llvm-args=-unroll-runtime=false -C target-cpu=x86-64-v3" cargo build --release

(`RUSTFLAGS` replaces the flags of `.cargo/config.toml`, so they are
repeated.) That build fuses multiply-adds as the arm64 build does, so an
x86-64-v3 machine and an Apple silicon Mac take the same paths.
`bench/pgo_build.sh` makes a profile-guided build (same results, about
2-3% faster; it can train on your own models).

Cargo features: `no_fma` never fuses multiply-adds (as a C++ build without
FMA instructions, or gcc with `-ffp-contract=off`); `libstdcxx` follows
libstdc++'s tie orders instead of libc++'s; `hyper_dfs` makes the
hyper-sparse triangular solves by HiGHS's depth-first search (by default
they run in the dense solve's order over a bitmap, which can differ from
HiGHS in the last bits); `highs_dual_proofs` keeps HiGHS's dual proofs,
which drop tiny coefficients and can then be invalid (for the oracle only). It is all Rust (no C++
compiler needed); its only crates.io dependency is flate2 (pure-Rust
miniz_oxide backend) for gzip-compressed model files.

## Usage

    crest model.mps

The command line, options and output are those of HiGHS's `highs` app:
`crest --help` lists the flags (`--options_file`, `--solution_file`,
`--time_limit`, `--presolve`, `--solver`, `--threads`, `--random_seed`,
...), and an options file takes any HiGHS option, e.g.

    printf 'mip_rel_gap = 0.01\n' > opts.txt
    crest --options_file opts.txt model.mps

A MIP solve on two threads (with `mip_rel_gap` at least 0.1%) runs a
concurrent LNS helper, so runs differ with the timing; `mip_deterministic
= true` makes them reproducible, at some cost in speed
([docs/performance.md](./docs/performance.md)).

Model files: free-format MPS and CPLEX LP, optionally gzip-compressed. The
library (crate `crestline`) can also be used from Rust.

## Status

Crestline solves LPs (dual and primal simplex, IPX interior point, PDLP),
MIPs and QPs, with presolve, IIS and ranging. Left out of the port:

- HiPO and HiPDLP (IPX and cuPDLP-based PDLP cover these)
- SIP and PAMI parallel simplex strategies (`simplex_strategy` 2 and 3 run
  the serial dual simplex)
- iCrash
- multi-objective solves
- the fixed-format MPS reader
- the C API and the C#/Fortran/Julia/Python shims

Asking for a left-out feature fails cleanly or falls back with a warning.
Details: [docs/porting.md](./docs/porting.md).

## Verification

Built with the `hyper_dfs` and `highs_dual_proofs` features and run with the options that restore
HiGHS's behaviour (`mip_graph_lns_cost_perturbation = true`,
`mip_root_lp_devex_density = inf`, `mip_symmetry_static_orbitopes = false`,
and `CRESTLINE_HIGHS_PATHS=1` in the environment for the defaults the C++
has no option for), Crestline makes the same
floating-point operations in the same order as the C++ HiGHS it derives
from, so its output (logs, solution and basis
files, iteration and node counts) matches the C++ `highs` app byte for
byte, but for timings and a few deliberate differences (the third-party
notice, and features left out).

CI (`.github/workflows/ci.yml`) runs on Linux x86_64:

- `cargo test --release` (unit tests against C++ reference values) and
  `cargo build --release --bin crest`;
- an oracle job: it builds the C++ `highs` app from a pinned HiGHS commit
  with gcc and `-ffp-contract=off`, builds crest with the `libstdcxx`,
  `no_fma`, `hyper_dfs` and `highs_dual_proofs` features (which follow
  libstdc++'s tie orders, unfused multiply-adds, HiGHS's hyper-sparse
  solves and its dual proofs), and runs `bench/quick_check.sh`: Netlib LPs with presolve
  on and off, MIPs at a 200-node limit, the full logs of every LP-format
  test model and gzip models, and the 166 command lines of
  `bench/cli_cases.txt` (`bench/cli_compare.sh`), comparing outputs and
  written files.

Locally, against a clang build of the C++ app on macOS, a build with
`--features "hyper_dfs highs_dual_proofs"` matches (and so does an x86-64-v3 build under Rosetta): `HIGHS_DIR=<HiGHS
checkout> bench/quick_check.sh <highs> target/release/crest`.

_Crestline is MIT-licensed; it derives from HiGHS (Copyright (c) HiGHS developers, MIT)._
