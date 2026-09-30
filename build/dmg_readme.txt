Installing Framecheck
=====================

Drag Framecheck.app onto the Applications folder next to it, then eject
this disk image and open Framecheck from Applications or Launchpad.

If macOS says it "cannot verify" Framecheck or that it "is damaged"
-------------------------------------------------------------------

This copy of Framecheck is not signed with an Apple Developer ID, so
macOS treats it as an app from an unidentified developer the first time
it is opened. It is not damaged. To open it once, after which macOS
remembers your choice:

  macOS 15 (Sequoia) and later
    1. Double-click Framecheck in Applications. Click "Done" on the
       message that appears.
    2. Open System Settings > Privacy & Security, scroll down to
       Security, and click "Open Anyway" next to Framecheck.
    3. Confirm with your password or Touch ID.

  macOS 13 and 14
    1. Right-click (or Control-click) Framecheck in Applications and
       choose "Open".
    2. Click "Open" in the dialog.

If it says the app "is damaged and can't be opened", the download was
quarantined by the browser. Open Terminal and run:

    xattr -dr com.apple.quarantine /Applications/Framecheck.app

then open it as above.

Everything else
---------------

Framecheck runs entirely on this Mac. Nothing is uploaded anywhere. Your
source files are never modified; every export is a new file.

Custom delivery specs go in Help > Open Custom Specs Folder
(~/Library/Application Support/Framecheck/specs). Updates never touch it.

Licence: GPL-3.0-or-later. Framecheck bundles FFmpeg and libmpv (GPL) and
Qt/PySide6 (LGPL-3.0). See THIRD_PARTY_NOTICES.md inside the app
(right-click Framecheck.app > Show Package Contents > Contents/Resources).
