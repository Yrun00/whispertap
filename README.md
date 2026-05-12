# WhisperTap

Local Windows voice dictation daemon powered by `faster-whisper` and NVIDIA CUDA.

Press a mouse button, speak, press it again, and the recognized text is pasted into the active cursor position.

## Requirements

- Windows
- NVIDIA GPU with a recent driver
- Python 3.11
- A working microphone

Check Python and GPU:

```powershell
py -3.11 --version
nvidia-smi
```

Install Python 3.11 from the official Windows downloads page:

https://www.python.org/downloads/windows/

During Python installation, enable the Python launcher if asked. The command `py -3.11 --version` should work after installation.

## Install

Clone or download this repository, then run:

```powershell
install.bat
```

The script creates `.venv` and installs the dependencies from `requirements.txt`.

Do not commit `.venv` to GitHub. It is intentionally ignored by `.gitignore`.

## Run

```powershell
start_whispertap.bat
```

Or manually:

```powershell
.\.venv\Scripts\python.exe .\whispertap.py
```

On the first run, the `large-v3-turbo` Whisper model is downloaded to the local Hugging Face cache. Later runs reuse the cached model from disk.

The tray menu has `Exit and unload model`. Closing the app exits the Python process and releases GPU memory.

## Use

Press `Mouse Button 5` / `XButton2` once to start recording.

Press it again to stop recording and paste the recognized text.

While WhisperTap is running, `Mouse Button 5` is captured by a low-level Windows mouse hook, so browsers should not also treat it as Back/Forward.

`F13` also works as a fallback hotkey.

## Defaults

- model: `large-v3-turbo`
- device: `cuda`
- compute type: `float16`
- language: `ru`
- beam size: `1`

Useful variants:

```powershell
# Auto-detect language
.\.venv\Scripts\python.exe .\whispertap.py --language auto

# More accuracy, a bit slower
.\.venv\Scripts\python.exe .\whispertap.py --beam-size 3

# Less VRAM
.\.venv\Scripts\python.exe .\whispertap.py --compute-type int8_float16
```

## Notes

- The model stays on disk after the first download.
- The model is loaded into GPU memory only while `whispertap.py` is running.
- If the app is not running as administrator, it may not capture input inside elevated administrator windows.
