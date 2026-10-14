/* Original _seq_sampled_stack/_movement_* and spatial-span gates. Uses pinned
 * R1 scatter rows and f64 timestamps. At most 127x512 byte samples, never a
 * whole-source matrix; one byte/comparison per bounded compile unit. */
typedef struct {
    const r1_job* source;const r1_stream* stream;const r6_order* targets;
} r6_traits_call;

typedef struct {
    r1_ledger* ledger;r6_traits_call call;void* owned;uint64_t bytes,stage,row,channel,scan;
    uint64_t sample_count,sample_step,spatial_step,spatial_row,span,valid_spans;
    uint8_t* stack;double* times;uint8_t minimum[512],maximum[512];
    uint64_t first_change,last_change,cross_sum;uint32_t moving_n,moving48,crossings;
    uint8_t spatial_max,spatial_min,spatial_high;uint32_t spatial_started;
    uint32_t time_variation,cyclic,global_transition,spatial_variation,cancelled;
    double widest;
} r6_source_traits;

static uint64_t r6_sample_index(uint64_t row,uint64_t step,uint64_t n)
{
    if (row>(n-1)/step) return n-1;return row*step;
}

static int r6_traits_create(r6_source_traits* out,r1_ledger* ledger,const r6_traits_call* call)
{
    uint64_t bytes=0,n,step,count;
    if (!out || !call || !call->source || !call->stream || !call->targets || call->targets->stage!=4) return R1_INVALID;
    n=call->stream->count;step=n/64;if (!step) step=1;
    count=n ? (n-1)/step+1+((n-1)%step!=0) : 0;
    if (count>127 || !r3_add_bytes(&bytes,count,512+sizeof(double))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;
    out->sample_count=count;out->sample_step=step;out->spatial_step=n/24;if (!out->spatial_step) out->spatial_step=1;
    memset(out->minimum,255,512);out->first_change=UINT64_MAX;
    if (count) {
        out->owned=out->stack=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
        out->times=(double*)(out->stack+count*512);
    }
    if (n<=1) out->stage=5;return R1_OK;
}

static int r6_traits_sample_one(r6_source_traits* out)
{
    if (out->row==out->sample_count) {out->stage=1;out->row=out->channel=out->scan=0;return R1_OK;}
    const r1_stream* stream=out->call.stream;uint64_t i=r6_sample_index(out->row,out->sample_step,stream->count);
    const uint8_t* data=r1_source_row(out->call.source,stream->begin+i);uint64_t c=out->channel;
    if (!c) {
        double time=out->call.source->times[stream->begin+i];if (!isfinite(time)) return R1_PENDING;
        out->times[out->row]=time;
    }
    uint8_t value=data[c];out->stack[out->row*512+c]=value;
    if (value<out->minimum[c]) out->minimum[c]=value;if (value>out->maximum[c]) out->maximum[c]=value;
    if (++out->channel==512) {out->channel=0;out->row++;}return R1_OK;
}

static int r6_traits_channel_one(r6_source_traits* out)
{
    if (out->channel==512) {
        double total=out->times[out->sample_count-1]-out->times[0];
        double mean=out->moving48 ? (double)out->cross_sum/(double)out->moving48 : 0.;
        double fraction=total<=1e-6 ? 0. : out->widest/total;
        out->time_variation=out->moving_n!=0;out->cyclic=mean>2.5;out->global_transition=fraction>=.8 && mean<=2.5;
        out->stage=2;out->channel=out->scan=0;return R1_OK;
    }
    uint64_t c=out->channel;
    if (out->maximum[c]==out->minimum[c]) {out->channel++;return R1_OK;}
    if (out->scan+1<out->sample_count) {
        uint64_t i=out->scan++;uint8_t a=out->stack[i*512+c],b=out->stack[(i+1)*512+c];
        if (a!=b) {if (out->first_change==UINT64_MAX) out->first_change=i;out->last_change=i+1;}
        int mid=((int)out->minimum[c]+(int)out->maximum[c])/2;out->crossings+=(a>mid)!=(b>mid);return R1_OK;
    }
    if (out->first_change!=UINT64_MAX) {
        double span=out->times[out->last_change]-out->times[out->first_change];if (span>out->widest) out->widest=span;
    }
    if (out->moving48<48) {out->cross_sum+=out->crossings;out->moving48++;}
    out->moving_n++;out->channel++;out->scan=out->crossings=0;out->first_change=UINT64_MAX;return R1_OK;
}

static int r6_traits_spans_one(r6_source_traits* out)
{
    if (out->scan<out->call.targets->n) {
        const r6_order_row* row=out->call.targets->rows+out->scan++;
        out->valid_spans+=row->start>=0 && row->start<row->end && row->end<=512;return R1_OK;
    }
    out->scan=0;out->stage=out->valid_spans>=2 ? 3 : 5;return R1_OK;
}

static int r6_traits_spatial_finish(r6_source_traits* out)
{
    if (!out->spatial_started) {
        out->spatial_min=out->spatial_high=out->spatial_max;out->spatial_started=1;
    } else {
        if (out->spatial_max<out->spatial_min) out->spatial_min=out->spatial_max;
        if (out->spatial_max>out->spatial_high) out->spatial_high=out->spatial_max;
    }
    if (out->spatial_min!=out->spatial_high) {out->spatial_variation=1;out->stage=5;}
    out->span++;out->channel=0;out->spatial_max=0;return R1_OK;
}

static int r6_traits_spatial_one(r6_source_traits* out)
{
    const r6_order* spans=out->call.targets;uint64_t n=out->call.stream->count;
    if (out->span==spans->n) {
        uint64_t i=r6_sample_index(out->spatial_row,out->spatial_step,n);
        if (i==n-1) {out->stage=5;return R1_OK;}
        out->spatial_row++;out->span=out->channel=out->spatial_started=0;return R1_OK;
    }
    const r6_order_row* span=spans->rows+out->span;
    if (!(span->start>=0 && span->start<span->end && span->end<=512)) {out->span++;return R1_OK;}
    if (!out->channel) out->channel=(uint64_t)span->start+1;
    uint64_t i=r6_sample_index(out->spatial_row,out->spatial_step,n);
    const uint8_t* data=r1_source_row(out->call.source,out->call.stream->begin+i);
    uint8_t value=data[out->channel-1];if (value>out->spatial_max) out->spatial_max=value;
    if (out->channel++==(uint64_t)span->end) return r6_traits_spatial_finish(out);return R1_OK;
}

static int r6_traits_step(r6_source_traits* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=5 && !status) switch(out->stage) {
    case 0:status=r6_traits_sample_one(out);break;case 1:status=r6_traits_channel_one(out);break;
    case 2:status=r6_traits_spans_one(out);break;case 3:status=r6_traits_spatial_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==5 ? R1_OK : R1_BUILDING;
}

static void r6_traits_destroy(r6_source_traits* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
