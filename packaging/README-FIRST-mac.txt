NotebookLM Harvester for Mac

1. Drag "NotebookLM Harvester.app" into your Applications folder (or anywhere you like).
2. The first time, right-click the app and choose Open, then choose Open again.
   macOS asks this because the app is not signed by Apple. If macOS still refuses, open
   System Settings, Privacy and Security, and press "Open Anyway" next to the app's name.
3. The app opens a page in your browser. Press Add account, then Continue with Google:
   a separate Google window opens in Chrome (or Microsoft Edge if Chrome is missing).
   Sign in there once and the app remembers it. The app never sees your password.
   Adding an account needs Chrome or Edge installed (both free); Safari alone is not enough.
4. Shortcut: "Use a Chrome profile instead" lists the Chrome profiles signed in on this Mac.
   The first time the app reads Chrome's sign-in, macOS asks to let it use "Chrome Safe
   Storage" from your keychain. Enter your Mac password and choose Always Allow.

Nothing else is needed: Python and ffmpeg are inside the app.
To stop the app, press Quit on the page. Double-clicking it again opens the same page.
Your data (sign-ins, the list of saved files, the log) stays in ~/.nlh on this Mac.
