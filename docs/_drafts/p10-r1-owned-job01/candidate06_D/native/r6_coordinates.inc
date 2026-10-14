/* Exact complete-snapshot vs project/page precedence. Native ids come from
 * the owning order compiler, not a host chosen geometry result. Numeric-string
 * compatibility is pending rather than silently reinterpreted as defaults. */
static int r6_coordinate_number(r1_atom raw,double fallback,double* output)
{
    if (raw.kind==R1_UNSUPPORTED) return R1_PENDING;
    return r5_number(raw,fallback,output);
}

static int r6_coordinate_or_number(r1_atom raw,double missing,double* output)
{
    int status=r6_coordinate_number(raw,missing,output);if (!status && *output==0.) *output=0.;return status;
}

static int r6_coordinates_create(r6_coordinates* out,r1_ledger* ledger,
                                 const r6_coordinate_raw* raw,const uint64_t* native_ids,uint64_t n)
{
    uint64_t bytes=0,external=0;
    if (!out || !raw || (n && !native_ids) || (raw->fixtures_n && !raw->fixtures)
            || (raw->snapshot_n && !raw->snapshot) || (raw->pages_n && !raw->pages)) return R1_INVALID;
    if (!r3_add_bytes(&bytes,n,2*sizeof(double)+sizeof(int64_t)+1)) return R1_BUDGET;
    if (!r3_add_bytes(&external,raw->fixtures_n,sizeof(r6_fixture_raw))
            || !r3_add_bytes(&external,raw->snapshot_n,sizeof(r6_snapshot_raw))
            || !r3_add_bytes(&external,raw->pages_n,sizeof(r6_page_raw)) || external>raw->external_limit) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->raw=raw;out->native_ids=native_ids;out->n=n;out->bytes=bytes;
    if (n) {
        out->xy=r1_allocate(ledger,bytes);if (!out->xy) return R1_BUDGET;
        out->fixture=(int64_t*)(out->xy+2*n);out->snapshot_valid=(uint8_t*)(out->fixture+n);
    }
    out->external_min=external;out->selected_snapshot=UINT64_MAX;out->complete_snapshot=n!=0;
    if (!n) out->stage=5;return R1_OK;
}

static int r6_snapshot_one(r6_coordinates* out)
{
    const r6_coordinate_raw* raw=out->raw;
    if (out->row==out->n) {
        out->row=out->scan=0;out->stage=out->complete_snapshot && out->has_snapshot ? 5 : 1;return R1_OK;
    }
    if (raw->use_snapshot && out->scan<raw->snapshot_n) {
        uint64_t i=out->scan++;if (raw->snapshot[i].token==out->native_ids[out->row]) out->selected_snapshot=i;
        return R1_OK;
    }
    int valid=0;double x=0.,y=0.;
    if (out->selected_snapshot!=UINT64_MAX) {
        const r6_snapshot_raw* entry=raw->snapshot+out->selected_snapshot;
        if ((entry->container_kind==1 || entry->container_kind==2) && entry->count>=2) {
            /* Snapshot does float(raw[0]), not float(raw[0] or 0). None raises
             * in its caught tuple conversion and makes the entire snapshot
             * incomplete; project/page fields below do have an 'or 0'. */
            if (entry->x.kind!=R1_NONE && entry->y.kind!=R1_NONE) {
                int status=r6_coordinate_number(entry->x,0.,&x);if (!status) status=r6_coordinate_number(entry->y,0.,&y);
                if (status) return status;valid=1;
            }
        }
    }
    out->xy[2*out->row]=x;out->xy[2*out->row+1]=y;out->snapshot_valid[out->row]=(uint8_t)valid;
    out->has_snapshot|=valid;out->complete_snapshot&=valid;out->fixture[out->row]=-1;
    out->row++;out->scan=0;out->selected_snapshot=UINT64_MAX;return R1_OK;
}

