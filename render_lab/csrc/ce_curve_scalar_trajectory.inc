static int ce_cs_phase(ce_cs_context *c,ce_traj_result *out) {
    const ce_cs_request *q=c->request; int truth,rc=ce_cs_truth(c,&q->duration,&truth);
    if(rc) return rc;
    double duration=4.,time=0.;
    if(truth) {
        rc=ce_cs_parse_at(c,&q->duration,&duration,out,CS_DURATION_STAGE,-1);
        if(rc) return rc<0?rc:0;
    }
    if(duration<=0.) duration=4.;
    rc=ce_cs_double(c,&q->argument,&time); if(rc<0) return rc;
    if(rc) { time=0.; ++c->report->handled_semantic; }
    double phase=q->flags&CS_LOOP?_ce_mod_div(time,duration)/duration:time/duration;
    ce_cs_number(out,ce_cs_clamp(c,phase)); ++c->report->phase; return 0;
}
static int ce_cs_fallback_ratio(ce_cs_context *c,double phase,const ce_cs_pair *a,
                                  const ce_cs_pair *b,double *fraction,
                                  ce_traj_result *out,int64_t index) {
    int relation,rc=ce_cs_compare(c,&b->x,&a->x,&relation); if(rc) return rc;
    *fraction=0.; if(!relation) return 0;
    double x0,denominator; rc=ce_cs_parse_at(c,&a->x,&x0,out,CS_RATIO_STAGE,index);
    if(rc) return rc<0?rc:0;
    double numerator=phase-x0;
    int error_selector;
    rc=ce_cs_difference(c,&b->x,&a->x,&denominator,&error_selector);
    if(rc>0) ce_cs_error(c,out,rc,CS_RATIO_STAGE,error_selector,index);
    if(rc) return rc<0?rc:0;
    if(denominator==0.) return ce_cs_error(c,out,CE_TR_ZERO,CS_RATIO_STAGE,-1,index);
    *fraction=numerator/denominator; return 0;
}
static int ce_cs_fallback_value(ce_cs_context *c,const ce_cs_pair *a,const ce_cs_pair *b,
                                  double fraction,ce_traj_result *out,int64_t index) {
    double delta,left; int error_selector;
    int rc=ce_cs_difference(c,&b->y,&a->y,&delta,&error_selector);
    if(rc>0) ce_cs_error(c,out,rc,CS_DIFFERENCE_STAGE,error_selector,index);
    if(rc) return rc<0?rc:0;
    double product=fraction*delta;
    rc=ce_cs_parse_at(c,&a->y,&left,out,CS_ADD_STAGE,index);
    if(rc) return rc<0?rc:0;
    ce_cs_number(out,ce_cs_clamp(c,left+product)); return 0;
}
static int ce_cs_fallback_sorted(ce_cs_context *c,double phase,ce_traj_result *out,
                                  const int64_t *ordered) {
    const ce_cs_request *q=c->request; int relation,rc;
    ce_traj_value value={CE_TR_FLOAT,-1,0,0,phase};
    const ce_cs_pair *first=ce_cs_fallback_at(c,ordered,0);
    const ce_cs_pair *last=ce_cs_fallback_at(c,ordered,q->fallback_count-1);
    rc=ce_cs_compare(c,&value,&first->x,&relation); if(rc) return rc;
    if(relation==0 || relation==-1) return ce_cs_clamped_value(c,&first->y,out);
    rc=ce_cs_compare(c,&value,&last->x,&relation); if(rc) return rc;
    if(relation==0 || relation==1) return ce_cs_clamped_value(c,&last->y,out);
    for(int64_t i=0;i<q->fallback_count-1;++i) {
        const ce_cs_pair *a=ce_cs_fallback_at(c,ordered,i);
        const ce_cs_pair *b=ce_cs_fallback_at(c,ordered,i+1);
        rc=ce_cs_compare(c,&a->x,&value,&relation); if(rc) return rc;
        if(relation!=0 && relation!=-1) continue;
        rc=ce_cs_compare(c,&value,&b->x,&relation); if(rc) return rc;
        if(relation!=0 && relation!=-1) continue;
        double fraction; rc=ce_cs_fallback_ratio(c,phase,a,b,&fraction,out,i);
        if(rc || out->kind==CE_TR_ERROR) return rc;
        return ce_cs_fallback_value(c,a,b,fraction,out,i);
    }
    return ce_cs_clamped_value(c,&last->y,out);
}
static int ce_cs_fallback(ce_cs_context *c,double phase,ce_traj_result *out) {
    const ce_cs_request *q=c->request;
    ce_cs_result_init(out);
    if(q->flags&CS_FB_ERROR) return ce_cs_error(c,out,q->fallback_semantic,
        CS_FB_META_STAGE,q->fallback_selector,-1);
    if(!q->fallback_count) { ce_cs_number(out,0.); return 0; }
    int64_t *ordered=NULL;
    int rc=ce_cs_fallback_order(c,&ordered);
    if(!rc) rc=ce_cs_fallback_sorted(c,phase,out,ordered);
    free(ordered);return rc;
}
static int ce_cs_model_phase(ce_cs_context *c,double phase,ce_traj_result *out) {
    ce_traj_value value={CE_TR_FLOAT,-1,0,0,phase};
    int rc=ce_cs_model(c,&value,out); if(rc) return rc;
    if(out->kind==CE_TR_ERROR) {
        ++c->report->handled_semantic; c->error_index=-1;
        return ce_cs_fallback(c,phase,out);
    }
    const ce_traj_value *raw=c->selected; double number;
    if(out->kind==CE_TR_SELECTED) {
        rc=ce_cs_double(c,raw,&number); if(rc<0) return rc;
        if(rc) { ++c->report->handled_semantic; return ce_cs_fallback(c,phase,out); }
    } else number=out->number;
    double clamped=ce_cs_clamp(c,number); ce_cs_number(out,clamped);
    if(raw && raw->kind==CE_TR_FLOAT && raw->selector>=0 && !(number<0. || number>1.))
        ce_cs_selected(c,raw,out);
    return 0;
}
static int ce_cs_eval_phase(ce_cs_context *c,const ce_traj_value *argument,
                             ce_traj_result *out) {
    double phase; int rc=ce_cs_double(c,argument,&phase); if(rc<0) return rc;
    if(rc) { phase=0.; ++c->report->handled_semantic; }
    else phase=ce_cs_clamp(c,phase);
    if(c->request->flags&CS_MODEL_PRESENT) return ce_cs_model_phase(c,phase,out);
    return ce_cs_fallback(c,phase,out);
}
