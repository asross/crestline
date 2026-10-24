//! The concurrent LNS helper (HighsMipSolverData's startConcurrentLns,
//! syncConcurrentLns, crossoverWithMain, publishRootCuts, importRootCuts
//! and stopConcurrentLns): a second MIP solver on a copy of the presolved
//! model, run in its own thread, that only does the root LP, cuts and
//! graph LNS. The two exchange incumbents, bounds and root cuts through a
//! [`Pool`], owned (with the thread) by the main solver's [`Main`] in
//! HighsMipScalars::concurrent_lns; the helper's HighsMipSolver points to
//! the pool (MipData::helper_pool), and so do the main solver's sub-MIPs,
//! which stop once the helper has closed the gap (MipData::lns_target).
//!
//! C++ keeps the object shells: the helper's options, model and basis
//! (CMipFns::helper_new) and its HighsMipSolver with its scheduler, timer
//! and profiling (CMipFns::helper_run), the LP rows and cut pool of the
//! root cut exchange. The atomics have the C++'s orderings (sequentially
//! consistent, the limit checks relaxed, the root cuts release/acquire).

use super::glue::{self, lp_status, MipData, Worker, P};
use super::root::op as rop;
use crate::lp_data::LogType;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicI64, AtomicU64, Ordering::*};
use std::sync::{Arc, Condvar, Mutex, OnceLock};

const INF: f64 = f64::INFINITY;
/// kSolutionSourceGraphLns
const SOURCE_GRAPH_LNS: i32 = 3;

/// An f64 in an AtomicU64 (std::atomic<double>)
struct AtomicF64(AtomicU64);
impl AtomicF64 {
    fn new(x: f64) -> Self {
        Self(AtomicU64::new(x.to_bits()))
    }
    fn load(&self) -> f64 {
        f64::from_bits(self.0.load(SeqCst))
    }
    fn store(&self, x: f64) {
        self.0.store(x.to_bits(), SeqCst)
    }
}

#[derive(Default)]
struct Best {
    /// best solution offered so far
    solution: Vec<f64>,
    objective: f64,
    /// each one's best while they search independently ([0]: main solver)
    own_solution: [Vec<f64>; 2],
    own_objective: [f64; 2],
    /// the crossover's log values
    crossover_differ: i32,
    crossover_before: f64,
    crossover_after: f64,
}

/// The helper's root cuts (rows a.x <= rhs)
pub struct RootCuts {
    pub start: Vec<i32>,
    pub index: Vec<i32>,
    pub value: Vec<f64>,
    pub rhs: Vec<f64>,
    pub integral: Vec<u8>,
}

/// What one side of a deterministic exchange shows the other at a sync
/// point: its state there, stamped with its work clock (i64::MAX once it
/// is done)
#[derive(Clone)]
struct Snap {
    work: i64,
    lower_bound: f64,
    /// its incumbent (INF: none)
    objective: f64,
    solution: Arc<Vec<f64>>,
    /// main: its quick search is done (its incumbent is then the
    /// crossover partner); helper: still independent, its root cuts are
    /// published, the crossover's state and log values
    quick_done: bool,
    independent: bool,
    root_cuts: bool,
    crossover: (i32, i32, f64, f64),
}

/// A side's own state in a deterministic exchange
#[derive(Default)]
struct Local {
    /// the objective of the other side's solution last tried
    seen: f64,
    /// main: its quick search is done; still independent (until the
    /// final sync); the helper's root cuts are there, its root bound
    quick_done: bool,
    independent: bool,
    root_cuts: bool,
    helper_lower_bound: f64,
    /// helper: the main solver's crossover partner; the crossover's state
    /// and log values (main: 3 once logged)
    partner: Option<(Arc<Vec<f64>>, f64)>,
    crossover: (i32, i32, f64, f64),
    /// wall seconds spent waiting for the other side
    waited: f64,
}

