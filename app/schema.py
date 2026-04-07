from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    model: str | None = None


class WikiPage(BaseModel):
    name: str
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    title: str
    type: str = "topic"


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class LintIssue(BaseModel):
    severity: str
    code: str
    message: str
    page: str | None = None
    line: int | None = None


class LintReport(BaseModel):
    ok: bool
    summary: str
    issues: list[LintIssue] = Field(default_factory=list)


class IngestRequest(BaseModel):
    model: str | None = None


class ToolCall(BaseModel):
    tool: str
    args: dict


class ToolResult(BaseModel):
    tool: str
    result: str
