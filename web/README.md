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
- `STRIPE_PRICE_PERSONAL`: a test Price ID for the Personal checkout.
- `STRIPE_PRICE_PRO`: a test Price ID for the Pro checkout.
- `STRIPE_WEBHOOK_SECRET`: the `whsec_...` value printed by Stripe CLI.

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

The dev script passes `--functions web/functions` explicitly. If you run
Wrangler by hand, include that flag or `/api/checkout` and `/api/download/...`
will be served as missing static paths instead of Pages Functions.

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
curl http://127.0.0.1:8788/api/download/mountlet-test.txt
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
Function or Stripe Payment Links:

1. Create products and prices in Stripe.
2. Enable adjustable quantity when the quantity should represent the number of
   supported devices.
3. Set `STRIPE_PRICE_PERSONAL` and `STRIPE_PRICE_PRO` for the Pages Function,
   or create Payment Links for the Personal and Pro plans.
4. If using Payment Links instead of the checkout function, replace the
   placeholder URLs in `config.js`.
5. Set successful-payment redirects in Stripe to a release/download page or a
   private fulfillment flow.

Do not put Stripe secret keys in the static site. Keep them as Cloudflare Pages
secrets or local `.dev.vars` values.

## Downloads

The default download buttons point to the latest GitHub release. For paid
downloads, either:

- use Stripe Payment Links with post-payment fulfillment; or
- replace the platform keys under `downloads` in `config.js` with signed
  download URLs produced by a backend.

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
- `STRIPE_PRICE_PERSONAL`: Stripe test/live Price ID for Personal checkout.
- `STRIPE_PRICE_PRO`: Stripe test/live Price ID for Pro checkout.

Configure Stripe to send `checkout.session.completed` events to:

```text
https://<site>/api/license/stripe-webhook
```

The webhook stores the generated license key in the `payments.license_key`
column for early manual fulfillment. Before broader commercial launch, replace
that with email delivery or a post-payment account/download page and stop
retaining raw license keys.

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
  -d '{"email":"tester@example.com","licenseKind":"beta","plan":"Beta","maxDevices":3}'
```

The response contains the raw key. Store or send it immediately; broad launch
should move key delivery to email or an account page.

If the database already existed before beta support, apply:

```bash
wrangler d1 execute mountlet-license --file web/migrations/0002_license_kind.sql
```
