/* Sparse INDEX semantics of retained original ce_cold_clip_change_times_v1.
 * 64-row channel words + preceding row indices. C owns all math and storage. */
static int r1_sparse_allocate(r1_job* job)
{
    uint64_t blocks=0;
    for (uint64_t slot=0;slot<job->streams_n;slot++) {
        r1_stream* stream=job->streams+slot;
        stream->sparse_begin=blocks; blocks+=(stream->count+63)/64;
    }
    job->sparse_blocks=blocks;
    job->sparse_word_bytes=blocks*512*sizeof(uint64_t);
    job->sparse_before_bytes=blocks*512*sizeof(int32_t);
    job->narrow_bytes=job->records_n*sizeof(float);
    job->change_words=r1_allocate(job->ledger,job->sparse_word_bytes);
    job->change_before=r1_allocate(job->ledger,job->sparse_before_bytes);
    job->narrow_times=r1_allocate(job->ledger,job->narrow_bytes);
    return job->change_words && job->change_before && job->narrow_times ? R1_OK : R1_BUDGET;
}

static unsigned r1_sparse_narrow(double selected, float* output)
{
    uint64_t bits;
    unsigned warnings=0;
    float narrowed=(float)selected;
    memcpy(&bits,&selected,sizeof(bits)); *output=narrowed;
    if (isfinite(selected) && isinf(narrowed)) warnings|=1;
    if ((bits & UINT64_C(0x7ff0000000000000))==UINT64_C(0x7ff0000000000000)
            && (bits & UINT64_C(0x000fffffffffffff))
            && !(bits & UINT64_C(0x0008000000000000))) warnings|=2;
    return warnings;
}

static void r1_sparse_store(r1_job* job, r1_stream* stream, uint64_t local)
{
    uint64_t row=stream->begin+local, block=stream->sparse_begin+local/64;
    const uint8_t* raw=r1_source_row(job,row);
    const uint8_t* previous=local ? r1_source_row(job,row-1) : NULL;
    uint64_t* words=job->change_words+block*512;
    int32_t* before=job->change_before+block*512;
    int any=0;
    if (local==0) for (int c=0;c<512;c++) job->compile_last[c]=-1;
    if ((local & 63)==0) memcpy(before,job->compile_last,512*sizeof(int32_t));
    for (int c=0;c<512;c++) {
        int changed=stream->base[c] && (local==0 || raw[c]!=previous[c]);
        if (changed) {
            words[c]|=UINT64_C(1)<<(local & 63);
            job->compile_last[c]=(int32_t)local; any=1;
        }
    }
    if (any) job->warning_flags|=r1_sparse_narrow(job->times[row],job->narrow_times+row);
}

static int r1_highest_bit(uint64_t word)
{
    int result=0;
    if (word>>32) {word>>=32;result+=32;}
    if (word>>16) {word>>=16;result+=16;}
    if (word>>8) {word>>=8;result+=8;}
    if (word>>4) {word>>=4;result+=4;}
    if (word>>2) {word>>=2;result+=2;}
    return result+(int)(word>>1);
}

static void r1_sparse_lookup(const r1_job* job, const r1_stream* stream,
                             uint64_t local, float* output)
{
    uint64_t block=stream->sparse_begin+local/64, shift=local & 63;
    uint64_t mask=shift==63 ? UINT64_MAX : (UINT64_C(1)<<(shift+1))-1;
    const uint64_t* words=job->change_words+block*512;
    const int32_t* before=job->change_before+block*512;
    for (int c=0;c<512;c++) {
        uint64_t word=words[c]&mask;
        int64_t index=word ? (int64_t)(local & ~UINT64_C(63))+r1_highest_bit(word) : before[c];
        output[c]=index<0 ? 0.f : job->narrow_times[stream->begin+(uint64_t)index];
    }
}
