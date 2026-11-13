NotebookLM Harvester for Mac

1. Drag "NotebookLM Harvester.app" into your Applications folder (or anywhere you like).
2. The first time, right-click the app and choose Open, then choose Open again.
   macOS asks this because the app is not signed by Apple. If macOS still refuses, open
   System Settings, Privacy and Security, and press "Open Anyway" next to the app's name.
3. The app opens a page in your browser. Use Add account to pick a Chrome profile that is
   signed in to the Google account with your notebooks.
4. When the app first reads Chrome's sign-in, macOS asks to let it use "Chrome Safe
   Storage" from your keychain. Enter your Mac password and choose Always Allow.

Nothing else is needed: Python and ffmpeg are inside the app.
To stop the app, press Quit on the page. Double-clicking it again opens the same page.
Your data (sign-ins, the list of saved files, the log) stays in ~/.nlh on this Mac.
