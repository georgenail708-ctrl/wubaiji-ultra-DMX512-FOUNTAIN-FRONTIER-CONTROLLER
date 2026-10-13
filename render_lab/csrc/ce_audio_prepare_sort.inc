/* Independent per-request scratch; no qsort global/context or numeric memo. */
static int cap_compare(const void *first, const void *last)
{
    const cap_sort_row *a = (const cap_sort_row *)first, *b = (const cap_sort_row *)last;
    if (a->key < b->key) return -1;
    if (a->key > b->key) return 1;
    if (a->secondary < b->secondary) return -1;
    if (a->secondary > b->secondary) return 1;
    return a->index < b->index ? -1 : a->index > b->index ? 1 : 0;
}

static int cap_selected(cap_batch *batch, const ce_audio_prepare_step_v1 *step, cap_sort_row *rows)
{
    int count = 0, kind = step->op == CAP_POINT_SORT ? CAP_POINT : CAP_CLIP;
    for (int index = step->first; index < step->last; ++index) {
        if (batch->step[index].op != kind || !batch->flags[index]) continue;
        rows[count].index = index;
        rows[count].key = batch->output[index * 4];
        rows[count].secondary = kind == CAP_CLIP ? batch->output[index * 4 + 1] : 0.0;
        ++count;
    }
    return count;
}

static int cap_publish_order(cap_batch *batch, const ce_audio_prepare_step_v1 *step, cap_sort_row *rows, int count)
{
    int published = 0;
    for (int index = 0; index < count; ++index) {
        if (step->op == CAP_POINT_SORT && index && index + 1 < count
            && rows[index - 1].key == rows[index].key && rows[index + 1].key == rows[index].key) continue;
        batch->order[step->first + published++] = rows[index].index;
    }
    return published;
}

static int cap_sort(cap_batch *batch, int index)
{
    const ce_audio_prepare_step_v1 *step = batch->step + index;
    if (step->first < 0 || step->last < step->first || step->last > index) return -1;
    int width = step->last - step->first;
    if (!width) { batch->flags[index] = 1; return 0; }
    cap_sort_row *rows = (cap_sort_row *)malloc((size_t)width * sizeof(*rows));
    if (!rows) return -2;
    int count = cap_selected(batch, step, rows);
    qsort(rows, (size_t)count, sizeof(*rows), cap_compare);
    batch->count[index] = cap_publish_order(batch, step, rows, count);
    batch->report->sort_rows += (uint64_t)count;
    batch->flags[index] = 1;
    free(rows);
    return 0;
}
