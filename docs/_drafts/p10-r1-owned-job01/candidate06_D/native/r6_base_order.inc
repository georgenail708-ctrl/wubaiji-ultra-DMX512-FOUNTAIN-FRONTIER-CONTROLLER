/* Raw authored base IDs -> used native source spans -> raw fixture-group order
 * -> original five-key natural fallback. Token index and used flags are C
 * compiler products. No host used-ID set, natural ranks or sorted IDs. */
typedef struct {uint64_t base_n,ids_n;const uint64_t *base,*ids;uint32_t reserved;} r6_group_raw;
typedef struct {const r6_order* spans;const uint8_t *active,*track;} r6_used_slot;
typedef struct {
    const r6_fixture_raw* fixtures;uint64_t fixtures_n;
    const uint64_t* authored_ids;uint64_t authored_n;
    const r6_group_raw* groups;uint64_t groups_n;
    const r6_used_slot* slots;uint64_t slots_n,external_limit;uint32_t decimal_limit;
} r6_base_call;

typedef struct {uint64_t fixture;r6_natural name,id;double address;uint32_t universe,reserved;} r6_base_row;
typedef struct {
    r1_ledger* ledger;r6_base_call call;void* owned;uint64_t bytes,stage,cursor,scan,slot,row,channel,n,rows_n;
    uint64_t *ids;r6_base_row *rows,*scratch;r6_token_index index;r6_token_probe probe;
    r6_natural_scan number;r6_natural_comparison comparison;uint32_t number_phase,compare_phase,cancelled;
    uint64_t group,member,external_min,width,begin,left,right,mid,end,destination,string_cursor;
    uint32_t merging,comparison_active;int compare_result;
} r6_base_order;

static int r6_base_create(r6_base_order* out,r1_ledger* ledger,const r6_base_call* call)
{
    uint64_t bytes=0,external=0;
    if (!out || !call || (call->fixtures_n && !call->fixtures) || (call->authored_n && !call->authored_ids)
            || (call->groups_n && !call->groups) || (call->slots_n && !call->slots)) return R1_INVALID;
    if (!r3_add_bytes(&bytes,call->fixtures_n,2*sizeof(r6_base_row)+sizeof(uint64_t))
            || !r3_add_bytes(&external,call->fixtures_n,sizeof(r6_fixture_raw))
            || !r3_add_bytes(&external,call->authored_n,sizeof(uint64_t))
            || !r3_add_bytes(&external,call->groups_n,sizeof(r6_group_raw)) || external>call->external_limit) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;out->external_min=external;
    if (call->fixtures_n) {
        out->owned=out->rows=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
        out->scratch=out->rows+call->fixtures_n;out->ids=(uint64_t*)(out->scratch+call->fixtures_n);
    }
    return r6_token_create(&out->index,ledger,call->fixtures_n);
}

static void r6_base_probe_reset(r6_base_order* out,uint64_t key)
{
    memset(&out->probe,0,sizeof(out->probe));out->probe.key=key;
}

static int r6_base_index_one(r6_base_order* out)
{
    if (out->cursor==out->call.fixtures_n) {out->cursor=0;out->stage=1;return R1_OK;}
    uint64_t key=out->call.fixtures[out->cursor].patch.token;if (!key) {out->cursor++;return R1_OK;}
    if (!out->probe.started) out->probe.key=key;
    int status=r6_token_probe_one(&out->index,&out->probe);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    r6_token_entry* entry=out->index.entries+out->probe.cursor;entry->key=key;entry->value=out->cursor;entry->present=1;
    out->cursor++;r6_base_probe_reset(out,0);return R1_OK;
}

static int r6_base_emit_probe(r6_base_order* out,int require_used)
{
    int status=r6_token_probe_one(&out->index,&out->probe);if (status==R1_BUILDING) return status;if (status) return status;
    r6_token_entry* entry=out->index.entries+out->probe.cursor;
    if (out->probe.found && (!require_used || (entry->aux&2)) && !(entry->aux&1)) {
        if (out->n==out->call.fixtures_n) return R1_INVALID;out->ids[out->n++]=entry->key;entry->aux|=1;
    }
    return R1_OK;
}

