/* One TICK19 call. Host applies only actual commit bits, then the first error. */
static int ce_pr_clock_advance(ce_pr_context *c,const ce_traj_value *v,int tick,
                              ce_pr_number *play,int64_t *commit) {
    ce_pr_number now={0},last={0},oldplay={0},dt={0};int rc;
    rc=ce_pr_num_get(c,v,&now,1);if(rc) goto done;
    rc=ce_pr_num_get(c,v+1,&last,1);if(rc) goto done;
    rc=ce_pr_add_numbers(c,&now,&last,&dt,-1);if(rc) goto done;
    rc=ce_pr_value_out(c,tick?&now:&dt);if(rc) goto done;
    *commit|=1;
    rc=ce_pr_num_get(c,v+2,&oldplay,2);if(rc) goto done;
    rc=ce_pr_add_numbers(c,&oldplay,&dt,play,1);if(rc) goto done;
    rc=ce_pr_value_out(c,play);if(!rc) *commit|=2;
done:
    ce_pr_num_clear(&now);ce_pr_num_clear(&last);ce_pr_num_clear(&oldplay);
    ce_pr_num_clear(&dt);return rc;
}
static int ce_pr_clock_finish(ce_pr_context *c,ce_pr_number *play,
                             const ce_traj_value *duration,unsigned loop,int64_t *route) {
    double seconds;int rc=ce_pr_double(c,duration,&seconds,4);if(rc) return rc;
    ce_pr_number limit={0};limit.kind=CE_TR_FLOAT;limit.selector=-1;limit.number=seconds;
    int comparison;rc=ce_pr_num_compare(c,play,&limit,&comparison);if(rc) return rc;
    *route=0;
    if(comparison==0 || comparison==1) {
        if(loop && seconds>0.) {
            double value;rc=ce_pr_num_double(c,play,&value,5);if(rc) return rc;
            ce_pr_num_clear(play);play->kind=CE_TR_FLOAT;play->selector=-1;
            play->number=_ce_mod_div(value,seconds);*route=1;
        } else {
            ce_pr_num_clear(play);play->kind=CE_TR_FLOAT;play->selector=-1;
            play->number=loop?0.:seconds;*route=loop?1:2;
        }
    }
    ce_pr_number_out(c,seconds);return 0;
}
static int ce_pr_tick(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;
    int64_t flags_at=c->r->written_indices,commit=0,route=0;
    ce_pr_index_out(c,0);ce_pr_index_out(c,0);
    ce_pr_number play={0};int rc=ce_pr_clock_advance(c,v,1,&play,&commit);
    c->q->index_out[flags_at]=commit;
    if(!rc) rc=ce_pr_clock_finish(c,&play,v+3,p->flags&1u,&route);
    if(!rc && route) {
        /* Replace the previously emitted calculated play with the actual endpoint. */
        ce_traj_value *out=c->q->value_out+c->r->written_values-1;
        memset(out,0,sizeof(*out));out->kind=CE_TR_FLOAT;out->selector=-1;out->number=play.number;
    }
    c->q->index_out[flags_at+1]=route;
    if(!rc) ++c->r->clock;ce_pr_num_clear(&play);return rc;
}
static int ce_pr_advance(ce_pr_context *c,const ce_pr_item *p) {
    int64_t commit=0,flags_at=c->r->written_indices;ce_pr_index_out(c,0);
    ce_pr_number play={0};int rc=ce_pr_clock_advance(c,c->q->values+p->value_offset,0,&play,&commit);
    c->q->index_out[flags_at]=commit;
    if(!rc) ++c->r->clock;ce_pr_num_clear(&play);return rc;
}
static int ce_pr_finish(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;
    ce_pr_number play={0};int64_t route=0;int rc=ce_pr_num_get(c,v,&play,1);
    if(!rc) rc=ce_pr_clock_finish(c,&play,v+1,p->flags&1u,&route);
    if(!rc) {
        /* This direct diagnostic op outputs final play; public TICK retains type. */
        double number;rc=ce_pr_num_double(c,&play,&number,5);
        if(!rc) { c->q->number_out[c->r->written_numbers-1]=number;
                  ce_pr_index_out(c,route);++c->r->clock; }
    }
    ce_pr_num_clear(&play);return rc;
}
static int ce_pr_scrub(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;double duration,value;
    int rc=ce_pr_double(c,v,&duration,1);if(rc) return rc;
    rc=ce_pr_double(c,v+1,&value,2);if(rc) return rc;
    double product=duration*value;double scaled=product/1000.;
    double inner=scaled<duration?scaled:duration;
    ce_pr_number_out(c,inner>0.?inner:0.);ce_pr_number_out(c,duration);
    ++c->r->clock;return 0;
}
static int ce_pr_debounce(ce_pr_context *c,const ce_pr_item *p) {
    const ce_traj_value *v=c->q->values+p->value_offset;double last,now;
    int rc=ce_pr_double(c,v+1,&last,1);if(rc) return rc;
    rc=ce_pr_double(c,v,&now,2);if(rc) return rc;
    ce_pr_index_out(c,now-last<.033?1:0);++c->r->clock;return 0;
}
