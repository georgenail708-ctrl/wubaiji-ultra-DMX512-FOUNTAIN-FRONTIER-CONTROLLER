/* Original sorted axis clustering for _matrix_like_points. The previous
 * value changes only when a new cluster is counted, not every adjacent value. */
typedef struct {
    r1_ledger* ledger;const double* xy;uint64_t n,bytes,stage,row;
    void* owned;_ce_fade_order_item *rows,*scratch;
    uint64_t width,begin,left,right,mid,end,destination;uint32_t merging,cancelled,axis,counts[2],matrix;
    double tolerance,previous;
} r6_axis_clusters;

static int r6_axis_create(r6_axis_clusters* out,r1_ledger* ledger,const double* xy,uint64_t n)
{
    uint64_t bytes=0;if (!out || (n && !xy)) return R1_INVALID;
    if (n>INT_MAX || !r3_add_bytes(&bytes,n,2*sizeof(_ce_fade_order_item))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->xy=xy;out->n=n;out->bytes=bytes;
    if (n<4) {out->stage=3;return R1_OK;}
    out->owned=out->rows=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;out->scratch=out->rows+n;return R1_OK;
}

static int r6_axis_key_one(r6_axis_clusters* out)
{
    if (out->row==out->n) {out->row=0;out->width=1;out->stage=1;return R1_OK;}
    uint64_t i=out->row++;double value=out->xy[2*i+out->axis];if (!isfinite(value)) return R1_PENDING;
    out->rows[i]=(_ce_fade_order_item){value,0.,(int32_t)i};return R1_OK;
}

static int r6_axis_sort_one(r6_axis_clusters* out)
{
    uint64_t n=out->n;
    if (out->width>=n) {
        out->previous=out->rows[0].key;out->tolerance=fmax(1e-6,(out->rows[n-1].key-out->previous)*.025);
        out->counts[out->axis]=1;out->stage=2;out->row=1;return R1_OK;
    }
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
    if (out->destination==out->end) {out->begin=out->end;out->merging=0;}return R1_OK;
}

static int r6_axis_count_one(r6_axis_clusters* out)
{
    if (out->row<out->n) {
        double value=out->rows[out->row++].key;
        if (fabs(value-out->previous)>out->tolerance) {out->counts[out->axis]++;out->previous=value;}return R1_OK;
    }
    if (!out->axis) {out->axis=1;out->stage=out->row=out->begin=0;return R1_OK;}
    uint64_t product=(uint64_t)out->counts[0]*(uint64_t)out->counts[1];
    out->matrix=out->counts[0]>=3 && out->counts[1]>=3 && (double)out->n/(double)product>=.80;
    out->stage=3;return R1_OK;
}

static int r6_axis_step(r6_axis_clusters* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=3 && !status) switch(out->stage) {
    case 0:status=r6_axis_key_one(out);break;case 1:status=r6_axis_sort_one(out);break;
    case 2:status=r6_axis_count_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==3 ? R1_OK : R1_BUILDING;
}

static void r6_axis_destroy(r6_axis_clusters* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
