# Lever Money — Plano de Evolucao (Conversas)

> Documento vivo. Registra conversas e decisoes sobre as proximas evolucoes do sistema.

---

## Sessao 1 — 2026-02-19

### Topico 1: Reconciliacao 100% com Extrato MP

**Pergunta do Eryk:** E possivel fazer um codigo que bata 100% com os valores do extrato do Mercado Pago, dada a complexidade dos lancamentos deles?

**Conclusao: SIM, e possivel.** O sistema ja demonstrou `diff = R$ 0,00` no comparativo diario de caixa da 141AIR em janeiro/2026.

**Por que e viavel:**
O extrato (`account_statement` / `release_report`) e um ledger fechado — cada linha tem `source_id`, valor de credito/debito e data. Conjunto finito e deterministico.

**Mapa de cobertura atual:**

| Tipo de linha no extrato | Fonte no sistema | Status |
|---|---|---|
| Venda aprovada (settlement) | `processor.py` → baixa API | Coberto |
| Comissao / fee | Embutido no calculo do processor (net) | Coberto |
| Shipping fee | Embutido no calculo do processor (net) | Coberto |
| Financing fee/transfer | Net-neutral, cancela entre si | Coberto (ignora) |
| Refund total/parcial | `_process_refunded` / `_process_partial_refund` | Coberto |
| Chargeback + mediacao | Branch no processor | Coberto |
| Payout (transferencia p/ banco) | Legado / mp_expenses | Coberto |
| Reserve for dispute | Legado | Parcial |
| Boleto / DARF / SaaS | `expense_classifier` → mp_expenses | Coberto |
| PIX / transferencia intra | `expense_classifier` → mp_expenses | Coberto |
| Cashback ML | `expense_classifier` → mp_expenses | Coberto |
| Taxa de antecipacao | Linha separada no extrato | Gap potencial |
| Liberacao parcial | Linha separada no extrato | Gap potencial |
| Ajustes manuais ML | Raro, mas existe | Gap potencial |

**Estrategia que ja funciona (regra 11.3b):**

```
extrato_total_dia = baixa_api_dia + ajustes_legado_dia
```

- `baixa_api_dia` = soma do net de vendas liquidadas com money_release_date = dia
- `ajustes_legado_dia` = tudo que o processor nao cobre (refunds, mediations, reserves, payouts, non-order)

**O que falta para garantir 100% sistematicamente:**
1. Linhas "raras" que nenhuma regra do classifier captura (ajuste manual ML, bonificacao)
2. Diferencas de centavos por arredondamento entre charges_details e extrato
3. Timing D/D+1 — transacao pode aparecer num dia no extrato e noutro na API por fuso

**Proximo passo (quando chegarmos nesse topico):** Analisar extrato real da 141AIR (CSV em `testes/extratos/`) para mapear todos os `record_type` e identificar gaps exatos.

---

### Topico 2: Onboarding Simplificado

#### Problema Atual

O onboarding de uma nova empresa hoje exige um tecnico para:
1. Criar o seller no Supabase (ou via self-service install)
2. Conectar OAuth do Mercado Livre
3. Aprovar via admin API com config completa (conta bancaria CA, centro de custo, contato, etc.)
4. Rodar backfill manual para puxar historico
5. Monitorar fila e verificar lancamentos no CA

Isso e um gargalo. O objetivo e que o proprio Eryk consiga habilitar uma empresa direto pelo dashboard, sem depender de um dev.

#### Visao do Novo Fluxo

Dois modos de operacao por empresa:

| Modo | O que faz | Quem usa |
|------|-----------|----------|
| **Dashboard only** | Acompanha faturamento (sync ML orders), metas, graficos. SEM integracao CA. | Empresas que nao precisam de controle financeiro granular |
| **Dashboard + Conta Azul** | Tudo acima + conciliacao automatica (receita, comissao, frete, baixas) | Empresas com controle financeiro completo |

#### Conceito: Data de Inicio (CA)

Para empresas no modo "Dashboard + Conta Azul":

- O operador define uma **data de inicio** no dashboard
- Todos os lancamentos com `money_release_date >= data_inicio` sao puxados para o CA
- Na ativacao, roda um **backfill automatico** de `data_inicio` ate `ontem`
- A partir dai, o **daily sync** assume o fluxo diario normalmente

Isso significa:
- Sem retroativo infinito (operador escolhe de quando comecar)
- Transicao limpa: o CA so ve lancamentos a partir da data escolhida
- Baixas tambem respeitam: so parcelas com vencimento >= data_inicio

---

#### Respostas do Eryk — Rodada 1 (2026-02-19)

**1. Config CA — Conta bancaria por seller:**
Cada seller que entrar no onboarding tera uma conta propria no Conta Azul para ser sua conta do Mercado Pago. Ou seja, cada seller precisa de um UUID de `ca_conta_bancaria` unico configurado no dashboard.

**2. Transicao de modo — Confirmado:**
Sim, faz sentido. Uma empresa pode comecar como "dashboard only" e depois ser promovida para "dashboard + CA". Basta setar a data de inicio e as configs CA.

**3. Backfill — Logica refinada (IMPORTANTE):**
O usuario NAO seta um periodo de historico manualmente. A logica e:

- O operador define a **data de inicio** (ex: 01/02/2026 = primeiro dia do mes)
- O sistema busca via API **todas as vendas cujo `money_release_date` cai naquele mes** (e nos meses seguintes), **mesmo que a venda tenha sido aprovada em meses anteriores**
- Essas vendas sao lancadas no CA com a **data de competencia correta** (`date_approved`), que pode ser anterior a data de inicio
- Isso garante que:
  - **Caixa (fluxo de caixa):** correto a partir da data de inicio — tudo que libera dali pra frente esta registrado
  - **DRE (competencia):** correto porque usa `date_approved` real, mesmo que de mes anterior

**Exemplo pratico:**
```
Data de inicio: 01/02/2026

Venda aprovada em 28/01 com money_release_date 05/02:
  → Receita CA: competencia 28/01, vencimento 05/02
  → Baixa: criada quando 05/02 chegar (ou no backfill se ja passou)
  → Efeito: aparece no DRE de janeiro, no caixa de fevereiro ✓

Venda aprovada em 03/02 com money_release_date 10/02:
  → Receita CA: competencia 03/02, vencimento 10/02
  → Fluxo normal ✓
```

**Implicacao tecnica:** O backfill de ativacao precisa buscar payments por `money_release_date >= ca_start_date` (nao por `date_approved`). Isso e diferente do backfill atual que busca por `date_approved`. Sera necessario um modo de busca alternativo ou complementar.

**4. Non-order payments — XLSX para Google Drive:**
Non-orders (boletos, PIX, SaaS, transferencias) sao pegos por **data de vencimento** (mesma logica do `money_release_date`) e geram XLSX que vao automaticamente para o Google Drive. Quem cuida do financeiro e que vai setar a data de competencia, categoria, etc. no CA.

