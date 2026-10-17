#!/usr/bin/env python3
"""Standard benchmarks: crest against HiGHS (release), SCIP and CBC on public sets.

  std.py run OUT.jsonl --suite SUITE --solvers crest=BIN,highs=BIN,scip,cbc
      [--tl 300] [--threads 1] [--shard I/N] [--select NAME...] [--opts K=V...] [--cache inst]
  std.py report RES.jsonl...
  std.py selftest

Suites (instances are downloaded at run time into --cache, never committed):
  miplib       MIPLIB 2017 benchmark set (240), from miplib.zib.de
  miplib-easy  the 66 of them HiGHS 1.15.1 solves in < 60 s (MIPFEAS logs, i9-285K)
  mipfeas      MIPFEAS: the benchmark set less its 7 infeasible instances (233)
  lp           LP relaxations of the MIPLIB benchmark set (solve_relaxation)
               + Mittelmann LP test set models (plato.asu.edu/ftp/lptestset)
--select keeps only the named instances (fnmatch globs); --opts are HiGHS
options for crest and highs (e.g. solver=ipm), not passed to SCIP or CBC;
NAME:K=V only for solver NAME, so crestX=BIN (any name starting with crest)
with --opts crestX:K=V is an A/B of a crest option against crest.

run: on each instance of the shard all solvers run back to back (order
rotating with the instance), so they see the same CPU. Wall time includes
reading the model. MIPs: each solver's default, relative gap 1e-4 (HiGHS's
default; set for SCIP and CBC). Every MIP result is checked against MIPLIB's
known objective (miplib2017-v31.solu): a wrong status, a bound beyond the
optimum, or a primal value better than it prints WRONG.
report: per arch, solved count, shifted geometric mean time (shift 10 s MIP,
1 s LP; unsolved count at the limit), MIPFEAS primal integral (MIP), and
per-instance tables; WRONG answers, LP disagreements and crashes first.
"""
import argparse, bz2, fnmatch, gzip, json, math, os, platform, re, shutil, subprocess, sys, time, urllib.request
from collections import defaultdict

MIPLIB = 'https://miplib.zib.de'
EASY = ('air05 app1-1 beasleyC3 binkar10_1 dano3_3 decomp2 drayage-100-23 drayage-25-23 eil33-2 enlight_hard ex10 ex9 '
        'exp-1-500-5-5 h80x6320d hypothyroid-k1 irp markshare_4_0 mas76 mc11 mik-250-20-75-4 mzzv11 mzzv42z n2seq36q n5-3 '
        'neos-1122047 neos-1445765 neos-1456979 neos-1582420 neos-2978193-inde neos-2987310-joes neos-3083819-nubu '
        'neos-3381206-awhea neos-3627168-kasai neos-4413714-turia neos-827175 neos-860300 neos-911970 neos-933966 neos-950242 '
        'neos-960392 neos17 neos5 neos8 ns1116954 ns1208400 ns1830653 nu25-pr12 nursesched-sprint02 nw04 p200x1188c pg '
        'physiciansched6-2 piperout-08 piperout-27 pk1 rmatr100-p10 roll3000 seymour1 sp150x300d supportcase33 supportcase7 '
        'swath1 swath3 timtab1 tr12-30 unitcal_7').split()
# Mittelmann LP test set models that are not MIPLIB relaxations, HiGHS <= ~120 s in his LPfeas runs
# (top level only: the subdirectories hold emps-compressed files)
MITTELMANN = ('datt256_lp.mps graph40-40.mps qap15.mps woodlands09.mps scpm1.mps neos-3025225.mps neos-5251015.mps '
              'rmine15.mps Primal2_1000.mps').split()  # L1_sixm250obs: fixed-format MPS, HiGHS cannot read it
