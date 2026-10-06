/* pmustat: count hardware events for a command, as `perf stat` does, without the perf tool (Linux).
     pmustat NAME=TYPE:CONFIG ... -- COMMAND [ARGS...]
   TYPE 0 = PERF_TYPE_HARDWARE, 4 = PERF_TYPE_RAW; CONFIG in hex. The child waits on a pipe while the
   counters are opened on it (counting from its exec, user space only), then execs COMMAND. Prints
   "NAME count" (or "NAME error errno") per event on stderr after the child exits, and exits with its
   status. Needs perf_event_paranoid <= 2 (Ubuntu's default is 4). */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __linux__
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAXEV 16

int main(int argc, char **argv) {
    char names[MAXEV][64];
    unsigned types[MAXEV];
    unsigned long long configs[MAXEV];
    int n = 0, i = 1;
    for (; i < argc && strcmp(argv[i], "--"); i++)
        if (n < MAXEV && sscanf(argv[i], "%63[^=]=%u:%llx", names[n], &types[n], &configs[n]) == 3) n++;
    if (i >= argc - 1) { fprintf(stderr, "usage: pmustat EVENTS -- COMMAND...\n"); return 2; }
    char **cmd = argv + i + 1;
    int go[2];
    if (pipe(go)) return 2;
    pid_t pid = fork();
    if (pid == 0) {
        char c;
        close(go[1]);
        if (read(go[0], &c, 1) != 1) _exit(127);
        execvp(cmd[0], cmd);
        _exit(127);
    }
    close(go[0]);
    int fd[MAXEV];
    for (int k = 0; k < n; k++) {
        struct perf_event_attr a;
        memset(&a, 0, sizeof a);
        a.size = sizeof a;
        a.type = types[k];
        a.config = configs[k];
        a.disabled = 1;
        a.enable_on_exec = 1;
        a.exclude_kernel = 1;
        a.exclude_hv = 1;
        fd[k] = (int)syscall(SYS_perf_event_open, &a, pid, -1, -1, 0);
        if (fd[k] < 0) fd[k] = -errno;
    }
    if (write(go[1], "x", 1) != 1) return 2;
    close(go[1]);
    int st = 0;
    waitpid(pid, &st, 0);
    for (int k = 0; k < n; k++) {
        if (fd[k] < 0) { fprintf(stderr, "%s error %d\n", names[k], -fd[k]); continue; }
        long long v = 0;
        if (read(fd[k], &v, sizeof v) != sizeof v) v = -1;
        fprintf(stderr, "%s %lld\n", names[k], v);
        close(fd[k]);
    }
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
#else
int main(void) { fputs("unsupported\n", stderr); return 2; }
#endif
