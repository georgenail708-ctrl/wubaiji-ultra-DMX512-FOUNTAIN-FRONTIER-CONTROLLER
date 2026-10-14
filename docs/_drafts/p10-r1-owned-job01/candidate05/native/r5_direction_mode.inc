enum {R5_DIR_NONE,R5_DIR_LTR,R5_DIR_RTL,R5_DIR_FTB,R5_DIR_BTF,R5_DIR_CW,R5_DIR_CCW,R5_DIR_CTR,R5_DIR_RTC};
static int r5_direction_name(const r5_text* raw,uint32_t* result)
{
    static const char* names[]={"none","ltr","rtl","ftb","btf","cw","ccw","ctr","rtc"};
    static const char* aliases[]={"default","left_to_right","front_to_back","front-to-back","front_back","f2b",
        "diag_tl_br","diag_bl_tr","right_to_left","back_to_front","back-to-front","back_front","b2f",
        "diag_br_tl","diag_tr_bl","reverse","clockwise","counterclockwise","anti_clockwise","anticlockwise","center_out","edge_in"};
    static const uint32_t alias_values[]={0,1,3,3,3,3,1,1,2,4,4,4,4,2,2,2,5,6,6,6,7,8};
    if (raw->reserved || raw->kind>2 || (raw->bytes && !raw->utf8)) return R1_PENDING;
    if (raw->kind==0 || raw->kind==2 || !raw->bytes) {*result=0;return R1_OK;}
    for (int i=0;i<9;i++) {int same=r5_text_equal(raw,names[i],1);if (same<0) return same;if (same) {*result=(uint32_t)i;return R1_OK;}}
    for (int i=0;i<22;i++) {int same=r5_text_equal(raw,aliases[i],1);if (same<0) return same;if (same) {*result=alias_values[i];return R1_OK;}}
    return R1_PENDING; /* original unknown-direction geometry selector remains explicit */
}

static int r5_raw_direction_set(const r5_text* raw)
{
    if (raw->reserved || raw->kind>2 || raw->bytes>256 || (raw->bytes && !raw->utf8)) return R1_PENDING;
    if (raw->kind!=1 || !raw->bytes) return 0;
    /* _get_clip_fade_* tests the unnormalized string against exact 'none'
     * before normalizing; ' NONE ' must not fall through to legacy. */
    return raw->bytes!=4 || memcmp(raw->utf8,"none",4)!=0;
}

static int r5_directions(r5_clip* clip,const r5_clip_raw* raw)
{
    uint32_t legacy;int status=r5_direction_name(&raw->direction_legacy,&legacy);if (status) return status;
    int in_set=r5_raw_direction_set(&raw->direction_in),out_set=r5_raw_direction_set(&raw->direction_out);
    if (in_set<0 || out_set<0) return R1_PENDING;
    status=r5_direction_name(in_set ? &raw->direction_in : &raw->direction_legacy,&clip->dir_in);
    if (status) return status;
    if (out_set) return r5_direction_name(&raw->direction_out,&clip->dir_out);
    clip->dir_out=legacy ? legacy : clip->dir_in;return R1_OK;
}

/* Identical existing CE direction evaluator, but its two K-float vectors are
 * supplied from the owning ledger. No unaccounted per-frame kernel malloc. */
static int r5_direction_kernel(const ce_source_direction* source,float* scratch,
                                float* gains,uint8_t* completed,uint8_t* arrived)
{
    if (!gains || !completed || !arrived || !_ce_motion_direction_valid(source) || (source->K && !scratch)) return R1_INVALID;
    int cb=_ce_motion_completed(source),scalar=!source->K || !source->mode || (!source->is_in && !source->is_out);
    float global=(float)_ce_motion_global(source,0,0);double bounds[3]={1.,0.,0.};
    if (!scalar && source->mode==2 && source->is_in) _ce_motion_rank_bounds(source,bounds);
    for (int i=0;i<source->K;i++) {
        scratch[i]=scalar ? global : source->ratio_nondefault ? 1.f :
            (source->mode==2 || source->mode==3) ? _ce_motion_sequential(source,i,0,bounds) : _ce_motion_parallel(source,i,0);
        scratch[source->K+i]=scratch[i];
        if (!scalar && !source->ratio_nondefault && source->motor_no_delay) scratch[source->K+i]=
            (source->mode==2 || source->mode==3) ? _ce_motion_sequential(source,i,1,bounds) : _ce_motion_parallel(source,i,1);
    }
    for (int c=0;c<512;c++) {
        int i=scalar ? -1 : source->span_idx[c];
        gains[c]=scalar ? global : i<0 ? 1.f : scratch[i+(source->motor && source->motor[c] && source->motor_no_delay ? source->K : 0)];
        completed[c]=cb>=0 && source->gi_in && source->span_idx && source->span_idx[c]>=0
            && (int)source->gi_in[source->span_idx[c]]%source->batch_count<=cb;
        arrived[c]=gains[c]>1e-6f;
    }
    return R1_OK;
}