# HiGHS's LOGLINE (bench/dispatch.py): BestSol column and time
LOGLINE = re.compile(r'^ ?[A-Za-z ]?\s+\d+\s+\d+\s+\d+\s+[\d.]+%\s+\S+\s+(\S+)\s+.*\s([\d.]+)s$')
CBCSOL = re.compile(r'^Cbc0012I Integer solution of (\S+) found .*\(([\d.]+) seconds\)')


def fetch(url, dst):
    if os.path.exists(dst):
        return dst
    for k in range(4):
        try:
            bz = url.endswith('.bz2')  # Mittelmann's .bz2 to .mps.gz (crest reads gzip)
            with urllib.request.urlopen(url, timeout=300) as r, (gzip.open if bz else open)(dst + '.part', 'wb') as f:
                shutil.copyfileobj(bz2.BZ2File(r) if bz else r, f)
            os.rename(dst + '.part', dst)
            return dst
        except OSError as e:
            print(f'fetch {url}: {e}', flush=True)
            time.sleep(10 * (k + 1))
    return None


def solu(cache):
    """{name: (kind, value)}, kind opt/best/inf/unbd/unkn"""
    ref = {}
    for l in open(fetch(f'{MIPLIB}/downloads/miplib2017-v31.solu', f'{cache}/miplib2017-v31.solu')):
        f = l.split()
        if f:
            ref[f[1]] = (f[0].strip('='), float(f[2]) if len(f) > 2 else None)
    return ref


def instances(suite, cache):
    """[(name, url, is_lp)]"""
    bench = [l.strip()[:-len('.mps.gz')] for l in open(fetch(f'{MIPLIB}/downloads/benchmark-v2.test', f'{cache}/benchmark-v2.test')) if l.strip()]
    mip = lambda ns: [(n, f'{MIPLIB}/WebData/instances/{n}.mps.gz', False) for n in ns]
    if suite == 'miplib':
        return mip(bench)
    if suite == 'miplib-easy':
        return mip(EASY)
    if suite == 'mipfeas':
        ref = solu(cache)
        return mip([n for n in bench if ref.get(n, ('',))[0] != 'inf'])
    if suite == 'lp':
        return [(n + '-relax', u, True) for n, u, _ in mip(bench)] + \
               [(re.sub(r'(_lp)?\.mps$', '', m.split('/')[-1]), f'https://plato.asu.edu/ftp/lptestset/{m}.bz2', True) for m in MITTELMANN]
    sys.exit(f'unknown suite {suite}')


def sense(path):
    """+1 minimize, -1 maximize (the OBJSENSE section of the MPS file)"""
    with gzip.open(path, 'rt', errors='replace') as f:
        prev = ''
        for l in f:
            u = l.strip().upper()
            if u.startswith('ROWS'):
                return 1
            if 'MAX' in u and (prev == 'OBJSENSE' or u.startswith('OBJSENSE')):
                return -1
            prev = u
    return 1


def cpu_model():
    try:
        for l in open('/proc/cpuinfo'):
            if l.startswith('model name'):
                return l.split(':', 1)[1].strip()
        return subprocess.run(['lscpu'], capture_output=True, text=True).stdout.split('Model name:')[1].split('\n')[0].strip()
    except (OSError, IndexError):
        return subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True).stdout.strip() or '?'


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def norm(raw):
    s = (raw or '').lower()
    if s in ('optimal', 'gaplimit', 'optimal solution found'):
        return 'optimal'
    if 'time limit' in s or s == 'timelimit':
        return 'timelimit'
    if 'infeasible or unbounded' in s or 'inforunbd' in s:
        return 'infeasible_or_unbounded'
    if 'infeasible' in s:
        return 'infeasible'
    if 'unbounded' in s:
        return 'unbounded'
    return 'other'


