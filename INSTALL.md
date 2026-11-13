# Install and run

NotebookLM Harvester runs on your own computer. It opens a page in your browser at a
local address (127.0.0.1) and saves your NotebookLM Audio Overviews as MP3 files in a
folder you choose. It only reads from NotebookLM; it never changes anything there.

## Easiest: the ready-made app (no Python or ffmpeg needed)

| | Download | First start |
|---|---|---|
| Mac (Intel, also runs on Apple Silicon through Rosetta) | `NotebookLM-Harvester-mac-x86_64-<version>.zip` | Unzip, drag the app to Applications, right-click it and choose Open, then Open again. If macOS still refuses: System Settings, Privacy and Security, Open Anyway. |
| Windows (64-bit) | `NotebookLM-Harvester-win-x64-<version>.zip` | Unzip to a short path (for example `C:\NLH`), open the `NotebookLM Harvester` folder, double-click `NotebookLM Harvester.exe`. If SmartScreen warns: More info, then Run anyway. |

The apps are not signed (signing is deferred), which is why the system asks once.
On a Mac, the first time the app reads a Chrome sign-in, macOS asks to let it use "Chrome
Safe Storage" from the keychain: enter your Mac password and choose Always Allow.

The ready-made Mac app adds accounts from Chrome profiles only (the Sign in to Google
window needs an extra 157 MB component that is left out). To add an account that is not
in Chrome yet, sign in to it in Chrome first, then add it from the list.
The ready-made Windows app includes the Sign in to Google window, because on Windows the
Chrome route needs Chrome fully closed and can trip Windows Security (see Windows notes).

To stop the app, press Quit on the page. Starting it again opens the same page.

## Move to another computer

Sign-ins are never copied between computers; each computer signs in on its own.

1. Install the ready-made app on the new computer (above) and start it.
2. Add account: pick the Chrome profile on that computer that is signed in to the Google account with your notebooks. Repeat for each account.
3. Choose folder for each account. To share one library between computers, pick the same synced folder (Google Drive, MEGA or OneDrive) on both.
4. Press **Scan folder**. The app reads the tags of the MP3s already in that folder and records them, so nothing is downloaded twice.
5. Refresh notebooks, then harvest.

## Portable mode (optional)

Put an empty folder named `data` and an empty file named `portable` beside the app (beside
the `.app` on a Mac, beside the `.exe` on Windows). The app then keeps its data, including
your Google sign-ins, in that `data` folder and shows a warning the first time. Keep such a
copy only on a drive that is yours alone; never on a shared or easily lost drive.

## Running from source

### What you need

| | Mac | Windows |
|---|---|---|
| Python 3.11 to 3.14 | `brew install python@3.13` | `winget install Python.Python.3.13` |
| ffmpeg and ffprobe | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| Google Chrome | signed in to the Google accounts that hold your notebooks | same |
| git (to get the app) | included with the Xcode tools | `winget install Git.Git` |

ffmpeg is a required install, not bundled (decision D-017).

### Get the app

```
git clone https://github.com/jordandigitals/notebooklm-harvester.git
```

Mac: put it anywhere, for example `~/repos/notebooklm-harvester`.
Windows: for example `C:\repos\notebooklm-harvester`.

### Start it

- Mac: double-click `run-mac.command`. The first time, macOS may ask whether to open it; choose Open. If it says the file is from an unidentified developer, right-click it, choose Open, then Open again.
- Windows: double-click `run-windows.bat`. If SmartScreen warns, choose More info, then Run anyway.

The first start sets up a private Python environment in `.venv` inside the app folder (about a minute). Later starts are quick. Your browser then opens the page. Keep the small terminal window open while you use the app; closing it stops the app.

The page address contains a one-time key, so only you can use it. Each start makes a new key.

## Use it

1. **Add account.** Pick a Chrome profile that is signed in to the Google account with your notebooks, or choose Sign in to sign in to any Google account in a Chrome window. On Windows, Sign in is listed first and recommended (see Windows notes).
2. **Choose folder.** Pick where the MP3 files go. Any folder works, including one synced by Google Drive, MEGA or OneDrive. Each account remembers its own folder.
3. **Refresh notebooks.** The app reads your notebooks and shows each audio overview as New, Saved, Saved but file missing, or Not finished.
4. **Harvest all new**, or tick some overviews and choose **Harvest selected**.

Each notebook gets its own subfolder, named after the notebook, and every overview from that notebook goes into it (the folder is made once and reused). Files are named after the overview's own title, for example `The Viral Referral Method/How WhatsApp AI Automates Viral Referrals.mp3`. Files saved before this change stay where they are; the app never moves files. If you rename a notebook in NotebookLM, its next overviews go into a folder with the new name. A name already in the folder gets ` (2)`; the app never overwrites or deletes a file. Harvesting again downloads nothing that was already saved.

If an account shows **Sign in again**, press it. For accounts added from Chrome this re-reads Chrome's sign-in (Chrome rotates its Google cookies, so a copied sign-in goes stale after some hours).

## Where the app keeps its data

| | Mac | Windows |
|---|---|---|
| App data | `~/.nlh/` | `%LOCALAPPDATA%\NLH\` |

It holds the saved Google sign-ins (`home/profiles/<account>/storage_state.json`, private to your user), the ledger of saved files (`ledger.db`) and a log (`logs/nlh.log`, no passwords or cookies). Set `NLH_HOME` to use another folder. Never share or upload this folder.

On Windows the folder is inside your user profile, which Windows already limits to your account.

## Windows notes

- **Two ways to add an account; Sign in to Google is recommended on Windows.** It opens a separate Chrome window kept just for this app (it looks like a blank guest window; that is expected); you sign in once and the app remembers it, your normal Chrome stays open and untouched, nothing reads Chrome's saved data, and Windows Security raises no alert. Use a Chrome profile is the quick start for an account already signed in to Chrome, with the two catches below.
- **Quit Chrome before adding an account from a Chrome profile.** Windows keeps Chrome's sign-in data locked while Chrome runs, including in the background. Close every Chrome window, then right-click the Chrome icon near the clock (if shown) and choose Exit. Open this page in another browser, such as Edge, for that step. You can reopen Chrome as soon as the account shows Signed in. The same applies to **Sign in again** on an account added from Chrome.
- **Windows Security may quarantine the Chrome reader.** Reading Chrome's saved sign-in is what info-stealing malware also does, so Microsoft Defender can flag the reader file (`rookie_cookies.pyd`, in `.venv` or inside the ready-made app's `_internal` folder) as `Trojan:Win64/InfoStealer`. It is part of the pinned notebooklm-py dependency and only reads the Google sign-in cookies it needs from your own Chrome on your own computer. To use the Chrome route, open Windows Security, Virus and threat protection, Protection history, select that entry, then Actions and Allow on device. If you would rather not, use **Sign in** instead: it signs in once in a separate Chrome window kept just for this app and never reads your normal Chrome.
- **Long paths.** On Windows, long overview titles are shortened automatically so the saved path stays under 240 characters (the MP3 tags keep the full title), and an output folder too deep even for that is refused with a plain message asking for a shorter folder.

## Updating

```
git pull
```

Then start the app as usual; the launcher installs any changed requirements.
