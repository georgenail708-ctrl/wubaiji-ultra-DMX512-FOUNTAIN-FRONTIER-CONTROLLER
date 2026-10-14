/* Exact decimal-six successor of the retained decimal3/4 adaptation.
 * Original reference CPython3.13.13 floatobject.c double_round/dtoa: round the
 * exact binary64 value to the nearest decimal quantum, half even, then round
 * that rational back to binary64. No scaled-double round/multiply shortcut.
 * SOURCE ONLY: independent full-bit qualification has NOT been run. */
static uint64_t r6_decimal_integer(uint64_t low,uint64_t high,int fraction)
{
    uint64_t integer,remainder,halfway;int more;
    if (fraction>=128) return 0;
    if (fraction<64) {
        integer=(low>>fraction)|(high<<(64-fraction));
        remainder=low&((UINT64_C(1)<<fraction)-1);halfway=UINT64_C(1)<<(fraction-1);
        more=remainder>halfway || (remainder==halfway && (integer&1));
    } else if (fraction==64) {
        integer=high;halfway=UINT64_C(1)<<63;
        more=low>halfway || (low==halfway && (integer&1));
    } else {
        int upper=fraction-64;integer=high>>upper;
        remainder=high&((UINT64_C(1)<<upper)-1);halfway=UINT64_C(1)<<(upper-1);
        more=remainder>halfway || (remainder==halfway && (low!=0 || (integer&1)));
    }
    return integer+(uint64_t)more;
}

static double r6_decimal6(double value)
{
    uint64_t raw,significand;memcpy(&raw,&value,8);
    unsigned eb=(unsigned)((raw>>52)&0x7ffU);
    if (eb==0x7ffU || value==0. || fabs(value)>=8589934592.) return value;
    significand=raw&UINT64_C(0x000fffffffffffff);int exponent=-1074;
    if (eb) {significand|=UINT64_C(0x0010000000000000);exponent=(int)eb-1023-52;}
    /* 53-bit significand *5**6 needs67 bits. Two exact32-bit products avoid
     * unsigned overflow and work on both original MSVC x64 ISA builds. */
    uint64_t a=(significand&UINT64_C(0xffffffff))*15625U,b=(significand>>32)*15625U;
    uint64_t low=a+(b<<32),high=(b>>32)+(low<a);int fraction=-(exponent+6);
    if (fraction<=0) return value; /* unreachable under the admitted boundary */
    uint64_t integer=r6_decimal_integer(low,high,fraction);
    volatile double divisor=1000000.;
    return copysign((double)integer/divisor,value);
}
