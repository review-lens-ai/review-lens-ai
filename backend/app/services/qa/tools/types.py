"""The shapes the tool loop and the tools agree on (S2-010).

    ToolResult{text, sources[], is_error}
    Source{label, review_id, detail}

- text is what the model reads. sources are the reviews behind it.
- The model cites labels ("R1"), never database ids. The answer builder maps
  cited labels back to sources, and a label no tool returned is a made-up
  citation (S2-BR-010).
- A tool never raises to the loop: a failure is a ToolResult with is_error set,
  so the model can read it and try again.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class Source(BaseModel):
    label: str  # "R1", "R2", ... valid for one question only
    review_id: UUID
    detail: str  # short line for the UI chip, e.g. "4 stars, 12 Mar 2026"


class ToolResult(BaseModel):
    text: str
    sources: list[Source] = Field(default_factory=list)
    is_error: bool = False
