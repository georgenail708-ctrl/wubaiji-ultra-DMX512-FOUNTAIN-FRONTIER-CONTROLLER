/* Fixed scalar clock ABI: int[16], double[24], owned pause buffers. */
static double _ce_clock_mod(double t,double period)
{
    double result=fmod(t,period);
    return result<0.0 ? result+period : result;
}

static double _ce_clock_pause_sum(int n,const double* pauses)
{
    return _ce_py_sum_f64(pauses, n);
}

static void _ce_clock_active(int batches,int np,const double* pauses,
    double local,double single,double step,double* out)
{
    double start=0.0;
    local=fmax(0.0,local); single=fmax(0.0,single); step=fmax(0.0,step);
    for (int b=0;b<batches;++b) {
        if (local<=start+single+1e-9) {
            out[0]=b; out[1]=fmin(fmax(0.0,local-start),single); out[2]=0.0; return;
        }
        double next=start+step+(b<np ? fmax(0.0,pauses[b]) : 0.0);
        if (b+1<batches && local<next-1e-9) {
            out[0]=b; out[1]=single; out[2]=1.0; return;
        }
        start=next;
    }
    out[0]=batches-1; out[1]=single; out[2]=0.0;
}

static void _ce_clock_profile(const double* v,double* out)
{
    double a=fmax(1e-6,v[0]),c=fmax(1e-6,v[1]),d=fmax(1e-6,v[2]);
    double total=(a+c)+d;
    out[0]=a/total; out[1]=c/total; out[2]=d/total;
}

static void _ce_clock_window(const int* i,const double* v,const double* pauses,
                            double* out,double* pout)
{
    int batches=i[0],np=i[1];
    double duration=fmax(0.0,v[0]),single=v[1],delay=fmax(0.0,v[2]),transition=fmax(0.0,v[3]);
    double total=_ce_clock_pause_sum(np,pauses),scale=1.0,step=-1.0;
    out[2]=0.0;
    int wing=i[2];
    if (wing==2) { double candidate=delay+transition; if(candidate<=1e-4)candidate=delay; wing=candidate>1e-4; }
    if (wing) {
        single=transition>1e-4 ? transition : 1e-4;
        step=batches>1 ? fmax(0.0,delay-fmax(0.0,total))/(batches-1) : 0.0;
        double span=batches>1 ? step*(batches-1)+fmax(0.0,total)+single : single;
        out[4]=span;
        if (duration>1e-4 && span>duration+1e-6) {
            scale=duration/fmax(span,1e-9);
            single=fmax(1e-4,single*scale); step=fmax(0.0,step*scale); out[2]=1.0;
        }
    } else {
        double span=fmax(0.0,single*batches+total);
        if (duration>1e-4 && span>1e-6 && fabs(span-duration)>1e-6) {
            scale=duration/span; single=fmax(.001,single*scale);
        }
    }
    out[0]=single; out[1]=step; out[3]=scale;
    for (int p=0;p<np;++p) pout[p]=fmax(0.0,pauses[p]*scale);
}

static void _ce_clock_sequential(const int* i,const double* v,const double* pauses,double* out)
{
    double local=v[0],duration=v[1],rate=v[2],single=v[3],step=v[4];
    if (!i[0]) { out[0]=fmax(0.0,local*rate); out[1]=0.0; return; }
    double cap=fmax(0.0,duration-1e-4),span=step*fmax(0.0,i[1]-1)+single+_ce_clock_pause_sum(i[2],pauses);
    if (i[3] && i[5] && i[6]) {
        double period=fmax(1e-6,duration);
        out[0]=fmin(period-1e-4,_ce_clock_mod(local,period)); out[1]=0.0; return;
    }
    if (i[3]) {
        cap=fmin(cap,v[5]);
        if (span>1e-6 && local>=span) {
            out[0]=i[6] ? fmin(fmax(1e-6,duration)-1e-4,
                _ce_clock_mod(local,fmax(1e-6,duration))) : cap;
            out[1]=0.0; return;
        }
    }
    local=fmax(0.0,local);
    if (i[4] && span>1e-6 && local>span) local=_ce_clock_mod(local,span);
    double active[3];
    _ce_clock_active(i[1],i[2],pauses,local,single,step,active);
    double t=single>1e-6 && duration>1e-6 ? active[1]/single*duration : active[1]*rate;
    out[0]=fmin(fmax(0.0,t),cap); out[1]=active[2];
}

