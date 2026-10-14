typedef struct {
    r1_atom number;uint64_t text_bytes;const uint8_t* text;uint32_t kind,reserved;
} r4_channel_raw; /* kind0 numeric atom, kind1 exact UTF8 string codec */
typedef struct {
    const r4_channel_raw* borrowed;uint64_t count,cursor;
    uint8_t mask[512];uint32_t present,all_numeric_equal,cancelled,semantic;
} r4_rule;

typedef struct {uint64_t bytes;const uint8_t* utf8;} r4_channel_name;
typedef struct {uint64_t token; r1_atom channel_count;uint64_t types_n;const r4_channel_name* names;} r4_patch_definition;
typedef struct {uint64_t token,definition,attached_definition;uint32_t universe,reserved;r1_atom start_channel;} r4_patch_fixture;
typedef struct {
    const r4_patch_fixture* fixtures;uint64_t fixtures_n;
    const r4_patch_definition* definitions;uint64_t definitions_n;
    const uint64_t* base_ids;uint64_t base_n;
    uint64_t id_cursor,fixture_cursor,definition_cursor,channel_cursor,last_fixture,last_definition;
    uint32_t universe,phase,base_present,cancelled,semantic;
    uint8_t base[512];
} r4_base_mask;

typedef struct {
    const r4_patch_fixture* fixtures;uint64_t fixtures_n;
    const r4_patch_definition* definitions;uint64_t definitions_n;
    uint64_t fixture_cursor,definition_cursor,last_definition,fine_cursor,match_cursor;
    uint32_t universe,phase,cancelled,semantic;int start;
    uint8_t mask[512],pair[512];int pair_any;
} r4_pair_mask;
