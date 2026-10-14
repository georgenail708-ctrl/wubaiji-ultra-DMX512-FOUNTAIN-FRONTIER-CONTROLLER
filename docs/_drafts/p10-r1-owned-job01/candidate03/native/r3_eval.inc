static void r3_lt_apply(r3_job* job,const r3_clip* clip,const r3_lt_call* call)
{
    if (!clip->lt || !job->fixtures_n) return;
    const _lt_scope* scope=&clip->lt_slots[call->slot].scope;
    job->lt_used=1;
    _lt_apply_core(scope,call->input,call->output,call->local,NULL,NULL,NULL,NULL,NULL,
        job->lt_state,job->lt_time,job->lt_exists,job->lt_color,job->lt_color_valid,
        job->lt_touched,job->lt_touched_color,(int)job->fixtures_n*4,(int)job->fixtures_n);
}

static int r3_task_row(r3_job* job,uint64_t row,uint64_t clip_index,uint64_t slot,double position)
{
    r3_clip* clip=job->clips+clip_index;r1_job* source=clip->source;r1_stream* stream=source->streams+slot;
    ce_prepared_task* task=job->tasks+row;
    double time=r1_source_time(source,position),local=position-source->start;
    int index=ce_frame_index(source->times+stream->begin,(int)stream->count,time),count=0;
    uint8_t* raw=job->lt_raw+row*512;uint8_t* active=job->candidate_active+row*512;
    float* gain=job->gains+row*512;float* anchor=job->anchors+row*512;
    const uint8_t* original=r1_source_row(source,stream->begin+(uint64_t)index);
    r3_lt_call call={slot,local,original,raw};
    memcpy(raw,original,512);r3_lt_apply(job,clip,&call);
    r1_active(source,stream,time,raw,active);
    if (clip->lt) for (int c=0;c<512;c++) active[c]|=clip->lt_slots[slot].light[c];
    ce_domain_mask(3,512,active,clip->consumed+slot*512,active,&count);
    if (!count) return 1;
    if (!stream->anchor_ready) {
        for (int c=0;c<512;c++) stream->anchor[c]=active[c] ? ((float)stream->minimum[c]+(float)stream->maximum[c])/2.f : 0.f;
        stream->anchor_ready=1;
    }
    memcpy(anchor,stream->anchor,512*sizeof(float));memset(task,0,sizeof(*task));
    task->raw=raw;task->active=active;task->gains=gain;task->changes=job->changes+row*512;
    r1_sparse_lookup(source,stream,(uint64_t)index,job->changes+row*512);
    double value=ce_source_motion_fade(source->duration,0.,clip->fo,local,clip->enabled,
        clip->curve_in,clip->curve_out,clip->strategy_in,clip->strategy_out);
    for (int c=0;c<512;c++) gain[c]=(float)value;
    r3_fade_task(job,clip,local,task);
    uint32_t uni_slot=job->universe_map[stream->universe]-1;
    task->base=job->idle+(size_t)uni_slot*512;task->idle=task->base;
    job->row_clip[row]=clip_index;job->row_stream[row]=slot;return R1_OK;
}

static uint64_t r3_prepare_rows(r3_job* job,double position)
{
    uint64_t row=0;
    for (uint64_t rank=0;rank<job->active_count;rank++) {
        uint64_t clip_index=(uint64_t)job->order[rank];r3_clip* clip=job->clips+clip_index;
        for (uint64_t s=0;s<clip->source->streams_n;s++) {
            if (!r3_task_row(job,row,clip_index,s,position)) {
                r3_score_task(job,clip,job->active_count-rank,job->tasks+row);row++;
            }
        }
    }
    return row;
}

