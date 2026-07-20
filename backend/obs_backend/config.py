from typing import Literal, TypedDict

EnvName = Literal["dev", "qa", "prod"]


class EnvConfig(TypedDict):
    project_id: str
    namespaces: list[str]
    app_namespace: str
    langfuse_url: str
    rag_base_url: str


# Mapeo entorno -> proyecto de la app. PROD se habilita en Fase 3; el mapeo ya
# existe para que el switch del front no necesite cambios después.
ENVIRONMENTS: dict[EnvName, EnvConfig] = {
    "dev": {
        "project_id": "itmind-macro-ai-dev-0",
        "namespaces": ["enterprise-ai", "langfuse", "airflow"],
        "app_namespace": "enterprise-ai",
        "langfuse_url": "http://langfuse-dev.macro.com.ar",
        "rag_base_url": "https://ia-dev.macro.com.ar",
    },
    "qa": {
        "project_id": "itmind-macro-ai-qa-0",
        "namespaces": ["enterprise-ai", "langfuse", "airflow"],
        "app_namespace": "enterprise-ai",
        "langfuse_url": "http://langfuse-qa.macro.com.ar",
        "rag_base_url": "https://ia-qa.macro.com.ar",
    },
    "prod": {
        "project_id": "itmind-macro-ai-prod-0",
        "namespaces": ["enterprise-ai", "langfuse", "airflow"],
        "app_namespace": "enterprise-ai",
        "langfuse_url": "http://langfuse-prod.macro.com.ar",
        "rag_base_url": "https://ia.macro.com.ar",
    },
}


def project_for(env: EnvName) -> str:
    return ENVIRONMENTS[env]["project_id"]  # KeyError si env desconocido
