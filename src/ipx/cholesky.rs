//! Crestline: a sparse Cholesky factorization of the normal matrix
//! AI*W*AI' (not in IPX, whose KKT solvers are iterative). The rows are
//! ordered by minimum degree on the quotient graph, with the approximate
//! degrees of Amestoy, Davis and Duff (no supervariables); the
//! factorization is the up-looking one (row by row along the elimination
//! tree). It serves as the preconditioner of the conjugate residual method
//! (kkt.rs), which then takes one or two steps.

use super::model::Model;

pub struct NormalCholesky {
    m: usize,
    n: usize,
    /// perm[new] = old row, iperm[old] = new row
    perm: Vec<u32>,
    iperm: Vec<u32>,
    /// A by columns, the rows permuted and ascending within a column
    ac_p: Vec<usize>,
    ac_i: Vec<u32>,
    ac_x: Vec<f64>,
    /// A by (permuted) rows
    ar_p: Vec<usize>,
    ar_j: Vec<u32>,
    ar_x: Vec<f64>,
    /// the upper triangle of the permuted normal matrix by columns (pattern)
    up: Vec<usize>,
    ui: Vec<u32>,
    parent: Vec<i32>,
    /// L by columns, the diagonal entry first
    lp: Vec<usize>,
    li: Vec<u32>,
    lx: Vec<f64>,
    // work
    x: Vec<f64>,
    c: Vec<usize>,
    stack: Vec<u32>,
    flag: Vec<u32>,
    /// pivots replaced because they were not positive
    pub replaced: usize,
}

/// The structure is not worth factorizing beyond these (dense columns of A
/// make the normal matrix dense)
const MAX_NORMAL_ENTRIES: usize = 20_000_000;
const MAX_FACTOR_ENTRIES: usize = 40_000_000;

