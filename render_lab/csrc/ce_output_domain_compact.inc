/* Default group semantics share one native rule with the public adapter. */
static int _ce_dom_chain_restores(int loser, int winner)
{
    return loser != winner;
}

CE_API int ce_domain_chain_restorable(int loser, int winner, int* output)
{
    if (loser < 0 || winner < 0 || output == NULL) return -1;
    *output = _ce_dom_chain_restores(loser, winner);
    return 0;
}

CE_API int ce_domain_ownership_compact(int n, int ngroups, const uint8_t* active,
    const double* scores, const uint8_t* data, const int* groups,
    const int* identities, const uint8_t* restorable, uint8_t* effective,
    uint8_t* hold_lost, uint8_t* takeover_lost, int* winner)
{
    if (n < 0 || ngroups < 0 || ngroups > n || (n && !ngroups) || winner == NULL ||
        (size_t)n > SIZE_MAX / 512 || (size_t)ngroups > SIZE_MAX / sizeof(int) ||
        (restorable && ngroups && (size_t)ngroups > SIZE_MAX / (size_t)ngroups) ||
        (n && (!active || !scores || !data || !groups || !identities ||
               !effective || !hold_lost || !takeover_lost))) return -1;
    for (int i = 0; i < n; i++) {
        if (groups[i] < 0 || groups[i] >= ngroups || identities[i] < 0 || identities[i] >= n) return -1;
    }
    int* first = ngroups ? (int*)malloc((size_t)ngroups * sizeof(int)) : NULL;
    uint8_t* several = ngroups ? (uint8_t*)malloc((size_t)ngroups) : NULL;
    if (ngroups && (!first || !several)) { free(first); free(several); return -2; }
    for (int c = 0; c < 512; c++) {
        double best = -INFINITY;
        for (int i = 0; i < n; i++) {
            const size_t row = (size_t)i * 512 + c;
            if (active[row] && isfinite(scores[row]) && scores[row] > best) best = scores[row];
        }
        for (int group = 0; group < ngroups; group++) { first[group] = -1; several[group] = 0; }
        int win = -1;
        for (int i = 0; i < n; i++) {
            const size_t row = (size_t)i * 512 + c;
            const int eff = active[row] && isfinite(scores[row]) && scores[row] >= best - 1e-12;
            effective[row] = (uint8_t)eff;
            if (!eff) continue;
            if (win < 0) win = i;
            const int group = groups[i];
            if (first[group] < 0) first[group] = identities[i];
            else if (first[group] != identities[i]) several[group] = 1;
        }
        winner[c] = win;
        for (int i = 0; i < n; i++) {
            const size_t row = (size_t)i * 512 + c;
            const int group = groups[i];
            hold_lost[row] = (uint8_t)(active[row] && !effective[row] && first[group] >= 0 &&
                (first[group] != identities[i] || several[group]));
            int restores = 1;
            if (win >= 0) {
                restores = restorable ? restorable[(size_t)group * ngroups + groups[win]] != 0
                                      : _ce_dom_chain_restores(group, groups[win]);
            }
            takeover_lost[row] = (uint8_t)(active[row] && win >= 0 && win != i && !restores &&
                data[row] != data[(size_t)win * 512 + c]);
        }
    }
    free(first); free(several);
    return 0;
}
