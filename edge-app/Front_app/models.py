from pydantic import BaseModel, Field
from typing import Optional, Any

class SensorData(BaseModel):
    Umidade_solo: float = 0.0
    Umidade_Ar: float = 0.0
    Temperatura_Atual: float = 0.0

class Setpoints(BaseModel):
    Setpoint_Umidade_solo: float = 60.0
    Setpoint_Umidade_Ar: float = 70.0
    Setpoint_temp: float = 25.0

class Controls(BaseModel):
    greenhouse_liga: bool = False
    cooler_status: bool = False
    Aquecimento_status: bool = False
    irrigacao_status: bool = False
    lamp_status: bool = False

class GreenhouseState(BaseModel):
    timestamp: str
    connected: bool = False
    sensors: SensorData = Field(default_factory=SensorData)
    setpoints: Setpoints = Field(default_factory=Setpoints)
    controls: Controls = Field(default_factory=Controls)
    meta: dict = Field(default_factory=dict)
    last_alarm: Optional[str] = None
