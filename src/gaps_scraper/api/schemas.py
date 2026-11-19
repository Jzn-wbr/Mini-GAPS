"""Contrats Pydantic des réponses privées ; aucune identité scolaire exposée."""

from typing import Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class Output(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr = Field(min_length=20,max_length=128)
    device_name: StrictStr = Field(min_length=1,max_length=80,pattern=r"^[^\x00-\x1f\x7f]+$")


class Period(Output):
    value: str
    label: str


class Grade(Output):
    value: str | None
    status: Literal["published","unpublished"]
    ignored: bool
    absent: bool


class Weight(Output):
    numerator: int
    denominator: int
    percent: str


class Assessment(Output):
    id: str
    course: str
    group: str
    date: str
    description: str
    class_mean: str | None
    weight: Weight
    grade: Grade


class GradeGroup(Output):
    name: str
    mean: str | None
    weight: str


class Course(Output):
    name: str
    mean: str | None
    mean_excludes_exam: bool
    groups: list[GradeGroup]


class ModuleUnit(Output):
    id: str
    code: str
    name: str
    coefficient: str | None
    optional: bool
    alternative: bool
    aliases: list[str]


class NoteModule(Output):
    id: str
    code: str
    name: str
    units: list[ModuleUnit]
    mean: str | None
    provisional: bool


class NotesPeriod(Output):
    academic_year: Period
    semester: Period


class Notes(Output):
    academic_year: Period
    semester: Period
    timezone: str
    courses: list[Course]
    assessments: list[Assessment]
    source_state: str
    modules: list[NoteModule] = Field(default_factory=list)
    unmapped_courses: list[str] = Field(default_factory=list)
    programme_plan: str | None = None


class Count(Output):
    total: int
    justified: int
    unjustified: int


class AbsenceRate(Output):
    periods: int
    rate_percent: str
    unjustified_rate_percent: str


class AbsenceCourse(Output):
    id: str
    name: str
    periods: dict[str,Count | None]
    total: Count
    relative: AbsenceRate
    absolute: AbsenceRate


class Absences(Output):
    academic_year: Period
    timezone: str
    filter_label: str
    courses: list[AbsenceCourse]


class Occurrence(Output):
    id: str
    series_id: str
    summary: str
    description: str
    location: str
    status: str
    all_day: bool
    start: str
    end: str


class Schedule(Output):
    academic_year: Period
    semester: Period
    timezone: str
    occurrences: list[Occurrence]


Data = TypeVar("Data")


class Dataset(Output,Generic[Data]):
    data: Data | None
    freshness: Literal["fresh","stale","unavailable"]
    last_sync: str | None
    last_attempt: str | None
    error: str | None


class Session(Output):
    id: str
    name: str
    expires_at: int


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: StrictStr = Field(min_length=80,max_length=100,pattern=r"^[A-Za-z0-9_-]+={0,2}$")
    auth: StrictStr = Field(min_length=20,max_length=30,pattern=r"^[A-Za-z0-9_-]+={0,2}$")


class PushSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: StrictStr = Field(min_length=10,max_length=2048)
    expirationTime: float | None = None
    keys: PushKeys


class PushConfiguration(Output):
    enabled: bool
    public_key: str | None
    subscribed: bool
