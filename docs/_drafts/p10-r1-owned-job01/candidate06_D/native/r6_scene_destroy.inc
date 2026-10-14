/* Destroy derived geometry before the timeline/source owners. All partial
 * compiler allocations retain their original ledger and allocation pointer. */
static void r6_slot_destroy(r6_slot_plan* slot,r1_ledger* ledger)
{
    r6_sample_cache_destroy(&slot->samples);r6_copy_destroy(&slot->copies);
    r6_traits_destroy(&slot->traits);r6_global_groups_destroy(&slot->in_groups);
    r6_global_groups_destroy(&slot->out_groups);r6_order_destroy(&slot->local_order);
    r6_order_destroy(&slot->source_order);
    r1_free(ledger,slot->local_ids,slot->local_ids_bytes);
    r1_free(ledger,slot->direction_scratch,slot->bytes);
}

static void r6_plan_destroy(r6_clip_plan* plan,uint64_t slots,r1_ledger* ledger)
{
    if (plan->slots) for (uint64_t s=0;s<slots;s++) r6_slot_destroy(plan->slots+s,ledger);
    r6_shapes_destroy(&plan->shapes);r6_virtual_destroy(&plan->virtual_positions);
    r6_coordinates_destroy(&plan->coordinates);r6_base_destroy(&plan->base);
    r1_free(ledger,plan->filtered,plan->filtered_bytes);
    r1_free(ledger,plan->source_slots,plan->source_slots_bytes);
    r1_free(ledger,plan->used,plan->used_bytes);r1_free(ledger,plan->slots,plan->slots_bytes);
}

CE_API void ce_owned_scene_destroy_r6(r6_job* job)
{
    if (!job) return;r5_job* timeline=job->timeline;r3_job* scene=timeline->base->scene;
    r1_ledger* ledger=scene->clock.ledger;
    if (job->geometry.clips) for (uint64_t i=0;i<job->geometry.clips_n;i++) {
        uint64_t slots=scene->clips[i].source ? scene->clips[i].source->streams_n : 0;
        r6_plan_destroy(job->geometry.clips+i,slots,ledger);
    }
    r1_free(ledger,job->geometry.clips,job->geometry.bytes);
    r1_free(ledger,job->grid_state,job->grid_state_bytes);
    r1_free(ledger,job,sizeof(*job));ce_owned_scene_destroy_r5(timeline);
}

CE_API int ce_owned_scene_release_r6(r5_block* block)
{
    /* Full FI/context export adds an owning sidecar before this is qualified.
     * The existing detached main/record/H/T/LT planes retain ledger lifetime. */
    return ce_owned_scene_release_r5(block);
}
