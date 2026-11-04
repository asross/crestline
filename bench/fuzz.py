#!/usr/bin/env python3
"""Differential fuzzing: crest against the HiGHS release and SCIP, for wrong answers.

  fuzz.py run OUT.jsonl --crest BIN --highs BIN [--sources random:2000,check,miplib,dispatch:6]
      [--check-dir DIR] [--dispatch-dir DIR] [--shard I/N] [--cache inst] [--tmp DIR]
  fuzz.py report RES.jsonl...
  fuzz.py make SOURCE KEY OUT.mps [--check-dir DIR] [--dispatch-dir DIR] [--cache inst]
      one job's model, for triage (a dispatch one stays local: it is private)
  fuzz.py selftest

Sources (one job = one model):
  random:N    N random small MIPs (seed = key): knapsacks, set covers, big-M fixed
              charge, general MIPs with coefficients over 1e-9..1e6 and bounds to 1e6,
              and general MIPs scaled like the dispatch models, with big-M rows
  check       the .mps models of HiGHS's check/instances (--check-dir), less QPs and NaN data
  miplib      the MIPLIB 2017 collection instances tagged easy, <= 20k nonzeros (249)
  dispatch:N  N perturbations per private dispatch model (--dispatch-dir/*.mps.gz):
              costs scaled by 1 +- 3%, bounds loosened 0-3%, init SOC down 0-3%, 3 seeds in 4
              truncate the horizon (time-indexed names) to 30-100% (perturb)
On each model: crest with default options and with one variant drawn from the key
(VARIANTS), the HiGHS release and SCIP, at mip_rel_gap 0 (random, check) or 1e-6,
time limit TL by source. A solution is evidence against another solver only if it is
polished: its integers rounded and fixed, the rest re-solved as an LP to 1e-10 (polish).
Flags on crest:
  WRONG_STATUS       crest infeasible/unbounded and a reference has a solution, or
                     crest has one and a reference (or MIPLIB) proved infeasibility
  DUAL_PAST_OPT      crest's dual bound beyond a polished reference's (or MIPLIB's) objective
  PRIMAL_PAST_BOUND  crest's objective beyond a reference's dual bound
  BAD_SOLUTION       crest's solution file breaks bounds, integrality or rows by more
                     than 1e-6 (absolute, as HiGHS), or its objective is not the reported one
  CRASH              crest died on a signal, panicked or printed no status
and the same on the HiGHS release against SCIP as `highs:` notes (inherited bugs).
Results hold names, seeds, options, statuses, objectives, bounds, violation sizes and
times only, never model contents or solution values: models are made in --tmp and
deleted after each job.
"""
import argparse, glob, json, os, re, subprocess, sys, time, zlib
import numpy as np, highspy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import std

