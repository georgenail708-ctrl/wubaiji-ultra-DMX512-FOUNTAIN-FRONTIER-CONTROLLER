typedef struct {
    const float *gi_in, *gp_in, *gs_in, *gi_out, *gp_out, *gs_out, *ease_lut;
    const int *span_idx;
    const uint8_t *motor;
    const double *pauses;
    int K, batch_count, mode, te, curve_in, curve_out, strategy_in, strategy_out;
    int is_in, is_out, motor_no_delay, completed_enabled, ratio_nondefault;
    double local_t, duration, fade_in, fade_out, delay_in, delay_out;
    double single, step, scale_in, scale_out;
} ce_source_direction;

CE_API int ce_source_motion_abi(void) { return 1; }
CE_API int ce_source_motion_direction_size(void) { return (int)sizeof(ce_source_direction); }

static double _ce_motion_offset(const ce_source_direction* s, int batch, double step)
{
    double cursor = 0.0;
    for (int b = 0; b < batch; ++b) {
        cursor += step;
        cursor += s->pauses ? s->pauses[b] : 0.0;
    }
    return cursor;
}

static double _ce_motion_global(const ce_source_direction* s, int skip_in, int skip_out)
{
    return ce_source_motion_fade(s->duration, skip_in ? 0.0 : s->fade_in,
        skip_out ? 0.0 : s->fade_out, s->local_t, s->te, s->curve_in, s->curve_out,
        s->strategy_in, s->strategy_out);
}

static float _ce_motion_gain(const ce_source_direction* s, float t, int in)
{
    t = ce_np_minf(ce_np_maxf(t, 0.0f), 1.0f);
    if (s->te) return _ce_motion_curve32(t, in, in ? s->curve_in : s->curve_out,
                                        in ? s->strategy_in : s->strategy_out);
    if (!s->ease_lut) return t * t * (3.0f - 2.0f * t);
    int idx = isfinite(t) ? (int)(t * 255.0f) : 0;
    idx = idx < 0 ? 0 : (idx > 255 ? 255 : idx);
    return s->ease_lut[idx];
}

static float _ce_motion_ramp64(const ce_source_direction* s, double start, double win, int in)
{
    double end = start + win;
    double numerator = in ? s->local_t - start : end - s->local_t;
    float t = (float)(numerator / (win > 1e-6 ? win : 1e-6));
    float g = _ce_motion_gain(s, t, in);
    if (s->local_t < start) return in ? 0.0f : 1.0f;
    if (s->local_t >= end) return in ? 1.0f : 0.0f;
    return g;
}

static float _ce_motion_ramp32(const ce_source_direction* s, float start, float win,
                              double win64, int in)
{
    float end = start + win, lt = (float)s->local_t;
    float num = in ? lt - start : end - lt;
    float t = win64 > 0.0 ? (float)((double)num / win64)
                          : num / ce_np_maxf(win, 1e-6f);
    float g = _ce_motion_gain(s, t, in);
    if (lt < start) return in ? 0.0f : 1.0f;
    if (lt >= end) return in ? 1.0f : 0.0f;
    return g;
}

static float _ce_motion_parallel_edge(const ce_source_direction* s,int i,int in,int no_delay)
{
        if (!(in ? s->is_in : s->is_out)) return 1.0f;
        const float* gp = in ? s->gp_in : s->gp_out;
        const float* gs = in ? s->gs_in : s->gs_out;
        double fade = (in ? s->fade_in * s->scale_in : s->fade_out * s->scale_out);
        if (fade < 0.0) fade = 0.0;
        double delay = no_delay ? 0.0 : (in ? s->delay_in : s->delay_out);
        if (!(fade > 1e-4 || delay > 1e-4)) return 1.0f;
        float start, win;
        double win64 = 0.0;
        if (delay > 1e-4) {
            double total = delay + fade;
            if (s->mode != 3 && s->mode != 4 && s->duration > 1e-4 && total > s->duration + 1e-6) {
                double ratio = s->duration / fmax(total, 1e-9);
                delay *= ratio; fade *= ratio;
            }
            start = gp[i] * (float)delay;
            if (!in) start = (float)(s->duration - (delay + fade)) + start;
            win64 = fmax(1e-6, fade); win = (float)win64;
        } else {
            if(s->mode==3 || s->mode==4) {
                win64=fade/fmax(gs[i],1.0);win=(float)win64;
                start=gp[i]*(float)fmax(0.0,fade-win64);
            } else {
                win = (float)fade / ce_np_maxf(gs[i], 1.0f);
                start = gp[i] * ce_np_maxf(0.0f, (float)fade - win);
            }
            if (!in) start = (float)(s->duration - fade) + start;
        }
        return _ce_motion_ramp32(s, start, win, win64, in);
}

