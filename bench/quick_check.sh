#!/bin/bash
# Checks crest against the C++ HiGHS app (minutes; see "Verification tiers"
# in docs/porting.md): LP solves of check/instances with presolve on and
# off, MIPs at 200 nodes, the whole log of every check/instances/*.lp and
# two gzip models, and cli_compare.sh.
#   HIGHS_DIR=<HiGHS checkout> bench/quick_check.sh HIGHS_BIN CREST_BIN
# Optional: MIPLIB_DIR (air05, neos17, ... .mps.gz) and DISPATCH_DIR add
# MIPs when present. Exit status 0 iff everything matches.
set -u
ab() { case $1 in /*) echo "$1";; *) echo "$PWD/$1";; esac; }
BENCH=$(ab "$(dirname "$0")")
ROOT=$(ab "${HIGHS_DIR:?set HIGHS_DIR to a HiGHS checkout}")
CPP=$(ab "$1"); CREST=$(ab "$2")
I=$ROOT/check/instances
MIPLIB=${MIPLIB_DIR:-/nonexistent}; DISPATCH=${DISPATCH_DIR:-/nonexistent}
fail=0
tmp=$(mktemp -d)
printf "threads = 1\nrandom_seed = 0\nmip_rel_gap = 0.01\nmip_max_nodes = 200\n" > $tmp/mip.opts
filt() { grep -E "iterations:|Objective value|Model status|^  Nodes|^  LP iterations|Primal bound|Dual bound"; }
n=0
for f in 25fv47 80bau3b greenbea perold stair afiro adlittle etamacro israel shell woodinfe gas11; do
  for o in "" "--presolve off"; do
    a=$($CPP $o $I/$f.mps 2>&1 | filt); c=$($CREST $o $I/$f.mps 2>&1 | filt)
    n=$((n+1)); [ "$a" == "$c" ] || { echo "LP DIFF $f $o"; fail=1; }
  done
done
for f in $MIPLIB/{air05,neos17,nu25-pr12,neos-911970,gen-ip002}.mps.gz $DISPATCH/lambda_20260930_080458_22776806.mps $I/{egout,bell5,rgn}.mps; do
  [ -f "$f" ] || continue
  a=$($CPP --options_file $tmp/mip.opts "$f" 2>&1 | filt); c=$($CREST --options_file $tmp/mip.opts "$f" 2>&1 | filt)
  n=$((n+1)); [ "$a" == "$c" ] || { echo "MIP DIFF $(basename $f)"; fail=1; }
done
# The LP files (LPs, QPs, MIPs, semi-variables) and gzip-compressed
# models, the whole output compared (times masked)
mask() { sed -E 's/[0-9]+(\.[0-9]+)?(e[-+][0-9]+)?s( |$)/Ts\3/g; s/(hash: )[0-9a-f]+/\1H/; /[Tt]ime|Timing|integral|%\)|Total|^ +[0-9.]+ \(/{ s/[0-9][0-9.e+-]*/N/g; s/ +/ /g; }'; }
gzip -c $I/25fv47.mps > $tmp/25fv47.mps.gz
gzip -c $I/qptestnw.lp > $tmp/qptestnw.lp.gz
for f in $I/*.lp $tmp/25fv47.mps.gz $tmp/qptestnw.lp.gz; do
  for o in "" "--presolve off"; do
    a=$(cd $tmp && $CPP $o --options_file mip.opts "$f" 2>&1 | mask)
    c=$(cd $tmp && $CREST $o --options_file mip.opts "$f" 2>&1 | mask)
    n=$((n+1)); [ "$a" == "$c" ] || { echo "DIFF $(basename $f) $o"; diff <(echo "$a") <(echo "$c") | head -10; fail=1; }
  done
done
rm -rf $tmp
bash "$BENCH/cli_compare.sh" "$CPP" "$CREST" || fail=1
echo "quick_check: $n solves compared, $( [ $fail = 0 ] && echo OK || echo FAILED )"
exit $fail
