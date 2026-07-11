from dataclasses import dataclass
from typing import TypeVar, Generic, Optional, Any

from plugp100.common.functional.tri import Try, Failure, Success
from plugp100.common.utils.json_utils import Json
from plugp100.responses.tapo_exception import TapoException

T = TypeVar("T")


@dataclass
class TapoResponse(Generic[T]):
    error_code: int
    result: Optional[T]
    msg: Optional[str]

    @staticmethod
    def try_from_json(json: dict[str, Any]) -> Try["TapoResponse[Json]"]:
        response = TapoResponse(
            json.get("error_code", -1),
            json.get("result", {}),
            json.get("msg", "No message"),
        )
        if response.error_code == 0:
            return Success(response)
        else:
            return Failure(
                TapoException.from_error_code(response.error_code, response.msg)
            )
