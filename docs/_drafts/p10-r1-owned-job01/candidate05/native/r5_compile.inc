static int r5_allocate_work(r5_job* job)
{
    r3_job* scene=job->base->scene;r1_ledger* ledger=scene->clock.ledger;uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,scene->clips_n,3*sizeof(int64_t))
            || !r3_add_bytes(&bytes,scene->clock.streams_n,512)) return R1_BUDGET;
    if (bytes>ledger->limit-ledger->used) return R1_BUDGET;
    job->order_bytes=scene->clips_n*sizeof(int64_t);job->coverage_bytes=scene->clock.streams_n*512;
    job->active_order=r1_allocate(ledger,job->order_bytes);job->hold_order=r1_allocate(ledger,job->order_bytes);
    job->body_order=r1_allocate(ledger,job->order_bytes);
    job->covered=r1_allocate(ledger,job->coverage_bytes);
    if (!job->active_order || !job->hold_order || !job->body_order || !job->covered) return R1_BUDGET;
    job->phase=2;job->row=0;return R1_OK;
}

static int r5_phase_select(r5_job* job)
{
    r5_clip* clip=job->clips+job->row;const r5_clip_raw* raw=job->borrowed->phases+job->row;double value;int flag,status;
    status=r5_phase_external(job,raw);if (status) return status;
    status=r5_modes(clip,raw);if (status) return status;
    status=r5_directions(clip,raw);if (status) {job->base->scene->pending_features|=R5_PENDING_GEOMETRY;return status;}
    flag=r5_text_equal(&raw->direction_in,"none",1);if (flag<0) return flag;
    int empty=r5_text_equal(&raw->direction_in,"",1);if (empty<0) return empty;
    clip->raw_dir_in_set=raw->direction_in.kind==1 && !empty && !flag;
    if (r5_truth(raw->values[R5_MOTOR_PHASE],0,&flag)) return R1_PENDING;clip->motor_no_delay=!flag;
    if (r5_number(raw->values[R5_PHASE],0.,&value) || !isfinite(value)) return R1_PENDING;
    if (value>1e-6 || raw->pending_feature_atoms) {job->base->scene->pending_features|=R5_PENDING_SOURCE_REMAP;return R1_PENDING;}
    if (r5_truth(raw->values[R5_PRECHARGE],0,&flag)) return R1_PENDING;clip->precharge=flag;
    if (flag) {job->base->scene->pending_features|=R5_PENDING_PRECHARGE_IO;return R1_PENDING;}
    if (raw->clip_universes_n && !raw->clip_universes) return R1_INVALID;
    if (r5_number(raw->clip_universes_n ? raw->clip_universes[0] : (r1_atom){R1_MISSING,0,0},1.,&value)
            || !isfinite(value)) return R1_PENDING;
    value=trunc(value);clip->primary_universe=value>=1. && value<R1_UNIVERSE_LIMIT ? (uint32_t)value : 0;
    clip->primary_slot=UINT32_MAX;clip->compile_stage=0;job->slot=0;job->subphase=1;
    return r5_sequence_prepare(job,clip,raw);
}

static int r5_primary_one(r5_job* job,r5_clip* clip)
{
    r1_job* source=job->base->scene->clips[job->row].source;
    if (job->slot<source->streams_n) {
        uint64_t s=job->slot++;if (source->streams[s].universe==clip->primary_universe) clip->primary_slot=(uint32_t)s;
        return R1_OK;
    }
    int status=r5_sequence_window(job,clip);if (status) return status;
    if ((clip->dir_in && (clip->sequential || clip->delay_in>1e-6))
            || (clip->dir_out && (job->base->scene->clips[job->row].fo>1e-6 || clip->delay_out>1e-6))) {
        job->base->scene->pending_features|=R5_PENDING_GEOMETRY|R5_PENDING_SOURCE_REMAP;return R1_PENDING;
    }
    if (clip->primary_slot==UINT32_MAX || source->streams[clip->primary_slot].count<2) {clip->compile_stage=5;return R1_OK;}
    r1_stream* stream=source->streams+clip->primary_slot;
    clip->sample_step=stream->count/64;if (!clip->sample_step) clip->sample_step=1;
    clip->sample_count=(stream->count-1)/clip->sample_step+1;
    if ((stream->count-1)%clip->sample_step) clip->sample_count++;
    if (clip->sample_count>127) return R1_INVALID;
    clip->sampled_bytes=clip->sample_count*512*sizeof(float);
    clip->sampled=r1_allocate(source->ledger,clip->sampled_bytes);if (!clip->sampled) return R1_BUDGET;
    clip->compile_stage=3;return R1_OK;
}

static int r5_phase_one(r5_job* job)
{
    r3_job* scene=job->base->scene;r5_clip* clip=job->clips+job->row;
    if (!job->subphase) return r5_phase_select(job);
    if (clip->compile_stage==1 && clip->pause_cursor<clip->pause_count)
        return r5_pause_one(clip,job->borrowed->phases+job->row);
    if (clip->compile_stage==1) return r5_primary_one(job,clip);
    if (clip->compile_stage==3) return r5_sample_extrema_one(job,clip);
    if (clip->compile_stage==4) return r5_cyclic_column_one(clip);
    if (clip->compile_stage!=5) return R1_INVALID;
    uint64_t bytes=0;if (!r3_add_bytes(&bytes,scene->clips[job->row].source->streams_n,513)) return R1_BUDGET;
    clip->bytes=bytes;clip->hold_mask=r1_allocate(scene->clock.ledger,bytes);if (!clip->hold_mask) return R1_BUDGET;
    clip->hold_seen=clip->hold_mask+scene->clips[job->row].source->streams_n*512;
    r1_free(scene->clock.ledger,clip->sampled,clip->sampled_bytes);clip->sampled=NULL;
    job->hold_order[job->row]=job->body_order[job->row]=(int64_t)job->row;job->row++;job->subphase=job->slot=0;
    if (job->row==scene->clips_n) {job->phase=3;job->sort_cursor=1;job->sort_walk=0;job->slot=0;}
    return R1_OK;
}

/* Stable hold ordering is incrementally compiled: at most one move per unit. */
static int r5_sort_one(r5_job* job)
{
    r3_job* scene=job->base->scene;
    if (job->sort_cursor==scene->clips_n) {
        if (!job->slot) {job->slot=1;job->sort_cursor=1;job->sort_walk=0;return R1_OK;}
        job->phase=4;job->seed_cursor=job->seed_clip=job->seed_slot=job->scan=0;return R1_OK;
    }
    int64_t* order=job->slot ? job->hold_order : job->body_order;
    if (!job->sort_walk) {job->sort_value=order[job->sort_cursor];job->sort_walk=job->sort_cursor;}
    uint64_t j=job->sort_walk;
    int before=job->slot ? r5_hold_sort_before(scene->clips+job->sort_value,scene->clips+order[j-1])
                        : r5_body_sort_before(scene->clips+job->sort_value,scene->clips+order[j-1]);
    if (j && before) {
        order[j]=order[j-1];job->sort_walk=j-1;
        if (job->sort_walk) return R1_OK;
    }
    order[job->sort_walk]=job->sort_value;job->sort_cursor++;job->sort_walk=0;return R1_OK;
}
