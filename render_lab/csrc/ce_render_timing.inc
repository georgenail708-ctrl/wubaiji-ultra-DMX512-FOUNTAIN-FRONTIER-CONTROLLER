/* Installed render/P7 scalar stages. Original container/state order is host-owned. */

static double _ce_rt_max_left(double left, double right)
{
    return right>left ? right : left;
}

static double _ce_rt_min_left(double left, double right)
{
    return right<left ? right : left;
}

static double _ce_rt_nonnegative(double value)
{
    return value>0.0 ? value : 0.0;
}

static void _ce_rt_local(const int* i,const double* v,double* out)
{
    double local=v[0]-v[1],duration=v[2];
    out[0]=local;out[2]=-1.0;
    if(!i[0] || !(duration>0.0)) return;
    double enter=_ce_rt_min_left(duration*0.25,_ce_rt_max_left(1e-4,v[3]*1.35));
    double exit=_ce_rt_min_left(duration*0.25,_ce_rt_max_left(1e-4,v[3]*1.75));
    out[3]=enter;out[4]=exit;out[2]=0.0;
    if(local>=duration-(i[1] ? exit : enter)) {
        out[0]=_ce_clock_sample_endpoint(duration);out[1]=1.0;out[2]=1.0;
    } else if(i[1] && local>=duration-exit) {
        out[0]=_ce_clock_sample_endpoint(duration);out[1]=1.0;out[2]=2.0;
    }
}

static void _ce_rt_active_source(const int* i,const double* v,double* out)
{
    double source=v[0],duration=v[1];
    if(i[0]) {out[0]=_ce_clock_sample_endpoint(duration);return;}
    out[0]=i[2] ? source : _ce_rt_min_left(source,_ce_clock_sample_endpoint(duration));
    if(!i[1]) return;
    double fade=_ce_rt_nonnegative(v[4]);
    if(fade>v[3]+1e-9) {
        out[1]=1.0;
        out[2]=_ce_rt_min_left(1.0,_ce_rt_max_left(0.0,v[2]/fade));
    }
}

static void _ce_rt_held_source(const int* i,const double* v,double* out)
{
    double elapsed=_ce_rt_nonnegative(v[0]-(v[1]+v[2]));
    out[0]=elapsed;
    out[1]=i[0] ? _ce_rt_nonnegative(v[3]-1e-4+elapsed*v[4]) : _ce_clock_sample_endpoint(v[3]);
    out[2]=v[2];out[3]=1.0;
}

static int _ce_rt_fade_out(const int* i,const double* v,const double* out)
{
    if(i[0] || !(out[2]>0.0)) return 0;
    return (out[1]>1e-4 && v[0]>out[2]-out[1]) ||
        (out[4]>1e-4 && i[3] && v[0]>out[2]-(out[1]+out[4]));
}

static void _ce_rt_windows(const int* i,const double* v,double* out)
{
    for(int k=0;k<5;k++) out[k]=_ce_rt_nonnegative(v[k+1]);
    int hold=i[0]!=0;
    out[5]=hold && v[0]>=out[2]-1e-9;
    out[6]=!hold && out[0]>1e-4 && v[0]<out[0];
    out[7]=_ce_rt_fade_out(i,v,out);
    out[8]=!hold && i[1] && i[2] && v[0]<_ce_rt_max_left(1e-4,v[6]);
    out[9]=!hold && out[3]>1e-4 && i[2] && v[0]<out[0]+out[3];
    out[10]=!hold && !i[1] && v[7]>1e-6 && i[2] && i[4];
    out[11]=out[6] || out[8] || out[9];
    out[12]=out[6] || out[7] || out[8] || out[9];
    if(!hold && out[12]) out[13]=(v[8]+1.0)*1e9;
    out[14]=!out[6] && !out[7] && !out[8] && !out[9] && !out[10] && !i[5] && v[9]<=1e-6;
}

static void _ce_rt_bucket(const double* v,double* out)
{
    if(v[1]==0.0) {out[23]=3.0;return;}
    double value=v[0]/v[1];
    if(isnan(value)) {out[23]=1.0;return;}
    if(isinf(value)) {out[23]=2.0;return;}
    out[0]=floor(value);
}

#include "ce_render_timing_cold.inc"

CE_API int ce_render_timing_eval(int op,const int* flags,const double* values,double* output)
{
    if(!flags || !values || !output || op<0 || op>9) return -1;
    for(int k=0;k<24;k++) output[k]=0.0;
    switch(op) {
    case 0:_ce_rt_local(flags,values,output);break;
    case 1:_ce_rt_active_source(flags,values,output);break;
    case 2:_ce_rt_held_source(flags,values,output);break;
    case 3:_ce_rt_windows(flags,values,output);break;
    case 4:output[0]=_ce_rt_nonnegative(values[0]);break;
    case 5:_ce_rt_bucket(values,output);break;
    case 6:output[0]=values[1]<=values[0] && values[0]<values[2];break;
    case 7:_ce_rt_cold_span(flags,values,output);break;
    case 8:_ce_rt_cold_buckets(values,output);break;
    case 9:_ce_rt_index_elapsed(values,output);break;
    }
    return 0;
}
