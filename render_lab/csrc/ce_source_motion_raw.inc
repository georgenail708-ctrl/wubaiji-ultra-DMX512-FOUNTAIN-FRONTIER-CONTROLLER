/* Source tables retain original f64 timestamps and exact recorded bytes. */
typedef struct { const double* times; const uint8_t* frames; int count; } ce_source_table;
typedef struct { int target, source, count, slot, group, phase; double relative; } ce_source_copy;
typedef struct {
    const double* pauses;
    int batch_count, seed, cyclic, motor_no_delay, parallel, phase_enabled, force_enabled;
    double local_t, source_t, duration, single, step, phase_amount, delay_in, fade_in;
    double accel, cruise, decel;
} ce_source_remap;
typedef struct { int64_t key; int slot, interp; double first_t; } ce_source_sample;

static int _ce_source_table_valid(const ce_source_table* s)
{
    return s->count >= 0 && (!s->count || (s->times && s->frames));
}

static int _ce_source_nearest(const ce_source_table* s, double t)
{
    int lo = 0, hi = s->count;
    while (lo < hi) { int m = lo + (hi-lo)/2; if (s->times[m] < t) lo=m+1; else hi=m; }
    if (!lo) return 0;
    if (lo == s->count) return lo-1;
    return t-s->times[lo-1] < s->times[lo]-t ? lo-1 : lo;
}

static void _ce_source_sample_frame(const ce_source_table* s, double t, int interp, uint8_t* out)
{
    if (!interp || s->count < 2) {
        memcpy(out, s->frames + (size_t)_ce_source_nearest(s,t)*512, 512); return;
    }
    int lo=0, hi=s->count;
    while (lo<hi) { int m=lo+(hi-lo)/2; if (s->times[m]<t) lo=m+1; else hi=m; }
    if (!lo || lo == s->count) {
        memcpy(out,s->frames+(size_t)(!lo ? 0 : lo-1)*512,512); return;
    }
    double span=s->times[lo]-s->times[lo-1];
    if (span<=1e-9) { memcpy(out,s->frames+(size_t)lo*512,512); return; }
    float ratio=(float)fmin(fmax((t-s->times[lo-1])/span,0.0),1.0);
    const uint8_t *a=s->frames+(size_t)(lo-1)*512, *b=s->frames+(size_t)lo*512;
    for (int c=0;c<512;++c) {
        float delta=(float)b[c]-(float)a[c];
        float value=(float)a[c]+delta*ratio;
        value=ce_np_minf(ce_np_maxf(value,0.0f),255.0f);
        out[c]=(uint8_t)ce_vc_round_even((double)value);
    }
}

CE_API int ce_source_motion_force(const double* times, const uint8_t* frames, int count,
    double t, double duration, double a, double c, double d, const uint8_t* motor,
    const uint8_t* raw, uint8_t* out, double* shaped)
{
    ce_source_table table={times,frames,count};
    if (!_ce_source_table_valid(&table) || !motor || !raw || !out || !shaped) return -1;
    double st=_ce_motion_force_time(t,duration,a,c,d);
    memcpy(out,raw,512); *shaped=st;
    if (!count || fabs(st-t)<=1e-7) return 0;
    const uint8_t* sample=frames+(size_t)_ce_source_nearest(&table,st)*512;
    for (int i=0;i<512;++i) if (motor[i]) out[i]=sample[i];
    return 0;
}

CE_API int ce_source_motion_scores(const double* times, const uint8_t* frames, int count,
    double source_t, double start, double rate, double cap, const uint8_t* active, double* out)
{
    ce_source_table table={times,frames,count};
    if (!_ce_source_table_valid(&table) || !active || !out) return -1;
    int valid=0;
    for (int i=0;i<count;++i) valid += times[i]<=source_t+1e-9;
    if (rate<=1e-9) rate=1.0;
    for (int ch=0;ch<512;++ch) {
        if (!active[ch]) { out[ch]=-INFINITY; continue; }
        if (!valid) { out[ch]=start; continue; }
        double changed=fmax(0.0,times[0]);
        for (int i=1;i<valid;++i) if (frames[(size_t)i*512+ch]!=frames[(size_t)(i-1)*512+ch])
            changed=fmax(changed,times[i]);
        double score=start+changed/rate;
        out[ch]=score<cap ? score : cap;
    }
    return 0;
}

