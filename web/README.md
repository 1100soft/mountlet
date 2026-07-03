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

For the first commercial version, use Stripe Payment Links:

1. Create products and prices in Stripe.
2. Create Payment Links for the Personal and Pro plans.
3. Replace the placeholder URLs in `config.js`.
4. Set successful-payment redirects in Stripe to a release/download page or a
   private fulfillment flow.

Do not put Stripe secret keys in this static site. If later fulfillment needs
license keys, account management, or signed download URLs, add a Cloudflare
Pages Function or Worker that talks to Stripe server-side.

## Downloads

The default download buttons point to the latest GitHub release. For paid
downloads, either:

- use Stripe Payment Links with post-payment fulfillment; or
- replace `downloads.system` and `downloads.bundled` in `config.js` with signed
  download URLs produced by a backend.