static int r6_base_authored_one(r6_base_order* out)
{
    if (out->cursor==out->call.authored_n) {out->cursor=0;out->stage=out->n ? 10 : 2;return R1_OK;}
    if (!out->probe.started) out->probe.key=out->call.authored_ids[out->cursor];
    int status=r6_base_emit_probe(out,0);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    out->cursor++;r6_base_probe_reset(out,0);return R1_OK;
}

static int r6_base_used_one(r6_base_order* out)
{
    if (out->slot==out->call.slots_n) {out->stage=4;out->group=out->member=0;return R1_OK;}
    const r6_used_slot* slot=out->call.slots+out->slot;
    if (!slot->spans || slot->spans->stage!=4 || !slot->active) return R1_INVALID;
    if (out->row==slot->spans->n) {out->slot++;out->row=out->channel=0;return R1_OK;}
    const r6_order_row* row=slot->spans->rows+out->row;
    if (!(row->start>=0 && row->start<row->end && row->end<=512)) {out->row++;return R1_OK;}
    if (!out->channel) out->channel=(uint64_t)row->start+1;
    uint64_t c=out->channel-1;
    if (slot->active[c] && (!slot->track || slot->track[c])) {
        r6_base_probe_reset(out,slot->spans->call.fixtures[row->fixture].patch.token);out->stage=3;return R1_OK;
    }
    if (out->channel++==(uint64_t)row->end) {out->row++;out->channel=0;}return R1_OK;
}

static int r6_base_mark_used_one(r6_base_order* out)
{
    int status=r6_token_probe_one(&out->index,&out->probe);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    if (out->probe.found) out->index.entries[out->probe.cursor].aux|=2;
    out->row++;out->channel=0;out->stage=2;r6_base_probe_reset(out,0);return R1_OK;
}

static int r6_base_group_one(r6_base_order* out)
{
    if (out->group==out->call.groups_n) {out->stage=out->n ? 10 : 5;out->cursor=out->scan=0;return R1_OK;}
    const r6_group_raw* group=out->call.groups+out->group;
    if (!out->member && !out->probe.started) {
        if (group->reserved || (group->base_n && !group->base) || (group->ids_n && !group->ids)) return R1_INVALID;
        if (!r3_add_bytes(&out->external_min,group->base_n,sizeof(uint64_t))
                || !r3_add_bytes(&out->external_min,group->ids_n,sizeof(uint64_t)) || out->external_min>out->call.external_limit) return R1_BUDGET;
    }
    uint64_t n=group->base_n ? group->base_n : group->ids_n;const uint64_t* ids=group->base_n ? group->base : group->ids;
    if (out->member==n) {out->group++;out->member=0;return R1_OK;}
    if (!out->probe.started) out->probe.key=ids[out->member];
    int status=r6_base_emit_probe(out,1);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    out->member++;r6_base_probe_reset(out,0);return R1_OK;
}

static int r6_base_natural_select(r6_base_order* out)
{
    if (out->cursor==out->call.fixtures_n) {out->stage=8;out->width=1;out->begin=0;return R1_OK;}
    const r6_fixture_raw* fixture=out->call.fixtures+out->cursor;
    if (!out->probe.started) out->probe.key=fixture->patch.token;
    int status=r6_token_probe_one(&out->index,&out->probe);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    int used=out->probe.found && (out->index.entries[out->probe.cursor].aux&2);r6_base_probe_reset(out,0);
    if (!used) {out->cursor++;return R1_OK;}
    if (!r3_add_bytes(&out->external_min,fixture->name.bytes,1)
            || !r3_add_bytes(&out->external_min,fixture->source_id.bytes,1) || out->external_min>out->call.external_limit) return R1_BUDGET;
    r6_base_row* row=out->rows+out->rows_n;row->fixture=out->cursor;row->universe=fixture->patch.universe ? fixture->patch.universe : 1;
    status=r6_order_address(fixture->patch.start_channel,1.,&row->address);if (status) return status;
    status=r6_natural_begin(&out->number,&fixture->name,out->call.decimal_limit);if (!status) out->stage=6;return status;
}

static int r6_base_natural_one(r6_base_order* out)
{
    int status=r6_natural_one(&out->number);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    r6_base_row* row=out->rows+out->rows_n;
    if (out->stage==6) {
        row->name=out->number.result;status=r6_natural_begin(&out->number,&out->call.fixtures[out->cursor].source_id,out->call.decimal_limit);
        if (!status) out->stage=7;return status;
    }
    row->id=out->number.result;out->rows_n++;out->cursor++;out->stage=5;return R1_OK;
}

