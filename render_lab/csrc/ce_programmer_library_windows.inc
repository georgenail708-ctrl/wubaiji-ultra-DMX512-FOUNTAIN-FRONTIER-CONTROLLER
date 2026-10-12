static int ce_pl_windows(ce_pl_context *c) {
    const ce_pl_request *q=c->q;
    if(q->n<1 || (!q->a && !q->typed_a) || (!q->b && !q->typed_b)) return -1;
    double duration; int rc=ce_pl_duration(c,&duration);
    if(rc || c->r->semantic) return rc;
    double *segments=malloc((size_t)q->n*sizeof(double)); if(!segments) return -2;
    for(int64_t i=0;i<q->n;++i) {
        double delta;rc=ce_pl_edge_delta(c,i,&delta);
        if(rc || c->r->semantic) { free(segments);return rc; }segments[i]=delta*duration;
    }
    for(int64_t i=0;i<=q->n;++i) {
        double transition;rc=ce_pl_column_double(c,1,i,&transition);
        if(rc || c->r->semantic) { free(segments);return rc; }
        double tau=transition>0.?transition:0.;
        q->out_f[i]=(i==0 || i==q->n)?tau:tau/2.;
    }
    for(int iteration=0;iteration<64;++iteration) {
        double worst=1.;
        for(int64_t i=0;i<q->n;++i) {
            double used=q->out_f[i]+q->out_f[i+1];
            if(used>segments[i]+1e-12 && used>1e-12) {
                double ratio=segments[i]/used; if(ratio<worst) worst=ratio;
            }
        }
        if(worst>=1.-1e-12) break;
        for(int64_t i=0;i<=q->n;++i) q->out_f[i]*=worst;
    }
    for(int64_t i=0;i<q->n;++i) {
        double total=q->out_f[i]+q->out_f[i+1],over=total-segments[i];
        if(over>1e-9 && total>1e-12) {
            double scale=segments[i]/total;
            q->out_f[i]*=scale; q->out_f[i+1]*=scale;
        }
    }
    for(int64_t i=0;i<=q->n;++i) {
        double edge;rc=ce_pl_column_double(c,0,i,&edge);
        if(rc || c->r->semantic) { free(segments);return rc; }
        double h=q->out_f[i],boundary=edge*duration;
        q->out_flags[i]=(uint8_t)(h>1e-9);
        q->out_i[i]=i;
        q->out_f[q->n+1+i]=i==0?0.:(i==q->n?duration-h:boundary-h);
        q->out_f[2*(q->n+1)+i]=i==0?h:(i==q->n?duration:boundary+h);
    }
    c->r->window+=(uint64_t)q->n+1; free(segments); return 0;
}