CE_API int ce_source_motion_scores_row(const void* changes,int itemsize,const uint8_t* active,
                                       double start,double rate,double cap,double* out)
{
    if (!active || !out || (itemsize!=0 && itemsize!=4 && itemsize!=8) || (itemsize && !changes)) return -1;
    if (rate<=1e-9) rate=1.0;
    for(int c=0;c<512;++c) {
        if (!active[c]) {out[c]=-INFINITY;continue;}
        if (!itemsize) {out[c]=start;continue;}
        double time=itemsize==4 ? ((const float*)changes)[c] : ((const double*)changes)[c];
        double score=start+time/rate;
        out[c]=isnan(score) || isnan(cap) ? NAN : fmin(score,cap);
    }
    return 0;
}

static double _ce_source_batch_start(const ce_source_remap* s,int group)
{
    double t=0.0;
    for (int i=0;i<group%s->batch_count;++i) t+=s->step+(s->pauses ? s->pauses[i] : 0.0);
    return t;
}

static double _ce_source_phase(const ce_source_remap* s)
{
    double delay=fmax(0.0,s->delay_in), fade=fmax(0.0,s->fade_in), total=delay+fade;
    return total>1e-6 ? delay/total : s->phase_amount/(1.0+s->phase_amount);
}

static double _ce_source_remap_time(const ce_source_remap* s,const ce_source_copy* r,
                                   double group_t,int no_delay)
{
    double local=fmax(0.0,s->local_t), end=fmax(0.0,s->duration-1e-4), t;
    if (no_delay) return s->cyclic ? fmod(local,fmax(1e-6,s->duration)) : fmin(local,end);
    if (group_t>=s->single && !s->cyclic) return end;
    t=fmax(0.0,s->source_t);
    if (r->phase && s->phase_enabled && s->single>1e-6 && s->duration>1e-6) {
        double scale=s->parallel ? 1.0 : 1.0-fmin(fmax(group_t/fmax(s->single,1e-6),0.0),1.0);
        t+=r->relative*fmax(0.0,s->duration)*_ce_source_phase(s)*scale;
        if (s->force_enabled) t=_ce_motion_force_time(t,s->duration,s->accel,s->cruise,s->decel);
    }
    if (s->cyclic && group_t>=s->single && s->duration>1e-6)
        t=fmod(fmax(0.0,s->source_t)+(group_t-s->single),s->duration);
    return fmin(fmax(t,0.0),end);
}

static int _ce_source_remap_valid(const ce_source_table* tables,int nt,
    const ce_source_copy* rows,int nr,const ce_source_remap* s)
{
    if (!s || nt<0 || nr<0 || s->batch_count<1 || (nt && !tables) || (nr && !rows)) return 0;
    for (int i=0;i<nt;++i) if (!_ce_source_table_valid(tables+i)) return 0;
    for (int i=0;i<nr;++i) {
        const ce_source_copy* r=rows+i;
        if (r->slot<0 || r->slot>=nt || r->group<0 || r->count<0 || r->count>512) return 0;
        if (r->target<0 || r->source<0 || r->target>512-r->count || r->source>512-r->count) return 0;
    }
    return 1;
}

CE_API int ce_source_motion_remap(const ce_source_table* tables,int nt,
    const ce_source_copy* rows,int nr,const ce_source_remap* s,const uint8_t* raw,uint8_t* out)
{
    if (!out || !_ce_source_remap_valid(tables,nt,rows,nr,s) || (s->seed && !raw)) return -1;
    ce_source_sample* cache=nr ? (ce_source_sample*)malloc((size_t)nr*sizeof(*cache)) : NULL;
    if (nr && !cache) return -2;
    if (s->seed) memcpy(out,raw,512); else memset(out,0,512);
    int used=0;
    for (int i=0;i<nr;++i) {
        const ce_source_copy* r=rows+i; const ce_source_table* table=tables+r->slot;
        int no_delay=r->phase && s->motor_no_delay && s->phase_amount<=1e-6 && s->cyclic;
        double start=no_delay ? 0.0 : _ce_source_batch_start(s,r->group);
        double local=fmax(0.0,s->local_t);
        if (local<start-1e-9 || !table->count || !r->count) continue;
        double t=_ce_source_remap_time(s,r,local-start,no_delay);
        int interp=r->phase && s->phase_amount>1e-6;
        int64_t key=ce_vc_round_even(t*10000.0);
        int j=0;
        for (;j<used;++j) if (cache[j].key==key && cache[j].slot==r->slot && cache[j].interp==interp) break;
        if (j==used) { cache[used++]=(ce_source_sample){key,r->slot,interp,t}; }
        uint8_t sampled[512];
        _ce_source_sample_frame(table,cache[j].first_t,interp,sampled);
        memcpy(out+r->target,sampled+r->source,(size_t)r->count);
    }
    free(cache); return 0;
}
