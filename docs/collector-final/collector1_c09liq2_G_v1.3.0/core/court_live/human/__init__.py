"""S-0.3.24: Pico 2 only input + a normal, human-launched Chrome (no Selenium / webdriver / CDP / JS clicks).

- winio:        Windows read-only helpers (cursor position, screen size, DPI, screenshots, foreground window,
                raw-input watcher that tells the Pico 2 apart from the human's own mouse/keyboard), beeps
- calibration:  5-point fit 'Pico 0..10000 -> screen pixels' (DPI / multi-monitor measured, not assumed)
- pico_input:   every input through the Pico 2 S-0.2.14 (MOVE_TO / HOVER / CLICK_DWELL / WHEEL / KEY / TYPE),
                ARM before and DISARM after each action, first point synced to the real cursor, takeover stop
- bridge:       127.0.0.1-only HTTP endpoint for the read-only Chrome extension (DOM text / button positions)
- captcha:      captcha in any frame (nested kapanet reCAPTCHA too) -> stop, alarm, park the cursor BESIDE the
                checkbox (never on it, never a click), wait for the human, resume when the PDF arrives
- verify:       before every click: real cursor on the target, element hit-test, Windows screenshot check
- driver:       HumanChromeDriver - same interface as SeleniumDriver/FixtureDriver, so the g3b logic is kept
- requeue:      re-download queues (verified against records.jsonl / outbox / index) for a resumed long run
- spacing:      wider appraisal spacing (longer after a captcha)
The old Selenium driver stays in core/court_live/driver.py, used only with input.backend = "selenium" (off).
"""
VERSION = 'S-0.3.24'
