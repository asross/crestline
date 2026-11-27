#!/usr/bin/env python3
"""Paired A/B runs of crest on a set of MIPs, and their report.

  paired.py run OUT.jsonl --cfg NAME=BIN[,option=value...] ... --inst FILE...
      [--seeds 0-15] [--tl 180] [--threads 1] [--gap 0.01] [--shard I/N] [-j 1]
  paired.py report RES.jsonl... [--base NAME]
  paired.py names FILE...

run: for each (instance, seed) of the shard, the configs run back to back
(in an order that alternates with the seed), so the configs of a pair see
the same machine. Appends one JSON line per run (resumable); prints only
an instance's name, objective, gap and times (CI logs are public). A model
under a directory called `private` is named by a hash of its file name
(`names` prints the hash of each file given).
report: per architecture, each config against the base, paired by
(instance, seed), with 95% t-intervals. On macOS, CRESTLINE_INSTR_CLOCK=R
in the environment makes a second R*1e9 instructions (reproducible
time limits on a loaded machine); instructions are recorded there too.
"""
import argparse, glob, hashlib, json, math, os, platform, re, subprocess, sys, tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

LOGLINE = re.compile(r'^ ?[A-Za-z ]?\s+\d+\s+\d+\s+\d+\s+[\d.]+%\s+\S+\s+(\S+)\s+.*\s([\d.]+)s$')


def name(p):
    """the instance's name in logs and results: the file name, or its hash for a private model"""
    n = re.sub(r'\.mps(\.gz)?$', '', os.path.basename(p))
    private = 'private' in os.path.normpath(os.path.abspath(p)).split(os.sep)
    return 'p' + hashlib.sha1(n.encode()).hexdigest()[:6] if private else n


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
        f.writelines(f"{k} = {v}\n" for k, v in opts)
    mac = sys.platform == 'darwin'
    cmd = (['/usr/bin/time', '-l'] if mac else []) + [binary, '--options_file', f.name, inst]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=1.5 * a.tl + 60)
        hung = False
    except subprocess.TimeoutExpired:
        # a hang: print the stacks of all threads (code only, no model data), then kill
        hung = True
        if not mac:
            subprocess.run('command -v gdb || sudo apt-get install -y -qq gdb', shell=True, capture_output=True)
            print(subprocess.run(['sudo', 'gdb', '-p', str(p.pid), '-batch', '-ex', 'thread apply all bt'],
                                 capture_output=True, text=True).stdout, file=sys.stderr, flush=True)
        p.kill()
        out, err = p.communicate()
    os.unlink(f.name)
    # the last match: completing a MIP start prints a solving report first
    g = lambda pat, s=out: (m[-1] if (m := re.findall(pat, s, re.M)) else None)
    num = lambda x: float(x) if x not in (None, 'inf', '-inf') else None
    traj, last = [], None
    for l in out.splitlines():
        m = LOGLINE.match(l)
        if m and m.group(1) != last and num(m.group(1)) is not None:
            last = m.group(1)
            traj.append([float(m.group(2)), float(m.group(1))])
    gap = g(r'^  Gap\s+(\S+)%')
    r = dict(cfg=cname, inst=name(inst), seed=seed, arch='arm64' if cpu.startswith('Apple') else platform.machine(), cpu=cpu, tl=a.tl, threads=a.threads,
             status='hung' if hung else g(r'^  Status\s+(.*)$'), primal=num(g(r'^  Primal bound\s+(\S+)')), dual=num(g(r'^  Dual bound\s+(\S+)')),
             gap=num(gap), pdi=num(g(r'^  P-D integral\s+(\S+)')), time=num(g(r'^  Timing\s+(\S+)')),
             nodes=int(g(r'^  Nodes\s+(\d+)') or 0), lpit=int(g(r'^  LP iterations\s+(\d+)') or 0), traj=traj)
    if r['status'] is None:
        # a crash: the panic message, or the signal (code only, no model data)
        print(f"{name(inst)} s{seed}: exit {p.returncode}\n" + '\n'.join(err.splitlines()[-5:]), file=sys.stderr, flush=True)
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
        return [run_one(c, inst, seed, a, cpu) for c in order if (c[0], name(inst), seed) not in done]

    with open(a.out, 'a') as out, ThreadPoolExecutor(a.j) as ex:
        for rs in ex.map(pair, pairs):
            for r in rs:
                out.write(json.dumps(r) + '\n')
                print(f"{r['cfg']:12s} {r['inst']:40s} s{r['seed']:<3d} {r['status']}  obj {r['primal']}  gap {r['gap']}%  {r['time']}s", flush=True)
            out.flush()


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
p = sp.add_parser('report')
p.add_argument('res', nargs='+')
p.add_argument('--base')
t = sp.add_parser('names')
t.add_argument('files', nargs='+')
a = ap.parse_args()
{'run': run, 'report': report, 'names': lambda a: [print(name(f), os.path.basename(f)) for f in a.files]}[a.cmd](a)
