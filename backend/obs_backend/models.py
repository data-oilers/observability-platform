from typing import Literal

from pydantic import BaseModel

Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEvent(BaseModel):
    ts: str
    severity: Severity = "INFO"
    pod: str = ""
    message: str = ""
    path: str | None = None
    status: int | None = None
    duration_ms: float | None = None
    request_id: str | None = None
    user_id: str | None = None


class Trace(BaseModel):
    id: str
    name: str = ""
    ts: str = ""
    latency_ms: float | None = None
    total_tokens: int | None = None
    faithfulness: float | None = None
    user_id: str | None = None


class HealthSummary(BaseModel):
    status: Literal["ok", "warn", "crit"]
    reasons: list[str]
    errors: int
    gemini_429: int


class NodeStat(BaseModel):
    name: str
    cpu_pct: float | None = None   # 0..100 (allocatable utilization * 100)
    mem_pct: float | None = None   # 0..100


class PodStat(BaseModel):
    name: str
    namespace: str = ""
    cpu_cores: float | None = None  # cores
    mem_bytes: float | None = None
    restarts: int | None = None


class InfraSnapshot(BaseModel):
    nodes: list[NodeStat]
    pods: list[PodStat]
    node_count: int
    pod_count: int


class PodIssue(BaseModel):
    namespace: str
    pod: str
    problem: str   # CrashLoopBackOff | ImagePullBackOff | ErrImagePull | Unschedulable | Pending | Failed | Unknown | …
    detail: str = ""


class ReplicaShortfall(BaseModel):
    kind: str      # Deployment | StatefulSet
    name: str
    namespace: str
    desired: int
    available: int


class PvcIssue(BaseModel):
    namespace: str
    name: str
    phase: str     # Pending | Lost


class WorkloadHealth(BaseModel):
    pod_issues: list[PodIssue]
    replica_shortfalls: list[ReplicaShortfall]
    pvc_issues: list[PvcIssue]
    errors: list[str] = []


class LatencyStat(BaseModel):
    label: str
    sample_count: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    max_ms: float | None = None


class LatencySummary(BaseModel):
    stats: list[LatencyStat]


class K8sEvent(BaseModel):
    ts: str
    type: str           # "Warning"
    reason: str = ""
    kind: str = ""      # involvedObject.kind
    name: str = ""      # involvedObject.name
    namespace: str = ""
    message: str = ""
    count: int | None = None


class RagNodeStat(BaseModel):
    node: str
    calls: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    errors: int = 0
    total_tokens: int | None = None