static float _ce_motion_parallel(const ce_source_direction* s, int i, int no_delay)
{
    return ce_np_minf(_ce_motion_parallel_edge(s,i,1,no_delay),
                      _ce_motion_parallel_edge(s,i,0,no_delay));
}

static double _ce_motion_rank_at(const ce_source_direction* s,int i,double gmax,double nb)
{
    int gi=(int)s->gi_in[i];
    double inside=s->gs_in[i]>1.0f ? (double)s->gp_in[i]*fmax(1.0,(double)s->gs_in[i]-1.0) : 0.0;
    return ((gi%s->batch_count)*nb+gi/s->batch_count)*gmax+inside;
}

static void _ce_motion_rank_bounds(const ce_source_direction* s,double* bounds)
{
    double gmax = 1.0, nb = 0.0;
    for (int k = 0; k < s->K; ++k) {
        if ((double)s->gs_in[k] > gmax) gmax = s->gs_in[k];
        double ordinal = (int)s->gi_in[k] / s->batch_count;
        if (ordinal + 1.0 > nb) nb = ordinal + 1.0;
    }
    double rmax = 0.0;
    for (int k = 0; k < s->K; ++k) {
        double rank = _ce_motion_rank_at(s,k,gmax,nb);
        if (rank > rmax) rmax = rank;
    }
    bounds[0]=gmax; bounds[1]=nb; bounds[2]=rmax;
}

static float _ce_motion_sequential_in(const ce_source_direction* s, int i, double step,
                                      const double* bounds)
{
    int bi = (int)s->gi_in[i] % s->batch_count;
    if (s->mode == 3) {
        if(s->delay_in>1e-4) return _ce_motion_parallel_edge(s,i,1,0);
        double pg=s->single>1e-4 ? s->single : s->fade_in;
        pg=fmax(1e-6,pg);
        float win = (float)pg / ce_np_maxf(s->gs_in[i], 1.0f);
        float start = (float)_ce_motion_offset(s, bi, pg)
            + s->gp_in[i] * ce_np_maxf(0.0f, (float)pg - win);
        return _ce_motion_ramp32(s, start, win, 0.0, 1);
    }
    double win = fmax(1e-6, s->single * s->scale_in);
    if (step > 1e-9) {
        double rmax=bounds[2], rank = _ce_motion_rank_at(s,i,bounds[0],bounds[1]);
        double latency = (float)_ce_motion_offset(s, s->batch_count - 1, step);
        double start = rmax > 1e-9 ? latency * rank / rmax : 0.0;
        return _ce_motion_ramp64(s, start, win, 1);
    }
    float start = (float)_ce_motion_offset(s, bi, step);
    /* numpy f32 t_start + scalar win gives f32 end; division is f64. */
    return _ce_motion_ramp32(s, start, (float)win, win, 1);
}

static float _ce_motion_sequential_out(const ce_source_direction* s, int i, double step)
{
    if(s->mode==3) {
        if(s->delay_out>1e-4) return _ce_motion_parallel_edge(s,i,0,0);
        double pg=fmax(1e-6,s->single>1e-4 ? s->single : s->fade_out);
        int bi=(int)s->gi_out[i]%s->batch_count;
        float win=(float)pg/ce_np_maxf(s->gs_out[i],1.0f);
        float relative=(float)_ce_motion_offset(s,bi,pg)
            +s->gp_out[i]*ce_np_maxf(0.0f,(float)pg-win);
        float start=(float)(s->duration-s->fade_out)+relative;
        return _ce_motion_ramp32(s,start,win,0.0,0);
    }
    double target = s->delay_out > 1e-4 ? s->delay_out : s->fade_out;
    double span = _ce_motion_offset(s, s->batch_count - 1, step) + s->single;
    double scale = fmax(1e-6, target / fmax(1e-6, span));
    double pg = fmax(1e-6, s->single * scale);
    int bi = (int)s->gi_out[i] % s->batch_count;
    float win = (float)(pg * s->scale_out) / ce_np_maxf(s->gs_out[i], 1.0f);
    float offset = (float)_ce_motion_offset(s, bi, step) * (float)scale;
    float relative = offset + s->gp_out[i] * ce_np_maxf(0.0f, (float)pg - win);
    float start = (float)(s->duration - target) + relative;
    return _ce_motion_ramp32(s, start, win, 0.0, 0);
}

static float _ce_motion_sequential(const ce_source_direction* s, int i, int no_delay,
                                   const double* bounds)
{
    double step = s->step < 0.0 ? s->single : s->step;
    if (no_delay && s->step >= 0.0) step = 0.0;
    float gain = 1.0f;
    if (s->is_in) gain = _ce_motion_sequential_in(s, i, step,bounds);
    int out = s->is_out && (s->fade_out > 1e-4 || s->delay_out > 1e-4);
    if (out) gain = ce_np_minf(gain, _ce_motion_sequential_out(s, i, step));
    return s->mode==3 ? gain : gain * (float)_ce_motion_global(s, s->is_in, out);
}