/// mip_deterministic: the two sides exchange only at their sync points,
/// each publishing its state stamped with its work clock (the LP
/// iterations of its thread, sub-MIPs included, as they happen) and taking
/// the other's latest state of at most RATIO times its own work, waiting
/// until the other's clock is beyond that. Which state is taken depends
/// only on the work counts, so a run with a given seed takes the same path
/// however the threads are scheduled (unless it stops at the time limit)
struct Det {
    snaps: Mutex<[Vec<Snap>; 2]>,
    cv: Condvar,
    /// each side's work clock as it goes: beyond a target, no state of
    /// work at most the target is still to come
    live: [AtomicI64; 2],
    local: [Mutex<Local>; 2],
    /// the clock at the time limit: no waiting beyond it
    deadline: f64,
}

/// The other side's state taken at work w is its state at RATIO * w (both
/// ways; RATIO^2 < 1, or the two could wait for each other for ever)
const RATIO: f64 = 0.5;

thread_local! {
    /// The LP iterations of this thread's LP solves: the work clock
    static WORK: std::cell::Cell<i64> = const { std::cell::Cell::new(0) };
    /// the live clock of the deterministic exchange side this thread is
    static LIVE: std::cell::Cell<*const AtomicI64> = const { std::cell::Cell::new(std::ptr::null()) };
}

/// Counts LP iterations on this thread's work clock
pub fn add_work(n: i64) {
    let w = WORK.with(|c| {
        c.set(c.get() + n);
        c.get()
    });
    let live = LIVE.with(|c| c.get());
    if !live.is_null() {
        // SAFETY: set by a side of a live exchange, cleared before it ends
        unsafe { (*live).store(w, SeqCst) };
    }
}

impl Det {
    fn new(deadline: f64) -> Det {
        let local = || Mutex::new(Local { seen: INF, helper_lower_bound: -INF, ..Default::default() });
        Det {
            snaps: Mutex::new([Vec::new(), Vec::new()]),
            cv: Condvar::new(),
            live: [AtomicI64::new(0), AtomicI64::new(0)],
            local: [local(), local()],
            deadline,
        }
    }
    /// This thread is side `who` (its work clock from 0), or no side
    fn attach(&self, who: Option<usize>) {
        WORK.with(|c| c.set(0));
        LIVE.with(|c| c.set(who.map_or(std::ptr::null(), |w| &self.live[w] as *const AtomicI64)));
    }
    fn publish(&self, who: usize, snap: Snap) {
        self.snaps.lock().unwrap()[who].push(snap);
        self.cv.notify_all();
    }
    /// The other side's latest state of work at most `target`, once its
    /// clock is beyond it; drops its older states
    fn take(&self, who: usize, target: i64) -> Option<Snap> {
        let other = 1 - who;
        let t0 = std::time::Instant::now();
        let mut b = self.snaps.lock().unwrap();
        while b[other].last().is_none_or(|s| s.work <= target)
            && self.live[other].load(SeqCst) <= target
            // past the time limit the run stops where it is anyway
            && crate::util::clock::now() < self.deadline
        {
            // the other side's LP solves move its clock without a
            // notification: poll
            b = self.cv.wait_timeout(b, std::time::Duration::from_millis(2)).unwrap().0;
        }
        self.local[who].lock().unwrap().waited += t0.elapsed().as_secs_f64();
        let v = &mut b[other];
        let k = v.iter().rposition(|s| s.work <= target)?;
        v.drain(..k);
        Some(v[0].clone())
    }
    /// The side is done: the other no longer waits for it
    fn done(&self, who: usize) {
        self.live[who].store(i64::MAX, SeqCst);
        let mut b = self.snaps.lock().unwrap();
        if let Some(last) = b[who].last().cloned().filter(|s| s.work != i64::MAX) {
            b[who].push(Snap { work: i64::MAX, ..last });
        }
        self.cv.notify_all();
    }
}

