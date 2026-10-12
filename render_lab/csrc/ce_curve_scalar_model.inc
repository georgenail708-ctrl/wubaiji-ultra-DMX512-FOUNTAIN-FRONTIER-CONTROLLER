static int ce_cs_base(ce_cs_context *c,const ce_cs_point *a,const ce_cs_point *b,
                        double *values,ce_traj_result *out,int64_t index) {
    const ce_traj_value *fields[4]={&a->x,&a->y,&b->x,&b->y};
    for(int i=0;i<4;++i) {
        int rc=ce_cs_parse_at(c,fields[i],values+i,out,CS_X0_STAGE+i,index);
        if(rc) return rc<0?rc:0;
    }
    return 0;
}
static int ce_cs_control_pair(ce_cs_context *c,const ce_cs_point *p,int outgoing,
                                double *values,ce_traj_result *out,int64_t index) {
    const ce_traj_value *x=outgoing?&p->c2x:&p->c1x;
    const ce_traj_value *y=outgoing?&p->c2y:&p->c1y;
    unsigned bit=outgoing?4u:1u; int stage=outgoing?CS_OUTX_STAGE:CS_INX_STAGE;
    int rc=ce_cs_parse_at(c,p->present&bit?x:&p->x,values,out,stage,index);
    if(rc) return rc<0?rc:0;
    rc=ce_cs_parse_at(c,p->present&(bit<<1)?y:&p->y,values+1,out,stage+1,index);
    return rc<0?rc:0;
}
static int ce_cs_controls(ce_cs_context *c,const ce_cs_point *a,const ce_cs_point *b,
                            const double *base,double *values,ce_traj_result *out,
                            int64_t index) {
    values[0]=base[0]; values[1]=base[1]; values[2]=base[2]; values[3]=base[3];
    int rc=0;
    if(a->present&12u) rc=ce_cs_control_pair(c,a,1,values,out,index);
    if(rc || out->kind==CE_TR_ERROR) return rc;
    if(b->present&3u) rc=ce_cs_control_pair(c,b,0,values+2,out,index);
    if(rc || out->kind==CE_TR_ERROR) return rc;
    double low=base[0]<=base[2]?base[0]:base[2];
    double high=base[0]<=base[2]?base[2]:base[0];
    values[0]=ccl_ordered_clamp(values[0],low,high);
    values[2]=ccl_ordered_clamp(values[2],values[0],high); return 0;
}
static int ce_cs_bezier(ce_cs_context *c,const ce_traj_value *query,const double *base,
                         const double *controls,ce_traj_result *out,int64_t index) {
    double xs[33],ys[33]; int rc,lower,upper;
    for(int k=0;k<=32;++k) {
        double t=k/32.;
        xs[k]=ccl_coord(t,base[0],controls[0],controls[2],base[2]);
        ys[k]=ccl_coord(t,base[1],controls[1],controls[3],base[3]);
    }
    c->report->bezier_samples+=33;
    for(int k=0;k<32;++k) {
        ce_traj_value left={CE_TR_FLOAT,-1,0,0,xs[k]},right={CE_TR_FLOAT,-1,0,0,xs[k+1]};
        rc=ce_cs_compare(c,&left,query,&lower); if(rc) return rc;
        if(lower!=0 && lower!=-1) continue;
        rc=ce_cs_compare(c,query,&right,&upper); if(rc) return rc;
        if(upper!=0 && upper!=-1) continue;
        double fraction=0.;
        if(xs[k+1]!=xs[k]) {
            double value; rc=ce_cs_parse_at(c,query,&value,out,CS_RATIO_STAGE,index);
            if(rc) return rc<0?rc:0;
            fraction=(value-xs[k])/(xs[k+1]-xs[k]);
        }
        ce_cs_number(out,ys[k]+fraction*(ys[k+1]-ys[k]));
        ++c->report->bezier; return 0;
    }
    ce_cs_number(out,ys[32]); ++c->report->bezier; return 0;
}
static int ce_cs_segment(ce_cs_context *c,const ce_cs_point *a,const ce_cs_point *b,
                           const ce_traj_value *query,ce_traj_result *out,
                           int64_t index) {
    double base[4],controls[4]; int rc=ce_cs_base(c,a,b,base,out,index);
    if(rc || out->kind==CE_TR_ERROR) return rc;
    if(!(a->present&12u) && !(b->present&3u)) {
        double fraction=0.;
        if(base[2]!=base[0]) {
            double value; rc=ce_cs_parse_at(c,query,&value,out,CS_RATIO_STAGE,index);
            if(rc) return rc<0?rc:0;
            fraction=(value-base[0])/(base[2]-base[0]);
        }
        ce_cs_number(out,base[1]+fraction*(base[3]-base[1]));
    } else {
        rc=ce_cs_controls(c,a,b,base,controls,out,index);
        if(rc || out->kind==CE_TR_ERROR) return rc;
        rc=ce_cs_bezier(c,query,base,controls,out,index);
    }
    if(!rc && out->kind!=CE_TR_ERROR) ++c->report->segment;
    return rc;
}
static void ce_cs_selected(ce_cs_context *c,const ce_traj_value *value,
                             ce_traj_result *out) {
    ce_cs_result_init(out); out->kind=CE_TR_SELECTED;
    out->selector=value->selector; c->selected=value;
    if(value->kind==CE_TR_FLOAT) out->number=value->number;
}
static int ce_cs_model(ce_cs_context *c,const ce_traj_value *query,ce_traj_result *out) {
    const ce_cs_request *q=c->request; int relation,rc; ce_cs_result_init(out);
    c->selected=NULL;
    if(!q->model_count) return ce_cs_error(c,out,CS_INDEX,CS_SELECT_STAGE,-1,0);
    rc=ce_cs_compare(c,query,&q->model_points[0].x,&relation); if(rc) return rc;
    ++c->report->model;
    if(relation==0 || relation==-1) {
        ce_cs_selected(c,&q->model_points[0].y,out); return 0;
    }
    const ce_cs_point *last=q->model_points+q->model_count-1;
    rc=ce_cs_compare(c,query,&last->x,&relation); if(rc) return rc;
    if(relation==0 || relation==1) { ce_cs_selected(c,&last->y,out); return 0; }
    for(int64_t i=0;i<q->model_count-1;++i) {
        const ce_cs_point *a=q->model_points+i,*b=a+1;
        rc=ce_cs_compare(c,&a->x,query,&relation); if(rc) return rc;
        if(relation!=0 && relation!=-1) continue;
        rc=ce_cs_compare(c,query,&b->x,&relation); if(rc) return rc;
        if(relation==0 || relation==-1) return ce_cs_segment(c,a,b,query,out,i);
    }
    ce_cs_number(out,0.); return 0;
}
