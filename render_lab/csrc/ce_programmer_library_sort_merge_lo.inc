/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static ptrdiff_t
ce_ps_merge_lo(ce_ps_MergeState *ms, ce_ps_sortslice ssa, ptrdiff_t na,
         ce_ps_sortslice ssb, ptrdiff_t nb)
{
    ptrdiff_t k;
    ce_ps_sortslice dest;
    int result = -1;            /* guilty until proved innocent */
    ptrdiff_t min_gallop;

    assert(ms && ssa.keys && ssb.keys && na > 0 && nb > 0);
    assert(ssa.keys + na == ssb.keys);
    if (CE_PS_MERGE_GETMEM(ms, na) < 0)
        return -1;
    ce_ps_sortslice_memcpy(&ms->a, 0, &ssa, 0, na);
    dest = ssa;
    ssa = ms->a;

    ce_ps_sortslice_copy_incr(&dest, &ssb);
    --nb;
    if (nb == 0)
        goto Succeed;
    if (na == 1)
        goto CopyB;

    min_gallop = ms->min_gallop;
    for (;;) {
        ptrdiff_t acount = 0;          /* # of times A won in a row */
        ptrdiff_t bcount = 0;          /* # of times B won in a row */

        /* Do the straightforward thing until (if ever) one run
         * appears to win consistently.
         */
        for (;;) {
            assert(na > 1 && nb > 0);
            k = CE_PS_ISLT(ssb.keys[0], ssa.keys[0]);
            if (k) {
                if (k < 0)
                    goto Fail;
                ce_ps_sortslice_copy_incr(&dest, &ssb);
                ++bcount;
                acount = 0;
                --nb;
                if (nb == 0)
                    goto Succeed;
                if (bcount >= min_gallop)
                    break;
            }
            else {
                ce_ps_sortslice_copy_incr(&dest, &ssa);
                ++acount;
                bcount = 0;
                --na;
                if (na == 1)
                    goto CopyB;
                if (acount >= min_gallop)
                    break;
            }
        }

        /* One run is winning so consistently that galloping may
         * be a huge win.  So try that, and continue galloping until
         * (if ever) neither run appears to be winning consistently
         * anymore.
         */
        ++min_gallop;
        do {
            assert(na > 1 && nb > 0);
            min_gallop -= min_gallop > 1;
            ms->min_gallop = min_gallop;
            k = ce_ps_gallop_right(ms, ssb.keys[0], ssa.keys, na, 0);
            acount = k;
            if (k) {
                if (k < 0)
                    goto Fail;
                ce_ps_sortslice_memcpy(&dest, 0, &ssa, 0, k);
                ce_ps_sortslice_advance(&dest, k);
                ce_ps_sortslice_advance(&ssa, k);
                na -= k;
                if (na == 1)
                    goto CopyB;
                /* na==0 is impossible now if the comparison
                 * function is consistent, but we can't assume
                 * that it is.
                 */
                if (na == 0)
                    goto Succeed;
            }
            ce_ps_sortslice_copy_incr(&dest, &ssb);
            --nb;
            if (nb == 0)
                goto Succeed;

            k = ce_ps_gallop_left(ms, ssa.keys[0], ssb.keys, nb, 0);
            bcount = k;
            if (k) {
                if (k < 0)
                    goto Fail;
                ce_ps_sortslice_memmove(&dest, 0, &ssb, 0, k);
                ce_ps_sortslice_advance(&dest, k);
                ce_ps_sortslice_advance(&ssb, k);
                nb -= k;
                if (nb == 0)
                    goto Succeed;
            }
            ce_ps_sortslice_copy_incr(&dest, &ssa);
            --na;
            if (na == 1)
                goto CopyB;
        } while (acount >= CE_PS_MIN_GALLOP || bcount >= CE_PS_MIN_GALLOP);
        ++min_gallop;           /* penalize it for leaving galloping mode */
        ms->min_gallop = min_gallop;
    }
Succeed:
    result = 0;
Fail:
    if (na)
        ce_ps_sortslice_memcpy(&dest, 0, &ssa, 0, na);
    return result;
CopyB:
    assert(na == 1 && nb > 0);
    /* The last element of ssa belongs at the end of the merge. */
    ce_ps_sortslice_memmove(&dest, 0, &ssb, 0, nb);
    ce_ps_sortslice_copy(&dest, nb, &ssa, 0);
    return 0;
}
