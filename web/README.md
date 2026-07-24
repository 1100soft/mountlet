# Mountlet Website

Static commercial download site for Cloudflare Pages.

## Cloudflare Pages

Use these settings:

- Build command: none
- Build output directory: `web`
- Root directory: repository root
- Functions directory: `functions`

Production app builds default to the generated license API below and derive
purchase links from the same site. Keep deployments relocatable with the listed
environment overrides.

<!-- mountlet-vars:start -->
- Production website: https://mountlet.app
- Production license API: https://mountlet.app/api/license
- Production report API: https://mountlet.app/api/report
- Relocated app API override: `MOUNTLET_LICENSE_API_URL`
- Relocated purchase-site override: `MOUNTLET_LICENSE_SITE_URL`
- Relocated report API override: `MOUNTLET_REPORT_API_URL`
- Resend API key: `RESEND_API_KEY`
- Resend sender: `RESEND_FROM`
- Optional Resend reply-to: `RESEND_REPLY_TO`
- Resend sender alias: `EMAIL_FROM`
- Optional Resend reply-to alias: `EMAIL_REPLY_TO`
- Optional report sender: `REPORT_FROM`
- Optional report recipient: `REPORT_TO`
- GitHub report token: `REPORT_GITHUB_TOKEN`
- GitHub report repository: `REPORT_GITHUB_REPO`
- Optional GitHub report labels: `REPORT_GITHUB_LABELS`
- Stripe secret key: `STRIPE_SECRET_KEY`
- Stripe webhook secret: `STRIPE_WEBHOOK_SECRET`
<!-- mountlet-vars:end -->

The site is plain HTML, CSS, and JavaScript, so no package install step is
required.

## Generated Variables

Shared public URLs and environment-variable names live in
`functions/_lib/public-vars.js`.
After changing them, run:

```bash
npm run docs:vars
```

The script rewrites only the marked generated blocks in the root, app, and web
READMEs.

## Local End-to-End Testing

Use Wrangler locally to test the website, license API, D1, R2, and Stripe
test-mode checkout before deploying.

Install the local tooling once:

```bash
npm install
```

Create local license signing keys and a root `.dev.vars` file:

```bash
npm run web:env
```

That command prints the public-key path and admin token. Keep both for the app
activation test below.

Edit `.dev.vars` and replace the Stripe placeholders with test-mode values:

- `STRIPE_SECRET_KEY`: a Stripe `sk_test_...` key.
- `STRIPE_WEBHOOK_SECRET`: the `whsec_...` value printed by Stripe CLI.

Initialize local D1 and optionally seed a local R2 object:

```bash
npm run web:d1:init
npm run web:r2:seed
```

If you initialized D1 before the no-customer-records schema change, recreate
the local D1 state or apply:

```bash
wrangler d1 execute mountlet-license --local --file web/migrations/0003_remove_customer_columns.sql
```

If you applied the earlier version of that migration that removed
`payments.stripe_customer_id`, add it back for refund lookup:

```bash
wrangler d1 execute mountlet-license --local --file web/migrations/0004_add_stripe_customer_id.sql
```

If you initialized D1 before subscription support, add the subscription fields:

```bash
wrangler d1 execute mountlet-license --local --file web/migrations/0005_subscription_fields.sql
```

`web:r2:seed` uploads local test release files to local R2. The download
buttons read `web/release-files.json` and route through `/api/download/...`, so
you can test the same release path before publishing real installer artifacts.

To upload real installer artifacts from a GitHub Actions artifact directory to
Cloudflare R2, run with `--remote`:

```bash
npm run web:r2:upload -- <bucket-name> <artifact-directory> --remote
```

For example, after `gh run download ... -D /tmp/mountlet-artifacts`, upload
preview installers to the bucket bound as preview `DOWNLOADS`:

```bash
npm run web:r2:upload -- mountlet-preview /tmp/mountlet-artifacts --remote
```

The upload tool reads the expected public file names from
`web/release-files.json` and finds those files recursively in the artifact
directory. It still accepts an explicit manifest file for one-off overrides.
Omit `--remote` only when intentionally seeding Wrangler's local R2 simulator.
Use `--dry-run` to verify artifact discovery without uploading:

