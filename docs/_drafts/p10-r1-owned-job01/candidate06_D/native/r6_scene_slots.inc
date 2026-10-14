/* Slot compile stages are all internal to one owning scene job. Neither spans,
 * motor masks, fixture phases nor source-copy rows are host-produced arrays. */
static int r6_slot_begin(r6_job* job,uint64_t clip,uint64_t slot_index)
{
    r6_slot_plan* slot=job->geometry.clips[clip].slots+slot_index;
    r3_job* scene=job->timeline->base->scene;uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,slot->local_order.n,2*sizeof(float))) return R1_BUDGET;
    slot->bytes=bytes;
    if (bytes) {slot->direction_scratch=r1_allocate(scene->clock.ledger,bytes);if (!slot->direction_scratch) return R1_BUDGET;}
    slot->row=slot->channel=0;slot->phase=4;
    for (int c=0;c<512;c++) slot->span_index[c]=-1;
    return r6_motor_create(&slot->motor,&job->borrowed->raw.raw,scene->clips[clip].source->streams[slot_index].universe);
}

static int r6_slot_span_one(r6_slot_plan* slot)
{
    if (slot->row==slot->local_order.n) {slot->phase=5;slot->row=slot->channel=0;return R1_OK;}
    const r6_order_row* row=slot->local_order.rows+slot->row;
    if (!(row->start>=0 && row->start<row->end && row->end<=512)) {slot->row++;slot->channel=0;return R1_OK;}
    if (!slot->channel) slot->channel=(uint64_t)row->start;
    slot->span_index[slot->channel++]=(int)slot->row;
    if (slot->channel==(uint64_t)row->end) {slot->row++;slot->channel=0;}
    return R1_OK;
}

static int r6_slot_motor_one(r6_job* job,uint64_t clip,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+clip;r6_slot_plan* slot=plan->slots+index;
    int status=r6_motor_step(&slot->motor,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    r1_job* source=job->timeline->base->scene->clips[clip].source;
    plan->source_slots[index]=(r6_source_span_slot){&slot->source_order,slot->motor.mask,source->streams[index].universe,index};
    r6_traits_call call={source,source->streams+index,&slot->local_order};
    status=r6_traits_create(&slot->traits,source->ledger,&call);if (!status) slot->phase=6;return status;
}

static int r6_slot_traits_one(r6_slot_plan* slot)
{
    int status=r6_traits_step(&slot->traits,1);if (status<0) return status;
    if (status==R1_OK) slot->phase=7;return R1_OK;
}

static int r6_slot_group_create(r6_job* job,uint64_t clip,uint64_t index,int out)
{
    r6_clip_plan* plan=job->geometry.clips+clip;r6_slot_plan* slot=plan->slots+index;
    const r6_clip_geometry_raw* raw=job->borrowed->geometry+clip;const r5_clip* phase=job->timeline->clips+clip;
    uint32_t direction=out ? phase->dir_out : phase->dir_in;if (!direction) direction=R5_DIR_LTR;
    const double* xy=direction==R5_DIR_CW || direction==R5_DIR_CCW ? plan->coordinates.xy : plan->virtual_positions.xy;
    r6_global_group_call call={plan->base.ids,slot->local_ids,xy,plan->base.n,slot->local_order.n,
        job->borrowed->raw.phases[clip].values[R5_GROUPS],direction,raw->overrides_n,
        &plan->shapes,!plan->coordinates.complete_snapshot};
    int status=r6_global_groups_create(out ? &slot->out_groups : &slot->in_groups,
        job->timeline->base->scene->clock.ledger,&call);
    if (!status) slot->phase=out ? 10 : 8;return status;
}

static int r6_slot_group_one(r6_slot_plan* slot,int out)
{
    int status=r6_global_groups_step(out ? &slot->out_groups : &slot->in_groups,1);
    if (status<0) return status;
    if (status==R1_OK) {if (out) slot->out_ready=1;else slot->in_ready=1;slot->phase=out ? 11 : 9;}
    return R1_OK;
}

static int r6_slot_compile_one(r6_job* job,uint64_t clip,uint64_t index)
{
    r6_slot_plan* slot=job->geometry.clips[clip].slots+index;
    switch(slot->phase) {
    case 3:return r6_slot_begin(job,clip,index);case 4:return r6_slot_span_one(slot);
    case 5:return r6_slot_motor_one(job,clip,index);case 6:return r6_slot_traits_one(slot);
    case 7:return r6_slot_group_create(job,clip,index,0);case 8:return r6_slot_group_one(slot,0);
    case 9:return r6_slot_group_create(job,clip,index,1);case 10:return r6_slot_group_one(slot,1);
    case 11:return R1_OK;default:return R1_INVALID;
    }
}

static int r6_plan_slots_one(r6_job* job,uint64_t clip)
{
    r6_clip_plan* plan=job->geometry.clips+clip;r1_job* source=job->timeline->base->scene->clips[clip].source;
    if (plan->slot==source->streams_n) {plan->phase=10;plan->slot=0;return R1_OK;}
    int status=r6_slot_compile_one(job,clip,plan->slot);
    if (!status && plan->slots[plan->slot].phase==11) plan->slot++;return status;
}

static int r6_plan_copy_one(r6_job* job,uint64_t clip)
{
    r6_clip_plan* plan=job->geometry.clips+clip;r1_job* source=job->timeline->base->scene->clips[clip].source;
    if (plan->slot==source->streams_n) {plan->phase=11;plan->directions_ready=1;return R1_OK;}
    r6_slot_plan* slot=plan->slots+plan->slot;
    if (!slot->copies.ledger) {
        r6_copy_call call={&slot->local_order,&slot->in_groups,&slot->traits,plan->source_slots,
            source->streams_n,slot->motor.mask,!job->timeline->clips[clip].sequential};
        return r6_copy_create(&slot->copies,source->ledger,&call);
    }
    int status=r6_copy_step(&slot->copies,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    status=r6_sample_cache_create(&slot->samples,source->ledger,slot->copies.count);
    if (!status) {slot->copy_ready=1;plan->slot++;}return status;
}

static int r6_scene_plan_one(r6_job* job,uint64_t clip)
{
    uint64_t phase=job->geometry.clips[clip].phase;
    if (phase==8) return r6_plan_slots_one(job,clip);
    if (phase==10) return r6_plan_copy_one(job,clip);
    if (phase==11) return R1_OK;return r6_plan_initial_one(job,clip);
}
