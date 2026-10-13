/* C07 research candidate; ordered comparisons, arbitrary-size integer clamp.
 * Semantics reference CPython 3.13.13; no upstream implementation copied. */
static int ltp_validate(const LtpRequest *r, const LtpReport *out)
{
    uint32_t i, stopped = 0;
    if (r == NULL || out == NULL || r->abi != 1 || r->struct_size != sizeof(*r) ||
        r->mode != 0 || r->count != 4 || out->abi != 1 || out->struct_size != sizeof(*out))
        return 0;
    for (i = 0; i < 4; ++i) {
        const LtpValue *v = &r->values[i];
        if (v->selector != i) return 0;
        if (stopped) {
            if (v->kind != 0 || v->offset != 0 || v->length != 0) return 0;
            continue;
        }
        if (v->kind == 3) {
            if (v->offset != 0 || v->length != 0) return 0;
            stopped = 1;
        } else if (i == 0 || i == 3) {
            if (v->kind != 1 || r->integers == NULL || v->length == 0 ||
                v->offset > r->integer_size || v->length > r->integer_size - v->offset)
                return 0;
        } else if (v->kind != 2 || v->offset != 0 || v->length != 0) return 0;
    }
    return 1;
}

static int32_t ltp_integer(const LtpRequest *r, const LtpValue *v)
{
    uint64_t i;
    const unsigned char *p = r->integers + v->offset;
    if ((p[v->length - 1] & 0x80u) != 0) return 0;
    for (i = 1; i < v->length; ++i) if (p[i] != 0) return 255;
    return (int32_t)p[0];
}

CE_API int ce_lt_prepare_v1(const LtpRequest *r, LtpReport *out)
{
    uint32_t i;
    LtpReport result;
    if (!ltp_validate(r, out)) return -1;
    memset(&result, 0, sizeof(result));
    result.abi = 1;
    result.struct_size = (uint32_t)sizeof(result);
    for (i = 0; i < 4; ++i) {
        const LtpValue *v = &r->values[i];
        if (v->kind == 3) {
            result.semantic = 1;
            result.selector = i;
            break;
        }
        if (i == 0) { result.threshold = ltp_integer(r, v); ++result.threshold_values; }
        else if (i == 3) { result.brightness = ltp_integer(r, v); ++result.brightness_values; }
        else {
            double number = v->number > 0.01 ? v->number : 0.01;
            if (i == 1) { result.attack = number; ++result.attack_values; }
            else { result.release = number; ++result.release_values; }
        }
        ++result.consumed;
    }
    memcpy(out, &result, sizeof(result));
    return 0;
}

CE_API int ce_lt_prepare_abi(void) { return 1; }
CE_API int ce_lt_prepare_request_size(void) { return (int)sizeof(LtpRequest); }
CE_API int ce_lt_prepare_value_size(void) { return (int)sizeof(LtpValue); }
CE_API int ce_lt_prepare_report_size(void) { return (int)sizeof(LtpReport); }
