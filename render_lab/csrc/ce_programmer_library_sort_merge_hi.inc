/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static ptrdiff_t
ce_ps_merge_hi(ce_ps_MergeState *ms, ce_ps_sortslice ssa, ptrdiff_t na,
         ce_ps_sortslice ssb, ptrdiff_t nb)
{
    ptrdiff_t k;
    ce_ps_sortslice dest, basea, baseb;
    int result = -1;            /* guilty until proved innocent */
    ptrdiff_t min_gallop;

    assert(ms && ssa.keys && ssb.keys && na > 0 && nb > 0);
    assert(ssa.keys + na == ssb.keys);
    if (CE_PS_MERGE_GETMEM(ms, nb) < 0)
        return -1;
    dest = ssb;
    ce_ps_sortslice_advance(&dest, nb-1);
    ce_ps_sortslice_memcpy(&ms->a, 0, &ssb, 0, nb);
    basea = ssa;
    baseb = ms->a;
    ssb.keys = ms->a.keys + nb - 1;
    if (ssb.values != NULL)
        ssb.values = ms->a.values + nb - 1;
    ce_ps_sortslice_advance(&ssa, na - 1);

    ce_ps_sortslice_copy_decr(&dest, &ssa);
    --na;
    if (na == 0)
        goto Succeed;
    if (nb == 1)
        goto CopyA;

    min_gallop = ms->min_gallop;
    for (;;) {
        ptrdiff_t acount = 0;          /* # of times A won in a row */
        ptrdiff_t bcount = 0;          /* # of times B won in a row */

        /* Do the straightforward thing until (if ever) one run
         * appears to win consistently.
         */
        for (;;) {
            assert(na > 0 && nb > 1);
            k = CE_PS_ISLT(ssb.keys[0], ssa.keys[0]);
            if (k) {
                if (k < 0)
                    goto Fail;
                ce_ps_sortslice_copy_decr(&dest, &ssa);
                ++acount;
                bcount = 0;
                --na;
                if (na == 0)
                    goto Succeed;
                if (acount >= min_gallop)
                    break;
            }
            else {
                ce_ps_sortslice_copy_decr(&dest, &ssb);
                ++bcount;
                acount = 0;
                --nb;
                if (nb == 1)
                    goto CopyA;
                if (bcount >= min_gallop)
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
            assert(na > 0 && nb > 1);
            min_gallop -= min_gallop > 1;
            ms->min_gallop = min_gallop;
            k = ce_ps_gallop_right(ms, ssb.keys[0], basea.keys, na, na-1);
            if (k < 0)
                goto Fail;
            k = na - k;
            acount = k;
            if (k) {
                ce_ps_sortslice_advance(&dest, -k);
                ce_ps_sortslice_advance(&ssa, -k);
                ce_ps_sortslice_memmove(&dest, 1, &ssa, 1, k);
                na -= k;
                if (na == 0)
                    goto Succeed;
            }
            ce_ps_sortslice_copy_decr(&dest, &ssb);
            --nb;
            if (nb == 1)
                goto CopyA;

            k = ce_ps_gallop_left(ms, ssa.keys[0], baseb.keys, nb, nb-1);
            if (k < 0)
                goto Fail;
            k = nb - k;
            bcount = k;
            if (k) {
                ce_ps_sortslice_advance(&dest, -k);
                ce_ps_sortslice_advance(&ssb, -k);
                ce_ps_sortslice_memcpy(&dest, 1, &ssb, 1, k);
                nb -= k;
                if (nb == 1)
                    goto CopyA;
                /* nb==0 is impossible now if the comparison
                 * function is consistent, but we can't assume
                 * that it is.
                 */
                if (nb == 0)
                    goto Succeed;
            }
            ce_ps_sortslice_copy_decr(&dest, &ssa);
            --na;
            if (na == 0)
                goto Succeed;
        } while (acount >= CE_PS_MIN_GALLOP || bcount >= CE_PS_MIN_GALLOP);
        ++min_gallop;           /* penalize it for leaving galloping mode */
        ms->min_gallop = min_gallop;
    }
Succeed:
    result = 0;
Fail:
    if (nb)
        ce_ps_sortslice_memcpy(&dest, -(nb-1), &baseb, 0, nb);
    return result;
CopyA:
    assert(nb == 1 && na > 0);
    /* The first element of ssb belongs at the front of the merge. */
    ce_ps_sortslice_memmove(&dest, 1-na, &ssa, 1-na, na);
    ce_ps_sortslice_advance(&dest, -na);
    ce_ps_sortslice_advance(&ssa, -na);
    ce_ps_sortslice_copy(&dest, 0, &ssb, 0);
    return 0;
}
