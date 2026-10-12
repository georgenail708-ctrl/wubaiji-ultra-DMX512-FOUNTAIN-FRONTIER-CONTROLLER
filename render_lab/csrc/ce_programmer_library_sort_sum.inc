/* Width adapter for ce_engine.c:5359-5377 _ce_py_sum_f64.
 * Ordinary counts reuse that actual helper; the wide branch changes only
 * the loop/count width, preserving CPython3.13 compensated operation order.
 * No narrowing count, Python call, nonfinite veto or different sum algorithm. */
static double ce_ps_sum(const double *a,int64_t n) {
    if(n<=INT_MAX) return _ce_py_sum_f64(a,(int)n);
    double result=0.0+a[0],correction=0.;
    for(int64_t i=1;i<n;++i) {
        double x=a[i],next=result+x;
        if(fabs(result)>=fabs(x)) correction+=(result-next)+x;
        else correction+=(x-next)+result;
        result=next;
    }
    if(correction!=0. && isfinite(correction)) result+=correction;
    return result;
}
