/* Actual cw/ccw path uses actual coordinates, including the method named
 * _angle_direction_positions_from_virtual_coords. It does not apply hidden
 * identity slots. Native input views are produced by r6_coordinates only. */
typedef struct {
    const double *xy,*scope;uint64_t n,scope_n;const r6_shapes* shapes;
    uint32_t clockwise,prefer_shapes,start_valid;double start;
} r6_circle_call;

typedef struct {
    r1_ledger* ledger;r6_circle_call call;void* owned;uint64_t bytes,stage,row;
    _ce_fade_order_item *items,*scratch;float* positions;
    r6_axis_clusters axis;r6_circle_fit fit;r6_sum sum_x,sum_y;
    uint32_t axis_active,use_reference,centroid_matrix,cancelled,numbered_fallback;
    double center[2],bounds[4],span[2],start;
    uint64_t width,begin,left,right,mid,end,destination;uint32_t merging;
} r6_circle_positions;

static int r6_circle_positions_create(r6_circle_positions* out,r1_ledger* ledger,const r6_circle_call* call)
{
    uint64_t bytes=0;
    if (!out || !call || (call->n && !call->xy) || (call->scope_n && !call->scope)) return R1_INVALID;
    if (call->n>INT_MAX || !r3_add_bytes(&bytes,call->n,2*sizeof(_ce_fade_order_item)+sizeof(float))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;
    if (call->n) {
        out->owned=out->items=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
        out->scratch=out->items+call->n;out->positions=(float*)(out->scratch+call->n);
    }
    if (call->n<=1) {if (call->n) out->positions[0]=.5f;out->stage=8;return R1_OK;}
    const double* scope=call->scope_n ? call->scope : call->xy;uint64_t n=call->scope_n ? call->scope_n : call->n;
    int status=r6_axis_create(&out->axis,ledger,scope,n);if (status) return status;out->axis_active=1;return R1_OK;
}

static int r6_circle_scope_one(r6_circle_positions* out)
{
    int status=r6_axis_step(&out->axis,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    uint32_t matrix=out->axis.matrix;r6_axis_destroy(&out->axis);out->axis_active=0;
    if (matrix) {out->stage=2;return R1_OK;}
    uint64_t n=out->call.scope_n>=3 ? out->call.scope_n : out->call.n;
    const double* xy=out->call.scope_n>=3 ? out->call.scope : out->call.xy;
    r6_circle_fit_call call={xy,n,out->call.shapes,out->call.prefer_shapes};
    status=r6_circle_fit_create(&out->fit,&call);if (!status) out->stage=1;return status;
}

static int r6_circle_reference_one(r6_circle_positions* out)
{
    int status=r6_circle_fit_step(&out->fit,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    out->use_reference=out->fit.available;out->stage=out->use_reference ? 5 : 2;out->row=0;return R1_OK;
}

static int r6_circle_center_one(r6_circle_positions* out)
{
    uint64_t n=out->call.scope_n ? out->call.scope_n : out->call.n;
    const double* xy=out->call.scope_n ? out->call.scope : out->call.xy;
    if (n<2) {
        /* Both original centroid suppliers return None for this domain. */
        out->numbered_fallback=1;out->stage=7;out->row=0;return R1_OK;
    }
    if (out->row<n) {
        const double* point=xy+2*out->row++;if (!isfinite(point[0]) || !isfinite(point[1])) return R1_PENDING;
        r6_sum_add(&out->sum_x,point[0]);r6_sum_add(&out->sum_y,point[1]);return R1_OK;
    }
    out->center[0]=r6_sum_finish(&out->sum_x)/(double)n;out->center[1]=r6_sum_finish(&out->sum_y)/(double)n;
    int status=r6_axis_create(&out->axis,out->ledger,out->call.xy,out->call.n);if (status) return status;
    out->axis_active=1;out->stage=3;out->row=0;return R1_OK;
}

static int r6_circle_matrix_one(r6_circle_positions* out)
{
    int status=r6_axis_step(&out->axis,1);if (status<0) return status;if (status==R1_BUILDING) return R1_OK;
    out->centroid_matrix=out->axis.matrix;r6_axis_destroy(&out->axis);out->axis_active=0;out->stage=4;return R1_OK;
}

static int r6_circle_bounds_one(r6_circle_positions* out)
{
    if (out->row==out->call.n) {
        out->span[0]=out->bounds[1]-out->bounds[0];out->span[1]=out->bounds[3]-out->bounds[2];out->stage=5;out->row=0;return R1_OK;
    }
    const double* xy=out->call.xy+2*out->row;
    if (!out->row) {out->bounds[0]=out->bounds[1]=xy[0];out->bounds[2]=out->bounds[3]=xy[1];}
    if (xy[0]<out->bounds[0]) out->bounds[0]=xy[0];if (xy[0]>out->bounds[1]) out->bounds[1]=xy[0];
    if (xy[1]<out->bounds[2]) out->bounds[2]=xy[1];if (xy[1]>out->bounds[3]) out->bounds[3]=xy[1];out->row++;return R1_OK;
}

static int r6_circle_key_one(r6_circle_positions* out)
{
    if (out->row==out->call.n) {out->stage=6;out->width=1;out->row=0;return R1_OK;}
    uint64_t i=out->row++;const double* xy=out->call.xy+2*i;double angle,tie=0.,key;
    if (out->use_reference) {
        double metric[2];_ce_geo_metric(out->fit.reference,xy,metric);angle=metric[1];
    } else angle=_ce_geo_mod(atan2(xy[1]-out->center[1],xy[0]-out->center[0]));
    if (!i) out->start=out->use_reference && out->call.start_valid ? _ce_geo_mod(out->call.start) : angle;
    double delta=_ce_geo_mod(out->call.clockwise ? angle-out->start : out->start-angle);
    if (!isfinite(delta)) return R1_PENDING;
    if (!out->use_reference) {
        key=out->centroid_matrix ? -_ce_geo_ring(xy,out->center,out->span) : delta;
        tie=out->centroid_matrix ? delta : -ce_py_hypot2(xy[0]-out->center[0],xy[1]-out->center[1]);
    } else key=delta;
    out->items[i]=(_ce_fade_order_item){key,tie,(int32_t)i};return R1_OK;
}

static int r6_circle_sort_one(r6_circle_positions* out)
{
    uint64_t n=out->call.n;
    if (out->width>=n) {out->stage=7;out->row=0;return R1_OK;}
    if (out->begin==n) {
        _ce_fade_order_item* swap=out->items;out->items=out->scratch;out->scratch=swap;out->begin=0;out->width*=2;return R1_OK;
    }
    if (!out->merging) {
        out->mid=out->begin+out->width;if (out->mid>n) out->mid=n;out->end=out->mid+out->width;if (out->end>n) out->end=n;
        out->left=out->begin;out->right=out->mid;out->destination=out->begin;out->merging=1;
    }
    int left=out->left<out->mid && (out->right==out->end || !r6_linear_less(out->items+out->right,out->items+out->left));
    out->scratch[out->destination++]=out->items[left ? out->left++ : out->right++];
    if (out->destination==out->end) {out->begin=out->end;out->merging=0;}return R1_OK;
}

static int r6_circle_output_one(r6_circle_positions* out)
{
    if (out->row==out->call.n) {out->stage=8;return R1_OK;}
    uint64_t rank=out->row++;double position=(double)rank/(double)(out->call.n-1);
    if (out->numbered_fallback) out->positions[rank]=(float)(out->call.clockwise ? position : 1.-position);
    else out->positions[out->items[rank].idx]=(float)position;return R1_OK;
}

static int r6_circle_positions_step(r6_circle_positions* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=8 && !status) switch(out->stage) {
    case 0:status=r6_circle_scope_one(out);break;case 1:status=r6_circle_reference_one(out);break;
    case 2:status=r6_circle_center_one(out);break;case 3:status=r6_circle_matrix_one(out);break;
    case 4:status=r6_circle_bounds_one(out);break;case 5:status=r6_circle_key_one(out);break;
    case 6:status=r6_circle_sort_one(out);break;case 7:status=r6_circle_output_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==8 ? R1_OK : R1_BUILDING;
}

static void r6_circle_positions_destroy(r6_circle_positions* out)
{
    if (out->axis_active) r6_axis_destroy(&out->axis);
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
