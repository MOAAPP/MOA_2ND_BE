"""Stage 2 public schemas. A parsed intent is not an observed financial transaction."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

PaymentMethod = Literal["debit", "credit", "transfer", "cash", "atm", "unknown"]
TransactionKind = Literal[
    "debit_approval", "credit_approval", "card_bill", "installment_approval",
    "account_in", "account_out", "automatic_transfer", "salary", "allowance",
    "refund", "cancellation", "atm_withdrawal", "unclassified"
]


class ParsedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(description="SHA256 identifier of normalized source message, never plaintext")
    fingerprint: str = Field(description="Per-user fingerprint for possible duplicates")
    user_id: str = Field(pattern=r"^S\d\d$")
    kind: TransactionKind
    issuer: str | None = None
    payment_method: PaymentMethod = "unknown"
    merchant: str | None = None
    amount: int | None = Field(default=None, ge=0)
    occurred_at: datetime | None = None
    debit_at: datetime | None = None
    card_id: str | None = None
    installments: int | None = Field(default=None, ge=1, le=60)
    transaction_status: Literal["authorization", "posted", "cancelled", "unconfirmed"]
    uncertainty: list[str] = Field(default_factory=list)
    duplicate_possible: bool = False
    parsing_source: Literal["rules", "llm", "manual_review"] = "rules"
    confidence: float = Field(ge=0, le=1)


class SmsAnalyzeRequest(BaseModel):
    user_id: str = Field(pattern=r"^S\d\d$", examples=["S01"])
    text: str = Field(min_length=4, max_length=12000, description="Pasted SMS, not stored as plaintext")
    use_llm_for_unknown: Literal[False] = Field(default=False, description="Legacy field; true is rejected. Financial SMS never calls AI.")


class SmsAnalysis(BaseModel):
    analysis_status: Literal["parsed", "needs_manual_review"]
    transactions: list[ParsedTransaction]
    skipped: int = 0
    llm_called: bool = False
    masked_before_llm: bool = False
    warnings: list[str] = Field(default_factory=list)


class ConfirmTransaction(BaseModel):
    user_id: str = Field(pattern=r"^S\d\d$")
    transaction: ParsedTransaction
    account_id: str | None = Field(default=None, description="Dataset account belonging to the same user")
    acknowledge_unresolved: bool = False

    @model_validator(mode="after")
    def must_match(self):
        if self.user_id != self.transaction.user_id:
            raise ValueError("user_id must match transaction.user_id")
        return self


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class VisualEvidence(BaseModel):
    field: str
    text: str | None = None
    region: Rect | None = None
    explanation: str | None = None


class AlternativeProduct(BaseModel):
    product_name: str | None = None
    selling_price: int | None = Field(default=None, ge=0)
    region: Rect | None = None
    reason_not_main: str | None = None


class VisionFields(BaseModel):
    """LLM must report only visible facts; monetary total is always calculated server-side."""
    model_config = ConfigDict(extra="forbid")
    product_name: str | None = None
    brand: str | None = None
    category: str | None = None
    main_product_candidate: str | None = None
    alternative_candidates: list[AlternativeProduct] = Field(default_factory=list)
    product_image_region: Rect | None = None
    original_price: int | None = Field(default=None, ge=0)
    selling_price: int | None = Field(default=None, ge=0)
    conditional_discount_price: int | None = Field(default=None, ge=0)
    selected_options: list[str] = Field(default_factory=list)
    quantity: int | None = Field(default=None, ge=1, le=999)
    option_extra_cost: int | None = Field(default=None, ge=0)
    shipping_fee: int | None = Field(default=None, ge=0)
    visual_evidence_regions: list[VisualEvidence] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    needs_user_confirmation: bool = True


class VisionAnalysis(BaseModel):
    analysis_id: str
    user_id: str
    analysis_status: Literal["succeeded"]
    source: Literal["multimodal_llm"] = "multimodal_llm"
    model: str
    cached: bool = False
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = None
    output_tokens: int | None = None
    product: VisionFields
    estimated_checkout_total: int | None = None
    checkout_total_confirmed: bool = False


class ConfirmPurchase(BaseModel):
    user_id: str = Field(pattern=r"^S\d\d$")
    analysis_id: str = Field(min_length=8)
    product: VisionFields
    shipping_confirmed: bool = False
    use_conditional_discount: bool = False
    discount_eligible_confirmed: bool = False
    confirm_total: bool = False


class PurchaseCandidate(BaseModel):
    id: str
    user_id: str
    analysis_id: str
    status: Literal["candidate"] = "candidate"
    product: VisionFields
    estimated_checkout_total: int | None
    checkout_total_confirmed: bool
    created_at: datetime
    # Only a later explicit simulation can affect a hypothetical world.
    actual_balance_impact: Literal[0] = 0


class PurchaseSimulationRequest(BaseModel):
    account_id: str | None = None
    debit_at: datetime = Field(description="Timezone-aware simulated debit date")
    payment_method: Literal["debit", "transfer"] = "debit"
    horizon_days: int = Field(default=14, ge=1, le=90)
    as_of: datetime | None = None