INF = highspy.kHighsInf
MIPLIB = '''
50v-10 reblock115 ran14x18-disj-8 cvs16r89-60 noswot gen-ip002 gen-ip021 gen-ip036 gen-ip054 gen-ip016 aflow40b
cvs16r106-72 neos-555424 ic97_potential flugplinf neos-831188 mkc1 neos-555001 pk1 p500x2988 n5-3 neos-1425699 misc07
dsbmip mod008inf neos-807639 a2c1s1 neos-555884 haprp rout stein15inf nh97_potential neos859080 blend2 gr4x6 h50x2450
enlight8 p0201 nsa nu25-pr12 nu120-pr12 mik-250-20-75-5 misc05inf neos-911970 markshare1 mik-250-20-75-3 g503inf
neos22 berlin p200x1188c b1c1s1 manna81 mas74 bienst1 fiber prod1 ran12x21 exp-1-500-5-5 beasleyC1 qnet1 markshare_4_0
khb05250 nexp-50-20-1-1 qap10 k16x240b cost266-UUE p500x2988d mik-250-20-75-2 opt1217 22433 nexp-50-20-4-2 neos-631517
dell neos-1430701 ic97_tension mas76 mc7 mtest4ma h80x6320 brasil prod2 10teams neos2 aligninq beasleyC2 gt2 n13-3
mik-250-20-75-1 23588 stein45inf ran13x13 stein9inf r50x360 nh97_tension g200x740 rlp1 tr12-30 nexp-150-20-1-5 neos5
mod010 flugpl dcmulti neos-555343 neos-574665 enlight_hard misc04inf sp150x300d mik-250-20-75-4 timtab1CUTS p500x2988c
mc8 qnet1_o neos-503737 neos-1330346 ta1-UUM enlight11 gen neos17 railway_8_1_0 acc-tight2 enlight4 beavma aflow30a
maxgasflow p2m2p1m1p0n100 acc-tight5 ponderthis0517-inf berlin_5_8_0 cvs08r139-94 breastcancer-regularized
neos-1442119 neos-686190 acc-tight4 b2c1s1 mine-166-5 markshare_5_0 neos-2626858-aoos neos-3610173-itata
neos-3665875-lesum neos-5107597-kakapo neos-4736745-arroux neos-3530903-gauja neos-3592146-hawea neos-3611447-jijia
neos-4954672-berkel neos-3024952-loue neos-4738912-atrato neos-3610051-istra neos-5078479-escaut neos-3118745-obra
neos-4333596-skien neos-2652786-brda neos-3046601-motu neos-3611689-kaihu neos-3083784-nive neos-3610040-iskar
neos-4338804-snowy neos-4650160-yukon neos-2656603-coxs neos-2657525-crna neos-3530905-gaula neos-3421095-cinca
neos-5182409-nasivi neos-5115478-kaveri neos-3426085-ticino neos-2328163-agri neos-5192052-neckar neos-2624317-amur
neos-3381206-awhea neos-3660371-kurow neos-3373491-avoca neos-3661949-lesse neos-3627168-kasai neos-5140963-mincio
neos-3046615-murg neos-3754480-nidda neos-3072252-nete neos-5075914-elvire no-ip-64999 no-ip-65059 app3 app1-1 app2-1
app2-2 neos16 beasleyC3 csched008 graph20-20-1rand csched010 b-ball mad bppc8-09 bppc8-02 newdano mine-90-10 glass4
gmu-35-50 supportcase16 supportcase14 supportcase17 supportcase26 neos-1396125 cvs16r128-89 pigeon-13 neos-1112782
cvs16r70-62 enlight9 milo-v12-6-r2-40-1 pigeon-10 neos-1112787 fhnw-binpack4-4 fhnw-binpack4-18 fhnw-binpack4-48
fhnw-binpack4-77 mcsched uct-subprob qiu lotsize gmu-35-40 toll-like bienst2 supportcase27i rococoC10-001000
f2gap801600 f2gap401600 f2gap40400 f2gap201600 fastxgemm-n2r6s0t2 cvrpsimple2i mc11 graphdraw-domain
graphdraw-gemcutter danoint gsvm2rl5 gsvm2rl3 pw-myciel4 timtab1 ej pg pigeon-08 csched007 rococoB10-011000 a1c1s1
chromaticindex32-8 assign1-5-8 control20-5-10-5 control30-3-2-3 binkar10_1 pg5_34
'''.split()
# crest's option variants, one per model besides the default; the last is the C++ app's paths
VARIANTS = ['presolve=off', 'random_seed=1', 'random_seed=7', 'threads=2', 'mip_detect_symmetry=false',
            'presolve=off random_seed=3',
            'mip_graph_lns_cost_perturbation=true mip_root_lp_devex_density=inf mip_symmetry_static_orbitopes=false mip_lp_scaling=true']
TL = dict(random=20, check=60, miplib=60, dispatch=90)
GAP = dict(random=0, check=0, miplib=1e-6, dispatch=1e-6)
NOSOL = ('infeasible', 'unbounded', 'infeasible_or_unbounded')


def jobs(a):
    js = []
    for s in a.sources.split(','):
        src, _, n = s.partition(':')
        if src == 'random':
            js += [('random', str(k)) for k in range(int(n or 100))]
        elif src == 'check':
            js += [('check', f[:-4]) for f in sorted(os.listdir(a.check_dir)) if f.endswith('.mps')]
        elif src == 'miplib':
            js += [('miplib', m) for m in MIPLIB]
        elif src == 'dispatch':
            js += [('dispatch', f'{os.path.basename(f)[:-7]}#{k}') for f in sorted(glob.glob(f'{a.dispatch_dir}/*.mps.gz')) for k in range(int(n or 4))]
        else:
            sys.exit(f'unknown source {src}')
    return js


