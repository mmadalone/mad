/*
 * LD_PRELOAD shim: smooths Sinden Lightgun cursor by intercepting SDL_WarpMouse.
 *
 * The Sinden Mono driver (LightgunMono.exe via libSdlInterface.so) calls
 * SDL_WarpMouse(x,y) for every camera frame to position the system cursor at
 * the gun's aim point. Per the gun's image-recognition algorithm those
 * positions jitter by 1-3 px even when held still. This shim:
 *   1. Replaces SDL_WarpMouse
 *   2. Applies an exponential moving average (EMA) per axis to smooth motion
 *   3. Snaps to the raw value when the EMA is "close" (deadzone) so the cursor
 *      doesn't swim around when held still
 *   4. Forwards the smoothed coords to the real SDL_WarpMouse via dlsym(RTLD_NEXT)
 *
 * Algorithm matches HOTDOV.ahk's AbsMove() (alpha=0.12, deadzone=1.6 by default).
 *
 * Tunable via env vars at driver-start time:
 *   SINDEN_SMOOTH_ALPHA=0.12   SINDEN_SMOOTH_DEADZONE=1.6  SINDEN_SMOOTH=1
 *
 * Build:  gcc -shared -fPIC -O2 -o sinden-smooth.so sinden-smooth.c -ldl
 * Use:    LD_PRELOAD=/home/deck/Emulation/tools/sinden-shim/sinden-smooth.so \
 *           mono-service LightgunMono.exe
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <dlfcn.h>
#include <stdint.h>

typedef uint16_t Uint16;
typedef void (*warp_fn)(Uint16, Uint16);

static warp_fn real_warp = NULL;
static double ema_x = 0.0, ema_y = 0.0;
static double last_emit_x = 0.0, last_emit_y = 0.0;
static int initialized = 0;
static double ALPHA = 0.12;
static double DEADZONE = 1.6;
static int ENABLED = 1;

static void __attribute__((constructor)) init_shim(void) {
    const char *e;
    if ((e = getenv("SINDEN_SMOOTH_ALPHA")))    ALPHA    = atof(e);
    if ((e = getenv("SINDEN_SMOOTH_DEADZONE"))) DEADZONE = atof(e);
    if ((e = getenv("SINDEN_SMOOTH")))          ENABLED  = atoi(e);
    fprintf(stderr, "[sinden-smooth] loaded alpha=%.3f deadzone=%.2f enabled=%d\n",
            ALPHA, DEADZONE, ENABLED);
}

void SDL_WarpMouse(Uint16 x, Uint16 y) {
    if (!real_warp) {
        real_warp = (warp_fn)dlsym(RTLD_NEXT, "SDL_WarpMouse");
        if (!real_warp) {
            fprintf(stderr, "[sinden-smooth] dlsym(SDL_WarpMouse) failed: %s\n", dlerror());
            return;
        }
    }

    if (!ENABLED) { real_warp(x, y); return; }

    if (!initialized) {
        ema_x = x; ema_y = y;
        last_emit_x = x; last_emit_y = y;
        initialized = 1;
        real_warp(x, y);
        return;
    }

    // EMA update — smoothed estimate of the gun's true aim point
    ema_x += ALPHA * ((double)x - ema_x);
    ema_y += ALPHA * ((double)y - ema_y);

    // Deadzone gate: only emit a new position if the smoothed value has moved
    // far enough from the last EMITTED one. Holding the gun still produces
    // micro-jitter in raw input, but EMA pins ema_* close to the average and
    // we suppress emits within deadzone — cursor stays put.
    double dx = ema_x - last_emit_x;
    double dy = ema_y - last_emit_y;
    if (fabs(dx) < DEADZONE && fabs(dy) < DEADZONE) {
        return;  // suppress; cursor doesn't move
    }

    last_emit_x = ema_x;
    last_emit_y = ema_y;
    real_warp((Uint16)(ema_x + 0.5), (Uint16)(ema_y + 0.5));

    // Periodic counter so we can prove the shim is actually being called
    static unsigned long n_calls = 0, n_emitted = 0;
    n_calls++;
    n_emitted++;
    if (n_calls % 500 == 0) {
        fprintf(stderr, "[sinden-smooth] %lu calls intercepted, %lu emitted, "
                "ema=(%.1f,%.1f) raw=(%u,%u)\n",
                n_calls, n_emitted, ema_x, ema_y, x, y);
    }
}
