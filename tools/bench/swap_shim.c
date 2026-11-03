/* Benchmark-only LD_PRELOAD: make glXSwapBuffers a glFlush, so a private Xvfb does not add its 24-30 ms swap. */
#define _GNU_SOURCE
#include <dlfcn.h>

void glXSwapBuffers(void *display, unsigned long drawable)
{
    static void (*flush)(void);
    (void)display;
    (void)drawable;
    if (!flush) {
        flush = (void (*)(void))dlsym(RTLD_DEFAULT, "glFlush");
        if (!flush) {
            void *gl = dlopen("libOpenGL.so.0", RTLD_LAZY);
            flush = gl ? (void (*)(void))dlsym(gl, "glFlush") : 0;
        }
    }
    if (flush)
        flush();
}
