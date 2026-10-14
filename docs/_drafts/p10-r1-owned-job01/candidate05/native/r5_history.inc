static uint64_t r5_history_bytes(const r3_job* scene)
{
    uint64_t bytes=sizeof(r5_history);
    if (!r3_add_bytes(&bytes,scene->slots_n,sizeof(r5_hold_state))
            || !r3_add_bytes(&bytes,scene->clips_n,sizeof(r5_latch_state))) return 0;
    return bytes;
}

static void r5_history_refresh(r5_job* owner,r5_history* history)
{
    r3_job* scene=owner->base->scene;uint64_t row=0;
    for (uint64_t i=0;i<scene->clips_n;i++) {
        r1_job* source=scene->clips[i].source;r5_clip* clip=owner->clips+i;
        r5_latch_state* latch=history->latch+i;latch->source_token=source->source_token;
        latch->track_order=(uint64_t)source->track_order;latch->present=clip->hold_latch_present;latch->value=clip->hold_latch;
        for (uint64_t s=0;s<source->streams_n;s++) {
            r5_hold_state* held=history->hold+row++;held->source_token=source->source_token;
            held->track_order=(uint64_t)source->track_order;held->universe=source->streams[s].universe;
            held->mask_present=clip->hold_seen[s];held->outer_present=clip->hold_outer_present;
            memcpy(held->mask,clip->hold_mask+s*512,512);
        }
    }
}

static r5_history* r5_history_create(r5_job* owner,uint64_t bytes)
{
    r3_job* scene=owner->base->scene;r5_history* history=r1_allocate(scene->clock.ledger,bytes);
    if (!history) return NULL;
    history->abi=R5_ABI;history->struct_size=sizeof(*history);history->bytes=bytes;
    history->clip_count=scene->clips_n;history->slots_count=scene->slots_n;
    history->hold=(r5_hold_state*)(history+1);history->latch=(r5_latch_state*)(history->hold+scene->slots_n);
    return history;
}

static r5_block* r5_block_create(r5_job* owner,const r1_step* request)
{
    r3_job* scene=owner->base->scene;uint64_t history_bytes=r5_history_bytes(scene),overhead=sizeof(r5_block);
    if (!history_bytes || !r3_add_bytes(&overhead,history_bytes,1) || request->max_block_bytes<=overhead) return NULL;
    r5_block* block=r1_allocate(scene->clock.ledger,sizeof(*block));if (!block) return NULL;
    block->magic=R1_MAGIC;block->bytes=sizeof(*block);block->ledger=scene->clock.ledger;block->ledger->refs++;
    r1_step bounded=*request;bounded.max_block_bytes-=overhead;
    block->history=r5_history_create(owner,history_bytes);if (block->history) block->frames=r3_new_block(scene,&bounded);
    if (!block->history || !block->frames) {ce_owned_scene_release_r5(block);return NULL;}
    return block;
}
