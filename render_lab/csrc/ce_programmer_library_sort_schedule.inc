/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static ptrdiff_t
ce_ps_merge_at(ce_ps_MergeState *ms, ptrdiff_t i)
{
    ce_ps_sortslice ssa, ssb;
    ptrdiff_t na, nb;
    ptrdiff_t k;

    assert(ms != NULL);
    assert(ms->n >= 2);
    assert(i >= 0);
    assert(i == ms->n - 2 || i == ms->n - 3);

    ssa = ms->pending[i].base;
    na = ms->pending[i].len;
    ssb = ms->pending[i+1].base;
    nb = ms->pending[i+1].len;
    assert(na > 0 && nb > 0);
    assert(ssa.keys + na == ssb.keys);

    /* Record the length of the combined runs; if i is the 3rd-last
     * run now, also slide over the last run (which isn't involved
     * in this merge).  The current run i+1 goes away in any case.
     */
    ms->pending[i].len = na + nb;
    if (i == ms->n - 3)
        ms->pending[i+1] = ms->pending[i+2];
    --ms->n;

    /* Where does b start in a?  Elements in a before that can be
     * ignored (already in place).
     */
    k = ce_ps_gallop_right(ms, *ssb.keys, ssa.keys, na, 0);
    if (k < 0)
        return -1;
    ce_ps_sortslice_advance(&ssa, k);
    na -= k;
    if (na == 0)
        return 0;

    /* Where does a end in b?  Elements in b after that can be
     * ignored (already in place).
     */
    nb = ce_ps_gallop_left(ms, ssa.keys[na-1], ssb.keys, nb, nb-1);
    if (nb <= 0)
        return nb;

    /* Merge what remains of the runs, using a temp array with
     * min(na, nb) elements.
     */
    if (na <= nb)
        return ce_ps_merge_lo(ms, ssa, na, ssb, nb);
    else
        return ce_ps_merge_hi(ms, ssa, na, ssb, nb);
}

static int
ce_ps_powerloop(ptrdiff_t s1, ptrdiff_t n1, ptrdiff_t n2, ptrdiff_t n)
{
    int result = 0;
    assert(s1 >= 0);
    assert(n1 > 0 && n2 > 0);
    assert(s1 + n1 + n2 <= n);
    /* midpoints a and b:
     * a = s1 + n1/2
     * b = s1 + n1 + n2/2 = a + (n1 + n2)/2
     *
     * Those may not be integers, though, because of the "/2". So we work with
     * 2*a and 2*b instead, which are necessarily integers. It makes no
     * difference to the outcome, since the bits in the expansion of (2*i)/n
     * are merely shifted one position from those of i/n.
     */
    ptrdiff_t a = 2 * s1 + n1;  /* 2*a */
    ptrdiff_t b = a + n1 + n2;  /* 2*b */
    /* Emulate a/n and b/n one bit a time, until bits differ. */
    for (;;) {
        ++result;
        if (a >= n) {  /* both quotient bits are 1 */
            assert(b >= a);
            a -= n;
            b -= n;
        }
        else if (b >= n) {  /* a/n bit is 0, b/n bit is 1 */
            break;
        } /* else both quotient bits are 0 */
        assert(a < b && b < n);
        a <<= 1;
        b <<= 1;
    }
    return result;
}

static int
ce_ps_found_new_run(ce_ps_MergeState *ms, ptrdiff_t n2)
{
    assert(ms);
    if (ms->n) {
        assert(ms->n > 0);
        struct ce_ps_s_slice *p = ms->pending;
        ptrdiff_t s1 = p[ms->n - 1].base.keys - ms->basekeys; /* start index */
        ptrdiff_t n1 = p[ms->n - 1].len;
        int power = ce_ps_powerloop(s1, n1, n2, ms->listlen);
        while (ms->n > 1 && p[ms->n - 2].power > power) {
            if (ce_ps_merge_at(ms, ms->n - 2) < 0)
                return -1;
        }
        assert(ms->n < 2 || p[ms->n - 2].power < power);
        p[ms->n - 1].power = power;
    }
    return 0;
}

static int
ce_ps_merge_force_collapse(ce_ps_MergeState *ms)
{
    struct ce_ps_s_slice *p = ms->pending;

    assert(ms);
    while (ms->n > 1) {
        ptrdiff_t n = ms->n - 2;
        if (n > 0 && p[n-1].len < p[n+1].len)
            --n;
        if (ce_ps_merge_at(ms, n) < 0)
            return -1;
    }
    return 0;
}

static ptrdiff_t
ce_ps_merge_compute_minrun(ptrdiff_t n)
{
    ptrdiff_t r = 0;           /* becomes 1 if any 1 bits are shifted off */

    assert(n >= 0);
    while (n >= CE_PS_MAX_MINRUN) {
        r |= n & 1;
        n >>= 1;
    }
    return n + r;
}
