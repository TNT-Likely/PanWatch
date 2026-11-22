from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, SecretStr, StringConstraints

Platform = Literal["ios", "android"]
PairingState = Literal[
    "waiting_for_device", "waiting_for_approval", "approved", "denied",
    "cancelled", "redeemed", "expired",
]
PairingMaterial = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
ClaimKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
VerificationCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
DeviceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Capabilities(BaseModel):
    service: Literal["panwatch"] = "panwatch"
    service_version: str
    protocol_min: Literal[1] = 1
    protocol_max: Literal[1] = 1
    pairing: bool
    password_login: bool
    portfolio_read: bool = True
    portfolio_metrics: bool = True
    symbol_facts: bool = True
    diagnostics_read: bool = True
    session_refresh: bool = True
    watchlist_read: bool = True
    alerts_manage: bool = True


class PairingStart(BaseModel):
    origin: str = Field(max_length=300)


class PasswordLogin(PairingStart):
    username: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    device_name: DeviceName
    platform: Platform
    renewable: bool = False
    manage_alerts: bool = False
    read_diagnostics: bool = False


class PairingQR(BaseModel):
    kind: Literal["panwatch-pairing"] = "panwatch-pairing"
    protocol: Literal[1] = 1
    pairing_id: str
    origin: str
    challenge: PairingMaterial
    expires_at: int


class PairingClaim(BaseModel):
    challenge: PairingMaterial
    claim_key: ClaimKey
    device_name: DeviceName
    platform: Platform
    renewable: bool = False
    manage_alerts: bool = False
    read_diagnostics: bool = False


class PairingDecision(BaseModel):
    decision: Literal["approve", "deny", "cancel"]
    verification_code: VerificationCode | None = None


class PairingStatus(BaseModel):
    status: PairingState
    expires_at: int
    device_name: str | None
    platform: Platform | None
    verification_code: VerificationCode | None
    renewable: bool = False
    manage_alerts: bool = False
    read_diagnostics: bool = False


class DeviceGrant(BaseModel):
    instance_id: str
    device_id: str
    origin: str
    access_token: str
    scope: Literal["portfolio:read"] = "portfolio:read"
    expires_at: int
    refresh_token: str | None = None
    refresh_expires_at: int | None = None
    watchlist_read: bool = False
    alerts_manage: bool = False
    portfolio_metrics: bool = True
    symbol_facts: bool = True
    diagnostics_read: bool = False


class DeviceSummary(BaseModel):
    id: str
    name: str
    platform: Platform
    created_at: int
    expires_at: int
    revoked_at: int | None


class Holding(BaseModel):
    symbol: str
    name: str
    market: str
    quantity: float
    market_value: float | None
    currency: Literal["CNY", "HKD", "USD"]


class Portfolio(BaseModel):
    total_assets: float
    currency: Literal["CNY"] = "CNY"
    valuation: Literal["complete", "partial", "unavailable"]
    holdings: list[Holding]


class WatchlistSymbol(BaseModel):
    symbol: str
    name: str
    market: str


T = TypeVar("T")


class MobileResponse(BaseModel, Generic[T]):
    code: Literal[0] = 0
    success: Literal[True] = True
    data: T
    message: Literal[""] = ""
