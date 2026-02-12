# Plano de Meta Diaria Dinamica

## Objetivo
Substituir a meta diaria fixa por uma meta adaptativa, que use historico real para distribuir a meta mensal com maior aderencia ao comportamento de venda.

## Estrategia implementada (Fase 1)
1. Manter a meta mensal como restricao principal.
2. Treinar um baseline preditivo com historico diario:
   - sazonalidade por dia da semana/mes/posicao no mes;
   - tendencia por EWMA com robustez a outliers.
3. Aprender multiplicadores de calendario por residual historico:
   - fim de semana (capturado na sazonalidade);
   - feriado/pre-feriado/pos-feriado (feriados nacionais + moveis);
   - janela de pagamento (4-7, 18-22, fim do mes).
4. Gerar um score de potencial por dia e distribuir a meta mensal pelo score.
5. Rebalancear dinamicamente os dias futuros com base no gap planejado vs realizado no mes (rolling).
6. Expor uma API unica de metas dinamicas para cards e graficos.
7. Permitir ligar/desligar o rebalanceamento de gap (modo dinamico vs fixo).

## Onde foi aplicado no codigo
- `src/utils/businessCalendar.ts`
  - calendario de negocio (feriados e buckets de pagamento).
- `src/utils/goalCalculator.ts`
  - `buildAdaptiveDailyGoalPlanner(...)` com modelo adaptativo.
- `src/hooks/useFilters.ts`
  - `goalMetrics`, `dailyData.goal` e `getGoalForDate` agora usam o planner dinamico.
  - detalhamento por empresa tambem usa planner dinamico por linha.
  - preset rapido `Hoje` e referencia dinamica Hoje/Ontem para metas.
- `src/App.tsx`
  - toggle visual de modo de gap (`Gap dinamico` / `Gap fixo`).
  - painel diario dedicado por empresa no preset `Hoje`.

## Garantias atuais
- Soma das metas diarias do mes continua fechando na meta mensal.
- Meta diaria, semanal e proporcional passam a refletir comportamento historico.
- Fora do mes de referencia, o sistema cai em fallback para regra tradicional ajustada.

## Evolucao para ML mais forte (Fase 2)
1. Persistir dataset de features diario (calendar + contexto comercial).
2. Treinar LightGBM/XGBoost para prever potencial diario por entidade.
3. Reaplicar o mesmo reconciliador de metas (normalizacao para meta mensal).
4. Servir inferencia por API (batch diario) e cache local no dashboard.

## Evolucao para operacao em producao (Fase 3)
1. Rotina automatica de retreino (mensal/semanal).
2. Monitoramento de drift e erro por entidade.
3. Fallback automatico para modelo heuristico quando confianca cair.
