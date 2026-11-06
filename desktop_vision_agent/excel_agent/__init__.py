"""Excel -> desktop data-entry agent.

The package is organised in layers:

* ``config`` / ``logging_setup`` - settings and diagnostics
* ``excel``                     - workbook reading and status writing
* ``desktop``                   - window capture, UIA, input
* ``ai``                        - Gemini grounding and verification
* ``agent``                     - orchestration, calibration, state
* ``safety``                    - input lock, hotkeys, watchdog, overlay
* ``utils``                     - shared helpers
"""

__version__ = "0.1.0"
