"""Validated request and JSON contracts for the optional Copilot (PAN-46)."""

import json
import re
from dataclasses import dataclass
from typing import Literal, TypedDict

MAX_QUESTION = 1000
MAX_SELECTED = 5
MAX_TOOL_CALLS = 8
MAX_ITEMS = 5
MAX_CONTEXT_BYTES = 24_000
MAX_ANSWER_BYTES = 100_000
MAX_GID = 2**63 - 1

Intent = Literal["explanation", "common_collector", "next_step", "trace", "other"]


class RequestError(ValueError):
    pass


def parse_gid(value) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_GID:
        raise RequestError("gid должен быть целым int64 или строкой цифр; float не допускается")
    return value


@dataclass(frozen=True)
class Request:
    question: str
    selected_gids: tuple[int, ...] = ()
    depth: int = 2
    use_nvidia: bool = False

    def __post_init__(self):
        if not isinstance(self.question, str) or not 1 <= len(self.question.strip()) <= MAX_QUESTION:
            raise RequestError(f"Вопрос должен содержать от 1 до {MAX_QUESTION} символов")
        if not isinstance(self.selected_gids, (list, tuple)) or len(self.selected_gids) > MAX_SELECTED:
            raise RequestError(f"Выберите не больше {MAX_SELECTED} gid")
        if type(self.depth) is not int or not 1 <= self.depth <= 4:
            raise RequestError("depth должен быть целым от 1 до 4")
        if type(self.use_nvidia) is not bool:
            raise RequestError("use_nvidia должен быть boolean")
        gids = tuple(dict.fromkeys(parse_gid(g) for g in self.selected_gids))
        # Explicit UI selection is authoritative. With no selection, recognize long
        # dataset identifiers or a labelled gid list, never arbitrary amounts/depths.
        mentioned = re.findall(r"(?<!\d)[0-9]{15,19}(?!\d)", self.question)
        labelled = re.search(r"\bgids?\s*[:=]?\s*([0-9][0-9,;\s]*)", self.question, re.I)
        if labelled:
            mentioned += re.findall(r"[0-9]+", labelled[1])
        explicit = tuple(dict.fromkeys(parse_gid(g) for g in mentioned))
        if gids and any(g not in gids for g in explicit):
            raise RequestError("gid в вопросе не совпадают с выбранными узлами")
        gids = gids or explicit
        if len(gids) > MAX_SELECTED:
            raise RequestError(f"Выберите не больше {MAX_SELECTED} gid")
        object.__setattr__(self, "question", self.question.strip())
        object.__setattr__(self, "selected_gids", gids)

    @classmethod
    def from_dict(cls, data: dict) -> "Request":
        if not isinstance(data, dict) or set(data) - {"question", "selected_gids", "depth", "use_nvidia"}:
            raise RequestError("Неизвестные поля запроса")
        if "question" not in data:
            raise RequestError("Нет question")
        return cls(**data)


class Answer(TypedDict):
    question: str
    intent: str
    provider: Literal["fallback", "nvidia"]
    status: Literal["ok", "empty", "error"]
    summary: str
    gids: list[int]
    claims: list[dict]
    candidates: list[dict]
    evidence: list[dict]
    sources: list[dict]
    tool_calls: list[dict]
    warnings: list[dict]
    next_steps: list[str]
    fallback_reason: str | None
    error: dict | None
    verification: Literal["passed", "unavailable", "not_applicable"]


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def for_browser(answer: Answer) -> dict:
    """PAN-47 uses Python ints; JSON sent to React must preserve int64 gid exactly."""
    scalar_keys = {"gid", "src", "dst"}
    array_keys = {"gids", "selected_gids", "exclude_gids"}

    def convert(value, key=""):
        if key in scalar_keys and type(value) is int:
            return str(value)
        if key in array_keys and isinstance(value, (list, tuple)):
            return [str(g) for g in value]
        if isinstance(value, dict):
            return {k: convert(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    return convert(answer)
