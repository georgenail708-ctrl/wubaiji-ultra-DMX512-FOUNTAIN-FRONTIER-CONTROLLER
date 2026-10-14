/* Declared lower bound precedes any new raw phase / seed / geometry read. */
static int r5_shape(const r5_input* input)
{
    uint64_t bytes=0,external=0;int status;
    if (!input || input->abi!=R5_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    if (!input->phases || input->source_epoch_mode>2 || input->nm_present>1) return R1_INVALID;
    if ((input->hold_n && !input->hold) || (input->latch_n && !input->latch)
            || (input->geometry_n && !input->geometry)) return R1_INVALID;
    status=r4_shape(&input->raw);if (status) return status;
    if (!r4_external_metadata(&input->raw,&external)
            || !r3_add_bytes(&external,input->raw.raw.clip_count,sizeof(r5_clip_raw))
            || !r3_add_bytes(&external,input->geometry_n,sizeof(r5_fixture_geometry_raw))
            || !r3_add_bytes(&external,input->hold_n,sizeof(r5_hold_state))
            || !r3_add_bytes(&external,input->latch_n,sizeof(r5_latch_state))) return R1_BUDGET;
    if (!r3_add_bytes(&bytes,external,1) || !r3_add_bytes(&bytes,1,sizeof(r5_job))
            || !r3_add_bytes(&bytes,input->raw.raw.clip_count,sizeof(r5_clip)+3*sizeof(int64_t))) return R1_BUDGET;
    if (bytes>input->raw.raw.byte_budget || external>input->raw.raw.external_bytes) return R1_BUDGET;
    /* Mode2 is precisely owned_engine(seed)->seek(0), whose old C state port
     * clears hold/latch. Mode1 supplies a complete typed overlay. Mode0 unknown
     * is not silently treated as an empty dictionary. */
    if (!input->source_epoch_mode) return R1_PENDING;
    if (input->nm_present!=input->raw.raw.nm_present) return R1_PENDING;
    if (input->source_epoch_mode==1 && (!input->raw.raw.state || input->raw.raw.state->origin_mode)) return R1_PENDING;
    if (input->source_epoch_mode==2 && (!input->raw.raw.state
            || input->raw.raw.state->origin_mode!=1 || input->hold_n || input->latch_n)) return R1_PENDING;
    return R1_OK;
}

static int r5_phase_external(r5_job* job,const r5_clip_raw* raw)
{
    const r5_text* texts[]={&raw->hold,&raw->run,&raw->sequence,&raw->direction_in,
        &raw->direction_out,&raw->direction_legacy,&raw->spatial_scope};
    uint64_t bytes=job->external_min;
    if (!r3_add_bytes(&bytes,raw->pauses_n,sizeof(r1_atom))
            || !r3_add_bytes(&bytes,raw->clip_universes_n,sizeof(r1_atom))) return R1_BUDGET;
    for (int i=0;i<7;i++) if (!r3_add_bytes(&bytes,texts[i]->bytes,1)) return R1_BUDGET;
    if (bytes>job->base->scene->external_bytes) return R1_BUDGET;
    job->external_min=bytes;return R1_OK;
}