def write_lp(out, cost, lo, up, isint, rows, sense):
    """rows: [(lo, up, {col: coef})]"""
    n = len(cost)
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = n, len(rows)
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = np.array(cost, float), np.array(lo, float), np.array(up, float)
    lp.row_lower_ = np.array([r[0] for r in rows], float)
    lp.row_upper_ = np.array([r[1] for r in rows], float)
    col = [[] for _ in range(n)]
    for i, (_, _, a) in enumerate(rows):
        for j, v in a.items():
            col[j].append((i, v))
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.num_col_, lp.a_matrix_.num_row_ = n, len(rows)
    lp.a_matrix_.start_ = np.cumsum([0] + [len(c) for c in col]).astype(np.int32)
    lp.a_matrix_.index_ = np.array([i for c in col for i, _ in c], np.int32)
    lp.a_matrix_.value_ = np.array([v for c in col for _, v in c], float)
    lp.integrality_ = [highspy.HighsVarType.kInteger if t else highspy.HighsVarType.kContinuous for t in isint]
    lp.sense_ = highspy.ObjSense.kMaximize if sense < 0 else highspy.ObjSense.kMinimize
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    h.passModel(lp)
    h.writeModel(out)


def rand_model(seed, out):
    """a small random MIP; family seed % 6"""
    r = np.random.default_rng(seed)
    lu = lambda lo, hi, k=None: 10 ** r.uniform(lo, hi, k)  # log-uniform
    fam, sense = seed % 6, 1
    if fam == 0:  # multi-dimensional knapsack, maximize
        n, m = r.integers(10, 50), r.integers(1, 5)
        ub = r.choice([1, 1, 3, 10], n)
        w = r.integers(1, 1000, (m, n))
        cost, lo, up, isint = list(w.sum(0) * r.uniform(.5, 1.5, n)), [0] * n, list(ub), [1] * n
        rows = [(-INF, float(w[i] @ ub) * r.uniform(.2, .6), {j: float(w[i, j]) for j in range(n)}) for i in range(m)]
        sense = -1
    elif fam == 1:  # (multi)set cover
        n, m = r.integers(20, 60), r.integers(10, 40)
        ub = r.choice([1, 1, 1, 2], n)
        cost, lo, up, isint = list(r.integers(1, 100, n).astype(float)), [0] * n, list(ub), [1] * n
        rows = [(float(r.choice([1, 1, 2])), INF, {int(j): 1.0 for j in r.choice(n, r.integers(2, 7), replace=False)}) for _ in range(m)]
    elif fam == 2:  # fixed charge with big-M and tiny coefficients
        F, C = r.integers(3, 10), r.integers(5, 20)
        d = r.uniform(1, 100, C)
        n = F + F * C  # y_j, then x_ij at F + i * F + j
        cost = list(lu(1, 4, F)) + list(r.uniform(1, 10, F * C) * lu(-2, 0, F * C))
        lo, up, isint = [0] * n, [1] * F + [float(d[i]) for i in range(C) for _ in range(F)], [1] * F + [0] * (F * C)
        rows = [(d[i], d[i], {F + i * F + j: 1.0 for j in range(F)}) for i in range(C)]
        M = lu(3, 6, F)
        if r.random() < .5:
            rows += [(-INF, 0.0, {F + i * F + j: 1.0, j: -float(M[j])}) for i in range(C) for j in range(F)]
        else:
            cap = d.sum() * r.uniform(.2, 1, F)
            rows += [(-INF, 0.0, {**{F + i * F + j: 1.0 for i in range(C)}, j: -float(cap[j])}) for j in range(F)]
        for row in rows:
            if r.random() < .3:
                row[2][int(r.integers(n))] = float(r.choice([-1, 1]) * lu(-9, -6))
    else:  # general MIP, coefficients over 1e-9..1e6; family 4 stresses tiny coefficients on huge bounds,
        # family 5 is scaled like the dispatch models (coefficients 3e-3..1e2, no tiny ones) with big-M rows
        n, m = r.integers(8, 40), r.integers(4, 30)
        big = 6 if fam == 4 else 3
        kind = r.integers(0, 4, n)  # binary, small int, wide int, continuous
        up = np.where(kind == 0, 1, np.where(kind == 1, 10, np.round(lu(0, big, n))))
        lo = np.where(kind == 1, -10, np.where(kind == 3, -up * (r.random(n) < .3), 0))
        isint = list(kind < 3)
        x0 = lo + (up - lo) * r.random(n)
        x0 = np.where(kind < 3, np.round(x0), x0)
        ptiny, phuge, coef = (.3, .1, (-2, 2)) if fam == 4 else (.15, .1, (-2, 2)) if fam == 3 else (0, 0, (-2.5, 2))
        rows = []
        if fam == 5:  # big-M indicators x_j <= M y_k, M 1-100 times x_j's bound (x0 kept feasible: y_k = 1)
            for j in np.flatnonzero(kind == 3)[:6]:
                if (kind == 0).any():
                    k = int(r.choice(np.flatnonzero(kind == 0)))
                    x0[k] = 1
                    rows.append((-INF, 0.0, {int(j): 1.0, k: -float(max(up[j], 1) * lu(0, 2))}))
        for _ in range(m):
            js = r.choice(n, r.integers(2, min(8, n) + 1), replace=False)
            a = {int(j): float(r.choice([-1, 1]) * (lu(-9, -5) if r.random() < ptiny else lu(4, 6) if r.random() < phuge else lu(*coef))) for j in js}
            act = sum(v * x0[j] for j, v in a.items())
            sl = r.exponential() * max(1, abs(act)) * .1 * (r.random() < .7)
            t = r.integers(0, 4 if (kind[js] == 3).any() else 3)  # an all-integer equality holds only within tolerance
            rows.append(((act - sl, INF), (-INF, act + sl), (act, act), (act - sl, act + sl + 1))[t] + (a,))
        if r.random() < .1:  # maybe infeasible
            l0, u0, a = rows[0]
            rows[0] = (u0 + 1, INF, a) if u0 < INF else (-INF, l0 - 1, a)
        cost = list(r.choice([-1, 1], n) * np.where(r.random(n) < (fam < 5) * .1, lu(-9, -6, n), lu(-2, 2, n)))
        lo, up = list(lo), list(up)
        sense = int(r.choice([-1, 1]))
    write_lp(out, cost, lo, up, isint, rows, sense)


