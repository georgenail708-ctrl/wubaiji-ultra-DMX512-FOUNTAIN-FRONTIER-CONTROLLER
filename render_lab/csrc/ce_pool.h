/*
 * ce_pool.h — 阶段B condvar 常驻线程池契约（COMPUTE_UTILIZATION_PLAN.md §4.3 冻结）
 * 实现见 ce_pool.c; 供 ce_engine.c 与 ctypes 调用方共享声明。
 */
#ifndef CE_POOL_H
#define CE_POOL_H

#ifndef CE_API
#define CE_API __declspec(dllexport)
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ce_pool ce_pool;
typedef void (*ce_task_fn)(void* arg, int thread_id);

CE_API ce_pool* ce_pool_create(int n_threads);                    /* 常驻; n_threads<=0 → 物理核数-1, cap 16 */
CE_API void     ce_pool_destroy(ce_pool* p);                      /* 等全部任务完成 */
CE_API int      ce_pool_submit(ce_pool* p, ce_task_fn fn, void* arg);        /* 入队即返 */
CE_API int      ce_pool_submit_batch(ce_pool* p, const ce_task_fn* fns,
                                     void* const* args, int n);   /* 批量入队(推荐), 信号一次 */
CE_API void     ce_pool_wait(ce_pool* p);                         /* 等队列空+全部完成(条件变量) */
CE_API int      ce_pool_selftest(void);                           /* 4*threads 任务自检: 每任务恰执行一次 */

#ifdef __cplusplus
}
#endif

#endif /* CE_POOL_H */
