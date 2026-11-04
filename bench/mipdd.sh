#!/bin/bash
# Shrinks a model on which crest misbehaves to a small reproducer with
# MIP-DD (github.com/scipopt/MIP-DD, arXiv:2405.19770), which removes rows,
# columns, coefficients and objective terms, fixes variables and rounds
# numbers while the predicate keeps holding. See "Shrinking a failing
# instance" in docs/porting.md.
#   bench/mipdd.sh [-o OPTIONS_FILE] INSTANCE PREDICATE
# PREDICATE (crest is "bad" when ...):
#   diff       its model status or objective differs from HIGHS_BIN's
#   crash      it dies on a signal or panics
#   above=V    its objective is above V (below=V: below V)
#   PROGRAM    `PROGRAM MODEL.mps` exits 0
# Both solvers get OPTIONS_FILE (default threads = 1, random_seed = 0,
# mip_max_nodes = 1000: deterministic, unlike a time limit), crest also
# mip_graph_lns_cost_perturbation = true and mip_lp_scaling = true so that
# it follows the C++ app's paths. Environment: CREST_BIN (default
# target/release/crest), HIGHS_BIN (the C++ app, needed for diff), MIPDD
# (a MIP-DD binary; built from MIPDD_SRC, default ~/.cache/mipdd, when
# missing; needs boost and tbb). Writes INSTANCE_min.mps in the working
# directory; MIP-DD's rounds are kept in a temporary directory, printed.
# MIPDD_SOL: a known solution to keep feasible (see below).
set -u
ab() { case $1 in /*) echo "$1";; *) echo "$PWD/$1";; esac; }
BENCH=$(ab "$(dirname "$0")")
CREST=$(ab "${CREST_BIN:-$BENCH/../target/release/crest}")

# Solves MODEL with BIN and OPTS: prints "STATUS OBJECTIVE RC" (RC 200 on a panic)
run() {
  local d; d=$(mktemp -d)
  (cd "$d" && "$1" --options_file "$2" --solution_file s.sol "$3" > out 2>&1); local rc=$?
  grep -q panicked "$d/out" && rc=200
  echo "$(sed -n 2p "$d/s.sol" 2>/dev/null | tr -d ' ') $(awk '/^Objective /{print $2; exit}' "$d/s.sol" 2>/dev/null) $rc"
  rm -rf "$d"
}

# The predicate: bench/mipdd.sh --test PREDICATE OPTS MODEL, exit 0 iff bad
if [ "${1:-}" == --test ]; then
  pred=$2 opts=$3 model=$4
  read -r cs co crc <<< "$(run "$CREST" "$opts.crest" "$model")"
  case $pred in
    crash) [ "$crc" -ge 128 ];;
    above=*) [ -n "$co" ] && awk -v a="$co" -v b="${pred#above=}" 'BEGIN{exit !(a > b)}';;
    below=*) [ -n "$co" ] && awk -v a="$co" -v b="${pred#below=}" 'BEGIN{exit !(a < b)}';;
    diff) read -r hs ho hrc <<< "$(run "$(ab "${HIGHS_BIN:?}")" "$opts" "$model")"
          [ "$cs" != "$hs" ] || awk -v a="$co" -v b="$ho" 'BEGIN{d=a-b; if(d<0)d=-d; m=(b<0?-b:b); exit !(d > 1e-6*(m>1?m:1))}';;
    *) "$pred" "$model";;
  esac
  exit
fi

opts=
[ "${1:-}" == -o ] && { opts=$(ab "$2"); shift 2; }
[ $# == 2 ] || { sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 2; }
inst=$(ab "$1") pred=$2
case $pred in diff|crash|above=*|below=*) ;; *) pred=$(ab "$pred");; esac
[ -x "$CREST" ] || { echo "no crest at $CREST (set CREST_BIN)"; exit 2; }

if [ -z "${MIPDD:-}" ]; then
  # MIP-DD is header-only around bugger.cpp: compile that one file with the
  # shell-predicate interface instead of its SCIP or SoPlex one
  src=${MIPDD_SRC:-$HOME/.cache/mipdd}
  MIPDD=$src/mipdd
  if [ ! -x "$MIPDD" ]; then
    [ -d "$src" ] || git clone -q https://github.com/scipopt/MIP-DD.git "$src" || exit 2
    git -C "$src" checkout -q 0ff9be9caf21dd8ecad395a659fa4236ebbdeac8 || exit 2
    mkdir -p "$src/gen/bugger"
    printf '#define BUGGER_USE_BOOST_IOSTREAMS_WITH_ZLIB\n#define BUGGER_VERSION_MAJOR 2\n#define BUGGER_VERSION_MINOR 0\n#define BUGGER_VERSION_PATCH 1\n#define BUGGER_VERSION_TWEAK 0\n' > "$src/gen/bugger/CMakeConfig.hpp"
    pre=$(brew --prefix 2>/dev/null || echo /usr)
    echo "building MIP-DD in $src"
    c++ -std=c++14 -O2 -DNDEBUG -DBUGGER_DOUBLE -w -I"$src/gen" -I"$src/src" -I"$src/src/bugger/external" -I"$pre/include" \
      -include "$BENCH/mipdd/ShellInterface.hpp" "$src/src/bugger.cpp" -L"$pre/lib" \
      -lboost_program_options -lboost_iostreams -ltbb -o "$MIPDD" || exit 2
  fi
fi

work=$(mktemp -d)
name=$(basename "$inst"); name=${name%.gz}; name=${name%.*}
case $inst in *.gz) gunzip -c "$inst";; *) cat "$inst";; esac > "$work/$name.mps"
if [ -n "$opts" ]; then cp "$opts" "$work/opts"; else printf 'threads = 1\nrandom_seed = 0\nmip_max_nodes = 1000\n' > "$work/opts"; fi
{ cat "$work/opts"; echo "mip_graph_lns_cost_perturbation = true"; echo "mip_lp_scaling = true"; } > "$work/opts.crest"
test=("$0" --test "$pred" "$work/opts")
"${test[@]}" "$work/$name.mps" || { echo "the predicate does not hold on $inst"; exit 1; }

# A reference solution keeps MIP-DD's reductions feasible: MIPDD_SOL ("name
# value" lines, e.g. MIPLIB's .sol less its =obj= line), else the C++ app's,
# else crest's
sol=${MIPDD_SOL:+$(ab "$MIPDD_SOL")}
for b in "${HIGHS_BIN:-}" "$CREST"; do
  [ -n "$sol" ] && break
  [ -n "$b" ] || continue
  (cd "$work" && "$(ab "$b")" --options_file opts --solution_file ref.full "$name.mps" > /dev/null 2>&1)
  if grep -qx Feasible "$work/ref.full" 2>/dev/null; then
    sed -n '/^# Columns/,/^# Rows/p' "$work/ref.full" | sed '1d;$d' > "$work/ref.sol"; sol=$work/ref.sol; break
  fi
done
sol=${sol:-unknown}

# nbatches 10, not MIP-DD's 2: its batch adaption needs a solving effort,
# which a shell predicate does not report
printf 'shell.command = %s\nnbatches = 10\n' "$(printf '%q ' "${test[@]}")" > "$work/mipdd.set"
(cd "$work" && "$MIPDD" -p mipdd.set -f "$name.mps" -o "$sol" > log 2>&1)
sed -n '/^ *modifiers/,$p' "$work/log"
last=$(ls -t "$work/${name}_"*.mps | head -1)
cp "$last" "${name}_min.mps"
grep '^\*\s' "${name}_min.mps"
echo "wrote ${name}_min.mps (rounds in $work)"
