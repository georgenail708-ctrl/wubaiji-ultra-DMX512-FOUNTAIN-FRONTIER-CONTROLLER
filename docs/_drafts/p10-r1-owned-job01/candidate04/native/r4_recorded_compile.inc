static int r4_recorded_create(r4_recorded* rows,r1_ledger* ledger,
                              const r4_domain_raw* raw,uint64_t count,uint64_t total)
{
    if (!rows || !ledger || (count && !raw) || count>SIZE_MAX/sizeof(r4_domain)) return R1_INVALID;
    if (total>SIZE_MAX/sizeof(r4_event) || (!count && total)) return R1_INVALID;
    memset(rows,0,sizeof(*rows));rows->ledger=ledger;rows->borrowed=raw;rows->domains_n=count;
    /* Declared total is verified incrementally; no raw domain traversal before
     * all header/extent/budget admission. No Boolean event matrix is created. */
    rows->events_n=total;rows->domain_bytes=count*sizeof(r4_domain);rows->event_bytes=total*sizeof(r4_event);
    if (count) rows->domains=r1_allocate(ledger,rows->domain_bytes);
    if (total) rows->events=r1_allocate(ledger,rows->event_bytes);
    if ((count && !rows->domains) || (total && !rows->events)) return R1_BUDGET;
    return count ? R1_BUILDING : R1_OK;
}

static void r4_recorded_destroy(r4_recorded* rows)
{
    if (!rows || !rows->ledger) return;
    r1_free(rows->ledger,rows->domains,rows->domain_bytes);r1_free(rows->ledger,rows->events,rows->event_bytes);
    memset(rows,0,sizeof(*rows));
}

static int r4_recorded_one(r4_recorded* rows)
{
    const r4_domain_raw* raw=rows->borrowed+rows->domain_cursor;
    r4_domain* domain=rows->domains+rows->domain_cursor;
    if (rows->event_cursor==0) {
        if (!raw->universe || raw->universe>=R1_UNIVERSE_LIMIT || raw->key_kind>1) return R1_PENDING;
        if (raw->count>rows->events_n-rows->out || (raw->count && !raw->events)) return R1_INVALID;
        domain->universe=raw->universe;domain->key_kind=raw->key_kind;domain->begin=rows->out;domain->count=raw->count;
    }
    if (rows->event_cursor<raw->count) {
        const r4_event_raw* event=raw->events+rows->event_cursor;
        r4_event* out=rows->events+rows->out;
        if (event->reserved || event->bytes!=64 || r1_atom_number(event->timestamp,0.,&out->timestamp)) return R1_PENDING;
        memcpy(out->bits,event->bits,64);rows->event_cursor++;rows->out++;
    }
    if (rows->event_cursor==raw->count) {rows->domain_cursor++;rows->event_cursor=0;}
    return R1_OK;
}

static int r4_recorded_step(r4_recorded* rows,uint32_t units)
{
    if (!rows || !rows->ledger || !units || units>65536) return R1_INVALID;
    if (rows->cancelled) return R1_CANCELLED;
    if (rows->semantic) return R1_PENDING;
    while (units-- && rows->domain_cursor<rows->domains_n) {
        int status=r4_recorded_one(rows);
        if (status) {rows->semantic=1;return status;}
    }
    if (rows->domain_cursor!=rows->domains_n) return R1_BUILDING;
    if (rows->out!=rows->events_n) return R1_INVALID;
    rows->borrowed=NULL;return R1_OK;
}
