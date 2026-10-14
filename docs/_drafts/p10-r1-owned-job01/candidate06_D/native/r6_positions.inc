/* Concrete C direction selector; linear receives the native hidden coordinate
 * layer, circles receive native actual coordinates. No host-prepared ranks. */
typedef struct {
    const double *xy,*scope;uint64_t n,scope_n;uint32_t direction;
    const r6_shapes* shapes;uint32_t prefer_shapes,start_valid;double start;
} r6_positions_call;

typedef struct {
    uint32_t kind;union {r6_linear linear;r6_circle_positions circle;} value;
} r6_positions;

static int r6_positions_create(r6_positions* out,r1_ledger* ledger,const r6_positions_call* call)
{
    if (!out || !call) return R1_INVALID;memset(out,0,sizeof(*out));
    if (call->direction==R5_DIR_CW || call->direction==R5_DIR_CCW) {
        r6_circle_call circle={call->xy,call->scope,call->n,call->scope_n,call->shapes,
            call->direction==R5_DIR_CW,call->prefer_shapes,call->start_valid,call->start};
        out->kind=2;return r6_circle_positions_create(&out->value.circle,ledger,&circle);
    }
    if (call->direction<R5_DIR_LTR || call->direction>R5_DIR_RTC) return R1_PENDING;
    r6_linear_call linear={call->xy,call->scope,call->n,call->scope_n,call->direction};
    out->kind=1;return r6_linear_create(&out->value.linear,ledger,&linear);
}

static int r6_positions_step(r6_positions* out,uint32_t units)
{
    if (out->kind==1) return r6_linear_step(&out->value.linear,units);
    if (out->kind==2) return r6_circle_positions_step(&out->value.circle,units);return R1_INVALID;
}

static const float* r6_positions_values(const r6_positions* out)
{
    return out->kind==1 ? out->value.linear.positions : out->kind==2 ? out->value.circle.positions : NULL;
}

static void r6_positions_destroy(r6_positions* out)
{
    if (out->kind==1) r6_linear_destroy(&out->value.linear);
    if (out->kind==2) r6_circle_positions_destroy(&out->value.circle);memset(out,0,sizeof(*out));
}
