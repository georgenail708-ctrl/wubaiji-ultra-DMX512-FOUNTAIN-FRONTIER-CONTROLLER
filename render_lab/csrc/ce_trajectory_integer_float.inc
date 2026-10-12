/* Nearest-even integer conversion adapts _PyLong_Frexp/PyLong_AsDouble;
 * finite integral reconstruction adapts PyLong_FromDouble, fixed CPython ref. */
static int ce_tr_bit(const ce_tr_big *v,size_t bit) {
    return (v->digits[bit>>3]>>(bit&7))&1;
}
static int ce_tr_to_double(const ce_tr_big *v,double *out) {
    size_t bits; unsigned topbits=0; uint64_t top=0; int sticky=0;
    if(!v->size) { *out=0.; return 0; }
    for(unsigned x=v->digits[v->size-1];x;x>>=1) ++topbits;
    if(v->size>129) return CE_TR_BIG;
    bits=(v->size-1)*8+topbits;
    if(bits>1024) return CE_TR_BIG;
    size_t keep=bits<53?bits:53, shift=bits-keep;
    for(size_t i=0;i<keep;++i) top=(top<<1)|ce_tr_bit(v,bits-i-1);
    if(shift) {
        for(size_t i=0;i+1<shift;++i) if(ce_tr_bit(v,i)) { sticky=1; break; }
        if(ce_tr_bit(v,shift-1) && (sticky || (top&1))) ++top;
    }
    *out=ldexp((double)top,(int)shift);
    if(isinf(*out)) return CE_TR_BIG;
    if(v->sign<0) *out=-*out;
    return 0;
}
static int ce_tr_from_double(double value,ce_tr_big *out) {
    int exponent,neg=value<0.; double f; size_t n;
    if(!isfinite(value)) return -1;
    f=frexp(fabs(value),&exponent);
    n=exponent>0?((size_t)exponent+7)/8:1;
    if(ce_tr_alloc(out,n)) return -2;
    for(int bit=exponent-1;bit>=0;--bit) {
        f*=2.; if(f>=1.) { out->digits[(unsigned)bit>>3]|=(uint8_t)(1u<<(bit&7)); f-=1.; }
    }
    out->sign=neg?-1:1; ce_tr_normalize(out); return 0;
}
static int ce_tr_value_double(const ce_traj_request *q,const ce_traj_value *v,
                             ce_traj_report *r,double *out) {
    ce_tr_big number={0}; int rc;
    if(v->kind==CE_TR_FLOAT) { *out=v->number; return 0; }
    if(v->kind==CE_TR_TYPE || v->kind==CE_TR_OBJECT) return CE_TR_PARSE_TYPE;
    if(v->kind==CE_TR_VALUE) return CE_TR_PARSE_VALUE;
    if(v->kind==CE_TR_OVERFLOW) return CE_TR_BIG;
    rc=ce_tr_decode(q,v,&number);
    if(!rc) { rc=ce_tr_to_double(&number,out); if(!rc) ++r->integer_conversions; }
    ce_tr_big_clear(&number); return rc;
}
static int ce_tr_round(double value,double *out) {
    if(isnan(value)) return CE_TR_NAN;
    if(isinf(value)) return CE_TR_INF;
    *out=_ce_round_half_even(value); return 0;
}
