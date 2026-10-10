/* lotteryprobe: does where the region or the stack lands change speed? (DESIGN 5.4, 5.5)
   Maps a 1 GB region as a custom allocator does and puts four 1 MB buffers in it 4 MB apart (all at the
   same offset mod 16 KB, as a buddy allocator places them), runs a sliding-window sum over them (the
   kind of kernel Amber's region lottery slowed), then a recursive kernel with a 512-byte local array per
   frame. Prints: region base, the address of a local in main, window time (ns), stack time (ns). */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

#ifndef MAP_NORESERVE
#define MAP_NORESERVE 0
#endif

#define N (1 << 17)           /* doubles per buffer: 1 MB */
#define W 100

static long long now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}

static double frame(int depth) {
    volatile double a[64];
    for (int i = 0; i < 64; i++) a[i] = depth + i;
    double s = 0;
    for (int i = 0; i < 64; i++) s += a[i];
    return depth ? s + frame(depth - 1) : s;
}

int main(void) {
    volatile int local = 0;
    char *r = mmap(NULL, (size_t)1 << 30, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (r == MAP_FAILED) return 1;
    double *b[4];
    for (int k = 0; k < 4; k++) {
        b[k] = (double *)(r + ((size_t)k << 22) + 64);
        for (int i = 0; i < N; i++) b[k][i] = (double)(i % 97) * (k + 1);
    }
    long long t0 = now();
    double sink = 0;
    for (int pass = 0; pass < 6; pass++)
        for (int k = 0; k < 3; k++) {            /* b[k+1][i] = moving sum of b[k] over W */
            double s = 0;
            for (int i = 0; i < N; i++) {
                s += b[k][i] - (i >= W ? b[k][i - W] : 0);
                b[k + 1][i] = s;
            }
            sink += b[k + 1][N - 1];
        }
    long long t1 = now();
    double fs = 0;
    for (int rep = 0; rep < 4000; rep++) fs += frame(64);
    long long t2 = now();
    printf("%llu %llu %lld %lld %g\n", (unsigned long long)(uintptr_t)r, (unsigned long long)(uintptr_t)&local,
           t1 - t0, t2 - t1, sink + fs);
    return 0;
}
