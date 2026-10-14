static int r1_sort_before(r1_sort_row left, r1_sort_row right)
{
    if (left.slot!=right.slot) return left.slot<right.slot;
    if (left.time!=right.time) return left.time<right.time;
    return left.source<=right.source;
}

static void r1_merge_runs(r1_sort_row* rows, r1_sort_row* scratch,
                          uint64_t begin, uint64_t middle, uint64_t end)
{
    uint64_t left=begin, right=middle, out=begin;
    while (left<middle && right<end) {
        scratch[out++]=r1_sort_before(rows[left],rows[right]) ? rows[left++] : rows[right++];
    }
    while (left<middle) scratch[out++]=rows[left++];
    while (right<end) scratch[out++]=rows[right++];
    memcpy(rows+begin,scratch+begin,(size_t)(end-begin)*sizeof(*rows));
}

static void r1_sort(r1_sort_row* rows, r1_sort_row* scratch, uint64_t count)
{
    for (uint64_t width=1;width<count;width*=2) {
        for (uint64_t begin=0;begin<count;begin+=2*width) {
            uint64_t middle=begin+width<count ? begin+width : count;
            uint64_t end=begin+2*width<count ? begin+2*width : count;
            r1_merge_runs(rows,scratch,begin,middle,end);
        }
    }
}

static uint64_t r1_map_streams(const r1_input* input, uint32_t* map)
{
    uint64_t count=0;
    for (uint64_t i=0;i<input->frame_count;i++) {
        uint32_t u=input->frames[i].universe;
        if (!map[u]) map[u]=(uint32_t)++count;
    }
    return count;
}

static void r1_explicit_channels(const r1_input* input, r1_job* job, const uint32_t* map)
{
    for (uint64_t i=0;i<input->channel_count;i++) {
        const r1_channel* channel=input->channels+i;
        if (!channel->universe || channel->universe>=R1_UNIVERSE_LIMIT) continue;
        if (!map[channel->universe] || channel->channel<1 || channel->channel>512) continue;
        r1_stream* stream=job->streams+map[channel->universe]-1;
        stream->base[channel->channel-1]=1; stream->explicit_count++;
    }
}

static void r1_infer_frame(r1_stream* stream, const uint8_t* raw)
{
    if (!stream->explicit_count) {
        for (int c=0;c<512;c++) {
            stream->base[c]|=(uint8_t)(stream->seen ? raw[c]!=stream->previous[c] : raw[c]>0);
        }
    }
    memcpy(stream->previous,raw,512); stream->seen=1;
}

static const uint8_t* r1_source_row(const r1_job* job,uint64_t sorted)
{
    return job->raw_owned[job->data_order[sorted]].data;
}

static void r1_raw_rows(const r1_input* input, r1_job* job,
                        const uint32_t* map, r1_sort_row* rows)
{
    r1_explicit_channels(input,job,map);
    for (uint64_t i=0;i<input->frame_count;i++) {
        const r1_raw_frame* frame=input->frames+i;
        uint64_t slot=map[frame->universe]-1;
        r1_stream* stream=job->streams+slot;
        stream->universe=frame->universe; stream->count++;
        r1_infer_frame(stream,frame->data);
        rows[i].source=i; rows[i].slot=slot;
        r1_atom_number(frame->timestamp,0.,&rows[i].time);
        if (rows[i].time==0.) rows[i].time=0.; /* original float(timestamp or 0.) */
        if (rows[i].time>=0.) {
            if (rows[i].time<job->cycle_first) job->cycle_first=rows[i].time;
            if (rows[i].time>job->cycle_last) job->cycle_last=rows[i].time;
        }
    }
}