def tindex(name):
    m = re.search(r'[_(](\d+)\)_?$', name)
    return int(m.group(1)) if m else None


def perturb(src, seed, out):
    """a dispatch model: costs scaled by 1 +- 3%; continuous column bounds and the bounds of inequalities
    with a continuous column loosened by 0-3%; init-SOC rows scaled by 0.97-1. 3 seeds in 4 truncate the
    horizon, dropping the columns past a cut, every row on them, and columns left in no row that would be
    unbounded. All but the SOC change relax the model."""
    r = np.random.default_rng(seed)
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    h.readModel(src)
    lp = h.getLp()
    n, m = lp.num_col_, lp.num_row_
    f = lambda k, p: 1 + r.uniform(-p, p, k)
    isint = np.array([int(t) != 0 for t in lp.integrality_]) if len(lp.integrality_) else np.zeros(n, bool)
    A = lp.a_matrix_  # column-wise after readModel
    cols, rows = np.repeat(np.arange(n), np.diff(A.start_)), np.asarray(A.index_, dtype=np.int64)
    rl, ru, cl, cu = (np.array(v) for v in (lp.row_lower_, lp.row_upper_, lp.col_lower_, lp.col_upper_))
    soc = np.array(['init_soc' in s for s in lp.row_names_], bool) if len(lp.row_names_) else np.zeros(m, bool)
    cont = np.zeros(m, bool)
    cont[rows[~isint[cols]]] = True
    er = np.where(cont & (rl < ru), r.uniform(0, .03, m), 0)  # balances stay balanced, integer rows integral
    ec = np.where(isint, 0, r.uniform(0, .03, n))
    fin = lambda v: np.where(np.abs(v) < INF, v, 0)
    loosen = lambda v, e, sign: np.where(np.abs(v) < INF, fin(v) + sign * np.abs(fin(v)) * e, v)
    rl, ru = loosen(rl, er, -1), loosen(ru, er, 1)
    g = 1 - r.uniform(0, .03, int(soc.sum()))
    rl[soc], ru[soc] = rl[soc] * g, ru[soc] * g
    h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(lp.col_cost_) * f(n, .03))
    h.changeRowsBounds(m, np.arange(m, dtype=np.int32), rl, ru)
    h.changeColsBounds(n, np.arange(n, dtype=np.int32), loosen(cl, ec, -1), loosen(cu, ec, 1))
    t = np.array([tindex(s) if tindex(s) is not None else -1 for s in lp.col_names_]) if len(lp.col_names_) else np.full(n, -1)
    if seed % 4 and t.max() > 0:
        drop = t > int(t.max() * r.uniform(.3, 1))
        gone = np.zeros(m, bool)
        gone[rows[drop[cols]]] = True
        inrow = np.zeros(n, bool)
        inrow[cols[~gone[rows]]] = True
        c = np.array(lp.col_cost_) * int(lp.sense_)  # minimizing
        drop |= ~inrow & ((c < 0) & (cu >= INF) | (c > 0) & (cl <= -INF))
        dr, dc = np.flatnonzero(gone).astype(np.int32), np.flatnonzero(drop).astype(np.int32)
        h.deleteRows(len(dr), dr)
        h.deleteCols(len(dc), dc)
    h.writeModel(out)


