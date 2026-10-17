#!/usr/bin/env python3
"""Paired A/B runs of crest on dispatch MILPs, and their report.

  dispatch.py run OUT.jsonl --cfg NAME=BIN[,option=value...] ... --inst FILE...
      [--seeds 0-15] [--tl 180] [--threads 1] [--gap 0.01] [--shard I/N] [-j 1]
  dispatch.py report RES.jsonl... [--base NAME]
  dispatch.py starts DIR CREST --inst FILE... [--shift]

run: for each (instance, seed) of the shard, the configs run back to back
(in an order that alternates with the seed), so the configs of a pair see
the same machine. Appends one JSON line per run (resumable); prints only
instance names, objective, gap and times (CI logs are public).
report: per architecture, each config against the base, paired by
(instance, seed), with 95% t-intervals. On macOS, CRESTLINE_INSTR_CLOCK=R
in the environment makes a second R*1e9 instructions (reproducible
time limits on a loaded machine); instructions are recorded there too.
starts: MIP starts for configs with read_solution_file=@ (run --starts
DIR); see starts().
"""
import argparse, glob, json, math, os, platform, re, subprocess, sys, tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

LOGLINE = re.compile(r'^ ?[A-Za-z ]?\s+\d+\s+\d+\s+\d+\s+[\d.]+%\s+\S+\s+(\S+)\s+.*\s([\d.]+)s$')


def name(p):
    return re.sub(r'\.mps(\.gz)?$', '', os.path.basename(p))


def cpu_model():
    try:
        if sys.platform == 'darwin':
            return subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True).stdout.strip()
        for l in open('/proc/cpuinfo'):
            if l.startswith('model name') or l.startswith('CPU part'):
                return l.split(':', 1)[1].strip()
    except OSError:
        pass
    return '?'


def run_one(cfg, inst, seed, a, cpu):
    cname, binary, opts = cfg
    with tempfile.NamedTemporaryFile('w', suffix='.opts', delete=False) as f:
        f.write(f"threads = {a.threads}\nrandom_seed = {seed}\nmip_rel_gap = {a.gap}\ntime_limit = {a.tl}\n")
        f.writelines(f"{k} = {v.replace('@', os.path.join(a.starts or '', name(inst) + '.sol'))}\n" for k, v in opts)
    mac = sys.platform == 'darwin'
    cmd = (['/usr/bin/time', '-l'] if mac else []) + [binary, '--options_file', f.name, inst]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    os.unlink(f.name)
    g = lambda pat, s=out: (m.group(1) if (m := re.search(pat, s, re.M)) else None)
    num = lambda x: float(x) if x not in (None, 'inf', '-inf') else None
    traj, last = [], None
    for l in out.splitlines():
        m = LOGLINE.match(l)
        if m and m.group(1) != last and num(m.group(1)) is not None:
            last = m.group(1)
            traj.append([float(m.group(2)), float(m.group(1))])
    gap = g(r'^  Gap\s+(\S+)%')
    r = dict(cfg=cname, inst=name(inst), seed=seed, arch='arm64' if cpu.startswith('Apple') else platform.machine(), cpu=cpu, tl=a.tl, threads=a.threads,
             status=g(r'^  Status\s+(.*)$'), primal=num(g(r'^  Primal bound\s+(\S+)')), dual=num(g(r'^  Dual bound\s+(\S+)')),
             gap=num(gap), pdi=num(g(r'^  P-D integral\s+(\S+)')), time=num(g(r'^  Timing\s+(\S+)')),
             nodes=int(g(r'^  Nodes\s+(\d+)') or 0), lpit=int(g(r'^  LP iterations\s+(\d+)') or 0), traj=traj)
    if mac:
        r['instr'] = int(g(r'(\d+)\s+instructions retired', err) or 0)
        r['user'] = num(g(r'([\d.]+) user', err))
    return r


