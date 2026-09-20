"""Strict authoring contracts. No medical thresholds are supplied by the app."""
from decimal import Decimal
from typing import Annotated, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Applicability(StrictModel):
    population: Text
    clinical_context: Text
    intended_use: Text
    prerequisites: list[Text]
    exclusions: list[Text]
    required_inputs: list[Text]
    missing_data_policy: Literal["insufficient_data"] = "insufficient_data"
    discordance_policy: Text
    limitations: list[Text] = Field(min_length=1)


class Measurement(StrictModel):
    modality: Text
    protocol: Text
    plane: Text
    landmarks: Text
    caliper_or_roi: Text
    quantity: Text
    unit: Text
    quality_requirements: Text
    pitfalls: list[Text] = Field(min_length=1)


class Threshold(StrictModel):
    parameter: Text
    operator: Literal["<", "<=", "==", ">=", ">"]
    value: Decimal = Field(allow_inf_nan=False)
    unit: Text


class Evidence(StrictModel):
    id: Identifier
    document_version_id: Identifier
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_page: int = Field(ge=1)
    printed_page: str | None = None
    section: Text
    table: str | None = None
    table_cell: str | None = None
    footnote: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    quote: Text
    parser_version: Text

    @model_validator(mode="after")
    def valid_bbox(self) -> Self:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("bbox must be normalized, ordered page coordinates")
        return self


class Claim(StrictModel):
    id: Identifier
    text_vi: Text
    evidence_ids: list[Identifier] = Field(min_length=1)
    threshold: Threshold | None = None


class Logic(StrictModel):
    # Deliberately not an executable clinical calculator in phase 1.
    kind: Literal["all", "any", "at_least", "reference_only"]
    claim_ids: list[Identifier] = Field(min_length=1)
    minimum: int | None = Field(default=None, ge=1)
    description: Text

    @model_validator(mode="after")
    def valid_minimum(self) -> Self:
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("duplicate logic claim")
        if self.kind == "at_least":
            if self.minimum is None or self.minimum > len(self.claim_ids):
                raise ValueError("at_least requires a valid minimum")
        elif self.minimum is not None:
            raise ValueError("minimum is only valid for at_least")
        return self


class Card(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    topic_id: Identifier
    name_vi: Text
    name_en: Text
    aliases: list[Text] = Field(default_factory=list, max_length=30)
    type: Literal["diagnostic_criteria", "severity_grading", "classification", "clinical_score", "imaging_guideline", "measurement", "management_algorithm", "follow_up", "red_flags", "differential_diagnosis", "reporting_system"]
    guideline_family: Text
    guideline_module: Text
    guideline_version: Text
    modality: Text
    origin: Literal["manual", "ai_extracted", "imported"]
    applicability: Applicability
    measurement: Measurement | None = None
    claims: list[Claim] = Field(min_length=1, max_length=100)
    logic: Logic
    evidence: list[Evidence] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_references(self) -> Self:
        claim_ids = {c.id for c in self.claims}
        evidence_ids = {e.id for e in self.evidence}
        if len(claim_ids) != len(self.claims) or len(evidence_ids) != len(self.evidence):
            raise ValueError("duplicate claim/evidence id")
        if any(not set(c.evidence_ids) <= evidence_ids for c in self.claims):
            raise ValueError("claim refers to missing evidence")
        if set(self.logic.claim_ids) != claim_ids:
            raise ValueError("logic must reference every claim exactly once")
        if self.type == "measurement" and self.measurement is None:
            raise ValueError("measurement card requires measurement protocol")
        if self.measurement and self.measurement.modality != self.modality:
            raise ValueError("measurement modality does not match card")
        return self


class SourceVersion(StrictModel):
    id: Identifier
    source_id: Identifier
    title: Text
    organization: Text
    version: Text
    doi: str | None = None
    official_url: str = Field(pattern=r"^https://[^\s]+$", max_length=2000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pdf_pages: int = Field(ge=1)
    license_note: Text
    archive_reference: Text
    # Admin registers a retained source, not a short-lived cache path.
    retention: Literal["retained_source"]


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    card: Card


class ReviewRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: Text
    evidence_checked: bool
    applicability_checked: bool


class WithdrawRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    reason: Text