def make(src, key, out, a):
    """(model path, MIPLIB (kind, value) or None)"""
    if src == 'random':
        rand_model(int(key), out)
        return out, None
    if src == 'check':
        return f'{a.check_dir}/{key}.mps', None
    if src == 'miplib':
        return std.fetch(f'{std.MIPLIB}/WebData/instances/{key}.mps.gz', f'{a.cache}/{key}.mps.gz'), std.solu(a.cache).get(key)
    nm, _, k = key.partition('#')
    perturb(f'{a.dispatch_dir}/{nm}.mps.gz', zlib.crc32(nm.encode()) + int(k), out)
    return out, None


def solve(solver, binary, path, opts, tl, gap, lp, tmp):
    """one solve, its solution checked against lp"""
    t0, sol = time.perf_counter(), f'{tmp}/{solver}.sol'
    if os.path.exists(sol):
        os.remove(sol)
    if solver == 'scip':
        cmd = [sys.executable, std.__file__, 'scip', path, str(tl), '1', str(gap), sol]
    else:
        of = f'{tmp}/{solver}.opts'
        with open(of, 'w') as f:
            f.write(f'threads = 1\ntime_limit = {tl}\nmip_rel_gap = {gap}\n' + ''.join(o.replace('=', ' = ', 1) + '\n' for o in opts.split()))
        cmd = [binary, '--options_file', of, '--solution_file', sol, path]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp)
    try:
        o, e = p.communicate(timeout=tl * 1.5 + 60)
    except subprocess.TimeoutExpired:
        p.kill()
        o, e = p.communicate()
        return dict(solver=solver, status='killed', primal=None, dual=None, wall=round(time.perf_counter() - t0, 2))
    try:
        res = std.parse('scip' if solver == 'scip' else 'crest', o, 0)
    except (ValueError, IndexError):
        res = dict(raw=None)
    if m := re.search(r'^Model status\s*:\s*(.*)$', o, re.M):  # final, after the solving report's
        res['raw'] = m.group(1)
    if 'Setting model status to Solve error' in o + e:
        res['raw'] = 'Solve error'
    st = std.norm(res.get('raw')) if res.get('raw') else 'load_error' if 'Error loading file' in o + e else 'error'
    if st == 'other' or st in NOSOL:  # a solve error is reported, so not a silent wrong answer
        res['primal'] = res['dual'] = None
    if p.returncode < 0 or 'panicked' in o + e:
        st = 'error'
    r = dict(solver=solver, status=st, primal=res.get('primal'), dual=res.get('dual'), wall=round(time.perf_counter() - t0, 2))
    if st == 'error':
        r['rc'], r['err'] = p.returncode, (e or o)[-300:]
    if r['primal'] is not None:
        v = verify(lp, sol)
        r['viol'] = v and [float(f'{x:.3g}') for x in v[:3]] + [float(f'{abs(v[3] - r["primal"]) / max(1, abs(v[3])):.3g}')]
        r['valid'] = bool(v) and max(r['viol']) <= 1e-6
        if r['valid']:  # evidence against another solver
            r['polished'] = polish(lp, v[4])
    return r


