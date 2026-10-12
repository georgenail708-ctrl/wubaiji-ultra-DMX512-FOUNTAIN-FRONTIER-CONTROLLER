/* Original short-live-frame rule: an out-of-range source makes the level zero. */
CE_API int ce_domain_pump_max(int n, const uint8_t* data, int n_index,
    const int* indices, int* output)
{
    if (n < 0 || n_index < 0 || !output || (n && !data) || (n_index && !indices)) return -1;
    int value = 0;
    for (int i = 0; i < n_index; i++) {
        if (indices[i] < 0) return -1;
        if (indices[i] >= n) { *output = 0; return 0; }
        if ((int)data[indices[i]] > value) value = data[indices[i]];
    }
    *output = value;
    return 0;
}
