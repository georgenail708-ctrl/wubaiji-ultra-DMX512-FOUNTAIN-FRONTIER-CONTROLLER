/* K05ad research candidate: called only from the existing timing ABI.
 * Include after _ce_rt_bucket; original op0--6 helpers stay byte-exact. */
static void _ce_rt_cold_span(const int* flags,const double* values,double* output)
{
    double base=values[0]+values[1];
    output[0]=base;output[1]=base;
    if(flags[0]) return;
    double fade=_ce_rt_nonnegative(values[2]);
    if(fade>values[1]+1e-9) output[0]=values[0]+fade;
}

static void _ce_rt_cold_buckets(const double* values,double* output)
{
    double first[24]={0.0},last[24]={0.0};
    double pair[2]={values[0],values[2]};
    _ce_rt_bucket(pair,first);
    if(first[23]!=0.0) {output[23]=first[23];return;}
    output[0]=first[0];
    pair[0]=_ce_rt_max_left(values[0],values[1]-1e-9);
    _ce_rt_bucket(pair,last);
    if(last[23]!=0.0) {output[23]=last[23];return;}
    output[1]=last[0];
}

static void _ce_rt_index_elapsed(const double* values,double* output)
{
    if(values[0]<0.0) {output[0]=1.0;return;}
    output[0]=(values[1]-values[0])>=values[2];
}
