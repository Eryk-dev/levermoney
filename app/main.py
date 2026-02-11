"""
API Conciliador V2 - Sincronização ML/MP <-> Conta Azul
"""
import logging
from fastapi import FastAPI
from app.routers import health, webhooks, auth_ml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="API Conciliador V2",
    description="Sincronização automática ML/MP → Conta Azul",
    version="2.0.0",
)

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(auth_ml.router)
