/* Bounded owning equivalent of existing ce_source_geometry_fit. Reuses its
 * exact metric/hypot kernels; ordered moments and compensated Python3.13 sum
 * state execute one point per unit, without malloc hidden in helper kernels. */
typedef struct {double total,correction;uint64_t count;} r6_sum;

static void r6_sum_add(r6_sum* sum,double value)
{
    if (!sum->count++) {sum->total=0.+value;return;}
    double next=sum->total+value;
    if (fabs(sum->total)>=fabs(value)) sum->correction+=(sum->total-next)+value;
    else sum->correction+=(value-next)+sum->total;sum->total=next;
}

static double r6_sum_finish(const r6_sum* sum)
{
    double result=sum->total;if (sum->correction!=0. && isfinite(sum->correction)) result+=sum->correction;return result;
}

typedef struct {
    const double* xy;uint64_t n;const r6_shapes* shapes;uint32_t prefer_shapes;
} r6_circle_fit_call;

typedef struct {
    r6_circle_fit_call call;uint64_t stage,row,shape,matched,best_count;
    uint32_t best_valid,available,cancelled;double best_average,tolerance,reference[5];
    double moments[9],matrix[3][4],radius; r6_sum sum;
} r6_circle_fit;

static int r6_circle_fit_create(r6_circle_fit* out,const r6_circle_fit_call* call)
{
    if (!out || !call || (call->n && !call->xy) || (call->shapes && call->shapes->stage!=3)) return R1_INVALID;
    memset(out,0,sizeof(*out));out->call=*call;out->best_average=999999999.;
    if (call->n<3) out->stage=5;
    else if (!call->prefer_shapes || !call->shapes || !call->shapes->count) out->stage=1;
    return R1_OK;
}

static int r6_circle_shape_one(r6_circle_fit* out)
{
    if (out->shape==out->call.shapes->count) {
        out->row=0;out->stage=out->best_valid ? 5 : 1;out->available=out->best_valid;memset(&out->sum,0,sizeof(out->sum));return R1_OK;
    }
    const double* shape=out->call.shapes->values+5*out->shape;
    double tolerance=fmax(24.,((shape[2]+shape[3])*.5)*.22);
    if (out->row<out->call.n) {
        double metric[2];_ce_geo_metric(shape,out->call.xy+2*out->row++,metric);
        if (metric[0]<=tolerance) {r6_sum_add(&out->sum,metric[0]);out->matched++;}return R1_OK;
    }
    double threshold=fmax(3.,ceil((double)out->call.n*.45));
    if ((double)out->matched>=threshold) {
        double average=r6_sum_finish(&out->sum)/(double)out->matched;
        if (!out->best_valid || out->matched>out->best_count || (out->matched==out->best_count && -average>-out->best_average)) {
            out->best_valid=1;out->best_count=out->matched;out->best_average=average;memcpy(out->reference,shape,5*sizeof(double));
        }
    }
    out->shape++;out->row=out->matched=0;memset(&out->sum,0,sizeof(out->sum));return R1_OK;
}

static int r6_circle_moment_one(r6_circle_fit* out)
{
    if (out->row==out->call.n) {
        double* m=out->moments;
        double matrix[3][4]={{m[2],m[4],m[0],-(m[5]+m[8])},{m[4],m[3],m[1],-(m[7]+m[6])},
            {m[0],m[1],(double)out->call.n,-(m[2]+m[3])}};
        memcpy(out->matrix,matrix,sizeof(matrix));out->stage=2;out->row=0;return R1_OK;
    }
    const double* xy=out->call.xy+2*out->row++;double x=xy[0],y=xy[1],x2=x*x,y2=y*y;double* m=out->moments;
    if (!isfinite(x) || !isfinite(y)) return R1_PENDING;
    m[0]+=x;m[1]+=y;m[2]+=x2;m[3]+=y2;m[4]+=x*y;m[5]+=x2*x;m[6]+=y2*y;m[7]+=x2*y;m[8]+=x*y2;return R1_OK;
}

static int r6_circle_eliminate_one(r6_circle_fit* out)
{
    int col=(int)out->row,pivot=col;double (*matrix)[4]=out->matrix;
    for (int r=col+1;r<3;r++) if (fabs(matrix[r][col])>fabs(matrix[pivot][col])) pivot=r;
    if (fabs(matrix[pivot][col])<1e-9) {out->stage=5;return R1_OK;}
    if (pivot!=col) for (int j=0;j<4;j++) {double value=matrix[col][j];matrix[col][j]=matrix[pivot][j];matrix[pivot][j]=value;}
    double divisor=matrix[col][col];for (int j=col;j<4;j++) matrix[col][j]/=divisor;
    for (int r=0;r<3;r++) {
        if (r==col) continue;double factor=matrix[r][col];if (fabs(factor)<=1e-12) continue;
        for (int j=col;j<4;j++) matrix[r][j]-=factor*matrix[col][j];
    }
    if (++out->row==3) {out->stage=3;out->row=0;}return R1_OK;
}

static int r6_circle_radius(r6_circle_fit* out)
{
    double cx=-out->matrix[0][3]/2.,cy=-out->matrix[1][3]/2.;
    double square=cx*cx+cy*cy-out->matrix[2][3];
    if (square<=1.) {out->stage=5;return R1_OK;}
    out->radius=sqrt(square);out->reference[0]=cx;out->reference[1]=cy;
    out->reference[2]=out->reference[3]=out->radius;out->reference[4]=0.;out->stage=4;return R1_OK;
}

static int r6_circle_error_one(r6_circle_fit* out)
{
    if (out->row==out->call.n) {
        double rms=sqrt(r6_sum_finish(&out->sum)/(double)out->call.n);
        out->available=!(rms>fmax(24.,out->radius*.22));out->stage=5;return R1_OK;
    }
    const double* xy=out->call.xy+2*out->row++;
    double error=fabs(ce_py_hypot2(xy[0]-out->reference[0],xy[1]-out->reference[1])-out->radius);
    r6_sum_add(&out->sum,error*error);return R1_OK;
}

static int r6_circle_fit_step(r6_circle_fit* out,uint32_t units)
{
    if (!out || !units || units>65536) return R1_INVALID;if (out->cancelled) return R1_CANCELLED;
    int status=R1_OK;
    while (units-- && out->stage!=5 && !status) switch(out->stage) {
    case 0:status=r6_circle_shape_one(out);break;case 1:status=r6_circle_moment_one(out);break;
    case 2:status=r6_circle_eliminate_one(out);break;case 3:status=r6_circle_radius(out);break;
    case 4:status=r6_circle_error_one(out);break;default:return R1_INVALID;
    }
    return status ? status : out->stage==5 ? R1_OK : R1_BUILDING;
}
