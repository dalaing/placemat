/* regionprobe: where a large region lands (the region lottery, DESIGN 5.4). Maps a 1 GB anonymous region
   as a custom allocator does (Amber maps its regions so), and mallocs an 8 MB block; prints the region's
   base, the block's address, the program image's address (main) and a stack local's address. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#ifndef MAP_NORESERVE
#define MAP_NORESERVE 0
#endif

int main(void) {
    volatile int local = 0;
    void *r = mmap(NULL, (size_t)1 << 30, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    void *b = malloc((size_t)8 << 20);
    if (r == MAP_FAILED || !b) return 1;
    printf("%llu %llu %llu %llu\n", (unsigned long long)(uintptr_t)r, (unsigned long long)(uintptr_t)b,
           (unsigned long long)(uintptr_t)&main, (unsigned long long)(uintptr_t)&local);
    return 0;
}
