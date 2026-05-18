/* game_data.h — AUTO-GENERATED. DO NOT EDIT. */
#ifndef GAME_DATA_H
#define GAME_DATA_H
#include <wonderful.h>
#include "game_types.h"

#define BUILD_ID        0x1B4Bu
#define START_NODE_IDX  0
#define NUM_NODES       82
#define NUM_FLAGS       0
#define USE_CYGNALS     0
#define NUM_CYG_SONGS   0
#define NUM_CYG_SFX     0
#define NUM_TRACKS      0
#define NUM_SFX         0
#define NUM_BG_ASSETS   23
#define NUM_FG_ASSETS   0
#define NUM_FG_ANIM_PATCHES 1
#define NUM_CHAR_ASSETS 0
#define FONT_STYLE      0
#define UI_SFX_TEXT     255
#define UI_SFX_CURSOR   255
#define UI_SFX_CONFIRM  255

extern const node_t __far * const __far NODES[NUM_NODES];
extern const int16_t        FLAG_INITIAL_VALUES[NUM_FLAGS > 0 ? NUM_FLAGS : 1];
extern const char __far * const __far NODE_NAMES[NUM_NODES > 0 ? NUM_NODES : 1];
extern const char __far * const __far BG_ASSET_NAMES[NUM_BG_ASSETS > 0 ? NUM_BG_ASSETS : 1];
extern const unsigned char __wf_rom * const __far CYG_SONGS[NUM_CYG_SONGS > 0 ? NUM_CYG_SONGS : 1];
extern const unsigned char __wf_rom * const __far CYG_SFX[NUM_CYG_SFX > 0 ? NUM_CYG_SFX : 1];
extern const music_track_t  __far TRACKS[NUM_TRACKS > 0 ? NUM_TRACKS : 1];
extern const sfx_asset_t    __far SFX_ASSETS[NUM_SFX > 0 ? NUM_SFX : 1];
extern const image_asset_t  __far SAVELOAD_BG;
extern const image_asset_t  __far BG_ASSETS  [NUM_BG_ASSETS   > 0 ? NUM_BG_ASSETS   : 1];
extern const image_asset_t  __far FG_ASSETS  [NUM_FG_ASSETS   > 0 ? NUM_FG_ASSETS   : 1];
extern const fg_anim_patch_t __far FG_ANIM_PATCHES[NUM_FG_ANIM_PATCHES > 0 ? NUM_FG_ANIM_PATCHES : 1];
extern const image_asset_t  __far CHAR_ASSETS[NUM_CHAR_ASSETS > 0 ? NUM_CHAR_ASSETS : 1];

#endif
