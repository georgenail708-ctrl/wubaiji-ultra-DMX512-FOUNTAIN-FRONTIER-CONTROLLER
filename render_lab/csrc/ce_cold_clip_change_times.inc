/* Cold compiled clip change-time numerical kernel.
 * Exact output contract: Python index-based changed/cummax/gather/f32 table.
 * Inputs already follow original timestamp ordering/universe selection.
 * No sort, projection, ownership rewrite, persistent allocation or native state.
 */
static int ce_cold_ranges_overlap_v1(const void* left, size_t left_bytes,
                                     const void* right, size_t right_bytes)
{
    uintptr_t a = (uintptr_t)left, b = (uintptr_t)right;
    if (!left_bytes || !right_bytes) return 0;
    /* The caller proves each owned pointer refers to a live buffer. */
    return a < b + right_bytes && b < a + left_bytes;
}

CE_API int ce_cold_clip_change_times_v1(
    const uint8_t* frames, const double* frame_times,
    const uint8_t* base_active, int64_t n_frames,
    int64_t frame_elements, int64_t time_elements, int64_t active_elements,
    uint8_t* changed_by_frame, int64_t changed_elements,
    float* last_change_time, int64_t output_elements)
{
    int64_t last[512];
    int64_t f, total;
    int c, warning_flags = 0;
    /* Validate every dimension before pointer dereference or output mutation. */
    if (n_frames < 0 || n_frames > INT64_MAX / 512) return -1;
    total = n_frames * 512;
    if (frame_elements != total || time_elements != n_frames
            || active_elements != 512 || changed_elements != total || output_elements != total) return -2;
    if ((uint64_t)total > SIZE_MAX / sizeof(float)) return -3;
    if ((uint64_t)n_frames > SIZE_MAX / sizeof(double)) return -3;
    if (base_active == NULL) return -4;
    if (n_frames == 0) return 0; /* owned zero-length f32 output, no writes */
    if (frames == NULL || frame_times == NULL || changed_by_frame == NULL || last_change_time == NULL) return -4;
    /* Host must pass native-endian aligned ndarrays. Reject unaligned typed
     * pointers before reads/writes rather than rely on x86's tolerant loads. */
    if ((uintptr_t)frame_times % sizeof(double) || (uintptr_t)last_change_time % sizeof(float)) return -6;
    if ((uintptr_t)frames > UINTPTR_MAX - (size_t)total
            || (uintptr_t)frame_times > UINTPTR_MAX - (size_t)n_frames * sizeof(double)
            || (uintptr_t)base_active > UINTPTR_MAX - 512
            || (uintptr_t)changed_by_frame > UINTPTR_MAX - (size_t)total
            || (uintptr_t)last_change_time > UINTPTR_MAX - (size_t)total * sizeof(float)) return -3;
    if (ce_cold_ranges_overlap_v1(frames,(size_t)total,last_change_time,(size_t)total*sizeof(float))
            || ce_cold_ranges_overlap_v1(frame_times,(size_t)n_frames*sizeof(double),last_change_time,(size_t)total*sizeof(float))
            || ce_cold_ranges_overlap_v1(base_active,512,last_change_time,(size_t)total*sizeof(float))
            || ce_cold_ranges_overlap_v1(frames,(size_t)total,changed_by_frame,(size_t)total)
            || ce_cold_ranges_overlap_v1(frame_times,(size_t)n_frames*sizeof(double),changed_by_frame,(size_t)total)
            || ce_cold_ranges_overlap_v1(base_active,512,changed_by_frame,(size_t)total)
            || ce_cold_ranges_overlap_v1(changed_by_frame,(size_t)total,last_change_time,(size_t)total*sizeof(float))) return -5;
    /* No callback/GIL reentry; ABI itself never raises a Python exception. */
    for (c = 0; c < 512; ++c) last[c] = -1;
    for (f = 0; f < n_frames; ++f) {
        const uint8_t* row = frames + (size_t)f * 512;
        const uint8_t* previous = (f == 0) ? NULL : row - 512;
        uint8_t* changed = changed_by_frame + (size_t)f * 512;
        float* output = last_change_time + (size_t)f * 512;
        for (c = 0; c < 512; ++c) {
            changed[c] = (uint8_t)(base_active[c] && (f == 0 || row[c] != previous[c]));
            if (changed[c]) last[c] = f;
            /* Preserve INDEX semantics: a NaN timestamp is retained until the
             * next VALUE change; later changes replace it. Do not use temporal
             * floating-point max/accumulate, which would poison later rows. */
            if (last[c] < 0) {
                output[c] = 0.0f;
            } else {
                double selected = frame_times[last[c]];
                uint64_t bits;
                float narrowed = (float)selected;
                memcpy(&bits, &selected, sizeof(bits));
                output[c] = narrowed;
                if (isfinite(selected) && isinf(narrowed)) warning_flags |= 1;
                /* Signaling NaN narrowing reproduces NumPy's invalid flag. */
                if ((bits & UINT64_C(0x7ff0000000000000)) == UINT64_C(0x7ff0000000000000)
                        && (bits & UINT64_C(0x000fffffffffffff))
                        && !(bits & UINT64_C(0x0008000000000000))) warning_flags |= 2;
            }
        }
    }
    return warning_flags;
}
