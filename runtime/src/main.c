/*
 * WSC VN Runtime â€” main engine
 * Target: WonderSwan Color (wswan/medium, Wonderful Toolchain)
 */

#include <ws.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#include "game_data.h"
#include "font.h"

#ifndef USE_CYGNALS
#define USE_CYGNALS 0
#endif

#ifndef CYGNALS_CALLS
#define CYGNALS_CALLS 1
#endif
#ifndef UI_SFX_TEXT
#define UI_SFX_TEXT 0xFF
#endif
#ifndef UI_SFX_CURSOR
#define UI_SFX_CURSOR 0xFF
#endif
#ifndef UI_SFX_CONFIRM
#define UI_SFX_CONFIRM 0xFF
#endif
#ifndef FONT_STYLE
#define FONT_STYLE 0
#endif

/* Fine-grained Cygnals call gates for crash isolation. */
#ifndef CYG_CALL_SETWAVE
#define CYG_CALL_SETWAVE 1
#endif
#ifndef CYG_CALL_PLAY
#define CYG_CALL_PLAY 1
#endif
#ifndef CYG_CALL_UPDATE
#define CYG_CALL_UPDATE 1
#endif
#ifndef CYG_CALL_STOP
#define CYG_CALL_STOP 1
#endif

#if USE_CYGNALS
#include "cygnals.h"
#endif

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * SAVE DATA STRUCTURES
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
#define NUM_SAVE_SLOTS        3
#define SAVE_MAGIC       0x5756u /* 'WV' */
#define SAVE_VERSION     0x0004u
#define SAVE_FLAG_WORDS  ((NUM_FLAGS > 0) ? NUM_FLAGS : 1)
#define SAVE_CG_WORDS    ((NUM_BG_ASSETS > 0) ? ((NUM_BG_ASSETS + 15) / 16) : 1)
#define BACKLOG_SIZE     6

typedef struct {
    uint16_t magic;
    uint16_t version;
    uint16_t node_id;
    uint16_t flag_count;
    int16_t  flags[SAVE_FLAG_WORDS];
    uint16_t checksum;
} save_slot_t;

typedef struct {
    uint16_t magic;
    uint16_t version;
    uint16_t build_id;
    uint16_t cg_seen[SAVE_CG_WORDS];
    save_slot_t slots[NUM_SAVE_SLOTS];
} save_store_t;

/* Acesso direto Ã  SRAM via ponteiro far â€” compatÃ­vel com wswan/medium padrÃ£o.
 * No WonderSwan a SRAM fica mapeada em CS:0x0000 com CS=0x1000. */
#define SRAM_STORE ((save_store_t __far *) MK_FP(0x1000, 0x0000))

static void vblank(void);
static void snd_update_frame(void);
static void wait_vblank_start(void);
static void wait_vblank_end(void);
static inline volatile uint16_t __wf_iram *pal_ptr(uint8_t idx);
static void snd_play(uint8_t t, uint8_t loop);
static void snd_stop(void);
static void sfx_play(uint8_t id, uint8_t loop);

static void ui_sfx_play(uint8_t id) {
    if (id != 0xFF && id < NUM_SFX) sfx_play(id, 0);
}
static void ui_sfx_text(void) { ui_sfx_play((uint8_t)UI_SFX_TEXT); }
static void ui_sfx_cursor(void) { ui_sfx_play((uint8_t)UI_SFX_CURSOR); }
static void ui_sfx_confirm(void) { ui_sfx_play((uint8_t)UI_SFX_CONFIRM); }

/* Palette cycle (BG image palette) â€” declared early because vblank() uses it. */
static uint8_t  g_palcycle_enable = 0;
static uint8_t  g_palcycle_start  = 0;
static uint8_t  g_palcycle_len    = 0;
static uint8_t  g_palcycle_speed  = 0;
static uint8_t  g_palcycle_ctr    = 0;
static uint8_t  g_palcycle_phase  = 0;
static uint16_t g_palcycle_base[16];
static uint8_t  g_blink_enabled   = 0;
static uint8_t  g_blink_closed    = 0;
static uint8_t  g_blink_ctr       = 0;
static uint8_t  g_blink_open_id   = 0xFF;
static uint8_t  g_blink_closed_id = 0xFF;
static uint8_t  g_blink_pos       = POS_NONE;
static uint8_t  g_talk_enabled    = 0;
static uint8_t  g_talk_phase      = 0;
static uint8_t  g_talk_ctr        = 0;
static uint8_t  g_talk_open       = 0;
static uint8_t  g_talk_blink_id   = 0xFF;
static uint8_t  g_fg_width_tiles  = 0;
static uint8_t  g_fg_talk_enabled = 0;
static uint8_t  g_fg_talk_patch   = 0xFF;
static uint8_t  g_fg_talk_open    = 0;
static uint8_t  g_fg_blink_enabled= 0;
static uint8_t  g_fg_blink_closed = 0;
static uint8_t  g_fg_blink_ctr    = 0;
static uint8_t  g_fg_blink_patch  = 0xFF;
static uint8_t  g_overlay_depth   = 0;
static void char_blink_update(void);
static void char_talk_tick(void);
static void char_talk_neutral(void);
static void fg_blink_update(void);

/* â”€â”€ libws port fallbacks â”€â”€ */
#ifndef IO_DISPLAY_CTRL
#define IO_DISPLAY_CTRL     0x00
#endif
#ifndef IO_LCD_LINE
#define IO_LCD_LINE         0x02
#endif
#ifndef IO_SCR_BASE
#define IO_SCR_BASE         0x07
#endif
#ifndef DISPLAY_SCR1_ENABLE
#define DISPLAY_SCR1_ENABLE 0x01
#endif
#ifndef DISPLAY_SCR2_ENABLE
#define DISPLAY_SCR2_ENABLE 0x02
#endif

/* â”€â”€ Screen layout â”€â”€ */
#define SCREEN_W    28
#define SCREEN_H    18
#define TBOX_Y      13
#define TBOX_H       5
#define TEXT_COLS   26
#define CHAR_AREA_H TBOX_Y

/* â”€â”€ Palette slots â”€â”€ */
#define PAL_BG_TOP   1
#define PAL_BG_BOT   2
#define PAL_BOX      4
#define PAL_TEXT     5
#define PAL_SPEAKER  6
#define PAL_BG_IMAGE 7
#define PAL_CHAR1    8
#define PAL_CHAR1B   9
#define PAL_CHAR2    10
#define PAL_CHAR2B   11
#define PAL_BG_IMAGE2 12
#define PAL_DECOR    13
#define FG_PAL_COUNT 8
static const uint8_t FG_PAL_SLOTS[FG_PAL_COUNT] = { 0, 3, PAL_CHAR1, PAL_CHAR1B, PAL_CHAR2, PAL_CHAR2B, 14, 15 };

/* â”€â”€ Tile indices â”€â”€ */
#define TILE_SIZE       32
#define TILE_FONT        0
#define TILE_BLANK      96
#define TILE_SOLID      97
#define TILE_FRAME_H   505
#define TILE_FRAME_V   506
#define TILE_FRAME_TL  507
#define TILE_FRAME_TR  508
#define TILE_FRAME_BL  509
#define TILE_FRAME_BR  510
#define TILE_FRAME_DOT 511
#define CHAR1_BASE     128
#define CHAR2_BASE     320
#define FG_BASE        128
#define BG_IMAGE_BASE    1

/* â”€â”€ VRAM addresses â”€â”€ */
#define TILE_BANK0_ADDR  0x4000u
#define TILE_BANK1_ADDR  0x8000u
#define SCR1_ADDR        0x3800u
#define SCR2_ADDR        0x3000u

#define TILE_BANK0_PTR  ((uint8_t  __wf_iram *) TILE_BANK0_ADDR)
#define TILE_BANK1_PTR  ((uint8_t  __wf_iram *) TILE_BANK1_ADDR)
#define SCR1_PTR        ((uint16_t __wf_iram *) SCR1_ADDR)
#define SCR2_PTR        ((uint16_t __wf_iram *) SCR2_ADDR)

/* â”€â”€ Video mode â”€â”€ */
#define PORT_SYSTEM_CTRL2         0x60
#define VMODE_COLOR_4BPP_PACKED  0xE0

/* â”€â”€ Sound â”€â”€ */

/* â”€â”€ Global game state â”€â”€ */
static int16_t  g_flags[NUM_FLAGS > 0 ? NUM_FLAGS : 1];
static uint16_t g_node = 0;
static const scene_t __far *g_last_scene = NULL;
static const char __far *g_backlog_speaker[BACKLOG_SIZE];
static const char __far *g_backlog_text[BACKLOG_SIZE];
static uint8_t g_backlog_count = 0;
static uint8_t g_backlog_pos = 0;

static const char __far TXT_TAG_PAUSE[]   = { 0x7B, 0x70, 0x61, 0x75, 0x73, 0x65, 0x7D, 0x00 };
static const char __far TXT_TAG_SFXP[]    = { 0x7B, 0x73, 0x66, 0x78, 0x3A, 0x00 }; /* "{sfx:" */
static const char __far TXT_TAG_MUSICP[]  = { 0x7B, 0x6D, 0x75, 0x73, 0x69, 0x63, 0x3A, 0x00 }; /* "{music:" */
static const char __far TXT_TAG_SPEEDP[]  = { 0x7B, 0x73, 0x70, 0x65, 0x65, 0x64, 0x3A, 0x00 }; /* "{speed:" */
static const char __far TXT_SAVE_GAME[]   = { 0x53, 0x61, 0x76, 0x65, 0x20, 0x47, 0x61, 0x6D, 0x65, 0x00 };
static const char __far TXT_LOAD_GAME[]   = { 0x4C, 0x6F, 0x61, 0x64, 0x20, 0x47, 0x61, 0x6D, 0x65, 0x00 };
static const char __far TXT_RESUME[]      = { 0x52, 0x65, 0x73, 0x75, 0x6D, 0x65, 0x00 };
static const char __far TXT_TITLE[]       = { 0x54, 0x69, 0x74, 0x6C, 0x65, 0x00 };
static const char __far TXT_SAVE_HDR[]    = { 0x20, 0x53, 0x41, 0x56, 0x45, 0x20, 0x47, 0x41, 0x4D, 0x45, 0x20, 0x00 };
static const char __far TXT_LOAD_HDR[]    = { 0x20, 0x4C, 0x4F, 0x41, 0x44, 0x20, 0x47, 0x41, 0x4D, 0x45, 0x20, 0x00 };
static const char __far TXT_SLOT[]        = { 0x53, 0x6C, 0x6F, 0x74, 0x20, 0x00 };
static const char __far TXT_EMPTY[]       = { 0x5B, 0x65, 0x6D, 0x70, 0x74, 0x79, 0x5D, 0x00 };
static const char __far TXT_OKBACK[]      = { 0x41, 0x3D, 0x4F, 0x4B, 0x20, 0x20, 0x42, 0x3D, 0x42, 0x61, 0x63, 0x6B, 0x00 };
static const char __far TXT_GAME_SAVED[]  = { 0x47, 0x61, 0x6D, 0x65, 0x20, 0x73, 0x61, 0x76, 0x65, 0x64, 0x21, 0x00 };
static const char __far TXT_THE_END[]     = { 0x2D, 0x2D, 0x2D, 0x20, 0x54, 0x48, 0x45, 0x20, 0x45, 0x4E, 0x44, 0x20, 0x2D, 0x2D, 0x2D, 0x00 };
static const char __far TXT_BACKLOG[]     = { 0x42, 0x61, 0x63, 0x6B, 0x6C, 0x6F, 0x67, 0x00 };
static const char __far TXT_NO_HISTORY[]  = { 0x4E, 0x6F, 0x20, 0x68, 0x69, 0x73, 0x74, 0x6F, 0x72, 0x79, 0x00 };
static const char __far TXT_NO_CG[]       = { 0x4E, 0x6F, 0x20, 0x43, 0x47, 0x20, 0x75, 0x6E, 0x6C, 0x6F, 0x63, 0x6B, 0x65, 0x64, 0x00 };
static const char __far TXT_NAV_BACK[]    = { 0x3C, 0x3E, 0x3D, 0x4E, 0x65, 0x78, 0x74, 0x20, 0x20, 0x42, 0x3D, 0x42, 0x61, 0x63, 0x6B, 0x00 };

static const char __far * const g_menu_items[4] = { TXT_SAVE_GAME, TXT_LOAD_GAME, TXT_TITLE, TXT_RESUME };
static uint8_t g_tile_util[TILE_SIZE];
static uint8_t g_choice_vm[4];
static const char __far *g_choice_vt[4];

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * SAVE LOGIC
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */

/* Alterado para aceitar ponteiro __far pois os dados estÃ£o na SRAM */
static uint16_t save_checksum(const save_slot_t __far *slot) {
    uint16_t sum = 0xA55Au;
    sum ^= slot->magic;
    sum ^= slot->version;
    sum ^= slot->node_id;
    sum ^= slot->flag_count;
    for (uint16_t i = 0; i < SAVE_FLAG_WORDS; i++) sum ^= (uint16_t)slot->flags[i];
    return sum;
}

static void save_store_init(void) {
    save_store_t __far *ss = SRAM_STORE;
    if (ss->magic != SAVE_MAGIC || ss->version != SAVE_VERSION || ss->build_id != BUILD_ID) {
        uint8_t __far *p = (uint8_t __far *)ss;
        for (uint16_t i = 0; i < (uint16_t)sizeof(save_store_t); i++) p[i] = 0;
        ss->magic = SAVE_MAGIC;
        ss->version = SAVE_VERSION;
        ss->build_id = BUILD_ID;
    }
}

static bool save_slot_valid(uint8_t slot) {
    if (slot >= NUM_SAVE_SLOTS) return false;
    const save_slot_t __far *s = &SRAM_STORE->slots[slot];
    if (s->magic != SAVE_MAGIC || s->version != SAVE_VERSION) return false;
    if (s->flag_count > SAVE_FLAG_WORDS) return false;
    return s->checksum == save_checksum(s);
}

static void save_slot_write(uint8_t slot, uint16_t node_id) {
    if (slot >= NUM_SAVE_SLOTS) return;
    save_slot_t __far *s = &SRAM_STORE->slots[slot];
    s->magic = SAVE_MAGIC;
    s->version = SAVE_VERSION;
    s->node_id = node_id;
    s->flag_count = (uint16_t)NUM_FLAGS;
    for (uint16_t i = 0; i < SAVE_FLAG_WORDS; i++)
        s->flags[i] = (i < NUM_FLAGS) ? g_flags[i] : 0;
    s->checksum = save_checksum(s);
}

static uint16_t save_slot_load(uint8_t slot) {
    if (slot >= NUM_SAVE_SLOTS || !save_slot_valid(slot)) return 0;
    const save_slot_t __far *s = &SRAM_STORE->slots[slot];
    for (uint16_t i = 0; i < NUM_FLAGS; i++) g_flags[i] = 0;
    for (uint16_t i = 0; i < s->flag_count && i < NUM_FLAGS; i++)
        g_flags[i] = s->flags[i];
    return s->node_id;
}

static void cg_mark_seen(uint8_t bg_id) {
    if (bg_id >= NUM_BG_ASSETS) return;
    SRAM_STORE->cg_seen[bg_id >> 4] |= (uint16_t)(1u << (bg_id & 15));
}

static bool cg_is_seen(uint8_t bg_id) {
    if (bg_id >= NUM_BG_ASSETS) return false;
    return (SRAM_STORE->cg_seen[bg_id >> 4] & (uint16_t)(1u << (bg_id & 15))) != 0;
}

static void backlog_add(const char __far *speaker, const char __far *text) {
    if (!text || !text[0]) return;
    g_backlog_speaker[g_backlog_pos] = speaker;
    g_backlog_text[g_backlog_pos] = text;
    g_backlog_pos = (uint8_t)((g_backlog_pos + 1) % BACKLOG_SIZE);
    if (g_backlog_count < BACKLOG_SIZE) g_backlog_count++;
}

static uint8_t backlog_slot_for_view(uint8_t view) {
    uint8_t oldest = (g_backlog_count < BACKLOG_SIZE) ? 0 : g_backlog_pos;
    return (uint8_t)((oldest + view) % BACKLOG_SIZE);
}

static void overlay_begin(void) {
    if (g_overlay_depth < 255) g_overlay_depth++;
}

static void overlay_end(void) {
    if (g_overlay_depth) g_overlay_depth--;
}

