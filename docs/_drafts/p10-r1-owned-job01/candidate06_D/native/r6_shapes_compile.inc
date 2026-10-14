/* Raw page shape enumeration from _topview_circle_shapes_for_ids. Source
 * page order and cad_shapes-before-pattern_matrices order are retained. */
typedef struct {
    uint32_t dict_kind,reserved;r5_text type;uint64_t token;
    r1_atom rx,ry,cx,cy,angle;
} r6_shape_raw;

typedef struct {
    const r6_page_raw* page;uint64_t cad_n,pattern_n;
    const r6_shape_raw *cad,*pattern;
} r6_shape_page_raw;

typedef struct {
    const r6_shape_page_raw* pages;uint64_t pages_n;
    const uint64_t* native_wanted;uint64_t wanted_n,external_limit;
} r6_shapes_call;

typedef struct {
    r1_ledger* ledger;r6_shapes_call call;void* owned;uint64_t bytes,stage,page,shape,count,capacity;
    uint64_t wanted,visible,external_min;uint32_t intersects,cancelled;
    double* values;uint64_t* source_tokens;
} r6_shapes;

static int r6_exact_text(const r5_text* text,const char* literal)
{
    if (text->reserved || text->kind>3 || (text->bytes && !text->utf8)) return R1_PENDING;
    return text->kind==1 && text->bytes==strlen(literal) && memcmp(text->utf8,literal,(size_t)text->bytes)==0;
}

static int r6_shapes_create(r6_shapes* out,r1_ledger* ledger,const r6_shapes_call* call)
{
    uint64_t external=0;
    if (!out || !call || (call->pages_n && !call->pages) || (call->wanted_n && !call->native_wanted)) return R1_INVALID;
    if (!r3_add_bytes(&external,call->pages_n,sizeof(r6_shape_page_raw)) || external>call->external_limit) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->call=*call;out->external_min=external;return R1_OK;
}

static int r6_shapes_admit_one(r6_shapes* out)
{
    if (out->page==out->call.pages_n) {
        if (!r3_add_bytes(&out->bytes,out->capacity,5*sizeof(double)+sizeof(uint64_t))) return R1_BUDGET;
        if (out->capacity) {
            out->owned=out->values=r1_allocate(out->ledger,out->bytes);if (!out->owned) return R1_BUDGET;
            out->source_tokens=(uint64_t*)(out->values+5*out->capacity);
        }
        out->page=0;out->stage=1;return R1_OK;
    }
    const r6_shape_page_raw* page=out->call.pages+out->page++;
    if (!page->page || (page->cad_n && !page->cad) || (page->pattern_n && !page->pattern)) return R1_INVALID;
    const r6_page_raw* visibility=page->page;
    if (visibility->reserved || visibility->dict_kind>1 || visibility->visible_present>1 || visibility->visible_kind>1
            || (visibility->visible_n && !visibility->visible) || (visibility->points_n && !visibility->points)) return R1_INVALID;
    if (!r3_add_bytes(&out->external_min,1,sizeof(r6_page_raw))
            || !r3_add_bytes(&out->external_min,page->cad_n,sizeof(r6_shape_raw))
            || !r3_add_bytes(&out->external_min,page->pattern_n,sizeof(r6_shape_raw))
            || !r3_add_bytes(&out->external_min,visibility->visible_n,sizeof(uint64_t))
            || !r3_add_bytes(&out->external_min,visibility->points_n,sizeof(r6_page_point_raw))
            || !r3_add_bytes(&out->capacity,page->cad_n,1) || !r3_add_bytes(&out->capacity,page->pattern_n,1)) return R1_BUDGET;
    return out->external_min>out->call.external_limit ? R1_BUDGET : R1_OK;
}

static uint64_t r6_page_id_count(const r6_page_raw* page)
{
    return page->visible_present ? page->visible_kind==1 ? page->visible_n : 0 : page->points_n;
}

static int r6_shapes_page_one(r6_shapes* out)
{
    if (out->page==out->call.pages_n) {out->stage=3;return R1_OK;}
    const r6_page_raw* page=out->call.pages[out->page].page;
    if (!page->dict_kind) {out->page++;return R1_OK;}
    if (!out->call.wanted_n) {out->stage=2;out->shape=0;return R1_OK;}
    if (out->wanted==out->call.wanted_n || out->intersects) {
        out->wanted=out->visible=0;
        if (out->intersects) {out->stage=2;out->shape=0;out->intersects=0;} else out->page++;
        return R1_OK;
    }
    uint64_t n=r6_page_id_count(page);
    if (out->visible==n) {out->wanted++;out->visible=0;return R1_OK;}
    uint64_t token=page->visible_present ? page->visible[out->visible] : page->points[out->visible].token;
    out->visible++;out->intersects=token && token==out->call.native_wanted[out->wanted];return R1_OK;
}

static int r6_shape_numbers(const r6_shape_raw* raw,int ellipse,double* values)
{
    double rx,ry;int status;
    status=r5_number(raw->rx,80.,&rx);if (status) return status;if (rx==0.) rx=80.;rx=fabs(rx);
    status=r5_number(raw->ry,rx,&ry);if (status) return status;if (ry==0.) ry=rx;ry=fabs(ry);
    if (rx<1. || ry<1.) return 1;
    status=r6_coordinate_or_number(raw->cx,0.,values);if (status) return status;
    status=r6_coordinate_or_number(raw->cy,0.,values+1);if (status) return status;
    values[2]=rx;values[3]=ry;values[4]=0.;
    return ellipse ? r6_coordinate_or_number(raw->angle,0.,values+4) : R1_OK;
}

static int r6_shapes_shape_one(r6_shapes* out)
{
    const r6_shape_page_raw* page=out->call.pages+out->page;
    /* checked admission proved cad_n+pattern_n fits before any traversal */
    uint64_t total=page->cad_n+page->pattern_n;
    if (out->shape==total) {out->page++;out->stage=1;out->shape=0;return R1_OK;}
    uint64_t i=out->shape++;const r6_shape_raw* raw=i<page->cad_n ? page->cad+i : page->pattern+(i-page->cad_n);
    if (raw->reserved || raw->dict_kind>1) return R1_INVALID;
    if (!raw->dict_kind) return R1_OK;
    int circle=r6_exact_text(&raw->type,"circle"),ellipse=r6_exact_text(&raw->type,"ellipse");
    if (circle<0 || ellipse<0) return R1_PENDING;if (!circle && !ellipse) return R1_OK;
    double values[5];int status=r6_shape_numbers(raw,ellipse,values);if (status<0) return status;if (status) return R1_OK;
    memcpy(out->values+5*out->count,values,sizeof(values));out->source_tokens[out->count++]=raw->token;return R1_OK;
}

static int r6_shapes_step(r6_shapes* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=3 && !status) switch(out->stage) {
    case 0:status=r6_shapes_admit_one(out);break;case 1:status=r6_shapes_page_one(out);break;
    case 2:status=r6_shapes_shape_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==3 ? R1_OK : R1_BUILDING;
}

static void r6_shapes_destroy(r6_shapes* out)
{
    if (out->ledger) r1_free(out->ledger,out->owned,out->bytes);memset(out,0,sizeof(*out));
}
