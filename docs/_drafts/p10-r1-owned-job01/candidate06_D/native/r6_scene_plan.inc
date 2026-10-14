/* One owning native compiled plan per raw clip. Every point, membership,
 * span, group, variation gate and copy map is computed in these C stages. */
static int r6_plan_allocate(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;r3_job* scene=job->timeline->base->scene;
    const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;r1_ledger* ledger=scene->clock.ledger;
    uint64_t n=scene->clips[index].source->streams_n,bytes=0;int status=r6_plan_admit(job,index);
    if (status) return status;
    if (!r3_add_bytes(&bytes,n,sizeof(r6_slot_plan))) return R1_BUDGET;
    plan->slots_bytes=bytes;if (n) {plan->slots=r1_allocate(ledger,bytes);if (!plan->slots) return R1_BUDGET;}
    bytes=0;if (!r3_add_bytes(&bytes,n,sizeof(r6_used_slot))) return R1_BUDGET;
    plan->used_bytes=bytes;if (n) {plan->used=r1_allocate(ledger,bytes);if (!plan->used) return R1_BUDGET;}
    bytes=0;if (!r3_add_bytes(&bytes,n,sizeof(r6_source_span_slot))) return R1_BUDGET;
    plan->source_slots_bytes=bytes;if (n) {plan->source_slots=r1_allocate(ledger,bytes);if (!plan->source_slots) return R1_BUDGET;}
    bytes=0;if (!r3_add_bytes(&bytes,raw->coordinates.fixtures_n,sizeof(r6_fixture_raw))) return R1_BUDGET;
    plan->filtered_bytes=bytes;if (bytes) {plan->filtered=r1_allocate(ledger,bytes);if (!plan->filtered) return R1_BUDGET;}
    if (!r6_plan_needs_geometry(job,index)) {
        plan->scalar_only=1;plan->phase=11;return R1_OK;
    }
    plan->phase=1;return R1_OK;
}

static int r6_plan_filter_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;const r6_coordinate_raw* raw=&job->borrowed->geometry[index].coordinates;
    const r1_job* source=job->timeline->base->scene->clips[index].source;
    if (plan->row==raw->fixtures_n) {plan->row=plan->slot=plan->scan=0;plan->phase=2;return R1_OK;}
    if (plan->scan<source->streams_n) {
        plan->found|=raw->fixtures[plan->row].patch.universe==source->streams[plan->scan++].universe;return R1_OK;
    }
    if (plan->found) plan->filtered[plan->filtered_n++]=raw->fixtures[plan->row];
    plan->row++;plan->scan=plan->found=0;return R1_OK;
}

static int r6_plan_source_order_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;r3_job* scene=job->timeline->base->scene;r1_job* source=scene->clips[index].source;
    const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;
    if (plan->slot==source->streams_n) {plan->slot=0;plan->phase=3;return R1_OK;}
    r6_slot_plan* slot=plan->slots+plan->slot;uint32_t universe=source->streams[plan->slot].universe;
    if (!slot->phase) {
        r6_order_call call={0};call.fixtures=raw->coordinates.fixtures;call.fixtures_n=raw->coordinates.fixtures_n;
        call.definitions=raw->definitions;call.definitions_n=raw->definitions_n;call.universe=universe;
        call.external_limit=scene->external_bytes;call.internal_address_only=1;
        int status=r6_order_create(&slot->source_order,scene->clock.ledger,&call);if (status) return status;slot->phase=1;return R1_OK;
    }
    int status=r6_order_step(&slot->source_order,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    r4_slot* masks=job->timeline->base->clips[index].slots+plan->slot;
    plan->used[plan->slot]=(r6_used_slot){&slot->source_order,source->streams[plan->slot].base,masks->rule.present ? masks->rule.mask : NULL};
    plan->slot++;return R1_OK;
}

