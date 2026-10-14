/* Job-owned native identity index, built from raw authored tokens. One probe
 * per compile unit; removes repeated whole-fixture scans from the compiled
 * geometry/order stages. No host-built membership/index arrays are accepted. */
typedef struct {uint64_t key,value,aux;uint32_t present,reserved;} r6_token_entry;
typedef struct {r1_ledger* ledger;r6_token_entry* entries;uint64_t capacity,bytes;} r6_token_index;
typedef struct {uint64_t key,cursor,probes;uint32_t started,done,found;} r6_token_probe;

static uint64_t r6_token_hash(uint64_t key)
{
    key^=key>>30;key*=UINT64_C(0xbf58476d1ce4e5b9);key^=key>>27;
    key*=UINT64_C(0x94d049bb133111eb);return key^(key>>31);
}

static int r6_token_create(r6_token_index* out,r1_ledger* ledger,uint64_t maximum)
{
    uint64_t capacity=1,bytes=0;if (!out || maximum>UINT64_MAX/2) return R1_BUDGET;
    while (capacity<maximum*2) {if (capacity>UINT64_MAX/2) return R1_BUDGET;capacity*=2;}
    if (!r3_add_bytes(&bytes,capacity,sizeof(r6_token_entry))) return R1_BUDGET;
    memset(out,0,sizeof(*out));out->ledger=ledger;out->capacity=capacity;out->bytes=bytes;
    out->entries=r1_allocate(ledger,bytes);return out->entries ? R1_OK : R1_BUDGET;
}

static int r6_token_probe_one(const r6_token_index* index,r6_token_probe* probe)
{
    if (probe->done) return R1_OK;
    if (!probe->started) {probe->cursor=r6_token_hash(probe->key)&(index->capacity-1);probe->started=1;}
    if (++probe->probes>index->capacity) return R1_INVALID;
    const r6_token_entry* entry=index->entries+probe->cursor;
    if (!entry->present || entry->key==probe->key) {
        probe->done=1;probe->found=entry->present!=0;return R1_OK;
    }
    probe->cursor=(probe->cursor+1)&(index->capacity-1);return R1_BUILDING;
}

static void r6_token_destroy(r6_token_index* out)
{
    if (out->ledger) r1_free(out->ledger,out->entries,out->bytes);memset(out,0,sizeof(*out));
}
