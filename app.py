"""Mia Home AI — local prototype. Not production hardened."""
import base64
import asyncio
from datetime import datetime, timezone
import secrets
import io
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Header, Request
from fastapi.responses import FileResponse, PlainTextResponse
import stripe
import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageOps

load_dotenv()
ROOT = Path(__file__).parent
app = FastAPI(title="Mia Home AI", docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger("mia.credits")

@app.get("/static/mia-mascot.jpg", include_in_schema=False)
async def mia_mascot_image():
    return FileResponse(ROOT / "static" / "mia-mascot.jpg", media_type="image/jpeg")



@app.middleware("http")
async def private_beta_access(request, call_next):
    """Protect every endpoint, including the API, with browser HTTP Basic auth.

    Fail closed until MIA_ACCESS_PASSWORD is configured in Render.
    """
    password = os.getenv("MIA_ACCESS_PASSWORD", "")
    username = os.getenv("MIA_ACCESS_USER", "mia")
    if len(password) < 16:
        return PlainTextResponse("Private beta access is not configured.", status_code=503)
    supplied_user = supplied_pass = ""
    auth = request.headers.get("authorization", "")
    # Browsers cannot send HTTP Basic and Supabase Bearer in the same
    # Authorization header. Permit Bearer only on endpoints that independently
    # verify the Supabase token; all other beta routes still require Basic.
    if request.url.path == "/api/stripe/webhook":
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    if (request.url.path in ("/api/me", "/api/credits", "/api/makeover", "/api/checkout") or request.url.path.startswith("/api/makeovers")) and auth.startswith("Bearer "):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1], validate=True).decode("utf-8")
            supplied_user, supplied_pass = raw.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            pass
    if not (secrets.compare_digest(supplied_user, username) and
            secrets.compare_digest(supplied_pass, password)):
        return PlainTextResponse("Mia private beta — sign in required.", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="Mia Private Beta"',
                                          "Cache-Control": "no-store"})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response



# Private-beta cost guards. Counters are per running server process, not a
# billing guarantee: they reset when Render restarts or scales to more workers.
# Set a real project budget/alerts in the OpenAI dashboard as a second layer.
_usage_lock = asyncio.Lock()
_usage_date = ""
_usage_counts = {"advice": 0, "makeover": 0}
_inflight = {"advice": 0, "makeover": 0}


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(0, min(value, 1000))


async def reserve_generation(kind: str):
    """Count attempts before contacting OpenAI; bound simultaneous requests."""
    global _usage_date
    today = datetime.now(timezone.utc).date().isoformat()
    limit = _positive_limit(
        "MIA_DAILY_MAKEOVER_LIMIT" if kind == "makeover" else "MIA_DAILY_ADVICE_LIMIT",
        10 if kind == "makeover" else 30,
    )
    concurrent_limit = _positive_limit("MIA_MAX_CONCURRENT_REQUESTS", 2)
    async with _usage_lock:
        if _usage_date != today:
            _usage_date = today
            _usage_counts.update(advice=0, makeover=0)
        if _usage_counts[kind] >= limit:
            raise HTTPException(429, "Mia's daily private-beta limit has been reached. Try again tomorrow (UTC).")
        if sum(_inflight.values()) >= concurrent_limit:
            raise HTTPException(429, "Mia is busy generating a design. Please try again shortly.")
        _usage_counts[kind] += 1
        _inflight[kind] += 1


async def release_generation(kind: str):
    async with _usage_lock:
        _inflight[kind] = max(0, _inflight[kind] - 1)


@app.get("/api/usage")
async def usage():
    today = datetime.now(timezone.utc).date().isoformat()
    async with _usage_lock:
        counts = _usage_counts.copy() if _usage_date == today else {"advice": 0, "makeover": 0}
        return {
            "date_utc": today,
            "used": counts,
            "daily_limits": {
                "advice": _positive_limit("MIA_DAILY_ADVICE_LIMIT", 30),
                "makeover": _positive_limit("MIA_DAILY_MAKEOVER_LIMIT", 10),
            },
            "active_requests": sum(_inflight.values()),
            "note": "Private-beta per-process counters; reset on server restart. Not a billing cap.",
        }




# Sandbox checkout is fail-closed until explicitly enabled and a webhook secret exists.
STRIPE_SANDBOX_PRICE_ID = "price_1UZxG3Dcs8799WD69ATQjjSD"

