static int rc07_count_value(rctx07_job* j,const rctx07_value* value)
{
    if (value->bytes && !value->data) return R1_INVALID;
    if (!r3_add_bytes(&j->external_min,value->bytes,1)
        || !r3_add_bytes(&j->data_bound,value->bytes,4)
        || !r3_add_bytes(&j->data_bound,1,192)) return R1_BUDGET;
    if (j->values_count==UINT64_MAX) return R1_BUDGET;
    j->values_count++;return j->external_min>j->raw->external_bytes ? R1_BUDGET : R1_OK;
}
static int rc07_count_values(rctx07_job* j,const rctx07_values* list)
{
    if (list->reserved || (list->n && !list->rows)) return R1_INVALID;
    if (!r3_add_bytes(&j->external_min,list->n,sizeof(rctx07_value))) return R1_BUDGET;
    if (list->n>j->largest) j->largest=list->n;
    return j->external_min>j->raw->external_bytes ? R1_BUDGET : R1_OK;
}
static const rctx07_value* rc07_clip_field(const rctx07_clip* c,uint64_t k)
{
    const rctx07_value* fields[8]={&c->id,&c->start,&c->duration,&c->hold_mode,&c->hold_last,c->filters,c->filters+1,c->filters+2};
    return k<8 ? fields[k] : NULL;
}
static const rctx07_value* rc07_track_field(const rctx07_track* t,uint64_t k)
{
    const rctx07_value* fields[5]={&t->id,&t->parent,&t->folder,&t->muted,&t->solo};return k<5 ? fields[k] : NULL;
}
static int rc07_count_clip_one(rctx07_job* j)
{
    const rctx07_clip* c=j->raw->clips+j->count_row;int status=0;
    switch(j->count_sub) {
    case 0:
        if (c->reserved || (c->snapshot_n && !c->snapshot) || (c->active_n && !c->active)) return R1_INVALID;
        if (!r3_add_bytes(&j->external_min,c->snapshot_n,sizeof(rctx07_snapshot))
            || !r3_add_bytes(&j->external_min,c->active_n,sizeof(rctx07_active))) return R1_BUDGET;
        if (c->snapshot_n>j->largest) j->largest=c->snapshot_n;if (c->active_n>j->largest) j->largest=c->active_n;
        status=rc07_count_values(j,&c->universes);if (!status) status=rc07_count_values(j,&c->base_ids);
        j->count_sub=1;j->count_member=0;break;
    case 1:
        if (j->count_member<8) return rc07_count_value(j,rc07_clip_field(c,j->count_member++));
        j->count_sub=2;j->count_member=0;break;
    case 2:
        if (j->count_member<c->universes.n) return rc07_count_value(j,c->universes.rows+j->count_member++);
        j->count_sub=3;j->count_member=0;break;
    case 3:
        if (j->count_member<c->base_ids.n) return rc07_count_value(j,c->base_ids.rows+j->count_member++);
        j->count_sub=4;j->count_member=0;j->scan=0;break;
    case 4:
        if (j->count_member<c->snapshot_n) {
            const rctx07_snapshot* s=c->snapshot+j->count_member;
            const rctx07_value* value=j->scan==0 ? &s->key : j->scan==1 ? &s->x : &s->y;
            status=rc07_count_value(j,value);if (++j->scan==3) {j->scan=0;j->count_member++;}return status;
        }
        j->count_sub=5;j->count_member=j->scan=0;break;
    case 5:
        if (j->count_member<c->active_n) {
            const rctx07_active* a=c->active+j->count_member;
            if (!j->scan) {status=rc07_count_values(j,&a->channels);if (!status) status=rc07_count_value(j,&a->key);j->scan=1;return status;}
            if (j->scan<=a->channels.n) return rc07_count_value(j,a->channels.rows+(j->scan++)-1);
            j->count_member++;j->scan=0;return R1_OK;
        }
        j->count_row++;j->count_sub=j->count_member=0;break;
    default:return R1_INVALID;
    }
    return status ? status : j->external_min>j->raw->external_bytes ? R1_BUDGET : R1_OK;
}
static int rc07_count_rule_one(rctx07_job* j,const rctx07_rule* rule)
{
    if (!j->scan) {j->scan=1;return rc07_count_values(j,&rule->channels);}
    if (j->scan<=rule->channels.n) return rc07_count_value(j,rule->channels.rows+(j->scan++)-1);
    j->scan=0;return R1_DONE;
}
static int rc07_count_track_one(rctx07_job* j)
{
    const rctx07_track* t=j->raw->tracks+j->count_row;int status;
    if (!j->count_sub) {
        if ((t->clips_n && !t->clips) || (t->rules_n && !t->rules)) return R1_INVALID;
        if (!r3_add_bytes(&j->external_min,t->clips_n,sizeof(uint64_t))
            || !r3_add_bytes(&j->external_min,t->rules_n,sizeof(rctx07_rule_entry))) return R1_BUDGET;
        if (t->clips_n>j->largest) j->largest=t->clips_n;j->count_sub=1;j->count_member=0;return R1_OK;
    }
    if (j->count_sub==1) {
        if (j->count_member<5) return rc07_count_value(j,rc07_track_field(t,j->count_member++));
        j->count_sub=2;j->count_member=0;return R1_OK;
    }
    if (j->count_sub==2 && j->count_member<t->rules_n) {
        const rctx07_rule_entry* row=t->rules+j->count_member;
        if (!j->scan && !j->kind) {j->kind=1;return rc07_count_value(j,&row->key);}
        status=rc07_count_rule_one(j,&row->rule);
        if (status==R1_DONE) {j->count_member++;j->kind=0;return R1_OK;}return status;
    }
    status=rc07_count_rule_one(j,&t->fallback_rule);
    if (status==R1_DONE) {j->count_row++;j->count_sub=j->count_member=j->kind=0;return R1_OK;}return status;
}
static int rc07_count_one(rctx07_job* j)
{
    if (!j->count_phase) {
        if (j->count_row<j->raw->clips_n) return rc07_count_clip_one(j);
        j->count_phase=1;j->count_row=j->count_sub=j->count_member=j->scan=0;return R1_OK;
    }
    if (j->count_phase==1) {
        if (j->count_row<j->raw->tracks_n) return rc07_count_track_one(j);
        /* Permit/runtime are genuine separate supplier domains. Their raw
         * compiler follows; no certificate fields are traversed before bounds. */
        j->count_phase=2;j->count_row=0;return R1_OK;
    }
    j->phase=RC07_ALLOCATE;return R1_OK;
}