def solver_cmd(solver, binary, inst, a, lp, scratch):
    if solver.startswith('crest') or solver == 'highs':
        opts = f'{scratch}/{solver}.opts'
        # NAME:K=V options apply to solver NAME only (A/B of crest options)
        mine = [o.split(':', 1)[-1] for o in a.opts if ':' not in o.split('=', 1)[0] or o.startswith(solver + ':')]
        with open(opts, 'w') as f:
            f.write(f'threads = {a.threads}\ntime_limit = {a.tl}\n' + ('solve_relaxation = true\n' if lp else '') +
                    ''.join(o.replace('=', ' = ', 1) + '\n' for o in mine))
        return [binary, '--options_file', opts, inst]
    if solver == 'scip':
        return [sys.executable, os.path.abspath(__file__), 'scip', inst, str(a.tl), str(a.threads)]
    if solver == 'cbc':
        return [binary or 'cbc', inst, '-sec', str(a.tl), '-threads', str(a.threads), '-ratioGap', '1e-4', '-solve']


def parse(solver, out, wall):
    g = lambda pat: (m.group(1).strip() if (m := re.search(pat, out, re.M)) else None)
    traj = []
    if solver.startswith('crest') or solver == 'highs':
        last = None
        for l in out.splitlines():
            m = LOGLINE.match(l)
            if m and m.group(1) != last and num(m.group(1)) is not None:
                last = m.group(1)
                traj.append([float(m.group(2)), float(m.group(1))])
        raw = g(r'^  Status\s+(.*)$') or g(r'^Model status\s*:\s*(.*)$')
        primal = num(g(r'^  Primal bound\s+(\S+)')) if re.search(r'^  Primal bound', out, re.M) else num(g(r'^Objective value\s*:\s*(\S+)'))
        return dict(raw=raw, primal=primal, dual=num(g(r'^  Dual bound\s+(\S+)')), traj=traj,
                    nodes=int(g(r'^  Nodes\s+(\d+)') or 0),
                    iters={k: int(v) for k, v in re.findall(r'^(\w+)\s+iterations\s*:\s*(\d+)', out, re.M)}, solver_time=num(g(r'^  Timing\s+(\S+)') or g(r'^HiGHS run time\s*:\s*(\S+)')))
    if solver == 'scip':
        return json.loads(out.strip().splitlines()[-1]) if out.strip() else dict(raw=None)
    if solver == 'cbc':
        for l in out.splitlines():
            if m := CBCSOL.match(l):
                if not traj or float(m.group(1)) != traj[-1][1]:
                    traj.append([float(m.group(2)), float(m.group(1))])
        raw = g(r'^Result - (.*)$')
        primal = num(g(r'^Objective value:\s+(\S+)')) if traj or (raw and 'Optimal' in raw) else None
        dual = num(g(r'^Lower bound:\s+(\S+)')) or (primal if raw and 'Optimal' in raw else None)
        return dict(raw={'Optimal solution found': 'optimal', 'Problem proven infeasible': 'infeasible'}.get(raw, raw),
                    primal=primal, dual=dual, traj=traj)


def scip_solve(inst, tl, threads):
    """one SCIP solve (pyscipopt), its result as the last JSON line"""
    from pyscipopt import Model, Eventhdlr, SCIP_EVENTTYPE
    t0, traj = time.perf_counter(), []

    class Best(Eventhdlr):
        def eventinit(self):
            self.model.catchEvent(SCIP_EVENTTYPE.BESTSOLFOUND, self)

        def eventexec(self, event):
            traj.append([time.perf_counter() - t0, self.model.getSolObjVal(self.model.getBestSol())])

    m = Model()
    m.hideOutput()
    m.setParam('limits/time', float(tl))
    m.setParam('limits/gap', 1e-4)
    m.setParam('parallel/maxnthreads', int(threads))
    m.includeEventhdlr(Best(), 'best', 'best solutions')
    m.readProblem(inst)
    m.optimize()
    st = m.getStatus()
    print(json.dumps(dict(raw=st, primal=m.getObjVal() if m.getNSols() else None,
                          dual=num(m.getDualbound()), traj=traj, nodes=m.getNNodes(), ver=f'SCIP {m.version()}')))


