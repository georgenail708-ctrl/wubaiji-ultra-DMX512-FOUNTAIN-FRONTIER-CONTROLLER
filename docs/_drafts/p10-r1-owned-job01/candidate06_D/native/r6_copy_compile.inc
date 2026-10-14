/* Actual remap-copy construction. Source order, spans, group vectors and
 * expanded motor masks are native owning-stage outputs, never host matrices.
 * Preserves Python emission order: all nonmotor runs, then all motor runs. */
typedef struct {
    const r6_order* spans;const uint8_t* motor;uint32_t universe;uint64_t source_slot;
} r6_source_span_slot;

typedef struct {
    const r6_order* targets;const r6_global_groups* groups;const r6_source_traits* traits;
    const r6_source_span_slot* slots;uint64_t slots_n;const uint8_t* target_motor;
    uint32_t phase_only;
} r6_copy_call;

typedef struct {
    r1_ledger* ledger;r6_copy_call call;ce_source_copy* rows;uint64_t bytes,capacity,count,stage,target,slot,scan;
    uint64_t selected_slot,selected_row,source_token;uint32_t offset,run_begin,joint_any,run_open,cancelled,counting;
    int target_start,source_start,length,group;double relative;uint8_t joint[512];
} r6_copy_plan;

static int r6_copy_create(r6_copy_plan* out,r1_ledger* ledger,const r6_copy_call* call)
{
    uint64_t capacity=0;
    if (!out || !call || !call->targets || !call->groups || !call->traits
            || call->targets->stage!=4 || call->groups->stage!=6 || call->traits->stage!=5) return R1_INVALID;
    if ((call->slots_n && !call->slots) || !call->target_motor || call->phase_only>1) return R1_INVALID;
    if (!r3_add_bytes(&capacity,call->targets->n,512)) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->capacity=capacity;out->counting=1;
    out->selected_slot=out->selected_row=UINT64_MAX;
    if (!call->traits->spatial_variation || call->traits->global_transition || !call->traits->time_variation) {
        out->stage=5;out->bytes=out->capacity=0;return R1_OK;
    }
    return R1_OK;
}

static int r6_copy_count_complete(r6_copy_plan* out)
{
    if (!out->counting) {
        if (out->count!=out->capacity) return R1_KERNEL;
        out->stage=5;return R1_OK;
    }
    if (!out->count) {out->stage=5;return R1_OK;}
    uint64_t bytes=0;if (!r3_add_bytes(&bytes,out->count,sizeof(ce_source_copy))) return R1_BUDGET;
    out->rows=r1_allocate(out->ledger,bytes);if (!out->rows) return R1_BUDGET;
    out->bytes=bytes;out->capacity=out->count;out->count=out->target=0;out->counting=0;return R1_OK;
}

static int r6_copy_target_one(r6_copy_plan* out)
{
    if (out->target==out->call.targets->n) return r6_copy_count_complete(out);
    const r6_order_row* target=out->call.targets->rows+out->target;
    if (!(target->start>=0 && target->start<target->end && target->end<=512)) {out->target++;return R1_OK;}
    const r6_global_groups* groups=out->call.groups;
    if (out->target>=groups->call.local_n || !groups->call.n) return R1_INVALID;
    double relative=(double)groups->gp[out->target];relative=fmin(fmax(relative,0.),1.);
    int64_t rank=ce_vc_round_even(relative*(double)(groups->call.n-1));
    if (rank<0 || (uint64_t)rank>=groups->call.n) return R1_INVALID;
    uint64_t source_index=(uint64_t)groups->rows[rank].idx;
    out->source_token=groups->call.ids[source_index];out->target_start=target->start;out->length=target->end-target->start;
    out->group=(int)groups->gi[out->target];out->relative=(double)groups->gp[out->target];
    out->slot=out->scan=0;out->selected_slot=out->selected_row=UINT64_MAX;out->stage=1;return R1_OK;
}

static int r6_copy_source_one(r6_copy_plan* out)
{
    if (out->slot==out->call.slots_n) {
        if (out->selected_slot==UINT64_MAX) {out->target++;out->stage=0;return R1_OK;}
        const r6_order_row* source=out->call.slots[out->selected_slot].spans->rows+out->selected_row;
        if (!(source->start>=0 && source->start<source->end && source->end<=512)) {out->target++;out->stage=0;return R1_OK;}
        out->source_start=source->start;if (source->end-source->start<out->length) out->length=source->end-source->start;
        out->offset=out->joint_any=out->run_open=0;out->stage=2;return R1_OK;
    }
    const r6_source_span_slot* slot=out->call.slots+out->slot;const r6_order* spans=slot->spans;
    if (!spans || spans->stage!=4 || !slot->motor || slot->source_slot>INT_MAX) return R1_INVALID;
    if (out->scan==spans->n) {out->slot++;out->scan=0;return R1_OK;}
    uint64_t i=out->scan++;const r6_order_row* row=spans->rows+i;
    if (spans->call.fixtures[row->fixture].patch.token==out->source_token) {out->selected_slot=out->slot;out->selected_row=i;}
    return R1_OK;
}

static int r6_copy_emit(r6_copy_plan* out,uint32_t begin,uint32_t end,int phase)
{
    if (end<=begin) return R1_OK;if (out->count==out->capacity) return R1_BUDGET;
    uint64_t source_slot=out->call.slots[out->selected_slot].source_slot;
    if (!out->counting) out->rows[out->count]=(ce_source_copy){out->target_start+(int)begin,out->source_start+(int)begin,
        (int)(end-begin),(int)source_slot,out->group,phase,out->relative};out->count++;return R1_OK;
}

static int r6_copy_joint_one(r6_copy_plan* out)
{
    if (out->offset<(uint32_t)out->length) {
        const uint8_t* source=out->call.slots[out->selected_slot].motor;uint32_t i=out->offset++;
        out->joint[i]=out->call.target_motor[out->target_start+(int)i] && source[out->source_start+(int)i];
        out->joint_any|=out->joint[i];return R1_OK;
    }
    if (!out->joint_any) {
        int status=out->call.phase_only ? R1_OK : r6_copy_emit(out,0,(uint32_t)out->length,0);
        out->target++;out->stage=0;return status;
    }
    out->offset=out->run_open=0;out->stage=out->call.phase_only ? 4 : 3;return R1_OK;
}

static int r6_copy_runs_one(r6_copy_plan* out)
{
    uint32_t i=out->offset;int phase=out->stage==4;
    int selected=i<(uint32_t)out->length && (phase ? out->joint[i]!=0 : out->joint[i]==0);
    if (selected && !out->run_open) {out->run_begin=i;out->run_open=1;}
    if (!selected && out->run_open) {
        int status=r6_copy_emit(out,out->run_begin,i,phase);if (status) return status;out->run_open=0;
    }
    if (i==(uint32_t)out->length) {
        out->offset=0;if (phase) {out->target++;out->stage=0;} else out->stage=4;return R1_OK;
    }
    out->offset++;return R1_OK;
}

static int r6_copy_step(r6_copy_plan* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=5 && !status) switch(out->stage) {
    case 0:status=r6_copy_target_one(out);break;case 1:status=r6_copy_source_one(out);break;
    case 2:status=r6_copy_joint_one(out);break;case 3:case 4:status=r6_copy_runs_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==5 ? R1_OK : R1_BUILDING;
}

static void r6_copy_destroy(r6_copy_plan* out)
{
    if (out->ledger) r1_free(out->ledger,out->rows,out->bytes);memset(out,0,sizeof(*out));
}
