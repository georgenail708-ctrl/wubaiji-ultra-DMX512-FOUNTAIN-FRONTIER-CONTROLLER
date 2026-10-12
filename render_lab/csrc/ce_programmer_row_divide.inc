/* Generic bounded sign/magnitude quotient, same CPython scaled/sticky proof.
 * Used for actual step floor/n-1 when both are Python arbitrary integers. */
static void ce_pr_shift_remainder(ce_tr_big *r,size_t capacity,unsigned bit) {
    unsigned carry=bit;
    for(size_t i=0;i<r->size;++i) {
        unsigned v=((unsigned)r->digits[i]<<1)|carry;
        r->digits[i]=(uint8_t)v;carry=v>>8;
    }
    if(carry && r->size<capacity) r->digits[r->size++]=(uint8_t)carry;
    if(!r->size && bit) { r->digits[0]=(uint8_t)bit;r->size=1; }
    r->sign=r->size?1:0;
}
static unsigned ce_pr_subtract_remainder(ce_tr_big *r,const ce_tr_big *b) {
    if(ce_tr_compare_abs(r,b)<0) return 0;
    unsigned borrow=0;
    for(size_t i=0;i<r->size;++i) {
        int v=r->digits[i]-(i<b->size?b->digits[i]:0)-(int)borrow;
        borrow=v<0;r->digits[i]=(uint8_t)v;
    }
    ce_tr_normalize(r);return 1;
}
static int ce_pr_round_quotient(uint64_t quotient,int shift,int sticky,int sign,double *out) {
    int extra=(int)ce_pr_u64_bits(quotient)-DBL_MANT_DIG;
    if(shift+extra<DBL_MIN_EXP-DBL_MANT_DIG) extra=DBL_MIN_EXP-DBL_MANT_DIG-shift;
    if(extra>0) {
        uint64_t half=(uint64_t)1<<(extra-1);
        uint64_t lost=quotient&(((uint64_t)1<<extra)-1);
        quotient>>=extra;
        if(lost>half || (lost==half && (sticky || (quotient&1)))) ++quotient;
        shift+=extra;
    }
    *out=ldexp((double)quotient,shift);
    if(isinf(*out)) return PR_DIV_BIG;
    if(sign<0) *out=-*out;return 0;
}
static int ce_pr_big_divide(const ce_tr_big *a,const ce_tr_big *b,double *out) {
    if(!b->sign) return CE_TR_ZERO;
    if(!a->sign) { *out=b->sign<0?-0.:0.;return 0; }
    size_t abits=ce_pr_bits(a),bbits=ce_pr_bits(b);
    if(abits>bbits && abits-bbits>1025) return PR_DIV_BIG;
    if(bbits>abits && bbits-abits>1076) { *out=a->sign*b->sign<0?-0.:0.;return 0; }
    int diff=abits>=bbits?(int)(abits-bbits):-(int)(bbits-abits);
    int shift=(diff>DBL_MIN_EXP?diff:DBL_MIN_EXP)-DBL_MANT_DIG-2;
    ce_tr_big remainder={0};size_t capacity=b->size+1;
    int rc=ce_tr_alloc(&remainder,capacity);if(rc) return rc;
    remainder.size=0;remainder.sign=0;uint64_t quotient=0;
    size_t first=shift>0?(size_t)shift:0;
    for(size_t k=abits;k>first;--k) {
        ce_pr_shift_remainder(&remainder,capacity,(unsigned)ce_tr_bit(a,k-1));
        quotient=(quotient<<1)|ce_pr_subtract_remainder(&remainder,b);
    }
    if(shift<0) for(int k=0;k<-shift;++k) {
        ce_pr_shift_remainder(&remainder,capacity,0);
        quotient=(quotient<<1)|ce_pr_subtract_remainder(&remainder,b);
    }
    int sticky=remainder.sign!=0 || (shift>0 && ce_pr_sticky(a,(size_t)shift));
    rc=ce_pr_round_quotient(quotient,shift,sticky,a->sign*b->sign,out);
    ce_tr_big_clear(&remainder);return rc;
}
