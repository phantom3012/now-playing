import asyncio
import argparse
import signal
import pygame
import common.logger_utils as logger_utils

# Fetch our standardized native logger
logger = logger_utils.get_logger("Main")

# ==============================================================================
# DISPLAY ENGINE SELECTION
#
# Two interchangeable display engines are available. Each subclasses
# BaseNowPlayingDisplay and is aliased to NowPlayingDisplay on import, so they
# can be swapped freely:
#
#   standard  -> display/album_sleeve.py    (AlbumSleeveDisplay, default)
#   vinyl     -> display/spinning_vinyl.py  (SpinningVinylDisplay)
#
# Choose one at launch with the --display flag, e.g.:
#
#   python main.py                 # uses "standard" (default)
#   python main.py --display vinyl # uses the spinning-vinyl engine
#
# To make the vinyl engine the permanent default instead, change
# DEFAULT_DISPLAY below to "vinyl" (the systemd service calls run.sh with no
# arguments, so this constant is what it will use unless run.sh passes a flag).
# ==============================================================================
DEFAULT_DISPLAY = "standard"


def load_display_engine(choice):
    """Imports and returns the NowPlayingDisplay class for the chosen engine.

    The import is deferred until after the choice is known so we only import
    the engine we actually use (each pulls in Pygame/hardware setup).
    """
    if choice == "vinyl":
        logger.info("Display engine selected: vinyl (spinning-vinyl)")
        from display.spinning_vinyl import SpinningVinylDisplay as NowPlayingDisplay
    else:
        logger.info("Display engine selected: standard")
        from display.album_sleeve import AlbumSleeveDisplay as NowPlayingDisplay
    return NowPlayingDisplay


def parse_args():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Now Playing - ambient music display for Raspberry Pi."
    )
    parser.add_argument(
        "--display",
        choices=["standard", "vinyl"],
        default=DEFAULT_DISPLAY,
        help=f"Which display engine to use (default: {DEFAULT_DISPLAY}).",
    )
    return parser.parse_args()


