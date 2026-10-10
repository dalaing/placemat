/* luabench: placemat's benchmark host for Lua (placemat examples, MIT licence).
 *
 * Uses only Lua's public C API (lua.h, lauxlib.h, lualib.h); no Lua source is copied here.
 *
 *     luabench DIR [CASE...]
 *
 * Each case is DIR/<case>.lua, a chunk returning a table { n = <iterations>, run = function(n) ... end }:
 * run(n) does n iterations of the case's work and returns a checksum. Every case runs in a fresh
 * lua_State: a full collection, a discarded warm-up (run(1)), then the timed run(n). Output is
 * placemat's benchmark protocol (DESIGN §8.1), one line per timed run:
 *
 *     <case> <microseconds> <iterations> us
 *
 * plus '# <case> checksum <value>' comment lines (ignored by placemat's parser), so that two arms
 * can be checked to compute the same thing.
 *
 * Cases: PLACEMAT_CASES (comma-separated) when set, else the command-line CASEs, else every *.lua in
 * DIR in name order. PLACEMAT_SERIES ('name=0.125,0.25,0.5,1;name2=...') times a listed case once per
 * scale, at max(1, round(n * scale)) iterations, after the same discarded warm-up.
 *
 * Data placement: built with -DPLACEMAT_HOOK (placemat's hooked builds), every state is created
 * with lua_newstate(placemat_lua_alloc, NULL) (plus luaL_makeseed(NULL) on 5.5), placemat's colouring allocator (placemat_alloc.h);
 * otherwise with luaL_newstate(), Lua's own realloc/free allocator, as the stock `lua` uses.
 */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

#ifdef PLACEMAT_HOOK
#include "placemat_alloc.h"
#endif

#define MAX_CASES 256

static double now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e6 + (double)ts.tv_nsec / 1e3;
}

#ifdef PLACEMAT_HOOK
static int panic(lua_State *L)
{
    const char *m = lua_tostring(L, -1);
    fprintf(stderr, "luabench: PANIC: %s\n", m ? m : "(error object is not a string)");
    return 0;
}
#endif

static lua_State *new_state(void)
{
#ifdef PLACEMAT_HOOK
#if LUA_VERSION_NUM >= 505     /* Lua 5.5 (development trees too) takes a hash seed */
    lua_State *L = lua_newstate(placemat_lua_alloc, NULL, luaL_makeseed(NULL));
#else
    lua_State *L = lua_newstate(placemat_lua_alloc, NULL);
#endif
    if (L)
        lua_atpanic(L, panic);
#else
    lua_State *L = luaL_newstate();
#endif
    if (!L) {
        fprintf(stderr, "luabench: cannot create a Lua state\n");
        exit(2);
    }
    luaL_openlibs(L);
    return L;
}

/* The scales PLACEMAT_SERIES gives `name`, into sc (at most 16); their number, 0 if none. */
static int series_for(const char *name, double *sc)
{
    const char *s = getenv("PLACEMAT_SERIES");
    size_t nl = strlen(name);
    int k = 0;
    while (s && *s) {
        const char *end = strchr(s, ';');
        size_t len = end ? (size_t)(end - s) : strlen(s);
        const char *eq = memchr(s, '=', len);
        if (eq && (size_t)(eq - s) == nl && memcmp(s, name, nl) == 0) {
            const char *p = eq + 1;
            while (p < s + len && k < 16) {
                char *q;
                double v = strtod(p, &q);
                if (q == p)
                    break;
                sc[k++] = v;
                p = (*q == ',') ? q + 1 : q;
            }
            return k;
        }
        s = end ? end + 1 : NULL;
    }
    return 0;
}

/* Call run(iters) (the function at index fi); 0 on error (message printed). */
static int call_run(lua_State *L, int fi, const char *name, long iters, double *us, double *sum)
{
    lua_pushvalue(L, fi);
    lua_pushinteger(L, (lua_Integer)iters);
    double t0 = now_us();
    int rc = lua_pcall(L, 1, 1, 0);
    double t1 = now_us();
    if (rc != LUA_OK) {
        fprintf(stderr, "luabench: %s: %s\n", name, lua_tostring(L, -1));
        lua_pop(L, 1);
        return 0;
    }
    *us = t1 - t0;
    *sum = lua_tonumber(L, -1);
    lua_pop(L, 1);
    return 1;
}

static int run_case(const char *dir, const char *name)
{
    char path[4096];
    snprintf(path, sizeof path, "%s/%s.lua", dir, name);
    lua_State *L = new_state();
    int ok = 0;
    if (luaL_loadfilex(L, path, "t") != LUA_OK || lua_pcall(L, 0, 1, 0) != LUA_OK) {
        fprintf(stderr, "luabench: %s: %s\n", name, lua_tostring(L, -1));
        goto done;
    }
    if (!lua_istable(L, -1)) {
        fprintf(stderr, "luabench: %s: the chunk must return a table {n=..., run=...}\n", name);
        goto done;
    }
    lua_getfield(L, -1, "n");
    long n = (long)lua_tointeger(L, -1);
    lua_pop(L, 1);
    lua_getfield(L, -1, "run");
    if (n < 1 || !lua_isfunction(L, -1)) {
        fprintf(stderr, "luabench: %s: needs n >= 1 and a run function\n", name);
        goto done;
    }
    int fi = lua_gettop(L);
    double sc[16], us, sum;
    int ns = series_for(name, sc);
    lua_gc(L, LUA_GCCOLLECT, 0);
    if (!call_run(L, fi, name, 1, &us, &sum))       /* discarded warm-up */
        goto done;
    if (ns == 0) {
        sc[0] = 1.0;
        ns = 1;
    }
    for (int i = 0; i < ns; i++) {
        long it = (long)(n * sc[i] + 0.5);
        if (it < 1)
            it = 1;
        lua_gc(L, LUA_GCCOLLECT, 0);
        if (!call_run(L, fi, name, it, &us, &sum))
            goto done;
        printf("%s %.3f %ld us\n", name, us, it);
        printf("# %s checksum %.17g\n", name, sum);
        fflush(stdout);
    }
    ok = 1;
done:
    lua_close(L);
    return ok;
}

static int cmp_str(const void *a, const void *b)
{
    return strcmp(*(char *const *)a, *(char *const *)b);
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: luabench DIR [CASE...]\n");
        return 2;
    }
    const char *dir = argv[1];
    char *cases[MAX_CASES];
    int nc = 0;
    const char *env = getenv("PLACEMAT_CASES");
    if (env && *env) {
        char *s = strdup(env);
        for (char *t = strtok(s, ","); t && nc < MAX_CASES; t = strtok(NULL, ","))
            if (*t)
                cases[nc++] = t;
    } else if (argc > 2) {
        for (int i = 2; i < argc && nc < MAX_CASES; i++)
            cases[nc++] = argv[i];
    } else {
        DIR *d = opendir(dir);
        struct dirent *e;
        if (!d) {
            perror(dir);
            return 2;
        }
        while ((e = readdir(d)) && nc < MAX_CASES) {
            size_t l = strlen(e->d_name);
            if (l > 4 && strcmp(e->d_name + l - 4, ".lua") == 0)
                cases[nc++] = strndup(e->d_name, l - 4);
        }
        closedir(d);
        qsort(cases, (size_t)nc, sizeof cases[0], cmp_str);
    }
    int bad = 0;
    for (int i = 0; i < nc; i++)
        bad += !run_case(dir, cases[i]);
    return bad ? 1 : 0;
}
