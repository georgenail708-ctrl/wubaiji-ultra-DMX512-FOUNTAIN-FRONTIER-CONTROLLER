/* Default no-filter finalize keeps the caller's numeric dtype and precision. */
#define CE_PLAIN_CLIP_SIGNED(T) do { \
    T* row=(T*)data; for(int at=0;at<count;++at) { \
        if(light && light[at]) row[at]=0; \
        else if(row[at]<0) row[at]=0; else if(row[at]>255) row[at]=255; \
    } } while(0)
#define CE_PLAIN_CLIP_UNSIGNED(T) do { \
    T* row=(T*)data; for(int at=0;at<count;++at) { \
        if(light && light[at]) row[at]=0; else if(row[at]>255) row[at]=255; \
    } } while(0)
#define CE_PLAIN_CLIP_FLOAT(T) CE_PLAIN_CLIP_SIGNED(T)

static void _ce_plain_half(uint16_t* row,int count,const uint8_t* light)
{
    for(int at=0;at<count;++at) {
        uint16_t value=row[at],magnitude=value&0x7fffu;
        if(light && light[at]) row[at]=0;
        else if(magnitude>0x7c00u) continue;
        else if((value&0x8000u) && magnitude) row[at]=0;
        else if(!(value&0x8000u) && value>0x5bf8u) row[at]=0x5bf8u;
    }
}

#define CE_PLAIN_COMPLEX(T) do { \
    T* row=(T*)data; for(int at=0;at<count;++at) { \
        T real=row[(size_t)at*2],imag=row[(size_t)at*2+1]; \
        if(light && light[at]) {row[(size_t)at*2]=0;row[(size_t)at*2+1]=0;} \
        else if(isnan(real) || isnan(imag)) continue; \
        else if(real<0 || (real==0 && imag<0)) {row[(size_t)at*2]=0;row[(size_t)at*2+1]=0;} \
        else if(real>255 || (real==255 && imag>0)) {row[(size_t)at*2]=255;row[(size_t)at*2+1]=0;} \
    } } while(0)

CE_API int ce_output_filter_clamp(void* data,int count,int kind,const uint8_t* light)
{
    if(count<0 || kind<0 || kind>12 || (count && !data)) return -1;
    switch(kind) {
    case 0: /* bool and u8 are already in range. */
        if(light) for(int at=0;at<count;++at) if(light[at]) ((uint8_t*)data)[at]=0;
        break;
    case 1: /* int8 cannot exceed the upper bound. */
        for(int at=0;at<count;++at) if((light && light[at]) || ((int8_t*)data)[at]<0) ((int8_t*)data)[at]=0;
        break;
    case 2: CE_PLAIN_CLIP_SIGNED(int16_t); break;
    case 3: CE_PLAIN_CLIP_UNSIGNED(uint16_t); break;
    case 4: CE_PLAIN_CLIP_SIGNED(int32_t); break;
    case 5: CE_PLAIN_CLIP_UNSIGNED(uint32_t); break;
    case 6: CE_PLAIN_CLIP_SIGNED(int64_t); break;
    case 7: CE_PLAIN_CLIP_UNSIGNED(uint64_t); break;
    case 8: CE_PLAIN_CLIP_FLOAT(float); break;
    case 9: CE_PLAIN_CLIP_FLOAT(double); break;
    case 10: _ce_plain_half((uint16_t*)data,count,light); break;
    case 11: CE_PLAIN_COMPLEX(float); break;
    case 12: CE_PLAIN_COMPLEX(double); break;
    }
    return 0;
}
#undef CE_PLAIN_CLIP_SIGNED
#undef CE_PLAIN_CLIP_UNSIGNED
#undef CE_PLAIN_CLIP_FLOAT
#undef CE_PLAIN_COMPLEX
