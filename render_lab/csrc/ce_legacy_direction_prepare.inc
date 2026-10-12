/* Retained K2 row preparation. Fixed int[16]/double[24], owned dynamic
 * values/float-vector inputs, and outputs of the exact declared lengths. */
static double _ce_legacy_lower(double lower,double value)
{
    return value>lower ? value : lower;
}

static void _ce_legacy_core(const int* i,const double* v,const double* pauses,double* out)
{
    double dur=_ce_prelude_positive(v[0]);
    double fi=_ce_prelude_positive(v[1]),fo=_ce_prelude_positive(v[2]);
    double di=_ce_prelude_positive(v[3]),dout=_ce_prelude_positive(v[4]);
    double single=_ce_prelude_positive(v[5]);
    int groups=i[2]<1 ? 1 : i[2];
    out[0]=dur;out[1]=fi;out[2]=fo;
    out[3]=dur<fi ? dur : fi;out[4]=dur<fo ? dur : fo;
    out[5]=di;out[6]=dout;
    out[7]=dur<di ? dur : di;out[8]=dur<dout ? dur : dout;
    out[9]=single;out[10]=_ce_prelude_positive(v[6]);
    out[11]=single>1e-4 ? single : _ce_legacy_lower(1e-6,out[3]);
    out[12]=single>1e-4 ? single : _ce_legacy_lower(1e-6,out[4]);
    out[13]=i[3] && groups>1 && single>1e-4;
    out[14]=groups;out[15]=_ce_py_sum_f64(pauses,i[0]);
    out[16]=i[3] && groups>1;out[17]=dur>0.0;
}

static void _ce_legacy_fixture(const int* i,const double* v,const double* pauses,double* out)
{
    int zero[16]={0};double stage[24]={0};
    ce_source_motion_clock(9,zero,v,NULL,stage,NULL);
    double per_group=_ce_legacy_lower(1e-6,stage[0]);
    double target=v[2]>1e-4 ? v[2] : v[4];
    double total=per_group*(double)i[3]+_ce_py_sum_f64(pauses,i[0]);
    double span=_ce_legacy_lower(1e-6,total);
    double scale=_ce_legacy_lower(1e-6,target/span);
    out[0]=per_group;out[1]=target;out[2]=span;out[3]=scale;
    out[4]=_ce_legacy_lower(1e-6,per_group*scale);
}

static void _ce_legacy_group_counts(const int* i,double* out)
{
    int count=i[2],groups=i[3]<1 ? 1 : i[3];
    if(count>0 && groups>count) groups=count;
    if(!count) groups=1;
    int width=count>1 ? count/groups+(count%groups!=0) : 1;
    out[0]=groups;out[1]=width;
}

CE_API int ce_legacy_direction_prepare(int op,const int* i,const double* v,
    const double* values,const float* vector,double* out,uint8_t* flags,float* result)
{
    if(op<0 || op>5 || !i || !v || !out || i[0]<0 || i[1]<0) return -1;
    if(i[0] && !values) return -1;
    switch(op) {
    case 0: _ce_legacy_core(i,v,values,out);break;
    case 1:
        if(i[3]<1) return -1;
        _ce_legacy_fixture(i,v,values,out);break;
    case 2: {
        if(i[1] && (!vector || !result)) return -1;
        float scale=(float)v[0];
        for(int n=0;n<i[1];++n) {float product=vector[n]*scale;result[n]=product;}
        break;
    }
    case 3:
        if(i[0] && !flags) return -1;
        out[0]=0.0;
        for(int n=0;n<i[0];++n) {
            flags[n]=fabs(values[n]-1.0)>1e-3;
            if(flags[n]) out[0]=1.0;
        }
        break;
    case 4:
        if(i[2]<0) return -1;
        _ce_legacy_group_counts(i,out);break;
    case 5: {
        if(i[1]%2 || (i[1] && (!vector || !result))) return -1;
        int count=i[1]/2;
        for(int n=0;n<count;++n) {
            result[n]=ce_np_minf(vector[n],vector[count+n]);
            result[count+n]=vector[count+n];
        }
        break;
    }
    }
    return 0;
}
