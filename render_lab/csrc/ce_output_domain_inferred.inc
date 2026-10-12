/* Fixed-width inferred ownership, after the public base-zero early gate.
 * Raw u8 comparisons precede truth conversion. Absent neighbors are NULL,
 * not authored zero rows. No allocation or persistent mutable-mask cache.
 */
CE_API int ce_domain_inferred_mask(const uint8_t *base, const uint8_t *current,
        const uint8_t *previous, const uint8_t *next, uint8_t *output,
        int *base_count, int *output_count) {
    int channel, bases = 0, outputs = 0;
    if (!base || !output || !base_count || !output_count) return -1;
    if (!current) {
        for (channel = 0; channel < 512; ++channel) {
            if (base[channel]) return -1;
        }
        memset(output, 0, 512);
        *base_count = 0;
        *output_count = 0;
        return 0;
    }
    for (channel = 0; channel < 512; ++channel) {
        int changed = 0;
        if (previous) changed |= current[channel] != previous[channel];
        if (next) changed |= current[channel] != next[channel];
        bases += base[channel] != 0;
        output[channel] = (uint8_t)((base[channel] != 0) &&
                                   ((current[channel] != 0) || changed));
        outputs += output[channel] != 0;
    }
    *base_count = bases;
    *output_count = outputs;
    return 0;
}
