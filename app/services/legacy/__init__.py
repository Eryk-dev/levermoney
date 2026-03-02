"""Legacy reconciliation subpackage.

Re-exports public API so existing imports continue to work unchanged:
    from app.services.legacy_bridge import run_legacy_reconciliation
    from app.services.legacy_daily_export import run_legacy_daily_for_all
"""
from .engine import (  # noqa: F401
    processar_conciliacao,
    gerar_xlsx_completo,
    gerar_xlsx_resumo,
    gerar_csv_conta_azul,
    gerar_ofx_mercadopago,
    is_zip_file,
    extrair_csvs_do_zip,
)
from .bridge import (  # noqa: F401
    run_legacy_reconciliation,
    build_legacy_expenses_zip,
)
from .daily_export import (  # noqa: F401
    run_legacy_daily_for_seller,
    run_legacy_daily_for_all,
    get_legacy_daily_status,
    _legacy_daily_scheduler,
)