def sandbox_ready():
    return (os.getenv("MIA_ENABLE_SANDBOX_CHECKOUT") == "true"
            and os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_test_")
            and os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_")
            and os.getenv("MIA_PUBLIC_URL", "").startswith("https://")
            and bool(os.getenv("SUPABASE_SECRET_KEY")))

@app.get("/api/checkout/status")
async def checkout_status():
    return {"sandbox_enabled": sandbox_ready(), "live_enabled": False}

@app.post("/api/checkout")
async def create_checkout(authorization: str | None = Header(default=None)):
    customer = await verified_customer(authorization)
    if not sandbox_ready():
        raise HTTPException(503, "Sandbox checkout is not enabled.")
    origin = os.getenv("MIA_PUBLIC_URL", "").rstrip("/")
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            api_key=os.environ["STRIPE_SECRET_KEY"],
            mode="payment",
            client_reference_id=customer["user_id"],
            customer_email=customer["email"],
            line_items=[{"price": STRIPE_SANDBOX_PRICE_ID, "quantity": 1}],
            success_url=origin + "/?checkout=success",
            cancel_url=origin + "/?checkout=cancel",
            metadata={"pack": "three_makeovers", "environment": "sandbox",
                      "user_id": customer["user_id"]},
        )
        return {"url": session.url}
    except Exception as exc:
        raise HTTPException(502, "Unable to start sandbox checkout.") from exc


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    # Raw body and signature are required. Browser redirects never grant credits.
    if not sandbox_ready():
        raise HTTPException(503, "Sandbox webhook is not enabled.")
    payload = await request.body()
    if len(payload) > 100_000:
        raise HTTPException(413, "Webhook payload too large.")
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, os.environ["STRIPE_WEBHOOK_SECRET"])
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(400, "Invalid Stripe webhook.") from exc
    if event["type"] != "checkout.session.completed":
        return {"received": True}
    session = event["data"]["object"]
    user_id = session.get("client_reference_id")
    metadata = session.get("metadata") or {}
    if (session.get("livemode") is not False
            or session.get("mode") != "payment"
            or session.get("payment_status") != "paid"
            or session.get("currency") != "usd"
            or session.get("amount_total") != 500
            or metadata.get("pack") != "three_makeovers"
            or metadata.get("environment") != "sandbox"
            or not user_id or metadata.get("user_id") != user_id):
        raise HTTPException(400, "Checkout session does not match the credit pack.")
    # Independently retrieve the purchased line item from Stripe.
    try:
        lines = await asyncio.to_thread(
            stripe.checkout.Session.list_line_items, session["id"],
            api_key=os.environ["STRIPE_SECRET_KEY"], limit=10)
    except Exception as exc:
        raise HTTPException(503, "Unable to verify Stripe line items.") from exc
    items = lines.get("data", [])
    if (lines.get("has_more") or len(items) != 1
            or items[0].get("price", {}).get("id") != STRIPE_SANDBOX_PRICE_ID
            or items[0].get("quantity") != 1):
        raise HTTPException(400, "Unexpected Stripe purchase.")
    granted = await credit_rpc("mia_grant_purchase", {
        "p_user_id": user_id,
        "p_session_id": session["id"],
        "p_event_id": event["id"]})
    return {"received": True, "credits_granted": bool(granted)}


