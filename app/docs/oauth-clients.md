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

## Your setup checklist

Use `Mountlet` as the public application name. Prepare a public Mountlet homepage,
privacy-policy URL, and support email; providers may request these during review.
Create credentials in your developer accounts, then save each pair here:

1. Open https://github.com/1100soft/mountlet/settings/secrets/actions.
2. Click **New repository secret**.
3. Enter the exact name from the table above and paste its value.
4. Click **Add secret**. Repeat for the matching secret.

Do this once as the app publisher. Mountlet users will only sign in to their own
storage accounts. Registering an app does not automatically grant production
approval, increase quota, or eliminate provider throttling.

### Google Drive

1. Open https://console.cloud.google.com/ and choose **Select a project → New
   project**. Name it `Mountlet Drive`, then select the new project.
2. Open **APIs & Services → Library**, search `Google Drive API`, and click
   **Enable**.
3. Open **Google Auth Platform → Get started**. Enter app name `Mountlet`, your
   support email, audience **External**, and your developer contact email.
4. Under **Branding**, supply your homepage and privacy-policy URLs and verify
   your domain when requested.
5. Under **Data Access → Add or remove scopes**, add these full scope URLs:

   ```text
   https://www.googleapis.com/auth/docs
   https://www.googleapis.com/auth/drive
   https://www.googleapis.com/auth/drive.metadata.readonly
   ```

6. Under **Audience**, add your Google account as a test user.
7. Under **Clients → Create client**, choose **Desktop app**, name it `Mountlet
   Desktop`, and click **Create**. No manually entered redirect URI is needed.
8. Save **Client ID** as `MOUNTLET_DRIVE_CLIENT_ID` and **Client secret** as
   `MOUNTLET_DRIVE_CLIENT_SECRET` in GitHub.
9. Test sign-in, then use **Audience → Publish app** and submit verification
   through Google's verification interface. Prepare a video showing Mountlet's
   sign-in and use of the requested scopes. Review may require additional
   evidence/security assessment; publishing alone does not complete verification.

Source checked: https://rclone.org/drive/#making-your-own-client-id.

### Google Photos

Repeat the Google Drive checklist with these replacements:

| Field | Enter |
| --- | --- |
| Project name | `Mountlet Photos` |
| API to enable | `Photos Library API` |
| GitHub ID secret | `MOUNTLET_GPHOTOS_CLIENT_ID` |
| GitHub client-secret name | `MOUNTLET_GPHOTOS_CLIENT_SECRET` |

Replace all Drive scopes with:

```text
https://www.googleapis.com/auth/photoslibrary.appendonly
https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata
https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata
```

This client only provides access to app-created media; it does not restore access
to the user's whole Google Photos library. Source:
https://rclone.org/googlephotos/#making-your-own-client-id.

### Dropbox

1. Open https://www.dropbox.com/developers/apps and click **Create app**.
2. Choose **Scoped access → Full Dropbox**. Name it `Mountlet`; if that global
   name is taken, use `Mountlet by 1100soft`. Click **Create app**.
3. On **Permissions**, enable the following and click **Submit**:

   ```text
   account_info.read
   files.metadata.read
   files.metadata.write
   files.content.read
   files.content.write
   sharing.read
   sharing.write
   ```

4. On **Settings → OAuth 2**, add redirect URI `http://localhost:53682/`.
5. Save **App key** as `MOUNTLET_DROPBOX_CLIENT_ID`. Reveal **App secret** and
   save it as `MOUNTLET_DROPBOX_CLIENT_SECRET`.
6. Enable additional development users for testing. Use **Apply for production**
   when the console permits it; complete the requested app description, branding,
   and review before broad release.

Source checked: https://rclone.org/dropbox/#get-your-own-dropbox-app-id.

### Microsoft OneDrive

These steps match rclone's documented flow, which uses a Web registration and
secret. My earlier recommendation to use a desktop/public registration was not
the documented rclone recipe.

1. Open https://portal.azure.com/ and go to **Microsoft Entra ID → App
   registrations → New registration**.
2. Name: `Mountlet`. Supported accounts: **Accounts in any organizational
   directory and personal Microsoft accounts**.
3. Redirect URI platform: **Web**. URI: `http://localhost:53682/`.
   Click **Register**.
4. On **Overview**, save **Application (client) ID** as
   `MOUNTLET_ONEDRIVE_CLIENT_ID`.
5. Open **Certificates & secrets → New client secret**. Description:
   `Mountlet desktop release`. Choose the permitted expiration, click **Add**,
   and immediately save **Value**, not Secret ID, as
   `MOUNTLET_ONEDRIVE_CLIENT_SECRET`. Set a renewal reminder before expiration.
6. Open **API permissions → Add a permission → Microsoft Graph → Delegated
   permissions**. Add `Files.Read`, `Files.ReadWrite`, `Files.Read.All`,
   `Files.ReadWrite.All`, `offline_access`, `User.Read`, and `Sites.Read.All`.
7. Save permissions. Complete publisher verification for organizational users;
   some organizations additionally require their administrator's consent.

Source checked: https://rclone.org/onedrive/#getting-your-own-client-id-and-key.

### Box

1. Open https://app.box.com/developers/console and click **Create New App →
   Custom App**.
2. Name it `Mountlet`, choose purpose **Automation**, and click **Next**.
3. Choose **User Authentication (OAuth 2.0)** and click **Create App**.
4. On **Configuration**, copy **Client ID** to `MOUNTLET_BOX_CLIENT_ID` and
   **Client Secret** to `MOUNTLET_BOX_CLIENT_SECRET` in GitHub.
5. Under **OAuth 2.0 Redirect URI**, add `http://127.0.0.1:53682/`.
6. Under **Application Scopes**, enable **Read all files and folders stored in
   Box** and **Write all files and folders stored in Box**. Save changes.
7. Test with your Box account. For enterprise accounts, users may need their
   Box administrator to approve the app. This user OAuth registration does not
   require a JWT configuration file or service-account credentials.

Source checked: https://rclone.org/box/#get-your-own-box-app-id.

### pCloud

1. Open https://docs.pcloud.com/my_apps/ and sign in with your pCloud account.
2. If **New app** is available, create `Mountlet`, describing it as a desktop
   cloud-storage browser, mount manager, and sync client using rclone.
3. Request/register callback `http://127.0.0.1:53682/` and file read/write
   access. Copy the client ID to `MOUNTLET_PCLOUD_CLIENT_ID` and the client
   secret to `MOUNTLET_PCLOUD_CLIENT_SECRET`.
4. If creation fails or those options are unavailable, contact pCloud support
   through your account and send:

   > Please register an OAuth application for Mountlet, a publicly distributed
   > Windows/macOS/Linux desktop app using rclone to browse, mount, upload,
   > download, and sync each signed-in user's files. We need a client ID and
   > client secret, file read/write access, and the loopback redirect
   > http://127.0.0.1:53682/. Please confirm support for both US and EU accounts
   > and whether embedding these application credentials in desktop releases is
   > permitted. Our homepage is [URL], privacy policy [URL], contact [email].

The signed-in app-creation form could not be verified publicly; treat step 3 as
the requested configuration and obtain pCloud's confirmation before shipping.

After adding or rotating any secret, rebuild every platform installer. Existing
rclone remotes retain the client credentials written when they were created;
changing a release binary does not migrate them or refresh their OAuth tokens.
