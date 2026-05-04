/*
 * game_types.h — shared type definitions for the engine and generated data
 */
#ifndef GAME_TYPES_H
#define GAME_TYPES_H

#include <stdint.h>
#include <stdbool.h>

/* Node types */
#define NODE_TITLE    0
#define NODE_CHAPTER  1
#define NODE_SCENE    2
#define NODE_CHOICE   3
#define NODE_BRANCH   4
#define NODE_END      5
#define NODE_INVESTIGATION 6

/* Text speed */
#define SPEED_SLOW    0
#define SPEED_NORMAL  1
#define SPEED_FAST    2
#define SPEED_INSTANT 3

/* Flag ops */
#define OP_ADD    0
#define OP_SUB    1
#define OP_SET    2
#define OP_TOGGLE 3

/* Condition ops */
#define COND_EQ   0
#define COND_NEQ  1
#define COND_GT   2
#define COND_GTE  3
#define COND_LT   4
#define COND_LTE  5

/* Music actions */
#define MUSIC_KEEP      0
#define MUSIC_CHANGE    1
#define MUSIC_STOP      2
#define MUSIC_FADE_OUT  3

/* SFX actions (voice DMA) */
#define SFX_KEEP        0
#define SFX_CHANGE      1
#define SFX_STOP        2

/* Music waveforms (matches editor names) */
#define WAVE_SQUARE     0
#define WAVE_TRIANGLE   1
#define WAVE_SAWTOOTH   2
#define WAVE_SINE       3

#define MUSIC_STEPS     32

/* Character positions */
#define POS_NONE   0
#define POS_LEFT   1
#define POS_CENTER 2
#define POS_RIGHT  3

/* Flag operation (used in scenes and choices) */
typedef struct {
    uint8_t flag_idx;
    uint8_t op;          // OP_ADD / OP_SUB / OP_SET / OP_TOGGLE
    int16_t value;
} flag_op_t;

/* Single choice option */
typedef struct {
    const char __far *text;
    uint16_t target;           // target node id
    uint8_t  flag_ops_count;
    const flag_op_t __far *flag_ops;
    /* optional condition */
    bool    has_condition;
    uint8_t cond_flag;
    uint8_t cond_op;
    int16_t cond_value;
} choice_opt_t;

/* Choice node */
typedef struct {
    const char __far *prompt;
    uint8_t choices_count;
    const choice_opt_t __far *choices;
    uint16_t default_target;
} choice_node_t;

/* Branch condition */
typedef struct {
    uint8_t  flag_idx;
    uint8_t  op;          // COND_EQ / COND_GT / etc
    int16_t  value;
    uint16_t target;
} branch_cond_t;

/* Branch node */
typedef struct {
    uint8_t branches_count;
    const branch_cond_t __far *branches;
    uint16_t default_target;
} branch_node_t;

typedef struct {
    uint8_t x;
    uint8_t y;
    uint8_t w;
    uint8_t h;
    const char __far *text;
    uint8_t required;
    uint8_t flag_ops_count;
    const flag_op_t __far *flag_ops;
    uint16_t target;
} hotspot_t;

/* Scene node (also used for title) */
typedef struct {
    const char __far *speaker;
    const char __far *dialogue;
    const char __far *title_main;
    const char __far *title_sub;
    uint8_t  menu_count;
    const char __far * const __far *menu_items;
    uint8_t  text_speed;
    uint32_t bg_color1;       // 0xRRGGBB
    uint32_t bg_color2;
    uint32_t speaker_color;   // 0xRRGGBB
    uint8_t  tb_style;
    uint8_t  bg_preset;
    uint8_t  particles;
    uint8_t  screen_fx;
    uint8_t  transition;
    uint8_t  char_anim;       /* 5=blink, 6=talking, 7=talk+blink: char2/char3 are alternate frames */
    uint8_t  bg_image_id;     // 0xFF = none
    uint8_t  fg_image_id;     // 0xFF = none; transparent foreground/group-shot layer
    uint8_t  fg_talk_patch_id; // 0xFF = none; foreground tile patch while text types
    uint8_t  fg_blink_patch_id;// 0xFF = none; foreground tile patch while waiting
    uint8_t  char_id;
    uint8_t  char_pos;
    uint8_t  char2_id;
    uint8_t  char2_pos;
    uint8_t  char3_id;        /* blink frame for talk+blink, 0xFF = none */
    uint8_t  music_action;
    uint8_t  music_track;
    uint8_t  music_loop;      /* 1 = loop, 0 = one-shot */
    uint8_t  sfx_id;          /* 0xFF = none */
    uint8_t  sfx_action;      /* SFX_* */
    uint8_t  sfx_loop;        /* 1 = loop/repeat, 0 = one-shot */
    uint8_t  pal_cycle_enable;
    uint8_t  pal_cycle_start; /* 0..15 */
    uint8_t  pal_cycle_len;   /* 0=disabled, else 2..16 */
    uint8_t  pal_cycle_speed; /* frames per step */
    uint8_t  flag_ops_count;
    const flag_op_t __far *flag_ops;
    uint16_t next_id;         // 0xFFFF = next in order
} scene_t;

typedef struct {
    scene_t scene;
    uint8_t hotspots_count;
    const hotspot_t __far *hotspots;
    uint16_t default_target;
} investigation_node_t;

/* One tracker channel (32 steps, 16th-note grid) */
typedef struct {
    uint8_t wave;                 /* WAVE_* */
    uint8_t base_vol;             /* 0..15 */
    uint16_t freq[MUSIC_STEPS];   /* precomputed WS freq divisors (0 = rest) */
    uint8_t vol[MUSIC_STEPS];     /* per-step volume (0..15) */
} music_channel_t;

/* One tracker track */
typedef struct {
    uint16_t bpm;                 /* editor BPM */
    uint8_t  length_steps;        /* typically 32 */
    uint8_t  channel_count;       /* 0..4 */
    music_channel_t ch[4];
} music_track_t;

typedef struct {
    uint32_t length_bytes;
    const uint8_t __far *data;    /* unsigned 8-bit PCM */
} sfx_asset_t;

/* Imported image asset: packed 4bpp tiles, row-major */
typedef struct {
    uint8_t  width_tiles;
    uint8_t  height_tiles;
    uint16_t tile_count;
    const uint16_t __far *palette;   /* 16 x 12-bit WS colors */
    const uint16_t __far *palette2;  /* optional second palette for per-tile character color */
    const uint8_t __far *tile_pals;  /* optional tile_count entries: 0 = palette, 1 = palette2 */
    const uint8_t __far *tiles;      /* tile_count * 32 bytes, packed 4bpp */
    const uint16_t __far * const __far *palettes; /* optional multi-palette table */
    uint8_t palette_count;
} image_asset_t;

typedef struct {
    uint16_t tile_idx;
    uint8_t  alt_pal;
    const uint8_t __far *alt_tile;
    uint8_t  base_pal;
    const uint8_t __far *base_tile;
} fg_anim_tile_t;

typedef struct {
    uint16_t count;
    const fg_anim_tile_t __far *tiles;
} fg_anim_patch_t;

/* Top-level node (tagged union) */
typedef struct {
    uint8_t type;
    union {
        scene_t       scene;
        choice_node_t choice;
        branch_node_t branch;
        investigation_node_t investigation;
    } data;
} node_t;

#endif /* GAME_TYPES_H */
