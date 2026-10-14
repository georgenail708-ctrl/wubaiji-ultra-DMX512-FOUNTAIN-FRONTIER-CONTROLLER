/* Iterative metadata equality. Workspace is caller's precharged ledger memory;
 * no recursion, per-comparison heap, Python callbacks or host numeric result. */
static const rfi_tree_node* rfi_node(const rfi_tree_view* view,uint64_t index)
{
    if (!view || !view->nodes || index>=view->nodes_n) return NULL;
    const rfi_tree_node* node=view->nodes+index;
    if (node->reserved || node->kind>RFT_DICT || node->child_first>view->edges_n
        || node->child_count>view->edges_n-node->child_first
        || node->data_first>view->data_n || node->data_count>view->data_n-node->data_first
        || (node->child_count && !view->edges) || (node->data_count && !view->data)
        || (node->kind==RFT_INT && !node->data_count) || (node->kind==RFT_BOOL && node->bits>1)) return NULL;
    return node;
}

static uint64_t rfi_child(const rfi_tree_view* view,const rfi_tree_node* node,uint64_t i)
{return i<node->child_count ? view->edges[node->child_first+i] : UINT64_MAX;}

static int rfi_tree_truth(const rfi_tree_view* view,const rfi_tree_node* node)
{
    if (node->kind==RFT_NONE) return 0;
    if (node->kind==RFT_BOOL) return node->bits!=0;
    if (node->kind==RFT_FLOAT) {double value;memcpy(&value,&node->bits,8);return value!=0.;}
    if (node->kind==RFT_INT) {
        for (uint64_t i=0;i<node->data_count;i++) if (view->data[node->data_first+i]) return 1;
        return 0;
    }
    return node->kind==RFT_TEXT || node->kind==RFT_BYTES ? node->data_count!=0 : node->child_count!=0;
}

typedef struct {const uint8_t* raw;uint64_t n,first;int negative;uint32_t boolean;} rfi_integer;
static rfi_integer rfi_integer_read(const rfi_tree_view* view,const rfi_tree_node* node)
{
    rfi_integer value={0};
    if (node->kind==RFT_BOOL) {value.n=1;value.boolean=(uint32_t)node->bits;return value;}
    value.raw=view->data+node->data_first;value.n=node->data_count;
    value.negative=(value.raw[value.n-1]&128)!=0;value.first=value.n;
    if (value.negative) for (uint64_t i=0;i<value.n;i++) if (value.raw[i]) {value.first=i;break;}
    return value;
}

static uint8_t rfi_magnitude(rfi_integer value,uint64_t byte)
{
    if (!value.raw) return byte ? 0 : (uint8_t)value.boolean;
    if (byte>=value.n) return 0;
    uint8_t raw=value.raw[byte];if (!value.negative) return raw;
    return (uint8_t)((uint32_t)(uint8_t)~raw+(byte<=value.first));
}

static int rfi_integer_equal(rfi_integer a,rfi_integer b)
{
    if (a.negative!=b.negative) return 0;
    uint64_t n=a.n>b.n ? a.n : b.n;
    for (uint64_t i=0;i<n;i++) if (rfi_magnitude(a,i)!=rfi_magnitude(b,i)) return 0;
    return 1;
}