Ou seja: o sistema NAO lanca non-orders diretamente no CA. Apenas gera os arquivos e faz upload automatico. O trabalho manual e do financeiro.

**5. Faturamento sync — Confirmado:**
No modo "dashboard only", basta o seller ter tokens ML e o FaturamentoSyncer ja funciona. Nenhuma config CA necessaria.

---

#### Respostas do Eryk — Rodada 2 (2026-02-19)

**1. Busca por money_release_date — CONFIRMADO pela API ML:**

Verificacao tecnica: a `search_payments` API do MP **ja suporta** `money_release_date` como `range_field`.

O proprio codigo em `ml_api.py:98-126` ja aceita o parametro:
```python
async def search_payments(
    seller_slug, begin_date, end_date, offset=0, limit=50,
    range_field="date_approved",  # aceita: date_approved, date_last_updated, date_created, money_release_date
)
```

Confirmado pela documentacao oficial do Mercado Pago:
> The range parameter can refer to: "date_created", "date_last_updated", "date_approved", and "money_release_date".

Fonte: [Search payments - Mercado Pago Developers](https://www.mercadopago.com.ar/developers/en/reference/payments/_payments_search/get)

**Nenhuma alteracao necessaria na camada HTTP.** Basta passar `range_field="money_release_date"` no backfill de ativacao.

**2. Config CA no dashboard — Dropdown via CA API:**
Confirmado: dropdowns populados via `GET /admin/ca/contas-financeiras` e `GET /admin/ca/centros-custo` (endpoints ja existem).

**3. Upload Google Drive — Reaproveitar infra legacy:**
Confirmado: usar a mesma infra de `legacy_daily_export.py` que ja suporta `LEGACY_DAILY_UPLOAD_MODE=gdrive`.

---

#### Respostas do Eryk — Rodada 3 (2026-02-19)

**1. Daily sync apos ativacao — Manter como esta:**
Confirmado. O daily sync continua buscando por `date_approved` (D-1 a D-3). Vendas novas sao pegas pelo date_approved, baixas vem depois pelo scheduler. Sem alteracao.

**2. Contato ML no CA — Fixo para todos:**
Todos os sellers usam o mesmo `CA_CONTATO_ML` fixo (`b247cccb-...` = "MERCADO LIVRE"). Nao precisa ser configuravel por seller.

**3. Goals — Nao sobrescrever existentes:**
- Se a `revenue_line` ja existe e ja tem metas → **nao sobrescrever** com zeros
- Se e uma linha nova → cria goals com valor=0, operador define depois via botao "editar metas" no dashboard
- Ou seja: goals NAO fazem parte do fluxo de onboarding. Sao configuradas depois.

**4. Permissao — Somente admin com senha:**
O painel de onboarding so e acessivel para o admin autenticado (mesmo mecanismo de `X-Admin-Token` ja existente).

**5. Erro no backfill — Continuar de onde parou:**
Se o backfill falha no meio, deve **continuar de onde parou** (nao reiniciar do zero). Motivo critico: a CA API nao permite exclusao de eventos financeiros — se rodar tudo de novo, os ja criados ficariam duplicados (a idempotencia da `ca_jobs` previne duplicatas na fila, mas os eventos JA criados no CA nao podem ser desfeitos).

Na pratica: o backfill ja e naturalmente resumivel porque:
- `_upsert_payment()` e idempotente (nao recria se ja existe)
- `ca_jobs` tem `idempotency_key` (nao duplica jobs)
- Payments ja processados (`synced`, `queued`) sao filtrados no backfill
- Basta re-executar o backfill com os mesmos parametros e ele pula o que ja foi feito

Porem, para dar visibilidade ao operador, o sistema deve:
- Persistir progresso do backfill (ex: ultimo payment_id processado, contadores)
- Mostrar no dashboard: "Backfill: 450/520 payments processados, 5 erros"
- Permitir re-trigger manual se parou por erro

**6. Sellers existentes — Migrar para novo schema:**
Os 3 sellers atuais (141air, net-air, netparts-sp) serao migrados:
- `integration_mode = "dashboard_only"` (nenhum deles tem CA habilitado hoje)
- `ca_start_date = null`
- `ca_backfill_status = null`

Quando/se algum deles precisar de CA, o operador faz upgrade pelo dashboard (seta data de inicio + config CA).

**7. ca_contato_ml — Fixo para todos:**
Manter o UUID fixo `b247cccb-38a2-4851-bf0e-700c53036c2c` ("MERCADO LIVRE") para todos os sellers. Nao precisa ser configuravel.

---

#### Decisoes Consolidadas (FINAL)

| # | Decisao | Detalhe |
|---|---------|---------|
| 1 | Modo de operacao | `dashboard_only` ou `dashboard_ca` por seller |
| 2 | Conta CA | Cada seller tem sua propria `ca_conta_bancaria` (dropdown CA API) |
| 3 | Centro de custo CA | Cada seller tem seu proprio `ca_centro_custo_variavel` (dropdown CA API) |
| 4 | Contato ML CA | Fixo para todos: `b247cccb-...` ("MERCADO LIVRE") |
| 5 | Transicao de modo | Permitida: dashboard_only → dashboard_ca a qualquer momento |
| 6 | Criterio do backfill | Por `money_release_date >= ca_start_date` (API ML suporta nativamente) |
| 7 | Competencia no backfill | `date_approved` real (pode ser anterior a data de inicio) |
| 8 | Non-orders | XLSX → Google Drive automatico (infra legacy). Financeiro categoriza manualmente no CA |
| 9 | Dashboard only | Funciona so com ML tokens + FaturamentoSyncer |
| 10 | Daily sync pos-ativacao | Mantem `date_approved` (sem mudanca) |
| 11 | Goals no onboarding | Nao sobrescrever existentes. Novas linhas: goals=0, operador seta depois |
| 12 | Permissao onboarding | Somente admin autenticado (X-Admin-Token) |
| 13 | Erro no backfill | Continuar de onde parou (idempotente). Dashboard mostra progresso |
| 14 | Sellers existentes | Migrar para `dashboard_only` (nenhum tem CA hoje) |
| 15 | Config CA | Dropdowns populados via CA API (endpoints existentes) |
| 16 | Upload Drive | Reaproveita `legacy_daily_export.py` (modo gdrive) |

---

#### Notas de Arquitetura (atualizadas)

**Campos novos na tabela `sellers`:**

```sql
ALTER TABLE sellers ADD COLUMN integration_mode text NOT NULL DEFAULT 'dashboard_only';
  -- 'dashboard_only' | 'dashboard_ca'

ALTER TABLE sellers ADD COLUMN ca_start_date date;
  -- Data de inicio para integracao CA (null se dashboard_only)

ALTER TABLE sellers ADD COLUMN ca_backfill_status text;
  -- 'pending' | 'running' | 'completed' | 'failed' | null

ALTER TABLE sellers ADD COLUMN ca_backfill_started_at timestamptz;
ALTER TABLE sellers ADD COLUMN ca_backfill_completed_at timestamptz;

ALTER TABLE sellers ADD COLUMN ca_backfill_progress jsonb;
  -- {"total": 520, "processed": 450, "errors": 5, "last_payment_id": 144370799868}
```

**Logica de backfill de ativacao (nova):**

```
1. Operador seta ca_start_date = 01/02/2026
2. ca_backfill_status = "pending"
3. Background task inicia:
   a. ca_backfill_status = "running", ca_backfill_started_at = now()
   b. Busca payments: search_payments(range_field="money_release_date",
      begin_date=ca_start_date, end_date=ontem)
   c. Filtra already_done (payments + mp_expenses ja no Supabase)
   d. Para cada payment com order_id:
      - process_payment_webhook() (receita + comissao + frete)
      - Competencia = date_approved, Vencimento = money_release_date
   e. Para cada payment sem order_id:
      - classify_non_order_payment() → mp_expenses
   f. Atualiza ca_backfill_progress a cada batch
4. Se todos processados sem erro:
   ca_backfill_status = "completed", ca_backfill_completed_at = now()
5. Se falha:
   ca_backfill_status = "failed" (operador pode re-trigger, continua de onde parou)
6. Gera XLSX de non-orders → upload Google Drive (infra legacy)
7. Daily sync assume (D-1 a D-3, fluxo normal)
```

**Fluxo de onboarding completo (FINAL):**

```
1. Seller autoriza no ML (link self-service: /auth/ml/install)
2. Aparece no dashboard admin como "pendente"
3. Admin abre painel de onboarding:
   a. Define nome, grupo, segmento
   b. Escolhe modo:
      → "Dashboard only": clica "Ativar" → pronto
      → "Dashboard + CA":
         - Define data de inicio (primeiro dia do mes)
         - Seleciona ca_conta_bancaria (dropdown CA API)
         - Seleciona ca_centro_custo_variavel (dropdown CA API)
         - Clica "Ativar"
4. Sistema:
   a. Salva config no seller
   b. Cria revenue_line (se nao existe)
   c. Cria 12 goals com valor=0 (somente se nao existem)
   d. Ativa seller → FaturamentoSyncer comeca imediatamente
   e. Se modo CA: dispara backfill em background
5. Admin acompanha progresso do backfill no dashboard:
   "Backfill: 450/520 payments | 5 erros | Rodando..."
6. Apos completar: daily sync assume automaticamente

Upgrade (dashboard_only → dashboard_ca):
1. Admin abre seller existente no dashboard
2. Muda modo para "Dashboard + CA"
3. Define data de inicio + seleciona conta/centro de custo
4. Sistema dispara backfill (mesma logica)
```

---

#### Respostas do Eryk — Rodada 4 (2026-02-19)

**1. Link OAuth ML — Copiar e enviar por WhatsApp:**
O admin copia o link de conexao ML e manda por WhatsApp manualmente. O dashboard deve ter um **campo com botao "copiar link"** na area do seller para facilitar.

**2. Data de inicio — Sempre primeiro dia do mes:**
A data de inicio e sempre o **1o dia de um mes**. O admin escolhe o mes/ano (ex: "Fevereiro 2026" → `2026-02-01`). Simplifica o UX com um date picker de mes, nao de dia.

**3. Non-orders XLSX — Consolidado no backfill + diario assume:**
- No backfill de ativacao: gera XLSX consolidado com todo o historico de non-orders
- Depois: o pipeline diario assume a geracao incremental
- Ou seja: ambos (backfill gera historico, pipeline diario gera o dia-a-dia)

**4. Dashboard UI — Evoluir o AdminPanel existente:**

Analise do dashboard revelou que o `AdminPanel.tsx` ja tem:
- Lista de sellers pendentes com formulario de aprovacao
- Dropdowns de conta bancaria e centro de custo CA (ja populados via CA API)
- Lista de sellers ativos com edicao
- Trigger de sync

**Decisao: evoluir o AdminPanel** em vez de criar nova view. O onboarding e uma extensao natural do que ja existe. Adicionar:
- Campo "copiar link de conexao ML" (URL copiavel com botao)
- Seletor de modo (Dashboard only / Dashboard + CA)
- Date picker de mes para data de inicio (restrito ao 1o dia)
- Barra de progresso do backfill
- Botao de upgrade (dashboard_only → dashboard_ca) nos sellers ativos

Componentes existentes relevantes:
- `AdminPanel.tsx` — painel principal (evoluir)
- `useAdmin.ts` — hook de API admin (estender com novos endpoints)
- `ViewToggle.tsx` — navegacao (sem alteracao necessaria)
- `AdminPanel.module.css` — estilos (seguir padrao)

**5. Relacao seller/ML/CA — Sempre 1:1:**
1 seller = 1 conta Mercado Livre = 1 conta bancaria no Conta Azul. Sem multi-conta.

---

#### Decisoes Consolidadas (FINAL — atualizado rodada 4)

| # | Decisao | Detalhe |
|---|---------|---------|
| 1 | Modo de operacao | `dashboard_only` ou `dashboard_ca` por seller |
| 2 | Conta CA | Cada seller tem sua propria `ca_conta_bancaria` (dropdown CA API) |
| 3 | Centro de custo CA | Cada seller tem seu proprio `ca_centro_custo_variavel` (dropdown CA API) |
| 4 | Contato ML CA | Fixo para todos: `b247cccb-...` ("MERCADO LIVRE") |
| 5 | Transicao de modo | Permitida: dashboard_only → dashboard_ca a qualquer momento |
| 6 | Criterio do backfill | Por `money_release_date >= ca_start_date` (API ML suporta nativamente) |
| 7 | Competencia no backfill | `date_approved` real (pode ser anterior a data de inicio) |
| 8 | Non-orders | XLSX → Google Drive: consolidado no backfill + diario assume |
| 9 | Dashboard only | Funciona so com ML tokens + FaturamentoSyncer |
| 10 | Daily sync pos-ativacao | Mantem `date_approved` (sem mudanca) |
| 11 | Goals no onboarding | Nao sobrescrever existentes. Novas linhas: goals=0, seta depois |
| 12 | Permissao onboarding | Somente admin autenticado (X-Admin-Token) |
| 13 | Erro no backfill | Continuar de onde parou (idempotente). Dashboard mostra progresso |
| 14 | Sellers existentes | Migrar para `dashboard_only` (nenhum tem CA hoje) |
| 15 | Config CA | Dropdowns populados via CA API (endpoints existentes) |
| 16 | Upload Drive | Reaproveita `legacy_daily_export.py` (modo gdrive) |
| 17 | Link OAuth ML | Campo copiavel no Admin. Operador envia por WhatsApp |
| 18 | Data de inicio | Sempre 1o dia do mes. UX: picker de mes/ano |
| 19 | Non-orders timing | Backfill gera consolidado + pipeline diario assume |
| 20 | UI onboarding | Evolucao do AdminPanel existente (nao criar nova view) |
| 21 | Relacao seller/ML/CA | 1:1:1 (1 seller = 1 ML = 1 conta CA) |

---

#### Respostas do Eryk — Rodada 5 / FINAL (2026-02-19)

**1. Backfill e baixas — Criar na hora:**
Para payments com `money_release_date` ja passado, o backfill cria as baixas imediatamente (nao espera o scheduler). Isso garante que ao terminar o backfill, todas as parcelas vencidas ja estao liquidadas no CA.

Implicacao tecnica: apos o backfill processar cada payment (receita + comissao + frete), verificar se `money_release_date <= hoje` e, se sim, enfileirar as baixas no mesmo fluxo. O CaWorker processa tudo em sequencia (prioridade: receita=10, comissao/frete=20, baixa=30).

**2. Progresso do backfill — Polling a cada 30s:**
O AdminPanel consulta o status do backfill por polling a cada 30 segundos. Sem necessidade de Supabase realtime para isso.

Endpoint: `GET /admin/sellers/{slug}/backfill-status` → retorna `ca_backfill_progress` JSON.

**3. Notificacao — Status no AdminPanel:**
Sem notificacao externa. O AdminPanel mostra o status atualizado do seller, algo como "CA sincronizado" quando o backfill completa. O dashboard (faturamento) e somente para controle de faturamento, nao mostra status de integracao CA.

**4. Nome do seller — Editavel pelo admin:**
Sim, o admin pode editar o nome do seller durante o onboarding. O nome vem do perfil ML por padrao, mas pode ser alterado.

**5. Multiplos onboardings simultaneos — Sim:**
O admin pode ativar mais de um seller ao mesmo tempo. Backfills rodam em paralelo (cada um como background task independente).

---

#### Decisoes Consolidadas (COMPLETO — 26 decisoes)

| # | Decisao | Detalhe |
|---|---------|---------|
| 1 | Modo de operacao | `dashboard_only` ou `dashboard_ca` por seller |
| 2 | Conta CA | Cada seller tem sua propria `ca_conta_bancaria` (dropdown CA API) |
| 3 | Centro de custo CA | Cada seller tem seu proprio `ca_centro_custo_variavel` (dropdown CA API) |
| 4 | Contato ML CA | Fixo para todos: `b247cccb-...` ("MERCADO LIVRE") |
| 5 | Transicao de modo | Permitida: dashboard_only → dashboard_ca a qualquer momento |
| 6 | Criterio do backfill | Por `money_release_date >= ca_start_date` (API ML suporta nativamente) |
| 7 | Competencia no backfill | `date_approved` real (pode ser anterior a data de inicio) |
| 8 | Non-orders | XLSX → Google Drive: consolidado no backfill + diario assume |
| 9 | Dashboard only | Funciona so com ML tokens + FaturamentoSyncer |
| 10 | Daily sync pos-ativacao | Mantem `date_approved` (sem mudanca) |
| 11 | Goals no onboarding | Nao sobrescrever existentes. Novas linhas: goals=0, seta depois |
| 12 | Permissao onboarding | Somente admin autenticado (X-Admin-Token) |
| 13 | Erro no backfill | Continuar de onde parou (idempotente). AdminPanel mostra progresso |
| 14 | Sellers existentes | Migrar para `dashboard_only` (nenhum tem CA hoje) |
| 15 | Config CA | Dropdowns populados via CA API (endpoints existentes) |
| 16 | Upload Drive | Reaproveita `legacy_daily_export.py` (modo gdrive) |
| 17 | Link OAuth ML | Campo copiavel no AdminPanel. Operador envia por WhatsApp |
| 18 | Data de inicio | Sempre 1o dia do mes. UX: picker de mes/ano |
| 19 | Non-orders timing | Backfill gera consolidado + pipeline diario assume |
| 20 | UI onboarding | Evolucao do AdminPanel existente (nao criar nova view) |
| 21 | Relacao seller/ML/CA | 1:1:1 (1 seller = 1 ML = 1 conta CA) |
| 22 | Baixas no backfill | Criar imediatamente para money_release_date <= hoje |
| 23 | Polling progresso | AdminPanel faz polling a cada 30s |
| 24 | Notificacao conclusao | Status "CA sincronizado" no AdminPanel (sem notificacao externa) |
| 25 | Nome editavel | Admin pode editar nome do seller no onboarding |
| 26 | Backfills simultaneos | Permitido: multiplos backfills em paralelo |

---

### Especificacao Tecnica Final

#### 1. Migracao Supabase

```sql
-- Novos campos na tabela sellers
ALTER TABLE sellers ADD COLUMN integration_mode text NOT NULL DEFAULT 'dashboard_only';
ALTER TABLE sellers ADD COLUMN ca_start_date date;
ALTER TABLE sellers ADD COLUMN ca_backfill_status text;
ALTER TABLE sellers ADD COLUMN ca_backfill_started_at timestamptz;
ALTER TABLE sellers ADD COLUMN ca_backfill_completed_at timestamptz;
ALTER TABLE sellers ADD COLUMN ca_backfill_progress jsonb;

-- Migrar sellers existentes (todos dashboard_only, nenhum tem CA)
-- Nao precisa de UPDATE pois o DEFAULT ja e 'dashboard_only'

-- Constraint para validar integration_mode
ALTER TABLE sellers ADD CONSTRAINT chk_integration_mode
  CHECK (integration_mode IN ('dashboard_only', 'dashboard_ca'));

-- Constraint: ca_start_date obrigatorio se dashboard_ca
-- (aplicada na logica da API, nao no banco)
```

#### 2. Novos Endpoints API

```
POST /admin/sellers/{slug}/activate
  Body: {
    "integration_mode": "dashboard_only" | "dashboard_ca",
    "name": "Nome editado",               -- opcional
    "dashboard_empresa": "EMPRESA",
    "dashboard_grupo": "NETAIR",
    "dashboard_segmento": "AR CONDICIONADO",
    "ca_conta_bancaria": "uuid",           -- obrigatorio se dashboard_ca
    "ca_centro_custo_variavel": "uuid",    -- obrigatorio se dashboard_ca
    "ca_start_date": "2026-02-01"          -- obrigatorio se dashboard_ca, sempre dia 1
  }
  Efeitos:
    1. Salva config no seller
    2. Cria revenue_line (se nao existe)
    3. Cria goals=0 (somente se nao existem para essa empresa)
    4. Marca seller como active
    5. Se dashboard_ca: dispara backfill em background task
  Resposta: { "status": "ok", "backfill_triggered": true/false }

POST /admin/sellers/{slug}/upgrade-to-ca
  Body: {
    "ca_conta_bancaria": "uuid",
    "ca_centro_custo_variavel": "uuid",
    "ca_start_date": "2026-02-01"
  }
  Efeitos:
    1. Muda integration_mode para "dashboard_ca"
    2. Dispara backfill em background
  Resposta: { "status": "ok", "backfill_triggered": true }

GET /admin/sellers/{slug}/backfill-status
  Resposta: {
    "ca_backfill_status": "running",
    "ca_backfill_started_at": "2026-02-19T14:30:00Z",
    "ca_backfill_progress": {
      "total": 520,
      "processed": 450,
      "orders_processed": 380,
      "expenses_classified": 60,
      "skipped": 10,
      "errors": 5,
      "baixas_created": 350
    }
  }

POST /admin/sellers/{slug}/backfill-retry
  (Re-trigger de backfill que falhou — continua de onde parou)
  Resposta: { "status": "ok" }

GET /admin/onboarding/install-link
  Resposta: { "url": "https://conciliador.levermoney.com.br/auth/ml/install" }
  (URL copiavel para o admin enviar ao seller)
```

#### 3. Servico de Backfill de Ativacao (`app/services/onboarding_backfill.py`)

```python
async def run_onboarding_backfill(seller_slug: str):
    """Background task: backfill por money_release_date >= ca_start_date.

    1. Marca status = "running"
    2. search_payments(range_field="money_release_date",
       begin_date=ca_start_date, end_date=ontem)
    3. Para cada payment:
       - Com order_id: process_payment_webhook()
       - Sem order_id: classify_non_order_payment()
    4. Para payments com money_release_date <= hoje:
       - Enqueue baixas imediatamente
    5. Gera XLSX consolidado de non-orders → Google Drive
    6. Marca status = "completed" ou "failed"
    7. Atualiza ca_backfill_progress a cada batch de 50 payments
    """
```

#### 4. Alteracoes no AdminPanel (Dashboard React)

```
AdminPanel.tsx — evolucoes:

1. Secao "Link de Conexao":
   - URL do /auth/ml/install em campo readonly
   - Botao "Copiar" (clipboard API)

2. Formulario de ativacao (sellers pendentes):
   - Nome (editavel, pre-preenchido do ML)
   - Grupo / Segmento (combo existente)
   - Toggle: "Dashboard only" / "Dashboard + Conta Azul"
   - Se CA:
     - Picker de mes/ano (data de inicio, sempre dia 1)
     - Dropdown conta bancaria CA (ja existe)
     - Dropdown centro de custo CA (ja existe)
   - Botao "Ativar"

3. Sellers ativos — coluna de status:
   - "Dashboard only" | "CA sincronizado" | "Backfill: 85%" | "Backfill falhou"
   - Botao "Upgrade para CA" (abre form com data inicio + config CA)
   - Botao "Retry backfill" (se status = failed)

4. Polling:
   - Se algum seller tem ca_backfill_status = "running":
     setInterval(fetchBackfillStatus, 30000)
   - Para quando nenhum backfill em andamento

useAdmin.ts — novos metodos:
   - activateSeller(slug, config)
   - upgradeToCA(slug, config)
   - getBackfillStatus(slug)
   - retryBackfill(slug)
   - getInstallLink()
```

#### 5. Fluxo de Onboarding Completo (FINAL)

```
PREPARACAO:
1. Admin abre AdminPanel no dashboard
2. Copia link de conexao ML (botao "Copiar")
3. Envia link por WhatsApp para o dono da empresa

CONEXAO ML:
4. Seller clica no link → autoriza no ML
5. Callback /auth/ml/callback cria seller com status "pending_approval"
6. Seller aparece na lista de pendentes no AdminPanel

ATIVACAO (Dashboard only):
7. Admin preenche: nome, grupo, segmento
8. Seleciona modo "Dashboard only"
9. Clica "Ativar"
10. Sistema: cria revenue_line + goals, ativa seller
11. FaturamentoSyncer comeca a puxar dados imediatamente
12. Pronto!

ATIVACAO (Dashboard + CA):
7. Admin preenche: nome, grupo, segmento
8. Seleciona modo "Dashboard + Conta Azul"
9. Escolhe mes de inicio (ex: "Fevereiro 2026")
10. Seleciona conta bancaria CA (dropdown)
11. Seleciona centro de custo CA (dropdown)
12. Clica "Ativar"
13. Sistema: cria revenue_line + goals, ativa seller
14. FaturamentoSyncer comeca imediatamente
15. Backfill dispara em background:
    - Busca payments por money_release_date >= 01/02/2026
    - Processa orders (receita + comissao + frete)
    - Classifica non-orders → mp_expenses
    - Cria baixas para parcelas com release_date <= hoje
    - Gera XLSX non-orders → Google Drive
16. AdminPanel mostra progresso (polling 30s):
    "Backfill: 450/520 payments | 350 baixas | 5 erros"
17. Ao completar: status muda para "CA sincronizado"
18. Daily sync assume automaticamente

UPGRADE (Dashboard only → Dashboard + CA):
1. Admin clica "Upgrade para CA" no seller ativo
2. Escolhe mes de inicio + conta + centro de custo
3. Backfill dispara (mesma logica acima)
```

---

### Ordem de Implementacao Sugerida

| Fase | Escopo | Estimativa |
|------|--------|-----------|
| **1. Migracao DB** | ALTER TABLE sellers + migrar existentes | Simples |
| **2. Backend: endpoints** | /activate, /upgrade-to-ca, /backfill-status, /backfill-retry | Medio |
| **3. Backend: onboarding_backfill.py** | Novo servico de backfill por money_release_date + baixas | Medio-alto |
| **4. Backend: XLSX non-orders** | Consolidado no backfill + integrar com legacy drive upload | Medio |
| **5. Frontend: AdminPanel** | Link copiavel, form ativacao, toggle modo, progress bar, upgrade | Medio |
| **6. Teste e2e** | Ativar seller de teste, verificar backfill completo, validar CA | Manual |

---

*Spec completa. Pronto para implementacao.*

---

## Sessao 2 — 2026-02-19: Reconciliacao 100% com Extrato MP

### Analise dos Extratos Reais (Janeiro 2026, 4 sellers)

Analisei os 4 extratos brutos + arquivos `extrato_unmatched.csv` de cada seller. Esses unmatched sao as linhas do extrato que o sistema NAO conseguiu parear com nenhum payment da API.

#### Taxonomia Completa de Linhas do Extrato

Cruzando todos os sellers, estas sao TODAS as categorias de linhas que aparecem no extrato:

| Categoria no extrato | Descricao | Sellers afetados | Coberto pelo sistema? |
|---|---|---|---|
| **Liberacao de dinheiro** | Venda liquidada (net credit) | Todos | SIM — processor + baixas |
| **Transferencia Pix enviada** | Payout para banco/terceiros | Todos | PARCIAL — mp_expenses (transfer_pix) |
| **Pagamento de conta** (Itau, etc) | Boleto pago via MP | 141air | SIM — mp_expenses (bill_payment) |
| **Pagamento com QR Pix** | Pag via QR code (YAPAY, Receita Federal, INPI, etc) | Todos | PARCIAL — mp_expenses mas nem sempre capturado |
| **Debito por divida — DIFAL** | Diferenca de aliquota ICMS | net-air (massivo) | NAO — gap principal |
| **Debito por divida — Faturas vencidas ML** | Cobranca de fatura ML | easy, netparts | NAO — gap |
| **Debito por divida — Reclamacoes no ML** | Chargeback/disputa debitado | 141air, net-air | PARCIAL — processor trata charged_back, mas debito direto nao |
| **Debito por divida — Envio do ML** | Cobranca de frete retroativo | netparts, net-air | NAO — gap |
| **Dinheiro retido — Reclamacoes** | Reserve for dispute | 141air, netparts, net-air | NAO — gap |
| **Reembolso — Reclamacoes e devolucoes** | ML devolveu $ ao seller apos disputa | Todos | NAO — gap (confunde com refund mas e diferente) |
| **Reembolso** (generico, valores pequenos) | Ajuste de centavos pelo ML | netparts, easy, net-air | NAO — gap |
| **Entrada de dinheiro** | Credito avulso (ex: reembolso parcial de disputa) | 141air, net-air | NAO — gap |
| **Dinheiro recebido** | Deposito/aporte | 141air | NAO — gap |
| **Liberacao de dinheiro cancelada** | Venda cancelada apos liberacao | net-air | NAO — gap |
| **Pix enviado** (sem "Transferencia") | Variante de pix | 141air | PARCIAL |

#### Diagnostico por Seller

**141AIR** (34 linhas unmatched):
- Dominado por **transferencias PIX** (payouts para pessoas fisicas/juridicas)
- Pagamentos QR (Receita Federal, YAPAY, INPI)
- 3 debitos DIFAL
- 3 debitos "dinheiro retido"
- **Unmatched total: R$ -56.399,66**

**NET-AIR** (131 linhas unmatched — O MAIS COMPLEXO):
- **DIFAL massivo** (centenas de linhas de "Diferenca da aliquota")
- Transferencias PIX (payouts)
- Liberacoes de dinheiro avulsas (fora do fluxo normal)
- Debitos de envio do ML
- Reembolsos de disputas
- Liberacao cancelada
- Pagamentos QR (YAPAY, Facebook)
- **Unmatched total: R$ -282.837,89** (DIFAL e o maior componente)

**NETPARTS** (131 linhas unmatched):
- **Reembolsos de disputas** (muitos, valores altos)
- **Dinheiro retido** (reservas de disputa)
- Transferencias PIX (payouts)
- Debitos "Faturas vencidas ML"
- Debitos "Envio do ML"
- **Unmatched total: R$ -52.124,45**

**EASY-UTILIDADES** (60 linhas unmatched):
- Transferencias PIX (payouts)
- **Debitos "Faturas vencidas ML"** (muitos)
- **Reembolsos de disputas**
- **Dinheiro retido** (reservas)
- Pagamento QR (Ministerio da Fazenda, Safe2Pay)
- **Unmatched total: R$ -33.472,10**

#### Os 8 Gaps Reais para Fechar 100%

| # | Tipo | Descricao | Frequencia | Impacto |
|---|------|-----------|-----------|---------|
| 1 | **DIFAL** | Diferenca de aliquota ICMS. ML cobra automaticamente. | Alto (net-air) | R$ dezenas de milhares/mes |
| 2 | **Faturas vencidas ML** | ML cobra faturas em atraso (frete, comissao) direto na conta | Medio | R$ centenas a milhares |
| 3 | **Reembolso de disputa** | ML devolve $ ao seller apos resolver disputa a favor | Alto (netparts) | R$ milhares |
| 4 | **Dinheiro retido** | Reserva bloqueada por disputa | Medio | R$ centenas |
| 5 | **Entrada de dinheiro** | Credito avulso (complemento de reembolso) | Baixo | R$ dezenas |
| 6 | **Debito envio ML** | Cobranca retroativa de frete | Baixo | R$ dezenas a centenas |
| 7 | **Liberacao cancelada** | Venda liberada e depois cancelada | Raro | R$ dezenas |
| 8 | **Reembolso generico** | Ajuste de centavos (arredondamento) | Baixo | R$ 0,10 a R$ 3,00 |

#### Conclusao

Os "matched" (liberacao de dinheiro = vendas) ja batem 100% (Validation_Error = 0.0 em todos os dias da `caixa_diaria_v2.csv`). O problema e que o extrato TEM MAIS LINHAS alem das liberacoes de vendas, e essas linhas extras nao estao cobertas.

A boa noticia: todos esses 8 tipos sao **deterministicos e parseveis** a partir do texto do extrato. Cada linha tem um `REFERENCE_ID` e um `TRANSACTION_TYPE` textual que pode ser classificado.

---

### Diagnostico Raiz do Problema

O problema fundamental: **o sistema so ingere dados da Payments API** (`/v1/payments/search`). Porem, o extrato (account_statement) contem linhas que **NAO sao payments** — sao movimentacoes diretas da conta MP (debitos fiscais, reservas de disputa, reembolsos, etc.). A Payments API simplesmente nao retorna esses registros.

Mapa visual do gap:

```
Payments API (/v1/payments/search)
  ├── Vendas (approved)           → processor.py → CA    ✓ COBERTO
  ├── Refunds (refunded)          → processor.py → CA    ✓ COBERTO
  ├── Chargebacks                 → processor.py → CA    ✓ COBERTO
  └── Non-order (boleto, PIX...)  → expense_classifier   ✓ COBERTO

Account Statement (extrato)
  ├── Liberacao de dinheiro       → matched via payment_id ✓ COBERTO
  ├── Transferencia PIX           → matched via payment_id ✓ COBERTO (mp_expenses)
  ├── Pagamento de conta          → matched via payment_id ✓ COBERTO (mp_expenses)
  │
  ├── DIFAL                       → NAO existe na Payments API  ✗ GAP
  ├── Faturas vencidas ML         → NAO existe na Payments API  ✗ GAP
  ├── Reembolso de disputa        → NAO existe na Payments API  ✗ GAP
  ├── Dinheiro retido             → NAO existe na Payments API  ✗ GAP
  ├── Entrada de dinheiro         → NAO existe na Payments API  ✗ GAP
  ├── Debito envio ML             → NAO existe na Payments API  ✗ GAP
  ├── Liberacao cancelada         → NAO existe na Payments API  ✗ GAP
  └── Reembolso generico          → NAO existe na Payments API  ✗ GAP
```

A unica fonte desses 8 tipos e o **proprio extrato CSV** (account_statement).

---

### Solucao Proposta: Extrato Line Ingester

#### Conceito

Novo servico `extrato_ingester.py` que:
1. Baixa o account_statement (release_report) diariamente para cada seller
2. Parseia cada linha do extrato
3. Cruza `REFERENCE_ID` com `payments` e `mp_expenses`
4. Linhas SEM match → classifica por `TRANSACTION_TYPE` → insere em `mp_expenses`
5. Resultado: **100% das linhas do extrato mapeadas** no sistema

O ingester roda como parte do nightly pipeline, **APOS** o daily sync (que cuida dos payments) e **ANTES** do coverage check (que valida).

#### Pipeline Atualizado

```
Nightly Pipeline (sequencial):
  1. sync_all_sellers()                  → Payments API → payments + mp_expenses
  2. validate_release_fees_all_sellers() → Valida fees vs release report
  3. ingest_extrato_all_sellers() ← NOVO → Account statement → mp_expenses (gaps)
  4. _run_baixas_all_sellers()           → Baixas
  5. run_legacy_daily_for_all()          → Legacy export
  6. check_extrato_coverage_all_sellers()→ Deve ser 100% agora
  7. _run_financial_closing()            → Fechamento
```

#### Classificacao dos 8 Tipos de Linha

Todos vao para `mp_expenses` via XLSX export (mesmo fluxo que non-orders). O financeiro controla a categorizacao final e importacao no CA.

| # | Tipo Extrato | expense_type | direction | auto_cat | ca_category | Descricao XLSX |
|---|-------------|-------------|-----------|----------|-------------|----------------|
| 1 | DIFAL | `difal` | expense | true | `2.2.7` (Impostos) | "DIFAL ICMS - Ref {ref_id}" |
| 2 | Faturas vencidas ML | `faturas_ml` | expense | true | `2.8.2` (Comissoes) | "Fatura Vencida ML - Ref {ref_id}" |
| 3 | Reembolso disputa | `reembolso_disputa` | income | true | `1.3.4` (Estornos) | "Reembolso Disputa ML - Ref {ref_id}" |
| 4 | Dinheiro retido | `dinheiro_retido` | expense | true | (nenhuma*) | "Reserva Disputa ML - Ref {ref_id}" |
| 5 | Entrada de dinheiro | `entrada_dinheiro` | income | true | (nenhuma*) | "Credito Avulso ML - Ref {ref_id}" |
| 6 | Debito envio ML | `debito_envio_ml` | expense | true | `2.9.4` (Frete) | "Debito Envio ML - Ref {ref_id}" |
| 7 | Liberacao cancelada | `liberacao_cancelada` | expense | true | (nenhuma*) | "Liberacao Cancelada - Ref {ref_id}" |
| 8 | Reembolso generico | `reembolso_generico` | income | true | `1.3.4` (Estornos) | "Reembolso ML - Ref {ref_id}" |

*`(nenhuma)` = pendente de categorizacao manual pelo financeiro. Status: `pending_review`.

**Notas sobre cada tipo:**

1. **DIFAL** — Imposto real. Despesa tributaria. Na net-air e massivo (centenas de linhas/mes, dezenas de milhares R$). Auto-categorizado como imposto.

2. **Faturas vencidas ML** — ML cobrando comissoes/fretes em atraso. Despesa operacional. Aparece quando o saldo nao era suficiente no dia da cobranca original.

3. **Reembolso de disputa** — Dinheiro que VOLTA ao seller apos ML resolver disputa. E income (receita). Diferente de "refund" (que e devolucao ao comprador). Os `REFERENCE_ID` frequentemente sao IDs curtos (claim/case IDs), nao payment IDs.

4. **Dinheiro retido** — Reserva temporaria bloqueada por disputa. Afeta caixa (saldo diminui), resolve quando disputa fecha (pode virar "reembolso de disputa" ou "debito por divida"). Tipo: expense (saida de caixa). O par retencao → resolucao se equilibra ao longo do tempo.

5. **Entrada de dinheiro** — Credito avulso, geralmente complemento de reembolso de disputa. Valores pequenos. Income.

6. **Debito envio ML** — Cobranca retroativa de frete nao cobrado na venda original. Despesa logistica. Raro.

7. **Liberacao cancelada** — ML liberou dinheiro e depois cancelou (venda fraudulenta detectada depois). Ajuste negativo ao caixa. Raro.

8. **Reembolso generico** — Centavos de arredondamento. Income minimo (R$ 0,10~3,00).

#### Estrutura Tecnica

##### Novo servico: `app/services/extrato_ingester.py`

```python
# Assinaturas principais

def _parse_account_statement(csv_bytes: bytes) -> tuple[dict, list[dict]]:
    """Parse account_statement CSV (format: RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;AMOUNT;BALANCE).
    Returns (summary_dict, list_of_transactions)."""

def _classify_extrato_line(tx_type: str) -> str:
    """Classifica TRANSACTION_TYPE textual em expense_type.
    Reutiliza a logica do reconciliation_jan2026_v2.py (classify_extrato_line)."""

def _build_expense_from_extrato_line(tx: dict, seller_slug: str) -> dict:
    """Constroi mp_expenses row a partir de uma linha do extrato nao-matched.
    Mapeia: tx_type → expense_type, direction, ca_category, description."""

async def ingest_extrato_for_seller(seller_slug: str, begin_date: str, end_date: str) -> dict:
    """Pipeline principal:
    1. Baixa account_statement via ml_api (release_report endpoints)
    2. Parseia todas as linhas
    3. Batch-lookup: quais REFERENCE_IDs ja existem em payments + mp_expenses?
    4. Para linhas nao-cobertas: classifica e insere em mp_expenses
    5. Retorna stats {total_lines, already_covered, newly_ingested, by_type}
    """

async def ingest_extrato_all_sellers(lookback_days: int = 3) -> list[dict]:
    """Roda ingestao para todos sellers ativos (D-1 a D-{lookback_days})."""

def get_last_ingestion_result() -> dict:
    """Retorna resultado da ultima ingestao (in-memory)."""
```

##### Alteracoes em arquivos existentes

**`app/services/expense_classifier.py`** — Sem alteracao. O ingester NAO usa o classifier. O classifier so funciona para payments da API (tem `operation_type`, `point_of_interaction`, etc.). Linhas do extrato nao tem esses campos.

**`app/services/extrato_coverage_checker.py`** — Duas alteracoes:
1. Remover `tax_withheld_at_source` de `INTERNAL_DESCRIPTIONS` (DIFAL e real, nao pode ser ignorado)
2. Apos o ingester rodar, o coverage check deve encontrar 100%

**`app/main.py`** — Adicionar `ingest_extrato_all_sellers` ao nightly pipeline (passo 3).

**`app/routers/admin.py`** — Novos endpoints:
- `POST /admin/extrato/ingest/{seller}?begin_date=...&end_date=...` — trigger manual
- `POST /admin/extrato/ingest-all?lookback_days=3` — trigger todos sellers
- `GET /admin/extrato/ingestion-status` — resultado da ultima ingestao

**`mp_expenses` tabela** — Sem alteracao de schema. Os novos `expense_type` values (`difal`, `faturas_ml`, `reembolso_disputa`, etc.) sao apenas novos valores no campo text existente. Adicionar constraint CHECK se quiser:

```sql
-- Opcional: atualizar ENUM de expense_type para incluir novos tipos
-- (nao obrigatorio pois o campo e text, nao enum)
COMMENT ON COLUMN mp_expenses.expense_type IS
  'bill_payment|subscription|darf|cashback|collection|transfer_pix|transfer_intra|deposit|savings_pot|other|'
  'difal|faturas_ml|reembolso_disputa|dinheiro_retido|entrada_dinheiro|debito_envio_ml|liberacao_cancelada|reembolso_generico';
```

**Novo campo sugerido em mp_expenses** (opcional):

```sql
ALTER TABLE mp_expenses ADD COLUMN source text DEFAULT 'payments_api';
-- Valores: 'payments_api' (classifier) | 'extrato' (ingester)
```

Isso permite diferenciar de onde veio cada mp_expense: da API de payments ou do extrato.

##### Logica de Parse do Extrato

O account_statement tem formato:

```
INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
4.476,23;207.185,69;-210.571,52;1.090,40

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-01-2026;Liberacao de dinheiro ;138199281600;3.994,84;5.771,27
01-01-2026;Debito por divida Diferenca da aliquota (DIFAL);2728587235;-20,36;4.459,87
```

Regras de classificacao (baseadas no `TRANSACTION_TYPE` textual):

```python
EXTRATO_CLASSIFICATION_RULES = [
    # pattern (case-insensitive)         → expense_type          → direction → ca_category
    ("liberação de dinheiro cancelada",   "liberacao_cancelada",   "expense",  None),
    ("liberação de dinheiro",             None,                    None,       None),  # SKIP (coberto por processor)
    ("reembolso reclamações",             "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso envío cancelado",         "reembolso_disputa",     "income",   "1.3.4"),
    ("reembolso",                         "reembolso_generico",    "income",   "1.3.4"),
    ("dinheiro retido",                   "dinheiro_retido",       "expense",  None),
    ("diferença da aliquota",             "difal",                 "expense",  "2.2.7"),
    ("difal",                             "difal",                 "expense",  "2.2.7"),
    ("faturas vencidas",                  "faturas_ml",            "expense",  "2.8.2"),
    ("envio do mercado livre",            "debito_envio_ml",       "expense",  "2.9.4"),
    ("reclamações no mercado livre",      "debito_divida_disputa", "expense",  None),
    ("transferência pix",                 None,                    None,       None),  # SKIP (coberto por mp_expenses via API)
    ("pix enviado",                       None,                    None,       None),  # SKIP
    ("pagamento de conta",                None,                    None,       None),  # SKIP (coberto por mp_expenses via API)
    ("pagamento com",                     None,                    None,       None),  # SKIP (coberto por mp_expenses via API)
    ("entrada de dinheiro",               "entrada_dinheiro",      "income",   None),
    ("dinheiro recebido",                 "deposito_avulso",       "income",   None),
]
```

Logica: para cada linha do extrato, primeiro tenta match por `REFERENCE_ID` nas tabelas `payments` + `mp_expenses`. Se nao encontra, aplica as regras acima no `TRANSACTION_TYPE`. Se `expense_type` resultante e `None` → skip (ja coberto).

##### Idempotencia

Key de upsert: `(seller_slug, payment_id)` onde `payment_id = REFERENCE_ID` do extrato.

Problema: um mesmo `REFERENCE_ID` pode ter MULTIPLAS linhas no extrato (ex: disputa que gera debito + reembolso + entrada). Nesse caso, criar registros separados em mp_expenses com composite key:

```
idempotency: (seller_slug, reference_id, expense_type, date)
```

Ou simplesmente usar `REFERENCE_ID + tipo` como `payment_id` no mp_expenses:
```
payment_id = f"{reference_id}:{expense_type_abbreviation}"
```

Exemplo real do extrato net-air (REFERENCE_ID 135321847364):
```
08-01-2026;Debito por divida Reclamacoes no ML;135321847364;-193,03   → debito_divida_disputa
08-01-2026;Entrada de dinheiro;135321847364;29,84                      → entrada_dinheiro
08-01-2026;Reembolso Reclamacoes e devolucoes;135321847364;140,74      → reembolso_disputa
```

Essas 3 linhas formam um "grupo de disputa" (debito = -193.03, entrada = +29.84, reembolso = +140.74, liquido = -22.45). Cada uma vira uma entrada separada em mp_expenses.

#### Impacto Esperado nos Numeros

Tomando como base janeiro 2026:

| Seller | Linhas Unmatched Hoje | Apos Ingester |
|--------|----------------------|---------------|
| 141AIR | 34 | 0 (todos classificados) |
| NET-AIR | 345+ | 0 (DIFAL massivo + transfers + disputas) |
| NETPARTS | 131 | 0 |
| EASY | 60 | 0 |

**Coverage esperado: 100%** para todos os sellers.

O total financeiro dessas linhas (R$ ~425k entre todos os sellers) passa a ser rastreado e exportavel via XLSX, dando ao financeiro visibilidade completa.

#### Ordem de Implementacao

| Fase | Escopo | Complexidade |
|------|--------|-------------|
| **1. extrato_ingester.py** | Novo servico: parse + classify + upsert | Media |
| **2. Integrar no nightly pipeline** | Adicionar passo 3 no main.py | Simples |
| **3. Endpoints admin** | POST ingest/{seller}, ingest-all, GET status | Simples |
| **4. Corrigir coverage_checker** | Remover tax_withheld_at_source de INTERNAL | Simples |
| **5. XLSX export** | Verificar que novos expense_types aparecem no export | Simples |
| **6. Teste com dados reais** | Rodar com extratos de janeiro, validar 100% | Manual |

#### Perguntas Pendentes para o Eryk

1. **Todos os 8 tipos devem ir pro XLSX?** Minha sugestao: sim, todos. O financeiro decide o que importar no CA. Mesmo "dinheiro retido" (temporario) afeta caixa e precisa ser rastreado.

2. **DIFAL — qual categoria CA?** Sugeri `2.2.7 Impostos`. Se o financeiro usa outra, facil mudar.

3. **Reembolsos de disputa = income no XLSX?** Sugeri sim — e dinheiro voltando pro seller. O financeiro categoriza como `1.3.4 Estornos de Taxas` ou cria categoria propria.

4. **Tudo via XLSX (mesmo pipeline mp_expenses)?** Sugeri sim — consistente com arquitetura existente. Nenhum tipo vira lancamento direto no CA, todos passam pelo XLSX → financeiro → CA.
