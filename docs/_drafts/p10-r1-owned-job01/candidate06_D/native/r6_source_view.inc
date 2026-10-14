/* Actual retained source kernel mathematics over R1 owning scatter rows.
 * No whole-job dense frame copy, Python callback or per-call heap allocation.
 * This module is not an admission of unfinished geometry/causal domains. */
enum {R6_NO_SEMANTIC,R6_SAMPLE_INT_NAN,R6_SAMPLE_INT_INF};
typedef struct {
    uint64_t bits;uint32_t large,slot,interpolate,reserved;double first_t;
} r6_sample_entry;
typedef struct {
    r6_sample_entry* entries;uint64_t count,capacity,bytes;
    r1_ledger* ledger;uint32_t semantic;
} r6_sample_cache;
typedef struct {
    const ce_source_copy* rows;uint64_t count;const ce_source_remap* phase;const uint8_t* raw;
} r6_remap_call;

static int r6_sample_key(double time,uint64_t* bits,uint32_t* large,uint32_t* semantic)
{
    double scaled=time*10000.;
    if (isnan(scaled)) {*semantic=R6_SAMPLE_INT_NAN;return R1_PENDING;}
    if (isinf(scaled)) {*semantic=R6_SAMPLE_INT_INF;return R1_PENDING;}
    /* sample_t is originally max(0,..): finite values >=2**63 are already
     * integral binary64. Their original arbitrary-int round keys compare
     * exactly iff these positive binary64 bits compare, without int64 cast. */
    if (scaled>=9223372036854775808.) {
        memcpy(bits,&scaled,8);*large=1;return R1_OK;
    }
    if (scaled<0.) return R1_PENDING;
    *bits=(uint64_t)ce_vc_round_even(scaled);*large=0;return R1_OK;
}

static int r6_sample_cache_create(r6_sample_cache* cache,r1_ledger* ledger,uint64_t rows)
{
    uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,rows,sizeof(r6_sample_entry))) return R1_BUDGET;
    cache->ledger=ledger;cache->capacity=rows;cache->bytes=bytes;
    if (rows) {cache->entries=r1_allocate(ledger,bytes);if (!cache->entries) return R1_BUDGET;}
    return R1_OK;
}

static void r6_sample_cache_destroy(r6_sample_cache* cache)
{
    if (cache->ledger) r1_free(cache->ledger,cache->entries,cache->bytes);
    memset(cache,0,sizeof(*cache));
}

static int r6_sample_cache_time(r6_sample_cache* cache,uint32_t slot,int interpolate,double* time)
{
    uint64_t key;uint32_t large;int status=r6_sample_key(*time,&key,&large,&cache->semantic);if (status) return status;
    for (uint64_t i=0;i<cache->count;i++) {
        const r6_sample_entry* row=cache->entries+i;
        if (row->bits==key && row->large==large && row->slot==slot && row->interpolate==(uint32_t)interpolate) {
            *time=row->first_t;return R1_OK;
        }
    }
    if (cache->count==cache->capacity) return R1_INVALID;
    cache->entries[cache->count++]=(r6_sample_entry){key,large,slot,(uint32_t)interpolate,0,*time};return R1_OK;
}

static void r6_source_sample(const r1_job* source,const r1_stream* stream,double time,int interpolate,uint8_t* out)
{
    uint64_t left=0,right=stream->count;const double* times=source->times+stream->begin;
    while (left<right) {
        uint64_t middle=left+(right-left)/2;if (times[middle]<time) left=middle+1;else right=middle;
    }
    if (!interpolate || stream->count<2 || !left || left==stream->count) {
        uint64_t row=!left ? 0 : left==stream->count ? left-1
            : time-times[left-1]<times[left]-time ? left-1 : left;
        memcpy(out,r1_source_row(source,stream->begin+row),512);return;
    }
    double span=times[left]-times[left-1];
    if (span<=1e-9) {memcpy(out,r1_source_row(source,stream->begin+left),512);return;}
    float ratio=(float)fmin(fmax((time-times[left-1])/span,0.),1.);
    const uint8_t *a=r1_source_row(source,stream->begin+left-1),*b=r1_source_row(source,stream->begin+left);
    for (int c=0;c<512;c++) {
        float delta=(float)b[c]-(float)a[c];float value=(float)a[c]+delta*ratio;
        value=ce_np_minf(ce_np_maxf(value,0.f),255.f);out[c]=(uint8_t)ce_vc_round_even((double)value);
    }
}

static int r6_source_remap(const r1_job* source,const r6_remap_call* call,r6_sample_cache* cache,uint8_t* out)
{
    if (!call) return R1_INVALID;
    const ce_source_copy* rows=call->rows;uint64_t count=call->count;const ce_source_remap* phase=call->phase;const uint8_t* raw=call->raw;
    if (!source || !phase || !cache || !out || (count && !rows) || phase->batch_count<1 || (phase->seed && !raw)) return R1_INVALID;
    if (cache->capacity<count) return R1_INVALID;
    cache->count=0;cache->semantic=0;
    if (phase->seed) memcpy(out,raw,512);else memset(out,0,512);
    for (uint64_t i=0;i<count;i++) {
        const ce_source_copy* row=rows+i;
        if (row->slot<0 || (uint64_t)row->slot>=source->streams_n || row->group<0 || row->count<0 || row->count>512
                || row->target<0 || row->source<0 || row->target>512-row->count || row->source>512-row->count) return R1_INVALID;
        const r1_stream* stream=source->streams+row->slot;
        int no_delay=row->phase && phase->motor_no_delay && phase->phase_amount<=1e-6 && phase->cyclic;
        double start=no_delay ? 0. : _ce_source_batch_start(phase,row->group),local=fmax(0.,phase->local_t);
        if (local<start-1e-9 || !stream->count || !row->count) continue;
        double time=_ce_source_remap_time(phase,row,local-start,no_delay);int interpolate=row->phase && phase->phase_amount>1e-6;
        int status=r6_sample_cache_time(cache,(uint32_t)row->slot,interpolate,&time);if (status) return status;
        uint8_t sampled[512];r6_source_sample(source,stream,time,interpolate,sampled);
        memcpy(out+row->target,sampled+row->source,(size_t)row->count);
    }
    return R1_OK;
}
