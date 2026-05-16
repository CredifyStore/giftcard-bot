"""
Integração com a API do Mercado Pago — pagamentos via PIX.

Correções aplicadas:
- HEADERS não é mais global com token em tempo de import
- Headers montados dentro de cada função para refletir mudanças em runtime
"""
import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta

import httpx

from config import MP_ACCESS_TOKEN, MP_BASE_URL, WEBHOOK_SECRET


def _headers() -> dict:
    """Monta headers frescos a cada chamada — garante token sempre atualizado."""
    return {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def create_pix_charge(external_id: str, amount_cents: int,
                             description: str, customer: dict) -> dict:
    amount_brl = round(amount_cents / 100, 2)

    name_parts = customer.get("name", "Cliente Bot").split()
    payer = {
        "email":      customer.get("email") or f"user{user_hash(customer)}@giftcardbot.com",
        "first_name": name_parts[0],
        "last_name":  " ".join(name_parts[1:]) or "Bot",
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

    headers = {**_headers(), "X-Idempotency-Key": str(uuid.uuid4())}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MP_BASE_URL}/v1/payments",
            json=payload,
            headers=headers,
        )
        if resp.status_code != 201:
            raise Exception(f"MP API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

    pix = data.get("point_of_interaction", {}).get("transaction_data", {})
    return {
        "payment_id":    str(data["id"]),
        "copia_cola":    pix.get("qr_code", ""),
        "qr_code_image": pix.get("qr_code_base64", ""),
        "expires_at":    data.get("date_of_expiration", ""),
        "status":        data.get("status", "pending"),
    }


async def get_payment_status(payment_id: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{MP_BASE_URL}/v1/payments/{payment_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("status", "unknown")


def verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Valida assinatura HMAC do webhook do Mercado Pago.
    Header formato: ts=<timestamp>,v1=<hash>
    """
    if not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    v1 = parts.get("v1", "")
    if not v1:
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, v1)


def user_hash(customer: dict) -> str:
    return str(abs(hash(customer.get("name", "x"))))[:8]


def _expires_in(minutes: int = 30) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return exp.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
