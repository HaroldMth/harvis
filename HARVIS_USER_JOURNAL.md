# HARVIS User Journal

*This journal logs all difficulties, bugs, bottlenecks, feature requests, and improvements encountered during tasks. Each entry is added after a task is completed.*

---

## Initial Setup & Current State
- **Date**: 2025-06-25
- **Location**: `/home/harold/HARVIS/`
- **Current Tools**:
  - YouTube Downloader Helper (`yt-download-helper/cli.js`)
  - HARVIS core system (Telegram-integrated AI assistant)

---

## Tasks & Entries

### 1. YouTube Downloader Helper Setup
**Date**: 2025-06-25
**Task**: Create a YouTube downloader tool (`yt-down`) for Harold.

#### What Was Done:
- Installed the `yt-download-helper` tool in `~/yt-download-helper/`.
- Created a CLI script (`cli.js`) to download YouTube videos/audio in `mp3` or `mp4` formats with quality options (`128kbps`, `360p`, `720p`).
- Tested the tool and successfully downloaded *Bxtch WTF? (Original)* as an MP3 to `~/Downloads/ytdownloads/`.

#### Difficulties/Bugs:
- **Issue**: Initial attempt to download `mp4 360p` failed with error: `No mp4 360p found`.
  - **Root Cause**: The Gifted API may not support all quality/format combinations for every video.
  - **Solution**: Retried with `mp3 128kbps`, which worked.
  - **Note**: Need to improve error handling to suggest alternative formats/qualities.

#### Bottlenecks:
- Dependency on the Gifted API (`apikey=gifted`).
  - **Risk**: API may change, rate-limit, or become unavailable.
  - **Suggestion**: Add a fallback to other APIs (e.g., yt-dlp) or local processing.

#### Features to Add:
1. **Local Processing Fallback**:
   - Use `yt-dlp` or `youtube-dl` as a fallback if the Gifted API fails.
   - **How to Implement**:
     - Add a `fallback` flag to the CLI (e.g., `yt-down "url" mp3 128kbps --fallback`).
     - If the API fails, execute a shell command to download using `yt-dlp`.
     - Example command:
       ```bash
       yt-dlp -x --audio-format mp3 -o "~/Downloads/ytdownloads/%(title)s.%(ext)s" "youtube_url"
       ```
     - **Testing**: Test in a temporary directory before integrating.

2. **Batch Downloading**:
   - Add support for downloading multiple videos/audio files in one command.
   - **How to Implement**:
     - Accept a file path (e.g., `urls.txt`) with one URL per line.
     - Loop through the file and download each URL.
     - Example usage:
       ```bash
       yt-down --batch urls.txt mp3 128kbps
       ```

3. **Progress Bar**:
   - Add a progress bar for downloads to improve user experience.
   - **How to Implement**:
     - Use a library like `progress` or `ora` in `cli.js`.
     - Example:
       ```javascript
       const progress = require('progress');
       const bar = new progress('[:bar] :percent :etas', { total: 100 });
       ```

4. **Metadata Tagging**:
   - Automatically add metadata (title, artist, album) to downloaded MP3s.
   - **How to Implement**:
     - Use a library like `music-metadata` or `ffmpeg` to inject metadata.
     - Example:
       ```javascript
       const mm = require('music-metadata');
       const metadata = { title: 'Track Title', artist: 'Artist Name' };
       ```

---

## Next Steps
- [ ] Implement local processing fallback (`yt-dlp`).
- [ ] Test batch downloading.
- [ ] Add progress bar for downloads.
- [ ] Research metadata tagging libraries.

---