static int rfi_integer_float(rfi_integer integer,uint64_t bits)
{
    uint64_t mantissa=bits&UINT64_C(0x000fffffffffffff),exponent=(bits>>52)&2047;
    int negative=(int)(bits>>63),shift;
    if (exponent==2047) return 0;
    if (exponent) {mantissa|=UINT64_C(1)<<52;shift=(int)exponent-1023-52;}
    else shift=-1074;
    if (!mantissa) negative=0;
    if (shift<0) {
        uint32_t right=(uint32_t)-shift;
        if (right>=64) {if (mantissa) return 0;}
        else {uint64_t mask=(UINT64_C(1)<<right)-1;if (mantissa&mask) return 0;mantissa>>=right;}
        shift=0;
    }
    if (integer.negative!=negative) return 0;
    /* A finite f64 integral value has <=1024 magnitude bits; arbitrary larger
     * original ints are compared exactly, without float conversion/overflow. */
    uint64_t n=integer.n>128 ? integer.n : 128;
    for (uint64_t i=0;i<n;i++) {
        uint64_t bit=i*8;uint8_t expected=0;
        if (bit>=(uint64_t)shift) {uint64_t offset=bit-(uint64_t)shift;if (offset<64) expected=(uint8_t)(mantissa>>offset);}
        else {uint64_t offset=(uint64_t)shift-bit;if (offset<8) expected=(uint8_t)(mantissa<<offset);}
        if (rfi_magnitude(integer,i)!=expected) return 0;
    }
    return 1;
}

static int rfi_numeric_equal(const rfi_tree_view* a,const rfi_tree_node* x,
                              const rfi_tree_view* b,const rfi_tree_node* y)
{
    if (x->kind==RFT_FLOAT && y->kind==RFT_FLOAT) {
        double av,bv;memcpy(&av,&x->bits,8);memcpy(&bv,&y->bits,8);return av==bv;
    }
    if (x->kind==RFT_FLOAT) return rfi_integer_float(rfi_integer_read(b,y),x->bits);
    if (y->kind==RFT_FLOAT) return rfi_integer_float(rfi_integer_read(a,x),y->bits);
    return rfi_integer_equal(rfi_integer_read(a,x),rfi_integer_read(b,y));
}

static int rfi_tree_equal(const rfi_tree_view* a,const rfi_tree_view* b,rfi_tree_work* work,int* equal)
{
    if (!a || !b || !work || !equal || !work->frames || !work->capacity) return R1_INVALID;
    uint64_t depth=1;int result=0;work->frames[0]=(rfi_eq_frame){a->root,b->root,0,0,0,0,0,0};
    while (depth) {
        rfi_eq_frame* frame=work->frames+depth-1;
        const rfi_tree_node *x=rfi_node(a,frame->a),*y=rfi_node(b,frame->b);
        if (!x || !y) return R1_INVALID;
        if (!frame->phase) {
            if (x->object_token && x->object_token==y->object_token && a->identity_space
                && a->identity_space==b->identity_space) {result=1;goto completed;}
            int xn=x->kind==RFT_BOOL||x->kind==RFT_INT||x->kind==RFT_FLOAT;
            int yn=y->kind==RFT_BOOL||y->kind==RFT_INT||y->kind==RFT_FLOAT;
            if (xn && yn) {result=rfi_numeric_equal(a,x,b,y);goto completed;}
            int xs=x->kind==RFT_SET||x->kind==RFT_FROZENSET;
            int ys=y->kind==RFT_SET||y->kind==RFT_FROZENSET;
            if (x->kind!=y->kind && !(xs&&ys)) {result=0;goto completed;}
            if (x->kind==RFT_NONE) {result=1;goto completed;}
            if (x->kind==RFT_TEXT || x->kind==RFT_BYTES) {
                result=x->data_count==y->data_count && (!x->data_count
                    || !memcmp(a->data+x->data_first,b->data+y->data_first,(size_t)x->data_count));goto completed;
            }
            if (x->child_count!=y->child_count) {result=0;goto completed;}
            if (!x->child_count) {result=1;goto completed;}
            frame->unordered=xs || x->kind==RFT_DICT;frame->phase=1;
        } else if (frame->phase==2) {
            if (!result && !frame->unordered) goto completed;
            if (frame->unordered && !result) {
                if (++frame->search==y->child_count) {result=0;goto completed;}
            } else {frame->cursor++;frame->search=0;}
            if (frame->cursor==x->child_count) {result=1;goto completed;}
            frame->phase=1;
        }
        if (frame->phase==1) {
            uint64_t left=rfi_child(a,x,frame->cursor);
            uint64_t right=rfi_child(b,y,frame->unordered ? frame->search : frame->cursor);
            if (depth==work->capacity) return R1_BUDGET;
            for (uint64_t i=0;i<depth;i++) if (work->frames[i].a==left && work->frames[i].b==right) return R1_PENDING;
            frame->phase=2;work->frames[depth++]=(rfi_eq_frame){left,right,0,0,0,0,0,0};continue;
        }
completed:
        depth--;
    }
    *equal=result;return R1_OK;
}

