from typing import Literal, TypedDict

EnvName = Literal["qa", "prod"]


class EnvConfig(TypedDict):
    project_id: str
    namespaces: list[str]
    app_namespace: str


# Mapeo entorno -> proyecto de la app. PROD se habilita en Fase 3; el mapeo ya
# existe para que el switch del front no necesite cambios después.
ENVIRONMENTS: dict[EnvName, EnvConfig] = {
    "qa": {
        "project_id": "itmind-macro-ai-qa-0",
        "namespaces": ["enterprise-ai", "langfuse", "airflow"],
        "app_namespace": "enterprise-ai",
    },
    "prod": {
        "project_id": "itmind-macro-ai-prod-0",
        "namespaces": ["enterprise-ai", "langfuse", "airflow"],
        "app_namespace": "enterprise-ai",
    },
}


def project_for(env: EnvName) -> str:
    return ENVIRONMENTS[env]["project_id"]  # KeyError si env desconocido
