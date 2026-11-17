NotebookLM Harvester for Windows

1. Unzip this folder anywhere you like, for example C:\NLH (a short path is best).
2. Open the "NotebookLM Harvester" folder and double-click "NotebookLM Harvester.exe".
   The first time, Windows SmartScreen may say it protected your PC, because the app is
   not signed. Choose "More info", then "Run anyway".
3. The app opens in its own window (on a PC without Microsoft's WebView2 it opens in your
   browser instead, and says so). Closing the window quits the app. Use Add account:
   - Continue with Google (recommended): a separate window opens in Chrome (or Edge) just
     for this app. It looks like a blank guest window; that is expected. Sign in to Google
     there once and the app remembers it. The app never sees your password, and your
     normal Chrome stays open and is not touched.
   - Or "Use a Chrome profile instead" (shortcut): first quit Chrome completely (close every
     window, then right-click the Chrome icon near the clock and choose Exit). The app's own
     window keeps working while Chrome is closed. Then press Add next to the profile.
4. If Windows Security reports "Trojan:Win64/InfoStealer" for rookie_cookies, that is the
   part that reads Chrome's saved sign-in. It only reads the Google sign-in cookies it
   needs from your own Chrome on this computer.
   To use the Chrome route, open Windows Security, Virus and threat protection,
   Protection history, select the entry, then Actions and Allow on device. Or use
   Continue with Google.

Nothing else is needed: Python and ffmpeg are inside the app.
To stop the app, close its window or press Quit on the page. If a harvest is running, closing
asks first and then finishes the current file (Quit on the page waits until the job ends).
Double-clicking the app again brings its window to the front.
Your data (sign-ins, the list of saved files, the log) stays in %LOCALAPPDATA%\NLH.

Updating: quit the app, then replace the "NotebookLM Harvester" folder with the new one. No copy
of the old version is kept or needed. Your saved files, history and settings are not touched.

New in this version: the player continues where you stopped, a few seconds back, on the laptop and on
a paired phone. To make room for this, the first start of this version keeps one copy of the list of
saved files, named ledger-before-v5.db, in %LOCALAPPDATA%\NLH. It is safe to delete by hand once
you are happy with this version; the app never removes it.
