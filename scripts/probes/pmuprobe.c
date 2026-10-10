/* pmuprobe: which hardware event counters this (Linux) machine lets a process count on itself. Each
   argument is NAME=TYPE:CONFIG (TYPE 0 = PERF_TYPE_HARDWARE, 4 = PERF_TYPE_RAW; CONFIG in hex); for each,
   prints "NAME count" after a fixed loop, or "NAME error errno" when the counter cannot be opened.
   Elsewhere it prints "unsupported". */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __linux__
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

static volatile long sink;

static void work(void) {
    long s = 0;
    for (long i = 0; i < 20000000; i++) s += (i * 7) ^ (s >> 3);
    sink = s;
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char name[64];
        unsigned type;
        unsigned long long config;
        if (sscanf(argv[i], "%63[^=]=%u:%llx", name, &type, &config) != 3) continue;
        struct perf_event_attr a;
        memset(&a, 0, sizeof a);
        a.size = sizeof a;
        a.type = type;
        a.config = config;
        a.disabled = 1;
        a.exclude_kernel = 1;
        a.exclude_hv = 1;
        int fd = (int)syscall(SYS_perf_event_open, &a, 0, -1, -1, 0);
        if (fd < 0) { printf("%s error %d\n", name, errno); continue; }
        ioctl(fd, PERF_EVENT_IOC_RESET, 0);
        ioctl(fd, PERF_EVENT_IOC_ENABLE, 0);
        work();
        ioctl(fd, PERF_EVENT_IOC_DISABLE, 0);
        long long n = 0;
        if (read(fd, &n, sizeof n) != sizeof n) n = -1;
        printf("%s %lld\n", name, n);
        close(fd);
    }
    return 0;
}
#else
int main(void) { puts("unsupported"); return 0; }
#endif
