/* Exact R5 time/mode/pause/sample compile reuse. New R6 geometry admission is
 * deferred to the owning scene-plan compiler, rather than the old refusal. */
static int r6_primary_one(r5_job* job,r5_clip* clip)
{
    r1_job* source=job->base->scene->clips[job->row].source;
    if (job->slot<source->streams_n) {
        uint64_t slot=job->slot++;if (source->streams[slot].universe==clip->primary_universe) clip->primary_slot=(uint32_t)slot;
        return R1_OK;
    }
    int status=r5_sequence_window(job,clip);if (status) return status;
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

static int r6_phase_one(r5_job* job)
{
    r5_clip* clip=job->clips+job->row;
    if (job->subphase && clip->compile_stage==1 && clip->pause_cursor==clip->pause_count) return r6_primary_one(job,clip);
    return r5_phase_one(job);
}

static int r6_timeline_compile_one(r6_job* job)
{
    r5_job* timeline=job->timeline;
    if (timeline->phase==0) {
        int status=r6_base_compile_one(job);if (status) return status;
        return timeline->base->phase==9 ? r5_after_base(timeline) : R1_OK;
    }
    if (timeline->phase==2) return r6_phase_one(timeline);
    return r5_compile_one(timeline);
}