static void _ce_clock_run_mode(const int* i,const double* v,double* out)
{
    int mode=i[0],cycle=i[1] && i[2] && v[4]>1e-6;
    double t=fmax(0.0,v[0]),duration=fmax(0.0,v[1]),end=fmax(0.0,duration-1e-4);
    out[1]=1.0;
    if (!mode || duration<=1e-6) {
        out[0]=cycle && t>v[5] ? v[3]+_ce_clock_mod(t-v[3],v[4]) : fmin(t,end); return;
    }
    if (mode==3) { out[0]=_ce_clock_mod(t,duration); return; }
    if (mode==4) {
        double recorded=fmin(fmax(0.0,v[2]),duration);
        out[1]=recorded<=1e-6 || t<=recorded+1e-9;
        out[0]=fmin(t,fmin(end,recorded ? recorded : end)); return;
    }
    double span=fmax(1e-6,duration);
    if (cycle && v[2]>1e-6) span=v[2];
    end=fmax(0.0,span-1e-4);
    if (t>span && cycle && t>v[5]) t=fmin(fmax(0.0,v[3]+_ce_clock_mod(t-v[3],v[4])),span);
    if (mode==1) { out[0]=fmax(0.0,end-fmin(t,end)); return; }
    double phase=fmin(t/span,1.0)*2.0;
    double mapped=phase<=1.0 ? phase : 2.0-phase;
    out[0]=fmin(fmax(0.0,mapped*span),end);
}

static void _ce_clock_phase(const int* i,const double* v,double* out)
{
    double manual=i[0] ? fmax(0.0,v[0]) : 0.0,force=i[1] ? fmax(0.0,v[1]) : 0.0;
    double amount=fmin(10.0,manual+force),delay=fmax(0.0,v[2]),fade=fmax(0.0,v[3]);
    double total=delay+fade;
    out[0]=amount; out[1]=total>1e-6 ? fmin(1.0,fmax(0.0,delay/total)) : amount/(1.0+amount);
}

static void _ce_clock_extent(const int* i,const double* v,double* out)
{
    double rate=fmax(.001,v[1] ? v[1] : 1.0),duration=fmax(0.0,v[0]),source=fmax(0.0,v[2]);
    double extent=duration*rate;
    out[0]=rate; out[1]=source>0.0 ? source : extent;
    out[2]=source>0.0 ? extent>source+1e-6 : i[0] && extent>v[3]+.05;
}

static void _ce_clock_direction(const int* i,const double* v,const double* pauses,double* out)
{
    double duration=fmax(0.0,v[0]),fi=fmin(fmax(0.0,v[1]),duration),fo=fmin(fmax(0.0,v[2]),duration);
    double di=fmin(fmax(0.0,v[3]),duration),dout=fmin(fmax(0.0,v[4]),duration),local=v[5];
    double span=fmax(0.0,v[6]*fmax(0.0,i[3]-1)+v[7]+_ce_clock_pause_sum(i[4],pauses));
    out[0]=duration;out[1]=fi;out[2]=fo;out[3]=di;out[4]=dout;
    out[5]=i[0] && ((fi>1e-4 && local<fi) || (di>1e-4 && local<di+fi)
                    || (i[2] && local<fmax(1e-4,span)));
    out[6]=i[1] && ((fo>1e-4 && local>duration-fo) || (dout>1e-4 && local>duration-(dout+fo)));
    out[7]=span;
}

static double _ce_clock_sample_endpoint(double duration)
{
    double endpoint=duration-1e-4;
    return endpoint>0.0 ? endpoint : 0.0;
}

static int _ce_clock_sample_floor(int count,const double* times,double source)
{
    double time=source>0.0 ? source : 0.0;
    double threshold=time+1e-9;
    int lo=0,hi=count;
    while(lo<hi) {
        int mid=lo+(hi-lo)/2;
        if(!isnan(times[mid]) && times[mid]<=threshold) lo=mid+1;
        else hi=mid;
    }
    return lo ? lo-1 : 0;
}