impl NormalCholesky {
    /// The ordering and the symbolic factorization; None if the normal
    /// matrix or its factor would be too dense
    pub fn analyze(model: &Model) -> Option<NormalCholesky> {
        let m = model.rows();
        let n = model.cols();
        let ai = model.ai();
        let (cp, ci, cx) = (&ai.colptr, &ai.rowidx, &ai.values);
        let col = |j: usize| cp[j] as usize..cp[j + 1] as usize;
        // entries of A*A' counted with multiplicity
        let pairs: usize = (0..n).map(|j| col(j).len() * col(j).len()).sum();
        if m == 0 || pairs / 2 > MAX_NORMAL_ENTRIES {
            return None;
        }
        // A by rows (original order)
        let mut rp = vec![0usize; m + 1];
        for j in 0..n {
            for p in col(j) {
                rp[ci[p] as usize + 1] += 1;
            }
        }
        for i in 0..m {
            rp[i + 1] += rp[i];
        }
        let mut rj = vec![0u32; rp[m]];
        let mut next = rp.clone();
        for j in 0..n {
            for p in col(j) {
                let i = ci[p] as usize;
                rj[next[i]] = j as u32;
                next[i] += 1;
            }
        }
        // adjacency of the normal matrix (both triangles, no diagonal)
        let mut adj: Vec<Vec<u32>> = vec![Vec::new(); m];
        let mut mark = vec![u32::MAX; m];
        let mut total = 0usize;
        for i in 0..m {
            mark[i] = i as u32;
            for &j in &rj[rp[i]..rp[i + 1]] {
                for p in col(j as usize) {
                    let k = ci[p] as usize;
                    if mark[k] != i as u32 {
                        mark[k] = i as u32;
                        adj[i].push(k as u32);
                    }
                }
            }
            total += adj[i].len();
            if total / 2 > MAX_NORMAL_ENTRIES {
                return None;
            }
        }
        let perm = minimum_degree(adj);
        let mut iperm = vec![0u32; m];
        for (new, &old) in perm.iter().enumerate() {
            iperm[old as usize] = new as u32;
        }
        // A by columns with permuted, ascending rows
        let mut ac_p = vec![0usize; n + 1];
        let mut ac_i = Vec::with_capacity(cp[n] as usize);
        let mut ac_x = Vec::with_capacity(cp[n] as usize);
        let mut tmp: Vec<(u32, f64)> = Vec::new();
        for j in 0..n {
            tmp.clear();
            tmp.extend(col(j).map(|p| (iperm[ci[p] as usize], cx[p])));
            tmp.sort_unstable_by_key(|e| e.0);
            for &(i, v) in &tmp {
                ac_i.push(i);
                ac_x.push(v);
            }
            ac_p[j + 1] = ac_i.len();
        }
        // A by permuted rows
        let mut ar_p = vec![0usize; m + 1];
        for &i in &ac_i {
            ar_p[i as usize + 1] += 1;
        }
        for i in 0..m {
            ar_p[i + 1] += ar_p[i];
        }
        let mut ar_j = vec![0u32; ar_p[m]];
        let mut ar_x = vec![0.0; ar_p[m]];
        let mut next = ar_p.clone();
        for j in 0..n {
            for p in ac_p[j]..ac_p[j + 1] {
                let i = ac_i[p] as usize;
                ar_j[next[i]] = j as u32;
                ar_x[next[i]] = ac_x[p];
                next[i] += 1;
            }
        }
        // the upper triangle's pattern by columns (rows < k, then k)
        let mut up = vec![0usize; m + 1];
        let mut ui: Vec<u32> = Vec::new();
        let mut mark = vec![u32::MAX; m];
        for k in 0..m {
            mark[k] = k as u32;
            for q in ar_p[k]..ar_p[k + 1] {
                let j = ar_j[q] as usize;
                for p in ac_p[j]..ac_p[j + 1] {
                    let i = ac_i[p] as usize;
                    if i >= k {
                        break;
                    }
                    if mark[i] != k as u32 {
                        mark[i] = k as u32;
                        ui.push(i as u32);
                    }
                }
            }
            up[k + 1] = ui.len();
        }
        // elimination tree
        let mut parent = vec![-1i32; m];
        let mut ancestor = vec![-1i32; m];
        for k in 0..m {
            for &i in &ui[up[k]..up[k + 1]] {
                let mut r = i as i32;
                while r != -1 && (r as usize) < k {
                    let nx = ancestor[r as usize];
                    ancestor[r as usize] = k as i32;
                    if nx == -1 {
                        parent[r as usize] = k as i32;
                    }
                    r = nx;
                }
            }
        }
        // column counts of L (the rows' patterns are reaches in the tree)
        let mut count = vec![1usize; m];
        let mut flag = vec![u32::MAX; m];
        for k in 0..m {
            flag[k] = k as u32;
            for &i in &ui[up[k]..up[k + 1]] {
                let mut r = i as usize;
                while flag[r] != k as u32 {
                    flag[r] = k as u32;
                    count[r] += 1;
                    r = parent[r] as usize;
                }
            }
        }
        let mut lp = vec![0usize; m + 1];
        for k in 0..m {
            lp[k + 1] = lp[k] + count[k];
        }
        if lp[m] > MAX_FACTOR_ENTRIES {
            return None;
        }
        Some(NormalCholesky {
            m,
            n,
            perm,
            iperm,
            ac_p,
            ac_i,
            ac_x,
            ar_p,
            ar_j,
            ar_x,
            up,
            ui,
            parent,
            li: vec![0; lp[m]],
            lx: vec![0.0; lp[m]],
            lp,
            x: vec![0.0; m],
            c: vec![0; m],
            stack: vec![0; m],
            flag: vec![u32::MAX; m],
            replaced: 0,
        })
    }

    pub fn factor_entries(&self) -> usize {
        self.lp[self.m]
    }

    /// (sum of squared column counts, columns with more than 100 entries, the largest column count)
    pub fn factor_stats(&self) -> (f64, usize, usize) {
        let counts = self.lp.windows(2).map(|w| w[1] - w[0]);
        (counts.clone().map(|c| (c * c) as f64).sum(), counts.clone().filter(|&c| c > 100).count(), counts.max().unwrap_or(0))
    }

