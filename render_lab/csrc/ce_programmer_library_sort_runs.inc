/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static int
ce_ps_binarysort(ce_ps_MergeState *ms, const ce_ps_sortslice *ss, ptrdiff_t n, ptrdiff_t ok)
{
    ptrdiff_t k; /* for CE_PS_IFLT macro expansion */
    int64_t * const a = ss->keys;
    int64_t * const v = ss->values;
    const bool has_values = v != NULL;
    int64_t pivot;
    ptrdiff_t M;

    assert(0 <= ok && ok <= n && 1 <= n && n <= CE_PS_MAX_MINRUN);
    /* assert a[:ok] is sorted */
    if (! ok)
        ++ok;
    /* Regular insertion sort has average- and worst-case O(n**2) cost
       for both # of comparisons and number of bytes moved. But its branches
       are highly predictable, and it loves sorted input (n-1 compares and no
       data movement). This is significant in cases like sortperf.py's %sort,
       where an out-of-order element near the start of a run is moved into
       place slowly but then the remaining elements up to length minrun are
       generally at worst one slot away from their correct position (so only
       need 1 or 2 commpares to resolve). If comparisons are very fast (such
       as for a list of Python floats), the simple inner loop leaves it
       very competitive with binary insertion, despite that it does
       significantly more compares overall on random data.

       Binary insertion sort has worst, average, and best case O(n log n)
       cost for # of comparisons, but worst and average case O(n**2) cost
       for data movement. The more expensive comparisons, the more important
       the comparison advantage. But its branches are less predictable the
       more "randomish" the data, and that's so significant its worst case
       in real life is random input rather than reverse-ordered (which does
       about twice the data movement than random input does).

       Note that the number of bytes moved doesn't seem to matter. CE_PS_MAX_MINRUN
       of 64 is so small that the key and value pointers all fit in a corner
       of L1 cache, and moving things around in that is very fast. */
#if 0 // ordinary insertion sort.
    int64_t  vpivot = 0;
    for (; ok < n; ++ok) {
        pivot = a[ok];
        if (has_values)
            vpivot = v[ok];
        for (M = ok - 1; M >= 0; --M) {
            k = CE_PS_ISLT(pivot, a[M]);
            if (k < 0) {
                a[M + 1] = pivot;
                if (has_values)
                    v[M + 1] = vpivot;
                goto fail;
            }
            else if (k) {
                a[M + 1] = a[M];
                if (has_values)
                    v[M + 1] = v[M];
            }
            else
                break;
        }
        a[M + 1] = pivot;
        if (has_values)
            v[M + 1] = vpivot;
    }
#else // binary insertion sort
    ptrdiff_t L, R;
    for (; ok < n; ++ok) {
        /* set L to where a[ok] belongs */
        L = 0;
        R = ok;
        pivot = a[ok];
        /* Slice invariants. vacuously true at the start:
         * all a[0:L]  <= pivot
         * all a[L:R]     unknown
         * all a[R:ok]  > pivot
         */
        assert(L < R);
        do {
            /* don't do silly ;-) things to prevent overflow when finding
               the midpoint; L and R are very far from filling a ptrdiff_t */
            M = (L + R) >> 1;
#if 1 // straightforward, but highly unpredictable branch on random data
            CE_PS_IFLT(pivot, a[M])
                R = M;
            else
                L = M + 1;
#else
            /* Try to get compiler to generate conditional move instructions
               instead. Works fine, but leaving it disabled for now because
               it's not yielding consistently faster sorts. Needs more
               investigation. More computation in the inner loop adds its own
               costs, which can be significant when compares are fast. */
            k = CE_PS_ISLT(pivot, a[M]);
            if (k < 0)
                goto fail;
            ptrdiff_t Mp1 = M + 1;
            R = k ? M : R;
            L = k ? L : Mp1;
#endif
        } while (L < R);
        assert(L == R);
        /* a[:L] holds all elements from a[:ok] <= pivot now, so pivot belongs
           at index L. Slide a[L:ok] to the right a slot to make room for it.
           Caution: using memmove is much slower under MSVC 5; we're not
           usually moving many slots. Years later: under Visual Studio 2022,
           memmove seems just slightly slower than doing it "by hand". */
        for (M = ok; M > L; --M)
            a[M] = a[M - 1];
        a[L] = pivot;
        if (has_values) {
            pivot = v[ok];
            for (M = ok; M > L; --M)
                v[M] = v[M - 1];
            v[L] = pivot;
        }
    }
#endif // pick binary or regular insertion sort
    return 0;

 fail:
    return -1;
}