# Validate customer tokens against Supabase Auth, never browser-supplied IDs.
# This is a prerequisite for durable credit accounting, not credit enforcement.
async def verified_customer(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in with your Mia email account.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Missing authentication token.")
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not url.startswith("https://") or not key:
        raise HTTPException(503, "Customer authentication is not configured.")
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            response = await http.get(
                url + "/auth/v1/user",
                headers={"apikey": key, "Authorization": "Bearer " + token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Authentication provider unavailable.") from exc
    if response.status_code != 200:
        raise HTTPException(401, "Your sign-in expired. Please sign in again.")
    user = response.json()
    if not user.get("id") or not user.get("email_confirmed_at"):
        raise HTTPException(403, "Verify your email before using Mia.")
    return {"user_id": user["id"], "email": user.get("email", "")}


@app.get("/api/me")
async def current_customer(authorization: str | None = Header(default=None)):
    return await verified_customer(authorization)



# Credit operations are disabled until the server-only Supabase key is installed.
# Never expose SUPABASE_SECRET_KEY to the website or source control.
def credit_config():
    url=os.getenv("SUPABASE_URL","").rstrip("/")
    key=os.getenv("SUPABASE_SECRET_KEY","")
    if not url.startswith("https://") or not key:
        raise HTTPException(503,"Mia credits are not configured yet.")
    return url,key

async def credit_rpc(name: str, payload: dict):
    url,key=credit_config()
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response=await http.post(url+"/rest/v1/rpc/"+name,
                headers={"apikey":key,"Authorization":"Bearer "+key,
                         "Content-Type":"application/json"},
                json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(503,"Credit database unavailable.") from exc
    if response.status_code >= 400:
        raise HTTPException(503,"Credit database operation failed.")
    return response.json()

async def storage_request(method: str, path: str, *, content: bytes | None = None, content_type: str | None = None):
    url, key = credit_config()
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.request(method, url + path, headers=headers, content=content)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Saved makeover storage is unavailable.") from exc
    if response.status_code >= 400:
        logger.error("Storage request failed status=%s path=%s", response.status_code, path.split("?")[0])
        raise HTTPException(503, "Could not save or retrieve your makeover.")
    return response


@app.get("/api/makeovers")
async def saved_makeovers(authorization: str | None = Header(default=None)):
    customer = await verified_customer(authorization)
    url, key = credit_config()
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response = await http.get(url + "/rest/v1/mia_saved_makeovers",
                params={"user_id": "eq." + customer["user_id"],
                        "select": "request_id,created_at", "order": "created_at.desc", "limit": "20"},
                headers={"apikey": key, "Authorization": "Bearer " + key})
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Makeover history unavailable.") from exc
    if response.status_code >= 400:
        raise HTTPException(503, "Makeover history unavailable.")
    return {"makeovers": response.json()}


@app.get("/api/makeovers/{request_id}")
async def saved_makeover_image(request_id: str, authorization: str | None = Header(default=None)):
    import uuid
    try:
        parsed_id = str(uuid.UUID(request_id))
    except ValueError:
        raise HTTPException(400, "Invalid makeover ID.")
    customer = await verified_customer(authorization)
    url, key = credit_config()
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response = await http.get(url + "/rest/v1/mia_saved_makeovers",
                params={"request_id": "eq." + parsed_id, "user_id": "eq." + customer["user_id"],
                        "select": "storage_path", "limit": "1"},
                headers={"apikey": key, "Authorization": "Bearer " + key})
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Makeover history unavailable.") from exc
    if response.status_code >= 400:
        raise HTTPException(503, "Makeover history unavailable.")
    rows = response.json()
    if not rows:
        raise HTTPException(404, "Saved makeover not found.")
    from fastapi.responses import Response
    path = rows[0]["storage_path"]
    image = await storage_request("GET", "/storage/v1/object/mia-makeovers/" + path)
    return Response(content=image.content, media_type="image/png",
                    headers={"Cache-Control": "private, no-store"})


@app.get("/api/credits")
async def get_credits(authorization: str | None = Header(default=None)):
    customer=await verified_customer(authorization)
    # Repair old reservations after interrupted requests or server restarts.
    # The database only touches requests older than 30 minutes and is idempotent.
    try:
        repaired = await credit_rpc("mia_reconcile_stale_credits",{"p_user_id":customer["user_id"]})
        if repaired:
            logger.warning("Reconciled %s stale makeover reservations for user=%s", repaired, customer["user_id"])
    except Exception:
        logger.exception("Stale credit reconciliation failed")
    result=await credit_rpc("mia_get_or_create_credits",{"p_user_id":customer["user_id"]})
    if not isinstance(result,list) or len(result)!=1:
        raise HTTPException(503,"Unable to load your credits.")
    return result[0]

PERSONA = ("You are Mia, a clearly disclosed AI home styling assistant. "
           "Be friendly, candid, specific, budget-conscious, and concise. "
           "Explain what works and what to change. Avoid claiming dimensions or materials "
           "you cannot verify from an image. Do not provide structural, electrical, or "
           "permitting assurances; recommend qualified professionals where appropriate.")


def client():
    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "replace_with_your_key":
        raise HTTPException(503, "Add OPENAI_API_KEY to your .env file first.")
    return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120)


async def normalized_image(upload: UploadFile):
    limit = int(os.getenv("MAX_IMAGE_MB", "10")) * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "Image exceeds the upload limit.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2048, 2048))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        buf.name = "room.png"
        return buf
    except Exception as exc:
        raise HTTPException(400, "Upload a valid JPEG, PNG, or WebP photo.") from exc


@app.get("/")
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "api_configured": bool(os.getenv("OPENAI_API_KEY"))}


@app.post("/api/advice")
async def advice(photo: UploadFile = File(...), request: str = Form("How can I improve this space?"),
                 budget: str = Form("Not specified")):
    if len(request) > 1500 or len(budget) > 100:
        raise HTTPException(400, "Request too long.")
    buf = await normalized_image(photo)
    await reserve_generation("advice")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    try:
        result = await client().responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini"),
            instructions=PERSONA,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": f"Budget: {budget}. Request: {request}. Give 3-5 practical, specific styling suggestions, then one caution if needed."},
                {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"}
            ]}], max_output_tokens=550)
        return {"advice": result.output_text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"AI advice failed: {type(exc).__name__}. Check model access and API billing.") from exc
    finally:
        await release_generation("advice")


