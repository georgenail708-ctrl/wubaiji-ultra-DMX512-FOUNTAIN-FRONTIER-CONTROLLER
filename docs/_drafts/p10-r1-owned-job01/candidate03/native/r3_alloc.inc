static void r3_clip_release(r3_job* job,r3_clip* clip)
{
    if (clip->source && clip->lt_slots) {
        for (uint64_t s=0;s<clip->source->streams_n;s++)
            r1_free(job->clock.ledger,clip->lt_slots[s].fx_rows,job->fixtures_n*91*sizeof(int32_t));
    }
    r1_free(job->clock.ledger,clip->lt_slots,clip->lt_bytes);
    r1_free(job->clock.ledger,clip->consumed,clip->consumed_bytes);
    r1_free(job->clock.ledger,clip->channel_presence,clip->presence_bytes);
    if (clip->source) ce_owned_job_destroy_r1(clip->source);
}

CE_API void ce_owned_scene_destroy_r3(r3_job* job)
{
    r1_ledger* ledger;
    if (!job) return;ledger=job->clock.ledger;
    if (job->clips) for (uint64_t i=0;i<job->clips_n;i++) r3_clip_release(job,job->clips+i);
    if (job->definitions) for (uint64_t i=0;i<job->definitions_n;i++)
        r1_free(ledger,(void*)job->definitions[i].channel_kinds,job->definitions[i].channel_count*sizeof(uint32_t));
    if (job->clock.state) r1_free(ledger,job->clock.state,job->clock.state->allocation_bytes);
    r1_free(ledger,job->clock.streams,job->clock.streams_bytes);
    r1_free(ledger,job->clips,job->clips_bytes);r1_free(ledger,job->tracks,job->tracks_bytes);
    r1_free(ledger,job->fixtures,job->fixtures_bytes);r1_free(ledger,job->definitions,job->definitions_bytes);
    r1_free(ledger,job->defaults,job->defaults_bytes);r1_free(ledger,job->universe_map,job->map_bytes);
    r1_free(ledger,job->idle,job->idle_bytes);r1_free(ledger,job->lt_state,job->lt_bytes);
    r1_free(ledger,job->work,job->work_bytes);r1_free(ledger,job->order,job->order_bytes);
    r1_free(ledger,job,sizeof(*job));r1_ledger_release(ledger);
}

CE_API int ce_owned_scene_release_r3(r3_block* block)
{
    r1_ledger* ledger;
    if (!block || block->magic!=R1_MAGIC) return R1_INVALID;
    ledger=block->ledger;block->magic=0;
    if (block->history) r1_free(ledger,block->history,block->history->allocation_bytes);
    if (block->frames) ce_owned_job_release_block_r1(block->frames);
    r1_free(ledger,block,block->allocation_bytes);r1_ledger_release(ledger);return R1_OK;
}

static void r3_report_current(const r3_job* job,r3_report* report,int status)
{
    memset(report,0,sizeof(*report));report->abi=R3_ABI;report->struct_size=sizeof(*report);report->status=status;
    report->pending_features=job->pending_features;report->pending_state=job->clock.state ? 0 : R1_STATE_PENDING;
    report->source_token=job->clock.source_token;report->compile_clip=job->compile_clip;report->compile_phase=job->compile_phase;
    report->cursor=job->clock.cursor;report->total_frames=job->clock.total_frames;report->streams=job->clock.streams_n;
    report->used_bytes=job->clock.ledger->used;report->peak_bytes=job->clock.ledger->peak+R1_KERNEL_RESERVE;
    report->interval=job->clock.interval;report->end=job->clock.end;report->last_time=job->clock.last_time;
    for (uint64_t i=0;i<job->clips_n;i++) if (job->clips[i].source) report->warning_flags|=job->clips[i].source->warning_flags;
}