def verify(lp, sol):
    """(max bound, row, integrality violation, objective) of a HiGHS or SCIP solution file, or None"""
    try:
        ls = open(sol).read().split('\n')
        if ls[0].startswith(('solution status', 'objective value')):  # SCIP: nonzeros by name
            ix = {nm: j for j, nm in enumerate(lp.col_names_)}
            x = np.zeros(lp.num_col_)
            for l in ls:
                f = l.split()
                if len(f) > 1 and f[0] in ix:
                    x[ix[f[0]]] = float(f[1])
        else:
            i = ls.index(f'# Columns {lp.num_col_}')
            x = np.array([float(l.rsplit(None, 1)[1]) for l in ls[i + 1:i + 1 + lp.num_col_]])
    except (OSError, ValueError, IndexError, KeyError):
        return None
    A = lp.a_matrix_
    outer = np.repeat(np.arange(len(A.start_) - 1), np.diff(A.start_))
    inner = np.asarray(A.index_, dtype=np.int64)
    rows, cols = (outer, inner) if A.format_ == highspy.MatrixFormat.kRowwise else (inner, outer)
    act = np.zeros(lp.num_row_)
    np.add.at(act, rows, np.array(A.value_) * x[cols])
    viol = lambda v, lo, up: float(np.max(np.maximum(np.maximum(lo - v, v - up), 0), initial=0))  # absolute, as HiGHS
    isint = np.array([int(t) == 1 for t in lp.integrality_], bool) if len(lp.integrality_) else np.zeros(lp.num_col_, bool)
    return (viol(x, np.array(lp.col_lower_), np.array(lp.col_upper_)), viol(act, np.array(lp.row_lower_), np.array(lp.row_upper_)),
            float(np.max(np.abs(x - np.round(x))[isint], initial=0)), float(np.array(lp.col_cost_) @ x + lp.offset_), x)


def polish(lp, x):
    """the objective of x's integers, rounded and fixed, with the continuous part re-solved as an LP to 1e-10,
    or None if that LP is infeasible: a solution feasible only within tolerance is no evidence"""
    isint = np.array([int(t) == 1 for t in lp.integrality_], bool) if len(lp.integrality_) else np.zeros(lp.num_col_, bool)
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    h.passModel(lp)
    j = np.flatnonzero(isint).astype(np.int32)
    if len(j):
        h.changeColsBounds(len(j), j, np.round(x[j]), np.round(x[j]))
        h.changeColsIntegrality(len(j), j, np.zeros(len(j), dtype=np.uint8))
    for o, v in (('presolve', 'off'), ('primal_feasibility_tolerance', 1e-10), ('time_limit', 60.0)):
        h.setOptionValue(o, v)
    h.run()
    return h.getInfo().objective_function_value if h.getModelStatus() == highspy.HighsModelStatus.kOptimal else None


def flags(x, others, s, known):
    """flags on result x against the others' verified solutions and MIPLIB's (kind, value); s = +1 minimize, -1 maximize"""
    tol = lambda z: 1e-6 * max(1, abs(z))
    prim = [s * q['polished'] for q in others if q.get('polished') is not None] + ([s * known[1]] if known and known[0] in ('opt', 'best') else [])
    st, f = x['status'], []
    if st in ('error', 'killed'):
        return ['CRASH']
    if st == 'optimal' and x['primal'] is None or st == 'infeasible' and prim or \
            st in NOSOL[1:] and (any(q['status'] == 'optimal' and q.get('polished') is not None for q in others) or known and known[0] == 'opt'):
        f.append('WRONG_STATUS')
    d = x['dual'] if x['dual'] is not None else x['primal'] if st == 'optimal' else None
    if d is not None and prim and s * d - min(prim) > tol(min(prim)):
        f.append(f'DUAL_PAST_OPT({(s * d - min(prim)) / max(1, abs(min(prim))):.1e})')
    if x['primal'] is not None and not x.get('valid'):
        f.append('BAD_SOLUTION')
    return f


def run(a):
    for k in ('crest', 'highs', 'check_dir', 'dispatch_dir', 'cache'):  # the solvers run in --tmp
        setattr(a, k, os.path.abspath(getattr(a, k)))
    os.makedirs(a.cache, exist_ok=True)
    tmp = os.path.abspath(a.tmp)
    os.makedirs(tmp, exist_ok=True)
    k, n = map(int, a.shard.split('/'))
    js = jobs(a)[k::n]
    print(f'shard {a.shard}: {len(js)} models', flush=True)
    with open(a.out, 'a') as out:
        for src, key in js:
            try:
                job(a, src, key, tmp, out)
            except Exception as e:  # dispatch: the type only, its names are private
                import traceback
                print(f'{src} {key}: harness error {type(e).__name__}', flush=True)
                if src != 'dispatch':
                    traceback.print_exc(file=sys.stdout)


