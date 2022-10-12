from jdatetime import datetime as jdatetime
from pydantic import BaseModel


class StateChange(BaseModel):
    datetime: jdatetime
    new_state: str

    class Config:
        arbitrary_types_allowed = True