```bash
npm run web:r2:upload -- mountlet-preview /tmp/mountlet-artifacts --dry-run
```

Run the local Pages site:

```bash
npm run web:dev
```

Wrangler detects Pages Functions from the repository-root `functions/`
directory. If `/api/checkout` returns an empty or HTML response, stop the dev
server and restart it from the repository root with `npm run web:dev`.

In another terminal, forward Stripe test webhooks to Wrangler:

```bash
stripe listen --forward-to http://127.0.0.1:8788/webhook
```

Copy the printed `whsec_...` value into `.dev.vars`, then restart
`npm run web:dev` so Wrangler reloads it.

Open `http://127.0.0.1:8788/#pricing`, buy a new license or validate an
existing key, then complete checkout with Stripe's test card:

```text
4242 4242 4242 4242
```

Retrieve the generated license key:

```bash
curl -H "Authorization: Bearer <LICENSE_ADMIN_TOKEN>" \
  http://127.0.0.1:8788/api/license/admin/payments
```

You can also verify that local R2 is bound:

```bash
curl "http://127.0.0.1:8788/api/download/$(node -p 'require("./web/release-files.json").downloads.linux')"
```

To activate a local paid build against the local API:

```bash
export MOUNTLET_REQUIRE_LICENSE=1
export MOUNTLET_LICENSE_API_URL=http://127.0.0.1:8788/api/license
export MOUNTLET_LICENSE_PUBLIC_KEY_FILE=/absolute/path/to/web/.local/license-public.pem
mountlet
```

Mountlet does not connect to D1 directly. It calls the license API URL above;
Wrangler Pages Functions then read and write the local D1 database. If Mountlet
reports that it cannot resolve the license server, it is still using the
default production API URL or another unreachable URL in the environment that
launched the app.

For a fast license API smoke test without Stripe, keep `npm run web:dev`
running and execute:

```bash
npm run web:license:smoke
```

## Stripe

For the first commercial version, use Stripe Checkout through the Pages
Function:

1. Configure the Stripe secret key and webhook signing secret.
2. Let the checkout function create dynamic Checkout prices for monthly,
   annual, and lifetime purchases.
3. Configure the Stripe webhook endpoint for the Pages Function.
4. Set successful-payment redirects through the checkout function. The default
   success URL returns to the license page and displays the generated license
   key.

Do not put Stripe secret keys in the static site. Keep them as Cloudflare Pages
secrets or local `.dev.vars` values.

## Downloads

The default download buttons point to `/api/download/...`, which reads objects
from the `DOWNLOADS` R2 binding. The public object keys live in
`web/release-files.json`.

The native package workflow uploads installers automatically after successful
builds when these GitHub settings are present:

- Secret: `CLOUDFLARE_R2_ACCESS_KEY_ID`
- Secret: `CLOUDFLARE_R2_SECRET_ACCESS_KEY`
- Variable: `CLOUDFLARE_ACCOUNT_ID`
- Variable: `MOUNTLET_PREVIEW_R2_BUCKET`
- Variable: `MOUNTLET_PRODUCTION_R2_BUCKET`

Use R2 S3 credentials generated from an R2 token with bucket-item read/write
access. Wrangler's Cloudflare REST upload path requires broader account-level
R2 permissions, so Mountlet uploads release artifacts through the
S3-compatible API instead.

The `wip` branch uploads to the preview bucket. `main` and version tags upload
to the production bucket.

Preview app builds also embed the preview license and report API defaults:
`https://wip.mountlet.pages.dev/api/license` and
`https://wip.mountlet.pages.dev/api/report`. Production builds keep the
relocatable production defaults under `https://mountlet.app`.

These keys are the public download API names. Keep them in
`web/release-files.json` so the website, deployment check, and R2 upload tool
use the same list.

Before uploading, verify that the site release list matches the package
workflow outputs:

```bash
npm run web:release:check
```

## License API

The `functions/api/license/*` Pages Functions implement the first licensing
control:

