import logging


def test_obs_backend_logs_llevan_prefijo_de_nivel(capsys):
    """El WARNING de la app debe salir a stderr con prefijo `WARNING:` para que Cloud
    Logging lo clasifique WARNING (no ERROR por venir de stderr sin nivel). C-F1."""
    from obs_backend.main import _configure_logging

    _configure_logging()  # dentro del test: el StreamHandler toma el stderr capturado
    logging.getLogger("obs_backend.sources.langfuse").warning(
        "langfuse inalcanzable: http://x (ConnectError)"
    )

    err = capsys.readouterr().err
    assert err.startswith("WARNING:"), f"esperaba prefijo de nivel, salió: {err!r}"
    assert "inalcanzable" in err
