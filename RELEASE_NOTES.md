# Release notes — the body of the next GitHub release

<!-- The release workflow (.github/workflows/release.yml) publishes this file verbatim
     as the release description and appends the installer's SHA256. Update it, tag, push
     the tag — that's the whole release process. -->

A local-first desktop app that reads your documents, files them where they belong, and lets you find anything — documents *and* photos — by describing it in plain English.

**Everything runs on your machine.** The language model, the embeddings, your files: nothing is uploaded anywhere.

### Fixed since 0.1.1

- **iPhone photos work.** HEIC images are now decoded natively — a folder of phone photos used to index as "no images in that folder" even though every file in it was a picture. The app also tells the truth now: "no images found" and "found 34 but couldn't read these" are different messages.
- **Switching pages no longer wipes your work.** The chat conversation, your half-typed message, and image search results all survive moving between Chat, Library, Images and Settings.
- **Clear chat** — the broom button in the composer starts a fresh conversation.
- **Reset database** — a Danger-zone option in Settings forgets everything the app has read (records, search indexes, undo history) without touching your actual files or your settings.
- Big photos no longer show as broken icons in search results — thumbnails are generated in the background, HEIC included, and the UI no longer freezes while they load.
- Live progress while indexing photos ("Looking at every photo… 120/500") instead of a spinner that looks like a hang.
- Shift+Enter makes a newline in the chat box; Enter sends.
- "organize my pictures folder" organizes it instead of searching photos, and "what does that file say" no longer triggers the filing flow.
- One corrupt photo no longer aborts indexing for the fifteen good ones next to it, and a photo can no longer be shown under another photo's name.
- Folder paths pasted with quotes (Explorer's "Copy as path") are accepted everywhere.
- Apply/Cancel/Undo buttons can't be double-clicked into filing a folder twice.
- Fixed a background status check that polled the database ten times a second on every page.
- Releases are now built, boot-tested and published automatically by CI when a version tag is pushed.

### What it does

- 📂 **Drop a folder into the chat** — it reads every file, builds a knowledge base, and proposes where each one belongs, with a reason. Nothing moves until you click Apply, and every move can be undone.
- 💬 **Ask your documents anything** — answers cite the exact file and page. If your library doesn't cover it, it says so instead of making something up.
- 🖼️ **Find photos by describing them** — *"a man lifting a baby"*, *"sunset on a beach"*. No tags, no filenames; it looks at the pictures. Now including iPhone HEIC photos.
- 🗂️ **Works while you're not looking** — lives in the system tray, watches your Downloads folder, and files new documents automatically. Closing the window doesn't stop it; quit from the tray icon.

### Install

1. Download **VectorVault-0.1.2-win64.msi** below and run it.
2. **Windows SmartScreen will warn you** — the installer is not code-signed yet. Click *More info → Run anyway*.
3. On first launch, a setup screen detects your GPU and RAM, recommends a language model that actually fits your hardware, installs [Ollama](https://ollama.com), and downloads the model with a progress bar. Internet is needed for this one step; after that the app is fully offline.

Installing 0.1.2 over an earlier version upgrades in place; your library and settings are kept.

**Requirements:** Windows 10/11 (64-bit). ~1.2 GB for the app plus 1–9 GB for the language model depending on what your hardware supports. A GPU with ≥4 GB VRAM is recommended but not required.

### Good to know

- The academic classifier ships with a **sample B.Tech CSE curriculum**. Edit `curriculum.yaml` (see the README) to teach it your own subjects — it rebuilds its knowledge base automatically.
- Scanned/photographed PDFs are detected and routed to the Review queue, but OCR isn't built yet — that's the next milestone.
- The AI never executes commands against your files. It proposes a plan; a logged, reversible engine applies it. Classification thresholds were calibrated by measurement, not guesswork.

### Under the hood

Ollama (qwen2.5, hardware-matched) · bge-small embeddings · CLIP image search · pillow-heif HEIC decoding · SQLite + write-ahead move log · 132 tests
