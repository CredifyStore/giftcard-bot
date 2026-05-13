"""
Integração com a API do Mercado Pago — pagamentos via PIX.
"""
import uuid
import hmac
import hashlib
import httpx
from config import MP_ACCESS_TOKEN, MP_BASE_URL, WEBHOOK_SECRET

HEADERS = {
    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
    "Content-Type":  "application/json",
}


async def create_pix_charge(external_id: str, amount_cents: int, description: str, customer: dict) -> dict:
    amount_brl = round(amount_cents / 100, 2)

    # Monta payer — CPF é opcional, não envia se estiver vazio
    payer = {
        "email": customer.get("email") or f"user{abs(hash(customer.get('name','x')))}@bot.com",
        "first_name": customer.get("name", "Cliente").split()[0],
        "last_name":  " ".join(customer.get("name", "Cliente").split()[1:]) or "Bot",
    }
    doc = customer.get("document", "").strip()
    if doc:
        payer["identification"] = {"type": "CPF", "number": doc}

    payload = {
        "transaction_amount": amount_brl,
        "description":        description[:250],
        "payment_method_id":  "pix",
        "external_reference": external_id,
        "date_of_expiration": _expires_in(minutes=30),
        "payer":              payer,
    }

    headers = {**HEADERS, "X-Idempotency-Key": str(uuid.uuid4())}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MP_BASE_URL}/v1/payments",
            json=payload,
            headers=headers,
        )
        if resp.status_code != 201:
            raise Exception(f"MP API error {resp.status_code}: {resp.text}")
        data = resp.json()

    pix_info = data.get("point_of_interaction", {}).get("transaction_data", {})

    return {
        "payment_id":    str(data["id"]),
        "copia_cola":    pix_info.get("qr_code", ""),
        "qr_code_image": pix_info.get("qr_code_base64", ""),
        "expires_at":    data.get("date_of_expiration", ""),
        "status":        data.get("status", "pending"),
    }


async def get_payment_status(payment_id: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{MP_BASE_URL}/v1/payments/{payment_id}",
            headers=HEADERS,
        )
        resp.raise_for_status()
        return resp.json().get("status", "unknown")


def verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    if not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    v1 = parts.get("v1", "")
    expected = hmac.new(WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def _expires_in(minutes: int = 30) -> str:
    from datetime import datetime, timezone, timedelta
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return exp.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
