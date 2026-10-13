/* Research-only R215-C06 decimal rounding primitive. Not compiled or installed.
 * Behavior reference: CPython v3.13.13 Objects/floatobject.c double_round,
 * correctly-rounded decimal lattice then correctly-rounded binary64 result.
 * PSF source reference/complete LICENSE retained alongside. The bounded
 * uint64 adaptation below is original; no dtoa source or mutable memo copied.
 * Integer half-even for bounded processor division reuses ce_round_half_even.
 */
#include <stdint.h>
#include <string.h>
#include <math.h>

#if defined(__FAST_MATH__)
#error Audio schema exact decimal rounding cannot use fast math
#endif

static double _ce_audio_decimal34(double value, int digits)
{
    uint64_t raw;
    memcpy(&raw, &value, sizeof(raw));
    const unsigned exponent_bits = (unsigned)((raw >> 52) & 0x7ffU);
    if (exponent_bits == 0x7ffU || value == 0.0) return value;
    if (digits != 3 && digits != 4) return value; /* caller admits only 3/4 */
    const double unchanged = digits == 3 ? 8796093022208.0 : 549755813888.0;
    if (fabs(value) >= unchanged) return value;
    uint64_t significand = raw & UINT64_C(0x000fffffffffffff);
    int exponent = -1074;
    if (exponent_bits) {
        significand |= UINT64_C(0x0010000000000000);
        exponent = (int)exponent_bits - 1023 - 52;
    }
    const uint64_t scaled = significand * (digits == 3 ? 125U : 625U);
    const int binary_shift = exponent + digits;
    if (binary_shift >= 0) return value;
    const int fractional_bits = -binary_shift;
    if (fractional_bits >= 64) return copysign(0.0, value);
    uint64_t integer = scaled >> fractional_bits;
    const uint64_t remainder = scaled & ((UINT64_C(1) << fractional_bits) - 1U);
    const uint64_t halfway = UINT64_C(1) << (fractional_bits - 1);
    if (remainder > halfway || (remainder == halfway && (integer & 1U))) ++integer;
    /* Under unchanged boundary integer < 2**53, so conversion is exact.
     * Volatile divisor keeps a correctly-rounded division, not reciprocal
     * multiplication. Windows x64 uses binary64 SSE; ROOT checks both ISAs. */
    volatile double divisor = digits == 3 ? 1000.0 : 10000.0;
    const double rounded = (double)integer / divisor;
    return copysign(rounded, value);
}
