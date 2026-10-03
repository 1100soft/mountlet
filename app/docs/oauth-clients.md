# Mountlet OAuth clients

Release builds can include Mountlet-owned OAuth application credentials for the
six rclone backends that support them. Set these as GitHub Actions repository or
environment secrets; never commit their values:

| Provider | Client ID secret | Client secret secret |
| --- | --- | --- |
| Google Drive | `MOUNTLET_DRIVE_CLIENT_ID` | `MOUNTLET_DRIVE_CLIENT_SECRET` |
| Google Photos | `MOUNTLET_GPHOTOS_CLIENT_ID` | `MOUNTLET_GPHOTOS_CLIENT_SECRET` |
| Dropbox | `MOUNTLET_DROPBOX_CLIENT_ID` | `MOUNTLET_DROPBOX_CLIENT_SECRET` |
| Microsoft OneDrive | `MOUNTLET_ONEDRIVE_CLIENT_ID` | `MOUNTLET_ONEDRIVE_CLIENT_SECRET` |
| Box | `MOUNTLET_BOX_CLIENT_ID` | `MOUNTLET_BOX_CLIENT_SECRET` |
| pCloud | `MOUNTLET_PCLOUD_CLIENT_ID` | `MOUNTLET_PCLOUD_CLIENT_SECRET` |

The values are compiled into the desktop executable. They prevent dependence on
rclone's shared clients, but they are public application credentials rather than
confidential server credentials: anyone can extract them from a distributed
binary. Restrict the provider registrations by redirect URI, scopes, application
review, and provider-side quota controls. Never use credentials that grant
application-only or service-account access to user data.

Mountlet uses explicitly entered credentials first, then credentials reused from a
Google remote for Drive, then these build-time values. Client IDs and secrets are
selected together from one source; a custom ID without a secret never receives
the embedded client’s secret. A build-time secret without a client ID is ignored.
If no build-time client ID exists, the default falls back to rclone’s client where
rclone still provides one. The dialog labels this choice “Default client
(Mountlet or rclone)” because build-time credentials are optional.

All browser-based providers must permit rclone's loopback callback. Depending on
the provider, register `http://localhost:53682/` or
`http://127.0.0.1:53682/` exactly as its rclone documentation specifies.

The other supported backends do not have an app-wide OAuth client to register:
iCloud Drive, MEGA, Proton Drive, Koofr, Nextcloud/WebDAV, and S3-compatible
storage authenticate with user credentials, app passwords, access keys, or the
user's chosen endpoint.

## Provider registration checklist

### Google Drive

1. Create a project in Google Cloud Console and configure its OAuth consent
   screen for an external production app (or internal app for one Workspace).
2. Enable Google Drive API and add the Drive scopes documented by rclone.
3. Create an OAuth client of type **Desktop app**.
4. Put its ID and secret in `MOUNTLET_DRIVE_CLIENT_ID` and
   `MOUNTLET_DRIVE_CLIENT_SECRET`.
5. Submit the consent screen for verification before distributing Mountlet to a
   general audience. Testing mode has user and token-lifetime restrictions.

### Google Photos

Use a separate Google Cloud project/client so its consent, quota, and review can
be managed independently. Enable Photos Library API, add rclone's
`photoslibrary.appendonly`, `photoslibrary.readonly.appcreateddata`, and
`photoslibrary.edit.appcreateddata` scopes, create a **Desktop app** OAuth
client, and store it in the two `MOUNTLET_GPHOTOS_*` secrets. Google Photos now
limits rclone to media created by the same app/client.

### Dropbox

1. In Dropbox App Console, create a scoped Dropbox API app. Choose **Full
   Dropbox** if Mountlet must browse all files; **App folder** confines it.
2. Enable `account_info.read`, `files.metadata.read`, `files.metadata.write`,
   `files.content.read`, `files.content.write`, `sharing.read`, and
   `sharing.write`.
3. Register `http://localhost:53682/` as an OAuth redirect URI.
4. Copy **App key** to `MOUNTLET_DROPBOX_CLIENT_ID` and **App secret** to
   `MOUNTLET_DROPBOX_CLIENT_SECRET`.
5. Apply for production before allowing users beyond the app owner/development
   team.

### Microsoft OneDrive

1. In Microsoft Entra admin center, create an app registration supporting
   organizational directories and personal Microsoft accounts.
2. Add a **Mobile and desktop applications** platform with the localhost/native
   redirect specified by the current rclone OneDrive guide, and enable public
   client flow where that guide requires it.
3. Add Microsoft Graph delegated permissions used by rclone, including file
   read/write and offline access; do not add application permissions.
4. Copy **Application (client) ID** to `MOUNTLET_ONEDRIVE_CLIENT_ID`. If the
   rclone registration flow requires a client secret, create one and copy its
   value immediately to `MOUNTLET_ONEDRIVE_CLIENT_SECRET`; otherwise leave the
   secret unset for a public native client.
5. Complete publisher verification/admin-consent work needed for organizational
   tenants before release.

### Box

1. In Box Developer Console, create a **Custom App** using **User
   Authentication (OAuth 2.0)**.
2. Register `http://127.0.0.1:53682/` as its OAuth redirect URI.
3. Enable read and write access to files and folders, then save the app.
4. Copy its Client ID and Client Secret to the two `MOUNTLET_BOX_*` secrets.
5. Submit/authorize the app as required by Box before distributing it outside
   the developer account or enterprise.

### pCloud

Create an application under **My Apps** in the pCloud developer portal and copy
its client ID and secret to the two `MOUNTLET_PCLOUD_*` secrets. Register the
loopback callback requested by pCloud/rclone. If self-service app creation is
unavailable, request creation and production approval from pCloud support. Test
both US (`api.pcloud.com`) and EU (`eapi.pcloud.com`) accounts before release.

After adding or rotating any secret, rebuild every platform installer. Existing
rclone remotes retain the client credentials written when they were created;
changing a release binary does not migrate them or refresh their OAuth tokens.
