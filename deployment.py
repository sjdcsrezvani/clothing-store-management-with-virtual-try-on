"""Application identity and desktop settings.

There is no build-mode switch. Every packaged copy is a customer install: it
starts empty, runs the first-run setup wizard, and then belongs to the store
that set it up. The store owner is created by that wizard, not by the build.
"""

APP_NAME = "Store Management"
APP_SLUG = "StoreManagement"
APP_TITLE = "مدیریت فروشگاه"
APP_BUNDLE_ID = "com.storemanagement.desktop"

# Fixed desktop server port. This makes the phone address stable across restarts.
DESKTOP_PORT = 8100
