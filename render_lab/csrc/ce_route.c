/*
 * ce_route.c — Route Runner CE 覆盖内核 (ce_route_coverage)
 *
 * 本文件由 ce_engine.c 末尾 #include 文本合入同一编译单元（build.py 只编译
 * ce_engine.c + ce_pool.c, 不单独编译本文件）→ 依赖 ce_engine.c 顶部已包含的
 * 标准头 (windows.h/intrin.h/float.h/math.h/stdint.h/stdlib.h/string.h) 与
 * #define CE_API __declspec(dllexport), 以及 omp.h (当 /openmp 时)。
 *
 * 契约 (ai-memory/_audit_r1/_route_runner_spec.md §CE 内核契约 ce_route_coverage):
 *   预计算: ce_route_paths_set (每路径累积弧长) + ce_route_devices_project
 *     (每设备在每路径上的 (最近弧长 s, 横向距离 d) 投影);
 *   每帧: ce_route_frame — trains[] = {path_id, nose_s, car_unit, car_count, radius},
 *     输出 out_active[dev] uint8 强度数组 (按设备索引)。
 *
 * 语义铁律 (与探针内嵌 Python 参考逐字一致, 供 A/B 对拍, 见 ce_fuzz_route.py):
 *   车厢 i 覆盖弧区间 = [nose_s-(i+1)*car_unit, nose_s-i*car_unit] (闭区间);
 *   设备激活当 (投影 s 在任一区间内 且 d<=radius);
 *   强度 = max over cars/trains of max(255*(1-d/radius), 1); 无覆盖 = 0。
 *   守卫: radius<=0 或 car_count<=0 或 path_id 越界 → 该车不贡献 (强度 0)。
 *
 * 位级等价纪律:
 *   全程 double, 运算顺序与 Python float 逐表达式一致 (左结合);
 *   sqrt 走 IEEE 正确舍入 (sqrtsd) = Python math.sqrt; 累积弧长 cum[j+1]
 *   = cum[j] + sqrt(dx*dx+dy*dy) 严格左到右累加; 投影最近段判定用严格 <
 *   (d2 < best_d2) 保持「首段优先」, 与参考 np.where(better,…) 一致;
 *   /fp:precise (build.py) + #pragma fp_contract(off) 禁 FMA 收缩。
 *
 * 并行: 投影 (ce_route_devices_project) 与每帧查询 (ce_route_frame) 均
 *   OpenMP 按设备并行 (schedule(static)); 每设备结果完全独立 (无跨设备
 *   归约) → 位级等价不受线程数影响。
 */

#define CE_ROUTE_MAX_PATHS   32     /* 契约: n_paths<=32 */
#define CE_ROUTE_MAX_DEVICES 20000  /* 契约: n_dev<=20000 */

#pragma fp_contract(off)

typedef struct {
    int path_id;     /* 路径索引 [0, n_paths) */
    double nose_s;   /* 车头弧长位置 */
    double car_unit; /* 车长 (弧长单位) */
    int car_count;   /* 车厢数 */
    double radius;   /* 横向激活半径 */
} ce_route_train;

typedef struct {
    int n_paths;      /* 已设路径数 */
    int total_verts;  /* 全路径拼接顶点总数 */
    int n_dev;        /* 已投影设备数 (0 = 未投影) */
    double* vert_x;   /* [total_verts] 全路径拼接顶点 x */
    double* vert_y;   /* [total_verts] 全路径拼接顶点 y */
    int* path_off;    /* [n_paths+1] 每路径顶点前缀偏移 */
    double* cum;      /* [total_verts] 累积弧长 (每路径 cum[off]=0 起算) */
    double* s_dev;    /* [n_dev*n_paths] 每设备每路径最近弧长 s */
    double* d_dev;    /* [n_dev*n_paths] 每设备每路径横向距离 d */
} ce_route_ctx;