def job(a, src, key, tmp, out):
    path, known = make(src, key, f'{tmp}/model.mps', a)
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    if path is None or h.readModel(path) != highspy.HighsStatus.kOk or h.getModel().hessian_.dim_ > 0:
        print(f'{src} {key}: skipped', flush=True)
        return
    lp = h.getLp()
    if not all(np.isfinite(v).all() for v in (lp.col_cost_, lp.a_matrix_.value_)) or \
            np.isnan(np.concatenate([lp.col_lower_, lp.col_upper_, lp.row_lower_, lp.row_upper_])).any():
        print(f'{src} {key}: skipped (NaN or infinite data)', flush=True)  # check/instances' nan*: HiGHS can hang on them
        return
    s = int(lp.sense_)
    tl, gap = TL[src], GAP[src]
    var = VARIANTS[zlib.crc32(f'{src}{key}'.encode()) % len(VARIANTS)]
    refs = [solve('highs', a.highs, path, '', tl, gap, lp, tmp), solve('scip', None, path, '', tl, gap, lp, tmp)]
    for opts in ('', var):
        c = solve('crest', a.crest, path, opts, tl, gap, lp, tmp)
        # twin: the HiGHS release with crest's options (crest-only options: its default)
        twin = refs[0] if opts == '' or 'mip_graph_lns' in opts else solve('highs', a.highs, path, opts, tl, gap, lp, tmp)
        c.update(source=src, key=key, opts=opts, sense=s, refs=refs, twin=twin, flags=flags(c, refs, s, known),
                 twin_flags=flags(twin, [o for o in refs + [c] if o is not twin], s, known),
                 notes=[f'{q["solver"]}:{x}' for q in refs for x in flags(q, [o for o in refs + [c] if o is not q], s, known)])
        if src == 'dispatch':  # private: no solver output
            for q in [c, twin] + refs:
                q.pop('err', None)
        out.write(json.dumps(c) + '\n')
        out.flush()
        fl = ' '.join(c['flags'] + c['notes'])
        print(f"{src:8s} {key:40s} [{opts[:30]}] crest {c['status']} {c['primal']} {c['dual']} {c['wall']}s | "
              + ' | '.join(f"{q['solver']} {q['status']} {q['primal']} {q['dual']}" for q in refs) + (f'  *** {fl} ***' if fl else ''), flush=True)
    if path.startswith(tmp):
        os.remove(path)


def report(a):
    from collections import Counter
    rs = [json.loads(l) for f in a.res for l in open(f) if l.strip()]
    kind = lambda f: f.split('(')[0]
    for r in rs:  # crest-only: the HiGHS release with the same options does not share the flag
        r['only'] = bool({kind(f) for f in r['flags']} - {kind(f) for f in r['twin_flags']})
    print('| source | models | crest solves | reference solves | crest optimal | crest flagged | crest-only | models with reference notes |\n|---|---|---|---|---|---|---|---|')
    for s in sorted({r['source'] for r in rs}) + ['all']:
        x = [r for r in rs if s in ('all', r['source'])]
        models = {(r['source'], r['key']) for r in x}
        twins = sum(bool(r['opts']) and 'mip_graph_lns' not in r['opts'] for r in x)
        print(f"| {s} | {len(models)} | {len(x)} | {2 * len(models) + twins} | {sum(r['status'] == 'optimal' for r in x)} | "
              f"{sum(bool(r['flags']) for r in x)} | {sum(r['only'] for r in x)} | {len({(r['source'], r['key']) for r in x if r['notes']})} |")
    print('\nCrest flags: ' + (', '.join(f'{k} {v}' for k, v in Counter(kind(f) for r in rs for f in r['flags']).items()) or 'none')
          + f"; crest-only (not shared by the HiGHS release): {sum(r['only'] for r in rs)} solves")
    print('\nReference notes (default crest rows): ' + (', '.join(f'{k} {v}' for k, v in Counter(kind(f) for r in rs if r['opts'] == '' for f in r['notes']).items()) or 'none'))
    bad = sorted([r for r in rs if r['flags'] or (r['notes'] and r['opts'] == '')],
                 key=lambda r: (not r['only'], not r['flags'], r['source'], r['key']))[:a.max]
    if bad:
        print('\nstatus primal dual wall [bound, row, integrality, objective violation]\n')
        print('| source | key | crest options | flags | crest | highs, same options | highs | scip |\n|---|---|---|---|---|---|---|---|')
        cell = lambda q: f"{q['status']} {q['primal']} {q['dual']} {q['wall']} {q.get('viol') or ''}"
        for r in bad:
            print(f"| {r['source']} | {r['key']} | {r['opts']} | {'**crest-only** ' * r['only']}{' '.join(r['flags'] + r['notes'])} | {cell(r)} | " + ' | '.join(cell(q) for q in [r['twin']] + r['refs']) + ' |')


