static int r4_rule_create(r4_rule* out,const r4_channel_raw* raw,uint64_t count,int present)
{
    if (!out || (count && !raw) || count>SIZE_MAX/sizeof(r4_channel_raw)) return R1_INVALID;
    memset(out,0,sizeof(*out));out->borrowed=raw;out->count=count;out->all_numeric_equal=1;
    out->present=present!=0 && count!=0;return count && out->present ? R1_BUILDING : R1_OK;
}

static int r4_rule_one(r4_rule* rule)
{
    const r4_channel_raw* raw=rule->borrowed+rule->cursor++;
    int channel,equal=0,status;
    if (raw->reserved || raw->kind>1) return R1_PENDING;
    if (raw->kind==1) {rule->all_numeric_equal=0;status=r4_text_channel(raw,&channel);}
    else status=r4_numeric_channel(raw,&channel,&equal);
    if (!equal) rule->all_numeric_equal=0;
    if (status<0) return status;
    if (status==1) rule->mask[channel]=1;
    return R1_OK;
}

static int r4_rule_finish(r4_rule* rule)
{
    int count=0;
    for (int c=0;c<512;c++) count+=rule->mask[c]!=0;
    /* Preserve FilterRule.is_empty/allows_all semantics, not merely a bitmap
     * equal to all ones after int("1") normalization. */
    if (!count || (count==512 && rule->count==512 && rule->all_numeric_equal)) rule->present=0;
    rule->borrowed=NULL;return R1_OK;
}

static int r4_rule_step(r4_rule* rule,uint32_t units)
{
    if (!rule || !units || units>65536) return R1_INVALID;
    if (rule->cancelled) return R1_CANCELLED;
    if (rule->semantic) return R1_PENDING;
    if (!rule->present || !rule->borrowed) return R1_OK;
    while (units-- && rule->cursor<rule->count) {
        int status=r4_rule_one(rule);
        if (status) {rule->semantic=1;return status;}
    }
    return rule->cursor==rule->count ? r4_rule_finish(rule) : R1_BUILDING;
}