static int r3_csr(r3_job* job,uint64_t rows)
{
    uint64_t flat=0,u=job->clock.streams_n;
    job->offsets[0]=0;
    for (uint64_t group=0;group<2*u;group++) {
        uint32_t universe=job->clock.streams[group%u].universe;
        for (uint64_t row=0;row<rows;row++) {
            r3_clip* clip=job->clips+job->row_clip[row];
            if (clip->source->streams[job->row_stream[row]].universe!=universe) continue;
            if (group>=u && !clip->source->record) continue;
            job->flat[flat++]=(int)row;
        }
        job->offsets[group+1]=(int)flat;
    }
    return (int)flat;
}

static void r3_mix_group(r3_job* job,int group,r1_block* block,uint64_t frame)
{
    int first=job->offsets[group],n=job->offsets[group+1]-first;
    uint64_t u=job->clock.streams_n,slot=(uint64_t)group%u,row=frame*u+slot;
    uint8_t* data=group<(int)u ? block->main_data+row*512 : block->record_data+row*512;
    uint8_t* active=group<(int)u ? block->main_active+row*512 : block->record_active+row*512;
    if (!n) return;
#ifdef CE_AVX2
    ce_resolve_many_group_avx2(job->flat+first,n,job->candidate_data,job->candidate_active,job->scores,
        NULL,NULL,job->effective+(size_t)first*512,job->data_eff+(size_t)first*512,job->counts+first);
#else
    ce_resolve_many_group_scalar(job->flat+first,n,job->candidate_data,job->candidate_active,job->scores,
        NULL,NULL,job->effective+(size_t)first*512,job->data_eff+(size_t)first*512,job->counts+first);
#endif
    _ce_prepared_winner(n,job->effective+(size_t)first*512,job->winner+(size_t)group*512);
    _ce_prepared_mix(n,job->flat+first,job->anchors,job->effective+(size_t)first*512,
        job->data_eff+(size_t)first*512,job->counts+first,(float)job->modulation_gain,job->mix_scratch,data,active);
    int any=0;for (int c=0;c<512;c++) any|=active[c];
    if (group<(int)u) block->main_present[row]=(uint8_t)(any!=0);else block->record_present[row]=(uint8_t)(any!=0);
}

static void r3_consume_group(r3_job* job,uint64_t group,r1_block* block,uint64_t frame)
{
    int first=job->offsets[group],n=job->offsets[group+1]-first;
    const int* winners=job->winner+group*512;
    for (int i=0;i<n;i++) {
        int row=job->flat[first+i];r3_clip* loser=job->clips+job->row_clip[row];
        uint64_t stream=job->row_stream[row];
        for (int c=0;c<512;c++) {
            int j=winners[c];
            if (j>=0) block->scores[(frame*job->clock.streams_n+group)*512+c]=job->scores[(size_t)job->flat[first+j]*512+c];
            if (j<0 || j==i || !job->candidate_active[(size_t)row*512+c]) continue;
            int winner=job->flat[first+j];
            if (job->candidate_data[(size_t)winner*512+c]!=job->candidate_data[(size_t)row*512+c]) {
                loser->consumed[stream*512+c]=1;loser->consumed_seen[stream]=1;
            }
        }
    }
}

static int r3_render_frame(r3_job* job,r1_block* block,uint64_t frame)
{
    double position=r1_position(&job->clock,job->clock.cursor);
#ifdef R1_TEST_FAIL_CURSOR
    if (job->clock.cursor==(uint64_t)R1_TEST_FAIL_CURSOR) return R1_KERNEL;
#endif
    r3_frame_order(job,position);r3_lt_gap(job);
    uint64_t rows=r3_prepare_rows(job,position);int groups=(int)job->clock.streams_n*2;
    if (ce_prepared_frame_eval((int)rows,job->tasks,job->candidate_float,job->candidate_data,job->scores)) return R1_KERNEL;
    r3_csr(job,rows);
    for (int g=0;g<groups;g++) r3_mix_group(job,g,block,frame);
    for (uint64_t g=0;g<job->clock.streams_n;g++) r3_consume_group(job,g,block,frame);
    block->positions[frame]=position;job->clock.last_time=position;job->clock.cursor++;
    return R1_OK;
}
