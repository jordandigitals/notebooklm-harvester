NotebookLM Harvester for Windows

1. Unzip this folder anywhere you like, for example C:\NLH (a short path is best).
2. Open the "NotebookLM Harvester" folder and double-click "NotebookLM Harvester.exe".
   The first time, Windows SmartScreen may say it protected your PC, because the app is
   not signed. Choose "More info", then "Run anyway".
3. The app opens a page in your browser. Use Add account:
   - Sign in to Google (recommended): a separate Chrome window opens just for this app.
     It looks like a blank guest window; that is expected. Sign in to Google there once
     and the app remembers it. Your normal Chrome stays open and is not touched.
   - Or use a Chrome profile (quick start): first quit Chrome completely (close every
     window, then right-click the Chrome icon near the clock and choose Exit). Open the
     page in another browser, such as Edge, for that step. Then press Add next to the
     profile.
4. If Windows Security reports "Trojan:Win64/InfoStealer" for rookie_cookies, that is the
   part that reads Chrome's saved sign-in. It only reads the Google sign-in cookies it
   needs from your own Chrome on this computer.
   To use the Chrome route, open Windows Security, Virus and threat protection,
   Protection history, select the entry, then Actions and Allow on device. Or use Sign in.

Nothing else is needed: Python and ffmpeg are inside the app.
To stop the app, press Quit on the page. Double-clicking it again opens the same page.
Your data (sign-ins, the list of saved files, the log) stays in %LOCALAPPDATA%\NLH.
