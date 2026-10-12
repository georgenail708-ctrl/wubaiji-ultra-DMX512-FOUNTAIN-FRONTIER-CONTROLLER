/* Shared typed CPython01104ce builtin sum state; Row and Library reuse it. */
static int ce_pr_from_i64(ce_pr_number *out,int64_t number) {
    uint8_t bytes[8];uint64_t bits=(uint64_t)number;
    for(int j=0;j<8;++j) { bytes[j]=(uint8_t)bits;bits>>=8; }
    ce_traj_value value={CE_TR_INT,-1,0,8,0};ce_traj_request view={0};
    view.integers=bytes;view.integer_size=8;
    memset(out,0,sizeof(*out));out->kind=CE_TR_INT;out->selector=-1;
    return ce_tr_decode(&view,&value,&out->integer);
}
static int ce_pr_sum_values(ce_pr_context *c,const ce_traj_value *values,int64_t count,
                             ce_pr_number *out) {
    int state=0,rc=0;int64_t integer=0,ce_pr_small_value,total;
    double sum=0.,low=0.;ce_pr_number generic={0};
    for(int64_t i=0;!rc && i<count;++i) {
        ce_pr_number value={0};rc=ce_pr_num_get(c,values+i,&value,1);
        if(rc) { ce_pr_num_clear(&value);break; }
        if(state==0 && value.kind!=CE_TR_FLOAT
           && ce_pr_small_signed(&value.integer,&ce_pr_small_value,32)
           && ce_pr_i64_add(integer,ce_pr_small_value,&total)) integer=total;
        else if(state==0 && value.kind==CE_TR_FLOAT) {
            /* CPython restores int result, adds this first float normally,
             * then starts its float accumulator with zero compensation. */
            sum=(double)integer+value.number;low=0.;state=1;
        } else if(state==1 && value.kind==CE_TR_FLOAT)
            ce_pr_compensate(&sum,&low,value.number);
        else if(state==1 && ce_pr_small_signed(&value.integer,&ce_pr_small_value,32))
            sum+=(double)ce_pr_small_value;
        else {
            if(state!=2) {
                if(state==0) rc=ce_pr_from_i64(&generic,integer);
                else { generic.kind=CE_TR_FLOAT;generic.selector=-1;
                       generic.number=ce_pr_compensated(sum,low); }
                state=2;
            }
            if(!rc) rc=ce_pr_mean_generic(c,&generic,&value);
        }
        ce_pr_num_clear(&value);
    }
    if(!rc) {
        if(state==0) rc=ce_pr_from_i64(out,integer);
        else if(state==1) { out->kind=CE_TR_FLOAT;out->selector=-1;
                           out->number=ce_pr_compensated(sum,low); }
        else ce_pr_move(out,&generic);
    }
    ce_pr_num_clear(&generic);return rc;
}
