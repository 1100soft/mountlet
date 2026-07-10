# Mountlet Website

Static commercial download site for Cloudflare Pages.

## Cloudflare Pages

Use these settings:

- Build command: none
- Build output directory: `web`
- Root directory: repository root

The site is plain HTML, CSS, and JavaScript, so no package install step is
required.

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
- `STRIPE_PRICE_LICENSE`: a test Price ID for the one-device Mountlet License
  checkout.
- `STRIPE_PRICE_DEVICE`: a test Price ID for adding one extra device slot.
- `STRIPE_WEBHOOK_SECRET`: the `whsec_...` value printed by Stripe CLI.
- Optional email delivery:
  - `RESEND_API_KEY`: Resend API key.
  - `LICENSE_EMAIL_FROM`: verified sender, for example
    `Mountlet <licenses@example.com>`.

Initialize local D1 and optionally seed a local R2 object:

```bash
npm run web:d1:init
npm run web:r2:seed
```

`web:r2:seed` uploads placeholder release files to local R2. The download
buttons in `config.js` point to those objects through `/api/download/...`, so
you can test the same release path before replacing the placeholders with real
installer artifacts.

Run the local Pages site:

```bash
npm run web:dev
```

Wrangler detects Pages Functions from the repository-root `functions/`
directory. If `/api/checkout` returns an empty or HTML response, stop the dev
server and restart it from the repository root with `npm run web:dev`.

In another terminal, forward Stripe test webhooks to Wrangler:

```bash
stripe listen --forward-to http://127.0.0.1:8788/api/license/stripe-webhook
```

Copy the printed `whsec_...` value into `.dev.vars`, then restart
`npm run web:dev` so Wrangler reloads it.

Open `http://127.0.0.1:8788/#pricing`, choose a device count, and complete
checkout with Stripe's test card:

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
curl http://127.0.0.1:8788/api/download/mountlet-linux-bundled.txt
```

To activate a local paid build against the local API:

```bash
export MOUNTLET_REQUIRE_LICENSE=1
export MOUNTLET_LICENSE_API_URL=http://127.0.0.1:8788/api/license
export MOUNTLET_LICENSE_PUBLIC_KEY_FILE=/absolute/path/to/web/.local/license-public.pem
mountlet
```

For a fast license API smoke test without Stripe, keep `npm run web:dev`
running and execute:

```bash
npm run web:license:smoke
```

## Stripe

For the first commercial version, use Stripe Checkout through the Pages
Function:

1. Create one product: Mountlet License.
2. Create one one-time Price for the initial license: `$20`.
3. Create one one-time Price for one extra device slot: `$5`.
4. Set `STRIPE_PRICE_LICENSE` and `STRIPE_PRICE_DEVICE` for the Pages Function.
5. Set successful-payment redirects through the checkout function. The default
   success URL returns to the pricing tab and displays the generated license
   key.

Do not put Stripe secret keys in the static site. Keep them as Cloudflare Pages
secrets or local `.dev.vars` values.

## Downloads

The default download buttons point to `/api/download/...`, which reads objects
from the `DOWNLOADS` R2 binding. For local testing, `npm run web:r2:seed`
uploads placeholder objects. For production, upload the real installer
artifacts to the bound R2 bucket using the same keys or update `config.js`.

## License API

The `functions/api/license/*` Pages Functions implement the first licensing
control:

- 7-day app-side trial.
- one-time activation with a license key.
- signed offline license token returned to the app.
- flexible `max_devices` per license.
- in-app active-device listing and deactivation.

Paid installers should set `MOUNTLET_REQUIRE_LICENSE=1` and provide the
matching public signing key to the app through `MOUNTLET_LICENSE_PUBLIC_KEY` or
`MOUNTLET_LICENSE_PUBLIC_KEY_FILE`.

Create a D1 database and apply `schema.sql`:

```bash
wrangler d1 execute mountlet-license --file web/schema.sql
```

Bind the D1 database to Pages as `DB`, then set these environment variables:

- `LICENSE_KEY_PEPPER`: private salt for hashing license keys in D1.
- `LICENSE_SIGNING_PRIVATE_KEY`: ECDSA P-256 private key in PKCS8 PEM format.
- `LICENSE_SIGNING_PUBLIC_KEY`: matching public key in SPKI PEM format.
- `LICENSE_ADMIN_TOKEN`: bearer token for admin-only license creation.
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signing secret.
- `STRIPE_SECRET_KEY`: optional; used to read Checkout line-item quantities.
- `STRIPE_PRICE_LICENSE`: Stripe test/live Price ID for the initial `$20`
  license checkout.
- `STRIPE_PRICE_DEVICE`: Stripe test/live Price ID for the `$5` extra-device
  checkout.
- `RESEND_API_KEY`: optional; used to email license keys after purchase.
- `LICENSE_EMAIL_FROM`: optional; verified sender used with Resend.

Configure Stripe to send `checkout.session.completed` events to:

```text
https://<site>/api/license/stripe-webhook
```

The webhook stores the generated license key in `payments.license_key` so the
success page can display it after Stripe redirects back. It does not store
Stripe customer IDs or customer email addresses. Tell buyers to save their
license key because Mountlet intentionally does not keep customer records for
key recovery.

If `RESEND_API_KEY` and `LICENSE_EMAIL_FROM` are set, the webhook also sends
the license key to the Stripe checkout email without storing that email in D1.
If email delivery fails, the payment still succeeds and the browser success
page remains the primary key delivery path.

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

If the database already existed before beta support, apply:

```bash
wrangler d1 execute mountlet-license --file web/migrations/0002_license_kind.sql
```
