# WSC VN Runtime Makefile
# Based on the official Wonderful Toolchain example structure

WONDERFUL_TOOLCHAIN ?= /opt/wonderful
TARGET = wswan/medium
include $(WONDERFUL_TOOLCHAIN)/target/$(TARGET)/makedefs.mk
MAKEFLAGS += --no-builtin-rules

NAME     ?= game
# Build outputs are keyed by NAME so you can produce multiple ROM variants
# (e.g. different CFLAGS) without object-file reuse causing mismatched builds.
BUILDDIR := build/$(NAME)
ELF      := $(BUILDDIR)/$(NAME).elf
ELF_S1   := $(BUILDDIR)/$(NAME)_stage1.elf
ROM      := $(NAME).wsc

# ── CYGNALS (Furnace tracker) ──
SOURCEDIRS  := src
INCLUDEDIRS := src

CYGNALS_DIR := third_party/cygnals
FUR2WS      := $(CYGNALS_DIR)/bin/windows_amd64/fur2ws.exe
MUSICDIR    := music
USE_CYGNALS := $(shell awk '/\#define USE_CYGNALS/{print $$3}' src/game_data.h 2>/dev/null || echo 0)

LIBS    := -lwse -lwsx -lws
LIBDIRS := $(WF_TARGET_DIR)

ifeq ($(USE_CYGNALS),1)
INCLUDEDIRS += $(CYGNALS_DIR)/include
LIBS        += -lcygnals
endif

INCLUDEFLAGS := $(foreach p,$(INCLUDEDIRS),-I$(p)) \
                $(foreach p,$(LIBDIRS),-isystem $(p)/include)

LIBDIRFLAGS  := $(foreach p,$(LIBDIRS),-L$(p)/lib)
ifeq ($(USE_CYGNALS),1)
LIBDIRFLAGS  += -L$(CYGNALS_DIR)/build/$(TARGET)
endif

CFLAGS  := -std=gnu11 -Wall -Wextra \
           -Wno-unused-parameter -Wno-sign-compare -Wno-type-limits \
           $(WF_ARCH_CFLAGS) $(INCLUDEFLAGS) \
           -ffunction-sections -fdata-sections -fno-common -Os \
           -MMD -MP

# Optional extra flags for experimental builds.
EXTRA_CFLAGS ?=
CFLAGS += $(EXTRA_CFLAGS)

ASFLAGS := -x assembler-with-cpp \
           $(WF_ARCH_CFLAGS) $(INCLUDEFLAGS) \
           -ffunction-sections -fdata-sections -fno-common -Os

LDFLAGS := -T$(WF_LDSCRIPT) $(LIBDIRFLAGS) $(WF_ARCH_LDFLAGS) $(LIBS)

SOURCES_C := $(shell find $(SOURCEDIRS) -name "*.c")
OBJS      := $(patsubst %.c,$(BUILDDIR)/%.o,$(SOURCES_C))

ifeq ($(USE_CYGNALS),1)
SOURCES_MUSIC := $(shell find -L $(MUSICDIR) -name "*.fur" 2>/dev/null)
OBJS          += $(addsuffix .o,$(addprefix $(BUILDDIR)/,$(SOURCES_MUSIC)))
CYGNALS_LIB   := $(CYGNALS_DIR)/build/$(TARGET)/libcygnals.a
endif

.PHONY: all clean clean_data convert

# Converter runs on the host. Wonderful installs a UCRT64 Python that usually
# has Pillow available, while MSYS Python often does not.
PY ?= $(if $(wildcard /ucrt64/bin/python.exe),/ucrt64/bin/python,python)

all: $(ROM)

convert:
	@if [ -z "$(JSON)" ]; then echo "Usage: make convert JSON=/path/to/project.wscvn.json"; exit 1; fi
	@$(PY) -c "import PIL" 2>/dev/null || (echo "Missing Pillow for $(PY). Install a Pillow-enabled Python (e.g. /ucrt64/bin/python) or add Pillow to this interpreter." && exit 1)
	$(PY) tools/convert_json.py "$(JSON)" src/

$(ROM): $(ELF_S1)
	$(BUILDROM) -c wfconfig.toml -o $(ROM) --output-elf $(ELF) $<
	@echo "=== BUILD COMPLETE ===" && ls -lh $(ROM)

$(ELF_S1): $(OBJS) $(CYGNALS_LIB)
	@mkdir -p $(BUILDDIR)
	$(CC) -r -o $@ $(OBJS) $(WF_CRT0) $(LDFLAGS) -lgcc

$(CYGNALS_LIB):
	$(MAKE) -C $(CYGNALS_DIR) TARGET=$(TARGET)

$(BUILDDIR)/%.o: %.c
	@mkdir -p $(@D)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILDDIR)/%.fur.o: %.fur
	@mkdir -p $(@D)
	@name=$$(basename "$<" .fur); \
	  echo "  CYGNALS $<"; \
	  "$(FUR2WS)" --i "$$name" --o "$(BUILDDIR)/$*.s" "$<"
	$(CC) $(ASFLAGS) -c -o $@ $(BUILDDIR)/$*.s

# Explicitly prevent Make from building any binary from src/*.c via implicit rules
src/%: src/%.c ;

clean:
	rm -rf $(BUILDDIR) $(ROM)

# Remove generated data sources (run `make convert ...` to regenerate).
clean_data:
	rm -f src/game_data.c src/game_data.h

-include $(OBJS:.o=.d)
