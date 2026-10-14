/* Independent original Main grid controls, source-only and UNRUN.
 * Does not replace actual whole-show positions or real installed ABI proof. */
#include <assert.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define CE_API
#include "main_grid.c"
typedef struct {G0Atom start,duration;uint32_t unused_flags,padding;} Row;
static G0Atom number(double value){G0Atom atom={1,0,0};memcpy(&atom.bits,&value,8);return atom;}
static G0Request request(Row* rows,uint64_t count,int32_t ms){
    G0Request value={0};value.abi=G0_ABI;value.size=sizeof(value);
    value.rows=rows;value.count=count;value.row_bytes=count*sizeof(Row);
    value.stride=sizeof(Row);value.start_offset=offsetof(Row,start);
    value.duration_offset=offsetof(Row,duration);value.timer_ms=ms;value.timer_present=1;
    return value;
}
static void unchanged(G0Request* raw,int expected){
    G0Grid value,previous;memset(&value,0xA7,sizeof(value));previous=value;
    assert(ce_main_grid_compile_g0(raw,&value)==expected);
    assert(memcmp(&value,&previous,sizeof(value))==0);
}
int main(void){
    Row rows[2]={0};G0Request raw=request(rows,0,25);G0Grid grid;double position=-1.;
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK);
    assert(grid.count==1&&grid.end==0.&&grid.interval==.025);
    raw.timer_present=0;
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.interval==.023);
    raw.timer_present=1;raw.timer_ms=-10;
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.interval==.015);
    raw.timer_ms=100;
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.interval==.05);
    /* Late duration-zero / negative-duration clips affect original Main end,
     * including clips marked muted/folder/solo-excluded in the raw source. */
    rows[0].start=number(0.);rows[0].duration=number(1.);
    rows[1].start=number(99.);rows[1].duration=number(0.);rows[1].unused_flags=7;
    raw=request(rows,2,25);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK);
    assert(grid.end==99.&&grid.count==3961);
    rows[1].duration=number(-3.);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.end==99.);
    rows[0].duration=number(.03);raw=request(rows,1,25);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.count==3);
    assert(ce_main_grid_position_g0(&grid,0,&position)==G0_OK&&position==0.);
    assert(ce_main_grid_position_g0(&grid,1,&position)==G0_OK&&position==.025);
    assert(ce_main_grid_position_g0(&grid,2,&position)==G0_OK&&position==.03);
    position=-1.;assert(ce_main_grid_position_g0(&grid,3,&position)==G0_BAD_INDEX&&position==-1.);
    rows[0].start=number(NAN);rows[0].duration=number(INFINITY);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.end==0.&&grid.count==1);
    rows[0].start=number(10.);rows[0].duration=number(NAN);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&grid.end==10.&&grid.count==401);
    rows[0].start=number(-0.);rows[0].duration=number(-0.);
    assert(ce_main_grid_compile_g0(&raw,&grid)==G0_OK&&!signbit(grid.end));
    rows[0].start=number(INFINITY);unchanged(&raw,G0_CEIL_INF);
    rows[0].start=number(1e20);unchanged(&raw,G0_BIG_COUNT);
    rows[0].start.kind=9;unchanged(&raw,G0_PENDING);
    rows[0].start.kind=1;rows[0].start.reserved=1;unchanged(&raw,G0_BAD_ABI);
    rows[0].start.reserved=0;raw.row_bytes--;unchanged(&raw,G0_BAD_BUFFER);
    raw=request(rows,1,25);raw.duration_offset=raw.stride;unchanged(&raw,G0_BAD_ABI);
    raw=request(rows,1,25);raw.rows=NULL;unchanged(&raw,G0_BAD_BUFFER);
    return 0;
}
