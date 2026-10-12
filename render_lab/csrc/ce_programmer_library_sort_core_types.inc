/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static void
ce_ps_reverse_slice(int64_t *lo, int64_t *hi)
{
    assert(lo && hi);

    --hi;
    while (lo < hi) {
        int64_t t = *lo;
        *lo = *hi;
        *hi = t;
        ++lo;
        --hi;
    }
}

/* Lots of code for an adaptive, stable, natural mergesort.  There are many
 * pieces to this algorithm; read listsort.txt for overviews and details.
 */

/* A ce_ps_sortslice contains a pointer to an array of keys and a pointer to
 * an array of corresponding values.  In other words, keys[i]
 * corresponds with values[i].  If values == NULL, then the keys are
 * also the values.
 *
 * Several convenience routines are provided here, so that keys and
 * values are always moved in sync.
 */

typedef struct {
    int64_t *keys;
    int64_t *values;
} ce_ps_sortslice;

static inline void
ce_ps_sortslice_copy(ce_ps_sortslice *s1, ptrdiff_t i, ce_ps_sortslice *s2, ptrdiff_t j)
{
    s1->keys[i] = s2->keys[j];
    if (s1->values != NULL)
        s1->values[i] = s2->values[j];
}

static inline void
ce_ps_sortslice_copy_incr(ce_ps_sortslice *dst, ce_ps_sortslice *src)
{
    *dst->keys++ = *src->keys++;
    if (dst->values != NULL)
        *dst->values++ = *src->values++;
}

static inline void
ce_ps_sortslice_copy_decr(ce_ps_sortslice *dst, ce_ps_sortslice *src)
{
    *dst->keys-- = *src->keys--;
    if (dst->values != NULL)
        *dst->values-- = *src->values--;
}


static inline void
ce_ps_sortslice_memcpy(ce_ps_sortslice *s1, ptrdiff_t i, ce_ps_sortslice *s2, ptrdiff_t j,
                 ptrdiff_t n)
{
    memcpy(&s1->keys[i], &s2->keys[j], sizeof(int64_t) * n);
    if (s1->values != NULL)
        memcpy(&s1->values[i], &s2->values[j], sizeof(int64_t) * n);
}

static inline void
ce_ps_sortslice_memmove(ce_ps_sortslice *s1, ptrdiff_t i, ce_ps_sortslice *s2, ptrdiff_t j,
                  ptrdiff_t n)
{
    memmove(&s1->keys[i], &s2->keys[j], sizeof(int64_t) * n);
    if (s1->values != NULL)
        memmove(&s1->values[i], &s2->values[j], sizeof(int64_t) * n);
}

static inline void
ce_ps_sortslice_advance(ce_ps_sortslice *slice, ptrdiff_t n)
{
    slice->keys += n;
    if (slice->values != NULL)
        slice->values += n;
}

/* Comparison function: ms->key_compare, which is set at run-time in
 * listsort_impl to optimize for various special cases.
 * Returns -1 on error, 1 if x < y, 0 if x >= y.
 */

#define CE_PS_ISLT(X, Y) (*(ms->key_compare))(X, Y, ms)

/* Compare X to Y via "<".  Goto "fail" if the comparison raises an
   error.  Else "k" is set to true iff X<Y, and an "if (k)" block is
   started.  It makes more sense in context <wink>.  X and Y are int64_t s.
*/
#define CE_PS_IFLT(X, Y) if ((k = CE_PS_ISLT(X, Y)) < 0) goto fail;  \
           if (k)

/* The maximum number of entries in a ce_ps_MergeState's pending-runs stack.
 * For a list with n elements, this needs at most floor(log2(n)) + 1 entries
 * even if we didn't force runs to a minimal length.  So the number of bits
 * in a ptrdiff_t is plenty large enough for all cases.
 */
#define CE_PS_MAX_MERGE_PENDING (sizeof(size_t) * 8)

/* When we get into galloping mode, we stay there until both runs win less
 * often than CE_PS_MIN_GALLOP consecutive times.  See listsort.txt for more info.
 */
#define CE_PS_MIN_GALLOP 7

/* Avoid malloc for small temp arrays. */
#define CE_PS_MERGESTATE_TEMP_SIZE 256

/* The largest value of minrun. This must be a power of 2, and >= 1, so that
 * the compute_minrun() algorithm guarantees to return a result no larger than
 * this,
 */
#define CE_PS_MAX_MINRUN 64
#if ((CE_PS_MAX_MINRUN) < 1) || ((CE_PS_MAX_MINRUN) & ((CE_PS_MAX_MINRUN) - 1))
#error "CE_PS_MAX_MINRUN must be a power of 2, and >= 1"
#endif

/* One ce_ps_MergeState exists on the stack per invocation of mergesort.  It's just
 * a convenient way to pass state around among the helper functions.
 */
struct ce_ps_s_slice {
    ce_ps_sortslice base;
    ptrdiff_t len;   /* length of run */
    int power; /* node "level" for powersort merge strategy */
};

typedef struct ce_ps_s_MergeState ce_ps_MergeState;
struct ce_ps_s_MergeState {
    /* This controls when we get *into* galloping mode.  It's initialized
     * to CE_PS_MIN_GALLOP.  ce_ps_merge_lo and ce_ps_merge_hi tend to nudge it higher for
     * random data, and lower for highly structured data.
     */
    ptrdiff_t min_gallop;

    ptrdiff_t listlen;     /* len(input_list) - read only */
    int64_t *basekeys;    /* base address of keys array - read only */

    /* 'a' is temp storage to help with merges.  It contains room for
     * alloced entries.
     */
    ce_ps_sortslice a;        /* may point to temparray below */
    ptrdiff_t alloced;

    /* A stack of n pending runs yet to be merged.  Run #i starts at
     * address base[i] and extends for len[i] elements.  It's always
     * true (so long as the indices are in bounds) that
     *
     *     pending[i].base + pending[i].len == pending[i+1].base
     *
     * so we could cut the storage for this, but it's a minor amount,
     * and keeping all the info explicit simplifies the code.
     */
    int n;
    struct ce_ps_s_slice pending[CE_PS_MAX_MERGE_PENDING];

    /* 'a' points to this when possible, rather than muck with malloc. */
    int64_t temparray[CE_PS_MERGESTATE_TEMP_SIZE];

    /* This is the function we will use to compare two keys,
     * even when none of our special cases apply and we have to use
     * safe_object_compare. */
    int (*key_compare)(int64_t , int64_t , ce_ps_MergeState *);
    const ce_ps_key *prepared; ce_ps_report *report; int alloc_failed;
    void *compare_context;

    /* This function is used by unsafe_object_compare to optimize comparisons
     * when we know our list is type-homogeneous but we can't assume anything else.
     * In the pre-sort check it is set equal to Py_TYPE(key)->tp_richcompare */
    int64_t (*key_richcompare)(int64_t , int64_t , int);

    /* This function is used by unsafe_tuple_compare to compare the first elements
     * of tuples. It may be set to safe_object_compare, but the idea is that hopefully
     * we can assume more, and use one of the special-case compares. */
    int (*tuple_elem_compare)(int64_t , int64_t , ce_ps_MergeState *);
};
