# VALORANT Keyboard Event Logger

This repository contains a Windows-only Tkinter application that records keyboard key-down and key-up events while VALORANT is the foreground process and stores them in CSV files with UTC timestamps and elapsed milliseconds.

## Python version

- Python 3.10 or newer recommended
- Windows 10/11 required for the low-level keyboard hook

## Installation

1. Install Python 3.10+ from https://www.python.org/downloads/windows/
2. In a Windows Command Prompt, verify Python is available:

   ```cmd
   py --version
   ```

3. No third-party packages are required for the core implementation because it uses Python's built-in `tkinter` and `ctypes` modules.

## Launch

From a Windows Command Prompt in the repository directory:

```cmd
py valorant_keyboard_logger.py
```

A `.cmd` launcher is also included for convenience:

```cmd
launch_valorant_logger.cmd
```

## Using the application

1. Click `Start Recording`.
2. Switch to VALORANT, then play normally.
3. The logger records only while VALORANT is the foreground process.
4. If the game loses focus, the UI changes to `Inactive - VALORANT not in foreground` and no events are captured until focus returns.
5. Click `Insert Sync Marker` to add a synchronization marker to a separate marker CSV file.
6. Click `Stop Recording` to finalize the session and flush the last pending data.
7. The output directory defaults to `Documents\valorant_keyboard_logs` unless you choose another folder.

## CSV format

The main CSV file contains rows like:

```csv
session_id,event_index,timestamp_utc,elapsed_ms,event_type,key_name,virtual_key_code
session_20261027T120000_1234abcd,1,2026-10-27T12:00:00.125000Z,125.0,key_down,W,87
session_20261027T120000_1234abcd,2,2026-10-27T12:00:00.180000Z,180.0,key_up,W,87
session_20261027T120000_1234abcd,3,2026-10-27T12:00:00.240000Z,240.0,key_down,SPACE,32
```

Columns:
- `session_id`: unique session identifier
- `event_index`: sequential event counter for that session
- `timestamp_utc`: absolute UTC timestamp in ISO 8601 format with microsecond precision
- `elapsed_ms`: elapsed time since recording start, measured with `time.perf_counter()` and reported in milliseconds
- `event_type`: `key_down` or `key_up`, plus `session_start` / `session_end` metadata rows written at the beginning and end of a session
- `key_name`: human-readable key such as `W`, `SPACE`, `F1`, `LEFT`
- `virtual_key_code`: Windows virtual-key value (for example `87` for `W`)

Sync markers are stored in a separate CSV file named like:

```text
<session_id>_markers.csv
```

with columns:

```csv
session_id,timestamp_utc,elapsed_ms,marker_label
session_...,2026-10-27T12:00:00.500000Z,500.0,sync_1729990000000
```

## Testing and limitations

Validation that can be performed in this environment:

- Source compiles cleanly with Python syntax validation.
- The Tkinter UI logic and CSV-writing logic have been implemented and structured for use on Windows.
- The foreground detection and hook logic are implemented using Windows APIs.

Limitations that cannot be fully tested here:

- This environment does not run VALORANT, so in-game capture cannot be verified from this sandbox.
- Riot Vanguard or another anti-cheat may block low-level hooks, which may make the logger unusable in real gameplay.
- Hook-based input capture gets the events from the system, but it cannot guarantee low-latency frame-accurate synchronization with a separate screen recorder.

This tool does not try to bypass anti-cheat safeguards or manipulate game memory.
