# Crestline

This MIP solver is based heavily on HiGHS but ported to Rust and augmented with [graph-based Large Neighborhood Search](./docs/dispatch.md). It also includes a number of additional optimizations discussed [here](./docs/performance.md).

It was built to solve power system dispatch problems: MIPs that are not amenable to local search, with optima too far from those of their LP relaxations for branch-and-bound to progress efficiently.

## Build

    cargo build --release

The `crest` binary lands in `target/release/crest`. It is all Rust (no C++
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

Crestline makes the same floating-point operations in the same order as
the C++ HiGHS it derives from, so its output (logs, solution and basis
files, iteration and node counts) matches the C++ `highs` app byte for
byte, but for timings and a few deliberate differences (the third-party
notice, and features left out).

CI (`.github/workflows/ci.yml`) runs on Linux x86_64:

- `cargo test --release` (unit tests against C++ reference values) and
  `cargo build --release --bin crest`;
- an oracle job: it builds the C++ `highs` app from a pinned HiGHS commit
  with gcc and `-ffp-contract=off`, builds crest with the `libstdcxx` and
  `no_fma` features (which follow libstdc++'s tie orders and unfused
  multiply-adds), and runs `bench/quick_check.sh`: Netlib LPs with presolve
  on and off, MIPs at a 200-node limit, the full logs of every LP-format
  test model and gzip models, and the 166 command lines of
  `bench/cli_cases.txt` (`bench/cli_compare.sh`), comparing outputs and
  written files.

Locally, against a clang build of the C++ app on macOS, default features
match: `HIGHS_DIR=<HiGHS checkout> bench/quick_check.sh <highs> target/release/crest`.

_Crestline is MIT-licensed; it derives from HiGHS (Copyright (c) HiGHS developers, MIT)._
