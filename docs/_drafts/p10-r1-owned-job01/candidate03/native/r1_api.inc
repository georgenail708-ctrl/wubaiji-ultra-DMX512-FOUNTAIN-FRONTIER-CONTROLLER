CE_API int ce_owned_job_abi_r1(void) {return R1_ABI;}
CE_API int ce_owned_job_size_r1(int selector)
{
    switch(selector) {
    case 0:return sizeof(r1_atom);case 1:return sizeof(r1_raw_frame);
    case 2:return sizeof(r1_channel);case 3:return sizeof(r1_input);
    case 4:return sizeof(r1_report);case 5:return sizeof(r1_step);
    case 6:return sizeof(r1_block);case 7:return sizeof(r1_raw_domain);
    case 8:return sizeof(r1_taken);case 9:return sizeof(r1_raw_state);
    case 10:return sizeof(r1_state_domain);case 11:return sizeof(r1_state);default:return -1;
    }
}

static r1_job* r1_new_job(const r1_input* input)
{
    r1_ledger* ledger;
    r1_job* job;
    if (input->byte_budget<=sizeof(r1_ledger)+sizeof(r1_job)+R1_KERNEL_RESERVE) return NULL;
    ledger=(r1_ledger*)calloc(1,sizeof(*ledger));
    if (!ledger) return NULL;
    ledger->limit=input->byte_budget-R1_KERNEL_RESERVE;
    ledger->used=ledger->peak=sizeof(*ledger); ledger->refs=1;
    job=(r1_job*)r1_allocate(ledger,sizeof(*job));
    if (!job) {free(ledger);return NULL;}
    job->ledger=ledger; job->source_token=input->source_token;
    return job;
}

CE_API int ce_owned_job_begin_r1(const r1_input* input, r1_job** output, r1_report* report)
{
    double values[R1_ATOMS]={0};
    r1_job* job;
    int status;
    if (!output || !report) return R1_INVALID;
    *output=NULL; status=r1_gate(input,values,report);
    if (status) {report->status=status;return status;}
    job=r1_new_job(input);
    if (!job) {report->status=R1_BUDGET;return R1_BUDGET;}
    status=r1_clock_plan(job,values);
    if (!status) status=r1_state_create(job,input->state);
    if (!status) status=r1_compile_ingest(job,input);
    r1_report_current(job,report,status);
    if (status) {ce_owned_job_destroy_r1(job);return status;}
    *output=job; report->status=R1_BUILDING;return R1_BUILDING;
}

CE_API int ce_owned_job_compile_r1(r1_job* job, uint32_t units, r1_report* report)
{
    int status;
    if (!job || !report) return R1_INVALID;
    status=r1_compile_advance(job,units);r1_report_current(job,report,status);
    return status;
}

/* Retained research facade compatibility only. New scene API uses begin +
 * compile steps; this synchronous entry is not the proposed product route. */
CE_API int ce_owned_job_create_r1(const r1_input* input, r1_job** output, r1_report* report)
{
    int status=ce_owned_job_begin_r1(input,output,report);
    if (status!=R1_BUILDING) return status;
    do {status=ce_owned_job_compile_r1(*output,65536,report);} while (status==R1_BUILDING);
    if (status) {ce_owned_job_destroy_r1(*output);*output=NULL;}
    return status;
}

CE_API int ce_owned_job_step_r1(r1_job* job, const r1_step* request,
                                r1_block** output, r1_report* report)
{
    r1_block* block;
    int status;
    if (!output || !report || !job || !request) return R1_INVALID;
    *output=NULL;
    if (request->struct_size!=sizeof(*request) || !request->max_frames || request->max_frames>256) return R1_INVALID;
    if (job->terminal_error) {r1_report_current(job,report,job->terminal_error);return job->terminal_error;}
    if (job->cancelled) {r1_report_current(job,report,R1_CANCELLED);return R1_CANCELLED;}
    if (job->compile_phase!=5) {r1_report_current(job,report,R1_BUILDING);return R1_BUILDING;}
    if (job->cursor==job->total_frames) {r1_report_current(job,report,R1_DONE);return R1_DONE;}
    block=r1_new_block(job,request);
    if (!block) {r1_report_current(job,report,R1_BUDGET);return R1_BUDGET;}
    status=r1_render_block(job,block);
    if (status) {
        if (job->cursor!=block->first_index) job->terminal_error=status;
        ce_owned_job_release_block_r1(block);r1_report_current(job,report,status);return status;
    }
    *output=block; r1_report_current(job,report,R1_OK);return R1_OK;
}

CE_API int ce_owned_job_cancel_r1(r1_job* job, r1_report* report)
{
    if (!job || !report) return R1_INVALID;
    job->cancelled=1; r1_report_current(job,report,R1_CANCELLED);
    return R1_CANCELLED;
}
