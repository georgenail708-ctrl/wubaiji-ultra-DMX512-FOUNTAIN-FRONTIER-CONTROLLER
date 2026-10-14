/* UNRUN independent raw controls. ROOT may include in a separate isolated
 * original-types/native-component test TU after actual R1/R4/R5/FI source.
 * No source guard/oracle replacement; this is not installed-owner acceptance. */
#include <assert.h>

static r1_atom rfi_control_float(double value)
{
    r1_atom atom={R1_F64,0,0};memcpy(&atom.bits,&value,8);return atom;
}

static void rfi_control_raw_membership(void)
{
    uint32_t semantic=0;r1_atom atoms[3]={rfi_control_float(1.9),rfi_control_float(2.),rfi_control_float(1e300)};
    rfi_universes_raw list={3,atoms};
    assert(rfi_member(&list,1,&semantic)==1 && !semantic);
    assert(rfi_member(&list,3,&semantic)==0 && !semantic);
    atoms[1]=rfi_control_float(NAN);semantic=0;
    /* A first matching value cannot short-circuit a later int(NaN) error. */
    assert(rfi_member(&list,1,&semantic)==R1_PENDING && semantic==RFI_UNIVERSE_INT_NAN);
    atoms[1]=rfi_control_float(INFINITY);semantic=0;
    assert(rfi_member(&list,1,&semantic)==R1_PENDING && semantic==RFI_UNIVERSE_INT_INF);
    atoms[1]=(r1_atom){R1_NONE,0,0};semantic=0;
    assert(rfi_member(&list,1,&semantic)==R1_PENDING && semantic==RFI_UNIVERSE_INT_NONE);
    atoms[1]=(r1_atom){R1_BOOL,0,2};semantic=0;
    assert(rfi_member(&list,1,&semantic)==R1_PENDING && !semantic);
    rfi_clip_raw clips[1]={{0}};rfi_track_raw tracks[1]={{0}};rfi_input raw={0};rfi_plan plan={0};
    raw.clips=clips;raw.tracks=tracks;plan.borrowed=&raw;
    tracks[0].universes=list;atoms[1]=rfi_control_float(NAN);
    assert(rfi_universe_allowed(&plan,0,7)==1); /* Original caught track conversion. */
    clips[0].universes=list;plan.semantic=0;
    assert(rfi_universe_allowed(&plan,0,7)==R1_PENDING && plan.semantic==RFI_UNIVERSE_INT_NAN);
    clips[0].universes.count=tracks[0].universes.count=0;
    tracks[0].universe=(r1_atom){R1_MISSING,0,0};
    assert(rfi_universe_allowed(&plan,0,1)==1 && rfi_universe_allowed(&plan,0,2)==0);
    clips[0].filters[0]=(r1_atom){R1_MISSING,0,0};
    clips[0].filters[1]=(r1_atom){R1_BOOL,0,0};clips[0].filters[2]=rfi_control_float(1.);
    assert(rfi_filter_primitives(clips));clips[0].filters[2]=(r1_atom){R1_NONE,0,0};
    assert(!rfi_filter_primitives(clips));
    assert(rfi_max_first(0.,rfi_min_first(1.,NAN))==1.);
    assert(rfi_max_first(0.,NAN)==0.);
}

static void rfi_control_endpoint_and_512(void)
{
    r3_job scene={0};r3_clip clips[1]={{0}};r1_job source={0};r1_stream stream={0};
    r4_job r4={0};r5_job r5={0};r5_clip phases[1]={{0}};rfi_plan plan={0};
    double times[3]={0.,1.5,2.};float narrow[3]={0.f,1.5f,2.f};
    uint64_t words[512];int32_t before[512];uint8_t active[512];
    double scores[512],best[512];float values[512],anchor[512];
    scene.clips=clips;clips[0].source=&source;r4.scene=&scene;r5.base=&r4;r5.clips=phases;plan.owner.timeline=&r5;
    source.streams=&stream;source.streams_n=1;source.times=times;source.narrow_times=narrow;
    source.change_words=words;source.change_before=before;source.start=1.;source.duration=2.;
    source.source_duration=2.;source.rate=2.;stream.count=3;
    rfi_reference reference={0,0,0,0};
    for (uint32_t c=0;c<512;c++) {
        words[c]=UINT64_C(1)|(c%3==1 ? UINT64_C(2) : c%3==2 ? UINT64_C(4) : 0);
        before[c]=-1;active[c]=(uint8_t)(c%5!=0);values[c]=(float)(17+c%211);
        anchor[c]=128.f;best[c]=-INFINITY;
    }
    /* Exact midpoint chooses the later frame, independently prescribed. */
    assert(rfi_endpoint_index(&source,&stream,.75)==1);
    assert(rfi_endpoint_index(&source,&stream,1.75)==2);
    rfi_endpoint_scores(&plan,&reference,active,scores);
    for (uint32_t c=0;c<512;c++) {
        double expected=c%3==0 ? 1. : c%3==1 ? 1.75 : 2.;
        assert(active[c] ? scores[c]==expected : scores[c]==-INFINITY);
    }
    assert(ce_prepared_anchor_update(values,active,scores,1000000.001,anchor,best)==409);
    for (uint32_t c=0;c<512;c++) assert(anchor[c]==(active[c] ? values[c] : 128.f));
    /* Last traversal wins equal finite score; inactive explicit zeros stay idle. */
    for (uint32_t c=0;c<512;c++) values[c]=222.f;
    assert(ce_prepared_anchor_update(values,active,scores,1000000.001,anchor,best)==409);
    for (uint32_t c=0;c<512;c++) assert(anchor[c]==(active[c] ? 222.f : 128.f));
    source.rate=0.;phases[0].hold=R5_HOLD_FRAME;source.duration=.25;
    rfi_endpoint_scores(&plan,&reference,active,scores);
    for (uint32_t c=0;c<512;c++) assert(active[c]
        ? scores[c]==(c%3==0 ? 1. : 1.25) : scores[c]==-INFINITY);
    reference.slot=UINT32_MAX;rfi_endpoint_scores(&plan,&reference,active,scores);
    for (uint32_t c=0;c<512;c++) assert(active[c] ? scores[c]==1. : scores[c]==-INFINITY);
    reference.slot=0;source.rate=2.;phases[0].hold=R5_HOLD_NONE;stream.count=1;
    times[0]=16777217.;narrow[0]=16777216.f;source.source_duration=16777217.;
    for (uint32_t c=0;c<512;c++) words[c]=1;
    rfi_endpoint_scores(&plan,&reference,active,scores);
    for (uint32_t c=0;c<512;c++) assert(active[c] ? scores[c]==8388609. : scores[c]==-INFINITY);
}

static void rfi_control_empty_cached_path(void)
{
    rfi_plan plan={0};rfi_entry entry={0};const float* result=NULL;
    plan.phase=RFI_READY;plan.entries_n=1;plan.entries=&entry;entry.ready=1;
    for (uint32_t c=0;c<512;c++) entry.anchor[c]=(float)(c%256);
    assert(rfi_anchor_step(&plan,0,1,&result)==R1_OK && result==entry.anchor);
    for (uint32_t c=0;c<512;c++) assert(result[c]==(float)(c%256));
    /* Cached branch must return before dereferencing any sample owner/state. */
    plan.cancelled=1;result=NULL;
    assert(rfi_anchor_step(&plan,0,1,&result)==R1_CANCELLED && result==NULL);
}

static void rfi_numeric_controls_draft(void)
{
    rfi_control_raw_membership();rfi_control_endpoint_and_512();rfi_control_empty_cached_path();
}