def check(r, ref):
    """'' or 'WRONG: ...' or a note, from MIPLIB's known objective"""
    if r['lp'] or ref is None:
        return ''
    kind, z = ref
    st, p, d, s = r['status'], r['primal'], r['dual'], r['sense']
    if kind == 'inf':
        return 'WRONG: solution of an infeasible instance' if p is not None or st == 'optimal' else ''
    if kind not in ('opt', 'best'):
        return ''
    tol = 1e-6 * max(1.0, abs(z))
    if st in ('infeasible', 'unbounded', 'infeasible_or_unbounded'):
        return f'WRONG: {st}, known objective {z}'
    if st == 'optimal' and p is None:
        return 'WRONG: optimal without a solution'
    if d is not None and s * (d - z) > tol:
        return f'WRONG: dual bound {d} beyond known objective {z}'
    if p is not None and s * (p - z) < -10 * tol:  # 1e-5: SCIP's mas74 incumbent is 7e-6 below (feasibility tolerance)
        return f'WRONG: primal {p} better than optimum {z}' if kind == 'opt' else f'improves best known {z}: {p}'
    if kind == 'opt' and st == 'optimal' and s * (p - z) > 1e-4 * max(1.0, abs(z)) + tol:
        return f'WRONG: optimal at {p}, optimum {z}'
    return ''


