/* stackprobe: where the initial stack sits (DESIGN 5.5). Prints argv's address (set by the block of
   arguments and environment the kernel copies above the stack) and a local's address in main. */
#include <stdint.h>
#include <stdio.h>

int main(int argc, char **argv) {
    volatile int local = argc;
    printf("%llu %llu\n", (unsigned long long)(uintptr_t)argv, (unsigned long long)(uintptr_t)&local);
    return 0;
}
