/* Internal owning fixture/order compiler. No host rank/span input is accepted.
 * Explicit authored IDs are compiled first; global fallback membership/group/
 * natural-number origin is an explicit separate stage, never fabricated. */
typedef struct {
    uint64_t object_token,name_token;r1_atom channel_count,channels_scalar;
    uint64_t channels_n;uint32_t channels_kind,reserved;
} r6_definition_raw;
typedef struct {
    const r6_fixture_raw* fixtures;uint64_t fixtures_n;
    const r6_definition_raw* definitions;uint64_t definitions_n;
    const uint64_t* authored_ids;uint64_t ids_n;uint32_t universe;
    const uint8_t* native_base;const uint8_t* native_rule;uint64_t external_limit;
    uint32_t internal_address_only; /* native source-span/used-ID stage, no host ranks */
} r6_order_call;
typedef struct {
    uint64_t fixture,rank;double address;int32_t start,end;
} r6_order_row;
typedef struct {
    r1_ledger* ledger;r6_order_call call;void* owned;uint64_t bytes,stage,cursor,scan,id_cursor,n,valid_ids_n;
    uint64_t *valid_ids,*valid_ranks;r6_order_row *rows,*scratch;
    uint64_t width,begin,left,right,mid,end,destination;uint32_t merging,cancelled,found,base_found;
    uint64_t selected_definition;double channel_count;
} r6_order;

static int r6_order_create(r6_order* out,r1_ledger* ledger,const r6_order_call* call)
{
    uint64_t bytes=0,external=0;
    if (!out || !call || call->internal_address_only>1 || (call->fixtures_n && !call->fixtures) || (call->definitions_n && !call->definitions)
            || (call->ids_n && !call->authored_ids)) return R1_INVALID;
    if (!r3_add_bytes(&bytes,call->fixtures_n,2*sizeof(r6_order_row)+2*sizeof(uint64_t))
            || !r3_add_bytes(&external,call->fixtures_n,sizeof(r6_fixture_raw))
            || !r3_add_bytes(&external,call->definitions_n,sizeof(r6_definition_raw))
            || !r3_add_bytes(&external,call->ids_n,sizeof(uint64_t)) || external>call->external_limit) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->bytes=bytes;out->stage=call->internal_address_only ? 1 : 0;
    if (call->fixtures_n) {
        out->owned=out->rows=r1_allocate(ledger,bytes);if (!out->owned) return R1_BUDGET;
        out->scratch=out->rows+call->fixtures_n;out->valid_ids=(uint64_t*)(out->scratch+call->fixtures_n);
        out->valid_ranks=out->valid_ids+call->fixtures_n;
    }
    return R1_OK;
}

static int r6_order_authored_one(r6_order* out)
{
    if (out->id_cursor==out->call.ids_n) {
        if (!out->valid_ids_n) return R1_PENDING; /* actual global fallback is not default empty */
        out->stage=1;out->cursor=out->scan=0;return R1_OK;
    }
    uint64_t token=out->call.authored_ids[out->id_cursor];
    if (out->cursor<out->valid_ids_n) {
        if (out->valid_ids[out->cursor++]==token) {out->id_cursor++;out->cursor=out->scan=out->found=0;}
        return R1_OK;
    }
    if (out->scan<out->call.fixtures_n) {
        out->found|=out->call.fixtures[out->scan++].patch.token==token;return R1_OK;
    }
    if (out->found && token) {
        if (out->valid_ids_n==out->call.fixtures_n) return R1_INVALID;
        out->valid_ids[out->valid_ids_n]=token;out->valid_ranks[out->valid_ids_n]=out->valid_ids_n;out->valid_ids_n++;
    }
    out->id_cursor++;out->cursor=out->scan=out->found=0;return R1_OK;
}

static int r6_order_address(r1_atom atom,double fallback,double* out)
{
    int status=r5_number(atom,fallback,out);if (status) return status;
    if (*out==0.) *out=fallback;
    if (!isfinite(*out)) return R1_PENDING;
    /* All ordinary DMX addresses are exact. Arbitrary-int sorting outside
     * binary64 integer precision is not silently rounded into a false order. */
    if (fabs(*out)>9007199254740992.) return R1_PENDING;
    *out=trunc(*out);return R1_OK;
}

