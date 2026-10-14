/* Existing ce_domain_ownership_compact equations over CSR views. Its public
 * facade mallocs temporary group arrays; this view form uses no allocation.
 * Every emitted row has a distinct original tuple identity (R4 alias gate).
 * Held rows omitted for zero active channels are NOT invented registry entries. */
static int r5_peer(r5_job* owner,int first,int count,int row)
{
    r3_job* scene=owner->base->scene;r3_clip* held=scene->clips+scene->row_clip[row];
    for (int i=0;i<count;i++) {
        int other=scene->flat[first+i];
        if (other!=row && scene->clips[scene->row_clip[other]].chain_token==held->chain_token) return 1;
    }
    return 0;
}

static int r5_winning_peer(r5_job* owner,int first,int count,int row,int channel,double best)
{
    r3_job* scene=owner->base->scene;r3_clip* held=scene->clips+scene->row_clip[row];
    for (int i=0;i<count;i++) {
        int other=scene->flat[first+i];size_t offset=(size_t)other*512+channel;
        if (other==row || scene->clips[scene->row_clip[other]].chain_token!=held->chain_token) continue;
        if (scene->candidate_active[offset] && isfinite(scene->scores[offset]) && scene->scores[offset]>=best-1e-12) return 1;
    }
    return 0;
}

static double r5_best(r3_job* scene,int first,int count,int channel)
{
    double best=-INFINITY;
    for (int i=0;i<count;i++) {
        size_t offset=(size_t)scene->flat[first+i]*512+channel;
        if (scene->candidate_active[offset] && isfinite(scene->scores[offset]) && scene->scores[offset]>best) best=scene->scores[offset];
    }
    return best;
}

static void r5_hold_group(r5_job* owner,uint64_t group)
{
    r3_job* scene=owner->base->scene;int first=scene->offsets[group],count=scene->offsets[group+1]-first;
    for (int i=0;i<count;i++) {
        int row=scene->flat[first+i];uint64_t index=scene->row_clip[row],slot=scene->row_stream[row];
        r5_clip* clip=owner->clips+index;if (!clip->real_hold) continue;
        if (!r5_peer(owner,first,count,row)) {
            /* _release_hold_consumed pops the universe and retains its outer
             * key even when the remaining original dictionary is empty. */
            clip->hold_seen[slot]=0;memset(clip->hold_mask+slot*512,0,512);continue;
        }
        for (int c=0;c<512;c++) {
            size_t offset=(size_t)row*512+c;double best=r5_best(scene,first,count,c);
            int effective=scene->candidate_active[offset] && isfinite(scene->scores[offset]) && scene->scores[offset]>=best-1e-12;
            if (scene->candidate_active[offset] && !effective && r5_winning_peer(owner,first,count,row,c,best)) {
                clip->hold_mask[slot*512+c]=1;clip->hold_seen[slot]=1;clip->hold_outer_present=1;
            }
        }
    }
}

static int r5_render_frame(r5_job* owner,r1_block* block,uint64_t frame)
{
    r3_job* scene=owner->base->scene;double position=r1_position(&scene->clock,scene->clock.cursor);uint64_t rows;int status;
#ifdef R1_TEST_FAIL_CURSOR
    if (scene->clock.cursor==(uint64_t)R1_TEST_FAIL_CURSOR) return R1_KERNEL;
#endif
    status=r5_body_schedule(owner,position);if (!status) status=r5_hold_schedule(owner,position);if (status) return status;
    r5_priority_sort(owner);r3_lt_gap(scene);status=r5_prepare_rows(owner,&rows);if (status) return status;
    if (ce_prepared_frame_eval((int)rows,scene->tasks,scene->candidate_float,scene->candidate_data,scene->scores)) return R1_KERNEL;
    r3_csr(scene,rows);
    for (uint64_t g=0;g<scene->clock.streams_n;g++) r5_hold_group(owner,g);
    for (uint64_t g=0;g<2*scene->clock.streams_n;g++) r3_mix_group(scene,(int)g,block,frame);
    for (uint64_t g=0;g<scene->clock.streams_n;g++) r4_consume_group(owner->base,g,block,frame);
    block->positions[frame]=position;scene->clock.last_time=position;scene->clock.cursor++;return R1_OK;
}
