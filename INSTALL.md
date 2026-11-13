# Install and run

NotebookLM Harvester runs on your own computer. It opens a page in your browser at a
local address (127.0.0.1) and saves your NotebookLM Audio Overviews as MP3 files in a
folder you choose. It only reads from NotebookLM; it never changes anything there.

## What you need

| | Mac | Windows |
|---|---|---|
| Python 3.11 to 3.14 | `brew install python@3.13` | `winget install Python.Python.3.13` |
| ffmpeg and ffprobe | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| Google Chrome | signed in to the Google accounts that hold your notebooks | same |
| git (to get the app) | included with the Xcode tools | `winget install Git.Git` |

ffmpeg is a required install, not bundled (decision D-017).

## Get the app

```
git clone https://github.com/jordandigitals/notebooklm-harvester.git
```

Mac: put it anywhere, for example `~/repos/notebooklm-harvester`.
Windows: for example `C:\repos\notebooklm-harvester`.

## Start it

- Mac: double-click `run-mac.command`. The first time, macOS may ask whether to open it; choose Open. If it says the file is from an unidentified developer, right-click it, choose Open, then Open again.
- Windows: double-click `run-windows.bat`. If SmartScreen warns, choose More info, then Run anyway.

The first start sets up a private Python environment in `.venv` inside the app folder (about a minute). Later starts are quick. Your browser then opens the page. Keep the small terminal window open while you use the app; closing it stops the app.

The page address contains a one-time key, so only you can use it. Each start makes a new key.

## Use it

1. **Add account.** Pick a Chrome profile that is signed in to the Google account with your notebooks, or choose Sign in to sign in to any Google account in a Chrome window.
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

## Updating

```
git pull
```

Then start the app as usual; the launcher installs any changed requirements.
