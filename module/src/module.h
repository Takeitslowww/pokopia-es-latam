/* Shared declarations for the es-419 language module (freestanding, no libc). */
#pragma once

#include "game_offsets.h"

typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
typedef long s64;
typedef int s32;
typedef u64 uptr;

#define HIDDEN __attribute__((visibility("hidden")))

/* Registers at the hook site, saved by the thunks in hooks.S. */
struct regs {
    u64 x[18];   /* x0..x17 */
    u64 fp;      /* x29 */
    u64 lr;      /* x30 */
};

/* Start of this module (header.S); main = module - MODULE_OFFSET. */
extern char __module_start[] HIDDEN;

static inline uptr main_base(void) { return (uptr)__module_start - MODULE_OFFSET; }
static inline uptr game(u64 offset) { return main_base() + offset; }

static inline u32 rd32(uptr a) { return *(volatile u32 *)a; }
static inline u64 rd64(uptr a) { return *(volatile u64 *)a; }
static inline void wr32(uptr a, u32 v) { *(volatile u32 *)a = v; }

void *memcpy(void *dst, const void *src, u64 n) HIDDEN;
void *memset(void *dst, int c, u64 n) HIDDEN;

/* latam.c: state shared with the debug helpers */
int latam_active(void) HIDDEN;
u32 latam_captured(void) HIDDEN;

#ifdef LATAM_DEBUG
/* debug.c: svcOutputDebugString logging (shows in Eden's log as Debug_Emulated). */
void dbg_log(const char *fmt, ...) HIDDEN;   /* %x %d %s %p only */
void dbg_grid_shown(void) HIDDEN;
#define DBG(...) dbg_log(__VA_ARGS__)
#else
#define DBG(...) ((void)0)
#endif
