/* Bounded adaptation of CPython 3.13.13 x_mul/long_true_divide, PSF v2.
 * Fixed01104ce: Objects/longobject.c3818-3917/4488-4746; license retained.
 * Portable base256 specialization: integer / positive uint64 denominator. */
static int ce_pr_multiply(const ce_tr_big *a,const ce_tr_big *b,ce_tr_big *out) {
    if(a->size>SIZE_MAX-b->size-1) return -2;
    int rc=ce_tr_alloc(out,a->size+b->size+1);if(rc) return rc;
    out->sign=a->sign*b->sign;
    for(size_t i=0;i<a->size;++i) {
        unsigned carry=0;
        for(size_t j=0;j<b->size;++j) {
            unsigned cell=out->digits[i+j]+a->digits[i]*b->digits[j]+carry;
            out->digits[i+j]=(uint8_t)cell;carry=cell>>8;
        }
        out->digits[i+b->size]=(uint8_t)carry;
    }
    ce_tr_normalize(out);return 0;
}
static size_t ce_pr_bits(const ce_tr_big *a) {
    if(!a->size) return 0;
    unsigned top=0;for(unsigned x=a->digits[a->size-1];x;x>>=1) ++top;
    return (a->size-1)*8+top;
}
static unsigned ce_pr_u64_bits(uint64_t a) {
    unsigned bits=0;for(;a;a>>=1) ++bits;return bits;
}
static int ce_pr_sticky(const ce_tr_big *a,size_t low) {
    size_t bytes=low/8;
    for(size_t i=0;i<bytes;++i) if(a->digits[i]) return 1;
    return (low&7u) && (a->digits[bytes]&((1u<<(low&7u))-1u));
}
static int ce_pr_true_divide(const ce_tr_big *a,uint64_t divisor,double *out) {
    if(!divisor || divisor>INT64_MAX) return -1;
    if(!a->sign) { *out=0.;return 0; }
    size_t bits=ce_pr_bits(a);unsigned dbits=ce_pr_u64_bits(divisor);
    /* Exponent difference beyond this bound cannot produce a finite double. */
    if(bits>(size_t)dbits+1025u) return PR_DIV_BIG;
    int diff=(int)bits-(int)dbits;
    int shift=(diff>DBL_MIN_EXP?diff:DBL_MIN_EXP)-DBL_MANT_DIG-2;
    uint64_t quotient=0,remainder=0;
    size_t first=shift>0?(size_t)shift:0;
    for(size_t k=bits;k>first;--k) {
        remainder=(remainder<<1)|(uint64_t)ce_tr_bit(a,k-1);
        unsigned bit=remainder>=divisor;
        if(bit) remainder-=divisor;
        quotient=(quotient<<1)|bit;
    }
    if(shift<0) for(int k=0;k<-shift;++k) {
        remainder<<=1;unsigned bit=remainder>=divisor;
        if(bit) remainder-=divisor;
        quotient=(quotient<<1)|bit;
    }
    int inexact=remainder!=0 || (shift>0 && ce_pr_sticky(a,(size_t)shift));
    unsigned qbits=ce_pr_u64_bits(quotient);
    int extra=(int)qbits-DBL_MANT_DIG;
    if(shift+extra<DBL_MIN_EXP-DBL_MANT_DIG) extra=DBL_MIN_EXP-DBL_MANT_DIG-shift;
    if(extra>0) {
        uint64_t mask=((uint64_t)1<<extra)-1;
        uint64_t half=(uint64_t)1<<(extra-1),lost=quotient&mask;
        quotient>>=extra;
        if(lost>half || (lost==half && (inexact || (quotient&1)))) ++quotient;
        shift+=extra;
    }
    *out=ldexp((double)quotient,shift);
    if(isinf(*out)) return PR_DIV_BIG;
    if(a->sign<0) *out=-*out;
    return 0;
}
static int ce_pr_small_signed(const ce_tr_big *v,int64_t *out,int bits) {
    if(v->size>8) return 0;
    uint64_t n=0;for(size_t i=v->size;i>0;--i) n=(n<<8)|v->digits[i-1];
    uint64_t limit=(uint64_t)1<<(bits-1);
    if((v->sign>=0 && n>=limit) || (v->sign<0 && n>limit)) return 0;
    if(v->sign<0) *out=n==((uint64_t)1<<63)?INT64_MIN:-(int64_t)n;
    else *out=(int64_t)n;
    return 1;
}
static int ce_pr_num_get(ce_pr_context *c,const ce_traj_value *v,ce_pr_number *out,int stage) {
    memset(out,0,sizeof(*out));out->kind=v->kind;out->selector=v->selector;
    if(v->kind==CE_TR_FLOAT) { out->number=v->number;return 0; }
    if(v->kind==CE_TR_INT || v->kind==CE_TR_BOOL)
        return ce_tr_decode(&c->integers,v,&out->integer);
    return ce_pr_fail(c,PR_OPERAND,stage,v->selector);
}
static int ce_pr_num_double(ce_pr_context *c,const ce_pr_number *v,double *out,int stage) {
    if(v->kind==CE_TR_FLOAT) { *out=v->number;return 0; }
    int rc=ce_tr_to_double(&v->integer,out);
    if(rc) return ce_pr_fail(c,rc,stage,v->selector);
    ++c->r->integer_conversions;return 0;
}
static int ce_pr_num_compare(ce_pr_context *c,const ce_pr_number *a,
                             const ce_pr_number *b,int *out) {
    (void)c;
    if(a->kind==CE_TR_FLOAT && b->kind==CE_TR_FLOAT) {
        *out=isnan(a->number)||isnan(b->number)?2:
            (a->number<b->number?-1:(a->number>b->number?1:0));return 0;
    }
    if(a->kind!=CE_TR_FLOAT && b->kind!=CE_TR_FLOAT) {
        *out=ce_cs_big_compare(&a->integer,&b->integer);return 0;
    }
    if(a->kind==CE_TR_FLOAT) return ce_cs_float_big(a->number,&b->integer,out);
    int rc=ce_cs_float_big(b->number,&a->integer,out);
    if(!rc && *out!=2) *out=-*out;return rc;
}
static void ce_pr_num_clear(ce_pr_number *value) { ce_tr_big_clear(&value->integer); }
static int ce_pr_num_integral(ce_pr_context *c,double value,ce_pr_number *out,int stage) {
    int rc=isnan(value)?CE_TR_NAN:isinf(value)?CE_TR_INF:0;
    if(rc) return ce_pr_fail(c,rc,stage,-1);
    memset(out,0,sizeof(*out));out->kind=CE_TR_INT;out->selector=-1;
    rc=ce_tr_from_double(value,&out->integer);
    if(!rc) ++c->r->integer_conversions;return rc;
}
static int ce_pr_num_small(ce_pr_number *out,int value) {
    memset(out,0,sizeof(*out));out->kind=CE_TR_INT;out->selector=-1;
    return ce_tr_small(&out->integer,value);
}