static int r6_order_select_one(r6_order* out)
{
    if (out->cursor==out->call.fixtures_n) {out->stage=2;out->width=1;out->begin=0;return R1_OK;}
    const r6_fixture_raw* fixture=out->call.fixtures+out->cursor;
    if (fixture->patch.universe!=out->call.universe) {out->cursor++;out->scan=0;return R1_OK;}
    if (out->call.internal_address_only) {
        double address;int status=r6_order_address(fixture->patch.start_channel,1.,&address);if (status) return status;
        out->rows[out->n++]=(r6_order_row){out->cursor,0,address,-1,-1};out->cursor++;return R1_OK;
    }
    if (out->scan<out->valid_ids_n) {
        uint64_t i=out->scan++;
        if (out->valid_ids[i]==fixture->patch.token) {
            double address;int status=r6_order_address(fixture->patch.start_channel,1.,&address);if (status) return status;
            out->rows[out->n++]=(r6_order_row){out->cursor,out->valid_ranks[i],address,-1,-1};out->cursor++;out->scan=0;
        }
        return R1_OK;
    }
    out->cursor++;out->scan=0;return R1_OK;
}

static int r6_order_before(const r6_order_row* left,const r6_order_row* right)
{
    return left->rank<right->rank || (left->rank==right->rank && (left->address<right->address
        || (left->address==right->address && left->fixture<right->fixture)));
}

static int r6_order_sort_one(r6_order* out)
{
    uint64_t n=out->n;
    if (out->width>=n) {out->stage=3;out->cursor=out->scan=0;out->selected_definition=UINT64_MAX;return R1_OK;}
    if (out->begin==n) {
        r6_order_row* swap=out->rows;out->rows=out->scratch;out->scratch=swap;out->begin=0;out->width*=2;return R1_OK;
    }
    if (!out->merging) {
        out->mid=out->begin+out->width;if (out->mid>n) out->mid=n;
        out->end=out->mid+out->width;if (out->end>n) out->end=n;
        out->left=out->begin;out->right=out->mid;out->destination=out->begin;out->merging=1;
    }
    int left=out->left<out->mid && (out->right==out->end || !r6_order_before(out->rows+out->right,out->rows+out->left));
    out->scratch[out->destination++]=out->rows[left ? out->left++ : out->right++];
    if (out->destination==out->end) {out->begin=out->end;out->merging=0;}
    return R1_OK;
}

static int r6_order_count(const r6_definition_raw* definition,double* output)
{
    int status=r5_number(definition->channel_count,0.,output);if (status) return status;
    if (!isfinite(*output)) return R1_PENDING;*output=trunc(*output);
    if (*output>0.) return R1_OK;
    if (definition->channels_kind==1) {*output=(double)definition->channels_n;return R1_OK;}
    status=r5_number(definition->channels_scalar,0.,output);if (status) return status;
    if (!isfinite(*output)) *output=0.;else *output=trunc(*output);return R1_OK;
}

static int r6_order_span_one(r6_order* out)
{
    if (out->cursor==out->n) {out->stage=4;return R1_OK;}
    r6_order_row* row=out->rows+out->cursor;const r4_patch_fixture* fixture=&out->call.fixtures[row->fixture].patch;
    if (row->address<1. || row->address>512.) {
        /* Original (start,start) outside512 is only used by validity tests.
         * Keep its no-channel semantics without any out-of-range C int cast. */
        row->start=row->end=-1;out->cursor++;return R1_OK;
    }
    if (out->scan<out->call.definitions_n) {
        uint64_t i=out->scan++;const r6_definition_raw* definition=out->call.definitions+i;
        int matches=fixture->attached_definition ? definition->object_token==fixture->attached_definition
                                                : definition->name_token==fixture->definition;
        if (matches) out->selected_definition=i;return R1_OK;
    }
    double count=0.;int status=R1_OK;
    if (out->selected_definition!=UINT64_MAX) status=r6_order_count(out->call.definitions+out->selected_definition,&count);
    if (status) return status;
    row->start=(int32_t)row->address-1;
    if (count<=0. && out->cursor+1<out->n) count=fmax(1.,out->rows[out->cursor+1].address-1.-row->start);
    if (!(count>0.)) count=1.;row->end=count>=512.-row->start ? 512 : row->start+(int32_t)count;
    out->cursor++;out->scan=0;out->selected_definition=UINT64_MAX;return R1_OK;
}

static int r6_order_step(r6_order* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=4 && !status) switch(out->stage) {
    case 0:status=r6_order_authored_one(out);break;case 1:status=r6_order_select_one(out);break;
    case 2:status=r6_order_sort_one(out);break;case 3:status=r6_order_span_one(out);break;
    default:return R1_INVALID;
    }
    return status ? status : out->stage==4 ? R1_OK : R1_BUILDING;
}

static void r6_order_destroy(r6_order* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
