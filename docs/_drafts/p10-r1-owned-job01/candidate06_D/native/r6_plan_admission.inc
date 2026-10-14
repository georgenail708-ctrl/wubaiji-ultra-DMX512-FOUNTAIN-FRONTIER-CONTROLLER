/* Admit every declared top-level extent before fixture filtering or pointer
 * traversal. Nested page/text owners are then admitted in the bounded original
 * component stages. This is an external-owner lower bound, not process proof. */
static int r6_plan_admit(r6_job* job,uint64_t index)
{
    const r6_clip_geometry_raw* raw=job->borrowed->geometry+index;
    const r6_coordinate_raw* xy=&raw->coordinates;r3_job* scene=job->timeline->base->scene;
    uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,xy->fixtures_n,sizeof(r6_fixture_raw))
            || !r3_add_bytes(&bytes,xy->snapshot_n,sizeof(r6_snapshot_raw))
            || !r3_add_bytes(&bytes,xy->pages_n,sizeof(r6_page_raw))
            || !r3_add_bytes(&bytes,raw->definitions_n,sizeof(r6_definition_raw))
            || !r3_add_bytes(&bytes,raw->groups_n,sizeof(r6_group_raw))
            || !r3_add_bytes(&bytes,raw->shape_pages_n,sizeof(r6_shape_page_raw))) return R1_BUDGET;
    if (bytes>scene->external_bytes || xy->external_limit!=scene->external_bytes) return R1_BUDGET;
    if ((xy->fixtures_n && !xy->fixtures) || (xy->snapshot_n && !xy->snapshot)
            || (xy->pages_n && !xy->pages) || (raw->definitions_n && !raw->definitions)
            || (raw->groups_n && !raw->groups) || (raw->shape_pages_n && !raw->shapes)) return R1_INVALID;
    if (raw->reserved || raw->overrides_n || raw->curve_points_n || raw->ratio_in_n
            || raw->ratio_out_n || raw->force_points_n) {
        scene->pending_features|=R6_PENDING_ATTRIBUTES;return R1_PENDING;
    }
    const r5_clip* clip=job->timeline->clips+index;
    if (clip->dir_in!=R5_DIR_NONE || clip->dir_out!=R5_DIR_NONE) {
        const r5_text* scope=&job->borrowed->raw.phases[index].spatial_scope;
        int global=r5_text_equal(scope,"global",1),overall=r5_text_equal(scope,"overall",1),world=r5_text_equal(scope,"world",1);
        if (global<0 || overall<0 || world<0) return R1_PENDING;
        if (!(global || overall || world)) {scene->pending_features|=R6_PENDING_SEGMENTS;return R1_PENDING;}
    }
    return R1_OK;
}

static int r6_plan_needs_geometry(const r6_job* job,uint64_t index)
{
    const r5_clip* clip=job->timeline->clips+index;
    return clip->dir_in!=R5_DIR_NONE || clip->dir_out!=R5_DIR_NONE;
}
