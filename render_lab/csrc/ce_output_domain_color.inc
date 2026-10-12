/* Original live RGB choice; absent source differs from a short present row. */
CE_API int ce_domain_color_pick(int n, const uint8_t* data, const int* indices,
    uint8_t* output)
{
    if (n < -1 || !indices || !output || (n >= 0 && !data) || (n == -1 && data)) return -1;
    output[0] = output[1] = output[2] = 255;
    if (n == -1) return 0;
    uint8_t rgb[3];
    for (int color = 0; color < 3; color++) {
        const int index = indices[color];
        if (index < -1 || index >= n) return -1;
        rgb[color] = index < 0 ? 255 : data[index];
    }
    if (rgb[0] || rgb[1] || rgb[2]) {
        memcpy(output, rgb, 3);
        return 0;
    }
    const int white = indices[3];
    if (white < -1 || white >= n) return -1;
    if (white >= 0) output[0] = output[1] = output[2] = data[white];
    return 0;
}
