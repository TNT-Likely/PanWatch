from typing import Literal
from pydantic import BaseModel, Field, PositiveInt, model_validator

Source = Literal['assistant', 'agent', 'market']
View = Literal['all', 'unread', 'pending', 'archived', 'attention']

class NotificationSelection(BaseModel):
    ids: list[PositiveInt] = Field(default_factory=list, max_length=100)
    through_id: PositiveInt | None = None
    source: Source | None = None
    view: View = 'all'

    @model_validator(mode='after')
    def require_selection(self):
        if bool(self.ids) == bool(self.through_id):
            raise ValueError('Specify IDs or an observed upper bound')
        return self

class ArchiveNotifications(NotificationSelection):
    archived: bool = True