static int r6_project_point_one(r6_coordinates* out)
{
    const r6_coordinate_raw* raw=out->raw;
    if (out->row==out->n) {out->stage=2;out->row=out->scan=0;return R1_OK;}
    if (raw->project_present && out->scan<raw->fixtures_n) {
        uint64_t i=out->scan++;if (raw->fixtures[i].patch.token==out->native_ids[out->row]) out->fixture[out->row]=(int64_t)i;
        return R1_OK;
    }
    double x=0.,y=0.;int64_t selected=out->fixture[out->row];
    if (selected>=0) {
        const r6_fixture_raw* fixture=raw->fixtures+(uint64_t)selected;
        int status=r6_coordinate_or_number(fixture->pos_x,0.,&x);if (!status) status=r6_coordinate_or_number(fixture->pos_y,0.,&y);
        if (status) return status;
    }
    out->xy[2*out->row]=x;out->xy[2*out->row+1]=y;out->row++;out->scan=0;return R1_OK;
}

static int r6_page_begin(r6_coordinates* out)
{
    double value;int status=r5_number(out->raw->active_page,0.,&value);if (status) return status;
    if (!isfinite(value)) value=0.;value=trunc(value);
    out->active_valid=value>=0. && value<(double)out->raw->pages_n;
    if (out->active_valid) out->active_page=(uint64_t)value;
    out->stage=3;out->page=out->row=out->point=out->visible_scan=0;return R1_OK;
}

static uint64_t r6_page_index(const r6_coordinates* out)
{
    if (!out->active_valid) return out->page;
    if (out->page==out->raw->pages_n-1) return out->active_page;
    return out->page>=out->active_page ? out->page+1 : out->page;
}

static int r6_page_coordinate_one(r6_coordinates* out)
{
    if (out->page==out->raw->pages_n || !out->raw->project_present) {out->stage=5;return R1_OK;}
    const r6_page_raw* page=out->raw->pages+r6_page_index(out);
    if (!out->page_admitted) {
        uint64_t bytes=out->external_min;
        if (!r3_add_bytes(&bytes,page->points_n,sizeof(r6_page_point_raw))
                || !r3_add_bytes(&bytes,page->visible_n,sizeof(uint64_t)) || bytes>out->raw->external_limit) return R1_BUDGET;
        out->external_min=bytes;out->page_admitted=1;return R1_OK;
    }
    if (!page->dict_kind || out->point==page->points_n || (page->visible_present && page->visible_kind!=1)) {
        out->page++;out->row=out->point=out->visible_scan=out->selected_visible=out->page_admitted=0;return R1_OK;
    }
    if (page->reserved || (page->points_n && !page->points) || (page->visible_n && !page->visible)) return R1_INVALID;
    const r6_page_point_raw* point=page->points+out->point;
    if (page->visible_present && out->visible_scan<page->visible_n) {
        out->selected_visible|=page->visible[out->visible_scan++]==point->token;return R1_OK;
    }
    if (out->row<out->n) {
        uint64_t row=out->row++;
        if (point->token!=out->native_ids[row] || out->fixture[row]<0 || !point->dict_kind
                || (page->visible_present && !out->selected_visible)) return R1_OK;
        if (point->reserved) return R1_INVALID;
        r1_atom x=point->x.kind==R1_MISSING ? point->pos_x : point->x;
        r1_atom y=point->y.kind==R1_MISSING ? point->pos_y : point->y;double next_x,next_y;
        int status=r6_coordinate_or_number(x,out->xy[2*row],&next_x);
        if (!status) status=r6_coordinate_or_number(y,out->xy[2*row+1],&next_y);if (status) return status;
        out->xy[2*row]=next_x;out->xy[2*row+1]=next_y;return R1_OK;
    }
    out->point++;out->row=out->visible_scan=out->selected_visible=0;return R1_OK;
}

static int r6_coordinates_step(r6_coordinates* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=5 && !status) switch(out->stage) {
    case 0:status=r6_snapshot_one(out);break;
    case 1:status=r6_project_point_one(out);break;
    case 2:status=r6_page_begin(out);break;
    case 3:status=r6_page_coordinate_one(out);break;
    default:return R1_INVALID;
    }
    return status ? status : out->stage==5 ? R1_OK : R1_BUILDING;
}

static void r6_coordinates_destroy(r6_coordinates* out)
{
    if (out->ledger) r1_free(out->ledger,out->xy,out->bytes);memset(out,0,sizeof(*out));
}