    /// L*L' = P*(A*W*A' + Ws)*P' for the weights w of the n + m columns
    /// of AI. A pivot that is not positive is replaced by a huge one (its
    /// row then drops out of the solves)
    pub fn factorize(&mut self, w: &[f64]) {
        let (m, n) = (self.m, self.n);
        self.replaced = 0;
        self.c.copy_from_slice(&self.lp[..m]);
        self.flag.fill(u32::MAX);
        let x = &mut self.x;
        for k in 0..m {
            // column k of the upper triangle into x
            let mut d = w[n + self.perm[k] as usize];
            for q in self.ar_p[k]..self.ar_p[k + 1] {
                let j = self.ar_j[q] as usize;
                let t = self.ar_x[q] * w[j];
                for p in self.ac_p[j]..self.ac_p[j + 1] {
                    let i = self.ac_i[p] as usize;
                    if i >= k {
                        if i == k {
                            d += t * self.ac_x[p];
                        }
                        break;
                    }
                    x[i] += t * self.ac_x[p];
                }
            }
            // the pattern of row k of L, in topological order
            let mut top = m;
            self.flag[k] = k as u32;
            for &i in &self.ui[self.up[k]..self.up[k + 1]] {
                let mut len = 0;
                let mut r = i as usize;
                while self.flag[r] != k as u32 {
                    self.flag[r] = k as u32;
                    self.stack[len] = r as u32;
                    len += 1;
                    r = self.parent[r] as usize;
                }
                while len > 0 {
                    len -= 1;
                    top -= 1;
                    self.stack[top] = self.stack[len];
                }
            }
            // the triangular solve for row k
            for t in top..m {
                let j = self.stack[t] as usize;
                let lkj = x[j] / self.lx[self.lp[j]];
                x[j] = 0.0;
                for p in self.lp[j] + 1..self.c[j] {
                    x[self.li[p] as usize] -= self.lx[p] * lkj;
                }
                d -= lkj * lkj;
                let p = self.c[j];
                self.c[j] += 1;
                self.li[p] = k as u32;
                self.lx[p] = lkj;
            }
            let p = self.c[k];
            self.c[k] += 1;
            self.li[p] = k as u32;
            self.lx[p] = if d > 0.0 && d.is_finite() {
                d.sqrt()
            } else {
                self.replaced += 1;
                1e64
            };
        }
    }

    /// lhs = (L*L')^-1 rhs (in the original row order)
    pub fn solve(&mut self, rhs: &[f64], lhs: &mut [f64]) {
        let m = self.m;
        let x = &mut self.x;
        for k in 0..m {
            x[k] = rhs[self.perm[k] as usize];
        }
        for j in 0..m {
            let v = x[j] / self.lx[self.lp[j]];
            x[j] = v;
            for p in self.lp[j] + 1..self.lp[j + 1] {
                x[self.li[p] as usize] -= self.lx[p] * v;
            }
        }
        for j in (0..m).rev() {
            let mut v = x[j];
            for p in self.lp[j] + 1..self.lp[j + 1] {
                v -= self.lx[p] * x[self.li[p] as usize];
            }
            x[j] = v / self.lx[self.lp[j]];
        }
        for k in 0..m {
            lhs[self.perm[k] as usize] = x[k];
            x[k] = 0.0;
        }
    }
}