static int r6_base_string_compare(r6_base_order* out,const r5_text* left,const r5_text* right)
{
    uint64_t n=left->bytes<right->bytes ? left->bytes : right->bytes,remaining=n-out->string_cursor;
    uint64_t count=remaining<64 ? remaining : 64;
    int result=count ? memcmp(left->utf8+out->string_cursor,right->utf8+out->string_cursor,(size_t)count) : 0;
    out->string_cursor+=count;
    if (result) {out->compare_result=result<0 ? -1 : 1;return R1_OK;}
    if (out->string_cursor==n) {out->compare_result=left->bytes<right->bytes ? -1 : left->bytes>right->bytes;return R1_OK;}
    return R1_BUILDING;
}

static int r6_base_compare_one(r6_base_order* out)
{
    const r6_base_row *a=out->rows+out->left,*b=out->rows+out->right;
    if (out->compare_phase<2) {
        if (!out->comparison_active) {
            memset(&out->comparison,0,sizeof(out->comparison));out->comparison.left=out->compare_phase ? a->id : a->name;
            out->comparison.right=out->compare_phase ? b->id : b->name;out->comparison_active=1;
        }
        int status=r6_natural_compare_one(&out->comparison);if (status==R1_BUILDING) return status;
        out->compare_result=out->comparison.result;if (out->compare_result) return R1_OK;
        out->comparison_active=0;out->compare_phase++;return R1_BUILDING;
    }
    if (a->universe!=b->universe) {out->compare_result=a->universe<b->universe ? -1 : 1;return R1_OK;}
    if (a->address!=b->address) {out->compare_result=a->address<b->address ? -1 : 1;return R1_OK;}
    return r6_base_string_compare(out,&out->call.fixtures[a->fixture].source_id,&out->call.fixtures[b->fixture].source_id);
}

static int r6_base_sort_one(r6_base_order* out)
{
    uint64_t n=out->rows_n;
    if (out->width>=n) {out->stage=9;out->cursor=0;return R1_OK;}
    if (out->begin==n) {r6_base_row* swap=out->rows;out->rows=out->scratch;out->scratch=swap;out->begin=0;out->width*=2;return R1_OK;}
    if (!out->merging) {
        out->mid=out->begin+out->width;if (out->mid>n) out->mid=n;out->end=out->mid+out->width;if (out->end>n) out->end=n;
        out->left=out->begin;out->right=out->mid;out->destination=out->begin;out->merging=1;
    }
    int left=out->right==out->end;
    if (out->left<out->mid && out->right<out->end) {
        int status=r6_base_compare_one(out);if (status==R1_BUILDING) return R1_OK;if (status) return status;left=out->compare_result<=0;
    }
    out->scratch[out->destination++]=out->rows[left ? out->left++ : out->right++];
    out->compare_phase=out->comparison_active=0;out->string_cursor=0;
    if (out->destination==out->end) {out->begin=out->end;out->merging=0;}return R1_OK;
}

static int r6_base_dedup_one(r6_base_order* out)
{
    if (out->cursor==out->rows_n) {out->stage=10;return R1_OK;}
    if (!out->probe.started) out->probe.key=out->call.fixtures[out->rows[out->cursor].fixture].patch.token;
    int status=r6_base_emit_probe(out,1);if (status==R1_BUILDING) return R1_OK;if (status) return status;
    out->cursor++;r6_base_probe_reset(out,0);return R1_OK;
}

static int r6_base_step(r6_base_order* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=10 && !status) switch(out->stage) {
    case 0:status=r6_base_index_one(out);break;case 1:status=r6_base_authored_one(out);break;
    case 2:status=r6_base_used_one(out);break;case 3:status=r6_base_mark_used_one(out);break;
    case 4:status=r6_base_group_one(out);break;case 5:status=r6_base_natural_select(out);break;
    case 6:case 7:status=r6_base_natural_one(out);break;case 8:status=r6_base_sort_one(out);break;
    case 9:status=r6_base_dedup_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==10 ? R1_OK : R1_BUILDING;
}

static void r6_base_destroy(r6_base_order* out)
{
    r6_token_destroy(&out->index);if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
