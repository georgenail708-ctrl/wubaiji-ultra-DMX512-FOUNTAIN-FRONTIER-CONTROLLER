/* Per-channel ownership math. Public state/container updates stay in Python. */
#define CE_DOMAIN_LIMIT 8192

CE_API int ce_domain_mask(int op, int n, const uint8_t* left,
    const uint8_t* right, uint8_t* output, int* count)
{
    if (op < 0 || op > 6 || n < 0 || count == NULL ||
        (n && (left == NULL || output == NULL || (op && right == NULL)))) return -1;
    int any = 0, total = 0;
    if (op == 5) for (int c = 0; c < n; c++) any |= left[c] != 0;
    for (int c = 0; c < n; c++) {
        const int a = left[c] != 0, b = right == NULL ? 0 : right[c] != 0;
        int value = a;
        if (op == 1) value = a && b;
        if (op == 2 || op == 4) value = a || b;
        if (op == 3) value = a && !b;
        if (op == 5) value = b && (!any || a);
        if (op == 6) value = left[c] != right[c];
        output[c] = (uint8_t)value;
        total += op == 4 ? b && !a : value;
    }
    *count = total;
    return 0;
}

CE_API int ce_domain_ownership(int n, const uint8_t* active, const double* scores,
    const uint8_t* data, const uint8_t* same, const uint8_t* restorable,
    uint8_t* effective, uint8_t* hold_lost, uint8_t* takeover_lost, int* winner)
{
    if (n < 0 || (size_t)n > SIZE_MAX / 512 ||
        (n && (size_t)n > SIZE_MAX / (size_t)n) || winner == NULL ||
        (n && (!active || !scores || !data || !same || !restorable ||
               !effective || !hold_lost || !takeover_lost))) return -1;
    for (int c = 0; c < 512; c++) {
        double best = -INFINITY;
        for (int i = 0; i < n; i++) {
            const double score = scores[(size_t)i * 512 + c];
            if (active[(size_t)i * 512 + c] && isfinite(score) && score > best) best = score;
        }
        int win = -1;
        for (int i = 0; i < n; i++) {
            const size_t row = (size_t)i * 512 + c;
            const int eff = active[row] && isfinite(scores[row]) && scores[row] >= best - 1e-12;
            effective[row] = (uint8_t)eff;
            if (eff && win < 0) win = i;
        }
        winner[c] = win;
        for (int i = 0; i < n; i++) {
            const size_t row = (size_t)i * 512 + c;
            int peer = 0;
            if (active[row] && !effective[row]) {
                for (int j = 0; j < n; j++) {
                    if (j != i && same[(size_t)i * n + j] && effective[(size_t)j * 512 + c]) {
                        peer = 1;
                        break;
                    }
                }
            }
            hold_lost[row] = (uint8_t)peer;
            takeover_lost[row] = (uint8_t)(active[row] && win >= 0 && win != i &&
                !restorable[(size_t)i * n + win] && data[row] != data[(size_t)win * 512 + c]);
        }
    }
    return 0;
}

CE_API int ce_domain_legacy_scores(int n, const double* scores, const uint8_t* active,
    const uint8_t* rules, const double* ties, const uint8_t* boosted,
    double boost, double* output, uint8_t* masks)
{
    if (n < 0 || (size_t)n > SIZE_MAX / 512 ||
        (n && (!scores || !active || !rules || !ties || !boosted || !output || !masks))) return -1;
    for (int i = 0; i < n; i++) {
        for (int c = 0; c < 512; c++) {
            const size_t index = (size_t)i * 512 + c;
            double value = scores[index] + ties[i];
            if (boosted[i]) value = value + boost;
            output[index] = value;
            masks[index] = (uint8_t)(active[index] && rules[index]);
        }
    }
    return 0;
}