def run(a):
    os.makedirs(a.cache, exist_ok=True)
    ref = solu(a.cache)
    insts = instances(a.suite, a.cache)
    if a.select:
        insts = [x for x in insts if any(fnmatch.fnmatch(x[0], p) for p in a.select)]
    k, n = map(int, a.shard.split('/'))
    insts = insts[k::n]
    solvers = [tuple(s.split('=', 1)) if '=' in s else (s, None) for s in a.solvers.split(',') if s]
    solvers = [(s, os.path.abspath(b) if b else None) for s, b in solvers]
    done = set()
    if os.path.exists(a.out):
        done = {(r['solver'], r['inst']) for r in map(json.loads, open(a.out))}
    cpu, arch = cpu_model(), platform.machine().replace('aarch64', 'arm64').replace('x86_64', 'x64')
    ver = {}
    for s, b in solvers:
        if s.startswith('crest') or s == 'highs':
            ver[s] = subprocess.run([b, '--version'], capture_output=True, text=True).stdout.strip().splitlines()[0]
        elif s == 'cbc':
            ver[s] = (re.search(r'Version: (\S+)', subprocess.run([b or 'cbc', '-quit'], capture_output=True, text=True).stdout) or [0, '?'])[1]
    print(f'{arch} {cpu}: {a.suite} shard {a.shard}: {len(insts)} instances x {[s for s, _ in solvers]}; {ver}', flush=True)
    scratch = os.path.abspath(f'{a.cache}/.scratch')
    os.makedirs(scratch, exist_ok=True)
    with open(a.out, 'a') as out:
        for i, (nm, url, lp) in enumerate(insts):
            path = fetch(url, f'{a.cache}/{nm.removesuffix("-relax")}.mps.gz')
            if path is None:
                print(f'{nm}: download failed', flush=True)
                continue
            sn = sense(path)
            for s, b in solvers[i % len(solvers):] + solvers[:i % len(solvers)]:
                if (s, nm) in done or (lp and s in ('scip', 'cbc')):  # ponytail: SCIP/CBC on LPs left out
                    continue
                t0 = time.perf_counter()
                p = subprocess.Popen(solver_cmd(s, b, path, a, lp, scratch), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    o, e = p.communicate(timeout=a.tl * 1.5 + 60)
                    killed = False
                except subprocess.TimeoutExpired:
                    p.kill()
                    o, e = p.communicate()
                    killed = True
                wall = time.perf_counter() - t0
                try:
                    res = parse(s, o, wall)
                except (ValueError, IndexError):
                    res = dict(raw=None)
                st = 'killed' if killed else norm(res.get('raw')) if res.get('raw') else 'error'
                if p.returncode not in (0, None) and st not in ('killed',) and p.returncode < 0:
                    st = 'error'  # killed by a signal (segfault, OOM, abort)
                r = dict(suite=a.suite + (f" [{' '.join(a.opts)}]" if a.opts else ''), solver=s, ver=res.pop('ver', None) or ver.get(s), inst=nm, lp=lp, arch=arch, cpu=cpu, tl=a.tl,
                         threads=a.threads, sense=sn, status=st, raw=res.get('raw'), primal=res.get('primal'), dual=res.get('dual'),
                         wall=round(wall, 3), solver_time=res.get('solver_time'), nodes=res.get('nodes'), iters=res.get('iters'), rc=p.returncode,
                         traj=res.get('traj', []), ref=None if lp else ref.get(nm))
                r['check'] = check(r, r['ref'])
                if st == 'error':
                    r['stderr'] = (e or '')[-2000:] + (o or '')[-1000:]
                out.write(json.dumps(r) + '\n')
                out.flush()
                flag = f'  *** {r["check"]} ***' if r['check'].startswith('WRONG') else (f'  *** {st.upper()} rc={p.returncode} ***' if st in ('error', 'killed') else '')
                print(f'{s:6s} {nm:36s} {st:10s} obj {r["primal"]}  bound {r["dual"]}  {wall:.1f}s{flag}', flush=True)


def pen(o, z):
    """MIPFEAS primal gap (bench/score.py of the MIPFEAS study)"""
    if o is None:
        return 2.0
    if abs(o) < 1e-6 and abs(z) < 1e-6:
        return 0.0
    if o * z < 0:
        return 1.0
    return max(0.0, abs(o - z) / max(abs(o), abs(z)))


def primal_integral(r):
    """MIPFEAS score: primal gap integrated over [0, tl] / tl, 2 before the first solution"""
    if not r['ref'] or r['ref'][1] is None or r['ref'][0] not in ('opt', 'best'):
        return None
    T, z = r['tl'], r['ref'][1]
    trace = list(r['traj'])
    if r['primal'] is not None and (not trace or abs(r['primal'] - trace[-1][1]) > 1e-6 * max(1, abs(trace[-1][1]))):
        trace.append([min(r['wall'], T), r['primal']])
    tot, tl, p = 0.0, 0.0, 2.0
    for t, o in trace:
        t = min(t, T)
        tot += (t - tl) * p
        tl, p = t, pen(o, z)
    return (tot + (T - tl) * p) / T


def solved(r):
    return r['status'] in (('optimal', 'infeasible', 'unbounded') if r['lp'] else ('optimal', 'infeasible')) \
        and not r['check'].startswith('WRONG') and r['wall'] <= r['tl'] * 1.02 + 1


def sgm(xs, shift):
    return math.exp(sum(math.log(x + shift) for x in xs) / len(xs)) - shift if xs else float('nan')


def report(a):
    rows = [json.loads(l) for f in a.res for l in open(f) if l.strip()]
    if not rows:
        print('no results')
        return
    by = defaultdict(dict)  # (arch, suite) -> inst -> solver -> row
    for r in rows:
        r['check'] = check(r, r['ref'])  # with this version's tolerances
        by[(r['arch'], r['suite'])].setdefault(r['inst'], {})[r['solver']] = r
    # LP: objectives of solvers that both claim optimal must agree
    for runs in by.values():
        for inst, rs in runs.items():
            opt = [r for r in rs.values() if r['lp'] and r['status'] == 'optimal' and r['primal'] is not None]
            for r in opt[1:]:
                if abs(r['primal'] - opt[0]['primal']) > 1e-6 * max(1.0, abs(opt[0]['primal'])):
                    for x in (r, opt[0]):
                        x['check'] = x['check'] or f'WRONG?: LP objectives disagree {opt[0]["solver"]} {opt[0]["primal"]} / {r["solver"]} {r["primal"]}'
            definite = {r['status'] for r in rs.values() if r['lp'] and r['status'] in ('optimal', 'infeasible', 'unbounded')}
            if len(definite) > 1:
                for r in rs.values():
                    r['check'] = r['check'] or f'WRONG?: LP statuses disagree {sorted(definite)}'
    bad = [r for r in rows if r['check'].startswith('WRONG') or r['status'] in ('error', 'killed')]
    print(f"# Standard benchmarks: {', '.join(sorted({r['suite'] for r in rows}))}; time limit "
          f"{', '.join(sorted({str(r['tl']) for r in rows}))} s; threads {', '.join(sorted({str(r['threads']) for r in rows}))}\n")
    print('Solvers: ' + '; '.join(sorted({r['solver'] + ': ' + str(r['ver']) for r in rows if r['ver']})) + '\n')
    print(f"## Wrong answers, crashes, kills: {len(bad)}\n")
    for r in sorted(bad, key=lambda r: (r['solver'] != 'crest', r['arch'], r['inst'])):
        print(f"- **{r['solver']}** {r['arch']} {r['inst']}: {r['status']} (rc {r['rc']}) {r['check']}"
              f" primal {r['primal']} dual {r['dual']} ref {r['ref']}")
    for (arch, suite), runs in sorted(by.items()):
        solvers = sorted({s for rs in runs.values() for s in rs}, key=lambda s: (s != 'crest', s != 'highs', s != 'scip', s))
        lp = any(r['lp'] for rs in runs.values() for r in rs.values())
        shift = 1 if lp else 10
        insts = sorted(i for i, rs in runs.items() if all(s in rs for s in solvers))
        cpus = sorted({r['cpu'] for rs in runs.values() for r in rs.values()})
        print(f"\n## {suite}, {arch} ({'; '.join(cpus)}): {len(insts)} instances run by all of {', '.join(solvers)}\n")
        t = lambda r: min(r['wall'], r['tl']) if solved(r) else r['tl']
        print(f"| solver | solved | wrong | error/killed | sgm time (shift {shift} s) |" + ('' if lp else ' sgm primal integral | with a solution |'))
        print('|---|---|---|---|---|' + ('' if lp else '---|---|'))
        for s in solvers:
            rs = [runs[i][s] for i in insts]
            pis = [x for x in map(primal_integral, rs) if x is not None]
            print(f"| {s} | {sum(map(solved, rs))} | {sum(r['check'].startswith('WRONG') for r in rs)} | "
                  f"{sum(r['status'] in ('error', 'killed') for r in rs)} | {sgm([t(r) for r in rs], shift):.2f} |" +
                  ('' if lp else f" {sgm(pis, 1e-3):.4f} | {sum(r['primal'] is not None for r in rs)} |"))
        if 'crest' not in solvers:
            continue
        for o in solvers[1:]:
            both = [i for i in insts if solved(runs[i]['crest']) and solved(runs[i][o])]
            only_c = [i for i in insts if solved(runs[i]['crest']) and not solved(runs[i][o])]
            only_o = [i for i in insts if solved(runs[i][o]) and not solved(runs[i]['crest'])]
            ratio = math.exp(sum(math.log((t(runs[i]['crest']) + shift) / (t(runs[i][o]) + shift)) for i in both) / len(both)) if both else float('nan')
            print(f"\ncrest against {o}: both solved {len(both)} (shifted time ratio crest/{o} {ratio:.3f}); "
                  f"only crest {len(only_c)}; only {o} {len(only_o)}: {' '.join(only_o)}\n")
            # by time ratio, then (both unsolved) by the primal integral difference
            pid = lambda i: (primal_integral(runs[i]['crest']) or 0) - (primal_integral(runs[i][o]) or 0)
            worst = sorted(insts, key=lambda i: (-math.log((t(runs[i]['crest']) + shift) / (t(runs[i][o]) + shift)), -pid(i)))[:15]
            print(f"Slowest for crest against {o} (shifted time ratio):\n")
            print(f"| instance | crest | {o} | ratio |" + ('' if lp else f' PI crest | PI {o} |'))
            print('|---|---|---|---|' + ('' if lp else '---|---|'))
            for i in worst:
                c, h = runs[i]['crest'], runs[i][o]
                cell = lambda r: f"{r['wall']:.1f}" if solved(r) else f"{r['status']} ({r['wall']:.0f})"
                f = lambda x: '' if x is None else f'{x:.4f}'
                print(f"| {i} | {cell(c)} | {cell(h)} | {(t(c) + shift) / (t(h) + shift):.2f} |" +
                      ('' if lp else f" {f(primal_integral(c))} | {f(primal_integral(h))} |"))
        print(f"\n<details><summary>per instance ({suite}, {arch})</summary>\n")
        print('| instance | ' + ' | '.join(solvers) + ' |')
        print('|---|' + '---|' * len(solvers))
        for i in insts:
            cells = []
            for s in solvers:
                r = runs[i][s]
                cells.append(f"{r['wall']:.1f}" if solved(r) else r['status'] + ('' if r['primal'] is None else f" {r['primal']:.6g}"))
            print(f"| {i} | " + ' | '.join(cells) + ' |')
        print('\n</details>')


def selftest():
    r = dict(lp=False, status='optimal', primal=10.0, dual=10.0, sense=1, ref=('opt', 10.0), tl=100, wall=5.0, traj=[[1.0, 12.0], [4.0, 10.0]])
    assert check(r, r['ref']) == '' and solved(dict(r, check=''))
    assert check(dict(r, primal=9.0, dual=9.0), r['ref']).startswith('WRONG: primal')
    assert check(dict(r, dual=10.5), r['ref']).startswith('WRONG: dual')
    assert check(dict(r, primal=9.5, dual=None, status='timelimit'), r['ref']).startswith('WRONG: primal')
    assert check(dict(r, primal=10 - 5e-5, dual=None, status='timelimit'), r['ref']) == ''
    assert check(dict(r, primal=11.0, dual=None), r['ref']).startswith('WRONG: optimal at')
    assert check(dict(r, status='infeasible', primal=None), r['ref']).startswith('WRONG')
    assert check(dict(r, sense=-1, primal=10.5, dual=10.5), r['ref']).startswith('WRONG')  # maximize: better is larger
    assert check(dict(r, primal=None, status='infeasible'), ('inf', None)) == ''
    assert check(dict(r, status='timelimit', primal=3.0), ('inf', None)).startswith('WRONG')
    # 2 for 1 s, 2/12 for 3 s, then 0: (2 + 0.5) / 100
    assert abs(primal_integral(r) - 0.025) < 1e-12, primal_integral(r)
    assert norm('Time limit reached') == 'timelimit' and norm('gaplimit') == 'optimal' and norm('Primal infeasible or unbounded') == 'infeasible_or_unbounded'
    assert abs(sgm([0, 0], 10)) < 1e-12
    print('ok')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    r = sp.add_parser('run')
    r.add_argument('out')
    r.add_argument('--suite', required=True)
    r.add_argument('--solvers', required=True)
    r.add_argument('--tl', type=float, default=300)
    r.add_argument('--threads', type=int, default=1)
    r.add_argument('--shard', default='0/1')
    r.add_argument('--select', nargs='*')
    r.add_argument('--opts', nargs='*', default=[])
    r.add_argument('--cache', default='inst')
    p = sp.add_parser('report')
    p.add_argument('res', nargs='+')
    s = sp.add_parser('scip')
    s.add_argument('inst')
    s.add_argument('tl')
    s.add_argument('threads')
    sp.add_parser('selftest')
    a = ap.parse_args()
    {'run': run, 'report': report, 'scip': lambda a: scip_solve(a.inst, a.tl, a.threads), 'selftest': lambda a: selftest()}[a.cmd](a)
