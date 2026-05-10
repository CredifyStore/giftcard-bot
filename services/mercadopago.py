"""
Integração com a API do Mercado Pago — pagamentos via PIX.

Documentação oficial:
https://www.mercadopago.com.br/developers/pt/docs/checkout-api/payment-methods/other-payment-methods/brasil/pix
"""
import uuid
import hmac
import hashlib
import httpx
from config import MP_ACCESS_TOKEN, MP_BASE_URL, WEBHOOK_SECRET

HEADERS = {
    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
    "Content-Type":  "application/json",
    "X-Idempotency-Key": "",  # sobrescrito em cada chamada
}


async def create_pix_charge(external_id: str, amount_cents: int, description: str, customer: dict) -> dict:
    """
    Cria um pagamento PIX no Mercado Pago.

    Parâmetros:
        external_id   → ID do topup (ex: TOP-AB12CD34)
        amount_cents  → valor em centavos (ex: 5000 = R$ 50,00)
        description   → texto da cobrança
        customer      → dict com 'name', 'email', 'document' (CPF, opcional)

    Retorna:
        payment_id    → ID do pagamento no MP (int)
        copia_cola    → string do PIX copia e cola
        qr_code_image → base64 do QR code (pode ser exibido como imagem)
        expires_at    → data/hora de expiração
    """
    amount_brl = amount_cents / 100  # MP usa reais, não centavos

    payload = {
        "transaction_amount": amount_brl,
        "description": description,
        "payment_method_id": "pix",
        "external_reference": external_id,
        "date_of_expiration": _expires_in(minutes=30),
        "payer": {
            "email":         customer.get("email", "cliente@email.com"),
            "first_name":    customer.get("name", "Cliente").split()[0],
            "last_name":     " ".join(customer.get("name", "Cliente").split()[1:]) or ".",
            "identification": {
                "type":   "CPF",
                "number": customer.get("document", ""),
            },
        },
    }

    headers = {**HEADERS, "X-Idempotency-Key": str(uuid.uuid4())}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MP_BASE_URL}/v1/payments",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
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
    """Consulta o status de um pagamento no Mercado Pago."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{MP_BASE_URL}/v1/payments/{payment_id}",
            headers=HEADERS,
        )
        resp.raise_for_status()
        return resp.json().get("status", "unknown")


def verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Valida a assinatura do webhook do Mercado Pago.

    O MP envia o header: x-signature
    Formato: ts=<timestamp>,v1=<hash>

    Documentação:
    https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks
    """
    if not signature_header:
        return False

    # Extrai ts e v1 do header
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    ts     = parts.get("ts", "")
    v1     = parts.get("v1", "")

    if not ts or not v1:
        return False

    # Monta o manifest para validação
    manifest = f"id:{{}};request-id:{{}};ts:{ts};"  # MP usa data.id e x-request-id

    # Validação simplificada usando o WEBHOOK_SECRET como chave
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, v1)


def _expires_in(minutes: int = 30) -> str:
    """Retorna data de expiração no formato ISO 8601 que o MP aceita."""
    from datetime import datetime, timezone, timedelta
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return exp.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
