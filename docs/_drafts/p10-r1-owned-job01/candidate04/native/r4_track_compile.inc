static int r4_tracks_create(r4_tracks* out,r1_ledger* ledger,
                            const r3_track_raw* raw,uint64_t n)
{
    uint64_t per=sizeof(r3_track_raw)+2*sizeof(int64_t)+4+sizeof(uint64_t)+sizeof(r4_dfs_level);
    uint8_t* cursor;
    if (!out || !ledger || !raw || !n || n>INT_MAX || n>SIZE_MAX/per) return R1_INVALID;
    if (n*per>SIZE_MAX-sizeof(r4_dfs_level)) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->count=n;out->bytes=n*per+sizeof(r4_dfs_level);out->ledger=ledger;
    out->borrowed=raw;
    cursor=r1_allocate(ledger,out->bytes);if (!cursor) return R1_BUDGET;
    out->tracks=(r3_track_raw*)cursor;cursor+=n*sizeof(r3_track_raw);
    out->parents=(int64_t*)cursor;cursor+=n*sizeof(int64_t);
    out->rank=(int64_t*)cursor;cursor+=n*sizeof(int64_t);
    out->visits=(uint64_t*)cursor;cursor+=n*sizeof(uint64_t);
    out->stack=(r4_dfs_level*)cursor;cursor+=(n+1)*sizeof(r4_dfs_level);
    out->emitted=cursor;cursor+=n;out->muted=cursor;cursor+=n;
    out->solo=cursor;cursor+=n;out->allowed=cursor;
    out->capacity=n;out->order_bytes=n*sizeof(int64_t);out->order=r1_allocate(ledger,out->order_bytes);
    if (!out->order) {r1_free(ledger,out->tracks,out->bytes);out->tracks=NULL;return R1_BUDGET;}
    out->last_match=-1;return R1_OK;
}

static void r4_tracks_destroy(r4_tracks* rows)
{
    if (!rows || !rows->tracks) return;
    r1_free(rows->ledger,rows->order,rows->order_bytes);
    r1_free(rows->ledger,rows->new_order,rows->new_capacity*sizeof(int64_t));
    r1_free(rows->ledger,rows->tracks,rows->bytes);memset(rows,0,sizeof(*rows));
}

static int r4_track_id_one(r4_tracks* rows)
{
    /* A raw reserved bit is an unsupported input ABI, never a foreign call. */
    uint64_t index=rows->cursor++;
    rows->tracks[index]=rows->borrowed[index];rows->parents[index]=-1;
    if (rows->tracks[index].reserved) return R1_PENDING;
    if (rows->cursor==rows->count) {rows->borrowed=NULL;rows->cursor=0;rows->phase=R4_TRACK_PARENT;}
    return R1_OK;
}

static void r4_track_parent_one(r4_tracks* rows)
{
    uint64_t parent=rows->tracks[rows->cursor].parent;
    if (parent && rows->tracks[rows->scan].token==parent) rows->last_match=(int64_t)rows->scan;
    if (++rows->scan==rows->count) {
        rows->parents[rows->cursor++]=rows->last_match;rows->scan=0;rows->last_match=-1;
        if (rows->cursor==rows->count) {rows->phase=R4_TRACK_DFS;rows->cursor=0;rows->stack[0].parent=0;rows->depth=1;}
    }
}

static int r4_track_dfs_one(r4_tracks* rows)
{
    r4_dfs_level* current=rows->stack+rows->depth-1;
    if (current->next==rows->count) {
        if (--rows->depth==0) {rows->phase=R4_TRACK_ORPHANS;rows->cursor=0;}
        return R1_OK;
    }
    uint64_t index=current->next;r3_track_raw* track=rows->tracks+index;
    if (track->parent==current->parent && rows->out==rows->capacity) return r4_track_grow_begin(rows);
    current->next++;
    if (track->parent!=current->parent) return R1_OK;
    rows->order[rows->out++]=(int64_t)index;rows->emitted[index]=1;
    if (track->folder) {
        if (rows->depth>rows->count) {rows->semantic=R4_RECURSIVE_FOLDER;return R1_PENDING;}
        rows->stack[rows->depth].parent=track->token;rows->stack[rows->depth].next=0;rows->depth++;
    }
    return R1_OK;
}

static int r4_track_orphan_one(r4_tracks* rows)
{
    uint64_t index=rows->cursor;
    if (!rows->emitted[index] && rows->out==rows->capacity) return r4_track_grow_begin(rows);
    rows->cursor++;
    if (!rows->emitted[index]) rows->order[rows->out++]=(int64_t)index;
    if (rows->cursor==rows->count) {
        rows->phase=R4_TRACK_ANCESTORS;rows->cursor=0;rows->walk=0;rows->tag=1;
    }
    return R1_OK;
}

static void r4_track_ancestor_one(r4_tracks* rows)
{
    uint64_t current=rows->walk;
    rows->muted[rows->cursor]|=rows->tracks[current].muted!=0;
    rows->solo[rows->cursor]|=rows->tracks[current].solo!=0;
    rows->visits[current]=rows->tag;
    int64_t parent=rows->parents[current];
    if (parent<0 || rows->visits[parent]==rows->tag) {
        rows->cursor++;rows->tag++;
        if (rows->cursor==rows->count) {rows->phase=R4_TRACK_SOLO;rows->cursor=0;}
        else rows->walk=rows->cursor;
    } else rows->walk=(uint64_t)parent;
}

static void r4_track_solo_one(r4_tracks* rows)
{
    uint64_t index=(uint64_t)rows->order[rows->cursor];
    rows->rank[index]=(int64_t)rows->cursor;
    if (!rows->tracks[index].folder && !rows->muted[index] && rows->solo[index]) rows->any_solo=1;
    if (++rows->cursor==rows->out) {rows->phase=R4_TRACK_ALLOW;rows->cursor=0;}
}

static void r4_track_allow_one(r4_tracks* rows)
{
    uint64_t index=rows->cursor++;
    rows->allowed[index]=(uint8_t)(!rows->tracks[index].folder && !rows->muted[index] && (!rows->any_solo || rows->solo[index]));
    if (rows->cursor==rows->count) rows->phase=R4_TRACK_READY;
}

static int r4_tracks_step(r4_tracks* rows,uint32_t units)
{
    int status=R1_OK;
    if (!rows || !rows->tracks || !units || units>65536) return R1_INVALID;
    if (rows->cancelled) return R1_CANCELLED;
    if (rows->semantic) return R1_PENDING;
    while (units-- && rows->phase!=R4_TRACK_READY && !status) {
        switch(rows->phase) {
        case R4_TRACK_IDS:status=r4_track_id_one(rows);break;
        case R4_TRACK_PARENT:r4_track_parent_one(rows);break;
        case R4_TRACK_DFS:status=r4_track_dfs_one(rows);break;
        case R4_TRACK_ORPHANS:status=r4_track_orphan_one(rows);break;
        case R4_TRACK_ANCESTORS:r4_track_ancestor_one(rows);break;
        case R4_TRACK_SOLO:r4_track_solo_one(rows);break;
        case R4_TRACK_ALLOW:r4_track_allow_one(rows);break;
        case R4_TRACK_GROW:r4_track_grow_one(rows);break;
        default:return R1_INVALID;
        }
    }
    return status ? status : rows->phase==R4_TRACK_READY ? R1_OK : R1_BUILDING;
}
