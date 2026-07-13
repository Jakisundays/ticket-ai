"""
Limiter compartido de slowapi. Vive en su propio módulo -- ni en app_factory.py
ni en un router -- para que app_factory.py (que registra el exception handler
en el FastAPI app) y los routers que lo usan (@limiter.limit(...)) importen el
mismo objeto sin import circular.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