static double _ce_clock_auto_hold(int count,const double* times,double position)
{
    double local=position>0.0 ? position : 0.0;
    if(count<=1) return local;
    double first=0.0,last=0.0;
    int found=0;
    for(int p=0;p<count;++p) {
        double value=times[p];
        if(!(value>=0.0)) continue;
        if(!found) first=last=value;
        else {
            if(value<first) first=value;
            if(value>last) last=value;
        }
        ++found;
    }
    if(found<=1) return local;
    double period=last-first;
    if(period<=1e-6 || local<=last) return local;
    return first+_ce_clock_mod(local-first,period);
}

static int _ce_clock_scalar_domain(const int* i,double progress)
{
    if(i[1]<100 || i[2]!=2 || i[3]) return 0;
    return _ce_motion_library(i[1],progress)<0.0;
}

static void _ce_clock_scalar_result(int strategy,double* out)
{
    if(strategy!=4) return;
    if(isnan(out[0])) out[1]=2.0;
    else if(out[0]==0.0) out[0]=0.0;
}

static void _ce_clock_scalar_curve(const int* i,const double* v,double* out)
{
    double progress=v[0];
    if(progress<0.0) progress=0.0;
    else if(progress>1.0) progress=1.0;
    out[1]=0.0;
    if(!i[0]) {
        out[0]=progress*progress*(3.0-2.0*progress);
        return;
    }
    if(i[1]==0 && progress==0.0) progress=0.0;
    if(_ce_clock_scalar_domain(i,progress)) {
        out[0]=NAN;out[1]=1.0;return;
    }
    out[0]=_ce_motion_curve64(progress,i[3],i[1],i[2]);
    _ce_clock_scalar_result(i[2],out);
}

static double _ce_clock_fade_progress(double progress)
{
    double bounded=progress<1.0 ? progress : 1.0;
    return bounded>0.0 ? bounded : 0.0;
}

static double _ce_clock_fade_window(double raw,double duration)
{
    if(duration<raw) raw=duration;
    return raw>0.0 ? raw : 0.0;
}

static void _ce_clock_scalar_fade(const int* i,const double* v,double* out)
{
    double duration=v[0]>0.0 ? v[0] : 0.0;
    out[0]=1.0;out[1]=0.0;
    if(duration<=0.0) return;
    double fi=_ce_clock_fade_window(v[1],duration);
    double fo=_ce_clock_fade_window(v[2],duration);
    if(fi>1e-6 && v[3]<fi) {
        int flags[4]={i[0],i[1],i[3],1};
        double progress[1]={_ce_clock_fade_progress(v[3]/fi)};
        _ce_clock_scalar_curve(flags,progress,out);
        if(out[1]) return;
    }
    if(fo>1e-6 && v[3]>duration-fo) {
        int flags[4]={i[0],i[2],i[4],0};
        double progress[1]={_ce_clock_fade_progress((duration-v[3])/fo)};
        double next[2];
        _ce_clock_scalar_curve(flags,progress,next);
        if(next[1]) {out[0]=next[0];out[1]=next[1];return;}
        if(next[0]<out[0]) out[0]=next[0];
    }
}

static void _ce_clock_vector_curve(const int* i,const double* input,double* out,double* result)
{
    int count=i[0],custom=i[5],width=i[7];
    out[0]=count;out[1]=0.0;
    for(int p=0;p<count;++p) {
        float t=(float)input[p];
        t=ce_np_minf(ce_np_maxf(t,0.0f),1.0f);
        if(!i[1] && custom) {
            float scaled=t*255.0f;
            int index=isfinite(scaled) ? (int)scaled : 0;
            if(index<0) index=0;
            else if(index>255) index=255;
            if(index>=i[6]) {out[1]=1.0;out[2]=index;return;}
            for(int column=0;column<width;++column)
                result[(size_t)p*width+column]=(float)input[count+(size_t)index*width+column];
        } else {
            float gain=i[1] ? _ce_motion_curve32(t,i[4],i[2],i[3])
                           : t*t*(3.0f-2.0f*t);
            result[p]=(double)gain;
        }
    }
}

static int _ce_clock_sampling(int op,const int* i,const double* v,
    const double* input,double* out,double* result)
{
    if(op==16) {out[0]=_ce_clock_sample_endpoint(v[0]);return 0;}
    if(op==20) {_ce_clock_scalar_curve(i,v,out);return 0;}
    if(i[0]<0 || (i[0] && !input)) return -1;
    switch(op) {
    case 17: out[0]=_ce_clock_sample_floor(i[0],input,v[0]);break;
    case 18: {
        double source=v[0]>0.0 ? v[0] : 0.0;
        ce_source_table table={input,NULL,i[0]};
        out[0]=i[0] ? _ce_source_nearest(&table,source) : -1;
        break;
    }
    case 19: out[0]=_ce_clock_auto_hold(i[0],input,v[0]);break;
    case 21:
        if((i[0] && !result) || i[6]<0 || i[7]<1) return -1;
        _ce_clock_vector_curve(i,input,out,result);break;
    default: return -1;
    }
    return 0;
}

