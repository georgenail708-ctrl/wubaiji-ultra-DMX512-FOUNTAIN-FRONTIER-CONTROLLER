/* Pure typed comparison; no PyAPI, callback, tie-by-index or nonfinite veto. */
static int ce_ps_same_owner(uint64_t a,uint64_t b,int64_t left,int64_t right) {
    return left==right || (a && a==b);
}
static int ce_ps_compare(int64_t left,int64_t right,ce_ps_MergeState *ms) {
    const ce_ps_key *a=ms->prepared+left,*b=ms->prepared+right;
    ++ms->report->comparisons;
    if(!a->pair) return a->first<b->first;
    if(!ce_ps_same_owner(a->first_owner,b->first_owner,left,right)
        && !(a->first==b->first)) return a->first<b->first;
    if(!ce_ps_same_owner(a->second_owner,b->second_owner,left,right)
        && !(a->second==b->second)) return a->second<b->second;
    return 0;
}