/// HighsConcurrentLns
pub struct Pool {
    /// mip_deterministic's exchange (then the fields above root_cuts are
    /// unused)
    det: Option<Box<Det>>,
    best: Mutex<Best>,
    version: AtomicI64,
    stop: AtomicBool,
    /// the main solver's lower bound, and whether the helper has found a
    /// solution within the target gap of it (the main solver then stops)
    main_lower_bound: AtomicF64,
    target_reached: AtomicBool,
    /// the helper's lower bound (its root LP with its own cuts), which the
    /// main solver takes during its root node
    helper_lower_bound: AtomicF64,
    /// written once (rootCutsReady)
    root_cuts: OnceLock<RootCuts>,
    /// until the helper has crossed its incumbent with the main solver's,
    /// at the end of its own quick search, the two search independently
    independent: AtomicBool,
    main_quick_done: AtomicBool,
    /// the main solver's settings of the heuristics that the helper turns
    /// off
    pub run_rins: bool,
    pub run_rens: bool,
    pub run_root_reduced_cost: bool,
    /// the crossover, for the main solver's log: 1 running, 2 done (then
    /// the main solver logs it and sets 3)
    crossover_state: AtomicI32,
}

impl Pool {
    fn offer(&self, sol: &[f64], obj: f64) {
        let mut b = self.best.lock().unwrap();
        if obj >= b.objective {
            return;
        }
        b.objective = obj;
        b.solution.clear();
        b.solution.extend_from_slice(sol);
        self.version.fetch_add(1, SeqCst);
    }
    /// The best solution if it is newer than seen and better than obj
    fn take(&self, seen: &mut i64, obj: f64) -> Option<Vec<f64>> {
        if self.version.load(SeqCst) == *seen {
            return None;
        }
        let b = self.best.lock().unwrap();
        *seen = self.version.load(SeqCst);
        if b.objective >= obj {
            return None;
        }
        Some(b.solution.clone())
    }
    fn offer_own(&self, who: usize, sol: &[f64], obj: f64) {
        let mut b = self.best.lock().unwrap();
        if obj >= b.own_objective[who] {
            return;
        }
        b.own_objective[who] = obj;
        b.own_solution[who].clear();
        b.own_solution[who].extend_from_slice(sol);
    }
    fn own_best(&self, who: usize) -> Option<(Vec<f64>, f64)> {
        let b = self.best.lock().unwrap();
        if b.own_solution[who].is_empty() {
            return None;
        }
        Some((b.own_solution[who].clone(), b.own_objective[who]))
    }
    /// The helper is to stop (relaxed, as the C++ limit check)
    pub fn stopped(&self) -> bool {
        self.stop.load(Relaxed)
    }
    /// The helper has closed the main solver's gap (relaxed)
    pub fn target_reached(&self) -> bool {
        self.target_reached.load(Relaxed)
    }
    pub fn independent(&self) -> bool {
        self.independent.load(SeqCst)
    }
}