static int r6_plan_base_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;
    r3_job* scene=job->timeline->base->scene;
    if (!plan->base.ledger) {
        const r4_clip_raw* masks=job->borrowed->raw.raw.clip_masks+index;
        r6_base_call call={plan->filtered,plan->filtered_n,masks->base_ids,masks->base_ids_n,raw->groups,raw->groups_n,
            plan->used,scene->clips[index].source->streams_n,scene->external_bytes,raw->decimal_limit};
        return r6_base_create(&plan->base,scene->clock.ledger,&call);
    }
    int status=r6_base_step(&plan->base,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    if (!plan->base.n) {scene->pending_features|=R6_PENDING_ATTRIBUTES;return R1_PENDING;}
    status=r6_coordinates_create(&plan->coordinates,scene->clock.ledger,&raw->coordinates,plan->base.ids,plan->base.n);
    if (!status) plan->phase=4;return status;
}

static int r6_plan_coordinates_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;int status=r6_coordinates_step(&plan->coordinates,1);
    if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    r6_clip_geometry_raw const* raw=job->borrowed->geometry+index;r1_ledger* ledger=job->timeline->base->scene->clock.ledger;
    r6_virtual_call call={&plan->coordinates,raw->skip_base_order,plan->base.n!=0,0};
    status=r6_virtual_create(&plan->virtual_positions,ledger,&call);if (!status) plan->phase=5;return status;
}

static int r6_plan_virtual_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;int status=r6_virtual_step(&plan->virtual_positions,1);
    if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;r3_job* scene=job->timeline->base->scene;
    r6_shapes_call call={raw->shapes,raw->shape_pages_n,plan->base.ids,plan->base.n,scene->external_bytes};
    status=r6_shapes_create(&plan->shapes,scene->clock.ledger,&call);if (!status) plan->phase=6;return status;
}

static int r6_plan_shapes_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;int status=r6_shapes_step(&plan->shapes,1);
    if (status<0) return status;if (status==R1_BUILDING) return R1_OK;plan->phase=7;plan->slot=0;return R1_OK;
}

static int r6_plan_local_order_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;r3_job* scene=job->timeline->base->scene;r1_job* source=scene->clips[index].source;
    if (plan->slot==source->streams_n) {plan->phase=8;plan->slot=0;return R1_OK;}
    r6_slot_plan* slot=plan->slots+plan->slot;const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;
    if (slot->phase==1) {
        r6_order_call call={0};call.fixtures=raw->coordinates.fixtures;call.fixtures_n=raw->coordinates.fixtures_n;
        call.definitions=raw->definitions;call.definitions_n=raw->definitions_n;call.universe=source->streams[plan->slot].universe;
        call.authored_ids=plan->base.ids;call.ids_n=plan->base.n;call.external_limit=scene->external_bytes;
        int status=r6_order_create(&slot->local_order,scene->clock.ledger,&call);if (!status) slot->phase=2;return status;
    }
    int status=r6_order_step(&slot->local_order,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    uint64_t bytes=0;if (!r3_add_bytes(&bytes,slot->local_order.n,sizeof(uint64_t))) return R1_BUDGET;
    slot->local_ids_bytes=bytes;if (bytes) {slot->local_ids=r1_allocate(scene->clock.ledger,bytes);if (!slot->local_ids) return R1_BUDGET;}
    slot->phase=3;plan->row=0;plan->phase=9;return R1_OK;
}

static int r6_plan_local_ids_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;r6_slot_plan* slot=plan->slots+plan->slot;
    if (plan->row<slot->local_order.n) {
        uint64_t i=plan->row++;const r6_order_row* row=slot->local_order.rows+i;
        slot->local_ids[i]=slot->local_order.call.fixtures[row->fixture].patch.token;return R1_OK;
    }
    plan->phase=7;plan->slot++;return R1_OK;
}

static int r6_plan_initial_one(r6_job* job,uint64_t index)
{
    r6_clip_plan* plan=job->geometry.clips+index;
    switch(plan->phase) {
    case 0:return r6_plan_allocate(job,index);case 1:return r6_plan_filter_one(job,index);
    case 2:return r6_plan_source_order_one(job,index);case 3:return r6_plan_base_one(job,index);
    case 4:return r6_plan_coordinates_one(job,index);case 5:return r6_plan_virtual_one(job,index);
    case 6:return r6_plan_shapes_one(job,index);case 7:return r6_plan_local_order_one(job,index);
    case 9:return r6_plan_local_ids_one(job,index);default:return R1_INVALID;
    }
}