@app.post("/api/makeover")
async def makeover(photo: UploadFile = File(...), request: str = Form(...),
                   budget: str = Form("Not specified"), authorization: str | None = Header(default=None)):
    if not request.strip() or len(request) > 1500 or len(budget) > 100:
        raise HTTPException(400, "Enter a makeover request (up to 1,500 characters).")
    customer = await verified_customer(authorization)
    credit_config()  # Fail closed before starting an expensive request.
    buf = await normalized_image(photo)
    await reserve_generation("makeover")
    request_id = str(__import__("uuid").uuid4())
    reserved = False
    success = False
    prompt = (
        "Edit this uploaded photograph into a photorealistic home-design concept. "
        "Preserve camera perspective, original room/building geometry, windows, doors, "
        "roofline, and any architectural elements unless the user specifically asks to change them. "
        "Keep all unrequested elements unchanged. Do not add text or watermarks. "
        "This is a visual concept, not a construction drawing or guarantee of feasibility. "
        f"Customer budget: {budget}. Requested change: {request}"
    )
    try:
        kind = await credit_rpc("mia_reserve_credit", {"p_user_id":customer["user_id"],"p_request_id":request_id})
        if kind == "insufficient":
            raise HTTPException(402,"No makeover credits remaining. Buy three more credits to continue.")
        if kind not in ("free","paid"):
            raise HTTPException(503,"Could not reserve a makeover credit.")
        reserved = True
        result = await client().images.edit(
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
            image=buf,
            prompt=prompt,
            size="1024x1024",
        )
        image_b64 = result.data[0].b64_json
        if not image_b64:
            raise ValueError("Image API did not return base64 image data")
        # Persist the generated result before charging the reserved credit.
        image_bytes = base64.b64decode(image_b64, validate=True)
        if len(image_bytes) > 10 * 1024 * 1024:
            raise HTTPException(502, "Generated image exceeds the storage limit.")
        storage_path = customer["user_id"] + "/" + request_id + ".png"
        await storage_request("POST", "/storage/v1/object/mia-makeovers/" + storage_path,
                              content=image_bytes, content_type="image/png")
        url, key = credit_config()
        async with httpx.AsyncClient(timeout=12) as http:
            recorded = await http.post(url + "/rest/v1/mia_saved_makeovers",
                headers={"apikey": key, "Authorization": "Bearer " + key,
                         "Content-Type": "application/json", "Prefer": "return=minimal"},
                json={"request_id": request_id, "user_id": customer["user_id"],
                      "storage_path": storage_path})
        if recorded.status_code >= 400:
            logger.error("Failed to record saved makeover request_id=%s status=%s", request_id, recorded.status_code)
            raise HTTPException(503, "Could not record saved makeover; credit will be refunded.")
        finished = await credit_rpc("mia_finish_credit",{"p_user_id":customer["user_id"],"p_request_id":request_id,"p_success":True})
        if finished is not True:
            raise HTTPException(503, "Could not finalize makeover credit. Please contact support before retrying.")
        success = True
        return {"image": f"data:image/png;base64,{image_b64}", "request_id": request_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Image generation failed: {type(exc).__name__}. Check model access, billing, and image API parameters.") from exc
    finally:
        # Keep refund cleanup running even if Safari disconnects mid-request.
        async def cleanup():
            if reserved and not success:
                try:
                    refunded = await credit_rpc("mia_finish_credit", {
                        "p_user_id": customer["user_id"],
                        "p_request_id": request_id,
                        "p_success": False,
                    })
                    if refunded is not True:
                        logger.error("Credit refund not confirmed request_id=%s", request_id)
                except Exception:
                    logger.exception("Credit refund failed request_id=%s", request_id)
            await release_generation("makeover")

        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            logger.warning("Client disconnected; credit cleanup continues request_id=%s", request_id)