/// A minimum degree ordering of a symmetric graph (adjacency lists without
/// the diagonal): the order of elimination. Quotient graph with element
/// absorption and approximate external degrees.
fn minimum_degree(mut adj: Vec<Vec<u32>>) -> Vec<u32> {
    let n = adj.len();
    const VAR: u8 = 0;
    const ELEM: u8 = 1;
    const DEAD: u8 = 2;
    let mut state = vec![VAR; n];
    let mut elems: Vec<Vec<u32>> = vec![Vec::new(); n]; // elements adjacent to a variable
    let mut members: Vec<Vec<u32>> = vec![Vec::new(); n]; // variables of an element
    let mut degree: Vec<usize> = adj.iter().map(|a| a.len()).collect();
    // degree lists
    let nil = u32::MAX;
    let mut head = vec![nil; n + 1];
    let mut next = vec![nil; n];
    let mut prev = vec![nil; n];
    let insert = |head: &mut Vec<u32>, next: &mut Vec<u32>, prev: &mut Vec<u32>, i: usize, d: usize| {
        next[i] = head[d];
        prev[i] = nil;
        if head[d] != nil {
            prev[head[d] as usize] = i as u32;
        }
        head[d] = i as u32;
    };
    let remove = |head: &mut Vec<u32>, next: &mut Vec<u32>, prev: &mut Vec<u32>, i: usize, d: usize| {
        if prev[i] != nil {
            next[prev[i] as usize] = next[i];
        } else {
            head[d] = next[i];
        }
        if next[i] != nil {
            prev[next[i] as usize] = prev[i];
        }
    };
    for i in 0..n {
        insert(&mut head, &mut next, &mut prev, i, degree[i]);
    }
    let mut mark = vec![0u32; n];
    let mut stamp = 0u32;
    let mut w = vec![0usize; n];
    let mut wflg = 1usize;
    let mut order = Vec::with_capacity(n);
    let mut mindeg = 0;
    let mut lp: Vec<u32> = Vec::new();
    for done in 0..n {
        while head[mindeg] == nil {
            mindeg += 1;
        }
        let p = head[mindeg] as usize;
        remove(&mut head, &mut next, &mut prev, p, mindeg);
        order.push(p as u32);
        // the new element: the variables adjacent to p, directly or
        // through its elements, which it absorbs
        stamp += 1;
        mark[p] = stamp;
        lp.clear();
        for &v in &adj[p] {
            if state[v as usize] == VAR && mark[v as usize] != stamp {
                mark[v as usize] = stamp;
                lp.push(v);
            }
        }
        for e in std::mem::take(&mut elems[p]) {
            let e = e as usize;
            if state[e] != ELEM {
                continue;
            }
            for &v in &members[e] {
                if mark[v as usize] != stamp {
                    mark[v as usize] = stamp;
                    lp.push(v);
                }
            }
            state[e] = DEAD;
            members[e] = Vec::new();
        }
        adj[p] = Vec::new();
        state[p] = ELEM;
        // w[e] - wflg = |L_e \ L_p| for the elements next to L_p
        wflg += n + 1;
        for &i in &lp {
            for &e in &elems[i as usize] {
                let e = e as usize;
                if state[e] != ELEM {
                    continue;
                }
                if w[e] < wflg {
                    w[e] = wflg + members[e].len();
                }
                w[e] -= 1;
            }
        }
        let left = n - done - 1;
        for &i in &lp {
            let i = i as usize;
            remove(&mut head, &mut next, &mut prev, i, degree[i]);
            adj[i].retain(|&v| state[v as usize] == VAR && mark[v as usize] != stamp);
            let mut d = adj[i].len() + lp.len() - 1;
            let el = &mut elems[i];
            let mut keep = 0;
            for t in 0..el.len() {
                let e = el[t] as usize;
                if state[e] != ELEM {
                    continue;
                }
                let outside = w[e] - wflg;
                if outside == 0 {
                    // all of e is in the new element: absorbed
                    state[e] = DEAD;
                    members[e] = Vec::new();
                    continue;
                }
                d += outside;
                el[keep] = e as u32;
                keep += 1;
            }
            el.truncate(keep);
            el.push(p as u32);
            let d = d.min(left.saturating_sub(1)).min(degree[i] + lp.len() - 1);
            degree[i] = d;
            insert(&mut head, &mut next, &mut prev, i, d);
            if d < mindeg {
                mindeg = d;
            }
        }
        members[p] = std::mem::take(&mut lp);
    }
    order
}

#[cfg(test)]
mod tests {
    use super::minimum_degree;

    #[test]
    fn minimum_degree_orders_an_arrow_last() {
        // a star: the centre 0 is adjacent to all, the leaves only to it;
        // eliminating a leaf first gives no fill
        let n = 6;
        let mut adj = vec![Vec::new(); n];
        for i in 1..n {
            adj[0].push(i as u32);
            adj[i].push(0);
        }
        let order = minimum_degree(adj);
        let mut seen = vec![false; n];
        for &i in &order {
            seen[i as usize] = true;
        }
        assert!(seen.iter().all(|&s| s));
        // the centre is not among the first n - 2
        assert!(order[..n - 2].iter().all(|&i| i != 0));
    }
}
