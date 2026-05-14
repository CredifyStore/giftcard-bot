# 🎁 Bot de Vendas de Giftcards — Telegram

Bot completo para venda automatizada de giftcards no Telegram, com pagamento via **PIX** integrado ao **BaasPago**.

---

## 🗂️ Estrutura do projeto

```
giftcard_bot/
├── main.py               # Ponto de entrada — inicia bot + servidor webhook
├── config.py             # Configurações e catálogo de produtos
├── requirements.txt
├── .env.example          # Template de variáveis de ambiente
├── giftcards.db          # Gerado automaticamente (SQLite)
├── handlers/
│   ├── user.py           # Comandos e fluxo de compra do usuário
│   ├── admin.py          # Painel de administração
│   └── webhook.py        # Recebe notificações de pagamento do BaasPago
├── models/
│   └── database.py       # Operações SQLite (pedidos, códigos)
└── services/
    └── baaspago.py       # Integração com a API BaasPago
```

---

## ⚙️ Instalação

### 1. Clone e instale as dependências

```bash
git clone https://github.com/seu-repo/giftcard_bot
cd giftcard_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure as variáveis de ambiente

```bash
cp .env.example .env
nano .env   # Edite com seus dados
```

| Variável | Descrição |
|---|---|
| `BOT_TOKEN` | Token do bot (obtido no [@BotFather](https://t.me/BotFather)) |
| `ADMIN_IDS` | IDs do Telegram dos admins, separados por vírgula |
| `GROUP_ID` | ID do grupo onde o bot opera |
| `BAASPAGO_API_KEY` | Chave de API do painel BaasPago |
| `WEBHOOK_SECRET` | Segredo para validar webhooks |
| `WEBHOOK_URL` | URL HTTPS pública do seu servidor |
| `PORT` | Porta do servidor webhook (padrão: 8443) |

### 3. Configure o BaasPago

No painel BaasPago:
1. Acesse **Configurações → Webhooks**
2. Adicione a URL: `https://seusite.com/webhook/baaspago`
3. Selecione os eventos: `charge.paid`, `charge.expired`
4. Copie o segredo gerado para o `.env` em `WEBHOOK_SECRET`

### 4. Rode o bot

```bash
python main.py
```

---

## 🛠️ Adicionar giftcards ao estoque

Acesse o painel de admin com `/admin` no Telegram (apenas para IDs em `ADMIN_IDS`):

1. Clique em **➕ Adicionar códigos**
2. Selecione o produto e o valor
3. Envie os códigos, **um por linha**:
   ```
   ABCD-1234-EFGH
   WXYZ-5678-MNOP
   ```

---

## 🗂️ Configurar o catálogo

Edite `config.py` — a variável `CATALOG`:

```python
CATALOG = {
    "meu_produto": {
        "name": "Meu Produto",
        "emoji": "🎯",
        "values": [
            {"label": "R$ 50", "amount": 5000},   # amount em centavos
            {"label": "R$ 100", "amount": 10000},
        ],
    },
}
```

---

## 🚀 Deploy em produção

### Railway (recomendado — gratuito para começar)

1. Faça push do código para um repositório GitHub
2. Acesse [railway.app](https://railway.app) e importe o repo
3. Adicione as variáveis de ambiente no painel do Railway
4. O Railway gera uma URL HTTPS automática — use ela no `WEBHOOK_URL`

### VPS com systemd

```ini
# /etc/systemd/system/giftcardbot.service
[Unit]
Description=Giftcard Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/giftcard_bot
ExecStart=/opt/giftcard_bot/venv/bin/python main.py
Restart=always
EnvironmentFile=/opt/giftcard_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable giftcardbot
systemctl start giftcardbot
```

---

## 📋 Fluxo de compra

```
Usuário → /start → Catálogo → Escolhe produto → Escolhe valor
→ Bot cria pedido → BaasPago gera PIX → Usuário paga
→ BaasPago notifica webhook → Bot entrega código automaticamente ✅
```

---

## 🔒 Segurança

- Webhooks validados com HMAC-SHA256
- Códigos entregues apenas após confirmação de pagamento
- Idempotência: pedido entregue não é processado novamente
- Admins autenticados por ID do Telegram

---

## 📞 Suporte

Abra uma issue ou entre em contato com o desenvolvedor.
