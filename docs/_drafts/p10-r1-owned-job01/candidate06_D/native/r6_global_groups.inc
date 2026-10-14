/* Native global spatial grouping from _sequential_fixture_group_data.
 * Membership follows the main direction; each contiguous group recalculates
 * its phase from its coordinates against the same global spatial scope.
 * All arrays passed here originate the native order/virtual-position stages. */
typedef struct {
    const uint64_t *ids,*local_ids;const double* xy;uint64_t n,local_n;
    r1_atom group_count;uint32_t direction;uint64_t raw_overrides_n;
    const r6_shapes* shapes;uint32_t prefer_shapes;
} r6_global_group_call;

typedef struct {
    r1_ledger* ledger;r6_global_group_call call;void* owned;uint64_t bytes,stage,row,scan;
    _ce_fade_order_item *rows,*scratch;double* group_xy;float *meta,*gi,*gp,*gs;
    uint64_t width,begin,left,right,mid,end,destination,group,group_begin,group_end;
    uint32_t merging,cancelled,groups,group_width,linear_active,scope_equal;
    r6_positions position;
} r6_global_groups;

static int r6_global_groups_create(r6_global_groups* out,r1_ledger* ledger,const r6_global_group_call* call)
{
    uint64_t bytes=0;double count;int status;
    if (!out || !call || (call->n && (!call->ids || !call->xy)) || (call->local_n && !call->local_ids)) return R1_INVALID;
    if (call->raw_overrides_n || !call->n || call->n>INT_MAX || call->local_n>INT_MAX) return R1_PENDING;
    if (call->direction<R5_DIR_LTR || call->direction>R5_DIR_RTC) return R1_PENDING;
    status=r5_number(call->group_count,1.,&count);if (status) return status;
    if (count==0.) count=1.;if (!isfinite(count)) return R1_PENDING;
    count=trunc(count);if (count<1.) count=1.;if (count>(double)call->n) count=(double)call->n;
    if (!r3_add_bytes(&bytes,call->n,2*sizeof(_ce_fade_order_item)+2*sizeof(double)+3*sizeof(float))
            || !r3_add_bytes(&bytes,call->local_n,3*sizeof(float))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;out->groups=(uint32_t)count;
    out->group_width=(uint32_t)(call->n/out->groups+(call->n%out->groups!=0));
    out->owned=out->rows=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
    out->scratch=out->rows+call->n;out->group_xy=(double*)(out->scratch+call->n);
    out->meta=(float*)(out->group_xy+2*call->n);out->gi=out->meta+3*call->n;
    out->gp=out->gi+call->local_n;out->gs=out->gp+call->local_n;
    r6_positions_call position={call->xy,call->xy,call->n,call->n,call->direction,call->shapes,call->prefer_shapes,0,0.};
    status=r6_positions_create(&out->position,ledger,&position);
    if (status) {r6_positions_destroy(&out->position);r1_free(ledger,out->owned,out->bytes);out->owned=NULL;out->bytes=0;return status;}
    out->linear_active=1;return R1_OK;
}

static int r6_group_main_one(r6_global_groups* out)
{
    int status=r6_positions_step(&out->position,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    if (out->row<out->call.n) {
        uint64_t i=out->row++;out->rows[i]=(_ce_fade_order_item){(double)r6_positions_values(&out->position)[i],0.,(int32_t)i};return R1_OK;
    }
    r6_positions_destroy(&out->position);out->linear_active=0;out->stage=1;out->row=0;out->width=1;return R1_OK;
}

static int r6_group_sort_one(r6_global_groups* out)
{
    uint64_t n=out->call.n;
    if (out->width>=n) {out->stage=2;out->row=0;return R1_OK;}
    if (out->begin==n) {
        _ce_fade_order_item* swap=out->rows;out->rows=out->scratch;out->scratch=swap;
        out->begin=0;out->width*=2;return R1_OK;
    }
    if (!out->merging) {
        out->mid=out->begin+out->width;if (out->mid>n) out->mid=n;
        out->end=out->mid+out->width;if (out->end>n) out->end=n;
        out->left=out->begin;out->right=out->mid;out->destination=out->begin;out->merging=1;
    }
    int left=out->left<out->mid && (out->right==out->end || !r6_linear_less(out->rows+out->right,out->rows+out->left));
    out->scratch[out->destination++]=out->rows[left ? out->left++ : out->right++];
    if (out->destination==out->end) {out->begin=out->end;out->merging=0;}
    return R1_OK;
}

static int r6_group_coordinates_one(r6_global_groups* out)
{
    if (out->group==out->groups) {out->stage=5;out->row=out->scan=0;return R1_OK;}
    if (!out->row) {
        out->group_begin=out->group*out->group_width;out->group_end=out->group_begin+out->group_width;
        if (out->group_end>out->call.n) out->group_end=out->call.n;
        if (out->group_begin>=out->call.n) {out->group++;return R1_OK;}
    }
    uint64_t n=out->group_end-out->group_begin;
    if (!out->row) out->scope_equal=n==out->call.n;
    if (out->row<n) {
        uint64_t i=(uint64_t)out->rows[out->group_begin+out->row].idx;
        out->scope_equal&=i==out->row;
        out->group_xy[2*out->row]=out->call.xy[2*i];out->group_xy[2*out->row+1]=out->call.xy[2*i+1];out->row++;
        return R1_OK;
    }
    r6_positions_call call={out->group_xy,out->call.xy,n,out->call.n,out->call.direction,out->call.shapes,
        out->scope_equal && out->call.prefer_shapes,0,0.};
    int status=r6_positions_create(&out->position,out->ledger,&call);if (status) return status;
    out->linear_active=1;out->stage=3;out->row=0;return R1_OK;
}

static int r6_group_linear_one(r6_global_groups* out)
{
    int status=r6_positions_step(&out->position,1);if (status<0) return status;
    if (status==R1_OK) {out->stage=4;out->row=0;}return R1_OK;
}

static int r6_group_meta_one(r6_global_groups* out)
{
    uint64_t n=out->group_end-out->group_begin;
    if (out->row<n) {
        uint64_t index=(uint64_t)out->rows[out->group_begin+out->row].idx;float* meta=out->meta+3*index;
        meta[0]=(float)out->group;meta[1]=r6_positions_values(&out->position)[out->row];meta[2]=(float)n;out->row++;return R1_OK;
    }
    r6_positions_destroy(&out->position);out->linear_active=0;out->group++;out->row=0;out->stage=2;return R1_OK;
}

static int r6_group_local_one(r6_global_groups* out)
{
    if (out->row==out->call.local_n) {out->stage=6;return R1_OK;}
    if (out->scan==out->call.n) return R1_PENDING; /* original numbered fallback has a distinct next port */
    uint64_t i=out->scan++;
    if (out->call.ids[i]==out->call.local_ids[out->row]) {
        const float* meta=out->meta+3*i;out->gi[out->row]=meta[0];out->gp[out->row]=meta[1];out->gs[out->row]=meta[2];
        out->row++;out->scan=0;
    }
    return R1_OK;
}

static int r6_global_groups_step(r6_global_groups* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=6 && !status) switch(out->stage) {
    case 0:status=r6_group_main_one(out);break;case 1:status=r6_group_sort_one(out);break;
    case 2:status=r6_group_coordinates_one(out);break;case 3:status=r6_group_linear_one(out);break;
    case 4:status=r6_group_meta_one(out);break;case 5:status=r6_group_local_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==6 ? R1_OK : R1_BUILDING;
}

static void r6_global_groups_destroy(r6_global_groups* out)
{
    if (out->linear_active) r6_positions_destroy(&out->position);
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
