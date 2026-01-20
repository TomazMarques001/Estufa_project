# models.py
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean
from database import Base

class LeituraModbus(Base):
    __tablename__ = "leituras_modbus"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    soil_humidity = Column(Float)
    air_humidity = Column(Float)
    soil_temp = Column(Float)
    air_temp = Column(Float)
    cooling_status = Column(Boolean)
    heating_status = Column(Boolean)
    lamp_status = Column(Boolean)

class SetpointChange(Base):
    __tablename__ = "setpoints_changes"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    name = Column(String, index=True)
    value = Column(Float)
