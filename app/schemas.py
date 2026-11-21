from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, field_validator, model_validator


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=128)


class Registration(Credentials):
    invite: str | None = Field(default=None, max_length=128)


class Profile(BaseModel):
    locator: str | None = Field(default=None, max_length=16)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    altitude_m: float = Field(default=0, ge=-500, le=10000, allow_inf_nan=False)
    timezone: str = Field(default='Asia/Shanghai', max_length=64)

    @model_validator(mode='after')
    def coordinates(self):
        if self.locator and self.locator.strip():
            from .locator import grid_center
            self.locator, self.latitude, self.longitude = grid_center(self.locator)
        else:
            self.locator = None
            if self.latitude is None or self.longitude is None:
                raise ValueError('请输入经纬度或 Maidenhead 定位')
        return self

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError('未知时区')
        return value


class BarkConfig(BaseModel):
    key: str = Field(max_length=256, pattern=r'^[A-Za-z0-9_-]*$')


class SubscriptionInput(BaseModel):
    min_elevation: float = Field(default=10, ge=0, le=90, allow_inf_nan=False)
    lead_minutes: int = Field(default=10, ge=1, le=120)
    enabled: bool = True


class OrbitInput(BaseModel):
    elements: str = Field(min_length=20, max_length=20000)
    format: Literal['tle', 'omm'] = 'tle'
    name: str = Field(default='', max_length=128)
    amsat_name: str | None = Field(default=None, max_length=64)


class FetchInput(BaseModel):
    norad_id: int = Field(gt=0, le=999999999)
    amsat_name: str | None = Field(default=None, max_length=64)


class RegistrationMode(BaseModel):
    mode: Literal['closed', 'invite', 'approval']


class Approval(BaseModel):
    approved: bool
