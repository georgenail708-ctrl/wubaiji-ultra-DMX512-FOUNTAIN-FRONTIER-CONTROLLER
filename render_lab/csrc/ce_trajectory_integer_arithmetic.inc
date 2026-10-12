/* Exact sign/magnitude addition/subtraction adapts CPython x_add/x_sub.
 * This ABI uses portable base-256 bytes rather than private PyLong digits. */
static int ce_tr_compare_abs(const ce_tr_big *a,const ce_tr_big *b) {
    if(a->size!=b->size) return a->size>b->size?1:-1;
    for(size_t i=a->size;i>0;--i)
        if(a->digits[i-1]!=b->digits[i-1]) return a->digits[i-1]>b->digits[i-1]?1:-1;
    return 0;
}
static int ce_tr_add(const ce_tr_big *a,const ce_tr_big *b,int bsign,ce_tr_big *out) {
    const ce_tr_big *ce_tr_large=a,*ce_tr_small=b;
    int as=a->sign,bs=b->sign*bsign,cmp; unsigned carry=0;
    size_t n=a->size>b->size?a->size:b->size;
    if(ce_tr_alloc(out,n+1)) return -2;
    if(as==bs || !as || !bs) {
        out->sign=as?as:bs;
        for(size_t i=0;i<n;++i) {
            unsigned x=(i<a->size?a->digits[i]:0)+(i<b->size?b->digits[i]:0)+carry;
            out->digits[i]=(uint8_t)x; carry=x>>8;
        }
        out->digits[n]=(uint8_t)carry;
    } else {
        cmp=ce_tr_compare_abs(a,b);
        if(cmp<0) { ce_tr_large=b; ce_tr_small=a; }
        out->sign=cmp<0?bs:as;
        for(size_t i=0;i<n;++i) {
            int x=(i<ce_tr_large->size?ce_tr_large->digits[i]:0)-(i<ce_tr_small->size?ce_tr_small->digits[i]:0)-(int)carry;
            carry=x<0; out->digits[i]=(uint8_t)x;
        }
    }
    ce_tr_normalize(out); return 0;
}
