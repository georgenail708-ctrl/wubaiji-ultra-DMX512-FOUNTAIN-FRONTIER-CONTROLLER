/* Original _linear_direction_positions_for_fixture_ids arithmetic, fed only
 * by the native selected/virtual coordinate phase. One source-key/rank/move
 * per compile unit; owned scratch replaces geometry facade malloc. */
typedef struct {
    const double* xy;const double* scope;uint64_t n,scope_n;uint32_t direction;
} r6_linear_call;
typedef struct {
    r1_ledger* ledger;r6_linear_call call;uint64_t bytes,row,stage,sort_row,sort_walk;
    _ce_fade_order_item *items,*scratch;void* owned;float* positions;
    double lo_x,hi_x,lo_y,hi_y,span,low,center,max_dist;
    uint32_t axis,constant,cancelled;_ce_fade_order_item value;
    uint64_t merge_width,merge_begin,merge_left,merge_right,merge_out,merge_mid,merge_end;
    uint32_t merging;
} r6_linear;

static int r6_linear_create(r6_linear* out,r1_ledger* ledger,const r6_linear_call* call)
{
    uint64_t bytes=0;
    if (!out || !call || (call->n && !call->xy) || (call->scope_n && !call->scope)
            || call->direction<R5_DIR_LTR || call->direction>R5_DIR_RTC
            || call->direction==R5_DIR_CW || call->direction==R5_DIR_CCW) return R1_INVALID;
    if (call->n>INT_MAX || !r3_add_bytes(&bytes,call->n,2*sizeof(_ce_fade_order_item)+sizeof(float))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;
    if (call->n) {
        out->owned=out->items=r1_allocate(ledger,bytes);if (!out->items) return R1_BUDGET;
        out->scratch=out->items+call->n;out->positions=(float*)(out->scratch+call->n);
    }
    if (call->n<=1) {if (call->n) out->positions[0]=.5f;out->stage=5;}
    return R1_OK;
}

static int r6_linear_bounds_one(r6_linear* out)
{
    uint64_t count=out->call.scope_n ? out->call.scope_n : out->call.n;
    const double* xy=out->call.scope_n ? out->call.scope : out->call.xy;
    if (out->row==count) {out->row=0;out->stage=1;return R1_OK;}
    double x=xy[2*out->row],y=xy[2*out->row+1];
    /* NaN sorting/exception compatibility is not approximated; ordinary
     * finite coordinate path remains fully native. */
    if (!isfinite(x) || !isfinite(y)) return R1_PENDING;
    if (!out->row) {out->lo_x=out->hi_x=x;out->lo_y=out->hi_y=y;}
    if (x<out->lo_x) out->lo_x=x;if (x>out->hi_x) out->hi_x=x;
    if (y<out->lo_y) out->lo_y=y;if (y>out->hi_y) out->hi_y=y;
    out->row++;return R1_OK;
}

static int r6_linear_axis(r6_linear* out)
{
    double sx=out->hi_x-out->lo_x,sy=out->hi_y-out->lo_y;
    uint32_t direction=out->call.direction;
    if (direction==R5_DIR_CTR || direction==R5_DIR_RTC) out->axis=sx>=sy ? 0 : 1;
    else {
        out->axis=direction==R5_DIR_FTB || direction==R5_DIR_BTF;
        double primary=out->axis ? sy : sx,fallback=out->axis ? sx : sy;
        if (!(primary>1e-6) && fallback>1e-6) out->axis=!out->axis;
    }
    out->low=out->axis ? out->lo_y : out->lo_x;
    out->span=out->axis ? sy : sx;out->constant=out->span<=1e-6;
    out->center=(out->low+(out->axis ? out->hi_y : out->hi_x))*.5;
    out->row=0;out->stage=2;return R1_OK;
}

static int r6_linear_center_one(r6_linear* out)
{
    if (out->call.direction!=R5_DIR_CTR && out->call.direction!=R5_DIR_RTC) {out->stage=3;out->row=0;return R1_OK;}
    uint64_t count=out->call.scope_n ? out->call.scope_n : out->call.n;
    const double* xy=out->call.scope_n ? out->call.scope : out->call.xy;
    if (out->row==count) {out->constant|=out->max_dist<=1e-6;out->row=0;out->stage=3;return R1_OK;}
    double distance=fabs(xy[2*out->row++ +out->axis]-out->center);
    if (distance>out->max_dist) out->max_dist=distance;return R1_OK;
}

static int r6_linear_key_one(r6_linear* out)
{
    if (out->row==out->call.n) {out->stage=4;out->merge_width=1;return R1_OK;}
    uint64_t i=out->row++;double coordinate=out->call.xy[2*i+out->axis],key;
    if (!isfinite(coordinate) || !isfinite(out->call.xy[2*i+!out->axis])) return R1_PENDING;
    uint32_t direction=out->call.direction;
    if (direction==R5_DIR_CTR || direction==R5_DIR_RTC) {
        key=out->constant ? .5 : fabs(coordinate-out->center)/out->max_dist;
        if (direction==R5_DIR_RTC && !out->constant) key=1.-key;
    } else {
        key=out->constant ? .5 : (coordinate-out->low)/out->span;
        if (direction==R5_DIR_RTL || direction==R5_DIR_BTF) key=1.-key;
    }
    out->items[i]=(_ce_fade_order_item){key,(direction==R5_DIR_CTR || direction==R5_DIR_RTC) ? 0. : out->call.xy[2*i+!out->axis],(int)i};
    return R1_OK;
}

static int r6_linear_less(const _ce_fade_order_item* left,const _ce_fade_order_item* right)
{
    return left->key<right->key || (left->key==right->key && (left->tie<right->tie
        || (left->tie==right->tie && left->idx<right->idx)));
}

static int r6_linear_sort_one(r6_linear* out)
{
    uint64_t n=out->call.n;
    if (out->merge_width>=n) {out->row=0;out->stage=6;return R1_OK;}
    if (out->merge_begin==n) {
        _ce_fade_order_item* swap=out->items;out->items=out->scratch;out->scratch=swap;
        out->merge_begin=0;out->merge_width*=2;return R1_OK;
    }
    if (!out->merging) {
        out->merge_mid=out->merge_begin+out->merge_width;if (out->merge_mid>n) out->merge_mid=n;
        out->merge_end=out->merge_mid+out->merge_width;if (out->merge_end>n) out->merge_end=n;
        out->merge_left=out->merge_begin;out->merge_right=out->merge_mid;out->merge_out=out->merge_begin;out->merging=1;
    }
    int take_left=out->merge_left<out->merge_mid && (out->merge_right==out->merge_end
        || !r6_linear_less(out->items+out->merge_right,out->items+out->merge_left));
    out->scratch[out->merge_out++]=out->items[take_left ? out->merge_left++ : out->merge_right++];
    if (out->merge_out==out->merge_end) {out->merge_begin=out->merge_end;out->merging=0;}
    return R1_OK;
}

static int r6_linear_output_one(r6_linear* out)
{
    if (out->row==out->call.n) {out->stage=5;return R1_OK;}
    uint64_t rank=out->row++;int index=out->items[rank].idx;
    int center=out->call.direction==R5_DIR_CTR || out->call.direction==R5_DIR_RTC;
    out->positions[index]=center && out->constant ? .5f : (float)((double)rank/(double)(out->call.n-1));return R1_OK;
}

static int r6_linear_step(r6_linear* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=5 && !status) switch(out->stage) {
    case 0:status=r6_linear_bounds_one(out);break;case 1:status=r6_linear_axis(out);break;
    case 2:status=r6_linear_center_one(out);break;case 3:status=r6_linear_key_one(out);break;
    case 4:status=r6_linear_sort_one(out);break;case 6:status=r6_linear_output_one(out);break;
    default:return R1_INVALID;
    }
    return status ? status : out->stage==5 ? R1_OK : R1_BUILDING;
}

static void r6_linear_destroy(r6_linear* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
