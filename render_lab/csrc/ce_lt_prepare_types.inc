/* C07 research candidate ABI v1; four ordered scalar stages. */
typedef struct {
    uint32_t kind, selector;
    uint64_t offset, length;
    double number;
} LtpValue;

typedef struct {
    uint32_t abi, struct_size, mode, count;
    const unsigned char *integers;
    uint64_t integer_size;
    LtpValue values[4];
} LtpRequest;

typedef struct {
    uint32_t abi, struct_size, semantic, selector;
    uint64_t consumed, threshold_values, attack_values, release_values, brightness_values;
    int32_t threshold, brightness;
    double attack, release;
} LtpReport;

/* Value kind: 0 unused after a semantic stage, 1 signed LE integer,
 * 2 binary64, 3 retained Python conversion-error selector. */
