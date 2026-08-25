import logging

from obs_backend.api import create_app


def _configure_logging() -> None:
    """Emite los logs propios de la app con prefijo de nivel (igual que uvicorn).

    Sin esto los logs de `obs_backend.*` caen al lastResort de Python (mensaje pelado
    a stderr, sin nivel) y Cloud Logging los clasifica ERROR por venir de stderr — la
    misma misclasificación stderr→ERROR que C busca reducir. El prefijo `LEVEL:` deja
    que Cloud Logging derive la severidad real (un WARNING queda WARNING, no ERROR).

    Se deja `propagate` en su valor por defecto (True): uvicorn no agrega handler al
    root, así que con este handler propio no hay doble emisión, y propagar mantiene
    funcionando a `caplog` en los tests. Asignar `handlers=[...]` es idempotente.
    """
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    pkg_log = logging.getLogger("obs_backend")
    pkg_log.setLevel(logging.INFO)
    pkg_log.handlers = [handler]


_configure_logging()
app = create_app()