CE_API void* ce_route_ctx_create(void)
{
    return (void*)calloc(1, sizeof(ce_route_ctx));
}

CE_API void ce_route_ctx_destroy(void* vctx)
{
    ce_route_ctx* ctx = (ce_route_ctx*)vctx;
    if (!ctx) return;
    free(ctx->vert_x);
    free(ctx->vert_y);
    free(ctx->path_off);
    free(ctx->cum);
    free(ctx->s_dev);
    free(ctx->d_dev);
    free(ctx);
}

/* 释放路径相关存储并重置投影 (路径变化 → 投影失效 → 全 0 输出) */
static void ce_route_clear(ce_route_ctx* ctx)
{
    free(ctx->vert_x);   ctx->vert_x = NULL;
    free(ctx->vert_y);   ctx->vert_y = NULL;
    free(ctx->path_off); ctx->path_off = NULL;
    free(ctx->cum);      ctx->cum = NULL;
    free(ctx->s_dev);    ctx->s_dev = NULL;
    free(ctx->d_dev);    ctx->d_dev = NULL;
    ctx->n_paths = 0;
    ctx->total_verts = 0;
    ctx->n_dev = 0;
}

/* 输入: path_x/path_y = 全路径拼接顶点坐标 (长度 path_off[n_paths]),
 *       path_off = [n_paths+1] 前缀偏移 (path_off[0]=0)。
 * 输出: 每路径累积弧长 cum (cum[path_off[p]]=0, cum[off+k+1]=cum[off+k]+seg_len)。
 * 返回 0 = 成功; -1 = 参数/分配错误 (ctx 已重置为空)。 */
CE_API int ce_route_paths_set(void* vctx,
                              const double* path_x, const double* path_y,
                              const int* path_off, int n_paths)
{
    ce_route_ctx* ctx = (ce_route_ctx*)vctx;
    if (!ctx) return -1;
    if (n_paths < 0 || n_paths > CE_ROUTE_MAX_PATHS) return -1;
    ce_route_clear(ctx);
    if (n_paths == 0) return 0;
    if (path_x == NULL || path_y == NULL || path_off == NULL)
        return -1;
    int total = path_off[n_paths];
    if (total < 0) return -1;
    if (total > 0) {
        ctx->vert_x = (double*)malloc((size_t)total * sizeof(double));
        ctx->vert_y = (double*)malloc((size_t)total * sizeof(double));
        ctx->cum = (double*)malloc((size_t)total * sizeof(double));
        if (!ctx->vert_x || !ctx->vert_y || !ctx->cum) {
            ce_route_clear(ctx);
            return -1;
        }
        memcpy(ctx->vert_x, path_x, (size_t)total * sizeof(double));
        memcpy(ctx->vert_y, path_y, (size_t)total * sizeof(double));
    }
    ctx->path_off = (int*)malloc((size_t)(n_paths + 1) * sizeof(int));
    if (!ctx->path_off) {
        ce_route_clear(ctx);
        return -1;
    }
    memcpy(ctx->path_off, path_off, (size_t)(n_paths + 1) * sizeof(int));
    ctx->n_paths = n_paths;
    ctx->total_verts = total;

    for (int p = 0; p < n_paths; p++) {
        int a = path_off[p];
        int b = path_off[p + 1];
        if (a < 0 || b < a || b > total) {
            ce_route_clear(ctx);
            return -1;
        }
        if (a < total)
            ctx->cum[a] = 0.0;
        for (int j = a; j + 1 < b; j++) {
            double dx = ctx->vert_x[j + 1] - ctx->vert_x[j];
            double dy = ctx->vert_y[j + 1] - ctx->vert_y[j];
            ctx->cum[j + 1] = ctx->cum[j] + sqrt(dx * dx + dy * dy);
        }
    }
    return 0;
}

