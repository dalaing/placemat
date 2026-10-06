/* placemat test fixture: small functions with small loops, for the binary analysis and pinning
   tests. Freestanding apart from main (no libc calls), so it also links with -nostdlib.
   `pinme N` runs N rounds; `pinme spin` runs until killed (for profilers). */
#define NOINLINE __attribute__((noinline))
#define N 512

static long a[N], b[N];
volatile long sink;

NOINLINE long sum(const long *x, long n) {
    long s = 0;
    for (long i = 0; i < n; i++) s += x[i];
    return s;
}

NOINLINE long dot(const long *x, const long *y, long n) {
    long s = 0;
    for (long i = 0; i < n; i++) s += x[i] * y[i] + (s >> 7);
    return s;
}

NOINLINE long count_eq(const long *x, long n, long v) {
    long c = 0;
    for (long i = 0; i < n; i++) c += x[i] == v;
    return c;
}

NOINLINE long maxv(const long *x, long n) {
    long m = x[0];
    for (long i = 1; i < n; i++) if (x[i] > m) m = x[i];
    return m;
}

NOINLINE long scan(long *x, long n) {
    for (long i = 1; i < n; i++) x[i] = (x[i] ^ x[i - 1]) & 0xffff;
    return x[n - 1];
}

NOINLINE long poly(long x, long n) {
    long r = 1;
#ifdef PLACEMAT_CHANGE
    while (n-- > 0) r = r * x + (r >> 5) + n;     /* the "change" for the same-code tests */
#else
    while (n-- > 0) r = r * x + (r >> 3) + n;
#endif
    return r;
}

NOINLINE void fill(long *x, long n, long seed) {
    for (long i = 0; i < n; i++) { seed = seed * 6364136223846793005L + 1442695040888963407L; x[i] = seed >> 40; }
}

NOINLINE long cold(long x) {   /* never hot: something unordered to sit between hot functions */
    long r = 0;
    for (int i = 0; i < 3; i++) r += x * i;
    return r;
}

NOINLINE long round_(long k) {
    long s = 0;
    fill(a, N, k);
    fill(b, N, k + 1);
    s += sum(a, N);
    s += dot(a, b, N);
    s += count_eq(a, N, a[k % N]);
    s += maxv(b, N);
    s += scan(b, N);
    s += poly(k, 64);
    return s;
}

int main(int argc, char **argv) {
    long rounds = 1, spin = 0, s = 0;
    if (argc > 1) {
        if (argv[1][0] == 's') spin = 1;
        else { rounds = 0; for (const char *p = argv[1]; *p >= '0' && *p <= '9'; p++) rounds = rounds * 10 + (*p - '0'); }
    }
    for (long k = 0; spin || k < rounds; k++) s += round_(k);
    sink = s + cold(argc);
    return 0;
}
