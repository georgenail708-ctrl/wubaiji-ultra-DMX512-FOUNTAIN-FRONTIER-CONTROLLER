/* Builtin numeric raw bits only. Do not reject NaN merely because another
 * field needs int conversion; each originating operation selects its error. */
static int r5_number(r1_atom atom,double missing,double* output)
{
    int64_t integer;if (atom.reserved) return R1_PENDING;
    switch(atom.kind) {
    case R1_MISSING:*output=missing;break;
    case R1_F64:memcpy(output,&atom.bits,8);break;
    case R1_I64:memcpy(&integer,&atom.bits,8);*output=(double)integer;break;
    case R1_BOOL:*output=atom.bits ? 1. : 0.;break;
    case R1_NONE:*output=0.;break;
    default:return R1_PENDING;
    }
    return R1_OK;
}

static int r5_positive_integer(r1_atom atom,double fallback,uint32_t* result)
{
    double value;int status=r5_number(atom,fallback,&value);if (status) return status;
    if (value==0.) value=fallback;
    if (!isfinite(value)) return R1_PENDING;
    value=trunc(value);if (value<1.) value=1.;
    if (value>INT_MAX) return R1_BUDGET;*result=(uint32_t)value;return R1_OK;
}

static int r5_truth(r1_atom atom,int missing,int* result)
{
    double value;int status=r5_number(atom,(double)missing,&value);
    if (!status) *result=value!=0.;return status;
}

static int r5_text_equal(const r5_text* raw,const char* literal,int lower)
{
    if (raw->reserved || raw->kind>3 || raw->bytes>256 || (raw->bytes && !raw->utf8)) return R1_PENDING;
    if (raw->kind!=1) return 0;
    /* Reject before a length mismatch: Unicode whitespace can change Python
     * strip() into a valid mode and must never silently select our fallback. */
    for (uint64_t i=0;i<raw->bytes;i++) if (raw->utf8[i]>=128) return R1_PENDING;
    uint64_t left=0,right=raw->bytes;
    while (left<right && r4_ascii_space(raw->utf8[left])) left++;
    while (right>left && r4_ascii_space(raw->utf8[right-1])) right--;
    if (right-left!=strlen(literal)) return 0;
    for (uint64_t i=0;i<right-left;i++) {
        uint8_t value=raw->utf8[left+i];
        if (lower && value>='A' && value<='Z') value=(uint8_t)(value+32);
        if (value!=(uint8_t)literal[i]) return 0;
    }
    return 1;
}

static int r5_modes(r5_clip* clip,const r5_clip_raw* raw)
{
    static const char* hold[]={"none","last_frame","last_cycle"};
    static const char* run[]={"normal","reverse","pingpong","loop","once"};
    int found=-1,legacy=0;
    for (int i=0;i<3;i++) {int same=r5_text_equal(&raw->hold,hold[i],1);if (same<0) return same;if (same) found=i;}
    if (r5_truth(raw->values[R5_HOLD_LAST],0,&legacy)) return R1_PENDING;
    clip->hold=found>=0 ? (uint32_t)found : legacy ? R5_HOLD_CYCLE : R5_HOLD_NONE;
    for (int i=0;i<5;i++) {int same=r5_text_equal(&raw->run,run[i],1);if (same<0) return same;if (same) clip->run=(uint32_t)i;}
    int same=r5_text_equal(&raw->sequence,"sequential",1);if (same<0) return same;clip->sequential=same!=0;
    return R1_OK;
}
