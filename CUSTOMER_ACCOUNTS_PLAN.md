# Mia customer accounts and credits

## Approved product experience
- Passwordless email signup with verified magic link.
- Exactly one free makeover for each new verified customer, subject to anti-abuse controls and a global free-trial spend cap.
- One-time $5 sandbox purchase adds three makeover credits after verified Stripe payment.
- Credits persist across devices.

## Security and architecture requirements
- Use managed authentication (e.g., Supabase Auth) and durable transactional database (Postgres).
- Verify authentication tokens on the server; never trust a browser-supplied user ID or credit balance.
- Atomically reserve and decrement credits for each generation; ensure retry-safe request IDs and clearly defined refund behavior on failures.
- Enforce a global daily free-trial limit and per-account limits; email verification alone cannot prevent repeated signups.
- Link Stripe Checkout Sessions to the authenticated user on the server and grant credits only from signature-verified, idempotently processed webhook events.
- Keep the shared Basic-auth beta protection until authentication, webhook processing, and credit checks are tested end to end.
- Do not activate the current prototype /api/checkout endpoint in production until durable account and credit enforcement exists.
- Do not commit secret keys, service-role credentials, or webhook secrets.

## Launch verification
1. Verify signup and magic link on mobile.
2. Confirm exactly one free rendering per new account, including simultaneous requests.
3. Verify checkout cancellation grants no credits.
4. Verify one successful sandbox payment grants exactly three credits, including repeated webhook deliveries.
5. Confirm credits persist across sessions/devices and zero balance blocks generation.
6. Confirm password-protected beta and cost caps remain in place until launch readiness.
