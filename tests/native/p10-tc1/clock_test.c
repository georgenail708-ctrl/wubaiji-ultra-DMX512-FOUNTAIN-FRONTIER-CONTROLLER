/* Test-only composition of the actual authored resident policies.
 * Product uses these entries in its single CE DLL. This isolated library
 * establishes numerical/ABI controls, never runtime source qualification. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define CE_API __declspec(dllexport)
#include "csrc/ce_sntc_v1_types.inc"
#include "csrc/ce_sntc_v1_api.inc"
#include "csrc/ce_sntc_consumer_v1.inc"
#include "csrc/ce_sntc_media_v1_types.inc"
#include "csrc/ce_sntc_media_v1_api.inc"
#include "csrc/ce_sntc_reference_policy_v1.inc"