def run(a):
    cfgs = []
    for c in a.cfg:
        nm, rest = c.split('=', 1)
        parts = rest.split(',')
        cfgs.append((nm, os.path.abspath(parts[0]), [tuple(p.split('=', 1)) for p in parts[1:] if p]))
    insts = sorted({f for pat in a.inst for f in glob.glob(pat)})
    lo, _, hi = a.seeds.partition('-')
    pairs = [(i, s) for s in range(int(lo), int(hi or lo) + 1) for i in insts]
    k, n = map(int, a.shard.split('/'))
    pairs = pairs[k::n]
    done = set()
    if os.path.exists(a.out):
        for l in open(a.out):
            r = json.loads(l)
            done.add((r['cfg'], r['inst'], r['seed']))
    cpu = cpu_model()
    print(f"{platform.machine()} {cpu}: {len(pairs)} pairs x {len(cfgs)} configs", flush=True)

    def pair(p):
        inst, seed = p
        order = cfgs if seed % 2 == 0 else cfgs[::-1]
        # a config with a start (read_solution_file=@) needs one
        if any('@' in v for c in cfgs for _, v in c[2]) and not os.path.exists(os.path.join(a.starts or '', name(inst) + '.sol')):
            return []
        return [run_one(c, inst, seed, a, cpu) for c in order if (c[0], name(inst), seed) not in done]

    with open(a.out, 'a') as out, ThreadPoolExecutor(a.j) as ex:
        for rs in ex.map(pair, pairs):
            for r in rs:
                out.write(json.dumps(r) + '\n')
                print(f"{r['cfg']:12s} {r['inst']:40s} s{r['seed']:<3d} {r['status']}  obj {r['primal']}  gap {r['gap']}%  {r['time']}s", flush=True)
            out.flush()


def start(sol, out, shift):
    """a crest solution file as a MIP start: as it is (for a sibling model
    with the same column names), or shifted one period earlier: a column
    whose name ends in a period index p takes the value of index p + 1,
    the last period and the initial state of charge (soc(..._0)) left out
    (inf: not given)"""
    lines = open(sol).read().splitlines()
    k = next(i for i, l in enumerate(lines) if l.startswith('# Columns '))
    cols = [l.split() for l in lines[k + 1:k + 1 + int(lines[k].split()[2])]]
    val = dict(cols)
    for c in cols if shift else []:
        m = re.match(r'^(.*[(_])(\d+)\)$', c[0])
        if m:
            soc0 = c[0].startswith('soc(') and m.group(2) == '0'
            c[1] = 'inf' if soc0 else val.get(f"{m.group(1)}{int(m.group(2)) + 1})", 'inf')
    open(out, 'w').write('\n'.join(lines[:k + 1] + [' '.join(c) for c in cols]) + '\n')


