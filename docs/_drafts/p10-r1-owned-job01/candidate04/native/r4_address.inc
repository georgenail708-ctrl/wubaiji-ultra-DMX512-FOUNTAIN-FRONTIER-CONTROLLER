/* Python int(raw): truncation before valid 1..512 check. Never cast a huge
 * finite double or subtract one from INT_MIN in C. Ordinary out-of-range
 * fixture addresses are numerical skip, not a forged small allocation. */
static int r4_motor_address(r1_atom atom,int* result)
{
    double number;
    if (atom.kind==R1_NONE) return R1_PENDING; /* original int(None) exception domain */
    if (r1_atom_number(atom,1.,&number)) return R1_PENDING;
    number=trunc(number);
    if (number<1. || number>512.) return 0;
    *result=(int)number-1;return 1;
}

static int r4_lt_address(r1_atom atom,int* result)
{
    double number;
    if (r1_atom_number(atom,1.,&number)) return R1_PENDING;
    /* Public PE target/member classifiers use int(raw or 1). */
    if (number==0.) number=1.;
    number=trunc(number);
    if (number>512. || number<=-512.) return 0;
    if (number<1.) return R1_PENDING;
    /* Small negative addresses have partial-span compatibility in old LT,
     * so they remain explicit pending until that whole original path is
     * represented. They are never silently truncated to a successful row. */
    *result=(int)number-1;return 1;
}
