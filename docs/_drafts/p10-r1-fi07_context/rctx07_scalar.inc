#include <stdio.h>
#include <locale.h>
/* Text copy is32 bytes/unit; libc decimal conversions have a fixed256-byte
 * admitted input cap. Larger numeric text is an explicit primitive-domain
 * refusal, distinct from a caught invalid conversion. No locale mutation. */
enum {RC07_OR_ZERO=16,RC07_USE_DEFAULT=32};
static int rc07_int_float(double x,uint8_t* bytes,uint64_t* n)
{
    if (!isfinite(x)) return R1_PENDING;uint64_t bits;memcpy(&bits,&x,8);
    uint64_t m=(bits&UINT64_C(0xfffffffffffff))|UINT64_C(0x10000000000000);
    int exponent=(int)((bits>>52)&2047)-1023-52;memset(bytes,0,129);
    if (((bits>>52)&2047)==0 || exponent<-52) {*n=1;return R1_OK;}
    if (exponent<0) {m>>=-exponent;exponent=0;}
    for (uint32_t i=0;i<53;i++) if ((m>>i)&1) bytes[(i+exponent)/8]|=(uint8_t)(1U<<((i+exponent)%8));
    *n=(uint64_t)(exponent+53+7)/8+1;
    if (bits>>63) {unsigned carry=1;for (uint64_t i=0;i<*n;i++) {carry+=(uint8_t)~bytes[i];bytes[i]=(uint8_t)carry;carry>>=8;}}
    return R1_OK;
}
static int rc07_ascii_integer(const uint8_t* text,uint64_t n,uint32_t limit,uint8_t* out,uint64_t* bytes)
{
    uint64_t a=0,b=n;uint32_t digits=0,under=0;int neg=0;
    while (a<b && r4_ascii_space(text[a])) a++;while (b>a && r4_ascii_space(text[b-1])) b--;
    if (a<b && (text[a]=='+' || text[a]=='-')) neg=text[a++]=='-';memset(out,0,129);
    uint64_t used=1;
    for (uint64_t i=a;i<b;i++) {
        unsigned c=text[i];if (c>=128) return R1_PENDING;
        if (c=='_' && digits && !under) {under=1;continue;}
        if (c<'0' || c>'9') return R1_INVALID;
        unsigned carry=c-'0';for (uint64_t k=0;k<used;k++) {carry+=out[k]*10U;out[k]=(uint8_t)carry;carry>>=8;}
        if (carry) {if (used==128) return R1_PENDING;out[used++]=(uint8_t)carry;}
        digits++;under=0;
    }
    if (!digits || under || (limit && digits>limit)) return R1_INVALID;
    if (out[used-1]&128) out[used++]=0;
    if (neg) {unsigned carry=1;for (uint64_t i=0;i<used;i++) {carry+=(uint8_t)~out[i];out[i]=(uint8_t)carry;carry>>=8;}}
    *bytes=used;return R1_OK;
}
static int rc07_ascii_float(uint8_t* buffer,uint64_t n,double* out)
{
    if (n>255) return R1_PENDING;struct lconv* locale=localeconv();
    if (!locale || strcmp(locale->decimal_point,".")) return R1_PENDING;
    uint64_t first=0,end=n,k=0;while (first<end && r4_ascii_space(buffer[first])) first++;
    while (end>first && r4_ascii_space(buffer[end-1])) end--;
    int previous_digit=0;
    for (uint64_t i=first;i<end;i++) {
        uint8_t c=buffer[i];if (c>=128) return R1_PENDING;
        if (c=='_') {if (!previous_digit || i+1==end || buffer[i+1]<'0' || buffer[i+1]>'9') return R1_INVALID;previous_digit=0;continue;}
        if ((c>='a' && c<='z') || (c>='A' && c<='Z')) {
            uint8_t low=(c>='A' && c<='Z') ? c+32 : c;
            if (low!='e' && low!='n' && low!='a' && low!='i' && low!='f' && low!='t' && low!='y') return R1_INVALID;
        } else if (!(c>='0' && c<='9') && c!='.' && c!='+' && c!='-') return R1_INVALID;
        buffer[k++]=c;previous_digit=c>='0' && c<='9';
    }
    buffer[k]=0;char* tail=NULL;*out=strtod((const char*)buffer,&tail);
    return k && tail==(char*)buffer+k ? R1_OK : R1_INVALID;
}
static int rc07_repr_float(double value,uint8_t* out,uint64_t* length)
{
    struct lconv* locale=localeconv();if (!locale || strcmp(locale->decimal_point,".")) return R1_PENDING;
    if (isnan(value)) {*length=3;memcpy(out,"nan",3);return R1_OK;}
    if (isinf(value)) {const char* s=signbit(value)?"-inf":"inf";*length=strlen(s);memcpy(out,s,(size_t)*length);return R1_OK;}
    /* Find shortest nearest decimal that roundtrips using the actual CRT.
     * Native full-bit independent controls still qualify this adaptation. */
    char candidate[64];uint64_t wanted;memcpy(&wanted,&value,8);int used=0;
    for (int digits=1;digits<=17;digits++) {
        used=snprintf(candidate,sizeof(candidate),"%.*g",digits,value);char* tail;double x=strtod(candidate,&tail);uint64_t got;memcpy(&got,&x,8);
        if (got==wanted && *tail==0) break;
    }
    if (used<=0 || used>=64) return R1_PENDING;
    char* exp=strchr(candidate,'e');if (!exp) exp=strchr(candidate,'E');int decimal_exp=0;
    if (exp) decimal_exp=atoi(exp+1);
    char digits[24];int count=0,sign=candidate[0]=='-',dot=-1;
    for (char* c=candidate+sign;*c && c!=exp;c++) {if (*c=='.') dot=count;else digits[count++]=*c;}
    if (dot<0) dot=count;decimal_exp+=dot-1;uint64_t pos=0;if (sign) out[pos++]='-';
    if (decimal_exp<-4 || decimal_exp>=16) {
        out[pos++]=(uint8_t)digits[0];if (count>1) {out[pos++]='.';for (int i=1;i<count;i++) out[pos++]=(uint8_t)digits[i];}
        out[pos++]='e';out[pos++]=decimal_exp<0?'-':'+';unsigned e=(unsigned)abs(decimal_exp);
        if (e>=100) out[pos++]=(uint8_t)('0'+e/100);out[pos++]=(uint8_t)('0'+(e/10)%10);out[pos++]=(uint8_t)('0'+e%10);
    } else {
        int point=decimal_exp+1;if (point<=0) {out[pos++]='0';out[pos++]='.';for (int i=0;i<-point;i++) out[pos++]='0';}
        for (int i=0;i<count;i++) {if (i==point && point>0) out[pos++]='.';out[pos++]=(uint8_t)digits[i];}
        if (point>=count) {for (int i=count;i<point;i++) out[pos++]='0';out[pos++]='.';out[pos++]='0';}
    }
    *length=pos;return R1_OK;
}
static int rc07_scalar_begin(rctx07_job* job,const rctx07_value* raw,uint32_t mode,uint64_t fallback)
{
    rctx07_scalar* s=&job->scalar;if (s->active) return R1_INVALID;memset(s,0,sizeof(*s));
    s->raw=raw;s->mode=mode;s->fallback=fallback;s->active=1;
    if (raw->reserved || raw->kind==RC07_FOREIGN || raw->kind>RC07_FOREIGN) {job->semantic=RC07_C_FOREIGN;return R1_PENDING;}
    if (raw->kind==RC07_INTEGER_BYTES && !raw->bytes) return R1_INVALID;
    if (raw->bytes && !raw->data) return R1_INVALID;return R1_OK;
}
static int rc07_scalar_finish(rctx07_job* job)
{
    rctx07_scalar* s=&job->scalar;const rctx07_value* raw=s->raw;rctx07_program* p=job->program;
    uint32_t mode=s->mode&15;double value=0.;int truth,status;uint64_t n=0;uint8_t bytes[129];
    if ((s->mode&RC07_OR_ZERO) || mode==RC07_STRING_OR_EMPTY || mode==RC07_STRING_OR_ID) {
        status=rc07_truth(raw,&truth);if (status) {job->semantic=RC07_C_FOREIGN;return status;}
        if (!truth) {if (mode==RC07_STRING_OR_ID) n=(uint64_t)snprintf((char*)s->local,256,"%llu",(unsigned long long)s->fallback);
            else if (mode==RC07_STRING_OR_EMPTY) n=0;else value=0.;goto scalar_ready;}
    }
    if (raw->kind==RC07_TEXT) {
        if (mode==RC07_RAW_LEAF || mode==RC07_STRING || mode==RC07_STRING_OR_EMPTY || mode==RC07_STRING_OR_ID) {
            status=rc07_node(p,RFT_TEXT,0,raw->bytes,&s->node);if (status) return status;s->done=2;return R1_BUILDING;
        }
        if (raw->bytes>255) {job->semantic=RC07_C_LONG_NUMERIC;return R1_PENDING;}
        if (raw->bytes) memcpy(s->local,raw->data,(size_t)raw->bytes);
        status=mode==RC07_INT ? rc07_ascii_integer(s->local,raw->bytes,job->raw->int_max_str_digits,bytes,&n)
                            : rc07_ascii_float(s->local,raw->bytes,&value);
        if (status) {job->semantic=status==R1_INVALID ? (mode==RC07_INT ? RC07_C_BAD_INT : RC07_C_BAD_FLOAT) : RC07_C_LONG_NUMERIC;return R1_PENDING;}
        goto scalar_ready;
    }
    if (mode==RC07_RAW_LEAF && raw->kind==RC07_NONE) {status=rc07_node(p,RFT_NONE,0,0,&s->node);goto complete;}
    if (raw->kind==RC07_INTEGER_BYTES) {
        if (mode!=RC07_INT && mode!=RC07_RAW_LEAF) {job->semantic=RC07_C_LONG_NUMERIC;return R1_PENDING;}
        status=rc07_node(p,RFT_INT,0,raw->bytes,&s->node);if (status) return status;s->done=2;return R1_BUILDING;
    }
    if (raw->kind==RC07_F64) memcpy(&value,&raw->bits,8);
    else if (raw->kind==RC07_I64) value=(double)(int64_t)raw->bits;
    else if (raw->kind==RC07_BOOL && raw->bits<=1) value=(double)raw->bits;
    else if ((raw->kind==RC07_NONE || raw->kind==RC07_MISSING) && (s->mode&RC07_USE_DEFAULT)) memcpy(&value,&s->fallback,8);
    else if (mode==RC07_STRING) {const char* literal=raw->kind==RC07_NONE ? "None" : "";n=strlen(literal);memcpy(s->local,literal,(size_t)n);goto scalar_ready;}
    else {job->semantic=mode==RC07_INT ? RC07_C_BAD_INT : RC07_C_BAD_FLOAT;return R1_PENDING;}
    if (mode==RC07_STRING || mode==RC07_STRING_OR_EMPTY || mode==RC07_STRING_OR_ID) {
        if (raw->kind==RC07_F64) status=rc07_repr_float(value,s->local,&n);
        else if (raw->kind==RC07_BOOL) {const char* literal=raw->bits ? "True" : "False";n=strlen(literal);memcpy(s->local,literal,(size_t)n);status=0;}
        else {n=(uint64_t)snprintf((char*)s->local,256,"%lld",(long long)(int64_t)raw->bits);status=0;}
        if (status) return status;
    } else if (mode==RC07_INT) {
        if (raw->kind==RC07_I64 || raw->kind==RC07_BOOL) {n=8;for (int i=0;i<8;i++) bytes[i]=(uint8_t)(raw->bits>>(i*8));}
        else if (rc07_int_float(value,bytes,&n)) {job->semantic=RC07_C_BAD_INT;return R1_PENDING;}
    }
scalar_ready:
    if (mode==RC07_STRING || mode==RC07_STRING_OR_EMPTY || mode==RC07_STRING_OR_ID) {
        status=rc07_node(p,RFT_TEXT,0,n,&s->node);if (!status) memcpy(p->data+p->nodes[s->node].data_first,s->local,(size_t)n);
    } else if (mode==RC07_INT || (mode==RC07_RAW_LEAF && (raw->kind==RC07_I64 || raw->kind==RC07_BOOL))) {
        if (!n) {n=8;for (int i=0;i<8;i++) bytes[i]=(uint8_t)(raw->bits>>(i*8));}
        status=rc07_node(p,RFT_INT,0,n,&s->node);if (!status) memcpy(p->data+p->nodes[s->node].data_first,bytes,(size_t)n);
    } else {if (mode==RC07_FLOAT3) value=_ce_audio_decimal34(value,3);if (mode==RC07_FLOAT6) value=r6_decimal6(value);status=rc07_float(p,value,&s->node);}
complete:
    if (status) return status;s->done=1;s->active=0;return R1_OK;
}
static int rc07_scalar_one(rctx07_job* job)
{
    rctx07_scalar* s=&job->scalar;if (!s->active) return R1_INVALID;
    if (s->done!=2) return rc07_scalar_finish(job);
    uint64_t left=s->raw->bytes-s->cursor,n=left>32 ? 32 : left;
    if (n) memcpy(job->program->data+job->program->nodes[s->node].data_first+s->cursor,s->raw->data+s->cursor,(size_t)n);s->cursor+=n;
    if (s->cursor!=s->raw->bytes) return R1_BUILDING;s->active=0;s->done=1;return R1_OK;
}
