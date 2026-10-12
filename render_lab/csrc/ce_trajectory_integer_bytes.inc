/* Signed byte serialization; callers retain immutable pools until C returns. */
static int ce_tr_decode(const ce_traj_request *q,const ce_traj_value *v,ce_tr_big *out) {
    size_t n; unsigned carry=1; int neg;
    if(v->kind==CE_TR_BOOL) {
        if(ce_tr_alloc(out,1)) return -2;
        out->digits[0]=(uint8_t)(v->number!=0); ce_tr_normalize(out); return 0;
    }
    if(v->kind!=CE_TR_INT || !ce_tr_bounds(v->offset,v->size,q->integer_size)
       || !v->size || !q->integers) return -1;
    n=(size_t)v->size; if(ce_tr_alloc(out,n)) return -2;
    neg=(q->integers[v->offset+v->size-1]&128)!=0;
    for(size_t i=0;i<n;++i) {
        unsigned x=q->integers[v->offset+(int64_t)i];
        if(neg) { x=(x^255)+carry; carry=x>>8; }
        out->digits[i]=(uint8_t)x;
    }
    out->sign=neg?-1:1; ce_tr_normalize(out); return 0;
}
static int ce_tr_encode(const ce_tr_big *v,const ce_traj_request *q,
                        ce_traj_report *r,ce_traj_value *out) {
    size_t n=v->size+1; unsigned carry=1;
    if(n>(size_t)INT64_MAX || !ce_tr_bounds(r->written_bytes,(int64_t)n,q->byte_capacity)
       || !q->bytes_out) return -2;
    memset(out,0,sizeof(*out)); out->kind=CE_TR_INT;
    out->offset=r->written_bytes; out->size=(int64_t)n;
    for(size_t i=0;i<n;++i) {
        unsigned x=i<v->size?v->digits[i]:0;
        if(v->sign<0) { x=(x^255)+carry; carry=x>>8; }
        q->bytes_out[r->written_bytes+(int64_t)i]=(uint8_t)x;
    }
    r->written_bytes+=(int64_t)n; return 0;
}
static int ce_tr_small(ce_tr_big *v,int number) {
    unsigned n=(unsigned)(number<0?-number:number);
    if(ce_tr_alloc(v,4)) return -2;
    for(size_t i=0;i<4;++i) { v->digits[i]=(uint8_t)n; n>>=8; }
    v->sign=number<0?-1:1; ce_tr_normalize(v); return 0;
}
static int ce_tr_byte_value(const ce_tr_big *v,int *value) {
    if(v->sign<0 || v->size>1) return 0;
    *value=v->size?v->digits[0]:0; return 1;
}
