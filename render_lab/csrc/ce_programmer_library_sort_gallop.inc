/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static ptrdiff_t
ce_ps_gallop_left(ce_ps_MergeState *ms, int64_t key, int64_t *a, ptrdiff_t n, ptrdiff_t hint)
{
    ptrdiff_t ofs;
    ptrdiff_t lastofs;
    ptrdiff_t k;

    assert(key >= 0 && key < ms->listlen && a && n > 0 && hint >= 0 && hint < n);

    a += hint;
    lastofs = 0;
    ofs = 1;
    CE_PS_IFLT(*a, key) {
        /* a[hint] < key -- gallop right, until
         * a[hint + lastofs] < key <= a[hint + ofs]
         */
        const ptrdiff_t maxofs = n - hint;             /* &a[n-1] is highest */
        while (ofs < maxofs) {
            CE_PS_IFLT(a[ofs], key) {
                lastofs = ofs;
                assert(ofs <= (PTRDIFF_MAX - 1) / 2);
                ofs = (ofs << 1) + 1;
            }
            else                /* key <= a[hint + ofs] */
                break;
        }
        if (ofs > maxofs)
            ofs = maxofs;
        /* Translate back to offsets relative to &a[0]. */
        lastofs += hint;
        ofs += hint;
    }
    else {
        /* key <= a[hint] -- gallop left, until
         * a[hint - ofs] < key <= a[hint - lastofs]
         */
        const ptrdiff_t maxofs = hint + 1;             /* &a[0] is lowest */
        while (ofs < maxofs) {
            CE_PS_IFLT(*(a-ofs), key)
                break;
            /* key <= a[hint - ofs] */
            lastofs = ofs;
            assert(ofs <= (PTRDIFF_MAX - 1) / 2);
            ofs = (ofs << 1) + 1;
        }
        if (ofs > maxofs)
            ofs = maxofs;
        /* Translate back to positive offsets relative to &a[0]. */
        k = lastofs;
        lastofs = hint - ofs;
        ofs = hint - k;
    }
    a -= hint;

    assert(-1 <= lastofs && lastofs < ofs && ofs <= n);
    /* Now a[lastofs] < key <= a[ofs], so key belongs somewhere to the
     * right of lastofs but no farther right than ofs.  Do a binary
     * search, with invariant a[lastofs-1] < key <= a[ofs].
     */
    ++lastofs;
    while (lastofs < ofs) {
        ptrdiff_t m = lastofs + ((ofs - lastofs) >> 1);

        CE_PS_IFLT(a[m], key)
            lastofs = m+1;              /* a[m] < key */
        else
            ofs = m;                    /* key <= a[m] */
    }
    assert(lastofs == ofs);             /* so a[ofs-1] < key <= a[ofs] */
    return ofs;

fail:
    return -1;
}

static ptrdiff_t
ce_ps_gallop_right(ce_ps_MergeState *ms, int64_t key, int64_t *a, ptrdiff_t n, ptrdiff_t hint)
{
    ptrdiff_t ofs;
    ptrdiff_t lastofs;
    ptrdiff_t k;

    assert(key >= 0 && key < ms->listlen && a && n > 0 && hint >= 0 && hint < n);

    a += hint;
    lastofs = 0;
    ofs = 1;
    CE_PS_IFLT(key, *a) {
        /* key < a[hint] -- gallop left, until
         * a[hint - ofs] <= key < a[hint - lastofs]
         */
        const ptrdiff_t maxofs = hint + 1;             /* &a[0] is lowest */
        while (ofs < maxofs) {
            CE_PS_IFLT(key, *(a-ofs)) {
                lastofs = ofs;
                assert(ofs <= (PTRDIFF_MAX - 1) / 2);
                ofs = (ofs << 1) + 1;
            }
            else                /* a[hint - ofs] <= key */
                break;
        }
        if (ofs > maxofs)
            ofs = maxofs;
        /* Translate back to positive offsets relative to &a[0]. */
        k = lastofs;
        lastofs = hint - ofs;
        ofs = hint - k;
    }
    else {
        /* a[hint] <= key -- gallop right, until
         * a[hint + lastofs] <= key < a[hint + ofs]
        */
        const ptrdiff_t maxofs = n - hint;             /* &a[n-1] is highest */
        while (ofs < maxofs) {
            CE_PS_IFLT(key, a[ofs])
                break;
            /* a[hint + ofs] <= key */
            lastofs = ofs;
            assert(ofs <= (PTRDIFF_MAX - 1) / 2);
            ofs = (ofs << 1) + 1;
        }
        if (ofs > maxofs)
            ofs = maxofs;
        /* Translate back to offsets relative to &a[0]. */
        lastofs += hint;
        ofs += hint;
    }
    a -= hint;

    assert(-1 <= lastofs && lastofs < ofs && ofs <= n);
    /* Now a[lastofs] <= key < a[ofs], so key belongs somewhere to the
     * right of lastofs but no farther right than ofs.  Do a binary
     * search, with invariant a[lastofs-1] <= key < a[ofs].
     */
    ++lastofs;
    while (lastofs < ofs) {
        ptrdiff_t m = lastofs + ((ofs - lastofs) >> 1);

        CE_PS_IFLT(key, a[m])
            ofs = m;                    /* key < a[m] */
        else
            lastofs = m+1;              /* a[m] <= key */
    }
    assert(lastofs == ofs);             /* so a[ofs-1] <= key < a[ofs] */
    return ofs;

fail:
    return -1;
}