/* 对每设备、每路径求最近弧长 s 与横向距离 d (点到折线最近点投影)。
 * 算法 (与参考逐表达式一致): 逐段把点投影到段, 参数 t 夹 [0,1];
 *   d2 = |P-Q|^2 取严格最小; s = cum[段起点] + t*sqrt(seg_len_sq)。
 * 空路径 → s=0,d=+inf; 单点路径 → s=0,d=|P-V|。
 * 点到线段最近点公式 (t = dot(P-A, B-A)/|B-A|^2, 夹 [0,1], 垂距 |P-Q|) 参照
 * distance3d (BSD-3-Clause, https://github.com/AlexanderFabisch/distance3d)
 * 的 segment-point 线性参考实现; 本项目克隆 (ai-memory/_downloads/distance3d)
 * 工作树为空/损坏, 此处按同一标准公式独立实现, 表达式逐项一致。
 * OpenMP 按设备并行 (每设备独立)。返回 0 = 成功; -1 = 参数/分配错误。 */
CE_API int ce_route_devices_project(void* vctx,
                                    const double* dev_x, const double* dev_y,
                                    int n_dev)
{
    ce_route_ctx* ctx = (ce_route_ctx*)vctx;
    if (!ctx) return -1;
    free(ctx->s_dev);   ctx->s_dev = NULL;
    free(ctx->d_dev);   ctx->d_dev = NULL;
    ctx->n_dev = 0;
    if (n_dev < 0 || n_dev > CE_ROUTE_MAX_DEVICES) return -1;
    if (n_dev == 0) return 0;
    if (dev_x == NULL || dev_y == NULL) return -1;
    int n_paths = ctx->n_paths;
    size_t stride = (size_t)(n_paths > 0 ? n_paths : 1);
    ctx->s_dev = (double*)malloc((size_t)n_dev * stride * sizeof(double));
    ctx->d_dev = (double*)malloc((size_t)n_dev * stride * sizeof(double));
    if (!ctx->s_dev || !ctx->d_dev) {
        free(ctx->s_dev);   ctx->s_dev = NULL;
        free(ctx->d_dev);   ctx->d_dev = NULL;
        return -1;
    }
    ctx->n_dev = n_dev;
    if (n_paths == 0) {
        for (size_t i = 0; i < (size_t)n_dev * stride; i++) {
            ctx->s_dev[i] = 0.0;
            ctx->d_dev[i] = INFINITY;
        }
        return 0;
    }
    int d;
    #pragma omp parallel for schedule(static)
    for (d = 0; d < n_dev; d++) {
        double px = dev_x[d];
        double py = dev_y[d];
        double* sd = ctx->s_dev + (size_t)d * n_paths;
        double* dd = ctx->d_dev + (size_t)d * n_paths;
        for (int p = 0; p < n_paths; p++) {
            int a = ctx->path_off[p];
            int b = ctx->path_off[p + 1];
            if (b <= a) {
                sd[p] = 0.0;
                dd[p] = INFINITY;
                continue;
            }
            if (b == a + 1) {
                double ex = px - ctx->vert_x[a];
                double ey = py - ctx->vert_y[a];
                sd[p] = 0.0;
                dd[p] = sqrt(ex * ex + ey * ey);
                continue;
            }
            double best_d2 = INFINITY;
            double best_s = 0.0;
            for (int j = a; j + 1 < b; j++) {
                double ax = ctx->vert_x[j];
                double ay = ctx->vert_y[j];
                double bx = ctx->vert_x[j + 1];
                double by = ctx->vert_y[j + 1];
                double dx = bx - ax;
                double dy = by - ay;
                double seg_len_sq = dx * dx + dy * dy;
                double t;
                if (seg_len_sq == 0.0) {
                    t = 0.0;
                } else {
                    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq;
                    if (t < 0.0) t = 0.0;
                    else if (t > 1.0) t = 1.0;
                }
                double qx = ax + t * dx;
                double qy = ay + t * dy;
                double ex = px - qx;
                double ey = py - qy;
                double d2 = ex * ex + ey * ey;
                if (d2 < best_d2) {
                    best_d2 = d2;
                    double seg_len = sqrt(seg_len_sq);
                    best_s = ctx->cum[j] + t * seg_len;
                }
            }
            sd[p] = best_s;
            dd[p] = sqrt(best_d2);
        }
    }
    return 0;
}

