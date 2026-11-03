/*
 * Debug-build helpers (compiled only with LATAM_DEBUG):
 *
 * - dbg_log(): printf-like logging through svcOutputDebugString; Eden writes
 *   it to its log as "Debug.Emulated <Info>".
 * - scripted controller input: the hid hooks of player 1 add the buttons of a
 *   built-in script, timed in pad polls (~frames) after the language grid is
 *   built. The emulator's own input does not reach this game, so this is the
 *   only way to drive the menus in automated tests. Pick the script with
 *   -DLATAM_DEBUG_SCRIPT=<n> (build.py --debug-script).
 */
#include <stdarg.h>
#include "module.h"

#ifndef LATAM_DEBUG_SCRIPT
#define LATAM_DEBUG_SCRIPT 0
#endif

/* ------------------------------------------------------------------------- */
/* logging                                                                   */
/* ------------------------------------------------------------------------- */

static void output_debug_string(const char *s, u64 len)
{
    register const char *x0 __asm__("x0") = s;
    register u64 x1 __asm__("x1") = len;
    __asm__ volatile("svc 0x27" : "+r"(x0), "+r"(x1) : : "memory");
}

static char *put_unsigned(char *p, char *end, u64 v, u32 base)
{
    char tmp[24];
    int n = 0;
    do {
        u32 d = (u32)(v % base);
        tmp[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10);
        v /= base;
    } while (v && n < 24);
    while (n && p < end)
        *p++ = tmp[--n];
    return p;
}

void dbg_log(const char *fmt, ...)
{
    char buf[256];
    char *p = buf, *end = buf + sizeof buf;
    const char *prefix = "[es-latam] ";
    while (*prefix)
        *p++ = *prefix++;
    va_list ap;
    va_start(ap, fmt);
    for (; *fmt && p < end; fmt++) {
        if (*fmt != '%') {
            *p++ = *fmt;
            continue;
        }
        switch (*++fmt) {
        case 'd': {
            s64 v = va_arg(ap, s32);
            if (v < 0 && p < end) {
                *p++ = '-';
                v = -v;
            }
            p = put_unsigned(p, end, (u64)v, 10);
            break;
        }
        case 'x':
            p = put_unsigned(p, end, va_arg(ap, u32), 16);
            break;
        case 'p':
            if (p + 2 < end) {
                *p++ = '0';
                *p++ = 'x';
            }
            p = put_unsigned(p, end, va_arg(ap, u64), 16);
            break;
        case 's': {
            const char *s = va_arg(ap, const char *);
            while (s && *s && p < end)
                *p++ = *s++;
            break;
        }
        default:
            if (p < end)
                *p++ = *fmt;
            if (!*fmt)
                fmt--;
        }
    }
    va_end(ap);
    output_debug_string(buf, (u64)(p - buf));
}

/* ------------------------------------------------------------------------- */
/* scripted input                                                            */
/* ------------------------------------------------------------------------- */

#define BTN_A (1u << 0)
#define BTN_B (1u << 1)
#define BTN_PLUS (1u << 10)
#define BTN_LEFT (1u << 12)
#define BTN_UP (1u << 13)
#define BTN_RIGHT (1u << 14)
#define BTN_DOWN (1u << 15)
#define BTN_STICK_LEFT (1u << 31)  /* not a hid bit: the hook turns it into stick L x = -max */

struct step {
    u16 wait;      /* polls to wait before pressing */
    u16 hold;      /* polls to hold */
    u32 buttons;
};

#define TAP(b) {40, 8, (b)}
/* A must be a short tap: the game turns a press into "decide" on release,
 * and only if it was held for less than its long-press time. */
#define PRESS_A(wait) {(wait), 3, BTN_A}
/* A on the chosen button, Up to "Yes" in the confirm prompt (the cursor
 * starts on "No"), A; then a few A taps to advance the following screens. */
#define CONFIRM_AND_ADVANCE \
    PRESS_A(90), {120, 8, BTN_UP}, PRESS_A(60), \
    PRESS_A(900), PRESS_A(300), PRESS_A(300), PRESS_A(300), PRESS_A(300)