def selftest():
    q = lambda st, p, d, ok=True: dict(solver='x', status=st, primal=p, dual=d, valid=ok, polished=p if ok else None)
    c = q('optimal', 10.0, 10.0)
    assert flags(c, [q('optimal', 10.0, 10.0)], 1, None) == []
    assert flags(q('optimal', 11.0, 11.0), [q('optimal', 10.0, 10.0)], 1, None) == ['DUAL_PAST_OPT(1.0e-01)']
    assert flags(q('optimal', 11.0, 11.0), [q('optimal', 10.0, 10.0, False)], 1, None) == []
    assert flags(q('optimal', 9.0, 9.0), [q('optimal', 10.0, 10.0)], -1, None) == ['DUAL_PAST_OPT(1.0e-01)']
    assert flags(q('infeasible', None, None), [q('timelimit', 10.0, 5.0)], 1, None) == ['WRONG_STATUS']
    assert flags(q('unbounded', None, None), [q('timelimit', 10.0, 5.0)], 1, None) == []
    assert flags(q('timelimit', 12.0, 9.0), [q('timelimit', 11.0, 8.0)], 1, ('opt', 10.0)) == []
    assert flags(q('timelimit', 12.0, 10.5), [], 1, ('opt', 10.0)) == ['DUAL_PAST_OPT(5.0e-02)']
    assert flags(q('optimal', 10.0, 10.0, False), [], 1, None) == ['BAD_SOLUTION']
    assert flags(q('error', None, None), [], 1, None) == ['CRASH']
    assert tindex('soc(train1_bank2_mod3_17)') == 17 and tindex('c_u_x(5)_') == 5 and tindex('residual_val(train1_bank1_mod2)') is None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for seed in range(10):
            rand_model(seed, f'{d}/m.mps')
            h = highspy.Highs()
            h.setOptionValue('output_flag', False)
            assert h.readModel(f'{d}/m.mps') == highspy.HighsStatus.kOk
            h.setOptionValue('solution_file', f'{d}/m.sol')
            h.setOptionValue('write_solution_to_file', True)
            h.run()
            if h.getModelStatus() == highspy.HighsModelStatus.kOptimal:
                v = verify(h.getLp(), f'{d}/m.sol')
                z = h.getInfo().objective_function_value
                assert max(v[:3]) <= 1e-6 and abs(v[3] - z) < 1e-6 * max(1, abs(v[3])), (seed, v)
                assert seed % 6 != 0 or abs(polish(h.getLp(), v[4]) - z) <= 1e-9 * abs(z)  # knapsack: all integer
    print('ok')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    r = sp.add_parser('run')
    r.add_argument('out')
    r.add_argument('--crest', required=True)
    r.add_argument('--highs', required=True)
    r.add_argument('--sources', default='random:100')
    r.add_argument('--shard', default='0/1')
    m = sp.add_parser('make')
    m.add_argument('src')
    m.add_argument('key')
    m.add_argument('out')
    for p in (r, m):
        p.add_argument('--check-dir', default='check/instances')
        p.add_argument('--dispatch-dir', default='dispatch/instances')
        p.add_argument('--cache', default='inst')
        p.add_argument('--tmp', default=os.environ.get('RUNNER_TEMP', '/tmp') + '/fuzz')
    p = sp.add_parser('report')
    p.add_argument('res', nargs='+')
    p.add_argument('--max', type=int, default=300, help='table rows')
    sp.add_parser('selftest')
    a = ap.parse_args()
    if a.cmd == 'make':
        os.makedirs(a.cache, exist_ok=True)
        print(make(a.src, a.key, os.path.abspath(a.out), a))
    else:
        {'run': run, 'report': report, 'selftest': lambda a: selftest()}[a.cmd](a)