/// The main solver's helper: the pool and the thread
pub struct Main {
    pub pool: Arc<Pool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Drop for Main {
    /// stopConcurrentLns
    fn drop(&mut self) {
        // on the main solver's thread, whose clock points into the pool
        if let Some(d) = &self.pool.det {
            d.done(0);
            d.attach(None);
        }
        self.pool.stop.store(true, SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// A raw pointer that may cross to the helper thread
struct SendPtr(P);
// SAFETY: the helper's data is handed to the thread and used only there
unsafe impl Send for SendPtr {}

impl MipData {
    /// The main solver's own pool (null without a helper)
    pub fn own_lns(&self) -> Option<&Pool> {
        let p = self.sc().concurrent_lns;
        // SAFETY: a Main from start_concurrent_lns, freed only by
        // stop_concurrent_lns on this thread
        (!p.is_null()).then(|| unsafe { &*(*p).pool })
    }
    /// The pool shared with the main solver, in a helper
    pub fn helper_lns(&self) -> Option<&Pool> {
        // SAFETY: the main solver's pool outlives its helper's solve
        (!self.helper_pool.is_null()).then(|| unsafe { &*self.helper_pool })
    }
    /// The pool whose target ends this sub-MIP's solve
    pub fn lns_target(&self) -> Option<&Pool> {
        // SAFETY: the pool of a solver above this sub-MIP, alive during it
        (!self.lns_target.is_null()).then(|| unsafe { &*self.lns_target })
    }
    /// The pool for a sub-MIP's lns_target_reached_
    pub fn sub_mip_lns_target(&self) -> *const Pool {
        self.own_lns().map_or(self.lns_target, |p| p as *const Pool)
    }

    /// The concurrent bits of checkLimits, in its order
    pub fn concurrent_limit(&self) -> bool {
        self.helper_lns().is_some_and(|p| p.stopped())
            || self.own_lns().is_some_and(|p| p.target_reached())
            || self.lns_target().is_some_and(|p| p.target_reached())
    }

    /// addIncumbent in a helper: offer the incumbent
    pub fn concurrent_offer(&self, upper_bound: f64) {
        if let Some(p) = self.helper_lns().filter(|p| p.det.is_none()) {
            p.offer(self.incumbent(), upper_bound);
        }
    }
    /// addIncumbent in a helper: its solution within the target gap of its
    /// main solver's bound finishes the main solve
    pub fn concurrent_target(&self, optimality_limit: f64) {
        if let Some(p) = self.helper_lns().filter(|p| p.det.is_none()) {
            if p.main_lower_bound.load() > optimality_limit {
                p.target_reached.store(true, SeqCst);
            }
        }
    }

    /// startConcurrentLns
    #[inline(never)]
    pub fn start_concurrent_lns(&self) {
        if self.own_lns().is_some()
            || self.op(rop::USE_CONCURRENT_HELPER, None, 0, 0.0) == 0.0
            || self.op(rop::FIRSTROOTBASIS_VALID, None, 0, 0.0) == 0.0
        {
            return;
        }
        let time_left = self.opts.time_limit - self.timer_read();
        if time_left < 1.0 {
            return;
        }
        // boxed: this thread's clock points into it
        let det = self.opts.mip_deterministic.then(|| Box::new(Det::new(crate::util::clock::now() + time_left)));
        if let Some(d) = &det {
            d.attach(Some(0));
        }
        let pool = Arc::new(Pool {
            det,
            best: Mutex::new(Best { objective: INF, own_objective: [INF; 2], ..Default::default() }),
            version: AtomicI64::new(0),
            stop: AtomicBool::new(false),
            main_lower_bound: AtomicF64::new(-INF),
            target_reached: AtomicBool::new(false),
            helper_lower_bound: AtomicF64::new(-INF),
            root_cuts: OnceLock::new(),
            independent: AtomicBool::new(self.opts.mip_concurrent_crossover),
            main_quick_done: AtomicBool::new(false),
            run_rins: self.opts.run_rins,
            run_rens: self.opts.run_rens,
            run_root_reduced_cost: self.opts.run_root_reduced_cost,
            crossover_state: AtomicI32::new(0),
        });
        let sc = self.sc();
        if !self.incumbent().is_empty() {
            pool.offer(self.incumbent(), sc.upper_bound);
        }
        sc.concurrent_lns_seen = pool.version.load(SeqCst);
        // the helper solves a copy of the presolved model from the root
        // basis, with its own random seed, without the heuristics that
        // solve sub-MIPs
        let data = SendPtr(glue::fns_call_helper_new(self, time_left));
        let run = glue::fns().helper_run;
        let shared = pool.clone();
        let thread = std::thread::Builder::new()
            // a C++ std::thread's stack on Linux (the solve recurses into
            // sub-MIPs)
            .stack_size(8 << 20)
            .spawn(move || {
                let data = data;
                if let Some(d) = &shared.det {
                    d.attach(Some(1));
                }
                // SAFETY: the C++ helper run on its data, which it frees;
                // the pool outlives it (this thread holds a reference)
                unsafe { run(data.0, Arc::as_ptr(&shared) as *const std::ffi::c_void) };
                if let Some(d) = &shared.det {
                    d.done(1);
                    d.attach(None);
                }
            })
            .expect("concurrent LNS helper thread");
        sc.concurrent_lns = Box::into_raw(Box::new(Main { pool, thread: Some(thread) }));
        crate::log_user!(self.log, LogType::Info, "Concurrent LNS helper thread started\n");
    }

    /// stopConcurrentLns
    pub fn stop_concurrent_lns(&self) {
        let sc = self.sc();
        let p = std::mem::replace(&mut sc.concurrent_lns, std::ptr::null_mut());
        if !p.is_null() {
            // SAFETY: from Box::into_raw in start_concurrent_lns
            let main = unsafe { Box::from_raw(p) };
            if let Some(d) = &main.pool.det {
                let (m, h) = (d.local[0].lock().unwrap().waited, d.local[1].lock().unwrap().waited);
                crate::log_dev!(self.log, LogType::Info, "Deterministic exchange: main waited %.2fs, helper %.2fs\n", m, h);
            }
            drop(main);
        }
    }

    /// This side's state for the other (deterministic exchange)
    #[inline(never)]
    fn snap(&self, pool: &Pool, d: &Det, who: usize) -> Snap {
        let sc = self.sc();
        let l = d.local[who].lock().unwrap();
        Snap {
            work: WORK.with(|c| c.get()),
            lower_bound: sc.lower_bound,
            objective: if self.incumbent().is_empty() { INF } else { sc.upper_bound },
            solution: Arc::new(self.incumbent().to_vec()),
            quick_done: l.quick_done,
            independent: pool.independent(),
            root_cuts: pool.root_cuts.get().is_some(),
            crossover: l.crossover,
        }
    }

    /// syncConcurrentLns of the deterministic exchange: publish this side's
    /// state, take the other's
    #[inline(never)]
    fn sync_det(&self, pool: &Pool, d: &Det, helper: bool) {
        let who = helper as usize;
        let snap = self.snap(pool, d, who);
        let target = (RATIO * snap.work as f64) as i64;
        d.publish(who, snap);
        if target < 1 {
            return;
        }
        let Some(o) = d.take(who, target) else { return };
        // while the two search independently (until the crossover), each
        // only offers its incumbents: the main solver takes the best at
        // the end
        let independent = if helper {
            let mut l = d.local[1].lock().unwrap();
            if o.quick_done && l.partner.is_none() && !o.solution.is_empty() {
                l.partner = Some((o.solution.clone(), o.objective));
            }
            pool.independent()
        } else {
            let mut l = d.local[0].lock().unwrap();
            l.root_cuts = o.root_cuts;
            l.helper_lower_bound = o.lower_bound;
            let independent = o.independent && l.independent;
            drop(l);
            // the helper's bound is valid for the same model; the tree
            // search has its own
            if self.sc().num_nodes == 0 && o.lower_bound > self.sc().lower_bound {
                self.update_lower_bound_ex(o.lower_bound, true, true);
            }
            let (state, differ, before, after) = o.crossover;
            if state >= 1 && !self.sc().crossover_start_logged {
                self.sc().crossover_start_logged = true;
                crate::log_user!(self.log, LogType::Info, "Crossover of the two searches' solutions started\n");
            }
            let mut l = d.local[0].lock().unwrap();
            if state == 2 && l.crossover.0 != 3 {
                l.crossover.0 = 3;
                drop(l);
                crate::log_user!(
                    self.log,
                    LogType::Info,
                    "Crossover (%d integer columns differ): %.12g -> %.12g\n",
                    differ,
                    before + self.offset,
                    after + self.offset
                );
            }
            independent
        };
        if !independent && o.objective < self.sc().upper_bound {
            let mut l = d.local[who].lock().unwrap();
            if o.objective < l.seen {
                l.seen = o.objective;
                drop(l);
                self.try_solution_rs(&o.solution, SOURCE_GRAPH_LNS);
            }
        }
    }

    /// syncConcurrentLns
    pub fn sync_concurrent_lns(&self) {
        let helper = self.helper_lns();
        let Some(pool) = helper.or(self.own_lns()) else { return };
        if let Some(d) = &pool.det {
            return self.sync_det(pool, d, helper.is_some());
        }
        // while the two search independently (until the crossover), each
        // only offers its incumbents: the main solver takes the best at
        // the end
        let independent = pool.independent();
        if helper.is_some() {
            let sc = self.sc();
            pool.helper_lower_bound.store(sc.lower_bound);
            // the helper's bound with its incumbent may close the gap on its
            // own
            if sc.upper_bound < INF && cmax(sc.lower_bound, pool.main_lower_bound.load()) > sc.optimality_limit {
                pool.target_reached.store(true, SeqCst);
            }
        } else {
            // the helper's bound is valid for the same model; the tree
            // search has its own
            let helper_bound = pool.helper_lower_bound.load();
            if self.sc().num_nodes == 0 && helper_bound > self.sc().lower_bound {
                self.update_lower_bound_ex(helper_bound, true, true);
            }
            pool.main_lower_bound.store(self.sc().lower_bound);
            let state = pool.crossover_state.load(SeqCst);
            if state == 1 && !self.sc().crossover_start_logged {
                self.sc().crossover_start_logged = true;
                crate::log_user!(self.log, LogType::Info, "Crossover of the two searches' solutions started\n");
            } else if state == 2 && pool.crossover_state.compare_exchange(2, 3, SeqCst, SeqCst).is_ok() {
                let (differ, before, after) = {
                    let b = pool.best.lock().unwrap();
                    (b.crossover_differ, b.crossover_before, b.crossover_after)
                };
                crate::log_user!(
                    self.log,
                    LogType::Info,
                    "Crossover (%d integer columns differ): %.12g -> %.12g\n",
                    differ,
                    before + self.offset,
                    after + self.offset
                );
            }
        }
        let ub = self.sc().upper_bound;
        if !independent {
            if let Some(sol) = pool.take(&mut self.sc().concurrent_lns_seen, ub) {
                self.try_solution_rs(&sol, SOURCE_GRAPH_LNS);
            }
        }
        let inc = self.incumbent();
        if !inc.is_empty() {
            let ub = self.sc().upper_bound;
            pool.offer(inc, ub);
            if independent {
                pool.offer_own(helper.is_some() as usize, inc, ub);
            }
        }
    }

    /// The main solver's quick search is done: its partner solution for
    /// the helper's crossover
    pub fn concurrent_main_quick_done(&self) {
        if let Some(p) = self.own_lns() {
            if let Some(d) = &p.det {
                if d.local[0].lock().unwrap().independent {
                    d.local[0].lock().unwrap().quick_done = true;
                    self.sync_concurrent_lns();
                }
            } else if p.independent() {
                self.sync_concurrent_lns();
                p.main_quick_done.store(true, SeqCst);
            }
        }
    }

    /// The end of the solve: no longer independent, so the helper's best
    /// solution is taken even if no crossover took place; returns the
    /// helper's lower bound
    pub fn concurrent_final_sync(&self) -> f64 {
        if let Some(d) = self.own_lns().and_then(|p| p.det.as_ref()) {
            d.local[0].lock().unwrap().independent = false;
            self.sync_concurrent_lns();
            d.done(0);
            return d.local[0].lock().unwrap().helper_lower_bound;
        }
        if let Some(p) = self.own_lns() {
            p.independent.store(false, SeqCst);
        }
        self.sync_concurrent_lns();
        self.own_lns().map_or(-INF, |p| p.helper_lower_bound.load())
    }

    /// crossoverWithMain: after its quick search, the helper crosses its
    /// incumbent with the main solver's (from the main solver's own quick
    /// search, with another seed): solutions from different seeds differ
    /// much more than solutions of one search over time. Then the two
    /// exchange incumbents as usual
    pub fn crossover_with_main(&self, w: &Worker) {
        let Some(pool) = self.helper_lns() else { return };
        if let Some(d) = &pool.det {
            return self.crossover_det(pool, d, w);
        }
        // the main solver's partner solution is the end of its quick search
        if !pool.independent() || !pool.main_quick_done.load(SeqCst) {
            return;
        }
        self.sync_concurrent_lns();
        let other = pool.own_best(0);
        pool.independent.store(false, SeqCst);
        let Some((other, other_objective)) = other else { return };
        if self.incumbent().is_empty() {
            return;
        }
        pool.best.lock().unwrap().crossover_before = cmin(self.sc().upper_bound, other_objective);
        pool.crossover_state.store(1, SeqCst);
        let time_limit = self.opts.time_limit;
        // SAFETY: the solver's heuristics, used as by the C++ crossover call
        let heur = unsafe { &*self.heur };
        let differ =
            heur.crossover(self, w, &other, other_objective, if time_limit < INF { 0.2 * time_limit } else { INF });
        {
            let mut b = pool.best.lock().unwrap();
            b.crossover_differ = differ;
            b.crossover_after = self.sc().upper_bound;
        }
        pool.crossover_state.store(2, SeqCst);
        self.sync_concurrent_lns();
    }

    /// crossoverWithMain of the deterministic exchange
    #[inline(never)]
    fn crossover_det(&self, pool: &Pool, d: &Det, w: &Worker) {
        if !pool.independent() {
            return;
        }
        // the main solver's partner solution is the end of its quick search
        self.sync_concurrent_lns();
        let Some((other, other_objective)) = d.local[1].lock().unwrap().partner.clone() else { return };
        pool.independent.store(false, SeqCst);
        if self.incumbent().is_empty() {
            return;
        }
        d.local[1].lock().unwrap().crossover = (1, 0, cmin(self.sc().upper_bound, other_objective), 0.0);
        let time_limit = self.opts.time_limit;
        // SAFETY: the solver's heuristics, used as by the C++ crossover call
        let heur = unsafe { &*self.heur };
        let differ =
            heur.crossover(self, w, &other, other_objective, if time_limit < INF { 0.2 * time_limit } else { INF });
        {
            let mut l = d.local[1].lock().unwrap();
            l.crossover = (2, differ, l.crossover.2, self.sc().upper_bound);
        }
        self.sync_concurrent_lns();
    }

    /// publishRootCuts: the helper hands its root cuts to the main solver,
    /// which adds them to its own when it gets to its root cuts. The LP's
    /// cut rows come from C++ (setRootCuts)
    pub fn publish_root_cuts(&self) {
        let Some(pool) = self.helper_lns() else { return };
        if pool.root_cuts.get().is_some() {
            return;
        }
        self.op(rop::PUBLISH_ROOT_CUTS, None, 0, 0.0);
    }

    /// importRootCuts: returns whether the LP is infeasible
    pub fn import_root_cuts(&self, w: &Worker) -> bool {
        let Some(pool) = self.own_lns() else { return false };
        if self.sc().root_cuts_imported {
            return false;
        }
        // deterministic: once a state of the helper taken says they are there
        if !root_cuts_taken(pool) {
            return false;
        }
        let Some(cuts) = pool.root_cuts.get() else { return false };
        self.sc().root_cuts_imported = true;
        for i in 0..cuts.rhs.len() {
            let (s, e) = (cuts.start[i] as usize, cuts.start[i + 1] as usize);
            glue::add_root_cut(self, &cuts.index[s..e], &cuts.value[s..e], cuts.rhs[i], cuts.integral[i] != 0);
        }
        // bring the violated ones into the LP until none is
        for _ in 0..20 {
            if self.op(rop::SEPARATE_POOL_INTO_LP, None, 0, 0.0) == 0.0 {
                break;
            }
            if self.evaluate_root_lp(w) == lp_status::INFEASIBLE {
                return true;
            }
        }
        false
    }
}

/// The root cuts may be imported: always, unless deterministic, where a
/// state of the helper taken must say they are there (not inlined: inlined,
/// it made the Xcode 15.0 linker drop a relocation's addend in
/// evaluate_root_node, see bench/macho_reloc_check.py)
#[inline(never)]
fn root_cuts_taken(pool: &Pool) -> bool {
    pool.det.as_ref().is_none_or(|d| d.local[0].lock().unwrap().root_cuts)
}

/// std::min / std::max
fn cmin(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}
fn cmax(a: f64, b: f64) -> f64 {
    if a < b {
        b
    } else {
        a
    }
}

/// The helper's root cuts from its LP (publishRootCuts)
///
/// # Safety
/// `pool` a helper's pool; the arrays of the given lengths
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn highs_rs_concurrent_lns_set_root_cuts(
    pool: *const Pool,
    start: *const i32,
    num_cuts: i32,
    index: *const i32,
    value: *const f64,
    nnz: i32,
    rhs: *const f64,
    integral: *const u8,
) {
    use crate::ffi::sl;
    let _ = (*pool).root_cuts.set(RootCuts {
        start: sl(start, num_cuts + 1).to_vec(),
        index: sl(index, nnz).to_vec(),
        value: sl(value, nnz).to_vec(),
        rhs: sl(rhs, num_cuts).to_vec(),
        integral: sl(integral, num_cuts).to_vec(),
    });
}

/// ~HighsMipSolverData: stops a helper still running
///
/// # Safety
/// `main` the address of HighsMipScalars::concurrent_lns
#[no_mangle]
pub unsafe extern "C" fn highs_rs_concurrent_lns_stop(main: *mut *mut Main) {
    let p = std::mem::replace(&mut *main, std::ptr::null_mut());
    if !p.is_null() {
        drop(Box::from_raw(p));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pool() -> Pool {
        Pool {
            det: None,
            best: Mutex::new(Best { objective: INF, own_objective: [INF; 2], ..Default::default() }),
            version: AtomicI64::new(0),
            stop: AtomicBool::new(false),
            main_lower_bound: AtomicF64::new(-INF),
            target_reached: AtomicBool::new(false),
            helper_lower_bound: AtomicF64::new(-INF),
            root_cuts: OnceLock::new(),
            independent: AtomicBool::new(false),
            main_quick_done: AtomicBool::new(false),
            run_rins: true,
            run_rens: false,
            run_root_reduced_cost: true,
            crossover_state: AtomicI32::new(0),
        }
    }

    #[test]
    fn offer_take() {
        let p = pool();
        let mut seen = 0;
        assert!(p.take(&mut seen, INF).is_none());
        p.offer(&[1.0, 2.0], 5.0);
        p.offer(&[3.0], 6.0); // worse: ignored
        assert_eq!(p.version.load(SeqCst), 1);
        assert!(p.take(&mut seen, 5.0).is_none()); // not better
        assert_eq!(seen, 1);
        p.offer(&[0.0, 1.0], 4.0);
        assert_eq!(p.take(&mut seen, 5.0), Some(vec![0.0, 1.0]));
        assert!(p.take(&mut seen, 5.0).is_none()); // seen
        p.offer_own(1, &[7.0], 3.0);
        assert!(p.own_best(0).is_none());
        assert_eq!(p.own_best(1), Some((vec![7.0], 3.0)));
    }

    fn snap(work: i64, objective: f64) -> Snap {
        Snap {
            work,
            lower_bound: -INF,
            objective,
            solution: Arc::new(vec![objective]),
            quick_done: false,
            independent: false,
            root_cuts: false,
            crossover: (0, 0, 0.0, 0.0),
        }
    }

    /// The state taken depends only on the work stamps, not on how far the
    /// other side has got beyond them
    #[test]
    fn det_take_by_work() {
        let d = Det::new(INF);
        d.publish(1, snap(10, 5.0));
        d.publish(1, snap(30, 4.0));
        d.publish(1, snap(50, 3.0));
        assert_eq!(d.take(0, 35).unwrap().objective, 4.0);
        assert_eq!(d.take(0, 49).unwrap().objective, 4.0);
        d.publish(1, snap(70, 2.0));
        assert_eq!(d.take(0, 50).unwrap().objective, 3.0);
        d.done(1);
        assert_eq!(d.take(0, 1000).unwrap().objective, 2.0);
        // a waiting side wakes once the other's clock passes the target
        let d = Arc::new(Det::new(INF));
        let d2 = d.clone();
        let t = std::thread::spawn(move || d2.take(1, 20).map(|s| s.objective));
        d.publish(0, snap(15, 9.0));
        d.publish(0, snap(25, 8.0));
        assert_eq!(t.join().unwrap(), Some(9.0));
    }
}
