from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    model: str | None = None


class WikiPage(BaseModel):
    name: str
    title: str
    summary: str
    tags: list[str] = []


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


class ToolCall(BaseModel):
    tool: str
    args: dict


class ToolResult(BaseModel):
    tool: str
    result: str
