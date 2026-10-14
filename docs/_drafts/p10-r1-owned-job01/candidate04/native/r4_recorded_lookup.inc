static const r4_domain* r4_recorded_domain(const r4_recorded* rows,uint32_t universe)
{
    const r4_domain *integer=NULL,*text=NULL;
    for (uint64_t i=0;i<rows->domains_n;i++) {
        const r4_domain* domain=rows->domains+i;
        if (domain->universe!=universe) continue;
        if (domain->key_kind==0) integer=domain;else text=domain;
    }
    return integer && integer->count ? integer : text && text->count ? text : NULL;
}

static void r4_recorded_select(const r4_recorded* rows,uint32_t universe,double source,r4_mask_selection* out)
{
    const r4_domain* domain=r4_recorded_domain(rows,universe);
    memset(out,0,sizeof(*out));out->event=-1;
    if (!domain) return;
    uint64_t left=0,right=domain->count;double needle=_ce_rt_nonnegative(source)+1e-9;
    while (left<right) {
        uint64_t middle=left+(right-left)/2;
        if (needle<rows->events[domain->begin+middle].timestamp) right=middle;
        else left=middle+1;
    }
    if (!left) {out->kind=R4_MASK_BEFORE_FIRST;return;}
    out->kind=R4_MASK_SELECTED;out->event=(int64_t)(left-1);out->selected=rows->events+domain->begin+left-1;
}

static void r4_recorded_active(const r4_mask_selection* selected,const uint8_t* base,uint8_t* active)
{
    if (selected->kind==R4_MASK_MISSING) {memcpy(active,base,512);return;}
    if (selected->kind==R4_MASK_BEFORE_FIRST) {memset(active,0,512);return;}
    for (int c=0;c<512;c++) active[c]=(uint8_t)(base[c] && ((selected->selected->bits[c>>3]>>(7-(c&7)))&1));
}

static double r4_source_mask_boundary(const r4_recorded* rows,uint32_t universe,
                                      const r4_mask_time* time)
{
    if (!r4_recorded_domain(rows,universe)) return time->source;
    return time->completed && time->declared>0. ? time->declared : time->source;
}

static void r4_rule_active(const r4_mask_selection* selected,const uint8_t* rule,uint8_t* active)
{
    int count=0;
    if (!rule) return;
    ce_domain_mask(selected->kind==R4_MASK_MISSING ? 5 : 1,512,active,rule,active,&count);
}
