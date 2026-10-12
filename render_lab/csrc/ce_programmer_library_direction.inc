/* Native selector permutation for nonspatial directions; spatial keys share
 * the fixed CPython core through ce_programmer_library_sort. */
static int ce_pl_direction(ce_pl_context *c) {
    const ce_pl_request *q=c->q;int direction=(int)(q->flags>>PL_DIRECTION_SHIFT);
    if(direction>=2 && direction<=7) return -1;
    int64_t k=0;
    if(direction==8) {
        for(int64_t i=0;i<q->n;i+=2) q->out_i[k++]=i;
        for(int64_t i=1;i<q->n;i+=2) q->out_i[k++]=i;
    } else for(int64_t i=0;i<q->n;++i) q->out_i[i]=direction==1?q->n-1-i:i;
    if(direction==1 || direction==8) c->r->direction+=(uint64_t)q->n;
    return 0;
}