#include "ce_source_motion_prelude.inc"

/* The render include follows this include in the same translation unit. */
static void _ce_rt_windows(const int* i,const double* v,double* out);
CE_API int ce_source_motion_clock(int op,const int* ints,const double* values,
    const double* pauses,double* out,double* pause_out);

static double _ce_clock_retained_tie(const double* values)
{
    int flags[16]={0};
    double input[24]={0},report[24]={0};
    input[1]=values[2];input[3]=values[0];input[4]=values[1];
    ce_source_motion_clock(14,flags,input,NULL,report,NULL);
    return report[2];
}

static void _ce_clock_retained_table(const int* ints,const double* values,double* out)
{
    int priority_flags[16]={0},memo_flags[16]={0};
    double windows[24]={0},priority[24]={0},memo[24]={0};
    priority_flags[0]=memo_flags[0]=ints[0];
    priority_flags[3]=ints[2];
    memo_flags[2]=memo_flags[3]=ints[1];
    windows[0]=values[3];windows[1]=values[5];windows[2]=values[6];
    windows[3]=values[4];windows[4]=values[7];windows[5]=values[8];
    windows[8]=values[9];
    _ce_rt_windows(priority_flags,windows,priority);
    _ce_rt_windows(memo_flags,windows,memo);
    out[0]=_ce_clock_retained_tie(values);
    out[1]=priority[13];
    out[2]=(priority[6] || priority[7]) ? out[0]+out[1] : out[0];
    out[3]=memo[2];out[4]=memo[0];out[5]=memo[1];
    out[6]=memo[3];out[7]=memo[4];
    out[8]=memo[6];out[9]=memo[7];out[10]=memo[9];
    out[11]=memo[5];out[12]=memo[14];
}

static void _ce_clock_retained(int op,const int* ints,const double* values,double* out)
{
    for(int slot=0;slot<24;++slot) out[slot]=0.0;
    switch(op) {
    case 26: {
        double position=values[0]*values[1];
        out[0]=position<values[2] ? position : values[2];
        break;
    }
    case 27:_ce_clock_retained_table(ints,values,out);break;
    case 28:out[0]=ints[0] && values[0]<values[1]-1e-9;break;
    case 29:out[0]=(values[0]+1.0)*1e9;break;
    }
}

static void _ce_clock_cache_index(const double* values,double* out)
{
    for(int slot=0;slot<24;++slot) out[slot]=0.0;
    double interval=values[0]>.001 ? values[0] : .001;
    double end=values[1]>0.0 ? values[1] : 0.0;
    double position=values[2]>0.0 ? values[2] : 0.0;
    if(end<position) position=end;
    out[1]=interval;out[2]=end;out[3]=position;
    double ratio=position/interval;
    if(isnan(ratio)) {out[4]=1.0;return;}
    if(isinf(ratio)) {out[4]=2.0;return;}
    double rounded=floor(ratio),fraction=ratio-rounded;
    if(fraction>.5 || (fraction==.5 && fmod(rounded,2.0)!=0.0)) rounded+=1.0;
    out[0]=rounded;
}