static const struct step script[] = {
#if LATAM_DEBUG_SCRIPT == 1
    /* pick the 10th button (bottom right), answer Yes, then advance */
    {240, 8, BTN_DOWN}, TAP(BTN_DOWN), TAP(BTN_DOWN), TAP(BTN_DOWN), TAP(BTN_RIGHT),
    CONFIRM_AND_ADVANCE,
#elif LATAM_DEBUG_SCRIPT == 2
    /* pick Espanol (Espana) (right column, third row) and confirm */
    {240, 8, BTN_RIGHT}, TAP(BTN_DOWN), TAP(BTN_DOWN),
    CONFIRM_AND_ADVANCE,
#elif LATAM_DEBUG_SCRIPT == 3
    /* as 1, then keep tapping A, Left and Plus (plays through the intro:
     * dialogue, character choice and customization) */
    {240, 8, BTN_DOWN}, TAP(BTN_DOWN), TAP(BTN_DOWN), TAP(BTN_DOWN), TAP(BTN_RIGHT),
    CONFIRM_AND_ADVANCE,
#else
    {0, 0, 0},
#endif
};
#define SCRIPT_STEPS (sizeof script / sizeof script[0])
static const int script_enabled = LATAM_DEBUG_SCRIPT != 0;

static struct {
    u64 polls;          /* calls of the player-1 pad read */
    u64 grid_poll;      /* poll count when the grid was built (0 = not yet) */
    u32 logged_real;
} d;

void dbg_grid_shown(void)
{
    if (!d.grid_poll)
        d.grid_poll = d.polls ? d.polls : 1;
    DBG("grid built at poll %d (captured %d Latam records, latam %d)", (u32)d.polls,
        latam_captured(), latam_active());
}

/* Buttons the script holds at this poll (0 = none / not started). */
static u32 script_buttons(void)
{
    if (!d.grid_poll || !script_enabled)
        return 0;
    u64 t = d.polls - d.grid_poll;
    for (u32 i = 0; i < SCRIPT_STEPS; i++) {
        const struct step *s = &script[i];
        if (t < s->wait)
            return 0;
        t -= s->wait;
        if (t < s->hold)
            return s->buttons;
        t -= s->hold;
    }
    if (LATAM_DEBUG_SCRIPT == 3) {                   /* endless: A, Left, (Plus) */
        const u64 c = t % 160;
        if (c < 3)
            return BTN_A;                            /* Yes/No prompts: the default */
        if (c >= 40 && c < 48)
            return BTN_LEFT | BTN_STICK_LEFT;
        if (c >= 120 && c < 123 && (t / 160) % 2)
            return BTN_PLUS;
    }
    return 0;
}

typedef u32 (*style_fn)(const u32 *id);
typedef void (*state_fn)(void *state, const u32 *id);

/* `bl nn::hid::GetNpadStyleSet(const u32 &id)` */
void HIDDEN on_dbg_npad_style(struct regs *r)
{
    const u32 *id = (const u32 *)r->x[0];
    u32 style = ((style_fn)game(GAME_HID_GET_NPAD_STYLE_SET))(id);
    d.polls++;
    if (d.polls == 1 || d.polls % 600 == 0)
        DBG("npad: poll %d, style set %x", (u32)d.polls, style);
    if (d.grid_poll && script_enabled)
        style |= 1;                                  /* FullKey */
    r->x[0] = style;
}

/* `bl nn::hid::GetNpadState(NpadFullKeyState *, const u32 &id)`:
 * {s64 sampling; u64 buttons; s32 stick_l[2]; s32 stick_r[2]; u32 attributes} */
void HIDDEN on_dbg_npad_state(struct regs *r)
{
    u8 *state = (u8 *)r->x[0];
    ((state_fn)game(GAME_HID_GET_NPAD_STATE_FULLKEY))(state, (const u32 *)r->x[1]);
    u64 *buttons = (u64 *)(state + 8);
    u32 *attributes = (u32 *)(state + 0x20);
    if (*buttons && d.logged_real < 20) {
        d.logged_real++;
        DBG("npad: real buttons %x attributes %x", (u32)*buttons, *attributes);
    }
    if (d.grid_poll && script_enabled) {
        const u32 b = script_buttons();
        *(s64 *)state = (s64)d.polls;
        *attributes |= 1;                            /* IsConnected */
        *buttons |= b & ~BTN_STICK_LEFT;
        if (b & BTN_STICK_LEFT)
            *(s32 *)(state + 0x10) = -0x7fff;        /* stick L x */
        static u32 last;
        if (b != last)
            DBG("npad: script buttons %x at poll %d", b, (u32)(d.polls - d.grid_poll));
        last = b;
    }
}
