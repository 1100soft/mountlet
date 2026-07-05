# Mountlet Website

Static commercial download site for Cloudflare Pages.

## Cloudflare Pages

Use these settings:

- Build command: none
- Build output directory: `web`
- Root directory: repository root

The site is plain HTML, CSS, and JavaScript, so no package install step is
required.

## Stripe

For the first commercial version, use Stripe Checkout or Payment Links:

1. Create products and prices in Stripe.
2. Enable adjustable quantity when the quantity should represent the number of
   supported devices.
3. Create Payment Links for the Personal and Pro plans.
4. Replace the placeholder URLs in `config.js`.
5. Set successful-payment redirects in Stripe to a release/download page or a
   private fulfillment flow.

Do not put Stripe secret keys in this static site. If later fulfillment needs
license keys, account management, or signed download URLs, add a Cloudflare
Pages Function or Worker that talks to Stripe server-side.

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

Configure Stripe to send `checkout.session.completed` events to:

```text
https://<site>/api/license/stripe-webhook
```

The webhook stores the generated license key in the `payments.license_key`
column for early manual fulfillment. Before broader commercial launch, replace
that with email delivery or a post-payment account/download page and stop
retaining raw license keys.

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
