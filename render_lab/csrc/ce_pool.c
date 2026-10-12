/*
 * Qascade CE — 阶段B: condvar 常驻线程池（COMPUTE_UTILIZATION_PLAN.md §4.3）
 * 背景: P8 spin/事件池实测负收益（忙等烧 CPU, 帧 17→32ms, iter#17）→
 * 本池全 kernel 等待: SRWLOCK + SleepConditionVariableSRW/WakeAllConditionVariable,
 * worker 与提交者路径绝无 _mm_pause/Sleep(0)/忙等。
 * 队列 = 固定容量环形（256 槽）, SRW 锁保护（仅入队/出队短临界区）;
 * 提交满则阻塞等空位; 批量提交全部入队后一次唤醒 worker（信号一次）。
 * 提交者不参与执行（纯生产者）: 契约 submit 入队即返 + wait 单独等待;
 * 实时路径线程数默认 = 核数-1, 预留一核给 UI/网络/GPU 提交线程。
 * 线程安全: 支持多批次提交后统一 wait（帧批量形态）。
 *
 * 构建: render_lab/qascade_ce/build.py (MSVC cl.exe 双变体)
 */
#include <windows.h>
#include <stdlib.h>

#define CE_API __declspec(dllexport)
#include "ce_pool.h"

#define CE_POOL_RING_CAP    256   /* 环形队列容量（任务槽） */
#define CE_POOL_MAX_THREADS 16    /* 线程数上限（契约: 自动档 cap 16） */

typedef struct {
    ce_task_fn fn;
    void* arg;
} ce_pool_slot;

typedef struct {
    ce_pool* pool;
    int idx;                     /* worker 下标 = 其 thread_id */
} ce_pool_worker_arg;

struct ce_pool {
    SRWLOCK lock;
    CONDITION_VARIABLE cv_task;    /* 队列非空（worker 等待取任务） */
    CONDITION_VARIABLE cv_space;   /* 队列有空位（提交者等待入队） */
    CONDITION_VARIABLE cv_done;    /* 队列空且无在途（wait 等待） */
    ce_pool_slot ring[CE_POOL_RING_CAP];
    unsigned head;                 /* 队首（worker 消费, 锁保护） */
    unsigned tail;                 /* 队尾（提交者生产, 锁保护） */
    unsigned count;                /* 队内任务数（锁保护） */
    unsigned in_flight;            /* 在途执行任务数（锁保护） */
    int n_threads;
    int stop;
    HANDLE* threads;
    ce_pool_worker_arg* wargs;
};

/* worker 主循环: 条件变量等任务 → 取出队首 → 锁外执行 fn(arg, tid)
 * → 记账, 且仅在「队空且无在途」时唤醒 cv_done 等待者。
 * stop 置位后仍清空剩余队列（destroy 契约: 等全部任务完成）再退出。 */
static DWORD WINAPI _ce_pool_worker(LPVOID param)
{
    ce_pool_worker_arg* wa = (ce_pool_worker_arg*)param;
    ce_pool* p = wa->pool;
    int tid = wa->idx;
    int wake_space = 0;
    int wake_done = 0;
    for (;;) {
        ce_pool_slot s;
        int quit = 0;
        AcquireSRWLockExclusive(&p->lock);
        while (p->count == 0 && !p->stop) {
            SleepConditionVariableSRW(&p->cv_task, &p->lock, INFINITE, 0);
        }
        if (p->count > 0) {
            s = p->ring[p->head];
            p->head = (p->head + 1) % CE_POOL_RING_CAP;
            p->count--;
            p->in_flight++;
            /* 每次出队都唤醒等待空位的提交者（单次提交仅满→非满有
             * 意义, 批量提交中途出队同样推进 → 不可漏; 无等待者时
             * WakeConditionVariable 为进程内空操作, 零系统调用） */
            wake_space = 1;
        } else {
            quit = 1;                    /* 队空且 stop: 退出 */
        }
        ReleaseSRWLockExclusive(&p->lock);
        if (quit) break;
        if (wake_space) { WakeConditionVariable(&p->cv_space); wake_space = 0; }
        s.fn(s.arg, tid);                /* 锁外执行, 不阻塞其他 worker 取任务 */
        AcquireSRWLockExclusive(&p->lock);
        p->in_flight--;
        if (p->count == 0 && p->in_flight == 0)
            wake_done = 1;
        ReleaseSRWLockExclusive(&p->lock);
        if (wake_done) { WakeAllConditionVariable(&p->cv_done); wake_done = 0; }
    }
    return 0;
}

CE_API ce_pool* ce_pool_create(int n_threads)
{
    if (n_threads <= 0) {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        n_threads = (int)si.dwNumberOfProcessors - 1;
    }
    if (n_threads < 1) n_threads = 1;
    if (n_threads > CE_POOL_MAX_THREADS) n_threads = CE_POOL_MAX_THREADS;
    ce_pool* p = (ce_pool*)calloc(1, sizeof(ce_pool));
    if (p == NULL) return NULL;
    InitializeSRWLock(&p->lock);
    InitializeConditionVariable(&p->cv_task);
    InitializeConditionVariable(&p->cv_space);
    InitializeConditionVariable(&p->cv_done);
    p->wargs = (ce_pool_worker_arg*)calloc((size_t)n_threads,
                                           sizeof(ce_pool_worker_arg));
    p->threads = (HANDLE*)calloc((size_t)n_threads, sizeof(HANDLE));
    if (p->wargs == NULL || p->threads == NULL) goto fail;
    p->n_threads = n_threads;
    for (int i = 0; i < n_threads; i++) {
        p->wargs[i].pool = p;
        p->wargs[i].idx = i;
        p->threads[i] = CreateThread(NULL, 0, _ce_pool_worker, &p->wargs[i],
                                     0, NULL);
        if (p->threads[i] == NULL) goto fail;
    }
    return p;
fail:
    ce_pool_destroy(p);
    return NULL;
}

