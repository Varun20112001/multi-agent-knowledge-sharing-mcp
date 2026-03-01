from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Citation(BaseModel):
    file_path: str
    line_start: int = Field(ge=1)
    line_end: int | None = Field(default=None, ge=1)
    quote: str | None = None

    @model_validator(mode="after")
    def validate_line_range(self) -> "Citation":
        if self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class CitationInput(BaseModel):
    file_path: str | None = None
    url: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    quote: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def validate_line_range(self) -> "CitationInput":
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be >= line_start")
        return self


class IngestRepoRequest(BaseModel):
    project_id: UUID
    repo_path: str
    include_globs: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude_globs: list[str] = Field(default_factory=lambda: ["**/.git/**", "**/.venv/**", "**/__pycache__/**"])
    max_file_size_kb: int = 512


class IngestRepoResponse(BaseModel):
    ingestion_run_id: UUID
    status: str


class IngestionStatusResponse(BaseModel):
    ingestion_run_id: UUID
    status: str
    files_scanned: int
    chunks_written: int
    error: str | None = None


class VerifyMemoryRequest(BaseModel):
    project_id: UUID
    memory_id: UUID


class VerifyMemoryResponse(BaseModel):
    status_before: str
    status_after: str
    checks: list[str]


class SearchDocsRequest(BaseModel):
    project_id: UUID
    query: str
    top_k: int = Field(default=8, ge=1, le=50)
    file_path_prefix: str | None = None
    language: str | None = None
    commit_sha: str | None = None


class SearchMemoryRequest(BaseModel):
    project_id: UUID
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    include_stale: bool = False


class StoreMemoryRequest(BaseModel):
    project_id: UUID
    subject: str
    fact: str
    reason: str | None = None
    citations: list[CitationInput]
    confidence: float = Field(default=0.75, ge=0, le=1)


class SnippetResponse(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    score: float
    chunk_text: str


class MemoryResponse(BaseModel):
    memory_id: UUID
    subject: str
    fact: str
    citations: list[dict[str, object]]
    confidence: float
    status: str
