/* Actual R6 row/source/direction/LT/mask/score/mix route. Missing causal
 * anchor is an explicit refusal, including directional-in with zero fade.
 * The stream midpoint below is solely the original mix anchor. */
static int r6_task_row(r6_job* job,uint64_t row,uint64_t index,uint64_t stream)
{
    r5_job* owner=job->timeline;r3_job* scene=owner->base->scene;
    r3_clip* original=scene->clips+index;r1_job* source=original->source;
    r1_stream* rows=source->streams+stream;r5_clip* clip=owner->clips+index;
    r6_slot_plan* plan=job->geometry.clips[index].slots+stream;
    uint8_t* raw=scene->lt_raw+row*512;uint8_t* active=scene->candidate_active+row*512;
    const uint8_t* endpoint=NULL;int remapped=0;
    int status=r6_frame_source(job,index,stream,raw,&endpoint,&remapped);if (status) return status;
    r3_lt_call call={stream,clip->local,raw,raw};r3_lt_apply(scene,original,&call);
    if (!r6_row_active(owner,index,stream,raw,active)) return 1;
    float* gains=scene->gains+row*512;
    status=r6_frame_gains(&job->transition,index,stream,clip->local,clip->hold_extension,gains);
    if (status) return status;
    double windows[24]={0};r6_main_windows(owner,index,windows);
    int directional=!job->geometry.clips[index].scalar_only;
    if (directional ? (!windows[7] && windows[11]) : windows[6]) {
        /* Do not replace the actual _get_fade_in_anchor event with idle or
         * the mix midpoint. FI07 context/getter/block is the remaining owner. */
        scene->pending_features|=R3_PENDING_FADEIN|R6_PENDING_CONTEXT|R6_PENDING_TRANSITION;
        return R1_PENDING;
    }
    if (!rows->anchor_ready) {
        for (int c=0;c<512;c++) rows->anchor[c]=active[c] ? ((float)rows->minimum[c]+(float)rows->maximum[c])/2.f : 0.f;
        rows->anchor_ready=1;
    }
    memcpy(scene->anchors+row*512,rows->anchor,512*sizeof(float));
    ce_prepared_task* task=scene->tasks+row;memset(task,0,sizeof(*task));
    task->raw=raw;task->active=active;task->gains=gains;task->changes=scene->changes+row*512;
    int frame=ce_frame_index(source->times+rows->begin,(int)rows->count,clip->source);
    if (frame<0 || (uint64_t)frame>=rows->count) return R1_KERNEL;
    r1_sparse_lookup(source,rows,(uint64_t)frame,scene->changes+row*512);
    uint32_t u=scene->universe_map[rows->universe]-1;
    task->base=scene->idle+(size_t)u*512;task->idle=task->base;
    if (directional && windows[11]) {
        for (int c=0;c<512;c++) plan->prearrival[c]=active[c] && !plan->arrived[c];
        task->prearrival=plan->prearrival;
    }
    /* Completed endpoint substitution belongs to the causal-in branch above
     * and must be wired after a genuine anchor. No guessed endpoint is used. */
    (void)endpoint;(void)remapped;scene->row_clip[row]=index;scene->row_stream[row]=stream;
    return R1_OK;
}

static int r6_prepare_rows(r6_job* job,uint64_t* output)
{
    r3_job* scene=job->timeline->base->scene;uint64_t row=0;
    for (uint64_t rank=0;rank<scene->active_count;rank++) {
        uint64_t index=(uint64_t)scene->order[rank];const r3_clip* clip=scene->clips+index;
        for (uint64_t s=0;s<clip->source->streams_n;s++) {
            int status=r6_task_row(job,row,index,s);if (status<0) return status;
            if (!status) {r5_task_timing(job->timeline,index,scene->active_count-rank,scene->tasks+row);row++;}
        }
    }
    *output=row;return R1_OK;
}

static int r6_render_frame(r6_job* job,r1_block* block,uint64_t frame)
{
    r5_job* owner=job->timeline;r3_job* scene=owner->base->scene;double position;uint64_t rows;
    int status=r6_source_current(job);if (status) return status;
    if (job->cancelled || scene->clock.cancelled) return R1_CANCELLED;
    status=r6_main_position(job,&position);if (status) return status;
#ifdef R1_TEST_FAIL_CURSOR
    if (scene->clock.cursor==(uint64_t)R1_TEST_FAIL_CURSOR) return R1_KERNEL;
#endif
    status=r5_body_schedule(owner,position);if (!status) status=r5_hold_schedule(owner,position);if (status) return status;
    r5_priority_sort(owner);r3_lt_gap(scene);status=r6_prepare_rows(job,&rows);if (status) return status;
    if (ce_prepared_frame_eval((int)rows,scene->tasks,scene->candidate_float,scene->candidate_data,scene->scores)) return R1_KERNEL;
    r3_csr(scene,rows);
    for (uint64_t g=0;g<scene->clock.streams_n;g++) r5_hold_group(owner,g);
    for (uint64_t g=0;g<2*scene->clock.streams_n;g++) r3_mix_group(scene,(int)g,block,frame);
    for (uint64_t g=0;g<scene->clock.streams_n;g++) r4_consume_group(owner->base,g,block,frame);
    block->positions[frame]=position;scene->clock.last_time=position;scene->clock.cursor++;return R1_OK;
}