static int rfi_empty_geometry_difference(const rfi_tree_view* stored,const rfi_tree_view* current,
                                        rfi_tree_work* work,int* equal)
{
    const rfi_tree_node *a=rfi_node(stored,stored->root),*b=rfi_node(current,current->root);
    *equal=0;if (!a || !b) return R1_INVALID;
    if (a->kind!=RFT_TUPLE || b->kind!=RFT_TUPLE || a->child_count!=4 || b->child_count!=4) return R1_OK;
    rfi_tree_view as=*stored,bs=*current;as.root=rfi_child(stored,a,1);bs.root=rfi_child(current,b,1);
    const rfi_tree_node *sa=rfi_node(&as,as.root),*sb=rfi_node(&bs,bs.root);
    if (!sa || !sb || sa->kind!=RFT_TUPLE || sb->kind!=RFT_TUPLE
        || sa->child_count<4 || sa->child_count!=sb->child_count) return R1_OK;
    const rfi_tree_node *positions=rfi_node(&as,rfi_child(&as,sa,3)),*empty=rfi_node(&bs,rfi_child(&bs,sb,3));
    if (!positions || !empty) return R1_INVALID;
    if (!rfi_tree_truth(&as,positions) || empty->kind!=RFT_TUPLE || empty->child_count) return R1_OK;
    for (uint64_t i=0;i<4;i++) {
        if (i==1) continue;rfi_tree_view x=*stored,y=*current;
        x.root=rfi_child(stored,a,i);y.root=rfi_child(current,b,i);int same=0;
        int status=rfi_tree_equal(&x,&y,work,&same);if (status || !same) return status;
    }
    for (uint64_t i=0;i<sa->child_count;i++) {
        if (i==3) continue;rfi_tree_view x=as,y=bs;
        x.root=rfi_child(&as,sa,i);y.root=rfi_child(&bs,sb,i);int same=0;
        int status=rfi_tree_equal(&x,&y,work,&same);if (status || !same) return status;
    }
    *equal=1;return R1_OK;
}

static int rfi_legacy_matches(const rfi_legacy_row* row,const rfi_current_context* current,
                              rfi_tree_work* work,int* matched)
{
    int same=0;*matched=0;int status=rfi_tree_equal(&row->context,&current->context,work,&same);
    if (status) return status;
    if (!same && row->have_proof && current->have_permit && current->spatial_qualified) {
        const rfi_tree_node *schema=rfi_node(&row->proof_schema,row->proof_schema.root),
            *slot=rfi_node(&row->proof_slot,row->proof_slot.root);
        if (!schema || !slot) return R1_INVALID;
        uint8_t one=1;rfi_tree_node number={RFT_INT,0,0,0,0,0,0,1};
        rfi_tree_view integer={0,0,1,0,1,&number,NULL,&one};
        status=rfi_tree_equal(&row->proof_schema,&integer,work,&same);if (status) return status;
        if (!same || slot->kind!=RFT_TUPLE || slot->child_count) return R1_OK;
        status=rfi_tree_equal(&row->proof_values,&current->permit,work,&same);if (status || !same) return status;
        status=rfi_empty_geometry_difference(&row->context,&current->context,work,&same);if (status) return status;
    }
    if (!same) return R1_OK;
    return rfi_tree_equal(&row->motor_context,&current->motor_context,work,matched);
}
