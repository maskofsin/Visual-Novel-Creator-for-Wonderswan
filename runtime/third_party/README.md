# Third-party dependencies

Do not commit downloaded toolchains or compiled third-party builds here.

For Cygnals/Furnace music support, install or clone Cygnals from its official upstream source during setup, then build it so this folder contains:

- `third_party/cygnals/include/cygnals.h`
- `third_party/cygnals/build/wswan/medium/libcygnals.a`
- `third_party/cygnals/bin/windows_amd64/fur2ws.exe` on Windows, or adjust `FUR2WS` in `runtime/Makefile` for your OS.

We will document the exact setup steps separately.