CE_API void ce_pool_destroy(ce_pool* p)
{
    if (p == NULL) return;
    AcquireSRWLockExclusive(&p->lock);
    p->stop = 1;
    ReleaseSRWLockExclusive(&p->lock);
    WakeAllConditionVariable(&p->cv_task);   /* 唤醒阻塞 worker → 清队 → 退出 */
    for (int i = 0; i < p->n_threads; i++) {
        if (p->threads != NULL && p->threads[i] != NULL) {
            WaitForSingleObject(p->threads[i], INFINITE);   /* 等全部任务完成 */
            CloseHandle(p->threads[i]);
        }
    }
    free(p->threads);
    free(p->wargs);
    free(p);
}

CE_API int ce_pool_submit(ce_pool* p, ce_task_fn fn, void* arg)
{
    if (p == NULL || fn == NULL) return -1;
    AcquireSRWLockExclusive(&p->lock);
    while (p->count == CE_POOL_RING_CAP && !p->stop) {
        SleepConditionVariableSRW(&p->cv_space, &p->lock, INFINITE, 0);
    }
    if (p->stop) { ReleaseSRWLockExclusive(&p->lock); return -1; }
    p->ring[p->tail].fn = fn;
    p->ring[p->tail].arg = arg;
    p->tail = (p->tail + 1) % CE_POOL_RING_CAP;
    p->count++;
    ReleaseSRWLockExclusive(&p->lock);
    WakeConditionVariable(&p->cv_task);      /* 单任务: 唤醒一个 worker */
    return 0;
}

CE_API int ce_pool_submit_batch(ce_pool* p, const ce_task_fn* fns,
                                void* const* args, int n)
{
    if (p == NULL) return 0;
    if (fns == NULL || n <= 0) return 0;
    if (n > CE_POOL_RING_CAP) return -1;   /* 单批超容量: 调用方自行分批/回退 */
    AcquireSRWLockExclusive(&p->lock);
    while (CE_POOL_RING_CAP - p->count < (unsigned)n && !p->stop) {
        SleepConditionVariableSRW(&p->cv_space, &p->lock, INFINITE, 0);
    }
    if (p->stop) { ReleaseSRWLockExclusive(&p->lock); return -1; }
    for (int i = 0; i < n; i++) {
        p->ring[p->tail].fn = fns[i];
        p->ring[p->tail].arg = args[i];
        p->tail = (p->tail + 1) % CE_POOL_RING_CAP;
    }
    p->count += (unsigned)n;
    ReleaseSRWLockExclusive(&p->lock);
    WakeAllConditionVariable(&p->cv_task);   /* 全部入队后信号一次 */
    return 0;
}

CE_API void ce_pool_wait(ce_pool* p)
{
    if (p == NULL) return;
    AcquireSRWLockExclusive(&p->lock);
    while (p->count > 0 || p->in_flight > 0) {
        SleepConditionVariableSRW(&p->cv_done, &p->lock, INFINITE, 0);
    }
    ReleaseSRWLockExclusive(&p->lock);
}

/* 自检: 4*threads 个任务批量提交 + 2 个单提交, 每任务恰好执行一次
 * 且 thread_id 合法（0..n_threads-1）; 返回 1 = 通过 */
typedef struct {
    volatile LONG runs;      /* 执行次数（应恰为 1） */
    volatile LONG bad_tid;   /* thread_id 越界计数 */
    int n_threads;
} ce_pool_selftest_state;

static void _ce_pool_selftest_task(void* arg, int thread_id)
{
    ce_pool_selftest_state* st = (ce_pool_selftest_state*)arg;
    if (thread_id < 0 || thread_id >= st->n_threads)
        InterlockedIncrement(&st->bad_tid);
    InterlockedIncrement(&st->runs);
}

CE_API int ce_pool_selftest(void)
{
    ce_pool* p = ce_pool_create(0);
    if (p == NULL) return 0;
    int n_threads = p->n_threads;
    int n_batch = 4 * n_threads;
    int n_single = 2;
    int n_tasks = n_batch + n_single;
    int ok = 0;
    ce_pool_selftest_state* states = (ce_pool_selftest_state*)calloc(
        (size_t)n_tasks, sizeof(ce_pool_selftest_state));
    ce_task_fn* fns = (ce_task_fn*)calloc((size_t)n_tasks, sizeof(ce_task_fn));
    void** args = (void**)calloc((size_t)n_tasks, sizeof(void*));
    if (states != NULL && fns != NULL && args != NULL) {
        for (int i = 0; i < n_tasks; i++) {
            states[i].runs = 0;
            states[i].bad_tid = 0;
            states[i].n_threads = n_threads;
            fns[i] = _ce_pool_selftest_task;
            args[i] = &states[i];
        }
        ok = (ce_pool_submit_batch(p, fns, args, n_batch) == 0);
        if (ok) {
            ok = (ce_pool_submit(p, fns[n_batch], args[n_batch]) == 0)
              && (ce_pool_submit(p, fns[n_batch + 1], args[n_batch + 1]) == 0);
        }
        if (ok) ce_pool_wait(p);
        for (int i = 0; i < n_tasks; i++) {
            if (states[i].runs != 1 || states[i].bad_tid != 0) ok = 0;
        }
    }
    free(args);
    free(fns);
    free(states);
    ce_pool_destroy(p);
    return ok ? 1 : 0;
}
