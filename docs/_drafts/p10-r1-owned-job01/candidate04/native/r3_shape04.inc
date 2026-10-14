static int r3_add_bytes(uint64_t* total,uint64_t count,uint64_t width)
{
    uint64_t bytes;
    if (count && width>UINT64_MAX/count) return 0;
    bytes=count*width;
    if (*total>UINT64_MAX-bytes) return 0;
    *total+=bytes;return 1;
}

static int r3_shape(const r3_input* input)
{
    uint64_t budget=sizeof(r3_job)+sizeof(r1_ledger)+R1_KERNEL_RESERVE,external_min=0;
    if (!input || input->abi!=R3_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    if (input->byte_budget>R3_WORKFLOW_MAX_BYTES) return R1_BUDGET;
    if (!r3_add_bytes(&budget,input->external_bytes,1)) return R1_BUDGET;
    if (!input->clip_count || !input->clips || !input->track_count || !input->tracks) return R1_INVALID;
    if (input->clip_count>INT_MAX || input->track_count>INT_MAX) return R1_BUDGET;
    if (input->fixture_count>INT_MAX/4) return R1_BUDGET;
    if ((input->fixture_count && !input->fixtures) || (input->definition_count && !input->definitions)
            || (input->default_count && !input->defaults)) return R1_INVALID;
    if (!r3_add_bytes(&budget,input->clip_count,sizeof(r3_clip))
            || !r3_add_bytes(&budget,input->track_count,sizeof(r3_track_raw))
            || !r3_add_bytes(&budget,input->fixture_count,sizeof(r3_fixture_raw))
            || !r3_add_bytes(&budget,input->definition_count,sizeof(r3_definition_raw))
            || !r3_add_bytes(&budget,input->default_count,sizeof(r3_default_raw))) return R1_BUDGET;
    if (budget>input->byte_budget || input->byte_budget>SIZE_MAX) return R1_BUDGET;
    /* Count arrays are exact typed caller lengths. Each child shape/budget is
     * checked before any raw frame dereference or allocation. */
    for (uint64_t i=0;i<input->clip_count;i++) {
        r1_input shape=input->clips[i].recorded;const r1_input* source=&shape;
        if (!input->clips[i].scatter || shape.frames) return R1_INVALID;
        shape.frames=(const r1_raw_frame*)input->clips[i].scatter; /* shape-only, never cast-read */
        if (r1_input_shape(source)) return R1_INVALID;
        if (!r3_add_bytes(&external_min,source->frame_count,512+sizeof(r3_frame_raw))
                || !r3_add_bytes(&external_min,source->channel_count,sizeof(r1_channel))) return R1_BUDGET;
        if (!r3_add_bytes(&budget,source->frame_count,sizeof(r1_raw_frame)+112+2*sizeof(r1_sort_row))
                || !r3_add_bytes(&budget,source->channel_count,sizeof(r1_channel))
                || !r3_add_bytes(&budget,1,R1_UNIVERSE_LIMIT*sizeof(uint32_t)+sizeof(r1_job))) return R1_BUDGET;
        if (budget>input->byte_budget) return R1_BUDGET;
    }
    if (input->external_bytes<external_min) return R1_BUDGET;
    return R1_OK;
}

static int r3_integer(r1_atom atom,double missing,double* output)
{
    double value;
    if (r1_atom_number(atom,missing,&value)) return R1_PENDING;
    if (value<(double)INT64_MIN || value>=(double)INT64_MAX) return R1_PENDING;
    *output=trunc(value);return R1_OK;
}

static int r3_extra(r3_clip* clip,const r3_clip_raw* raw)
{
    static const double defaults[R3_EXTRA]={0.,0.,0.,128.,.08,.22,255.,0.};
    double values[R3_EXTRA],integers[2];
    LtpRequest request={0};LtpReport result={0};int64_t bytes[2];
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
    /* Cross-track fade-in anchor/content histories are not yet admitted. */
    return clip->fi>0. ? R1_PENDING : R1_OK;
}
