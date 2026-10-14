/* One whole constructor/create/legacy-add numerical producer. */
static int rr_create_count(const rr_create_request* input,rr_create_report* output)
{
    if (!input->count_bytes || !input->n_count) return -1;
    const unsigned char* bytes = input->count_bytes;
    int negative = (bytes[input->n_count-1]&128) != 0;
    output->count_selector = negative ? 0 : 1;
    output->count_fits = negative || input->n_count <= 4;
    uint32_t value = 0;
    if (!negative && output->count_fits) {
        for (size_t j=0;j<input->n_count;j++) value |= (uint32_t)bytes[j]<<(8*j);
        if (value > INT_MAX) output->count_fits = 0;
    }
    output->edit.train.car_count = output->count_fits && !negative ? (int)value : 0;
    return 0;
}
static int rr_create_initialize(const rr_create_request* input,rr_create_report* output)
{
    memset(output,0,sizeof(*output));
    rr_train* train = &output->edit.train;
    train->id=input->id; train->color=input->color;
    train->base_speed=input->base_speed; train->car_length=input->car_length;
    if (input->operation == RR_CREATE_LEGACY || input->operation == RR_CREATE_PARTIAL) {
        train->car_length=input->car_unit > 0.0 ? input->car_unit : 0.0;
        train->base_speed=input->car_unit > 0.0 ? input->base_speed*input->car_unit : input->base_speed;
    }
    if (input->operation == RR_CREATE_PANEL) {
        train->car_length=input->car_unit;
        train->radius=input->car_unit*0.6;
    } else train->radius=input->radius;
    train->chain_length=train->car_length;
    train->throttle=train->target=train->ramp_from=train->dir_sign=1.0;
    train->mode=0; train->easing=105; /* original loop_forward / OutCubic */
    train->held_node=train->home_edge=train->span_edge=-1;
    train->input_index=train->parent_index=-1; train->alive=1;
    output->edit.has_train=1; output->edit.dirty=input->operation != RR_CREATE_DIRECT;
    output->edit.sync_edge=-1;
    output->phase=input->operation == RR_CREATE_PARTIAL ? 1 : 0;
    return rr_create_count(input,output);
}
static int rr_create_admission(const rr_create_request* input)
{
    if (!input || input->operation > RR_CREATE_PANEL || !input->id || !input->color
        || !input->count_bytes || !input->n_count || input->n_points < 0
        || (input->n_points && !input->points) || input->anticipated_attach > 1
        || input->anticipated_points < 0) return -1;
    size_t bytes=input->host_bytes;
    if (!rr_add_size(&bytes,input->n_count,2) || !rr_add_size(&bytes,input->n_points,512)) return RR_BUDGET_REFUSED;
    if (input->operation == RR_CREATE_LEGACY) {
        for (int j=0;j<input->n_points;j++)
            if (!isfinite(input->points[j].x) || !isfinite(input->points[j].y)) return -1;
    }
    return 0;
}
static int rr_create_host_size(const rr_create_request* input,size_t* bytes)
{
    rr_create_report initial;
    int rc=rr_create_initialize(input,&initial); if (rc) return rc;
    *bytes=input->host_bytes;
    if (!rr_add_size(bytes,input->n_count,2) || !rr_add_size(bytes,input->n_points,1024)
        || !rr_add_size(bytes,input->anticipated_points,1024)) return RR_BUDGET_REFUSED;
    int attached=(input->operation == RR_CREATE_LEGACY && input->n_points) || input->anticipated_attach;
    if (!attached) return 0;
    if (!initial.count_fits || !rr_add_size(bytes,initial.edit.train.car_count,1024)) return RR_BUDGET_REFUSED;
    return 0;
}
static int rr_create_attach(rr_create_report* output,const rr_create_request* input)
{
    if (!output->count_fits) return RR_BUDGET_REFUSED;
    size_t bytes=input->host_bytes;
    if (!rr_add_size(&bytes,output->edit.train.car_count,512)) return RR_BUDGET_REFUSED;
    rr_edit_request attach={0}; attach.target_edge=attach.legacy_edge=0;
    attach.target_found=1; attach.target_length=output->edit.geometry.edge.length;
    return rr_edit_attach(&output->edit,&attach,0,0.0);
}
static int rr_create_calculate(rr_create_report* output,const rr_create_request* input)
{
    rr_create_report initialized;
    int rc=rr_create_initialize(input,&initialized); if (rc) return rc;
    *output=initialized;
    /* All retained report pointers are owned before any failure can clear it. */
    output->edit.train.id=NULL; output->edit.train.color=NULL;
    output->edit.train.id=rr_string(input->id); output->edit.train.color=rr_string(input->color);
    if (!output->edit.train.id || !output->edit.train.color) return -2;
    if (input->operation != RR_CREATE_LEGACY) return 0;
    rr_edit_request geometry={0}; geometry.points=input->points; geometry.n_points=input->n_points;
    rc=rr_edit_geometry(&output->edit,&geometry); if (rc) return rc;
    if (input->n_points) return rr_create_attach(output,input);
    return 0; /* Original empty legacy ensures an edge without attaching. */
}
CE_API int ce_runner_train_initialize_v7(const rr_create_request* input,rr_create_report* output)
{
    if (!output) return -1;
    int rc=rr_create_admission(input); if (rc) return rc;
    if (input->operation != RR_CREATE_DIRECT) return -1;
    return rr_create_initialize(input,output); /* fixed stack output, zero native heap */
}
