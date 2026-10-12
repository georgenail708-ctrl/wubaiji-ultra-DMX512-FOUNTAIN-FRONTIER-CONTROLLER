/* Original no-fixture direction positions; random ordering stays authoritative. */
static float _ce_motion_zigzag(int at,int count)
{
    int quarter=count>=4 ? count/4 : 1;
    int divisor=quarter>1 ? quarter-1 : 1;
    double local=(double)(at%quarter)/divisor;
    return (float)((at/quarter)%2 ? 1.0-local : local);
}

static float _ce_motion_position(float value,int at,int count,int mode,const float* order)
{
    float delta;
    switch(mode) {
    case 1: return 1.0f-value;
    case 2: return fabsf(value-0.5f)*2.0f;
    case 3: return 1.0f-fabsf(value-0.5f)*2.0f;
    case 4: return (float)(at%2);
    case 5: return (float)((at/(count/4>0 ? count/4 : 1))%2);
    case 6: return order[at]/(float)(count-1);
    case 7:
        delta=value*(float)M_PI;
        delta=delta*2.0f;
        delta=delta-(float)(M_PI/2.0);
        return (ce_npy_sinf(delta)+1.0f)/2.0f;
    case 8: return value*value;
    case 9: delta=1.0f-value;return 1.0f-delta*delta;
    case 10: return _ce_motion_zigzag(at,count);
    default: return value;
    }
}

CE_API int ce_source_motion_positions(int mode,int count,const float* order,float* out)
{
    if(mode<0 || mode>10 || !out || (mode==6 && count>1 && !order)) return -1;
    if(count<=1) {out[0]=0.5f;return 0;}
    double step=1.0/(count-1);
    for(int at=0;at<count;++at) {
        float value=at==count-1 ? 1.0f : (float)((double)at*step);
        out[at]=_ce_motion_position(value,at,count,mode,order);
    }
    if(mode==10) {
        float lo=out[0],hi=out[0];
        for(int at=1;at<count;++at) {if(out[at]<lo)lo=out[at];if(out[at]>hi)hi=out[at];}
        float span=hi-lo;
        for(int at=0;at<count;++at) out[at]=span>0.0f ? (out[at]-lo)/span : 0.0f;
    }
    return 0;
}
