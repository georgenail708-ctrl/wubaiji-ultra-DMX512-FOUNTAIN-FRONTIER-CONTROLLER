/* No raw clip/source/default traversal occurs before count lower bounds and
 * budget admission. Caller pins exact extents; pointer validity is not guessed. */
static int r4_shape(const r4_input* input)
{
    uint64_t bytes=sizeof(r4_job)+sizeof(r3_job)+sizeof(r1_ledger)+R1_KERNEL_RESERVE;
    if (!input || input->abi!=R4_ABI || input->struct_size!=sizeof(*input)) return R1_INVALID;
    const r3_input* raw=&input->raw;
    if (raw->abi!=R3_ABI || raw->struct_size!=sizeof(*raw)) return R1_INVALID;
    if (!raw->clip_count || !raw->clips || !input->clip_masks || !raw->track_count || !raw->tracks) return R1_INVALID;
    if (raw->clip_count>INT_MAX || raw->track_count>INT_MAX || raw->fixture_count>INT_MAX/4) return R1_BUDGET;
    if ((raw->fixture_count && !raw->fixtures) || (raw->definition_count && !raw->definitions)
            || (raw->default_count && !raw->defaults) || (input->patch_fixtures_n && !input->patch_fixtures)
            || (input->patch_definitions_n && !input->patch_definitions)) return R1_INVALID;
    if (!r3_add_bytes(&bytes,raw->external_bytes,1)
            || !r3_add_bytes(&bytes,raw->clip_count,sizeof(r3_clip)+sizeof(r4_clip))
            || !r3_add_bytes(&bytes,raw->track_count,sizeof(r3_track_raw)+84)
            || !r3_add_bytes(&bytes,raw->fixture_count,sizeof(r3_fixture_raw))
            || !r3_add_bytes(&bytes,raw->definition_count,sizeof(r3_definition_raw))
            || !r3_add_bytes(&bytes,raw->default_count,sizeof(r3_default_raw))) return R1_BUDGET;
    if (raw->byte_budget>R3_WORKFLOW_MAX_BYTES || raw->byte_budget>SIZE_MAX || bytes>raw->byte_budget) return R1_BUDGET;
    uint64_t minimum=0;
    if (!r4_external_metadata(input,&minimum) || minimum>raw->external_bytes) return R1_BUDGET;
    return R1_OK;
}

static int r4_external_metadata(const r4_input* input,uint64_t* bytes)
{
    const r3_input* raw=&input->raw;
    return r3_add_bytes(bytes,raw->clip_count,sizeof(r3_clip_raw)+sizeof(r4_clip_raw))
        && r3_add_bytes(bytes,raw->track_count,sizeof(r3_track_raw))
        && r3_add_bytes(bytes,raw->fixture_count,sizeof(r3_fixture_raw))
        && r3_add_bytes(bytes,raw->definition_count,sizeof(r3_definition_raw))
        && r3_add_bytes(bytes,raw->default_count,sizeof(r3_default_raw))
        && r3_add_bytes(bytes,input->patch_fixtures_n,sizeof(r4_patch_fixture))
        && r3_add_bytes(bytes,input->patch_definitions_n,sizeof(r4_patch_definition));
}

static int r4_model_allocate(r4_job* job,const r3_input* raw)
{
    r3_job* scene=job->scene;r1_ledger* ledger=scene->clock.ledger;
    uint64_t bytes=0;
    if (!r3_add_bytes(&bytes,scene->tracks_n,sizeof(r3_track_raw))
            || !r3_add_bytes(&bytes,scene->fixtures_n,sizeof(r3_fixture_raw))
            || !r3_add_bytes(&bytes,scene->definitions_n,sizeof(r3_definition_raw))
            || !r3_add_bytes(&bytes,scene->defaults_n,sizeof(r3_default_raw))) return R1_BUDGET;
    if (bytes>ledger->limit-ledger->used) return R1_BUDGET;
    scene->tracks_bytes=scene->tracks_n*sizeof(r3_track_raw);scene->fixtures_bytes=scene->fixtures_n*sizeof(r3_fixture_raw);
    scene->definitions_bytes=scene->definitions_n*sizeof(r3_definition_raw);scene->defaults_bytes=scene->defaults_n*sizeof(r3_default_raw);
    scene->tracks=r1_allocate(ledger,scene->tracks_bytes);
    if (scene->fixtures_n) scene->fixtures=r1_allocate(ledger,scene->fixtures_bytes);
    if (scene->definitions_n) scene->definitions=r1_allocate(ledger,scene->definitions_bytes);
    if (scene->defaults_n) scene->defaults=r1_allocate(ledger,scene->defaults_bytes);
    if (!scene->tracks || (scene->fixtures_n && !scene->fixtures) || (scene->definitions_n && !scene->definitions)
            || (scene->defaults_n && !scene->defaults)) return R1_BUDGET;
    return r4_tracks_create(&job->tracks,ledger,raw->tracks,raw->track_count);
}