- 7-day app-side trial.
- activation and renewal checks with a license key.
- signed offline license token returned to the app.
- flexible `max_devices` per license.
- in-app active-device listing and deactivation.

Paid installers should set `MOUNTLET_REQUIRE_LICENSE=1` and provide the
matching public signing key to the app through `MOUNTLET_LICENSE_PUBLIC_KEY` or
`MOUNTLET_LICENSE_PUBLIC_KEY_FILE`.

Create a D1 database and bind it to Pages as `DB`. After deployment, initialize
or upgrade the bound database through the admin endpoint:

```bash
curl -X POST https://<site>/api/license/admin/init \
  -H "Authorization: Bearer $LICENSE_ADMIN_TOKEN"
```

Then set these environment variables:

- `LICENSE_KEY_PEPPER`: private salt for hashing license keys in D1.
- `LICENSE_SIGNING_PRIVATE_KEY`: ECDSA P-256 private key in PKCS8 PEM format.
- `LICENSE_SIGNING_PUBLIC_KEY`: matching public key in SPKI PEM format.
- `LICENSE_ADMIN_TOKEN`: bearer token for admin-only license creation.
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signing secret.
- `STRIPE_SECRET_KEY`: Stripe secret key used to create Checkout sessions and
  read subscription state.
- `RESEND_API_KEY`: optional Resend API key for sending license-key emails.
- `EMAIL_FROM` or `RESEND_FROM`: optional verified sender, for example
  `Mountlet <licenses@example.com>`.
- `EMAIL_REPLY_TO` or `RESEND_REPLY_TO`: optional reply-to address for license
  emails.
- `MOUNTLET_NOTICES_JSON`: optional emergency fallback notices served from `/api/notices`.
- `REPORT_TO`: optional recipient for app reports and website support requests.
  If unset, reports fall back to the reply-to or sender address.
- `REPORT_FROM`: optional verified sender for reports and support requests. If
  unset, messages fall back to the license email sender.
- `REPORT_GITHUB_TOKEN`: optional fine-grained GitHub token for creating private
  app report and support-request issues.
- `REPORT_GITHUB_REPO`: optional GitHub repository target in `owner/repo`
  format.
- `REPORT_GITHUB_LABELS`: optional comma-separated issue labels. If set, create
  those labels in GitHub first. Mountlet adds `bug`, `crash`, or `support` as
  appropriate and retries without labels if GitHub rejects the labels.

The report endpoint creates GitHub issues directly. Email through Resend is only
an optional secondary notification path. For GitHub reporting, create a private
support repository, create a fine-grained personal access token limited to that
repository with Issues read/write permission, then set `REPORT_GITHUB_TOKEN` as
a Pages secret and `REPORT_GITHUB_REPO` as an environment variable. The Function
redacts obvious tokens and secrets, but app reports can still include paths and
filenames because users review them before sending. Website support requests do
not include diagnostic logs.

The website form includes a honeypot and field limits. For public deployment,
also apply a Cloudflare rate-limit rule to the exact `/api/report` path to
contain spam without adding report storage or customer accounts. This endpoint
is submission-only; Mountlet never retrieves reports through it, so a rule that
counts every HTTP method on that path is safe.

If in-app reports return Cloudflare error 1010 or another 403 before reaching
the Function, add a Cloudflare security/WAF skip or allow rule for `/api/report`
or for the `Mountlet/...` user agent. The report Function still validates JSON
and sends only through configured report sinks.

## App notices

Notices are stored in the bound D1 database and become visible as soon as they
are published. The same `LICENSE_ADMIN_TOKEN` protects management actions.
Initialize the current environment once after deploying this schema:

```bash
curl -X POST https://<site>/api/license/admin/init \
  -H "Authorization: Bearer $LICENSE_ADMIN_TOKEN"
```

Manage notices without editing Cloudflare variables or redeploying:

```bash
LICENSE_ADMIN_TOKEN=... npm run web:notices -- list --site https://<site>
LICENSE_ADMIN_TOKEN=... npm run web:notices -- create --site https://<site> \
  --id maintenance-2026-08 --title "Maintenance" \
  --message "Cloud sync may be briefly unavailable." --level important \
  --audience production --publish
LICENSE_ADMIN_TOKEN=... npm run web:notices -- update maintenance-2026-08 \
  --site https://<site> --message "Maintenance is complete."
LICENSE_ADMIN_TOKEN=... npm run web:notices -- archive maintenance-2026-08 --site https://<site>
```

Editing increments the notice version, so clients see the revision as unread.
Published notices must be archived before deletion, and critical/price notices
cannot be hard-deleted. `MOUNTLET_NOTICES_JSON` remains a read-only emergency
fallback.

Notice audiences are `production`, `preview`, `local`, and `all`. New notices
default to the environment whose admin endpoint receives the request. Use
`--audience all` only for messages intended for every build channel. Legacy
rows created before audience support are treated as preview notices so test
content cannot leak into production. The app stores notification history
separately for each channel.

Production and preview deployments can use different bindings and secrets.
Use production D1/R2 plus live Stripe keys for the production environment, and
preview D1/R2 plus Stripe test keys for the preview environment. This project
keeps `wrangler.toml` for local development only; configure the production and
preview split in the Cloudflare Pages dashboard.

Recommended Cloudflare Pages environment split:

- Production branch: `main`
- Preview branch: `wip`
- Production `DB`: production license D1
- Preview `DB`: test license D1
- Production `DOWNLOADS`: `mountlet-production`
- Preview `DOWNLOADS`: `mountlet-preview`
- Production `STRIPE_SECRET_KEY`: Stripe live key, `sk_live_...`
- Preview `STRIPE_SECRET_KEY`: Stripe test key, `sk_test_...`
- Production webhook secret: live Stripe webhook endpoint secret
- Preview webhook secret: test Stripe webhook endpoint secret

After each deployment, verify that Pages Functions and bindings are active:

```bash
npm run web:deploy:check -- https://mountlet.app
npm run web:deploy:check -- https://<preview-url>
```

If a purchase attempt returns `405 Method Not Allowed`, first check
`/api/health`. If that route is missing or returns HTML, the deployment is not
serving Pages Functions. Confirm that the Cloudflare Pages root directory is
the repository root, not `web`, and that the deployed branch contains the
repository-root `functions/` directory.

Configure Stripe to send `checkout.session.completed`, `invoice.paid`,
`customer.subscription.updated`, and `customer.subscription.deleted` events to
either endpoint:

```text
https://<site>/api/license/stripe-webhook
https://<site>/webhook
```

The webhook stores the generated license key in `payments.license_key` so the
success page can display it after Stripe redirects back. It stores Stripe
customer IDs for transaction lookup and refund handling. Tell buyers to save
their license key because Mountlet does not recover lost keys.

If Resend is configured with `RESEND_API_KEY` and either `EMAIL_FROM` or
`RESEND_FROM`, the webhook also sends the license key to the checkout email
reported by Stripe. The license result page includes an "Email key" button that
can resend the same message from the checkout session without storing the email
address in D1.

For local and early admin use, retrieve recent fulfilled keys with:

```bash
curl -H "Authorization: Bearer $LICENSE_ADMIN_TOKEN" \
  https://<site>/api/license/admin/payments
```

## Beta Keys

Beta keys use the same activation and device-count system as paid keys, but
they are distinguished in three places:

- `licenses.license_kind = "beta"` in D1.
- `licenseKind: "beta"` in the signed app token.
- `MTB-...` key prefix for human recognition.

Create a beta key through the admin endpoint:

```bash
curl -X POST https://<site>/api/license/admin/create \
  -H "Authorization: Bearer $LICENSE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"licenseKind":"beta","plan":"Beta","maxDevices":3}'
```

The response contains the raw key. Store or send it immediately.

Notices are a JSON array. Critical notices, including price notices, are always
shown in the app:

```json
[
  {
    "id": "price-2026-09",
    "version": "1",
    "type": "price",
    "level": "critical",
    "title": "Subscription price change",
    "message": "Subscription prices will change on September 1. You can cancel before renewal.",
    "url": "https://mountlet.app/#pricing"
  }
]
```
