"""Developer-controlled deployment profile.

This file is intentionally source-controlled and is the single place to change
which kind of desktop build is being produced.

OWNER_MODE is the safe default for the RaiKids shop installation: the app uses
the existing owner database and never exposes the first-run setup wizard.

Before preparing a build for another shop, the developer changes OWNER_MODE to
False and sets the appropriate build-time provisioning policy. A customer
cannot enable provisioning from the application UI.
"""

# True for the owner's private RaiKids installation.
# False only when the developer intentionally prepares a distributable/demo
# build for a new shop.
OWNER_MODE = True

# Developer-controlled identity for the owner build. These are defaults only;
# the real password remains in the local database/environment and is never
# embedded here.
OWNER_STORE_NAME = "رای کیدز"
OWNER_STORE_TAGLINE = "فروشگاه پوشاک کودک"
OWNER_STORE_INSTAGRAM = "@raykids_official"

# In owner mode, require an existing database rather than silently creating a
# new empty shop. This prevents a misplaced executable from provisioning a
# fresh store accidentally.
REQUIRE_EXISTING_OWNER_DATA = True

# Setup is available only in an explicitly prepared sales/demo build.
SETUP_ROUTE_ENABLED = not OWNER_MODE

# Fixed desktop server port. This makes the phone address stable across restarts.
# Keep it in the developer profile so it is easy to change if another program
# already uses the port.
DESKTOP_PORT = 8100

# Optional build label shown only in developer diagnostics/logs.
BUILD_CHANNEL = "owner" if OWNER_MODE else "sales-demo"
