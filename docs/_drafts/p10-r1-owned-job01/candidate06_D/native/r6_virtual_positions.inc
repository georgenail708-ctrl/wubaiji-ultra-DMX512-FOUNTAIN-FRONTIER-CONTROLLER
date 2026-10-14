/* Owning hidden coordinate layer from _virtual_topview_fixture_positions_for_ids.
 * Inputs are internal C order/coordinate results. No host virtual slots allowed.
 * Circle slots remain a distinct pending stage; linear slots use exact x/y/rank. */
typedef struct {
    const r6_coordinates* actual;r1_atom skip_base_order;
    uint32_t native_base_nonempty,circle_slots;
} r6_virtual_call;

typedef struct {
    r1_ledger* ledger;r6_virtual_call call;void* owned;uint64_t bytes,stage,row,n;
    _ce_fade_order_item *rows,*scratch;uint64_t* base_indices;double* xy;uint8_t* present;
    uint64_t width,begin,left,right,mid,end,destination;uint32_t merging,cancelled,direct;
} r6_virtual;

static int r6_virtual_create(r6_virtual* out,r1_ledger* ledger,const r6_virtual_call* call)
{
    uint64_t bytes=0;int skip=0,status;
    if (!out || !call || !call->actual || call->actual->stage!=5) return R1_INVALID;
    uint64_t n=call->actual->n;if (n>INT_MAX) return R1_BUDGET;
    if (!r3_add_bytes(&bytes,n,2*sizeof(_ce_fade_order_item)+sizeof(uint64_t)+2*sizeof(double)+1)) return R1_BUDGET;
    status=r5_truth(call->skip_base_order,0,&skip);if (status) return status;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;
    out->direct=skip || !call->native_base_nonempty;
    if (call->circle_slots && !out->direct) return R1_PENDING;
    if (n) {
        out->owned=out->rows=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
        out->scratch=out->rows+n;out->base_indices=(uint64_t*)(out->scratch+n);
        out->xy=(double*)(out->base_indices+n);out->present=(uint8_t*)(out->xy+2*n);
    }
    return R1_OK;
}

static int r6_virtual_select_one(r6_virtual* out)
{
    const r6_coordinates* actual=out->call.actual;
    if (out->row==actual->n) {
        out->row=0;out->stage=out->direct || out->n<=1 ? 2 : 1;out->width=1;return R1_OK;
    }
    uint64_t row=out->row++;int valid=actual->complete_snapshot ? actual->snapshot_valid[row]!=0 : actual->fixture[row]>=0;
    out->present[row]=(uint8_t)valid;if (!valid) return R1_OK;
    double x=actual->xy[2*row],y=actual->xy[2*row+1];
    /* Python tuple sorting with NaN is not replaced by C comparisons. This
     * finite-only slot stage declares pending before any output is sealed. */
    if (!isfinite(x) || !isfinite(y)) return R1_PENDING;
    out->base_indices[out->n]=row;
    out->rows[out->n]=(_ce_fade_order_item){x,y,(int32_t)out->n};out->n++;
    return R1_OK;
}

static int r6_virtual_sort_one(r6_virtual* out)
{
    uint64_t n=out->n;
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

static int r6_virtual_assign_one(r6_virtual* out)
{
    if (out->row==out->n) {out->stage=3;return R1_OK;}
    uint64_t target=out->base_indices[out->row];
    uint64_t source=out->direct ? target : out->base_indices[(uint64_t)out->rows[out->row].idx];
    const double* xy=out->call.actual->xy;
    out->xy[2*target]=xy[2*source];out->xy[2*target+1]=xy[2*source+1];out->row++;
    return R1_OK;
}

static int r6_virtual_step(r6_virtual* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=3 && !status) switch(out->stage) {
    case 0:status=r6_virtual_select_one(out);break;case 1:status=r6_virtual_sort_one(out);break;
    case 2:status=r6_virtual_assign_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==3 ? R1_OK : R1_BUILDING;
}

static void r6_virtual_destroy(r6_virtual* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
