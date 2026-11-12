# Mia analytics and conversion instrumentation plan

Audited 2026-11-12 against Render's live `mia-stripe-checkout` branch.

## Current observations
- Render free web service; `/health` exists but Render healthCheckPath is unset.
- `/api/usage` uses in-memory process counters, not historical analytics.
- Supabase authentication, saved makeovers, and credit reservations exist.
- No first-party website analytics or UTM attribution in `static/index.html`.
- Render HTTP metrics query returned empty series; do not infer zero visitors.
- Sandbox checkout is guarded; **do not change any payment settings**.

## Proposed funnel
1. `page_view` (anonymous, consent/privacy policy considered)
2. `signup_started` (magic-link request)
3. `signup_completed` (verified authentication)
4. `makeover_started` (authenticated request)
5. `makeover_completed` (server-confirmed finalized generation)
6. `makeover_failed` (server-confirmed failure, classified without error text or personal details)

## Design constraints
- Store aggregated counts or pseudonymous IDs only when necessary. Do not record email, IP, uploaded photos, prompts, tokens, full URLs, or generated image data in analytics.
- Allowlisted attribution: source, medium, campaign, content; bound lengths and strip untrusted input.
- Server-side success is authoritative; browser success alone is not a completion event.
- Prefer durable storage with retention policy and access controls. Document privacy disclosure before deployment.
- Make instrumentation failure non-blocking to the core makeover flow.
- Exclude bot/health-check traffic from funnel counts where possible.
- Never enable Stripe checkout, change credits, or increase OpenAI usage limits as part of analytics.
- Test sign-in, free credits, makeover success/failure, storage, and Safari disconnect recovery before production rollout.

## Suggested next changes
- Add a narrowly scoped event-ingestion API and migration with rate limiting and bounded event schema.
- Add browser UTM capture and event hooks, preserving privacy choices.
- Add daily aggregate reporting with reliable event deduplication.
- Configure Render `/health` separately after confirming production health response.
