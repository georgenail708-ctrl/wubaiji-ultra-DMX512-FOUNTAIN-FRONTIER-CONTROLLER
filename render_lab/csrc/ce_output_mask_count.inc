/* Count-only adaptation of ce_domain_mask(op=0)'s first-party !=0 loop.
 * Old ce_domain_mask, including its required output pointer, is unchanged.
 * The caller owns valid, disjoint input/output buffers through this call.
 */
typedef struct ce_mask_count_row {
    const uint8_t *data;
    int32_t length;
    int32_t reserved;
} ce_mask_count_row;

static int ce_mask_count_rows_valid(int32_t nrows, const ce_mask_count_row *rows)
{
    if (nrows < 0 || (nrows && rows == NULL) ||
        (size_t)nrows > SIZE_MAX / sizeof(ce_mask_count_row)) return 0;
    for (int32_t i = 0; i < nrows; i++) {
        const ce_mask_count_row *row = &rows[i];
        if (row->length < 0 || row->reserved != 0 ||
            (row->length && row->data == NULL)) return 0;
    }
    return 1;
}

static int32_t ce_mask_count_bytes(const uint8_t *data, int32_t length)
{
    int32_t total = 0;
    for (int32_t c = 0; c < length; c++) {
        const int value = data[c] != 0;
        total += value;
    }
    return total;
}

CE_API int ce_domain_mask_counts(int32_t nrows, const ce_mask_count_row *rows,
    int32_t *counts, int32_t *selected, int64_t *report)
{
    if (report == NULL || (nrows > 0 && (counts == NULL || selected == NULL)) ||
        !ce_mask_count_rows_valid(nrows, rows)) return -1;
    int32_t selected_length = 0;
    int64_t actual_values = 0;
    for (int32_t i = 0; i < nrows; i++) {
        counts[i] = ce_mask_count_bytes(rows[i].data, rows[i].length);
        actual_values += rows[i].length;
        if (counts[i]) selected[selected_length++] = i;
    }
    /* INT32_MAX rows of INT32_MAX values fit in this signed int64 sum. */
    report[0] = nrows;
    report[1] = actual_values;
    report[2] = selected_length;
    report[3] = 0;
    return 0;
}
