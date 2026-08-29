# ==============================================================================
# cleanup.py - Storage Housekeeping
# ==============================================================================
# Periodically checks: is any chat currently playing/paused on a call?
#   - If NO chat has an active call anywhere -> wipe cache/ and downloads/
#     completely (nothing is using those files, so no risk).
#   - If at least ONE chat is active -> skip this round and check again
#     next interval, so we never touch a file mid-stream.
# This keeps disk usage under control without breaking active playback.
# ==============================================================================

import asyncio
import time
from pathlib import Path

from tito import db, logger, tasks

# How often to check whether it's safe to wipe (seconds).
# Runs every 4 hours, per request - this is just housekeeping, not something
# that needs to run every couple of minutes.
CLEANUP_INTERVAL = 4 * 60 * 60

# Folders to keep clean
WATCHED_DIRS = ("downloads", "cache")

# Never touch a file that was written in the last N seconds, even if
# nothing appears to be "active" - it might be a download that just hasn't
# registered yet, or is about to be picked up. Extra safety net on top of
# the active_downloads/active_calls checks below.
MIN_FILE_AGE = 60


def _wipe_folder(folder: Path, protected_ids: set) -> tuple:
    """Delete every file inside a folder (keeps the folder itself). Returns (count, bytes).

    Skips any file whose video ID is currently downloading, and any file
    that's simply too fresh to be safe to touch yet.

    NOTE: This function is 100% synchronous/blocking (iterdir + unlink are
    real disk syscalls). It must NEVER be awaited directly inside a coroutine —
    doing so freezes the whole asyncio event loop, which means every other
    task (including handling incoming /play commands) stalls until the wipe
    finishes. Always call this through asyncio.to_thread(...), same as the
    rest of the codebase does for blocking work (see core/youtube.py).
    """
    removed_count = 0
    freed_bytes = 0
    now = time.time()
    for file in folder.iterdir():
        try:
            if not file.is_file():
                continue
            # Skip files still being written to (.part/.ytdl/temp artifacts)
            if file.name.endswith((".part", ".ytdl", ".temp")):
                continue
            # Skip anything tied to a video that's currently downloading
            if any(file.stem.startswith(vid) for vid in protected_ids):
                continue
            stat = file.stat()
            if now - stat.st_mtime < MIN_FILE_AGE:
                continue
            size = stat.st_size
            file.unlink(missing_ok=True)
            removed_count += 1
            freed_bytes += size
        except Exception as e:
            logger.debug(f"cleanup_worker: skipped {file}: {e}")
            continue
    return removed_count, freed_bytes


async def cleanup_worker():
    """Background loop: wipe cache/ and downloads/ whenever nothing is playing."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)

            # If any chat has an active call (playing or paused), it's not safe
            # to wipe — one of those files might still be in use.
            if db.active_calls:
                continue

            from tito import yt
            # If anything, anywhere, is actively downloading right now,
            # skip this round entirely rather than risk racing it.
            if yt.active_downloads:
                continue

            removed_count = 0
            freed_bytes = 0

            for folder_name in WATCHED_DIRS:
                folder = Path(folder_name)
                if not folder.exists():
                    continue
                # Offload the blocking disk work to a worker thread so the
                # event loop stays free to handle /play and everything else
                # while the wipe is in progress. This is the actual fix:
                # previously _wipe_folder ran inline on the event loop and
                # froze all command handling until it returned.
                count, size = await asyncio.to_thread(_wipe_folder, folder, set(yt.active_downloads))
                removed_count += count
                freed_bytes += size

            if removed_count:
                freed_mb = round(freed_bytes / (1024 * 1024), 2)
                logger.info(
                    f"🧹 Cleanup: no active playback anywhere, wiped {removed_count} "
                    f"file(s) from cache/downloads, freed {freed_mb}MB."
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Critical error in cleanup_worker: {e}")
            await asyncio.sleep(5)


# Always run the cleanup sweep in the background
tasks.append(asyncio.create_task(cleanup_worker()))