class NowPlayingApp:
    def __init__(self, display_choice=DEFAULT_DISPLAY):
        """Initializes the main application, display, and audio engines."""
        logger.info("Initializing NowPlayingApp...")

        # Resolve which display engine to use, then import it
        NowPlayingDisplay = load_display_engine(display_choice)

        # Deferred here (not at module top) so --help / arg parsing stays fast
        from audio.audio_engine import NowPlayingRecognizer

        # 1. Start the Display Engine
        logger.info("Booting Display Engine...")
        self.display = NowPlayingDisplay(fullscreen=True)

        # Force the initial state to the clock screensaver to bypass the "Now Playing" flash
        self.display.display_state = 'CLOCK'
        self.display.draw_frame()

        # 2. Start the Audio Engine quietly in the background
        logger.info("Booting Audio Engine...")
        self.recognizer = NowPlayingRecognizer()

        # State Tracking
        self.running = False
        self.current_title = None
        self.current_artist = None
        self.first_song_found = False
        self.rec_task = None
        self.ml_miss_count = 0
        logger.success("Initialization complete. State tracking ready.")

    def _update_status(self, msg, fade=True):
        """Callback to safely push status updates to the display engine."""
        # We only show full status screens if an album cover isn't actively on screen.
        if not self.first_song_found:
            self.display.set_status(msg, fade=fade)
        else:
            # If we ARE showing album art, extract the retry count to update the spinner!
            if "(" in msg and "/" in msg:
                try:
                    # Extracts the "1" from "(1/3)" to use as the true retry count
                    retry_num = int(msg.split('(')[1].split('/')[0])
                    self.display.set_refreshing(True, retry_text=str(retry_num))
                except Exception:
                    pass

        logger.info(f"Status Callback Update: {msg}")

    def _process_song_match(self, song_dict):
        """Handles state tracking and triggers a display update when a new song matches."""
        title = song_dict.get('title')
        artist = song_dict.get('artist')

        # Check if this is indeed a new song track to avoid redundant updates
        if title != self.current_title or artist != self.current_artist:
            logger.success(f"---> NEW MATCH: {title} by {artist} <---")
            self.current_title = title
            self.current_artist = artist
            self.first_song_found = True
            logger.info("Pushing song data to Display Engine...")
            self.display.update_song(song_dict)
        else:
            logger.info(f"Duplicate match detected ({title}), maintaining current display.")

    def _clear_track_state(self):
        """Helper to cleanly wipe the track tracking variables and return to the screensaver."""
        self.first_song_found = False
        self.current_title = None
        self.current_artist = None
        self.ml_miss_count = 0
        self.display.set_clock()

    def _handle_ml_miss(self):
        """Handles logic for when ML detects no music, utilizing the 2-strike buffer."""
        if self.first_song_found:
            self.ml_miss_count += 1
            if self.ml_miss_count >= 2:
                logger.info("Music stopped playing (2 consecutive misses). Clearing track state & returning to screensaver.")
                self._clear_track_state()
            else:
                logger.info(f"ML missed ({self.ml_miss_count}/2). Music might be quiet. Keeping screen active...")
        else:
            # We aren't displaying anything right now, safely fall back to the clock
            self._clear_track_state()

    async def _recognition_loop(self):
        """Background task that continuously listens to the room using divided state checks."""
        logger.info("Entering background recognition loop...")

        # Initial boot status ensures we are safely on the clock
        self.display.set_clock()

        while self.running:
            try:
                # 1. Silently monitor room via ML in background (captures 3s sample)
                logger.info("Silently scanning room for music (ML)...")
                is_music, raw_audio, actual_rate = await self.recognizer.check_for_music()

                if is_music:
                    self.ml_miss_count = 0

                    # 2. First pass triggered! Show the Listening state (fade screensaver out, listening in)
                    logger.success("RESULT: [MUSIC DETECTED!] Triggering verification...")
                    self._update_status("Listening to room...", fade=True)

                    # 3. VERIFICATION PASS: Record another sample to confirm and double-dip
                    logger.info("Verifying music presence (Second ML pass)...")
                    is_music_confirmed, confirmed_audio, confirmed_rate = await self.recognizer.check_for_music()

                    if is_music_confirmed:
                        logger.success("Music confirmed! Initiating Shazam cloud recognition...")

                        # TURN SPINNER ON WITH INITIAL RETRY NUMBER (0 since it's the first attempt)
                        self.display.set_refreshing(False, retry_text="0")

                        try:
                            # 4. Double-dip on the confirmed audio to save 8 seconds on Try 0!
                            song_dict = await self.recognizer.recognize_song(
                                max_retries=3,
                                status_callback=self._update_status,
                                pre_recorded_audio=confirmed_audio,
                                pre_recorded_rate=confirmed_rate
                            )
                        finally:
                            # GUARANTEE SPINNER TURNS OFF NO MATTER WHAT
                            self.display.set_refreshing(False)

                        if song_dict and song_dict.get('is_recognized'):
                            logger.success("Shazam cloud recognition successful!")

                            # 5. Successful match! Show "Now Playing" bridging screen briefly
                            self._update_status("Now Playing", fade=True)
                            await asyncio.sleep(2)

                            # 6. Hand over data to display engine to show album art
                            self._process_song_match(song_dict)

                            # Keep displaying the active playing song for 15 seconds before scanning again
                            logger.info("Holding active display for 10 seconds...")
                            await asyncio.sleep(10)
                        else:
                            logger.warning("Shazam recognition failed. Returning to screensaver.")
                            self._clear_track_state()
                    else:
                        logger.info("Second ML pass found no music (False positive/music stopped). Reverting...")
                        self._handle_ml_miss()
                else:
                    logger.info("RESULT: [NO MUSIC]. Waiting...")
                    self._handle_ml_miss()

            except asyncio.CancelledError:
                logger.info("Recognition loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in recognition loop: {e}")

            # Cooldown delay between checks to conserve Pi system resources
            await asyncio.sleep(3)

    def _handle_pygame_events(self):
        """Processes keypresses and window close hooks from SDL."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logger.info("SDL QUIT event received.")
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    logger.info("ESC key pressed. Exiting...")
                    self.running = False

    async def run(self):
        """Runs the main application thread loops cleanly."""
        logger.info("Starting Main App Loop (Target: 60 FPS)...")
        self.running = True

        # Fire off the audio detection loop in the background asynchronously
        self.rec_task = asyncio.create_task(self._recognition_loop())

        # Establish target loop metrics to guarantee a flawless 60 FPS.
        target_fps = 60
        frame_time = 1.0 / target_fps

        try:
            while self.running:
                loop_start = asyncio.get_event_loop().time()

                # Check events and render current state
                self._handle_pygame_events()
                self.display.draw_frame()

                # Calculate real draw elapsed time, and sleep precisely for remaining frame duration
                elapsed = asyncio.get_event_loop().time() - loop_start
                await asyncio.sleep(max(0.001, frame_time - elapsed))

        finally:
            self._shutdown()

    def _shutdown(self):
        """Safely cleans up background tasks and hardware interfaces."""
        logger.info("Shutting down...")
        if self.rec_task:
            self.rec_task.cancel()
        self.recognizer.close()
        pygame.quit()
        logger.success("Hardware interfaces closed cleanly.")


async def main(display_choice=DEFAULT_DISPLAY):
    app = None
    try:
        app = NowPlayingApp(display_choice=display_choice)
        await app.run()
    finally:
        try:
            pygame.quit()
        except Exception:
            pass


if __name__ == "__main__":
    args = parse_args()
    # Translate SIGTERM (what `systemctl restart/stop` sends) into a clean
    # exit so asyncio unwinds and the finally block runs pygame.quit().
    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(main(display_choice=args.display))
    except KeyboardInterrupt:
        pass