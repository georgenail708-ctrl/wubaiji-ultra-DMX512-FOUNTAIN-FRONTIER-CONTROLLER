static int ce_pl_duration(ce_pl_context *c,double *duration) {
    int truth,rc=ce_cs_truth(&c->scalar,&c->q->scalar->duration,&truth); if(rc) return rc;
    *duration=4.;
    if(truth) {
        ce_traj_result value;
        rc=ce_cs_parse_at(&c->scalar,&c->q->scalar->duration,duration,&value,
                          CS_DURATION_STAGE,-1);
        if(rc>0) { ce_pl_scalar_result(c,&value); return 0; }
        if(rc) return rc;
    }
    if(*duration<=0.) *duration=4.; return 0;
}
static int ce_pl_segment_phase(ce_pl_context *c,double phase) {
    const ce_pl_request *q=c->q;
    if(q->n<1 || (!q->a && !q->typed_a)) return -1;
    q->out_i[0]=0; q->out_f[0]=0.;
    for(int64_t i=0;i<q->n;++i) {
        ce_traj_value s=ce_pl_column(q,0,i),e=ce_pl_column(q,0,i+1),p=ce_pl_float(phase);
        int left,right,rc=ce_cs_compare(&c->scalar,&s,&p,&left);if(rc) return rc;
        int chosen=0;
        if(left==0 || left==-1) {
            rc=ce_cs_compare(&c->scalar,&p,&e,&right);if(rc) return rc;chosen=right==-1;
        }
        if(!chosen && i==q->n-1) chosen=left==0 || left==-1;
        if(chosen) {
            q->out_i[0]=i;
            rc=ce_cs_compare(&c->scalar,&e,&s,&right);if(rc) return rc;
            double value=1.;
            if(right==1) {
                double numerator,denominator;
                rc=ce_pl_difference(c,&p,&s,&numerator);if(rc || c->r->semantic) return rc;
                rc=ce_pl_difference(c,&e,&s,&denominator);if(rc || c->r->semantic) return rc;
                value=numerator/denominator;
            }
            q->out_f[0]=ce_cs_clamp(&c->scalar,value);
            break;
        }
    }
    ++c->r->segment; return 0;
}
static int ce_pl_segment(ce_pl_context *c) {
    ce_traj_result phase; int rc=ce_cs_phase(&c->scalar,&phase);
    if(rc || ce_pl_scalar_result(c,&phase)) return rc;
    return ce_pl_segment_phase(c,phase.number);
}
static int ce_pl_path(ce_pl_context *c) {
    double number;int rc;
    if(c->q->flags&PL_PATH_VALUE_READY) {
        rc=ce_pl_parameter_double(c,0,&number);
        if(rc || c->r->semantic) return rc;
    } else {
        ce_traj_result phase,value;rc=ce_cs_phase(&c->scalar,&phase);
        if(rc || ce_pl_scalar_result(c,&phase)) return rc;
        ce_traj_value argument=ce_pl_float(phase.number);
        rc=ce_cs_eval_phase(&c->scalar,&argument,&value);
        if(rc || ce_pl_scalar_result(c,&value)) return rc;
        number=value.number;
    }
    rc=ce_pl_quantize(c,number,c->q->out_b,0);
    if(rc || c->r->semantic) return rc;
    rc=ce_pl_segment(c); if(rc || c->r->semantic) return rc;
    int64_t i=c->q->out_i[0];double delta;
    rc=ce_pl_edge_delta(c,i,&delta);if(rc || c->r->semantic) return rc;
    double duration; rc=ce_cs_double(&c->scalar,&c->q->scalar->duration,&duration);
    if(rc>0) {
        ce_pl_fail(c,rc,CS_DURATION_STAGE,-1);
        c->r->result.selector=c->q->scalar->duration.selector; return 0;
    }
    if(rc) return rc;
    c->q->out_f[1]=delta*duration; return 0;
}
static int ce_pl_shape_time(ce_pl_context *c) {
    double duration,time; int rc=ce_pl_duration(c,&duration);
    if(rc || c->r->semantic) return rc;
    rc=ce_cs_double(&c->scalar,&c->q->scalar->argument,&time);
    if(rc>0) {
        ce_pl_fail(c,rc,CS_TIME_STAGE,-1);
        c->r->result.selector=c->q->scalar->argument.selector; return 0;
    }
    if(rc) return rc;
    double teff;
    if((c->q->scalar->flags&CS_LOOP) && duration>0.) teff=_ce_mod_div(time,duration);
    else { teff=time<duration?time:duration; teff=teff>0.?teff:0.; }
    c->q->out_f[0]=duration; c->q->out_f[1]=teff; return 0;
}
static int ce_pl_window_select(ce_pl_context *c) {
    const ce_pl_request *q=c->q; if(q->n && (!q->a || !q->b)) return -1;
    double time;int rc=ce_pl_parameter_double(c,0,&time);if(rc || c->r->semantic) return rc;
    q->out_i[0]=-1;
    for(int64_t i=0;i<q->n;++i)
        if(q->a[i]-1e-9<=time && time<=q->b[i]+1e-9 && q->b[i]>q->a[i]) {
            q->out_i[0]=i; double delta=q->b[i]-q->a[i];
            q->out_f[0]=delta>1e-6?delta:1e-6;
            q->out_f[1]=ce_cs_clamp(&c->scalar,(time-q->a[i])/q->out_f[0]);
            break;
        }
    return 0;
}
static int ce_pl_static_select(ce_pl_context *c) {
    const ce_pl_request *q=c->q; if(q->n<1 || (!q->a && !q->typed_a)) return -1;
    double time,duration;int rc=ce_pl_parameter_double(c,0,&time);
    if(rc || c->r->semantic) return rc;
    rc=ce_pl_parameter_double(c,1,&duration);if(rc || c->r->semantic) return rc;
    q->out_i[0]=0;
    for(int64_t i=0;i<q->n;++i) {
        double s,e;rc=ce_pl_column_double(c,0,i,&s);if(rc || c->r->semantic) return rc;
        int chosen=0;
        if(s*duration<=time) {
            rc=ce_pl_column_double(c,0,i+1,&e);if(rc || c->r->semantic) return rc;
            chosen=time<e*duration;
        }
        if(chosen || (i==q->n-1 && time>=s*duration)) { q->out_i[0]=i;break; }
    }
    ++c->r->segment; return 0;
}
