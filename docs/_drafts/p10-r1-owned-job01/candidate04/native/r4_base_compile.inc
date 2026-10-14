/* Raw fixture-id and patch spans. Repeated base ids only repeat an idempotent
 * union; no host dedup/order or computed channel spans are accepted. */
static int r4_base_create(r4_base_mask* out,const r4_patch_fixture* fixtures,
                          const r4_patch_definition* definitions,const uint64_t* ids)
{
    if (!out || (out->fixtures_n && !fixtures) || (out->definitions_n && !definitions)
            || (out->base_n && !ids)) return R1_INVALID;
    out->fixtures=fixtures;out->definitions=definitions;out->base_ids=ids;
    out->last_fixture=out->last_definition=UINT64_MAX;return R1_OK;
}

static int r4_base_span(r4_base_mask* out)
{
    const r4_patch_fixture* fixture=out->fixtures+out->last_fixture;
    const r4_patch_definition* def=out->last_definition==UINT64_MAX ? NULL : out->definitions+out->last_definition;
    double address,count=0.;
    if (r1_atom_number(fixture->start_channel,1.,&address)) return R1_PENDING;
    if (address==0.) address=1.;address=trunc(address);
    if (address<1. || address>512.) return R1_OK;
    if (def) {
        if (r1_atom_number(def->channel_count,0.,&count)) return R1_PENDING;
        count=trunc(count);if (count<=0.) count=(double)def->types_n;
    }
    if (count<=0.) count=1.;
    int start=(int)address-1,end=count>512.-start ? 512 : start+(int)count;
    for (int c=start;c<end;c++) out->base[c]=1;
    return R1_OK;
}

static int r4_base_one(r4_base_mask* out)
{
    if (out->phase==0) {
        if (out->fixture_cursor<out->fixtures_n) {
            uint64_t index=out->fixture_cursor++;
            if (out->fixtures[index].token==out->base_ids[out->id_cursor]) out->last_fixture=index;
            return R1_OK;
        }
        if (out->last_fixture==UINT64_MAX || out->fixtures[out->last_fixture].universe!=out->universe) out->phase=2;
        else out->phase=1;
    } else if (out->phase==1) {
        const r4_patch_fixture* fixture=out->fixtures+out->last_fixture;
        uint64_t token=fixture->attached_definition ? fixture->attached_definition : fixture->definition;
        if (out->definition_cursor<out->definitions_n) {
            uint64_t index=out->definition_cursor++;
            if (out->definitions[index].token==token) out->last_definition=index;
            return R1_OK;
        }
        int status=r4_base_span(out);if (status) return status;out->phase=2;
    } else {
        out->id_cursor++;out->fixture_cursor=out->definition_cursor=0;
        out->last_fixture=out->last_definition=UINT64_MAX;out->phase=0;
    }
    return R1_OK;
}

static int r4_base_step(r4_base_mask* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;
    if (out->cancelled) return R1_CANCELLED;
    if (out->semantic) return R1_PENDING;
    while (units-- && out->id_cursor<out->base_n) {
        int status=r4_base_one(out);if (status) {out->semantic=1;return status;}
    }
    if (out->id_cursor<out->base_n) return R1_BUILDING;
    int any=0;for (int c=0;c<512;c++) any|=out->base[c];out->base_present=any!=0;
    return R1_OK;
}
