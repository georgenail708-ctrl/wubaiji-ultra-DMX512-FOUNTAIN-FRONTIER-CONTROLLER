static int r4_ascii_space(uint8_t c)
{
    return c==' ' || c=='\t' || c=='\n' || c=='\r' || c=='\f' || c=='\v';
}

/* This parser only needs the observable in-range channel value. Python
 * int overflow/invalid exceptions are swallowed by the original channel loop.
 * A huge valid magnitude stays out of range without unsafe C int conversion. */
static int r4_text_channel(const r4_channel_raw* raw,int* channel)
{
    uint64_t left=0,right=raw->text_bytes;int negative=0,value=0,digits=0,underscore=0;
    if (right && !raw->text) return R1_INVALID;
    if (right>4096) return R1_PENDING; /* unported configurable CPython digit-limit domain */
    while (left<right && r4_ascii_space(raw->text[left])) left++;
    while (right>left && r4_ascii_space(raw->text[right-1])) right--;
    if (left<right && (raw->text[left]=='+' || raw->text[left]=='-')) negative=raw->text[left++]=='-';
    for (uint64_t i=left;i<right;i++) {
        uint8_t c=raw->text[i];
        if (c>=128) return R1_PENDING; /* non-ASCII Unicode int domain not silently lost */
        if (c=='_' && digits && !underscore) {underscore=1;continue;}
        if (c<'0' || c>'9') return 0;
        if (value<=512) value=value*10+(c-'0');
        digits=1;underscore=0;
    }
    if (!digits || underscore) return 0;
    if (negative || value<1 || value>512) return 2; /* valid int, outside DMX range */
    *channel=value-1;return 1;
}

static int r4_numeric_channel(const r4_channel_raw* raw,int* channel,int* equal_integer)
{
    double value;
    if (raw->number.kind==R1_NONE) return 0;
    if (r1_atom_number(raw->number,0.,&value)) {
        if (raw->number.kind==R1_F64) return 0; /* int(NaN/inf) caught by original */
        return R1_PENDING;
    }
    double integer=trunc(value);*equal_integer=value==integer;
    if (integer<1. || integer>512.) return 0;
    *channel=(int)integer-1;return 1;
}