def starts(a):
    """the MIP start of each instance: the best solution (one thread, seed
    0, 0.1% gap, 60 s) of its sibling with the same columns (dm_*: the
    pert_s1 variant of its size and ramp; lambda_*: with or without
    _wind185), or (--shift) of
    itself shifted (dm_* only, whose column names end in a period index).
    Start files show the model: never upload them."""
    os.makedirs(a.dir, exist_ok=True)
    path = {name(f): f for pat in a.inst for f in glob.glob(pat)}
    src = {}
    for nm in path:
        if a.shift:
            s = nm if nm.startswith('dm_') else None
        else:
            m = re.match(r'^(dm_[a-z]+_)(pert_s2|prices|randsoc|hardpert|windlull)(_(no)?ramp)$', nm)
            s = m.group(1) + 'pert_s1' + m.group(3) if m else nm[:-8] if nm.endswith('_wind185') else nm + '_wind185'
        if s in path and (a.shift or s != nm):
            src[nm] = s

    def best(s):
        with tempfile.NamedTemporaryFile('w', suffix='.opts', delete=False) as f:
            f.write("threads = 1\nrandom_seed = 0\nmip_rel_gap = 0.001\ntime_limit = 60\n")
        out = os.path.join(a.dir, f'best_{s}.sol')
        subprocess.run([os.path.abspath(a.bin), '--options_file', f.name, '--solution_file', out, path[s]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.unlink(f.name)

    with ThreadPoolExecutor(os.cpu_count()) as ex:
        list(ex.map(best, sorted(set(src.values()))))
    for nm, s in sorted(src.items()):
        if os.path.exists(os.path.join(a.dir, f'best_{s}.sol')):
            start(os.path.join(a.dir, f'best_{s}.sol'), os.path.join(a.dir, nm + '.sol'), a.shift)
            print(f"start for {nm} from {s}", flush=True)


def tq(df):
    """the 97.5% quantile of Student's t (within 0.002 for df >= 3)"""
    return 1.96 + 2.4 / df + 3.0 / df ** 2 if df > 0 else float('nan')


def ci(xs):
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, float('nan')
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, tq(n - 1) * sd / math.sqrt(n)


def primal_gap_mean(r, best, tl):
    """mean over [0, tl] of the primal gap (%) of the incumbent to the best
    known objective: 100% without one (Berthold's primal integral / tl)"""
    def pg(p):
        if p is None:
            return 1.0
        d = max(abs(p), abs(best))
        return 0.0 if d == 0 else min(1.0, abs(p - best) / d)
    acc, t0, cur = 0.0, 0.0, None
    for t, p in r['traj'] + [[tl, None]]:
        t = min(t, tl)
        acc += (t - t0) * pg(cur)
        t0 = t
        if p is not None:
            cur = p
    return 100 * acc / tl


def report(a):
    rows = [json.loads(l) for f in a.res for l in open(f)]
    best = {}
    for r in rows:
        if r['primal'] is None:
            continue
        sense = 1 if r['dual'] is None or r['dual'] <= r['primal'] else -1
        b = best.get(r['inst'])
        best[r['inst']] = r['primal'] if b is None or sense * r['primal'] < sense * b else b
    by = defaultdict(dict)
    for r in rows:
        b = best.get(r['inst'], 0.0)
        r['pgm'] = primal_gap_mean(r, b, r['tl'])
        # the time to within 1% of the best known (the limit if never)
        r['t1'] = next((t for t, p in r['traj'] if abs(p - b) <= 0.01 * max(abs(p), abs(b))), r['tl'])
        by[(r['arch'], r['cfg'])][(r['inst'], r['seed'])] = r
    base = a.base or rows[0]['cfg']
    opt = lambda r: r['status'] == 'Optimal'
    fmt = lambda m, h: f"{m:+.3f} ± {h:.3f}"
    for arch in sorted({r['arch'] for r in rows}):
        cpus = sorted({r['cpu'] for r in rows if r['arch'] == arch})
        print(f"\n## {arch} ({'; '.join(cpus)})\n")
        A = by[(arch, base)]
        for cb in sorted({r['cfg'] for r in rows if r['arch'] == arch} - {base}):
            B = by[(arch, cb)]
            keys = sorted(set(A) & set(B))
            print(f"### {cb} against {base}: {len(keys)} pairs; solved {sum(opt(A[k]) for k in keys)} -> {sum(opt(B[k]) for k in keys)}\n")
            print("Differences B - A, mean ± 95% half-width over seeds. time: geometric mean ratio B/A of runs both solved "
                  "(log-space interval); t1: the same over all pairs of the time to within 1% of the best known (the limit if never); "
                  "gap: % at the end; pg: mean primal gap % over the limit (primal integral / limit).\n")
            print("| instance | n | solved A/B | time B/A | instr B/A | t1 A | t1 B/A | gap A | gap B-A | pg A | pg B-A |")
            print("|---|---|---|---|---|---|---|---|---|---|---|")
            logs = []
            for inst in sorted({k[0] for k in keys}):
                ks = [k for k in keys if k[0] == inst]
                both = [k for k in ks if opt(A[k]) and opt(B[k])]
                cell = lambda f: ('' if not both or not all(f(A[k]) and f(B[k]) for k in both) else
                                  (lambda m, h: f"{math.exp(m):.3f} [{math.exp(m - h):.3f}, {math.exp(m + h):.3f}]")(
                                      *ci([math.log(f(B[k]) / f(A[k])) for k in both])))
                tcell = cell(lambda r: r['time'])
                if both and all(A[k]['time'] and B[k]['time'] for k in both):
                    logs.append(sum(math.log(B[k]['time'] / A[k]['time']) for k in both) / len(both))
                gaps = [k for k in ks if A[k]['gap'] is not None and B[k]['gap'] is not None]
                ga = sum(A[k]['gap'] for k in gaps) / len(gaps) if gaps else float('nan')
                gd = fmt(*ci([B[k]['gap'] - A[k]['gap'] for k in gaps])) if len(gaps) > 1 else ''
                pa = sum(A[k]['pgm'] for k in ks) / len(ks)
                pd = fmt(*ci([B[k]['pgm'] - A[k]['pgm'] for k in ks])) if len(ks) > 1 else ''
                t1a = math.exp(sum(math.log(A[k]['t1']) for k in ks) / len(ks))
                t1 = (lambda m, h: f"{math.exp(m):.3f} [{math.exp(m - h):.3f}, {math.exp(m + h):.3f}]")(
                    *ci([math.log(B[k]['t1'] / A[k]['t1']) for k in ks]))
                print(f"| {inst} | {len(ks)} | {sum(opt(A[k]) for k in ks)}/{sum(opt(B[k]) for k in ks)} | {tcell} | "
                      f"{cell(lambda r: r.get('instr'))} | {t1a:.2f} | {t1} | {ga:.3f} | {gd} | {pa:.3f} | {pd} |")
            if logs:
                print(f"\nGeometric mean over instances of the time ratio: {math.exp(sum(logs) / len(logs)):.3f} ({len(logs)} instances)\n")
            m, h = ci([math.log(B[k]['t1'] / A[k]['t1']) for k in keys])
            gk = [k for k in keys if A[k]['gap'] is not None and B[k]['gap'] is not None]
            print(f"\nAll {len(keys)} pairs: t1 B/A {math.exp(m):.3f} [{math.exp(m - h):.3f}, {math.exp(m + h):.3f}]; "
                  f"pg B-A {fmt(*ci([B[k]['pgm'] - A[k]['pgm'] for k in keys]))}; "
                  f"gap B-A {fmt(*ci([B[k]['gap'] - A[k]['gap'] for k in gk])) if len(gk) > 1 else ''} ({len(gk)} pairs with gaps)\n")


ap = argparse.ArgumentParser()
sp = ap.add_subparsers(dest='cmd', required=True)
r = sp.add_parser('run')
r.add_argument('out')
r.add_argument('--cfg', nargs='+', required=True)
r.add_argument('--inst', nargs='+', required=True)
r.add_argument('--seeds', default='0-15')
r.add_argument('--tl', type=float, default=180)
r.add_argument('--threads', type=int, default=1)
r.add_argument('--gap', default='0.01')
r.add_argument('--shard', default='0/1')
r.add_argument('-j', type=int, default=1)
r.add_argument('--starts', help="directory of the MIP starts (dispatch.py starts): '@' in an option value is the instance's")
p = sp.add_parser('report')
p.add_argument('res', nargs='+')
p.add_argument('--base')
t = sp.add_parser('starts')
t.add_argument('dir')
t.add_argument('bin')
t.add_argument('--inst', nargs='+', required=True)
t.add_argument('--shift', action='store_true')
a = ap.parse_args()
{'run': run, 'report': report, 'starts': starts}[a.cmd](a)
