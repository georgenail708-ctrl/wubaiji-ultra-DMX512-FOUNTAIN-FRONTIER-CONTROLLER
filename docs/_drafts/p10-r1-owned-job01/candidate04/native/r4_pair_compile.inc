/* The original expansion is generic {X}/{X}_fine, case-sensitive UTF8, not
 * merely motor enums. Names are raw encoded metadata. Oversized individual
 * names select pending rather than hiding an unbounded memcmp in one unit. */
static int r4_name_equal(const r4_channel_name* left,const r4_channel_name* right,uint64_t size)
{
    return left->bytes==size && (!size || memcmp(left->utf8,right->utf8,(size_t)size)==0);
}

static int r4_pair_name(const r4_channel_name* name)
{
    if (name->bytes>512 || (name->bytes && !name->utf8)) return R1_PENDING;
    return name->bytes>5 && memcmp(name->utf8+name->bytes-5,"_fine",5)==0;
}

static int r4_pair_fixture(r4_pair_mask* out)
{
    const r4_patch_fixture* fixture=out->fixtures+out->fixture_cursor;
    if (fixture->reserved) return R1_PENDING;
    if (fixture->universe!=out->universe) {out->fixture_cursor++;return R1_OK;}
    out->last_definition=UINT64_MAX;out->definition_cursor=0;out->phase=1;return R1_OK;
}

static int r4_pair_definition(r4_pair_mask* out)
{
    const r4_patch_fixture* fixture=out->fixtures+out->fixture_cursor;
    if (out->definition_cursor<out->definitions_n) {
        uint64_t index=out->definition_cursor++;
        if (out->definitions[index].token==fixture->definition) out->last_definition=index;
        return R1_OK;
    }
    double address;
    if (out->last_definition!=UINT64_MAX && out->definitions[out->last_definition].types_n>R3_WORKFLOW_MAX_BYTES/sizeof(r4_channel_name)) return R1_BUDGET;
    if (r1_atom_number(fixture->start_channel,1.,&address)) {
        /* This conversion is inside the original whole-loop try/except.
         * Keep the partial expansion and stop visiting remaining fixtures. */
        out->fixture_cursor=out->fixtures_n;out->phase=0;return R1_OK;
    }
    if (address==0.) address=1.;address=trunc(address);
    if (address>512. || address<=-512. || out->last_definition==UINT64_MAX) {out->fixture_cursor++;out->phase=0;return R1_OK;}
    out->start=(int)address-1;out->fine_cursor=0;out->phase=2;return R1_OK;
}

static int r4_pair_fine(r4_pair_mask* out)
{
    const r4_patch_definition* def=out->definitions+out->last_definition;
    if (def->types_n && !def->names) return R1_INVALID;
    if (out->fine_cursor==def->types_n) {out->fixture_cursor++;out->phase=0;return R1_OK;}
    int fine=r4_pair_name(def->names+out->fine_cursor);
    if (fine<0) return fine;
    if (!fine) {out->fine_cursor++;return R1_OK;}
    memset(out->pair,0,512);out->pair_any=0;out->match_cursor=0;out->phase=3;return R1_OK;
}

static int r4_pair_match(r4_pair_mask* out)
{
    const r4_patch_definition* def=out->definitions+out->last_definition;
    if (out->match_cursor==def->types_n) {
        if (out->pair_any) for (int c=0;c<512;c++) out->mask[c]|=out->pair[c];
        out->fine_cursor++;out->phase=2;return R1_OK;
    }
    uint64_t index=out->match_cursor++;
    const r4_channel_name *name=def->names+index,*fine=def->names+out->fine_cursor;
    if (r4_pair_name(name)<0) return R1_PENDING;
    if (!r4_name_equal(name,fine,fine->bytes) && !r4_name_equal(name,fine,fine->bytes-5)) return R1_OK;
    if (index>1024) return R1_OK; /* every larger index is outside any admitted DMX span */
    int channel=out->start+(int)index;
    if (channel>=0 && channel<512) {out->pair[channel]=1;out->pair_any|=out->mask[channel]!=0;}
    return R1_OK;
}

static int r4_pair_step(r4_pair_mask* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;
    if (out->cancelled) return R1_CANCELLED;
    if (out->semantic) return R1_PENDING;
    while (units-- && out->fixture_cursor<out->fixtures_n) {
        int status;
        switch(out->phase) {
        case 0:status=r4_pair_fixture(out);break;
        case 1:status=r4_pair_definition(out);break;
        case 2:status=r4_pair_fine(out);break;
        case 3:status=r4_pair_match(out);break;
        default:return R1_INVALID;
        }
        if (status) {out->semantic=1;return status;}
    }
    return out->fixture_cursor==out->fixtures_n ? R1_OK : R1_BUILDING;
}
