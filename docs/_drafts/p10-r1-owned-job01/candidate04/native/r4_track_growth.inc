/* Original duplicated folder ids may emit the same child several times.
 * Dynamic order growth is charged twice only during a bounded scalar copy;
 * it never duplicates any source payload. */
static int r4_track_grow_begin(r4_tracks* rows)
{
    uint64_t capacity=rows->capacity*2;
    if (capacity<rows->capacity || capacity>SIZE_MAX/sizeof(int64_t)) return R1_BUDGET;
    rows->new_order=r1_allocate(rows->ledger,capacity*sizeof(int64_t));
    if (!rows->new_order) return R1_BUDGET;
    rows->new_capacity=capacity;rows->copy_cursor=0;rows->resume_phase=rows->phase;
    rows->phase=R4_TRACK_GROW;return R1_OK;
}

static void r4_track_grow_one(r4_tracks* rows)
{
    rows->new_order[rows->copy_cursor]=rows->order[rows->copy_cursor];
    if (++rows->copy_cursor==rows->out) {
        r1_free(rows->ledger,rows->order,rows->order_bytes);rows->order=rows->new_order;rows->new_order=NULL;
        rows->capacity=rows->new_capacity;rows->order_bytes=rows->capacity*sizeof(int64_t);rows->new_capacity=0;
        rows->phase=rows->resume_phase;
    }
}
