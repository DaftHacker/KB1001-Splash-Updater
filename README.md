# KB1001 Splash Updater

A safety-focused splash-screen updater for the AUMI/KB1001 Allwinner A333 tablet. It prepares one or two normal image files, patches the verified `bootloader_a` FAT16 image, flashes only `bootloader_a`, and verifies the flashed partition afterward.

> **Important:** This utility writes a bootloader partition. Read the safety notes below and keep a known-good stock image outside this repository.

## What it does

The updater automatically:

1. Verifies the local stock `bootloader_a` base image by exact size and SHA-256.
2. Verifies the connected tablet identity, root access, hardware, platform, and active slot.
3. Accepts PNG, JPG, BMP, and other Pillow-supported images.
4. Applies EXIF orientation and converts the artwork to exactly 800×1332.
5. Writes an exact 24-bit Windows BMP v3 / BI_RGB image.
6. Replaces `bootlogo.bmp` and `bootlogo-go.bmp` inside a clean stock image.
7. Verifies that changed bytes are confined to those two files.
8. Performs additional FAT consistency checks when available.
9. Reboots the verified tablet to fastboot.
10. Verifies the fastboot device/product.
11. Flashes **only** `bootloader_a`.
12. Reboots Android and verifies the partition SHA-256 through root.

It never flashes `bootloader_b`.

## Requirements

- Python 3
- Pillow (`pip install -r requirements.txt`)
- Android Platform Tools (`adb` and `fastboot`)
- A rooted KB1001 with working `su`
- The exact verified stock `bootloader_a` image described below

On Debian/Ubuntu, the optional FAT checker can be installed with:

```bash
sudo apt install python3-pil dosfstools adb fastboot
```

On Windows, install Android Platform Tools and either add them to `PATH` or place `adb.exe` and `fastboot.exe` under `tools/windows/`.

The repository intentionally does **not** redistribute device firmware images, patched boot images, APKs, or Android Platform Tools binaries.

## Stock base image

The updater expects:

```text
./base/bootloader_a-stock.img
```

The accepted stock image must match exactly:

```text
Size:   33,554,432 bytes
SHA256: 725e7f88eea5656c31d997cf68d9f92cd8108aa156970a97e2f1e93ed0cb55ae
```

If the canonical file is missing, the updater also checks for an exact `bootloader_a-current.img` next to the updater, in its parent directory, or in the current working directory. On Windows it additionally checks common local locations such as Downloads and Desktop.

The file is copied into `base/` only after its size and SHA-256 match the expected stock image.

## Usage

### Interactive menu

Linux/macOS:

```bash
./kb1001-splash
```

Windows:

```bat
kb1001-splash.cmd
```

The menu provides:

```text
1) Update splash image
2) Restore stock splash
3) Status
0) Exit
```

### One image

One image is written to both splash slots:

```bash
./kb1001-splash update image.png
```

Windows can also use the minimal file-input form:

```bat
kb1001-splash.cmd "C:\Images\splash.png"
```

Result:

```text
image.png -> bootlogo.bmp
image.png -> bootlogo-go.bmp
```

### Two images

Supply two images to use a different image for each splash slot:

```bash
./kb1001-splash update first.png second.png
```

Result:

```text
first.png  -> bootlogo.bmp
second.png -> bootlogo-go.bmp
```

On Windows, dragging one or two image files onto `kb1001-splash.cmd` uses the same behavior.

### Fit modes

The default mode is `cover`, which preserves aspect ratio, fills the complete 800×1332 splash area, and center-crops excess edges.

```bash
./kb1001-splash update image.png --fit cover
```

Use `contain` to preserve the complete image and add background space when necessary:

```bash
./kb1001-splash update image.png --fit contain
```

Change the contain-mode background:

```bash
./kb1001-splash update image.png --fit contain --background "#05060a"
```

Use `stretch` only when distortion is acceptable:

```bash
./kb1001-splash update image.png --fit stretch
```

### Prepare without flashing

Prepare, patch, and verify an image without touching the tablet:

```bash
./kb1001-splash prepare image.png
```

or:

```bash
./kb1001-splash update image.png --no-flash
```

### Restore stock splash

```bash
./kb1001-splash restore
```

### Status

```bash
./kb1001-splash status
```

### Noninteractive flashing

By default, the updater requires one final `FLASH` confirmation after verification. To intentionally skip that prompt:

```bash
./kb1001-splash update image.png --yes
```

or:

```bash
./kb1001-splash restore --yes
```

## Run archives

Every update, prepare, or restore operation creates a timestamped directory under `runs/`. Generated run data is intentionally ignored by Git.

An update run can contain files such as:

```text
input.<ext>
prepared-preview.png
prepared-bootlogo.bmp
prepared-bootlogo-go.bmp
bootloader_a-custom.img
verification.json
```

This keeps each generated flash image reproducible and inspectable without committing generated firmware images to the repository.

## Device safety checks

The updater is intentionally device-specific. Its current safety locks include:

```text
ro.hardware:   sun65iw1p1
platform:      earth
slot:          _a
fastboot:      sunxi
partition:     bootloader_a
```

The expected ADB/fastboot serial can be overridden with `--serial`, while the hardware, platform, slot, partition, stock-image hash, and fastboot-product checks remain enforced.

Example:

```bash
./kb1001-splash --serial YOUR_DEVICE_SERIAL status
```

`bootloader_b` was observed to be unusable as a fallback on the tested tablet, so this project does not write it or rely on it for recovery.

## Safety notes

- Keep an independent backup of the original stock `bootloader_a` image.
- Do not bypass the image hash or hardware/platform checks unless you have independently verified the target device and partition layout.
- Disconnect other ADB/fastboot devices before flashing; the updater refuses to proceed when multiple devices are detected.
- Use `prepare` or `--no-flash` first when testing new artwork or host environments.

## Repository layout

```text
KB1001-Splash-Updater/
├── base/                   # Local verified stock image goes here
├── runs/                   # Generated run artifacts
├── tools/
│   ├── linux/              # Optional local adb/fastboot binaries
│   └── windows/            # Optional local adb.exe/fastboot.exe
├── kb1001-splash           # Linux/macOS launcher
├── kb1001-splash.cmd       # Windows launcher
├── kb1001_splash_updater.py
├── requirements.txt
└── README.md
```