/* 只读回读投影 (供 A/B 对拍直接校验 s/d): 把 ctx 内部 s_dev/d_dev 拷贝到
 * 调用方缓冲。输出长度 = n_dev * max(1, n_paths) (n_paths==0 时 stride=1)。
 * 返回 投影设备数 (>=0); -1 = 参数/状态错误。 */
CE_API int ce_route_devices_get(void* vctx, double* s_out, double* d_out)
{
    ce_route_ctx* ctx = (ce_route_ctx*)vctx;
    if (!ctx || s_out == NULL || d_out == NULL) return -1;
    int n_dev = ctx->n_dev;
    if (n_dev <= 0) return 0;
    size_t stride = (size_t)(ctx->n_paths > 0 ? ctx->n_paths : 1);
    if (ctx->s_dev == NULL || ctx->d_dev == NULL) return -1;
    memcpy(s_out, ctx->s_dev, (size_t)n_dev * stride * sizeof(double));
    memcpy(d_out, ctx->d_dev, (size_t)n_dev * stride * sizeof(double));
    return n_dev;
}

/* 每帧一次调用: 对每设备按 trains 计算强度 (uint8)。
 * 车厢 i 覆盖区间 [nose_s-(i+1)*car_unit, nose_s-i*car_unit]; 设备在该路径的
 * (s,d) 满足 s∈区间 且 d<=radius → 强度 = max(255*(1-d/radius), 1); 取 max over
 * cars/trains; 无覆盖 = 0。OpenMP 按设备并行 (每设备独立)。返回 0 = 成功。 */
CE_API int ce_route_frame(void* vctx, const ce_route_train* trains,
                          int n_train, uint8_t* out_active)
{
    ce_route_ctx* ctx = (ce_route_ctx*)vctx;
    if (!ctx || out_active == NULL) return -1;
    int n_dev = ctx->n_dev;
    if (n_dev <= 0) return 0;
    int n_paths = ctx->n_paths;
    if (n_train < 0) n_train = 0;
    int have = (trains != NULL && n_train > 0 && n_paths > 0) ? 1 : 0;
    int d;
    /* 每帧查询串行 + /O2 /arch:AVX2 自动 SIMD (设备维): 实测 OpenMP 区域进入
     * 开销 ~5ms (ACTIVE 等待策略 + 16 线程) 远超本内核 <0.5ms 预算与单次
     * 11382×8 计算量 (~0.1ms) → 每帧不加 OpenMP (「合理向量化」= SIMD)。 */
    for (d = 0; d < n_dev; d++) {
        double best = 0.0;
        if (have) {
            const double* sd = ctx->s_dev + (size_t)d * n_paths;
            const double* dd = ctx->d_dev + (size_t)d * n_paths;
            for (int t = 0; t < n_train; t++) {
                int p = trains[t].path_id;
                if (p < 0 || p >= n_paths) continue;
                double radius = trains[t].radius;
                if (radius <= 0.0) continue;
                double s = sd[p];
                double dist = dd[p];
                for (int i = 0; i < trains[t].car_count; i++) {
                    double lo = trains[t].nose_s - (double)(i + 1) * trains[t].car_unit;
                    double hi = trains[t].nose_s - (double)i * trains[t].car_unit;
                    if (dist <= radius && s >= lo && s <= hi) {
                        double val = 255.0 * (1.0 - dist / radius);
                        if (val < 1.0) val = 1.0;
                        if (val > best) best = val;
                    }
                }
            }
        }
        out_active[d] = (uint8_t)best;
    }
    return 0;
}
