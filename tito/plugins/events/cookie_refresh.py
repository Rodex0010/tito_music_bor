# ==============================================================================
# cookie_refresh.py - Automatic Cookie Refresh
# ==============================================================================
# Cookies passed via COOKIE_URL in .env are only fetched once at startup by
# default, so they slowly go stale while the bot keeps running for days.
# This background loop re-downloads them from the same URL(s) periodically,
# so as long as you keep the pasted content behind COOKIE_URL up to date,
# the bot picks up the fresh cookies automatically - no restart needed.
# ==============================================================================

import asyncio

from tito import config, logger, tasks, yt

# How often to re-pull cookies (hours), configurable via COOKIE_REFRESH_HOURS
REFRESH_INTERVAL = max(1, config.COOKIE_REFRESH_HOURS) * 60 * 60


async def cookie_refresh_worker():
    """Background loop: periodically re-download cookies from COOKIE_URL."""
    if not config.COOKIES_URL:
        # Nothing to refresh - no COOKIE_URL configured.
        return

    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL)
            logger.info("🍪 Refreshing cookies from COOKIE_URL...")
            await yt.refresh_cookies(config.COOKIES_URL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in cookie_refresh_worker: {e}")
            await asyncio.sleep(5)


# Always run the refresh loop in the background (it no-ops if no COOKIE_URL
# is configured).
tasks.append(asyncio.create_task(cookie_refresh_worker()))
