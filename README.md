# Brigadá — site, pedidos e painel para doceria

Um único programa Python (sem instalar nada além do Python 3.9+) que entrega:
- **Site** em `/` (cardápio vivo, pedido, orçamento de eventos)
- **Painel da confeiteira** em `/admin`
- **API** em `/api/...` e banco SQLite

## Rodar no seu computador
```
cd brigada
ADMIN_TOKEN=minha-senha python3 server.py
```
Abra http://localhost:8000 (site) e http://localhost:8000/admin (painel).
Se você não definir `ADMIN_TOKEN`, uma senha temporária aparece no terminal.

## Subir na internet (escolha um)
**Render (mais simples):** envie esta pasta para um repositório no GitHub, crie um "Blueprint" no Render apontando para ele
(o `render.yaml` já configura disco para o banco). Defina `ADMIN_TOKEN` no painel do Render.

**Railway / Fly.io:** crie um serviço a partir do `Dockerfile`, monte um volume em `/data` e defina `ADMIN_TOKEN` e `WEBHOOK_SECRET`.

**VPS (qualquer Linux):**
```
docker build -t brigada .
docker run -d --restart=always -p 8000:8000 -v brigada-data:/data \
  -e ADMIN_TOKEN=sua-senha -e WEBHOOK_SECRET=segredo-longo brigada
```
Coloque Caddy ou Nginx na frente para ter HTTPS e seu domínio.

Sempre use **HTTPS** (a senha do painel trafega pela rede) e mantenha o volume/disco `/data`: é lá que ficam os pedidos.

## Personalizar
- **Cardápio inicial:** edite a lista na criação da tabela `menu` em `server.py` (só vale com banco novo) ou use o painel.
- **Pedido mínimo / antecedência:** `MIN_ITEMS` e `LEAD_DAYS` no topo do `server.py`.
- **Nome, textos e cores:** `public/index.html` (as cores estão em `:root`).

## O que ainda precisa de uma conta sua
**Pagamento real.** Hoje o Pix gera um código de teste e o cartão só registra o pedido.
1. Crie conta no Mercado Pago, Asaas ou Pagar.me.
2. Em `server.py`, troque a função `make_pix` por uma chamada à API do provedor que crie a cobrança e devolva o "copia e cola".
3. Configure o aviso de pagamento (webhook) do provedor para `POST /api/webhooks/payment` com o corpo `{"code":"CODIGO_DO_PEDIDO"}` e o cabeçalho `X-Signature` = HMAC-SHA256 do corpo com o `WEBHOOK_SECRET`.
   (Alguns provedores usam outro formato de assinatura: ajuste a rota para o formato deles.)

## Backup
Copie o arquivo `/data/brigada.db` (ou `brigada.db`) periodicamente.
