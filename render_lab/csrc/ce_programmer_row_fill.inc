/* Ordered group fill. Scratch only, no public map/cache/dirty owner writes. */
static int ce_pr_fill_program(const ce_pr_request *q,const ce_pr_item *p) {
    int64_t cursor=p->index;
    if(p->total<0 || cursor<0 || cursor>=q->offset_count
       || p->total>q->offset_count-cursor-1) return -1;
    for(int64_t group=0;group<=p->total;++group) {
        if(cursor>=q->offset_count) return -1;
        int64_t count=q->offsets[cursor++];
        if(!ce_tr_bounds(cursor,count,q->offset_count)) return -1;
        for(int64_t i=0;i<count;++i) {
            int64_t cell=q->offsets[cursor+i];if(cell<0 || cell>=q->value_count) return -1;
        }
        cursor+=count;
    }
    return 0;
}
static int ce_pr_fill_max(ce_pr_context *c,int64_t cursor,int64_t count,
                          ce_pr_number *maximum,int *any_light) {
    int rc=ce_pr_num_small(maximum,0);if(rc) return rc;
    for(int64_t i=0;!rc && i<count;++i) {
        const ce_traj_value *cell=c->fill_cells+c->q->offsets[cursor+i];
        if(cell->kind==PR_NONE) continue;
        ce_pr_number value={0},second={0};int comparison;
        rc=ce_pr_int_value(c,cell,&value,1);if(rc) goto cell_done;
        rc=ce_pr_num_compare(c,&value,maximum,&comparison);if(rc) goto cell_done;
        if(comparison==1) ce_pr_move(maximum,&value);
        /* Original source consumes int(v) again for its separate positive test. */
        rc=ce_pr_int_value(c,cell,&second,2);
        if(!rc && second.integer.sign>0) *any_light=1;
cell_done:
        ce_pr_num_clear(&value);ce_pr_num_clear(&second);
    }
    return rc;
}
static int ce_pr_fill(ce_pr_context *c,const ce_pr_item *p) {
    int64_t cursor=p->index;double total=0.;int any_light=0,rc=0;
    for(int64_t group=0;group<p->total;++group) {
        int64_t count=c->q->offsets[cursor++];ce_pr_number maximum={0};
        rc=ce_pr_fill_max(c,cursor,count,&maximum,&any_light);cursor+=count;
        if((p->flags&PR_FILL_PREFIX_ONLY) && group==p->total-1) {
            /* Metadata failed inside this last source group.  Only its
             * already consumed max/positive prefix belongs to the caller. */
            ce_pr_num_clear(&maximum);return rc;
        }
        if(!rc) {
            double value;rc=ce_pr_num_double(c,&maximum,&value,3);
            if(!rc) total+=value/255.; /* Explicit += left fold, not builtin sum. */
        }
        ce_pr_num_clear(&maximum);if(rc) return rc;
    }
    int64_t destinations=c->q->offsets[cursor++];
    if(!any_light || !p->total) {
        ce_pr_number zero={0};rc=ce_pr_num_small(&zero,0);
        if(!rc) rc=ce_pr_value_out(c,&zero);ce_pr_num_clear(&zero);
        ce_pr_index_out(c,0);return rc;
    }
    double ratio=total/(double)p->total,scaled=ratio*255.,rounded;
    rc=ce_tr_round(scaled,&rounded);if(rc) return ce_pr_fail(c,rc,4,-1);
    rounded=ce_pr_min_first(rounded,255.);
    rc=ce_pr_round_out(c,rounded,4,-1);if(rc) return rc;
    for(int64_t i=0;i<destinations;++i) {
        ce_traj_value *cell=c->fill_cells+c->q->offsets[cursor+i];
        memset(cell,0,sizeof(*cell));cell->kind=CE_TR_FLOAT;cell->selector=-1;cell->number=rounded;
    }
    ce_pr_index_out(c,1);++c->r->fill;return 0;
}
