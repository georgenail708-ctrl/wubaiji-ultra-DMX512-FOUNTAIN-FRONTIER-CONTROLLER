/* Adapted from CPython3.13.13/01104ce Objects/listobject.c.
 * Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.
 * PSF License Version2, complete license accompanies this candidate.
 * Changes: readonly int64 row selectors in place of PyObject storage;
 * pure typed compare/context; malloc/free failure flag; symbol prefix.
 * Fixed original run/gallop/merge comparison ordering is retained.
 * RESEARCH ONLY: not compiled, executed, admitted or performance qualified. */
static void
ce_ps_merge_init(ce_ps_MergeState *ms, ptrdiff_t list_size, int has_keyfunc,
           ce_ps_sortslice *lo)
{
    assert(ms != NULL);
    if (has_keyfunc) {
        /* The temporary space for merging will need at most half the list
         * size rounded up.  Use the minimum possible space so we can use the
         * rest of temparray for other things.  In particular, if there is
         * enough extra space, listsort() will use it to store the keys.
         */
        ms->alloced = (list_size + 1) / 2;

        /* ms->alloced describes how many keys will be stored at
           ms->temparray, but we also need to store the values.  Hence,
           ms->alloced is capped at half of CE_PS_MERGESTATE_TEMP_SIZE. */
        if (CE_PS_MERGESTATE_TEMP_SIZE / 2 < ms->alloced)
            ms->alloced = CE_PS_MERGESTATE_TEMP_SIZE / 2;
        ms->a.values = &ms->temparray[ms->alloced];
    }
    else {
        ms->alloced = CE_PS_MERGESTATE_TEMP_SIZE;
        ms->a.values = NULL;
    }
    ms->a.keys = ms->temparray;
    ms->n = 0;
    ms->min_gallop = CE_PS_MIN_GALLOP;
    ms->listlen = list_size;
    ms->basekeys = lo->keys;
}

static void
ce_ps_merge_freemem(ce_ps_MergeState *ms)
{
    assert(ms != NULL);
    if (ms->a.keys != ms->temparray) {
        free(ms->a.keys);
        ms->a.keys = NULL;
    }
}

static int
ce_ps_merge_getmem(ce_ps_MergeState *ms, ptrdiff_t need)
{
    int multiplier;

    assert(ms != NULL);
    if (need <= ms->alloced)
        return 0;

    multiplier = ms->a.values != NULL ? 2 : 1;

    /* Don't realloc!  That can cost cycles to copy the old data, but
     * we don't care what's in the block.
     */
    ce_ps_merge_freemem(ms);
    if ((size_t)need > PTRDIFF_MAX / sizeof(int64_t) / multiplier) {
        ms->alloc_failed = 1;
        return -1;
    }
    ms->a.keys = (int64_t *)malloc(multiplier * need
                                          * sizeof(int64_t));
    if (ms->a.keys != NULL) {
        ms->alloced = need;
        if (ms->a.values != NULL)
            ms->a.values = &ms->a.keys[need];
        return 0;
    }
    ms->alloc_failed = 1;
    return -1;
}

#define CE_PS_MERGE_GETMEM(MS, NEED) ((NEED) <= (MS)->alloced ? 0 :   \
                                ce_ps_merge_getmem(MS, NEED))