static void reset_new_game_state(void) {
    for (uint8_t i = 0; i < NUM_FLAGS; i++) {
        g_flags[i] = FLAG_INITIAL_VALUES[i];
    }
    g_backlog_count = 0;
    g_backlog_pos = 0;
    for (uint8_t i = 0; i < BACKLOG_SIZE; i++) {
        g_backlog_speaker[i] = NULL;
        g_backlog_text[i] = NULL;
    }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * INPUT & VIDEO HELPERS (Simplificado)
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
#define PORT_KEYPAD  0xB5
#define _K_X1  0x0001u
#define _K_X2  0x0002u
#define _K_X3  0x0004u
#define _K_X4  0x0008u
#define _K_START 0x0200u
#define _K_A   0x0400u
#define _K_B   0x0800u
#define KEY_UP    _K_X1
#define KEY_DOWN  _K_X3
#define KEY_LEFT  _K_X4
#define KEY_RIGHT _K_X2
#define KEY_A     _K_A
#define KEY_B     _K_B
#define KEY_START _K_START

static uint16_t k_cur = 0, k_prev = 0, k_new = 0;

static void read_keys(void) {
    k_prev = k_cur;
    k_cur  = 0;
    outportb(PORT_KEYPAD, 0x20);
    uint8_t xpad = inportb(PORT_KEYPAD) & 0x0F;
    if (xpad & 0x01) k_cur |= _K_X1;
    if (xpad & 0x02) k_cur |= _K_X2;
    if (xpad & 0x04) k_cur |= _K_X3;
    if (xpad & 0x08) k_cur |= _K_X4;
    outportb(PORT_KEYPAD, 0x40);
    uint8_t btn = inportb(PORT_KEYPAD) & 0x0F;
    if (btn & 0x02) k_cur |= _K_START;
    if (btn & 0x04) k_cur |= _K_A;
    if (btn & 0x08) k_cur |= _K_B;
    k_new = k_cur & ~k_prev;
}
static bool pressed(uint16_t m) { return (k_new & m) != 0; }

static void wait_key_release(void) {
    while (1) {
        vblank();
        read_keys();
        if (k_cur == 0) {
            k_prev = 0;
            k_new = 0;
            return;
        }
    }
}

static void vblank(void) {
    wait_vblank_start();
    snd_update_frame();
    if (!g_overlay_depth) { char_blink_update(); fg_blink_update(); }
    /* Palette cycle runs during VBlank to avoid tearing. */
    if (g_palcycle_enable && g_palcycle_len >= 2 && g_palcycle_speed) {
        g_palcycle_ctr++;
        if (g_palcycle_ctr >= g_palcycle_speed) {
            g_palcycle_ctr = 0;
            g_palcycle_phase++;
            if (g_palcycle_phase >= g_palcycle_len) g_palcycle_phase = 0;
            volatile uint16_t __wf_iram *p = pal_ptr(PAL_BG_IMAGE);
            for (uint8_t i = 0; i < g_palcycle_len; i++) {
                uint8_t src = (uint8_t)((i + g_palcycle_phase) % g_palcycle_len);
                p[g_palcycle_start + i] = g_palcycle_base[g_palcycle_start + src];
            }
        }
    }
    wait_vblank_end();
}

static void wait_vblank_start(void) {
    while (inportb(IO_LCD_LINE) != 144) {}
}

static void wait_vblank_end(void) {
    while (inportb(IO_LCD_LINE) == 144) {}
}

static uint16_t rgb24_to_wsc(uint32_t c24) {
    uint8_t r = (uint8_t)((c24 >> 20) & 0xF);
    uint8_t g = (uint8_t)((c24 >> 12) & 0xF);
    uint8_t b = (uint8_t)((c24 >>  4) & 0xF);
    /* WSC palette is RGB444: bits 11-8=R, 7-4=G, 3-0=B. */
    return ((uint16_t)r << 8) | ((uint16_t)g << 4) | b;
}
#define col rgb24_to_wsc

static inline volatile uint16_t __wf_iram *pal_ptr(uint8_t idx) {
    return (volatile uint16_t __wf_iram *)(0xFE00u + (uint16_t)idx * 32u);
}
static void set_pal16(uint8_t idx, const uint16_t __far *src) {
    volatile uint16_t __wf_iram *p = pal_ptr(idx);
    for (uint8_t i = 0; i < 16; i++) p[i] = src[i];
}
static void set_pal2(uint8_t idx, uint16_t c0, uint16_t c1) {
    volatile uint16_t __wf_iram *p = pal_ptr(idx);
    p[0] = c0; p[1] = c1;
}

/* â”€â”€ Palette-based transitions â”€â”€ */
#define TRANSITION_NONE  0
#define TRANSITION_FADE  1
#define TRANSITION_FLASH 6

static uint16_t g_trans_pal[256];

static inline uint16_t scale_rgb444(uint16_t c, uint8_t num, uint8_t den) {
    uint8_t r = (uint8_t)((c >> 8) & 0x0F);
    uint8_t g = (uint8_t)((c >> 4) & 0x0F);
    uint8_t b = (uint8_t)((c >> 0) & 0x0F);
    r = (uint8_t)((r * num) / den);
    g = (uint8_t)((g * num) / den);
    b = (uint8_t)((b * num) / den);
    return (uint16_t)((r << 8) | (g << 4) | b);
}

static void pal_snapshot(uint16_t out256[256]) {
    volatile uint16_t __wf_iram *p = (volatile uint16_t __wf_iram *)0xFE00u;
    for (uint16_t i = 0; i < 256; i++) out256[i] = p[i];
}

static void pal_apply_scaled(const uint16_t src256[256], uint8_t num, uint8_t den) {
    volatile uint16_t __wf_iram *p = (volatile uint16_t __wf_iram *)0xFE00u;
    for (uint16_t i = 0; i < 256; i++) p[i] = scale_rgb444(src256[i], num, den);
}

static void transition_fade_to_black(uint8_t steps) {
    pal_snapshot(g_trans_pal);
    for (uint8_t s = steps; s > 0; s--) {
        wait_vblank_start();
        pal_apply_scaled(g_trans_pal, (uint8_t)(s - 1), steps);
        snd_update_frame();
        wait_vblank_end();
    }
}

static void transition_fade_from_black(uint8_t steps) {
    for (uint8_t s = 0; s <= steps; s++) {
        wait_vblank_start();
        pal_apply_scaled(g_trans_pal, s, steps);
        snd_update_frame();
        wait_vblank_end();
    }
}

static void transition_flash_white(uint8_t frames) {
    pal_snapshot(g_trans_pal);
    for (uint8_t f = 0; f < frames; f++) {
        wait_vblank_start();
        volatile uint16_t __wf_iram *p = (volatile uint16_t __wf_iram *)0xFE00u;
        for (uint16_t i = 0; i < 256; i++) p[i] = 0x0FFF;
        snd_update_frame();
        wait_vblank_end();
    }
    /* Restore current palette snapshot in the next vblank. */
    wait_vblank_start();
    volatile uint16_t __wf_iram *p = (volatile uint16_t __wf_iram *)0xFE00u;
    for (uint16_t i = 0; i < 256; i++) p[i] = g_trans_pal[i];
    snd_update_frame();
    wait_vblank_end();
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * ENGINE LOGIC (Visuals, Text, Flags)
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
static uint32_t g_bg_col1     = 0x1A1040UL;
static uint32_t g_bg_col2     = 0x2A1060UL;
static uint32_t g_speaker_col = 0xFF3366UL;
static uint8_t  g_tb_style    = 0;
static uint8_t  g_music       = 0xFF;
static uint8_t  g_music_step  = 0;
static uint16_t g_music_acc   = 0;
static uint8_t  g_music_loop  = 1;
/* Place wavetable at the hardware-standard WS wavetable RAM address (0x0EC0). */
__attribute__((section(".iramx_0ec0.wave"))) static ws_sound_wavetable_t g_wavetable __attribute__((aligned(64)));

#if USE_CYGNALS
static music_state_t g_cyg_song_state __attribute__ ((aligned (2)));
static channel_t g_cyg_song_channels[4] __attribute__ ((aligned (2)));
#endif

/* Cache which assets are currently uploaded into VRAM tile memory. */
static uint8_t g_loaded_bg_id = 0xFE;     /* 0..NUM_BG_ASSETS-1, 0xFF none, 0xFE unknown */
static uint8_t g_loaded_fg_id = 0xFE;     /* 0..NUM_FG_ASSETS-1, 0xFF none, 0xFE unknown */
static uint8_t g_loaded_char1 = 0xFE;     /* 0..NUM_CHAR_ASSETS-1, 0xFF none, 0xFE unknown */
static uint8_t g_loaded_char2 = 0xFE;

/* SFX playback (sound DMA to channel 2 voice mode). */
static uint16_t g_sfx_frames_left = 0;
static uint8_t  g_sfx_looping = 0;

static void apply_scene_theme(const scene_t __far *s) {
    g_bg_col1     = s ? s->bg_color1     : 0x1A1040UL;
    g_bg_col2     = s ? s->bg_color2     : 0x2A1060UL;
    g_speaker_col = s ? s->speaker_color : 0xFF3366UL;
    g_tb_style    = s ? s->tb_style      : 0;
}

static void wtb0(uint16_t tile, const uint8_t *src) {
    uint8_t __wf_iram *dst = TILE_BANK0_PTR + (uint32_t)tile * TILE_SIZE;
    for (uint16_t i = 0; i < TILE_SIZE; i++) dst[i] = src[i];
}
static void wtb0f(uint16_t tile, const uint8_t __far *src) {
    uint8_t __wf_iram *dst = TILE_BANK0_PTR + (uint32_t)tile * TILE_SIZE;
    for (uint16_t i = 0; i < TILE_SIZE; i++) dst[i] = src[i];
}
static void wtb1f(uint16_t tile, const uint8_t __far *src) {
    uint8_t __wf_iram *dst = TILE_BANK1_PTR + (uint32_t)tile * TILE_SIZE;
    for (uint16_t i = 0; i < TILE_SIZE; i++) dst[i] = src[i];
}
static void make_blank(uint16_t tile) {
    for (uint8_t i=0;i<TILE_SIZE;i++) g_tile_util[i]=0x00; 
    wtb0(tile,g_tile_util);
}
static void make_solid_b0(uint16_t tile) {
    for (uint8_t i=0;i<TILE_SIZE;i++) g_tile_util[i]=0x11; 
    wtb0(tile,g_tile_util);
}
static void make_solid_b1(uint16_t tile) {
    uint8_t t[TILE_SIZE]; for (uint8_t i=0;i<TILE_SIZE;i++) t[i]=0x11;
    uint8_t __wf_iram *dst = TILE_BANK1_PTR + (uint32_t)tile * TILE_SIZE;
    for (uint16_t i=0;i<TILE_SIZE;i++) dst[i]=t[i];
}
static void make_frame_tile_b1(uint16_t tile, uint8_t kind) {
    uint8_t t[TILE_SIZE];
    for (uint8_t i=0;i<TILE_SIZE;i++) t[i]=0x00;
    for (uint8_t y=0;y<8;y++) {
        for (uint8_t x=0;x<8;x++) {
            bool on = false;
            switch (kind) {
                case 0: on = (y == 1 || y == 6); break;
                case 1: on = (x == 1 || x == 6); break;
                case 2: on = ((x == 1 && y >= 1) || (y == 1 && x >= 1) || (x == 2 && y == 2)); break;
                case 3: on = ((x == 6 && y >= 1) || (y == 1 && x <= 6) || (x == 5 && y == 2)); break;
                case 4: on = ((x == 1 && y <= 6) || (y == 6 && x >= 1) || (x == 2 && y == 5)); break;
                case 5: on = ((x == 6 && y <= 6) || (y == 6 && x <= 6) || (x == 5 && y == 5)); break;
                case 6: on = ((x == 3 || x == 4) && y >= 1 && y <= 6) ||
                             ((y == 3 || y == 4) && x >= 1 && x <= 6) ||
                             ((x == y || x + y == 7) && x >= 2 && x <= 5); break;
            }
            if (on) {
                uint8_t bi = (uint8_t)(y * 4 + (x >> 1));
                if (x & 1) t[bi] |= 0x01;
                else t[bi] |= 0x10;
            }
        }
    }
    uint8_t __wf_iram *dst = TILE_BANK1_PTR + (uint32_t)tile * TILE_SIZE;
    for (uint16_t i=0;i<TILE_SIZE;i++) dst[i]=t[i];
}

static void load_font(void) {
    for (int g = 0; g < 96; g++) {
        const uint8_t __far *src = FONT_DATA + (g * 8);
        for (uint8_t y = 0; y < 8; y++) {
            uint8_t row = src[y];
            for (uint8_t x = 0; x < 4; x++) {
                uint8_t pl = (row & (0x80>>(x*2+0))) ? 1 : 0;
                uint8_t pr = (row & (0x80>>(x*2+1))) ? 1 : 0;
                g_tile_util[y*4+x] = (uint8_t)((pl<<4)|pr);
            }
        }
        wtb0(TILE_FONT + g, g_tile_util);
    }
}

static inline uint16_t cell(uint16_t tile, uint8_t pal, bool b1) {
    uint16_t v = (tile & 0x01FF) | ((uint16_t)(pal&0x0F) << 9);
    if (b1) v |= 0x2000;
    return v;
}
static void put_cell(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, uint16_t tile, uint8_t pal, bool b1) {
    scr[(uint16_t)y*32+x] = cell(tile, pal, b1);
}
static void fill_region(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t tile, uint8_t pal, bool b1) {
    uint16_t v = cell(tile, pal, b1);
    for (uint8_t dy=0;dy<h;dy++)
        for (uint8_t dx=0;dx<w;dx++)
            scr[(uint16_t)(y+dy)*32+(x+dx)] = v;
}
static void clear_screen(uint16_t __wf_iram *scr) {
    uint16_t v = cell(TILE_BLANK, PAL_TEXT, false);
    for (uint16_t i=0;i<32u*32u;i++) scr[i]=v;
}

static void render_image_bg_on(uint16_t __wf_iram *scr, const image_asset_t __far *a) {
    for (uint8_t y=0;y<SCREEN_H;y++) {
        uint8_t p = (y<SCREEN_H/2) ? PAL_BG_TOP : PAL_BG_BOT;
        for (uint8_t x=0;x<SCREEN_W;x++) put_cell(scr,x,y,0,p,true);
    }
    if (!a || a->tile_count==0) return;
    for (uint16_t i=0;i<a->tile_count;i++)
        wtb1f(BG_IMAGE_BASE+i, a->tiles+(uint32_t)i*TILE_SIZE);
    for (uint8_t ty=0;ty<a->height_tiles&&ty<SCREEN_H;ty++)
        for (uint8_t tx=0;tx<a->width_tiles&&tx<SCREEN_W;tx++) {
            uint16_t tile_idx = (uint16_t)ty*a->width_tiles+tx;
            uint8_t pal = PAL_BG_IMAGE;
            if (a->palette2 && a->tile_pals && a->tile_pals[tile_idx]) pal = PAL_BG_IMAGE2;
            put_cell(scr,tx,ty, BG_IMAGE_BASE+tile_idx, pal,true);
        }
}
static void render_image_bg_map_on(uint16_t __wf_iram *scr, const image_asset_t __far *a) {
    for (uint8_t y=0;y<SCREEN_H;y++) {
        uint8_t p = (y<SCREEN_H/2) ? PAL_BG_TOP : PAL_BG_BOT;
        for (uint8_t x=0;x<SCREEN_W;x++) put_cell(scr,x,y,0,p,true);
    }
    if (!a || a->tile_count==0) return;
    for (uint8_t ty=0;ty<a->height_tiles&&ty<SCREEN_H;ty++)
        for (uint8_t tx=0;tx<a->width_tiles&&tx<SCREEN_W;tx++) {
            uint16_t tile_idx = (uint16_t)ty*a->width_tiles+tx;
            uint8_t pal = PAL_BG_IMAGE;
            if (a->palette2 && a->tile_pals && a->tile_pals[tile_idx]) pal = PAL_BG_IMAGE2;
            put_cell(scr,tx,ty, BG_IMAGE_BASE+tile_idx, pal,true);
        }
}
static void render_image_bg(const image_asset_t __far *a) {
    set_pal2(PAL_BG_TOP, 0x0111, col(g_bg_col1));
    set_pal2(PAL_BG_BOT, 0x0111, col(g_bg_col2));
    if (a && a->tile_count) set_pal16(PAL_BG_IMAGE, a->palette);
    if (a && a->palette2) set_pal16(PAL_BG_IMAGE2, a->palette2);
    render_image_bg_on(SCR1_PTR, a);
}
static void clear_char_layer(void) {
    fill_region(SCR2_PTR,0,0,SCREEN_W,SCREEN_H,TILE_BLANK,PAL_TEXT,false);
}
static void render_char_map_clipped(const image_asset_t __far *a, uint8_t pos, uint8_t pal_slot, uint16_t base_tile, uint8_t max_y) {
    if (!a||a->tile_count==0||pos==POS_NONE) return;
    uint8_t x=0;
    if (pos==POS_CENTER) x=(SCREEN_W>a->width_tiles)?(uint8_t)((SCREEN_W-a->width_tiles)/2):0;
    else if (pos==POS_RIGHT) x=(SCREEN_W>a->width_tiles)?(uint8_t)(SCREEN_W-a->width_tiles):0;
    /* Ã‚ncora na base â€” evita underflow se sprite > CHAR_AREA_H */
    uint8_t y = (a->height_tiles < SCREEN_H) ? (uint8_t)(SCREEN_H - a->height_tiles) : 0;
    set_pal16(pal_slot, a->palette);
    if (a->palette2) set_pal16((uint8_t)(pal_slot + 1), a->palette2);
    if (max_y > SCREEN_H) max_y = SCREEN_H;
    for (uint8_t ty=0;ty<a->height_tiles&&(y+ty)<max_y;ty++)
        for (uint8_t tx=0;tx<a->width_tiles&&(x+tx)<SCREEN_W;tx++) {
            uint16_t tile_idx = (uint16_t)ty*a->width_tiles+tx;
            uint8_t use_pal = pal_slot;
            if (a->palette2 && a->tile_pals && a->tile_pals[tile_idx]) use_pal = (uint8_t)(pal_slot + 1);
            put_cell(SCR2_PTR,x+tx,y+ty, base_tile+tile_idx, use_pal,false);
        }
}

static void render_char_map(const image_asset_t __far *a, uint8_t pos, uint8_t pal_slot, uint16_t base_tile) {
    render_char_map_clipped(a, pos, pal_slot, base_tile, SCREEN_H);
}

static void render_foreground_map(const image_asset_t __far *a) {
    if (!a || a->tile_count == 0) return;
    uint8_t pal_count = 0;
    if (a->palettes && a->palette_count) {
        pal_count = a->palette_count;
        if (pal_count > FG_PAL_COUNT) pal_count = FG_PAL_COUNT;
        for (uint8_t i = 0; i < pal_count; i++) set_pal16(FG_PAL_SLOTS[i], a->palettes[i]);
    } else {
        set_pal16(PAL_CHAR1, a->palette);
        if (a->palette2) set_pal16(PAL_CHAR1B, a->palette2);
    }
    for (uint8_t ty=0; ty<a->height_tiles && ty<TBOX_Y; ty++) {
        for (uint8_t tx=0; tx<a->width_tiles && tx<SCREEN_W; tx++) {
            uint16_t tile_idx = (uint16_t)ty*a->width_tiles + tx;
            uint8_t pal = PAL_CHAR1;
            if (pal_count) {
                uint8_t pi = a->tile_pals ? a->tile_pals[tile_idx] : 0;
                if (pi >= pal_count) pi = 0;
                pal = FG_PAL_SLOTS[pi];
            } else if (a->palette2 && a->tile_pals && a->tile_pals[tile_idx]) {
                pal = PAL_CHAR1B;
            }
            put_cell(SCR2_PTR, tx, ty, FG_BASE + tile_idx, pal, false);
        }
    }
}

static void fg_apply_anim_patch(uint8_t patch_id, uint8_t alt) {
    if (patch_id == 0xFF || patch_id >= NUM_FG_ANIM_PATCHES || !g_fg_width_tiles) return;
    const fg_anim_patch_t __far *p = &FG_ANIM_PATCHES[patch_id];
    for (uint16_t i = 0; i < p->count; i++) {
        const fg_anim_tile_t __far *it = &p->tiles[i];
        const uint8_t __far *tile = alt ? it->alt_tile : it->base_tile;
        uint8_t pal_idx = alt ? it->alt_pal : it->base_pal;
        if (pal_idx >= FG_PAL_COUNT) pal_idx = 0;
        wtb0f(FG_BASE + it->tile_idx, tile);
        uint8_t x = (uint8_t)(it->tile_idx % g_fg_width_tiles);
        uint8_t y = (uint8_t)(it->tile_idx / g_fg_width_tiles);
        if (x < SCREEN_W && y < TBOX_Y) put_cell(SCR2_PTR, x, y, FG_BASE + it->tile_idx, FG_PAL_SLOTS[pal_idx], false);
    }
}

static void fg_talk_show(uint8_t open) {
    if (!g_fg_talk_enabled || g_fg_talk_patch >= NUM_FG_ANIM_PATCHES) return;
    if (g_fg_talk_open == open) return;
    fg_apply_anim_patch(g_fg_talk_patch, open);
    g_fg_talk_open = open;
}

static void fg_talk_neutral(void) {
    if (!g_fg_talk_enabled) return;
    fg_talk_show(0);
}

static void fg_blink_show(uint8_t closed) {
    if (!g_fg_blink_enabled || g_fg_blink_patch >= NUM_FG_ANIM_PATCHES) return;
    fg_apply_anim_patch(g_fg_blink_patch, closed);
    g_fg_blink_closed = closed;
}

static void fg_blink_update(void) {
    if (!g_fg_blink_enabled) return;
    g_fg_blink_ctr++;
    if (!g_fg_blink_closed) {
        if (g_fg_blink_ctr >= 150) {
            g_fg_blink_ctr = 0;
            fg_blink_show(1);
        }
    } else if (g_fg_blink_ctr >= 8) {
        g_fg_blink_ctr = 0;
        fg_blink_show(0);
    }
}

static void decorate_box(uint8_t style) {
    switch (style) {
        case 6:
        case 9:
        case 10:
            put_cell(SCR1_PTR, 0, TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, 0, (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            break;
        case 7:
            put_cell(SCR1_PTR, 0, TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, 0, (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            break;
        case 8:
            put_cell(SCR1_PTR, 0, TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), TBOX_Y, TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, 0, (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            put_cell(SCR1_PTR, (uint8_t)(SCREEN_W - 1), (uint8_t)(TBOX_Y + TBOX_H - 1), TILE_FRAME_DOT, PAL_DECOR, true);
            break;
        default:
            break;
    }
}

static bool tb_style_has_decor(void) {
    return g_tb_style >= 6 && g_tb_style <= 10;
}

static bool tb_style_has_frame(void) {
    return false;
}

static uint8_t tb_text_lines(void) {
    return tb_style_has_frame() ? (TBOX_H - 2) : (TBOX_H - 1);
}

static uint8_t tb_text_cols(void) {
    return tb_style_has_frame() ? (TEXT_COLS - 1) : TEXT_COLS;
}

static void decorate_speaker_box(uint8_t w) {
    if (!tb_style_has_decor() || w < 5) return;
    uint8_t y = (uint8_t)(TBOX_Y - 1);
    put_cell(SCR1_PTR, 1, y, TILE_FRAME_DOT, PAL_DECOR, true);
}

static void char_blink_show(uint8_t closed) {
    if (!g_blink_enabled || g_blink_open_id >= NUM_CHAR_ASSETS || g_blink_closed_id >= NUM_CHAR_ASSETS) return;
    const image_asset_t __far *a = closed ? &CHAR_ASSETS[g_blink_closed_id] : &CHAR_ASSETS[g_blink_open_id];
    render_char_map_clipped(a, g_blink_pos, PAL_CHAR1, closed ? CHAR2_BASE : CHAR1_BASE, (uint8_t)(TBOX_Y - 1));
    g_blink_closed = closed;
}

static void char_blink_update(void) {
    if (!g_blink_enabled) return;
    g_blink_ctr++;
    if (!g_blink_closed) {
        if (g_blink_ctr >= 150) {
            g_blink_ctr = 0;
            char_blink_show(1);
        }
    } else if (g_blink_ctr >= 8) {
        g_blink_ctr = 0;
        char_blink_show(0);
    }
}

static void char_talk_show(uint8_t open) {
    if (!g_talk_enabled || g_blink_open_id >= NUM_CHAR_ASSETS || g_blink_closed_id >= NUM_CHAR_ASSETS) return;
    if (g_talk_open == open) return;
    const image_asset_t __far *a = open ? &CHAR_ASSETS[g_blink_closed_id] : &CHAR_ASSETS[g_blink_open_id];
    render_char_map_clipped(a, g_blink_pos, PAL_CHAR1, open ? CHAR2_BASE : CHAR1_BASE, (uint8_t)(TBOX_Y - 1));
    g_talk_open = open;
}

static void char_talk_tick(void) {
    if (!g_talk_enabled && !g_fg_talk_enabled) return;
    if (++g_talk_ctr < 3) return;
    g_talk_ctr = 0;
    g_talk_phase = (uint8_t)((g_talk_phase + 1) & 3);
    uint8_t open = (g_talk_phase == 1 || g_talk_phase == 2) ? 1 : 0;
    char_talk_show(open);
    fg_talk_show(open);
}

static void char_talk_neutral(void) {
    g_talk_phase = 0;
    g_talk_ctr = 0;
    char_talk_show(0);
    fg_talk_neutral();
}

static void char_talk_finish(bool final_page) {
    char_talk_neutral();
    if (final_page && g_talk_enabled && g_talk_blink_id != 0xFF && g_talk_blink_id < NUM_CHAR_ASSETS) {
        const image_asset_t __far *blink = &CHAR_ASSETS[g_talk_blink_id];
        wait_vblank_start();
        for (uint16_t i=0;i<blink->tile_count;i++) wtb0f(CHAR2_BASE+i, blink->tiles+(uint32_t)i*TILE_SIZE);
        g_loaded_char2 = g_talk_blink_id;
        g_blink_closed_id = g_talk_blink_id;
        g_blink_closed = 0;
        g_blink_ctr = 0;
        g_blink_enabled = 1;
    }
    if (final_page && g_fg_talk_enabled && g_fg_blink_patch != 0xFF && g_fg_blink_patch < NUM_FG_ANIM_PATCHES) {
        g_fg_blink_enabled = 1;
        g_fg_blink_closed = 0;
        g_fg_blink_ctr = 0;
    }
    if (final_page) {
        g_talk_enabled = 0;
        g_talk_blink_id = 0xFF;
        g_fg_talk_enabled = 0;
    }
}

static void draw_box(void) {
    wait_vblank_start();
    uint16_t bg=0x0111,tx0=0x0000,tx1=0x0FFF, decor0=0x0111, decor1=0x0FFF;
    uint16_t sp0=0x0000, sp1=col(g_speaker_col);
    bool no_box = false;
    switch (g_tb_style) {
        case 1: bg=0x0222;tx1=0x0FFF;break;
        case 2: bg=0x0111;tx1=0x0FFF;break;
        case 3: bg=0x0EEE;tx1=0x0000;sp1=0x0000;break;
        case 4: no_box = true; tx1=0x0FFF; break;
        case 5: bg=0x0144;tx1=0x0FFF;break;
        case 6: bg=0x0111;tx1=0x0FFF;decor0=0x0111;decor1=0x0CCC;break;
        case 7: bg=0x0111;tx1=0x0FFF;decor0=0x0111;decor1=0x0FE8;break;
        case 8: bg=0x0111;tx1=0x0FFF;decor0=0x0111;decor1=0x0F7B;break;
        case 9: bg=0x0122;tx1=0x0EEF;decor0=0x0122;decor1=sp1;break;
        case 10: bg=0x0112;tx1=0x0FFF;decor0=0x0112;decor1=sp1;break;
        default: bg=0x0111;tx1=0x0FFF;break;
    }
    if (g_tb_style >= 6 && g_tb_style <= 10) decor1 = sp1;
    set_pal2(PAL_BOX,bg,bg);
    set_pal2(PAL_TEXT,tx0,tx1);
    set_pal2(PAL_SPEAKER,sp0,sp1);
    set_pal2(PAL_DECOR,decor0,decor1);
    if (!no_box) fill_region(SCR1_PTR,0,TBOX_Y,SCREEN_W,TBOX_H,0,PAL_BOX,true);
    if (!no_box) decorate_box(g_tb_style);
    if (!no_box) fill_region(SCR2_PTR,0,TBOX_Y,SCREEN_W,TBOX_H,TILE_BLANK,PAL_TEXT,false);
}

static void glyph(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, char c, uint8_t pal) {
    if ((uint8_t)c < 32 || (uint8_t)c > 127) c = ' ';
    put_cell(scr,x,y,TILE_FONT+((uint8_t)c-32),pal,false);
}
static uint8_t far_strlen(const char __far *s) {
    uint8_t l = 0;
    while (s && *s) { l++; s++; }
    return l;
}
static void text_on(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, const char __far *s, uint8_t pal) {
    while (s && *s) glyph(scr, x++, y, *s++, pal);
}
static void text_on_clip(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, const char __far *s, uint8_t pal, uint8_t max) {
    while (s && *s && max--) glyph(scr, x++, y, *s++, pal);
}
static bool dialogue_blip_allowed(const char __far *speaker) {
    if (!speaker || !speaker[0]) return false;
    if (speaker[0]=='R'&&speaker[1]=='y'&&speaker[2]=='u'&&speaker[3]=='i'&&speaker[4]=='c'&&speaker[5]=='h'&&speaker[6]=='i'&&speaker[7]==0) return false;
    return true;
}
static void draw_speaker_box(const char __far *speaker) {
    if (!speaker || !speaker[0] || g_tb_style == 4) return;
    uint8_t len = far_strlen(speaker);
    if (len > 16) len = 16;
    uint8_t decorated = tb_style_has_decor() ? 1 : 0;
    uint8_t w = (uint8_t)(len + (decorated ? 3 : 2));
    if (w > SCREEN_W - 2) w = SCREEN_W - 2;
    fill_region(SCR1_PTR,1,(uint8_t)(TBOX_Y-1),w,1,0,PAL_BOX,true);
    decorate_speaker_box(w);
    fill_region(SCR2_PTR,1,(uint8_t)(TBOX_Y-1),w,1,TILE_BLANK,PAL_TEXT,false);
    text_on_clip(SCR2_PTR,(uint8_t)(decorated ? 3 : 2),(uint8_t)(TBOX_Y-1),speaker,PAL_SPEAKER,len);
}
static void textf(uint8_t x, uint8_t y, const char __far *s, uint8_t pal) {
    while (*s) glyph(SCR2_PTR,x++,y,*s++,pal);
}
static void put_num_on(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, uint16_t v, uint8_t pal) {
    char buf[4]; buf[3]=0;
    buf[2]=(char)('0'+(v%10)); v/=10;
    buf[1]=(char)('0'+(v%10)); v/=10;
    buf[0]=(char)('0'+(v%10));
    uint8_t start = (buf[0]=='0') ? ((buf[1]=='0') ? 2 : 1) : 0;
    for (uint8_t i = start; i < 3; i++) glyph(scr, x++, y, buf[i], pal);
}

static bool match_tag(const char __far *p, const char __far *tag) {
    uint8_t i=0; while(tag[i]){if(p[i]!=tag[i])return false;i++;} return true;
}

static void text_wrap_on(uint16_t __wf_iram *scr, uint8_t x, uint8_t y, uint8_t w, uint8_t lines, const char __far *s, uint8_t pal) {
    uint8_t cx = 0, cy = 0;
    while (s && *s && cy < lines) {
        if (*s == '{') {
            while (*s && *s != '}') s++;
            if (*s == '}') s++;
            continue;
        }
        if (*s == '\n') { cx = 0; cy++; s++; continue; }
        glyph(scr, (uint8_t)(x + cx), (uint8_t)(y + cy), *s++, pal);
        cx++;
        if (cx >= w) { cx = 0; cy++; }
    }
}

static const char __far *parse_u8_until_rbrace(const char __far *p, uint8_t *out) {
    uint16_t v = 0;
    bool any = false;
    while (*p >= '0' && *p <= '9') {
        any = true;
        v = (uint16_t)(v * 10u + (uint16_t)(*p - '0'));
        if (v > 255u) v = 255u;
        p++;
    }
    if (!any || *p != '}') return NULL;
    *out = (uint8_t)v;
    return p + 1;
}

static const char __far *show_text_block(const char __far *speaker, const char __far *text, uint8_t speed) {
    draw_box();
    draw_speaker_box(speaker);
    uint8_t cx=1,cy=TBOX_Y+1,col_=0,line=0;
    const uint8_t maxl=tb_text_lines();
    const uint8_t maxc=tb_text_cols();
    const char __far *p=text;
    const char __far *after=NULL;
    uint8_t cur_speed = speed;
    uint8_t blip_ctr = 0;
    bool text_blip = dialogue_blip_allowed(speaker);
    while (*p&&line<maxl) {
        if (*p=='{') {
            if (match_tag(p, TXT_TAG_PAUSE)) { after=p+7; break; }

            if (match_tag(p, TXT_TAG_SFXP)) {
                uint8_t id = 0;
                const char __far *np = parse_u8_until_rbrace(p + 5, &id);
                if (np) { sfx_play(id, 0); p = np; continue; }
            }
            if (match_tag(p, TXT_TAG_MUSICP)) {
                const char __far *q = p + 7;
                if (q[0]=='s'&&q[1]=='t'&&q[2]=='o'&&q[3]=='p'&&q[4]=='}') {
                    snd_stop();
                    p = q + 5;
                    continue;
                }
                uint8_t tid = 0;
                const char __far *np = parse_u8_until_rbrace(q, &tid);
                if (np) { snd_play(tid, 1); p = np; continue; }
            }
            if (match_tag(p, TXT_TAG_SPEEDP)) {
                const char __far *q = p + 7;
                if (q[0]=='s'&&q[1]=='l'&&q[2]=='o'&&q[3]=='w'&&q[4]=='}') { cur_speed = 4; p=q+5; continue; }
                if (q[0]=='n'&&q[1]=='o'&&q[2]=='r'&&q[3]=='m'&&q[4]=='a'&&q[5]=='l'&&q[6]=='}') { cur_speed = 2; p=q+7; continue; }
                if (q[0]=='f'&&q[1]=='a'&&q[2]=='s'&&q[3]=='t'&&q[4]=='}') { cur_speed = 1; p=q+5; continue; }
                if (q[0]=='i'&&q[1]=='n'&&q[2]=='s'&&q[3]=='t'&&q[4]=='a'&&q[5]=='n'&&q[6]=='t'&&q[7]=='}') { cur_speed = 0; p=q+8; continue; }
            }
        }
        if (*p=='\n') { col_=0;line++;cy++;p++;continue; }
        const char __far *e=p;
        while(*e&&*e!=' '&&*e!='\n'&&*e!='{') e++;
        uint8_t wl=(uint8_t)(e-p);
        if (col_+wl>maxc&&col_>0) { col_=0;line++;cy++; if(line>=maxl)break; }
        for (uint8_t i=0;i<wl&&line<maxl;i++) {
            char_talk_tick();
            char c = *p++;
            glyph(SCR2_PTR,cx+col_,cy,c,PAL_TEXT); col_++;
            if (text_blip && cur_speed && c != ' ' && ++blip_ctr >= 3) {
                ui_sfx_text();
                blip_ctr = 0;
            }
            if (cur_speed) { for(uint8_t d=0;d<cur_speed;d++) vblank(); }
        }
        if (*p==' ') { if(col_<maxc){glyph(SCR2_PTR,cx+col_,cy,' ',PAL_TEXT);col_++;} p++; }
    }
    char_talk_finish(after == NULL);
    glyph(SCR2_PTR,SCREEN_W-2,(uint8_t)(TBOX_Y + (tb_style_has_frame() ? TBOX_H - 2 : TBOX_H - 1)),'v',PAL_TEXT);
    return after;
}

static void do_ops(const flag_op_t __far *ops, uint8_t n) {
    for (uint8_t i=0;i<n;i++) {
        if (ops[i].flag_idx>=NUM_FLAGS) continue;
        int16_t *f=&g_flags[ops[i].flag_idx];
        switch(ops[i].op) {
            case OP_ADD: *f+=ops[i].value; break;
            case OP_SUB: *f-=ops[i].value; break;
            case OP_SET: *f =ops[i].value; break;
            case OP_TOGGLE: *f=!*f; break;
        }
    }
}
static bool eval_cond(uint8_t fi, uint8_t op, int16_t v) {
    if (fi>=NUM_FLAGS) return false;
    int16_t f=g_flags[fi];
    switch(op) {
        case COND_EQ:  return f==v;
        case COND_NEQ: return f!=v;
        case COND_GT:  return f> v;
        case COND_GTE: return f>=v;
        case COND_LT:  return f< v;
        case COND_LTE: return f<=v;
    }
    return false;
}

static inline uint8_t clamp_u4(uint8_t v) { return (v > 15) ? 15 : v; }

static void pack_wave_4bit(uint8_t dst16[16], const uint8_t src32[32]) {
    for (uint8_t i = 0; i < 16; i++) {
        uint8_t a = src32[i * 2 + 0] & 0x0F;
        uint8_t b = src32[i * 2 + 1] & 0x0F;
        dst16[i] = (uint8_t)(a | (b << 4));
    }
}

static void make_wave(uint8_t wave_type, uint8_t out32[32]) {
    static const uint8_t sine32[32] = {
        8,  9, 11, 12, 13, 14, 15, 15,
        15, 15, 14, 13, 12, 11,  9,  8,
        7,  5,  4,  3,  2,  1,  0,  0,
        0,  0,  1,  2,  3,  4,  5,  7,
    };

    switch (wave_type) {
        case WAVE_TRIANGLE:
            for (uint8_t i = 0; i < 16; i++) out32[i] = i;
            for (uint8_t i = 0; i < 16; i++) out32[16 + i] = (uint8_t)(15 - i);
            break;
        case WAVE_SAWTOOTH:
            for (uint8_t i = 0; i < 32; i++) out32[i] = (uint8_t)(i & 0x0F);
            break;
        case WAVE_SINE:
            for (uint8_t i = 0; i < 32; i++) out32[i] = sine32[i];
            break;
        case WAVE_SQUARE:
        default:
            for (uint8_t i = 0; i < 16; i++) out32[i] = 0;
            for (uint8_t i = 16; i < 32; i++) out32[i] = 15;
            break;
    }
}

static void snd_apply_step(const music_track_t __far *tr) {
    static const uint8_t freq_ports[4] = {
        WS_SOUND_FREQ_CH1_PORT, WS_SOUND_FREQ_CH2_PORT,
        WS_SOUND_FREQ_CH3_PORT, WS_SOUND_FREQ_CH4_PORT
    };
    static const uint8_t vol_ports[4] = {
        WS_SOUND_VOL_CH1_PORT, WS_SOUND_VOL_CH2_PORT,
        WS_SOUND_VOL_CH3_PORT, WS_SOUND_VOL_CH4_PORT
    };

    for (uint8_t ch = 0; ch < 4; ch++) {
        uint16_t fq = tr->ch[ch].freq[g_music_step];
        uint8_t vv = clamp_u4(tr->ch[ch].vol[g_music_step]);
        if (fq == 0 || vv == 0) {
            outportb(vol_ports[ch], 0);
        } else {
            outportw(freq_ports[ch], fq);
            /* Volume ports use 4-bit L/R nibbles. Set both for mono. */
            outportb(vol_ports[ch], (uint8_t)(vv | (vv << 4)));
        }
    }
}

static void snd_set_waves_for_track(const music_track_t __far *tr) {
    uint8_t samples[32];
    for (uint8_t ch = 0; ch < 4; ch++) {
        make_wave(tr->ch[ch].wave, samples);
        pack_wave_4bit(g_wavetable.wave[ch].data, samples);
    }
}

static void sfx_stop(void) {
    outportb(WS_SDMA_CTRL_PORT, 0);
    g_sfx_frames_left = 0;
    g_sfx_looping = 0;
    /* Disable voice mode; keep CH2 enabled for music wavetable playback. */
    uint8_t ctrl = inportb(WS_SOUND_CH_CTRL_PORT);
    ctrl &= (uint8_t)~WS_SOUND_CH_CTRL_CH2_VOICE;
    outportb(WS_SOUND_CH_CTRL_PORT, ctrl);
}

static void sfx_play(uint8_t id, uint8_t loop) {
    if (NUM_SFX == 0 || id >= NUM_SFX) return;
    const sfx_asset_t __far *s = &SFX_ASSETS[id];
    if (!s->data || s->length_bytes == 0) return;

    /* Restart DMA. */
    outportb(WS_SDMA_CTRL_PORT, 0);
    ws_sdma_set_source(s->data);
    ws_sdma_set_length(s->length_bytes);

    /* Enable voice output (both L/R full). */
    outportb(WS_SOUND_VOICE_VOL_PORT, (uint8_t)(WS_SOUND_VOICE_VOL_LEFT_FULL | WS_SOUND_VOICE_VOL_RIGHT_FULL));

    uint8_t ch_ctrl = inportb(WS_SOUND_CH_CTRL_PORT);
    ch_ctrl |= (uint8_t)(WS_SOUND_CH_CTRL_CH2_ENABLE | WS_SOUND_CH_CTRL_CH2_VOICE);
    outportb(WS_SOUND_CH_CTRL_PORT, ch_ctrl);

    outportb(WS_SDMA_CTRL_PORT, (uint8_t)(WS_SDMA_CTRL_ENABLE |
                                         WS_SDMA_CTRL_INC |
                                         WS_SDMA_CTRL_TARGET_CH2 |
                                         (loop ? WS_SDMA_CTRL_REPEAT : WS_SDMA_CTRL_ONESHOT) |
                                         WS_SDMA_CTRL_RATE_4000));

    g_sfx_looping = (loop != 0);
    /* 4 kHz -> ~66.67 samples per frame @ 60 FPS (ceil(len * 60 / 4000)). */
    uint32_t len = s->length_bytes;
    g_sfx_frames_left = g_sfx_looping ? 0 : (uint16_t)((len * 3u + 199u) / 200u);
}

static void snd_init(void) {
#if USE_CYGNALS
    ws_sound_reset();
#if CYGNALS_CALLS
    if (CYG_CALL_SETWAVE) {
        cygnals_set_wavetable_ram_address((unsigned char ws_iram *) &g_wavetable);
    }
#endif
    outportb(WS_SOUND_OUT_CTRL_PORT, (uint8_t)(WS_SOUND_OUT_CTRL_SPEAKER_ENABLE |
                                               WS_SOUND_OUT_CTRL_HEADPHONE_ENABLE |
                                               WS_SOUND_OUT_CTRL_SPEAKER_VOLUME_800));
    outportb(WS_SOUND_CH_CTRL_PORT, 0);
    /* Ensure all channels start muted; Cygnals will enable as needed. */
    outportb(WS_SOUND_VOL_CH1_PORT, 0);
    outportb(WS_SOUND_VOL_CH2_PORT, 0);
    outportb(WS_SOUND_VOL_CH3_PORT, 0);
    outportb(WS_SOUND_VOL_CH4_PORT, 0);
#else
    ws_sound_reset();
    ws_sound_set_wavetable_address(&g_wavetable);
    outportb(WS_SOUND_OUT_CTRL_PORT, (uint8_t)(WS_SOUND_OUT_CTRL_SPEAKER_ENABLE |
                                               WS_SOUND_OUT_CTRL_HEADPHONE_ENABLE |
                                               WS_SOUND_OUT_CTRL_SPEAKER_VOLUME_800));
    outportb(WS_SOUND_CH_CTRL_PORT, 0);
    outportb(WS_SOUND_VOL_CH1_PORT, 0);
    outportb(WS_SOUND_VOL_CH2_PORT, 0);
    outportb(WS_SOUND_VOL_CH3_PORT, 0);
    outportb(WS_SOUND_VOL_CH4_PORT, 0);
#endif
}

static void snd_stop(void) {
#if USE_CYGNALS
#if CYGNALS_CALLS
    if (CYG_CALL_STOP) {
        if (NUM_CYG_SONGS) cygnals_stop(&g_cyg_song_state);
    }
#endif
    outportb(WS_SOUND_CH_CTRL_PORT, 0);
    outportb(WS_SOUND_VOL_CH1_PORT, 0);
    outportb(WS_SOUND_VOL_CH2_PORT, 0);
    outportb(WS_SOUND_VOL_CH3_PORT, 0);
    outportb(WS_SOUND_VOL_CH4_PORT, 0);
    sfx_stop();
    g_music = 0xFF;
#else
    outportb(WS_SOUND_CH_CTRL_PORT, 0);
    outportb(WS_SOUND_VOL_CH1_PORT, 0);
    outportb(WS_SOUND_VOL_CH2_PORT, 0);
    outportb(WS_SOUND_VOL_CH3_PORT, 0);
    outportb(WS_SOUND_VOL_CH4_PORT, 0);
    sfx_stop();
    g_music = 0xFF;
    g_music_step = 0;
    g_music_acc = 0;
#endif
}

static void snd_play(uint8_t t, uint8_t loop) {
#if USE_CYGNALS
    if (t == g_music) return;
    snd_stop();
    if (NUM_CYG_SONGS == 0 || t >= NUM_CYG_SONGS) return;

#if CYGNALS_CALLS
    if (CYG_CALL_PLAY) {
        cygnals_play(CYG_SONGS[t], &g_cyg_song_state, g_cyg_song_channels);
        cygnals_set_master_volume(&g_cyg_song_state, 0x80);
        if (loop) cygnals_enable_looping(&g_cyg_song_state);
        else cygnals_disable_looping(&g_cyg_song_state);
    } else {
        (void)loop;
    }
#else
    (void)loop;
#endif

    g_music = t;
    g_music_loop = (loop != 0);
#else
    if (t == g_music) return;
    snd_stop();
    if (NUM_TRACKS == 0 || t >= NUM_TRACKS) return;

    const music_track_t __far *tr = &TRACKS[t];
    snd_set_waves_for_track(tr);

    g_music = t;
    g_music_loop = (loop != 0);
    g_music_step = 0;
    g_music_acc = 0;

    outportb(WS_SOUND_CH_CTRL_PORT,
             (uint8_t)(WS_SOUND_CH_CTRL_CH1_ENABLE |
                       WS_SOUND_CH_CTRL_CH2_ENABLE |
                       WS_SOUND_CH_CTRL_CH3_ENABLE |
                       WS_SOUND_CH_CTRL_CH4_ENABLE));
    snd_apply_step(tr);
#endif
}

static void snd_update_frame(void) {
#if USE_CYGNALS
#if CYGNALS_CALLS
    if (!CYG_CALL_UPDATE) return;
    if (NUM_CYG_SONGS && (g_cyg_song_state.flags & CYG_STATE_FLAG_PLAYING)) {
        cygnals_update(&g_cyg_song_state);
    }
#endif
#else
    if (g_music == 0xFF || NUM_TRACKS == 0 || g_music >= NUM_TRACKS) return;

    const music_track_t __far *tr = &TRACKS[g_music];
    uint16_t bpm = tr->bpm ? tr->bpm : 120;
    uint8_t len = tr->length_steps ? tr->length_steps : 32;

    /* Editor uses 16th notes: step_ms = (60 / bpm / 4) * 1000.
     * At ~60 FPS, frames_per_step â‰ˆ 900 / bpm. Use an accumulator: +bpm each frame, step at >=900. */
    g_music_acc = (uint16_t)(g_music_acc + bpm);
    while (g_music_acc >= 900) {
        g_music_acc = (uint16_t)(g_music_acc - 900);
        g_music_step++;
        if (g_music_step >= len) {
            if (g_music_loop) g_music_step = 0;
            else { snd_stop(); return; }
        }
        snd_apply_step(tr);
    }

#endif

    if (!g_sfx_looping && g_sfx_frames_left) {
        g_sfx_frames_left--;
        if (!g_sfx_frames_left) {
            /* SFX finished: disable voice mode and restore music playback. */
            sfx_stop();
#if !USE_CYGNALS
            if (g_music != 0xFF && NUM_TRACKS && g_music < NUM_TRACKS) {
                snd_apply_step(&TRACKS[g_music]);
            }
#endif
        }
    }
}

static void prepare_scene_visuals(const scene_t __far *s);
static void restore_scene_visuals(void) {
    if (g_last_scene) prepare_scene_visuals(g_last_scene);
}

static void prepare_scene_visuals(const scene_t __far *s) {
    /* Start heavy VRAM/palette work at the beginning of VBlank to reduce tearing flashes. */
    wait_vblank_start();
    g_last_scene = s;
    apply_scene_theme(s);
    clear_char_layer();
    g_blink_enabled = 0;
    g_blink_closed = 0;
    g_blink_ctr = 0;
    g_blink_open_id = 0xFF;
    g_blink_closed_id = 0xFF;
    g_blink_pos = POS_NONE;
    g_talk_enabled = 0;
    g_talk_phase = 0;
    g_talk_ctr = 0;
    g_talk_open = 0;
    g_talk_blink_id = 0xFF;
    g_fg_width_tiles = 0;
    g_fg_talk_enabled = 0;
    g_fg_talk_patch = 0xFF;
    g_fg_talk_open = 0;
    g_fg_blink_enabled = 0;
    g_fg_blink_closed = 0;
    g_fg_blink_ctr = 0;
    g_fg_blink_patch = 0xFF;

    /* Background: avoid re-uploading tiles when unchanged (reduces flashes). */
    set_pal2(PAL_BG_TOP, 0x0111, col(g_bg_col1));
    set_pal2(PAL_BG_BOT, 0x0111, col(g_bg_col2));
    if (s->bg_image_id!=0xFF&&s->bg_image_id<NUM_BG_ASSETS) {
        cg_mark_seen(s->bg_image_id);
        const image_asset_t __far *bg = &BG_ASSETS[s->bg_image_id];
        set_pal16(PAL_BG_IMAGE, bg->palette);
        if (bg->palette2) set_pal16(PAL_BG_IMAGE2, bg->palette2);
        /* Configure palette cycling (only affects PAL_BG_IMAGE). */
        g_palcycle_enable = (s->pal_cycle_enable != 0 && s->pal_cycle_len >= 2 && s->pal_cycle_start < 16);
        if (g_palcycle_enable) {
            g_palcycle_start = s->pal_cycle_start;
            g_palcycle_len   = s->pal_cycle_len;
            g_palcycle_speed = s->pal_cycle_speed ? s->pal_cycle_speed : 1;
            if (g_palcycle_start + g_palcycle_len > 16) g_palcycle_len = (uint8_t)(16 - g_palcycle_start);
            for (uint8_t i = 0; i < 16; i++) g_palcycle_base[i] = bg->palette[i];
            g_palcycle_ctr = 0;
            g_palcycle_phase = 0;
        } else {
            g_palcycle_len = 0;
            g_palcycle_speed = 0;
            g_palcycle_ctr = 0;
            g_palcycle_phase = 0;
        }
        if (g_loaded_bg_id != s->bg_image_id) {
            for (uint16_t i=0;i<bg->tile_count;i++)
                wtb1f(BG_IMAGE_BASE+i, bg->tiles+(uint32_t)i*TILE_SIZE);
            g_loaded_bg_id = s->bg_image_id;
        }
        render_image_bg_map_on(SCR1_PTR, bg);
    } else {
        g_palcycle_enable = 0;
        g_palcycle_len = 0;
        g_palcycle_speed = 0;
        g_loaded_bg_id = 0xFF;
        render_image_bg_map_on(SCR1_PTR, NULL);
    }

    if (s->fg_image_id!=0xFF&&s->fg_image_id<NUM_FG_ASSETS) {
        const image_asset_t __far *fg = &FG_ASSETS[s->fg_image_id];
        if (g_loaded_fg_id != s->fg_image_id) {
            for (uint16_t i=0;i<fg->tile_count;i++) wtb0f(FG_BASE+i, fg->tiles+(uint32_t)i*TILE_SIZE);
            g_loaded_fg_id = s->fg_image_id;
        }
        render_foreground_map(fg);
        g_fg_width_tiles = fg->width_tiles;
        if ((s->char_anim == 6 || s->char_anim == 7) && s->fg_talk_patch_id != 0xFF && s->fg_talk_patch_id < NUM_FG_ANIM_PATCHES) {
            g_fg_talk_enabled = 1;
            g_fg_talk_patch = s->fg_talk_patch_id;
            g_fg_talk_open = 0;
            if (s->char_anim == 7 && s->fg_blink_patch_id != 0xFF && s->fg_blink_patch_id < NUM_FG_ANIM_PATCHES) {
                g_fg_blink_patch = s->fg_blink_patch_id;
            }
        } else if (s->char_anim == 5 && s->fg_blink_patch_id != 0xFF && s->fg_blink_patch_id < NUM_FG_ANIM_PATCHES) {
            g_fg_blink_enabled = 1;
            g_fg_blink_patch = s->fg_blink_patch_id;
        }
        g_loaded_char1 = 0xFF;
        g_loaded_char2 = 0xFF;
        return;
    } else {
        g_loaded_fg_id = 0xFF;
    }

    /* Characters: avoid re-uploading tiles when unchanged (still redraw map each scene). */
    if (s->char_id!=0xFF&&s->char_id<NUM_CHAR_ASSETS) {
        const image_asset_t __far *ch = &CHAR_ASSETS[s->char_id];
        if (g_loaded_char1 != s->char_id) {
            for (uint16_t i=0;i<ch->tile_count;i++) wtb0f(CHAR1_BASE+i, ch->tiles+(uint32_t)i*TILE_SIZE);
            g_loaded_char1 = s->char_id;
        }
        render_char_map(ch, s->char_pos, PAL_CHAR1, CHAR1_BASE);
        if ((s->char_anim == 5 || s->char_anim == 6 || s->char_anim == 7) && s->char2_pos == POS_NONE && s->char2_id != 0xFF && s->char2_id < NUM_CHAR_ASSETS) {
            const image_asset_t __far *blink = &CHAR_ASSETS[s->char2_id];
            if (g_loaded_char2 != s->char2_id) {
                for (uint16_t i=0;i<blink->tile_count;i++) wtb0f(CHAR2_BASE+i, blink->tiles+(uint32_t)i*TILE_SIZE);
                g_loaded_char2 = s->char2_id;
            }
            g_blink_open_id = s->char_id;
            g_blink_closed_id = s->char2_id;
            g_blink_pos = s->char_pos;
            if (s->char_anim == 5) g_blink_enabled = 1;
            else {
                g_talk_enabled = 1;
                if (s->char_anim == 7 && s->char3_id != 0xFF && s->char3_id < NUM_CHAR_ASSETS) g_talk_blink_id = s->char3_id;
            }
        }
    } else {
        g_loaded_char1 = 0xFF;
    }
    if (s->char2_id!=0xFF&&s->char2_id<NUM_CHAR_ASSETS && !((s->char_anim == 5 || s->char_anim == 6 || s->char_anim == 7) && s->char2_pos == POS_NONE)) {
        const image_asset_t __far *ch2 = &CHAR_ASSETS[s->char2_id];
        if (g_loaded_char2 != s->char2_id) {
            for (uint16_t i=0;i<ch2->tile_count;i++) wtb0f(CHAR2_BASE+i, ch2->tiles+(uint32_t)i*TILE_SIZE);
            g_loaded_char2 = s->char2_id;
        }
        render_char_map(ch2, s->char2_pos, PAL_CHAR2, CHAR2_BASE);
    } else {
        g_loaded_char2 = 0xFF;
    }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * SAVE / LOAD OVERLAY
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
static void backlog_overlay(void) {
    overlay_begin();
    wait_vblank_start();
    set_pal2(PAL_BG_TOP, 0x0111, 0x0122);
    set_pal2(PAL_BG_BOT, 0x0111, 0x0224);
    set_pal2(PAL_TEXT, 0x0111, 0x0EEF);
    set_pal2(PAL_SPEAKER, 0x0111, 0x0FFF);
    fill_region(SCR1_PTR, 0, 0, SCREEN_W, 9, 0, PAL_BG_TOP, true);
    fill_region(SCR1_PTR, 0, 9, SCREEN_W, 9, 0, PAL_BG_BOT, true);
    fill_region(SCR2_PTR, 0, 0, SCREEN_W, SCREEN_H, TILE_BLANK, PAL_TEXT, false);
    uint8_t view = g_backlog_count ? (uint8_t)(g_backlog_count - 1) : 0;
    uint8_t last_view = 0xFF;
    while (1) {
        if (view != last_view) {
            fill_region(SCR2_PTR, 0, 0, SCREEN_W, SCREEN_H, TILE_BLANK, PAL_TEXT, false);
            text_on(SCR2_PTR, 1, 1, TXT_BACKLOG, PAL_TEXT);
            if (!g_backlog_count) {
                text_on(SCR2_PTR, 1, 4, TXT_NO_HISTORY, PAL_TEXT);
            } else {
                uint8_t slot = backlog_slot_for_view(view);
                const char __far *speaker = g_backlog_speaker[slot];
                if (speaker && speaker[0]) text_on_clip(SCR2_PTR, 1, 3, speaker, PAL_SPEAKER, 24);
                text_wrap_on(SCR2_PTR, 1, 5, 26, 10, g_backlog_text[slot], PAL_TEXT);
            }
            text_on(SCR2_PTR, 1, 16, TXT_NAV_BACK, PAL_TEXT);
            last_view = view;
        }
        vblank(); read_keys();
        if ((pressed(KEY_LEFT) || pressed(KEY_UP)) && view > 0) { view--; ui_sfx_cursor(); }
        if ((pressed(KEY_RIGHT) || pressed(KEY_DOWN)) && view + 1 < g_backlog_count) { view++; ui_sfx_cursor(); }
        if (pressed(KEY_A|KEY_B|KEY_START)) { ui_sfx_confirm(); break; }
    }
    wait_key_release();
    overlay_end();
}

static uint8_t gallery_next_seen(uint8_t start, int8_t dir) {
#if NUM_BG_ASSETS == 0
    (void)start;
    (void)dir;
    return 0xFF;
#else
    if (!NUM_BG_ASSETS) return 0xFF;
    uint8_t idx = start;
    for (uint16_t i = 0; i < NUM_BG_ASSETS; i++) {
        if (dir > 0) idx = (uint8_t)((idx + 1) % NUM_BG_ASSETS);
        else idx = (idx == 0) ? (uint8_t)(NUM_BG_ASSETS - 1) : (uint8_t)(idx - 1);
        if (cg_is_seen(idx)) return idx;
    }
    return 0xFF;
#endif
}

static uint8_t gallery_first_seen(void) {
    for (uint16_t i = 0; i < NUM_BG_ASSETS; i++) {
        if (cg_is_seen((uint8_t)i)) return (uint8_t)i;
    }
    return 0xFF;
}

static void gallery_draw(uint8_t idx) {
    wait_vblank_start();
    fill_region(SCR2_PTR, 0, 0, SCREEN_W, SCREEN_H, TILE_BLANK, PAL_TEXT, false);
    set_pal2(PAL_TEXT, 0x0111, 0x0EEF);
    set_pal2(PAL_SPEAKER, 0x0111, 0x0EEF);
    if (idx != 0xFF && idx < NUM_BG_ASSETS) {
        const image_asset_t __far *bg = &BG_ASSETS[idx];
        set_pal2(PAL_BG_TOP, 0x0111, 0x0111);
        set_pal2(PAL_BG_BOT, 0x0111, 0x0111);
        set_pal16(PAL_BG_IMAGE, bg->palette);
        render_image_bg_on(SCR1_PTR, bg);
        g_loaded_bg_id = 0xFE;
        text_on_clip(SCR2_PTR, 1, 1, BG_ASSET_NAMES[idx], PAL_TEXT, 26);
    } else {
        render_image_bg_map_on(SCR1_PTR, NULL);
        text_on(SCR2_PTR, 1, 4, TXT_NO_CG, PAL_TEXT);
    }
    text_on(SCR2_PTR, 1, 16, TXT_NAV_BACK, PAL_TEXT);
}

static void gallery_overlay(void) {
    overlay_begin();
    uint8_t cur = gallery_first_seen();
    gallery_draw(cur);
    while (1) {
        vblank(); read_keys();
        if ((pressed(KEY_RIGHT) || pressed(KEY_DOWN)) && cur != 0xFF) {
            uint8_t next = gallery_next_seen(cur, 1);
            if (next != 0xFF) { cur = next; ui_sfx_cursor(); gallery_draw(cur); }
        }
        if ((pressed(KEY_LEFT) || pressed(KEY_UP)) && cur != 0xFF) {
            uint8_t next = gallery_next_seen(cur, -1);
            if (next != 0xFF) { cur = next; ui_sfx_cursor(); gallery_draw(cur); }
        }
        if (pressed(KEY_A|KEY_B|KEY_START)) { ui_sfx_confirm(); break; }
    }
    wait_key_release();
    overlay_end();
}

#define OVL_X  4
#define OVL_Y  4
#define OVL_W 20
#define OVL_H 10

static uint8_t saveload_overlay(uint8_t mode) {
    overlay_begin();
    /* Use SCR1 for the background and SCR2 for text only (transparent), like the title menu. */
    set_pal2(PAL_TEXT, 0x0111, 0x0EEF);
    set_pal2(PAL_SPEAKER, 0x0111, 0x0EEF);

    /* SAVELOAD_BG overwrites BG_IMAGE tiles in bank1, so invalidate the cache. */
    g_loaded_bg_id = 0xFE;

    /* Background on SCR1 (optional PNG embedded in ROM). */
    wait_vblank_start();
    if (SAVELOAD_BG.tile_count) {
        set_pal2(PAL_BG_TOP, 0x0111, 0x0111);
        set_pal2(PAL_BG_BOT, 0x0111, 0x0111);
        set_pal16(PAL_BG_IMAGE, SAVELOAD_BG.palette);
        render_image_bg_on(SCR1_PTR, &SAVELOAD_BG);
    }

    uint8_t sel = 0;
    if (mode==1) {
        for (uint8_t i = 0; i < NUM_SAVE_SLOTS; i++) {
            if (save_slot_valid(i)) { sel = i; break; }
        }
    }

    /* Center the menu vertically on the background. */
    const uint8_t lines_total = (uint8_t)(1 + 1 + NUM_SAVE_SLOTS + 1); /* header + spacer + slots + footer */
    const uint8_t y0 = (uint8_t)((SCREEN_H > lines_total) ? ((SCREEN_H - lines_total) / 2) : 0);
    const uint8_t x0 = 4;
    const uint8_t w0 = 20;
    uint8_t last_sel = 0xFF;

    /* Clear SCR2 to transparent tiles. */
    fill_region(SCR2_PTR, 0, 0, SCREEN_W, SCREEN_H, TILE_BLANK, PAL_TEXT, false);

    while (1) {
        if (sel != last_sel) {
            /* Clear SCR2 in the UI region (transparent) and redraw text. */
            fill_region(SCR2_PTR, x0, y0, w0, lines_total, TILE_BLANK, PAL_TEXT, false);

            const char __far *hdr = (mode==0) ? TXT_SAVE_HDR : TXT_LOAD_HDR;
            uint8_t hl = far_strlen(hdr);
            uint8_t hx = (uint8_t)(x0 + (w0 > hl ? ((w0 - hl) / 2) : 0));
            text_on(SCR2_PTR, hx, y0, hdr, PAL_TEXT);

            for (uint8_t i=0;i<NUM_SAVE_SLOTS;i++) {
                uint8_t row = (uint8_t)(y0 + 2 + i);
                glyph(SCR2_PTR, x0, row, sel==i ? '>' : ' ', PAL_TEXT);
                text_on(SCR2_PTR, (uint8_t)(x0 + 2), row, TXT_SLOT, PAL_TEXT);
                glyph(SCR2_PTR, (uint8_t)(x0 + 7), row, (char)('1' + i), PAL_TEXT);
                if (save_slot_valid(i)) {
                    uint16_t nid = SRAM_STORE->slots[i].node_id;
                    if (nid < NUM_NODES) text_on_clip(SCR2_PTR, (uint8_t)(x0 + 9), row, NODE_NAMES[nid], PAL_TEXT, 11);
                    else put_num_on(SCR2_PTR, (uint8_t)(x0 + 9), row, (uint16_t)(nid + 1), PAL_TEXT);
                } else {
                    text_on(SCR2_PTR, (uint8_t)(x0 + 9), row, TXT_EMPTY, PAL_TEXT);
                }
            }

            uint8_t fl = far_strlen(TXT_OKBACK);
            uint8_t fx = (uint8_t)(x0 + (w0 > fl ? ((w0 - fl) / 2) : 0));
            text_on(SCR2_PTR, fx, (uint8_t)(y0 + lines_total - 1), TXT_OKBACK, PAL_TEXT);

            last_sel = sel;
        }

        vblank(); read_keys();
        if (pressed(KEY_UP)   && sel>0) { sel--; ui_sfx_cursor(); }
        if (pressed(KEY_DOWN) && sel<NUM_SAVE_SLOTS-1) { sel++; ui_sfx_cursor(); }
        if (pressed(KEY_B)) {
            wait_vblank_start();
            fill_region(SCR2_PTR, x0, y0, w0, lines_total, TILE_BLANK, PAL_TEXT, false);
            restore_scene_visuals();
            overlay_end();
            return 0xFF;
        }
        if (pressed(KEY_A|KEY_START)) {
            if (mode==1 && !save_slot_valid(sel)) continue;
            ui_sfx_confirm();
            for (uint8_t d=0;d<8;d++) vblank();
            wait_vblank_start();
            fill_region(SCR2_PTR, x0, y0, w0, lines_total, TILE_BLANK, PAL_TEXT, false);
            restore_scene_visuals();
            overlay_end();
            return sel;
        }
    }
}

static uint16_t ingame_menu(uint16_t current_node) {
    overlay_begin();
    uint8_t saved_tb_style = g_tb_style;
    g_tb_style = 0;
    draw_box();
    set_pal2(PAL_TEXT,0x0111,0x0FFF);
    uint8_t sel=0;
    while (1) {
        for (uint8_t i=0;i<4;i++) {
            fill_region(SCR2_PTR,2,TBOX_Y+1+i,SCREEN_W-4,1,TILE_BLANK,PAL_TEXT,false);
            glyph(SCR2_PTR,2,TBOX_Y+1+i,sel==i?'>':' ',PAL_TEXT);
            textf(4,TBOX_Y+1+i,g_menu_items[i],PAL_TEXT);
        }
        vblank(); read_keys();
        if (pressed(KEY_UP)   && sel>0) { sel--; ui_sfx_cursor(); }
        if (pressed(KEY_DOWN) && sel<3) { sel++; ui_sfx_cursor(); }
        if (pressed(KEY_B)) { g_tb_style = saved_tb_style; draw_box(); overlay_end(); return 0xFFFF; }
        if (pressed(KEY_A|KEY_START)) {
            ui_sfx_confirm();
            if (sel==3) { g_tb_style = saved_tb_style; draw_box(); overlay_end(); return 0xFFFF; }
            if (sel==2) { g_tb_style = saved_tb_style; overlay_end(); return START_NODE_IDX; }
            if (sel==0) {
                uint8_t slot = saveload_overlay(0);
                if (slot!=0xFF) {
                    save_slot_write(slot, current_node);
                    draw_box();
                    set_pal2(PAL_TEXT,0x0111,0x0EEF);
                    textf(8,TBOX_Y+2,TXT_GAME_SAVED,PAL_TEXT);
                    for (uint8_t d=0;d<60;d++) vblank();
                }
                g_tb_style = saved_tb_style;
                draw_box();
                overlay_end();
                return 0xFFFF;
            }
            uint8_t slot = saveload_overlay(1);
            if (slot==0xFF) { g_tb_style = saved_tb_style; draw_box(); overlay_end(); return 0xFFFF; }
            g_tb_style = saved_tb_style;
            overlay_end();
            return save_slot_load(slot);
        }
    }
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * RUNTIME MODES (Title, Scene, Choice)
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
static void draw_title_text(const scene_t __far *s) {
    set_pal2(PAL_TEXT,0x0111,0x0EEF);
    fill_region(SCR2_PTR,0,0,SCREEN_W,SCREEN_H,TILE_BLANK,PAL_TEXT,false);
    if (s->title_main&&s->title_main[0]) {
        uint8_t l=0; const char __far *p=s->title_main; while(*p++)l++;
        textf((SCREEN_W-l)/2,4,s->title_main,PAL_TEXT);
    }
    if (s->title_sub&&s->title_sub[0]) {
        uint8_t l=0; const char __far *p=s->title_sub; while(*p++)l++;
        textf((SCREEN_W-l)/2,6,s->title_sub,PAL_TEXT);
    }
}

static uint16_t run_title(const scene_t __far *s) {
    prepare_scene_visuals(s);
    switch (s->music_action) {
        case MUSIC_CHANGE: snd_play(s->music_track, s->music_loop); break;
        case MUSIC_STOP: case MUSIC_FADE_OUT: snd_stop(); break;
        default: break;
    }
    switch (s->sfx_action) {
        case SFX_STOP: sfx_stop(); break;
        case SFX_CHANGE:
            if (s->sfx_id != 0xFF) sfx_play(s->sfx_id, s->sfx_loop);
            else sfx_stop();
            break;
        case SFX_KEEP:
        default: break;
    }
    draw_title_text(s);
    while (1) {
        uint8_t choice = 0;
        if (s->menu_count>0) {
            uint8_t sel=0;
            while (1) {
                for (uint8_t i=0;i<s->menu_count;i++) {
                    fill_region(SCR2_PTR,2,10+i,SCREEN_W-4,1,TILE_BLANK,PAL_TEXT,false);
                    glyph(SCR2_PTR,2,10+i,sel==i?'>':' ',PAL_TEXT);
                    textf(4,10+i,s->menu_items[i],PAL_TEXT);
                }
                vblank(); read_keys();
                if (pressed(KEY_UP)   && sel>0) { sel--; ui_sfx_cursor(); }
                if (pressed(KEY_DOWN) && sel<s->menu_count-1) { sel++; ui_sfx_cursor(); }
                if (pressed(KEY_A|KEY_START)) { ui_sfx_confirm(); choice=sel; break; }
            }
        } else {
            while(1){vblank();read_keys();if(pressed(KEY_A|KEY_START)){ui_sfx_confirm();break;}}
            reset_new_game_state();
            return s->next_id;
        }
        const char __far *label = s->menu_items[choice];
        if (label[0]=='L'||label[0]=='l') {
            fill_region(SCR2_PTR,0,0,SCREEN_W,SCREEN_H,TILE_BLANK,PAL_TEXT,false);
            draw_box();
            uint8_t slot = saveload_overlay(1);
            wait_key_release();
            if (slot==0xFF) {
                prepare_scene_visuals(s);
                draw_title_text(s);
                continue;
            }
            return save_slot_load(slot);
        }
        if (label[0]=='G'||label[0]=='g') {
            gallery_overlay();
            prepare_scene_visuals(s);
            draw_title_text(s);
            continue;
        }
        reset_new_game_state();
        return s->next_id;
    }
}

static uint16_t run_scene(const scene_t __far *s, uint16_t node_id) {
    if (s->transition == TRANSITION_FADE) {
        transition_fade_to_black(8);
        prepare_scene_visuals(s);
        pal_snapshot(g_trans_pal);
        /* Start from black, then fade in to the new palette. */
        transition_fade_from_black(8);
    } else if (s->transition == TRANSITION_FLASH) {
        transition_flash_white(2);
        prepare_scene_visuals(s);
    } else {
        prepare_scene_visuals(s);
    }
    switch (s->music_action) {
        case MUSIC_CHANGE: snd_play(s->music_track, s->music_loop); break;
        case MUSIC_STOP: case MUSIC_FADE_OUT: snd_stop(); break;
        default: break;
    }
    switch (s->sfx_action) {
        case SFX_STOP: sfx_stop(); break;
        case SFX_CHANGE:
            if (s->sfx_id != 0xFF) sfx_play(s->sfx_id, s->sfx_loop);
            else sfx_stop();
            break;
        case SFX_KEEP:
        default: break;
    }
    uint8_t spd=2;
    if(s->text_speed==SPEED_SLOW) spd=4;
    if(s->text_speed==SPEED_FAST) spd=1;
    if(s->text_speed==SPEED_INSTANT) spd=0;
    const char __far *txt = s->dialogue;
    while (txt) {
        backlog_add(s->speaker, txt);
        const char __far *next_block = show_text_block(s->speaker,txt,spd);
        while (1) {
            vblank(); read_keys();
            if (pressed(KEY_START)) {
                uint16_t jump = ingame_menu(node_id);
                wait_key_release();
                if (jump!=0xFFFF) return jump;
                restore_scene_visuals();
                show_text_block(s->speaker,txt,0);
            }
            if (pressed(KEY_LEFT)) {
                backlog_overlay();
                restore_scene_visuals();
                show_text_block(s->speaker,txt,0);
            }
            if (pressed(KEY_A|KEY_B)) break;
        }
        if (!next_block) break;
        wait_vblank_start();
        fill_region(SCR2_PTR,0,TBOX_Y,SCREEN_W,TBOX_H,TILE_BLANK,PAL_TEXT,false);
        txt=next_block;
    }
    do_ops(s->flag_ops,s->flag_ops_count);
    return 0xFFFF;
}

static uint16_t run_investigation(const investigation_node_t __far *inv, uint16_t node_id) {
    const scene_t __far *s = &inv->scene;
    prepare_scene_visuals(s);
    switch (s->music_action) {
        case MUSIC_CHANGE: snd_play(s->music_track, s->music_loop); break;
        case MUSIC_STOP: case MUSIC_FADE_OUT: snd_stop(); break;
        default: break;
    }
    switch (s->sfx_action) {
        case SFX_STOP: sfx_stop(); break;
        case SFX_CHANGE:
            if (s->sfx_id != 0xFF) sfx_play(s->sfx_id, s->sfx_loop);
            else sfx_stop();
            break;
        default: break;
    }

    uint8_t cursor_x = SCREEN_W / 2;
    uint8_t cursor_y = 8;
    uint8_t old_x = 0xFF, old_y = 0xFF;
    uint8_t seen = 0;
    uint8_t required_mask = 0;
    for (uint8_t i = 0; i < inv->hotspots_count && i < 8; i++) {
        if (inv->hotspots[i].required) required_mask |= (uint8_t)(1u << i);
    }

    while (1) {
        if (old_x != cursor_x || old_y != cursor_y) {
            if (old_x != 0xFF) glyph(SCR2_PTR, old_x, old_y, ' ', PAL_TEXT);
            set_pal2(PAL_TEXT, 0x0000, 0x0FFF);
            glyph(SCR2_PTR, cursor_x, cursor_y, '+', PAL_TEXT);
            old_x = cursor_x; old_y = cursor_y;
        }

        vblank(); read_keys();
        if (pressed(KEY_START)) {
            uint16_t jump = ingame_menu(node_id);
            wait_key_release();
            if (jump != 0xFFFF) return jump;
            restore_scene_visuals();
            old_x = 0xFF;
        }
        if (pressed(KEY_LEFT)  && cursor_x > 0) { cursor_x--; ui_sfx_cursor(); }
        if (pressed(KEY_RIGHT) && cursor_x < SCREEN_W - 1) { cursor_x++; ui_sfx_cursor(); }
        if (pressed(KEY_UP)    && cursor_y > 0) { cursor_y--; ui_sfx_cursor(); }
        if (pressed(KEY_DOWN)  && cursor_y < SCREEN_H - 1) { cursor_y++; ui_sfx_cursor(); }
        if (pressed(KEY_A)) {
            uint8_t px = (uint8_t)(cursor_x * 8u + 4u);
            uint8_t py = (uint8_t)(cursor_y * 8u + 4u);
            for (uint8_t i = 0; i < inv->hotspots_count && i < 8; i++) {
                const hotspot_t __far *h = &inv->hotspots[i];
                if (px >= h->x && px < (uint8_t)(h->x + h->w) && py >= h->y && py < (uint8_t)(h->y + h->h)) {
                    ui_sfx_confirm();
                    seen |= (uint8_t)(1u << i);
                    do_ops(h->flag_ops, h->flag_ops_count);
                    if (h->text && h->text[0]) {
                        show_text_block(NULL, h->text, 0);
                        wait_key_release();
                        while (1) { vblank(); read_keys(); if (pressed(KEY_A|KEY_B)) break; }
                    }
                    if (h->target != 0xFFFF) return h->target;
                    restore_scene_visuals();
                    old_x = 0xFF;
                    if (required_mask && (seen & required_mask) == required_mask) return inv->default_target;
                    break;
                }
            }
        }
        if (pressed(KEY_B) && inv->default_target != 0xFFFF) return inv->default_target;
    }
}

static uint16_t run_choice(const choice_node_t __far *c, uint16_t node_id) {
    uint8_t saved_tb_style = g_tb_style;
    uint8_t vc=0;
    for (uint8_t i=0;i<c->choices_count&&vc<4;i++) {
        const choice_opt_t __far *o=&c->choices[i];
        if (!o->has_condition||eval_cond(o->cond_flag,o->cond_op,o->cond_value))
            { g_choice_vt[vc]=o->text; g_choice_vm[vc++]=i; }
    }
    if (!vc) return c->default_target;
    uint8_t framed = tb_style_has_frame() ? 1 : 0;
    uint8_t has_prompt = (c->prompt && c->prompt[0]) ? 1 : 0;
    if (framed && (uint8_t)(vc + has_prompt) > 3) {
        g_tb_style = 0;
        framed = 0;
    }
    draw_box();
    uint8_t prompt_y = (uint8_t)(TBOX_Y + framed);
    uint8_t option_y = (uint8_t)(TBOX_Y + 1 + framed);
    if (c->prompt&&c->prompt[0]) textf(1,prompt_y,c->prompt,PAL_TEXT);
    uint8_t sel=0;
    while (1) {
        for (uint8_t i=0;i<vc;i++) {
            uint8_t row = (uint8_t)(option_y + i);
            if (framed && row >= (uint8_t)(TBOX_Y + TBOX_H - 1)) continue;
            fill_region(SCR2_PTR,2,row,SCREEN_W-4,1,TILE_BLANK,PAL_TEXT,false);
            glyph(SCR2_PTR,2,row,sel==i?'>':' ',PAL_TEXT);
            textf(4,row,g_choice_vt[i],PAL_TEXT);
        }
        vblank(); read_keys();
        if (pressed(KEY_UP)   && sel>0) { sel--; ui_sfx_cursor(); }
        if (pressed(KEY_DOWN) && sel<vc-1) { sel++; ui_sfx_cursor(); }
        if (pressed(KEY_START)) {
            uint16_t jump=ingame_menu(node_id);
            wait_key_release();
            if (jump!=0xFFFF) { g_tb_style = saved_tb_style; return jump; }
            restore_scene_visuals();
            if (!framed && saved_tb_style != 0 && tb_style_has_frame()) g_tb_style = 0;
            draw_box();
            if(c->prompt&&c->prompt[0]) textf(1,prompt_y,c->prompt,PAL_TEXT);
        }
        if (pressed(KEY_A)) {
            ui_sfx_confirm();
            const choice_opt_t __far *ch=&c->choices[g_choice_vm[sel]];
            do_ops(ch->flag_ops,ch->flag_ops_count);
            g_tb_style = saved_tb_style;
            return ch->target;
        }
    }
}

static uint16_t run_branch(const branch_node_t __far *b) {
    for (uint8_t i=0;i<b->branches_count;i++) {
        const branch_cond_t __far *br=&b->branches[i];
        if (eval_cond(br->flag_idx,br->op,br->value)) return br->target;
    }
    return b->default_target;
}

static uint16_t run_end(void) {
    snd_stop();
    apply_scene_theme(NULL);
    clear_char_layer();
    render_image_bg(NULL);
    draw_box();
    set_pal2(PAL_TEXT,0x0111,0x0EEF);
    textf(7,9,TXT_THE_END,PAL_TEXT);
    wait_key_release();
    while (1) {
        vblank();
        read_keys();
        if (pressed(KEY_A|KEY_B|KEY_START)) {
            wait_key_release();
            return START_NODE_IDX;
        }
    }
}

static void hw_init(void) {
    outportb(PORT_SYSTEM_CTRL2, VMODE_COLOR_4BPP_PACKED);
    outportb(IO_SCR_BASE, (6u<<4)|7u);
    outportb(IO_DISPLAY_CTRL, 0);
    /* Avoid â€œred flashâ€ backdrop during heavy VRAM/palette updates (emulators/hardware). */
    outportb(WS_DISPLAY_BACK_PORT, 0x00);
    { volatile uint16_t __wf_iram *p=(volatile uint16_t __wf_iram *)0xFE00u;
      for(uint16_t i=0;i<256;i++) p[i]=0; }
    clear_screen(SCR1_PTR);
    clear_screen(SCR2_PTR);
    { uint8_t __wf_iram *p=TILE_BANK0_PTR;
      for(uint32_t i=0;i<(uint32_t)TILE_SIZE*128;i++) p[i]=0; }
    make_blank(TILE_BLANK);
    make_solid_b0(TILE_SOLID);
    make_solid_b1(0);
    make_frame_tile_b1(TILE_FRAME_H, 0);
    make_frame_tile_b1(TILE_FRAME_V, 1);
    make_frame_tile_b1(TILE_FRAME_TL, 2);
    make_frame_tile_b1(TILE_FRAME_TR, 3);
    make_frame_tile_b1(TILE_FRAME_BL, 4);
    make_frame_tile_b1(TILE_FRAME_BR, 5);
    make_frame_tile_b1(TILE_FRAME_DOT, 6);
    load_font();
    snd_init();
    outportb(IO_DISPLAY_CTRL, DISPLAY_SCR1_ENABLE|DISPLAY_SCR2_ENABLE);
}

/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
 * MAIN
 * â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */
int main(void) {
    hw_init();
    save_store_init();

    /* Start at the project-defined start node (usually the title screen). */
    reset_new_game_state();
    g_node = START_NODE_IDX;

    while (g_node < NUM_NODES) {
        const node_t __far *n = NODES[g_node];
        uint16_t next = g_node + 1;

        switch (n->type) {
            case NODE_TITLE:
                next = run_title(&n->data.scene);
                if (next == 0xFFFF) next = g_node + 1;
                break;
            case NODE_SCENE: {
                uint16_t jump = run_scene(&n->data.scene, g_node);
                if (jump != 0xFFFF) next = jump;
                else next = (n->data.scene.next_id != 0xFFFF) ? n->data.scene.next_id : g_node + 1;
                break;
            }
            case NODE_CHOICE: {
                uint16_t jump = run_choice(&n->data.choice, g_node);
                if (jump != 0xFFFF) next = jump;
                else next = g_node + 1;
                break;
            }
            case NODE_BRANCH:
                next = run_branch(&n->data.branch);
                if (next == 0xFFFF) next = g_node + 1;
                break;
            case NODE_INVESTIGATION: {
                uint16_t jump = run_investigation(&n->data.investigation, g_node);
                if (jump != 0xFFFF) next = jump;
                else next = g_node + 1;
                break;
            }
            case NODE_CHAPTER: next = g_node + 1; break;
            case NODE_END: default: next = run_end(); break;
        }
        g_node = next;
    }
    g_node = run_end();
    return 0;
}
