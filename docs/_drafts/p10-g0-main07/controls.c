/* Independent authored-grid/state controls. Source only, never executed. */
#define CE_API
#include "main_grid_step.c"
#include <assert.h>

typedef struct {G0Atom start,duration;} TestRow;
static G0Atom f64(double number) {G0Atom result={1,0,0};memcpy(&result.bits,&number,8);return result;}
static G0Request request(TestRow* rows,uint64_t n,int32_t ms) {
    G0Request value={0};value.abi=G0_ABI;value.size=sizeof(value);value.rows=rows;
    value.count=n;value.row_bytes=n*sizeof(*rows);value.stride=sizeof(*rows);
    value.duration_offset=sizeof(G0Atom);value.timer_ms=ms;value.timer_present=1;return value;
}
static void sentinel(G0Grid* grid) {memset(grid,0x5a,sizeof(*grid));}
static void unchanged(const G0Grid* grid,const G0Grid* before) {assert(!memcmp(grid,before,sizeof(*grid)));}

static void one_unit_and_final(void) {
    TestRow rows[]={{f64(10.),f64(-3.)},{f64(99.),f64(0.)}};
    G0Request raw=request(rows,2,25);G0CompileState state,before;G0Grid grid,old;
    sentinel(&grid);old=grid;
    assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    assert(state.next==0&&state.completed_units==0&&state.end==0.);before=state;
    assert(ce_main_grid_step_g0_v7(&state,0,&grid)==G0_BUILDING);
    assert(!memcmp(&state,&before,sizeof(state)));unchanged(&grid,&old);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_BUILDING);
    assert(state.next==1&&state.completed_units==1&&state.end==10.);unchanged(&grid,&old);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_BUILDING);
    assert(state.next==2&&state.completed_units==2&&state.end==99.);unchanged(&grid,&old);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_OK);
    assert(state.next==2&&state.completed_units==3&&grid.count==3961&&grid.end==99.);
    assert(grid.interval==.025);before=state;sentinel(&grid);old=grid;
    assert(ce_main_grid_step_g0_v7(&state,64,&grid)==G0_OK);
    assert(!memcmp(&state,&before,sizeof(state)));unchanged(&grid,&old);
}
static void empty_final_and_cap(void) {
    G0Request raw=request(NULL,0,1);G0CompileState state,before;G0Grid grid,old;
    assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);before=state;
    sentinel(&grid);old=grid;
    assert(ce_main_grid_step_g0_v7(&state,65,&grid)==G0_BAD_UNITS);
    assert(!memcmp(&state,&before,sizeof(state)));unchanged(&grid,&old);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_OK);
    assert(state.completed_units==1&&grid.count==1&&grid.end==0.&&grid.interval==.015);
}
static void known_original_bits(void) {
    TestRow rows[]={{f64(NAN),f64(INFINITY)},{f64(-0.),f64(-0.)},{f64(.03),f64(0.)}};
    G0Request raw=request(rows,3,25);G0CompileState state;G0Grid grid;double pos=-1.;
    assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    assert(ce_main_grid_step_g0_v7(&state,4,&grid)==G0_OK);
    assert(grid.count==3&&grid.end==.03&&grid.interval==.025);
    assert(ce_main_grid_position_g0(&grid,0,&pos)==G0_OK&&pos==0.&&!signbit(pos));
    assert(ce_main_grid_position_g0(&grid,1,&pos)==G0_OK&&pos==.025);
    assert(ce_main_grid_position_g0(&grid,2,&pos)==G0_OK&&pos==.03);
}
static void terminal_errors_preserve_grid(void) {
    TestRow row={f64(INFINITY),f64(0.)};G0Request raw=request(&row,1,25);
    G0CompileState state,before;G0Grid grid,old;sentinel(&grid);old=grid;
    assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_BUILDING);unchanged(&grid,&old);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_CEIL_INF);unchanged(&grid,&old);before=state;
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_CEIL_INF);
    assert(!memcmp(&state,&before,sizeof(state)));unchanged(&grid,&old);
    row.start=f64(1e20);assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    assert(ce_main_grid_step_g0_v7(&state,2,&grid)==G0_BIG_COUNT);unchanged(&grid,&old);
    row.start=(G0Atom){9,0,0};assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_PENDING);
    assert(state.next==0&&state.completed_units==1);unchanged(&grid,&old);
}
static void prevalidation_no_writes(void) {
    TestRow row={f64(1.),f64(1.)};G0Request raw=request(&row,1,25);
    G0CompileState state,before;G0Grid grid,old;memset(&state,0x5a,sizeof(state));before=state;
    raw.row_bytes--;assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BAD_BUFFER);
    assert(!memcmp(&state,&before,sizeof(state)));raw.row_bytes++;
    assert(ce_main_grid_begin_g0_v7(&raw,&state)==G0_BUILDING);
    state.reserved=1;before=state;sentinel(&grid);old=grid;
    assert(ce_main_grid_step_g0_v7(&state,1,&grid)==G0_BAD_ABI);
    assert(!memcmp(&state,&before,sizeof(state)));unchanged(&grid,&old);
}
int main(void) {
    assert(sizeof(G0Atom)==16&&offsetof(G0Atom,bits)==8);
    assert(sizeof(G0Request)==56&&offsetof(G0Request,rows)==8&&offsetof(G0Request,count)==16);
    assert(offsetof(G0Request,row_bytes)==24&&offsetof(G0Request,stride)==32);
    assert(offsetof(G0Request,start_offset)==36&&offsetof(G0Request,duration_offset)==40);
    assert(offsetof(G0Request,timer_ms)==44&&offsetof(G0Request,timer_present)==48);
    assert(sizeof(G0Grid)==40&&offsetof(G0Grid,count)==16&&offsetof(G0Grid,end)==24&&offsetof(G0Grid,interval)==32);
    assert(sizeof(G0CompileState)==96&&offsetof(G0CompileState,next)==16);
    assert(offsetof(G0CompileState,completed_units)==24&&offsetof(G0CompileState,end)==32&&offsetof(G0CompileState,request)==40);
    one_unit_and_final();empty_final_and_cap();known_original_bits();
    terminal_errors_preserve_grid();prevalidation_no_writes();return 0;
}
