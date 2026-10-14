/* Stable insertion sort, one move or one <=32-byte/one-child comparison per
 * unit. Original order survives equal keys and NaN's unordered comparison. */
static uint8_t rc07_signed_byte(const uint8_t* bytes,uint64_t n,uint64_t i)
{return i<n ? bytes[i] : (bytes[n-1]&128) ? 255 : 0;}
static int rc07_numeric_compare(rctx07_job* j,rctx07_cmp_frame* f,
                                const rfi_tree_node* a,const rfi_tree_node* b)
{
    const uint8_t *ap=NULL,*bp=NULL;uint64_t an=0,bn=0;double av=0.,bv=0.;int af=a->kind==RFT_FLOAT,bf=b->kind==RFT_FLOAT;
    if (af) memcpy(&av,&a->bits,8);if (bf) memcpy(&bv,&b->bits,8);
    if ((af && isnan(av)) || (bf && isnan(bv))) {f->result=0;return R1_DONE;}
    if (af && bf) {f->result=av<bv ? -1 : av>bv;return R1_DONE;}
    if (af && isinf(av)) {f->result=av<0. ? -1 : 1;return R1_DONE;}
    if (bf && isinf(bv)) {f->result=bv<0. ? 1 : -1;return R1_DONE;}
    if (af) {rc07_int_float(av,j->numeric_a,&an);ap=j->numeric_a;} else {ap=j->program->data+a->data_first;an=a->data_count;}
    if (bf) {rc07_int_float(bv,j->numeric_b,&bn);bp=j->numeric_b;} else {bp=j->program->data+b->data_first;bn=b->data_count;}
    if (!an || !bn) return R1_INVALID;int as=(ap[an-1]&128)!=0,bs=(bp[bn-1]&128)!=0;
    if (as!=bs) {f->result=as ? -1 : 1;return R1_DONE;}
    uint64_t n=an>bn ? an : bn;
    for (uint32_t units=0;units<32 && f->byte<n;units++,f->byte++) {
        uint64_t i=n-1-f->byte;uint8_t x=rc07_signed_byte(ap,an,i),y=rc07_signed_byte(bp,bn,i);
        if (x!=y) {f->result=x<y ? -1 : 1;return R1_DONE;}
    }
    if (f->byte!=n) return R1_BUILDING;
    f->result=af && av!=trunc(av) ? (av<trunc(av) ? -1 : 1)
              : bf && bv!=trunc(bv) ? (bv<trunc(bv) ? 1 : -1) : 0;return R1_DONE;
}
static void rc07_compare_begin(rctx07_job* j,uint64_t a,uint64_t b)
{j->compare_depth=1;j->compare_result=0;memset(j->compare,0,sizeof(j->compare));j->compare[0].a=a;j->compare[0].b=b;}
static int rc07_compare_one(rctx07_job* j)
{
    if (!j->compare_depth) return R1_DONE;
    rctx07_cmp_frame* f=j->compare+j->compare_depth-1;rctx07_program* p=j->program;
    const rfi_tree_node *a=p->nodes+f->a,*b=p->nodes+f->b;int status=R1_DONE;
    if (!f->phase) {
        if (a->object_token && a->object_token==b->object_token && p->identity_space) f->result=0;
        else if ((a->kind==RFT_INT || a->kind==RFT_FLOAT) && (b->kind==RFT_INT || b->kind==RFT_FLOAT)) {
            status=rc07_numeric_compare(j,f,a,b);if (status!=R1_DONE) return status;
        } else if (a->kind==RFT_TEXT && b->kind==RFT_TEXT) {
            uint64_t n=a->data_count<b->data_count ? a->data_count : b->data_count;
            for (int k=0;k<32 && f->byte<n;k++,f->byte++) {
                uint8_t x=p->data[a->data_first+f->byte],y=p->data[b->data_first+f->byte];
                if (x!=y) {f->result=x<y ? -1 : 1;goto completed;}
            }
            if (f->byte<n) return R1_BUILDING;f->result=a->data_count<b->data_count ? -1 : a->data_count>b->data_count;
        } else if (a->kind==RFT_TUPLE && b->kind==RFT_TUPLE) {f->phase=1;return R1_BUILDING;}
        else {j->semantic=RC07_C_SORT_TYPE;return R1_PENDING;}
    } else if (f->phase==1) {
        if (f->child==a->child_count || f->child==b->child_count) {f->result=a->child_count<b->child_count ? -1 : a->child_count>b->child_count;goto completed;}
        if (j->compare_depth==8) return R1_BUDGET;
        rctx07_cmp_frame* child=j->compare+j->compare_depth++;memset(child,0,sizeof(*child));
        child->a=p->edges[a->child_first+f->child];child->b=p->edges[b->child_first+f->child];f->phase=2;return R1_BUILDING;
    } else return R1_INVALID;
completed:
    j->compare_result=f->result;j->compare_depth--;
    if (!j->compare_depth) return R1_DONE;
    rctx07_cmp_frame* parent=j->compare+j->compare_depth-1;
    if (f->result) {j->compare_depth=0;return R1_DONE;}
    parent->child++;parent->phase=1;return R1_BUILDING;
}
static void rc07_sort_begin(rctx07_job* j)
{memset(&j->sort,0,sizeof(j->sort));j->sort.cursor=1;j->sort.active=1;}
static int rc07_sort_one(rctx07_job* j,uint64_t n,rctx07_item* items)
{
    rctx07_sort* s=&j->sort;if (s->cursor>=n) {s->active=0;return R1_DONE;}
    if (!s->walk && !s->comparing) {s->walk=s->cursor;s->key=s->cursor;}
    if (!s->comparing) {rc07_compare_begin(j,items[s->walk].key,items[s->walk-1].key);s->comparing=1;return R1_BUILDING;}
    int status=rc07_compare_one(j);if (status!=R1_DONE) return status;s->comparing=0;
    if (j->compare_result<0) {rctx07_item tmp=items[s->walk-1];items[s->walk-1]=items[s->walk];items[s->walk]=tmp;s->walk--;if (s->walk) return R1_BUILDING;}
    s->cursor++;s->walk=0;return R1_BUILDING;
}