CE_API int ce_source_motion_clock(int op,const int* ints,const double* values,
    const double* pauses,double* out,double* pause_out)
{
    if (op<0 || op>31 || !ints || !values || !out) return -1;
    if(op==31) {
        if(ints[0]<0 || ints[0]>3) return -1;
        for(int slot=0;slot<24;++slot) out[slot]=0.0;
        if(ints[0]==3) {
            double start=values[0]*1e6;
            double order=(values[1]+1.0)*1e-3;
            out[0]=start+order;
        } else {
            double value=values[0];
            if(ints[0]==2) value=value-values[1];
            if(ints[0]) value=value-1e-4;
            out[0]=value>0.0 ? value : 0.0;
        }
        return 0;
    }
    if(op==30) {_ce_clock_cache_index(values,out);return 0;}
    if(op>=26) {_ce_clock_retained(op,ints,values,out);return 0;}
    if(op==25) {_ce_clock_scalar_fade(ints,values,out);return 0;}
    if(op==22) return _ce_prelude_normalize(ints,values,pauses,out,pause_out);
    if(op==23) return _ce_prelude_offsets(ints,values,pauses,out,pause_out);
    if(op==24) return _ce_prelude_gate(ints,values,pauses,out,pause_out);
    if(op>=16) return _ce_clock_sampling(op,ints,values,pauses,out,pause_out);
    if (op==8 && (ints[3]<1 || ints[4]<0 || (ints[4] && !pauses))) return -1;
    if ((op==10 || op==11 || op==13) && (ints[0]<0 || (ints[0] && !pauses))) return -1;
    if (op>=1 && op<=3) {
        int batches=op==3 ? ints[1] : ints[0],np=op==3 ? ints[2] : ints[1];
        if (batches<1 || np<0 || (np && !pauses) || (op==1 && np && !pause_out)) return -1;
    }
    switch (op) {
    case 0: _ce_clock_profile(values,out); break;
    case 1: _ce_clock_window(ints,values,pauses,out,pause_out); break;
    case 2:
        _ce_clock_active(ints[0],ints[1],pauses,values[0],values[1],values[2],out);
        out[3]=out[1]>=values[1]-1e-6 ? out[0] : out[0]-1.0; break;
    case 3: _ce_clock_sequential(ints,values,pauses,out); break;
    case 4:
        if (ints[0]<0 || ints[0]>4) return -1;
        _ce_clock_run_mode(ints,values,out); break;
    case 5: _ce_clock_phase(ints,values,out); break;
    case 6: _ce_clock_extent(ints,values,out); break;
    case 7:
        out[0]=ints[0] ? _ce_motion_force_time(values[0],fmax(0.0,values[1]),
                                             values[2],values[3],values[4]) : fmax(0.0,values[0]); break;
    case 8: _ce_clock_direction(ints,values,pauses,out); break;
    case 9:
        if (values[0]>1e-4) out[0]=values[0];
        else if (fmax(values[1],values[2])>1e-4) out[0]=fmax(1e-6,fmax(values[1],values[2]));
        else out[0]=fmax(1e-6,fmax(fmax(values[3],values[4]),values[5]));
        break;
    case 10:
        out[1]=_ce_clock_pause_sum(ints[0],pauses);
        out[0]=fmax(0.0,values[0]*fmax(0.0,ints[1]-1)+values[1]+out[1]); break;
    case 11:
        out[3]=0.0;
        if (ints[0]>1) {
            double lo=0.0,hi=0.0;int found=0;
            for(int p=0;p<ints[0];++p) {
                if(pauses[p]<0.0)continue;
                if(!found){lo=hi=pauses[p];found=1;}
                else {if(pauses[p]<lo)lo=pauses[p];if(pauses[p]>hi)hi=pauses[p];}
            }
            out[0]=lo;out[1]=hi-lo;out[2]=hi;out[3]=found && out[1]>1e-6;
        }
        break;
    case 12: out[0]=values[0]+fmax(0.0,values[1]); break;
    case 13:
        out[1]=ints[1] ? values[0] : 0.0;
        if(!ints[1]) for(int p=0;p<ints[0];++p) out[1]=fmax(out[1],fmax(0.0,pauses[p]));
        out[0]=fmin(fmax(0.0,out[1]),fmax(0.0,values[1])); break;
    case 14:
        out[0]=values[0]>1e-9 ? values[0] : 1.0;
        out[1]=ints[0] && ints[1] ? values[1]+fmax(0.0,values[2]) : INFINITY;
        out[2]=values[3]*1e9+(values[4]+1.0)*1e-3;
        out[2]=out[2]+values[1]*1e-4;
        out[3]=(values[5]+1.0)*1e12;
        break;
    case 15: {
        double interval=values[0]/1000.0;
        if(interval<1e-4) interval=0.025;
        if(ints[0]) {
            double delta=values[1]-values[2];
            if(delta>=1e-4 && delta<=0.2 && delta>interval) interval=delta;
        }
        if(0.005>interval) interval=0.005;
        if(0.08<interval) interval=0.08;
        out[0]=interval; break;
    }
    }
    return 0;
}
