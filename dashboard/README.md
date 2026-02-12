# Faturamento Dashboard

Dashboard de acompanhamento de faturamento, metas e projeções por **Linha de Receita** (antigo “Empresa”), **Grupo** e **Segmento**. Inclui PWA com instalação e uso offline parcial.

## Principais funcionalidades
- KPIs diários, semanais, mensais e anuais (com referência D‑1 para esperado).
- Gráficos de ritmo (mensal/acumulado), faturamento diário e contribuição por grupo.
- Filtros por **Linha**, **Grupo** e **Segmento**.
- Gestão de **Linhas de Receita** (tela “Linhas”) com inclusão/remoção automática em filtros e metas.
- PWA instalável (ícone customizado e manifesto configurado).

## Mobile (UX)
- Filtros abrem em **sheet** com checkboxes e botão **Aplicar**.
- “Todos” é limpo automaticamente ao selecionar itens.
- Tooltips de gráfico desativados no mobile para evitar cliques acidentais durante scroll.

## Rodando localmente
```bash
npm install
npm run dev
```

## Build
```bash
npm run build
```

## PWA / Instalação
- O botão **Instalar** aparece em produção (HTTPS) quando o navegador dispara `beforeinstallprompt`.
- No iOS: usar **Compartilhar → Adicionar à Tela de Início**.
- Em **dev**, o Service Worker é desativado para evitar cache “travado”.

## Dados (Supabase)
- Tabela: `faturamento`
- Campos usados: `empresa`, `data` (YYYY‑MM‑DD), `valor`

As credenciais podem ser configuradas via:
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`

## Linhas de Receita
- A lista de linhas é carregada do **Supabase** (tabela `revenue_lines`).
- Edições persistem via API admin e são sincronizadas em tempo real.
- Ao adicionar uma linha, cria meta anual com 12 meses = 0.
- Ao remover, ela é desativada no banco (dados históricos continuam).

## Fonte única de metas
Toda a lógica de metas e esperado está documentada em:
- `REFATORACAO_METAS_E_PWA.md`

Leia antes de alterar cálculos.
