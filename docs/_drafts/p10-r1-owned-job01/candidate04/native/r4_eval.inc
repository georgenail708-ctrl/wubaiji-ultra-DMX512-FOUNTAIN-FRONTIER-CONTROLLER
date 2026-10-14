static int r4_task_row(r4_job* owner,uint64_t row,uint64_t clip_index,uint64_t slot,double position)
{
    r3_job* job=owner->scene;r3_clip* clip=job->clips+clip_index;
    r1_job* source=clip->source;r1_stream* stream=source->streams+slot;
    r4_clip* extra=owner->clips+clip_index;r4_slot* mask=extra->slots+slot;
    ce_prepared_task* task=job->tasks+row;double time=r1_source_time(source,position),local=position-source->start;
    int index=ce_frame_index(source->times+stream->begin,(int)stream->count,time),count=0;
    uint8_t *raw=job->lt_raw+row*512,*active=job->candidate_active+row*512;
    float *gain=job->gains+row*512,*anchor=job->anchors+row*512;
    const uint8_t* original=r1_source_row(source,stream->begin+(uint64_t)index);
    r3_lt_call call={slot,local,original,raw};r4_mask_selection selected;
    memcpy(raw,original,512);r3_lt_apply(job,clip,&call);
    r4_cached_select(&extra->events,mask->event_domain,time,&selected);
    if (selected.kind==R4_MASK_MISSING) r1_active(source,stream,time,raw,active);
    else r4_recorded_active(&selected,stream->base,active);
    if (clip->lt) for (int c=0;c<512;c++) active[c]|=clip->lt_slots[slot].light[c];
    r4_rule_active(&selected,mask->rule.present ? mask->rule.mask : NULL,active);
    ce_domain_mask(3,512,active,clip->consumed+slot*512,active,&count);if (!count) return 1;
    if (!stream->anchor_ready) {
        for (int c=0;c<512;c++) stream->anchor[c]=active[c] ? ((float)stream->minimum[c]+(float)stream->maximum[c])/2.f : 0.f;
        stream->anchor_ready=1;
    }
    memcpy(anchor,stream->anchor,512*sizeof(float));memset(task,0,sizeof(*task));
    task->raw=raw;task->active=active;task->gains=gain;task->changes=job->changes+row*512;
    r1_sparse_lookup(source,stream,(uint64_t)index,job->changes+row*512);
    double value=ce_source_motion_fade(source->duration,0.,clip->fo,local,clip->enabled,
        clip->curve_in,clip->curve_out,clip->strategy_in,clip->strategy_out);
    for (int c=0;c<512;c++) gain[c]=(float)value;r3_fade_task(job,clip,local,task);
    uint32_t uni_slot=job->universe_map[stream->universe]-1;task->base=job->idle+(size_t)uni_slot*512;task->idle=task->base;
    job->row_clip[row]=clip_index;job->row_stream[row]=slot;return R1_OK;
}

static uint64_t r4_prepare_rows(r4_job* owner,double position)
{
    r3_job* job=owner->scene;uint64_t row=0;
    for (uint64_t rank=0;rank<job->active_count;rank++) {
        uint64_t clip_index=(uint64_t)job->order[rank];r3_clip* clip=job->clips+clip_index;
        for (uint64_t s=0;s<clip->source->streams_n;s++) if (!r4_task_row(owner,row,clip_index,s,position)) {
            r3_score_task(job,clip,job->active_count-rank,job->tasks+row);row++;
        }
    }
    return row;
}

static void r4_consume_group(r4_job* owner,uint64_t group,r1_block* block,uint64_t frame)
{
    r3_job* job=owner->scene;int first=job->offsets[group],n=job->offsets[group+1]-first;
    const int* winners=job->winner+group*512;
    for (int i=0;i<n;i++) {
        int row=job->flat[first+i];r3_clip* loser=job->clips+job->row_clip[row];uint64_t stream=job->row_stream[row];
        for (int c=0;c<512;c++) {
            int j=winners[c];
            if (j>=0) block->scores[(frame*job->clock.streams_n+group)*512+c]=job->scores[(size_t)job->flat[first+j]*512+c];
            if (j<0 || j==i || !job->candidate_active[(size_t)row*512+c]) continue;
            int winner=job->flat[first+j];r3_clip* winner_clip=job->clips+job->row_clip[winner];
            /* For ordinary nonfolder clips, folder-chain tuple equality iff
             * immediate raw parent id agrees; its final tuple element is the
             * parent id and the unique-id parent map is deterministic. */
            if (loser->chain_token!=winner_clip->chain_token) continue;
            if (job->candidate_data[(size_t)winner*512+c]!=job->candidate_data[(size_t)row*512+c]) {
                loser->consumed[stream*512+c]=1;loser->consumed_seen[stream]=1;
            }
        }
    }
}

static int r4_render_frame(r4_job* owner,r1_block* block,uint64_t frame)
{
    r3_job* job=owner->scene;double position=r1_position(&job->clock,job->clock.cursor);
#ifdef R1_TEST_FAIL_CURSOR
    if (job->clock.cursor==(uint64_t)R1_TEST_FAIL_CURSOR) return R1_KERNEL;
#endif
    r3_frame_order(job,position);r3_lt_gap(job);uint64_t rows=r4_prepare_rows(owner,position);
    if (ce_prepared_frame_eval((int)rows,job->tasks,job->candidate_float,job->candidate_data,job->scores)) return R1_KERNEL;
    r3_csr(job,rows);for (uint64_t g=0;g<2*job->clock.streams_n;g++) r3_mix_group(job,(int)g,block,frame);
    for (uint64_t g=0;g<job->clock.streams_n;g++) r4_consume_group(owner,g,block,frame);
    block->positions[frame]=position;job->clock.last_time=position;job->clock.cursor++;return R1_OK;
}
