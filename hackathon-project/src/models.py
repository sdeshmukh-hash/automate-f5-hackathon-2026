"""Data models for the Orchestrator Agent (`run_traffic`) and Verifier Agent (`verify_metric`)."""

import copy
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:
    def Field(default: Any = None, default_factory: Any = None, **kwargs: Any) -> Any:  # type: ignore
        if default_factory is not None:
            return default_factory
        return default

    class BaseModel:  # type: ignore
        """Standard-library model fallback when pydantic is not installed."""

        def __init__(self, **kwargs: Any) -> None:
            annotations = {}
            for cls in reversed(self.__class__.__mro__):
                annotations.update(getattr(cls, "__annotations__", {}))
            for name in annotations:
                if name in kwargs:
                    val = kwargs[name]
                elif hasattr(self.__class__, name):
                    attr = getattr(self.__class__, name)
                    val = attr() if callable(attr) else copy.deepcopy(attr)
                else:
                    val = None
                setattr(self, name, val)

        def model_dump(self) -> Dict[str, Any]:
            def _conv(obj: Any) -> Any:
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                if isinstance(obj, list):
                    return [_conv(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _conv(v) for k, v in obj.items()}
                return obj

            return {k: _conv(v) for k, v in self.__dict__.items()}

        def model_copy(self, deep: bool = False) -> Any:
            return copy.deepcopy(self) if deep else copy.copy(self)


class AgentMessage(BaseModel):
    """Structured execution trace step emitted by an agent or skill."""

    agent_id: str = Field(default="orchestrator", description="orchestrator, skill_run_traffic, or verifier_agent_1")
    agent_name: str
    role_badge: str
    timestamp: str
    headline: str
    bullets: List[str] = Field(default_factory=list)
    artifact_summary: Optional[str] = None
