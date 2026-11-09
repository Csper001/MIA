# Mia Home AI — Stripe sandbox integration

Sandbox price ID: `price_1UZxG3Dcs8799WD69ATQjjSD` (3 makeover credits, $5.00).

## Required before paid launch

1. Create a Stripe Checkout Session on the server with `STRIPE_SECRET_KEY` and the server-configured `STRIPE_PRICE_ID`; never accept a price or credit count from the browser.
2. Authenticate each customer with a stable, private user ID (not the shared beta Basic-auth password).
3. Store users, purchases, Stripe event IDs, and credit balances in durable transactional storage (e.g. Postgres); do not use per-process memory or browser local storage as the source of truth.
4. Verify Stripe webhook signatures using `STRIPE_WEBHOOK_SECRET`. On confirmed `checkout.session.completed` with payment status paid, grant exactly 3 credits to the associated authenticated user, idempotently by Checkout Session ID. Handle delayed payment success events if supported. Never grant credits from a success-page redirect alone.
5. Reserve a credit atomically before calling the image API; handle retries/failures with explicit refund/release rules. Enforce rate limits and maximum request concurrency in durable/shared storage where needed.
6. Keep Basic auth and the existing beta experience in place until paid flows, customer identity, webhook retries, and end-to-end sandbox purchases pass tests.
7. Before live activation: publish privacy/terms/refund policy, complete Stripe verification, use live Stripe credentials and a live price ID, and review AI costs.

## Security

Never commit secret keys. Add `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PRICE_ID` only to Render environment settings. Sandbox keys only until test flows pass. Stripe product and price IDs are not secrets.
