/* R215-C03: first-party bounded fade-default primitives.
 * Existing ndarray normalization and callback order belong to the caller.
 * Synchronous borrowed buffers remain owned by Python through return.
 * The signature prefix's maximum sum is 512*255, exactly representable.
 */
CE_API int ce_fade_target_signature(const uint8_t *data, int32_t data_length,
    const uint8_t *active, int32_t active_length, int64_t *signature,
    int64_t *report)
{
    if (data_length < 0 || active_length < 0 || signature == NULL ||
        report == NULL || (data_length && data == NULL) ||
        (active_length && active == NULL)) return -1;
    const int32_t nd = data_length < 512 ? data_length : 512;
    const int32_t na = active_length < 512 ? active_length : 512;
    int64_t total = 0, count = 0;
    for (int32_t c = 0; c < nd; c++) total += data[c];
    for (int32_t c = 0; c < na; c++) count += active[c] != 0;
    signature[0] = data_length;
    signature[1] = active_length;
    signature[2] = total;
    signature[3] = count;
    report[0] = nd;
    report[1] = na;
    report[2] = 0;
    report[3] = 0;
    return 0;
}

CE_API int ce_fade_target_frame(const float *data, int32_t data_length,
    const uint8_t *active, int32_t active_length,
    const uint8_t *motor, float *target, int64_t *report)
{
    if (data_length < 0 || active_length < 0 || motor == NULL ||
        target == NULL || report == NULL || (data_length && data == NULL) ||
        (active_length && active == NULL)) return -1;
    for (int32_t c = 0; c < 512; c++) {
        const int owns = c < active_length && active[c] != 0;
        if (owns) target[c] = c < data_length ? data[c] : 0.0f;
        else target[c] = motor[c] != 0 ? 128.0f : 0.0f;
    }
    report[0] = data_length < 512 ? data_length : 512;
    report[1] = active_length < 512 ? active_length : 512;
    report[2] = 512;
    report[3] = 512;
    return 0;
}