static void
ce_ps_sortslice_reverse(ce_ps_sortslice *s, ptrdiff_t n)
{
    ce_ps_reverse_slice(s->keys, &s->keys[n]);
    if (s->values != NULL)
        ce_ps_reverse_slice(s->values, &s->values[n]);
}

static ptrdiff_t
ce_ps_count_run(ce_ps_MergeState *ms, ce_ps_sortslice *slo, ptrdiff_t nremaining)
{
    ptrdiff_t k; /* used by CE_PS_IFLT macro expansion */
    ptrdiff_t n;
    int64_t * const lo = slo->keys;

    /* In general, as things go on we've established that the slice starts
       with a monotone run of n elements, starting at lo. */

    /* We're n elements into the slice, and the most recent neq+1 elments are
     * all equal. This reverses them in-place, and resets neq for reuse.
     */
#define REVERSE_LAST_NEQ                        \
    if (neq) {                                  \
        ce_ps_sortslice slice = *slo;                 \
        ++neq;                                  \
        ce_ps_sortslice_advance(&slice, n - neq);     \
        ce_ps_sortslice_reverse(&slice, neq);         \
        neq = 0;                                \
    }

    /* Sticking to only __lt__ compares is confusing and error-prone. But in
     * this routine, almost all uses of CE_PS_IFLT can be captured by tiny macros
     * giving mnemonic names to the intent. Note that inline functions don't
     * work for this (CE_PS_IFLT expands to code including `goto fail`).
     */
#define IF_NEXT_LARGER  CE_PS_IFLT(lo[n-1], lo[n])
#define IF_NEXT_SMALLER CE_PS_IFLT(lo[n], lo[n-1])

    assert(nremaining);
    /* try ascending run first */
    for (n = 1; n < nremaining; ++n) {
        IF_NEXT_SMALLER
            break;
    }
    if (n == nremaining)
        return n;
    /* lo[n] is strictly less */
    /* If n is 1 now, then the first compare established it's a descending
     * run, so fall through to the descending case. But if n > 1, there are
     * n elements in an ascending run terminated by the strictly less lo[n].
     * If the first key < lo[n-1], *somewhere* along the way the sequence
     * increased, so we're done (there is no descending run).
     * Else first key >= lo[n-1], which implies that the entire ascending run
     * consists of equal elements. In that case, this is a descending run,
     * and we reverse the all-equal prefix in-place.
     */
    if (n > 1) {
        CE_PS_IFLT(lo[0], lo[n-1])
            return n;
        ce_ps_sortslice_reverse(slo, n);
    }
    ++n; /* in all cases it's been established that lo[n] has been resolved */

    /* Finish descending run. All-squal subruns are reversed in-place on the
     * fly. Their original order will be restored at the end by the whole-slice
     * reversal.
     */
    ptrdiff_t neq = 0;
    for ( ; n < nremaining; ++n) {
        IF_NEXT_SMALLER {
            /* This ends the most recent run of equal elments, but still in
             * the "descending" direction.
             */
            REVERSE_LAST_NEQ
        }
        else {
            IF_NEXT_LARGER /* descending run is over */
                break;
            else /* not x < y and not y < x implies x == y */
                ++neq;
        }
    }
    REVERSE_LAST_NEQ
    ce_ps_sortslice_reverse(slo, n); /* transform to ascending run */

    /* And after reversing, it's possible this can be extended by a
     * naturally increasing suffix; e.g., [3, 2, 3, 4, 1] makes an
     * ascending run from the first 4 elements.
     */
    for ( ; n < nremaining; ++n) {
        IF_NEXT_SMALLER
            break;
    }

    return n;
fail:
    return -1;

#undef REVERSE_LAST_NEQ
#undef IF_NEXT_SMALLER
#undef IF_NEXT_LARGER
}
