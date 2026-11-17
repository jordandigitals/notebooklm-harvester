NotebookLM Harvester for Mac

1. Drag "NotebookLM Harvester.app" into your Applications folder (or anywhere you like).
2. The first time, right-click the app and choose Open, then choose Open again.
   macOS asks this because the app is not signed by Apple. If macOS still refuses, open
   System Settings, Privacy and Security, and press "Open Anyway" next to the app's name.
3. The app opens in its own window. (If that window cannot open on this Mac, the app opens in your
   browser instead and says so. Settings, "Open the app in", chooses between the two.)
   Press Add account, then Continue with Google:
   a separate Google window opens in Chrome (or Microsoft Edge if Chrome is missing).
   Sign in there once and the app remembers it. The app never sees your password.
   Adding an account needs Chrome or Edge installed (both free); Safari alone is not enough.
4. Shortcut: "Use a Chrome profile instead" lists the Chrome profiles signed in on this Mac.
   The first time the app reads Chrome's sign-in, macOS asks to let it use "Chrome Safe
   Storage" from your keychain. Enter your Mac password and choose Always Allow.

Nothing else is needed: Python and ffmpeg are inside the app.
To stop the app, close its window (or press Command-Q). If a harvest is running, it asks first and
then finishes the current file. Opening the app again brings its window to the front.
Your data (sign-ins, the list of saved files, the log) stays in ~/.nlh on this Mac.

Updating: drag the new "NotebookLM Harvester.app" over the old one and choose Replace. No copy of
the old version is kept or needed. Your saved files, history and settings in ~/.nlh are not touched.

New in this version: the player continues where you stopped, a few seconds back, on the laptop and on
a paired phone. To make room for this, the first start of this version keeps one copy of the list of
saved files, named ledger-before-v5.db, in ~/.nlh. It is safe to delete by hand once you are happy
with this version; the app never removes it.
