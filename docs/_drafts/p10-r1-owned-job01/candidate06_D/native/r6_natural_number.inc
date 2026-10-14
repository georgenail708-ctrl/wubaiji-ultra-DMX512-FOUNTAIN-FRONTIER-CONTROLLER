/* _natural_number selects the LAST decimal run, with its caught int-string
 * limit fallback 10**9. Arbitrary-length ASCII integers are compared as digit
 * spans, not rounded through binary64. Other Nd scripts remain explicit
 * pending until the exact runtime Unicode decimal table is admitted. */
typedef struct {const uint8_t* digits;uint64_t count;} r6_natural;
typedef struct {
    const r5_text* raw;uint64_t cursor,begin,last_begin,last_end;uint32_t in_digits,decimal_limit,trim;
    r6_natural result;
} r6_natural_scan;

static int r6_utf8_one(const uint8_t* bytes,uint64_t n,uint64_t* cursor,uint32_t* cp)
{
    uint64_t i=*cursor;uint8_t first=bytes[i++];uint32_t value;int extra;
    if (first<128) {value=first;extra=0;}
    else if (first>=0xc2 && first<=0xdf) {value=first&31;extra=1;}
    else if (first>=0xe0 && first<=0xef) {value=first&15;extra=2;}
    else if (first>=0xf0 && first<=0xf4) {value=first&7;extra=3;}
    else return R1_INVALID;
    if (n-i<(uint64_t)extra) return R1_INVALID;
    for (int j=0;j<extra;j++) {uint8_t c=bytes[i++];if ((c&0xc0)!=0x80) return R1_INVALID;value=(value<<6)|(c&63);}
    if ((extra==1 && value<128) || (extra==2 && value<2048) || (extra==3 && value<65536)
            || value>0x10ffff || (value>=0xd800 && value<=0xdfff)) return R1_INVALID;
    *cursor=i;*cp=value;return R1_OK;
}

static int r6_natural_begin(r6_natural_scan* out,const r5_text* raw,uint32_t decimal_limit)
{
    if (!out || !raw || raw->reserved || raw->kind>2 || (raw->bytes && !raw->utf8)) return R1_PENDING;
    memset(out,0,sizeof(*out));out->raw=raw;out->decimal_limit=decimal_limit;return R1_OK;
}

static int r6_natural_one(r6_natural_scan* out)
{
    static const uint8_t fallback[]="1000000000";
    const r5_text* raw=out->raw;
    if (out->trim) {
        if (out->last_begin+1<out->last_end && raw->utf8[out->last_begin]=='0') {out->last_begin++;return R1_BUILDING;}
        out->result=(r6_natural){raw->utf8+out->last_begin,out->last_end-out->last_begin};return R1_OK;
    }
    if (raw->kind!=1 || out->cursor==raw->bytes) {
        if (out->in_digits) out->last_end=out->cursor;
        uint64_t begin=out->last_begin,end=out->last_end;
        if (!end || (out->decimal_limit && end-begin>out->decimal_limit)) {out->result=(r6_natural){fallback,10};return R1_OK;}
        out->trim=1;return R1_BUILDING;
    }
    uint64_t begin=out->cursor;uint32_t cp;int status=r6_utf8_one(raw->utf8,raw->bytes,&out->cursor,&cp);if (status) return status;
    int digit=cp>='0' && cp<='9';
    /* Common Latin/Greek/Cyrillic and CJK authoring strings contain no Nd
     * outside ASCII in these admitted Unicode blocks. Do not silently treat
     * Arabic/Thai/fullwidth/mathematical decimal digits as nondigits. */
    if (cp>=0x660 && !(cp>=0x2e80 && cp<=0x9fff) && !(cp>=0x1f300 && cp<=0x1faff)
            && !(cp>=0x20000 && cp<=0x323af)) return R1_PENDING;
    if (digit) {if (!out->in_digits) {out->begin=begin;out->last_begin=begin;}out->in_digits=1;out->last_end=out->cursor;}
    else out->in_digits=0;return R1_BUILDING;
}

typedef struct {r6_natural left,right;uint64_t cursor;int result;uint32_t done;} r6_natural_comparison;

static int r6_natural_compare_one(r6_natural_comparison* state)
{
    if (state->done) return R1_OK;
    if (state->left.count!=state->right.count) {
        state->result=state->left.count<state->right.count ? -1 : 1;state->done=1;return R1_OK;
    }
    uint64_t left=state->left.count-state->cursor,n=left<64 ? left : 64;
    int result=n ? memcmp(state->left.digits+state->cursor,state->right.digits+state->cursor,(size_t)n) : 0;
    state->cursor+=n;if (result || state->cursor==state->left.count) {
        state->result=result<0 ? -1 : result>0;state->done=1;return R1_OK;
    }
    return R1_BUILDING;
}