static int _ce_motion_completed(const ce_source_direction* s)
{
    if (!s->completed_enabled) return -1;
    double step = s->step < 0.0 ? s->single : s->step;
    double lt = s->local_t < 0.0 ? 0.0 : s->local_t;
    for (int b = 0; b < s->batch_count; ++b) {
        double start = _ce_motion_offset(s, b, step);
        if (lt <= start + s->single + 1e-9) {
            double gt = lt - start;
            gt = gt < 0.0 ? 0.0 : (gt > s->single ? s->single : gt);
            return gt >= s->single - 1e-6 ? b : b - 1;
        }
        if (b + 1 < s->batch_count && lt < _ce_motion_offset(s, b + 1, step) - 1e-9) return b;
    }
    return s->batch_count - 1;
}

static int _ce_motion_direction_valid(const ce_source_direction* s)
{
    if (!s || s->K < 0 || s->batch_count < 1 || s->mode < 0 || s->mode > 4) return 0;
    if (!s->K) return !s->completed_enabled;
    if (!s->mode && !s->completed_enabled) return 1;
    if (!s->span_idx) return 0;
    if ((s->is_in || s->completed_enabled) && (!s->gi_in || !s->gp_in || !s->gs_in)) return 0;
    if (s->is_out && (!s->gi_out || !s->gp_out || !s->gs_out)) return 0;
    for (int c = 0; c < 512; ++c) if (s->span_idx[c] >= s->K) return 0;
    for (int k = 0; k < s->K; ++k) {
        if (s->gi_in && (!isfinite(s->gi_in[k]) || s->gi_in[k] < 0.0f)) return 0;
        if (s->gi_out && (!isfinite(s->gi_out[k]) || s->gi_out[k] < 0.0f)) return 0;
    }
    return 1;
}

CE_API int ce_source_motion_direction(const ce_source_direction* s,
                                     float* gains, uint8_t* completed, uint8_t* arrived)
{
    if (!gains || !completed || !arrived || !_ce_motion_direction_valid(s)) return -1;
    float* fi = s->K ? (float*)malloc((size_t)s->K * sizeof(float) * 2) : NULL;
    if (s->K && !fi) return -2;
    int cb = _ce_motion_completed(s);
    float global = (float)_ce_motion_global(s, 0, 0);
    int scalar = !s->K || !s->mode || (!s->is_in && !s->is_out);
    double bounds[3]={1.0,0.0,0.0};
    if (!scalar && s->mode==2 && s->is_in) _ce_motion_rank_bounds(s,bounds);
    for (int i = 0; i < s->K; ++i) {
        fi[i] = scalar ? global : (s->ratio_nondefault ? 1.0f :
            ((s->mode == 2 || s->mode == 3) ? _ce_motion_sequential(s, i, 0,bounds)
                                                       : _ce_motion_parallel(s, i, 0)));
        fi[s->K + i] = fi[i];
        if (!scalar && !s->ratio_nondefault && s->motor_no_delay)
            fi[s->K + i] = (s->mode == 2 || s->mode == 3)
                ? _ce_motion_sequential(s, i, 1,bounds) : _ce_motion_parallel(s, i, 1);
    }
    for (int c = 0; c < 512; ++c) {
        int i = scalar ? -1 : s->span_idx[c];
        gains[c] = scalar ? global : (i < 0 ? 1.0f : fi[i +
            (s->motor && s->motor[c] && s->motor_no_delay ? s->K : 0)]);
        completed[c] = cb >= 0 && s->gi_in && s->span_idx && s->span_idx[c] >= 0
            && ((int)s->gi_in[s->span_idx[c]] % s->batch_count <= cb);
        arrived[c] = gains[c] > 1e-6f;
    }
    free(fi);
    return 0;
}

/* The public no-fixture fallback uses stable contiguous groups. */
CE_API int ce_source_motion_groups(const float* positions,int count,int groups,int parallel,
                                   float* gi,float* gp,float* gs)
{
    if (count<0 || groups<1 || (count && (!positions || !gi || !gp || !gs))) return -1;
    if (!count) return 0;
    if (groups>count) groups=count;
    int width=count/groups+(count%groups!=0);
    int* order=(int*)malloc((size_t)width*sizeof(int));
    if (!order) return -2;
    for (int g=0;g<groups;++g) {
        int start=g*width,end=start+width;
        if (end>count) end=count;
        int n=end-start;
        if (n<=0) continue;
        for (int j=0;j<n;++j) {
            int at=j,index=start+j;
            while (at>0 && positions[order[at-1]]>positions[index]) {order[at]=order[at-1];--at;}
            order[at]=index;
            gi[index]=(float)g;gs[index]=(float)(parallel ? width : n);
        }
        for (int j=0;j<n;++j) gp[order[j]]=n>1 ? (float)((double)j/(n-1)) : 0.0f;
    }
    free(order);return 0;
}
