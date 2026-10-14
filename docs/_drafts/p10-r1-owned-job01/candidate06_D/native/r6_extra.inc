/* Explicit R6 normalization successor. It does not call the old r3_extra and
 * discard PENDING: all old checks remain, then FI needs actual FI06 plan/sample
 * admission before R6 READY. Old R3/R4/R5 functions/exports are untouched. */
static int r6_extra(r3_clip* clip,const r3_clip_raw* raw)
{
    static const double defaults[R3_EXTRA]={0.,0.,0.,128.,.08,.22,255.,0.};
    double values[R3_EXTRA],integers[2];LtpRequest request={0};LtpReport result={0};int64_t bytes[2];
    for (int i=0;i<R3_EXTRA;i++) if (r1_atom_number(raw->extra[i],defaults[i],values+i)) return R1_PENDING;
    if (r3_integer(raw->extra[R3_THRESHOLD],128.,integers)
            || r3_integer(raw->extra[R3_BRIGHTNESS],255.,integers+1)) return R1_PENDING;
    if (raw->extra[R3_BRIGHTNESS].kind==R1_NONE) integers[1]=255.;
    bytes[0]=(int64_t)(integers[0]==0. ? 128. : integers[0]);bytes[1]=(int64_t)integers[1];
    request.abi=1;request.struct_size=sizeof(request);request.count=4;
    request.integers=(const unsigned char*)bytes;request.integer_size=sizeof(bytes);
    for (int i=0;i<4;i++) request.values[i].selector=(uint32_t)i;
    request.values[0].kind=request.values[3].kind=1;
    request.values[0].length=request.values[3].length=sizeof(int64_t);request.values[3].offset=sizeof(int64_t);
    request.values[1].kind=request.values[2].kind=2;
    request.values[1].number=values[R3_ATTACK] ? values[R3_ATTACK] : .08;
    request.values[2].number=values[R3_RELEASE] ? values[R3_RELEASE] : .22;
    result.abi=1;result.struct_size=sizeof(result);
    if (ce_lt_prepare_v1(&request,&result) || result.semantic) return R1_PENDING;
    clip->fi=_ce_rt_nonnegative(values[R3_FI]);clip->fo=_ce_rt_nonnegative(values[R3_FO]);
    clip->lt=values[R3_LT]!=0.;clip->enabled=values[R3_ENABLED]!=0.;
    clip->threshold=result.threshold;clip->brightness=result.brightness;clip->attack=result.attack;clip->release=result.release;
    clip->curve_in=raw->curve_in;clip->curve_out=raw->curve_out;clip->strategy_in=raw->strategy_in;clip->strategy_out=raw->strategy_out;
    clip->source_kind=raw->source_kind==R3_VALVE ? R3_VALVE : R3_PUMP;
    clip->white=raw->color_is_white!=0;clip->red=(int)raw->red;clip->green=(int)raw->green;clip->blue=(int)raw->blue;
    if (raw->red>255 || raw->green>255 || raw->blue>255 || raw->features || raw->reserved) return R1_PENDING;
    return R1_OK;
}
