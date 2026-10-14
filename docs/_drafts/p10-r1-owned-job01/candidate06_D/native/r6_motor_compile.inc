/* _build_motor_channel_mask then the original generic pair expansion.
 * Uses raw definition channel-name bytes. Attached definitions are ignored
 * here, exactly as _build_motor_channel_mask does (named defs_by_id lookup). */
typedef struct {
    const r4_input* raw;uint32_t universe;
    uint64_t fixture,definition,selected,channel;uint32_t stage,cancelled,base_any;
    int start;r4_pair_mask pair;uint8_t mask[512];
} r6_motor;

static int r6_motor_name(const r4_channel_name* name,int* result)
{
    static const char prefix[]="motor_";
    if (name->bytes>512 || (name->bytes && !name->utf8)) return R1_PENDING;
    *result=0;if (name->bytes!=7) return R1_OK;
    for (int i=0;i<7;i++) if (name->utf8[i]>=128) return R1_PENDING;
    for (int i=0;i<6;i++) {
        uint8_t c=name->utf8[i];if (c>='A' && c<='Z') c=(uint8_t)(c+32);
        if (c!=(uint8_t)prefix[i]) return R1_OK;
    }
    uint8_t axis=name->utf8[6];if (axis>='A' && axis<='Z') axis=(uint8_t)(axis+32);
    *result=axis=='x' || axis=='y' || axis=='z';return R1_OK;
}

static int r6_motor_create(r6_motor* out,const r4_input* raw,uint32_t universe)
{
    if (!out || !raw || (raw->patch_fixtures_n && !raw->patch_fixtures)
            || (raw->patch_definitions_n && !raw->patch_definitions)) return R1_INVALID;
    /* Outer R6 create must admit all raw4 nested owner bytes first. This
     * internal stage never accepts a public uncharged definition pointer. */
    memset(out,0,sizeof(*out));out->raw=raw;out->universe=universe;out->selected=UINT64_MAX;
    return R1_OK;
}

static int r6_motor_fixture_one(r6_motor* out)
{
    if (out->fixture==out->raw->patch_fixtures_n) {
        out->pair.fixtures=out->raw->patch_fixtures;out->pair.fixtures_n=out->raw->patch_fixtures_n;
        out->pair.definitions=out->raw->patch_definitions;out->pair.definitions_n=out->raw->patch_definitions_n;
        out->pair.universe=out->universe;memcpy(out->pair.mask,out->mask,512);out->stage=3;return R1_OK;
    }
    const r4_patch_fixture* fixture=out->raw->patch_fixtures+out->fixture;
    if (fixture->reserved) return R1_PENDING;
    if (fixture->universe!=out->universe) {out->fixture++;return R1_OK;}
    /* This origin is int(value), without 'or 1'. None is an original error,
     * not the otherwise valid zero/out-of-range address skip. */
    if (fixture->start_channel.kind==R1_NONE) return R1_PENDING;
    double address;int status=r5_number(fixture->start_channel,1.,&address);if (status) return status;
    if (!isfinite(address)) return R1_PENDING;address=trunc(address);
    if (address<1. || address>512.) {out->fixture++;return R1_OK;}
    out->start=(int)address-1;out->definition=0;out->selected=UINT64_MAX;out->stage=1;return R1_OK;
}

static int r6_motor_definition_one(r6_motor* out)
{
    const r4_patch_fixture* fixture=out->raw->patch_fixtures+out->fixture;
    if (out->definition<out->raw->patch_definitions_n) {
        uint64_t i=out->definition++;if (out->raw->patch_definitions[i].token==fixture->definition) out->selected=i;
        return R1_OK;
    }
    if (out->selected==UINT64_MAX) {out->fixture++;out->stage=0;return R1_OK;}
    out->channel=0;out->stage=2;return R1_OK;
}

static int r6_motor_channel_one(r6_motor* out)
{
    const r4_patch_definition* definition=out->raw->patch_definitions+out->selected;
    if (out->channel==definition->types_n || out->channel>=(uint64_t)(512-out->start)) {
        out->fixture++;out->stage=0;return R1_OK;
    }
    if (!definition->names) return R1_INVALID;
    int motor=0;int status=r6_motor_name(definition->names+out->channel,&motor);if (status) return status;
    if (motor) {out->mask[out->start+(int)out->channel]=1;out->base_any=1;}out->channel++;return R1_OK;
}

static int r6_motor_pair_one(r6_motor* out)
{
    /* The original expansion returns immediately for an empty base mask. */
    if (!out->base_any) {out->stage=4;return R1_OK;}
    int status=r4_pair_step(&out->pair,1);if (status<0) return status;
    if (status==R1_OK) {memcpy(out->mask,out->pair.mask,512);out->stage=4;}return R1_OK;
}

static int r6_motor_step(r6_motor* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=4 && !status) switch(out->stage) {
    case 0:status=r6_motor_fixture_one(out);break;case 1:status=r6_motor_definition_one(out);break;
    case 2:status=r6_motor_channel_one(out);break;case 3:status=r6_motor_pair_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==4 ? R1_OK : R1_BUILDING;
}
