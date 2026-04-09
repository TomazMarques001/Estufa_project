import asyncio
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
import json
from pathlib import Path
import os
import httpx 

from models import SensorData, Setpoints, Controls, GreenhouseState

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ESTADO GLOBAL (em memória)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NODE_RED_URL = os.getenv("NODE_RED_URL", "http://nodered:1880")



current_state = GreenhouseState(
    timestamp=datetime.now().isoformat(),
    connected=False,
    sensors=SensorData(),
    setpoints=Setpoints(),
    controls=Controls(),
    meta={}
)

# Lista de WebSockets conectados (para broadcast)
active_connections: list[WebSocket] = []

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FASTAPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(title="Greenhouse Observer API", version="3.0")

# Templates (se quiser HTML separado)
templates = Jinja2Templates(directory="templates")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENDPOINTS: Node-RED → FastAPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.post("/api/setpoint")
async def set_setpoint(payload: dict):
    """
    Frontend envia novo setpoint.

    POST /api/setpoint
    {"name": "Setpoint_Umidade_solo", "value": 65}
    """
    global current_state

    name = payload.get("name")
    value = payload.get("value")

    # Atualiza estado local (igual você já fazia)
    if name == "Setpoint_Umidade_solo":
        current_state.setpoints.Setpoint_Umidade_solo = value
    elif name == "Setpoint_Umidade_Ar":
        current_state.setpoints.Setpoint_Umidade_Ar = value
    elif name == "Setpoint_temp":
        current_state.setpoints.Setpoint_temp = value
    else:
        return {"error": f"Setpoint desconhecido: {name}"}
    # if not hasattr(current_state.setpoints, name):
    #   return {"error": f"Setpoint desconhecido: {name}"}

    # setattr(current_state.setpoints, name, value)
    
    logger.info(f"✓ Setpoint '{name}' = {value}")

    # Envia ao Node-RED apenas o setpoint alterado (topic e value separados).
    setpoint_update = {
      "topic": name,
      "value": value
    }

    try:
      async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(f"{NODE_RED_URL}/api/setpoint", json=setpoint_update)
      logger.info(f"✓ Enviado setpoints para Node-RED: status {resp.status_code}")
    except Exception as e:
        logger.error(f"✗ Erro ao enviar setpoints para Node-RED: {e}")

    # Atualiza frontend
    await broadcast_state()

    return {"status": "ok", "name": name, "value": value}

@app.post("/api/sensors")
async def update_state(data: dict):
    """
    Node-RED envia dados dos sensores aqui.
    POST /api/sensors
    {
  "sensors": {
    "Umidade_solo": 45.2,
    "Umidade_Ar": 62.1,
    "Temperatura_Atual": 24.8
  },
  
    """
    global current_state

    sensors = data.get("sensors", {})

    current_state.sensors = SensorData(**sensors) if sensors else current_state.sensors
    
    current_state.timestamp = datetime.now().isoformat()
    current_state.connected = True

    # Broadcast para todos WebSockets conectados
    await broadcast_state()
    
    return {"status": "ok", "timestamp": current_state.timestamp}

@app.post("/api/controls/update")
async def update_controls(data: Controls):
    """
    Node-RED envia estados dos atuadores aqui.
    POST /api/controls/update
    {
      "cooler_status": false,
      "Aquecimento_status": true,
      "lamp_status": false
    }
    """
    global current_state
    
    # Controls sao somente leitura no frontend e chegam apenas do Node-RED.
    current_state.controls = data
    current_state.timestamp = datetime.now().isoformat()
    current_state.connected = True
    
    logger.info(
      f"✓ Controles atualizados: Cooling={data.cooler_status}, Heating={data.Aquecimento_status}, Irrigacao={data.irrigacao_status}, Lamp={data.lamp_status}"
    )
    
    await broadcast_state()
    
    return {"status": "ok"}


@app.post("/api/meta/update")
async def update_meta(data: dict):
    """
    Node-RED envia dados de comunicação e status geral da estufa.
    POST /api/meta/update
    {
      "latency_ms": 35,
      "uptime_s": 1234,
      "greenhouse_enabled": false,
      "plc_status": true
    }
    """
    global current_state

    current_state.meta = data if data else current_state.meta
    current_state.timestamp = datetime.now().isoformat()
    current_state.connected = True

    await broadcast_state()

    return {"status": "ok", "timestamp": current_state.timestamp}




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENDPOINTS: Consulta de estado
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.get("/api/state")
async def get_state():
    """Retorna estado completo atual"""
    return current_state.dict()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WEBSOCKET (broadcast para frontend)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket para enviar atualizações em tempo real ao frontend"""
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"✓ WebSocket conectado (total: {len(active_connections)})")
    
    try:
        # Envia estado inicial
        await websocket.send_json(current_state.dict())
        
        # Mantém conexão viva
        while True:
            await asyncio.sleep(1)
            # Estado é enviado via broadcast quando algo muda
    
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info(f"✗ WebSocket desconectado (restantes: {len(active_connections)})")
    except Exception as e:
        logger.error(f"Erro no WebSocket: {e}")
        active_connections.remove(websocket)


async def broadcast_state():
    """Envia estado atual para todos os WebSockets conectados"""
    if not active_connections:
        return
    
    data = current_state.dict()
    
    for connection in active_connections:
        try:
            await connection.send_json(data)
        except Exception as e:
            logger.error(f"Erro ao enviar para WebSocket: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FRONTEND HTML
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.get("/")
async def get_dashboard():
    """Serve o dashboard HTML"""
    # Mantém o mesmo HTML que você já tem
    # Só muda a estrutura de dados que o WebSocket envia
    html_content = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Estufa - Dashboard</title>
<style>
body {
  margin: 0;
  padding: 0;
  background: #1a1a1a;
  font-family: Arial, sans-serif;
  color: #eaeaea;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}

.screen {
  width: 100%;
  height: 100%;
  padding: 10px;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-template-rows: auto auto auto;
  grid-template-areas:
    "solo   ar    temp"
    "ctrl   eventos eventos"
    "ctrl   clima   clima";
  gap: 12px;
}

.block {
  background: #262626;
  border-radius: 14px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
}
.block-solo       { grid-area: solo; }
.block-ar         { grid-area: ar; }
.block-temp       { grid-area: temp; }
.block-controles  { grid-area: ctrl; }
.block-eventos    { grid-area: eventos; }
.block-clima      { grid-area: clima; }
.title {
  font-size: 1.05rem;
  font-weight: bold;
  opacity: 0.9;
  margin-bottom: 8px;
}

.line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.95rem;
  padding: 6px 0;
}

.value {
  font-size: 1.3rem;
  font-weight: bold;
  color: #4caf50;
}

.toggle.disabled {
  opacity: 0.45;
  pointer-events: none;
  filter: grayscale(0.5);
}

.set {
  background: #333;
  border: 1px solid #555;
  border-radius: 8px;
  color: #fff;
  padding: 6px 10px;
  width: 80px;
  text-align: center;
  font-size: 0.95rem;
}

.set:focus {
  outline: 2px solid #4caf50;
  border-color: #4caf50;
}

.toggle {
  background: #444;
  padding: 8px 14px;
  border-radius: 8px;
  text-align: center;
  font-weight: bold;
  cursor: pointer;
  user-select: none;
  transition: all 0.3s;
  border: 2px solid transparent;
}

.toggle:hover {
  border-color: #666;
}

.toggle.on {
  background: #3fa33f;
  color: #fff;
}

.toggle.off {
  background: #a33f3f;
  color: #fff;
}

.chart-container {
  height: 120px;
  margin: 8px 0;
}

.status-bar {
  background: #1a1a1a;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 0.85rem;
  color: #999;
  text-align: center;
}

.status-bar.connected {
  color: #4caf50;
}

.status-bar.disconnected {
  color: #f44336;
}

/* Bloco de eventos: texto um pouco menor */
.events-text {
  font-size: 0.9rem;
  color: #ccc;
}
</style>
</head>
<body>
<div class="screen">
  <!-- BLOCO 1: Umidade do Solo -->
  <div class="block block-solo">
    <div class="title">💧 Umidade do Solo</div>
    <div class="chart-container"><canvas id="chartSolo"></canvas></div>
    <div class="line">
      <span>Atual</span>
      <span class="value" id="soloValue">--</span>
    </div>
    <div class="line">
      <span>Meta</span>
      <input class="set" id="soloSp" type="number" value="60" min="0" max="100">
    </div>
  </div>

  <!-- BLOCO 2: Umidade do Ar -->
  <div class="block block-ar">
    <div class="title">💨 Umidade do Ar</div>
    <div class="chart-container"><canvas id="chartAr"></canvas></div>
    <div class="line">
      <span>Atual</span>
      <span class="value" id="arValue">--</span>
    </div>
    <div class="line">
      <span>Meta</span>
      <input class="set" id="arSp" type="number" value="70" min="0" max="100">
    </div>
  </div>

  <!-- BLOCO 3: Temperatura do Solo -->
  <div class="block block-temp">
    <div class="title">🌡️ Temperatura do Solo</div>
    <div class="chart-container"><canvas id="chartTempSolo"></canvas></div>
    <div class="line">
      <span>Atual</span>
      <span class="value" id="tempSoloValue">--</span>
    </div>
    <div class="line">
      <span>Meta</span>
      <input class="set" id="tempSoloSp" type="number" value="25" min="0" max="50">
    </div>
  </div>

<!-- BLOCO 4: Controles -->
<div class="block block-controles">
  <div class="title">⚙️ Controles</div>

  <div class="line">
    <span>Refrigeração</span>
    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">
      <div class="toggle off" id="toggleCooling">Desligado</div>
    </div>
  </div>

  <div class="line">
    <span>Aquecimento</span>
    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">
      <div class="toggle off" id="toggleHeating">Desligado</div>
    </div>
  </div>

  <div class="line">
    <span>Irrigação</span>
    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">
      <div class="toggle off" id="toggleIrrigation">Desligado</div>
    </div>
  </div>

  <div class="line">
    <span>Lâmpada</span>
    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:4px;">
      <div class="toggle off" id="toggleLamp">Desligado</div>
    </div>
  </div>

  <div class="status-bar" id="statusBar">● Conectando...</div>

  <div style="margin-top:10px; display:grid; grid-template-columns:1fr 1fr; gap:8px;">
    <div style="background:#333; padding:8px; border-radius:8px; font-size:0.8rem;">
      <div style="color:#999;">Latência</div>
      <div id="statusLatency">-- ms</div>
    </div>
    <div style="background:#333; padding:8px; border-radius:8px; font-size:0.8rem;">
      <div style="color:#999;">Uptime</div>
      <div id="statusUptime">--</div>
    </div>
  <div style="background:#333; padding:8px; border-radius:8px; font-size:0.8rem;">
    <div style="color:#999;">Estufa</div>
    <div id="statusGreenhouse">--</div>
  </div>
    <div style="background:#333; padding:8px; border-radius:8px; font-size:0.8rem;">
      <div style="color:#999;">CLP</div>
      <div id="statusPlc">--</div>
    </div>
  </div>
</div>

  <!-- BLOCO 5: Tendências & Últimos Eventos (NOVA PARTE) -->
  <div class="block block-eventos">
    <div class="title">📊 Tendências & Últimos Eventos</div>
    <div class="line events-text">
      <span>Última irrigação</span>
      <span id="lastIrrigation">--</span>
    </div>
    <div class="line events-text">
      <span>Última refrigeração</span>
      <span id="lastCooling">--</span>
    </div>
    <div class="line events-text">
      <span>Último aquecimento</span>
      <span id="lastHeating">--</span>
    </div>
    <div class="line events-text">
      <span>Último alarme</span>
      <span id="lastAlarm">Nenhum</span>
    </div>
  </div>

  <!-- BLOCO 6: Clima da Estufa (NOVA PARTE) -->
  <div class="block block-clima">
    <div class="title">🌤️ Clima da Estufa</div>
    <div class="chart-container"><canvas id="chartClimate"></canvas></div>
    <div class="line">
      <span>Janela</span>
      <span style="font-size:0.85rem; color:#999;">últimos 30 pontos</span>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// GRÁFICOS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const chartInstances = {};
let climateChart = null;

function createChart(id, label) {
  const ctx = document.getElementById(id).getContext('2d');
  chartInstances[id] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: label,
        data: [],
        borderColor: '#4caf50',
        borderWidth: 2,
        fill: false,
        tension: 0.4,
        pointRadius: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { display: false },
        y: { 
          display: true,
          grid: { color: 'rgba(255,255,255,0.1)' },
          ticks: { color: '#999', font: { size: 10 } }
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

function updateChart(id, value) {
  const chart = chartInstances[id];
  if (!chart) return;
  
  chart.data.labels.push(new Date().toLocaleTimeString());
  chart.data.datasets[0].data.push(value);
  
  if (chart.data.labels.length > 30) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  
  chart.update('none');
}

// Clima da estufa: 3 variáveis no mesmo gráfico
function createClimateChart() {
  const ctx = document.getElementById('chartClimate').getContext('2d');
  climateChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Umidade Solo (%)',
          data: [],
          borderColor: '#4caf50',
          borderWidth: 2,
          fill: false,
          tension: 0.3,
          pointRadius: 0
        },
        {
          label: 'Umidade Ar (%)',
          data: [],
          borderColor: '#03a9f4',
          borderWidth: 2,
          fill: false,
          tension: 0.3,
          pointRadius: 0
        },
        {
          label: 'Temp. Solo (°C)',
          data: [],
          borderColor: '#ff9800',
          borderWidth: 2,
          fill: false,
          tension: 0.3,
          pointRadius: 0,
          yAxisID: 'y2'
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { display: false },
        y: { 
          position: 'left',
          grid: { color: 'rgba(255,255,255,0.1)' },
          ticks: { color: '#999', font: { size: 10 } }
        },
        y2: {
          position: 'right',
          grid: { display: false },
          ticks: { color: '#ffb74d', font: { size: 10 } }
        }
      },
      plugins: {
        legend: {
          display: true,
          labels: { color: '#ccc', font: { size: 9 } }
        }
      }
    }
  });
}
function setToggleEnabled(elementId, enabled) {
  const element = document.getElementById(elementId);
  if (!element) return;

  if (enabled) {
    element.classList.remove("disabled");
    element.title = "";
  } else {
    element.classList.add("disabled");
    element.title = "Estufa desligada";
  }
}

function updateClimateChart(soilHum, airHum, soilTemp) {
  if (!climateChart) return;
  const label = new Date().toLocaleTimeString();
  const data = climateChart.data;

  data.labels.push(label);
  data.datasets[0].data.push(soilHum);
  data.datasets[1].data.push(airHum);
  data.datasets[2].data.push(soilTemp);

  if (data.labels.length > 30) {
    data.labels.shift();
    data.datasets.forEach(ds => ds.data.shift());
  }

  climateChart.update('none');
}

// Inicializa gráficos
createChart('chartSolo', 'Umidade do Solo');
createChart('chartAr', 'Umidade do Ar');
createChart('chartTempSolo', 'Temperatura do Solo');
createClimateChart();

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// WEBSOCKET
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

let lastControls = null;

const ws = new WebSocket("ws://" + window.location.host + "/ws/live");

ws.onopen = () => {
  console.log("✓ WebSocket conectado");
  document.getElementById("statusBar").className = "status-bar connected";
  document.getElementById("statusBar").textContent = "● Conectado";
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  // Status
  const statusBar = document.getElementById("statusBar");
  if (data.connected) {
    statusBar.className = "status-bar connected";
    statusBar.textContent = "● Conectado ao CLP";
  } else {
    statusBar.className = "status-bar disconnected";
    statusBar.textContent = "● Desconectado";
  }
  
  const meta = data.meta || {};
  const controls = data.controls || {};
  const greenhouseEnabled = meta.greenhouse_enabled ?? false;

  document.getElementById("statusGreenhouse").textContent =
    greenhouseEnabled ? "Ligada" : "Desligada";

  setToggleEnabled("toggleCooling", greenhouseEnabled);
  setToggleEnabled("toggleHeating", greenhouseEnabled);
  setToggleEnabled("toggleIrrigation", greenhouseEnabled);
  setToggleEnabled("toggleLamp", greenhouseEnabled);

  if (meta.latency_ms != null) {
    document.getElementById("statusLatency").textContent = meta.latency_ms + " ms";
  }

  if (meta.uptime_s != null) {
    document.getElementById("statusUptime").textContent = meta.uptime_s + " s";
  }

  if (meta.plc_status != null) {
    document.getElementById("statusPlc").textContent = meta.plc_status ? "OK" : "Falha";
  }
    
  // ━━ Sensores
  const sensors = data.sensors || {};
  
  const soilHum = sensors.Umidade_solo || 0;
  const airHum = sensors.Umidade_Ar || 0;
  const soilTemp = sensors.Temperatura_Atual || 0;

  document.getElementById("soloValue").textContent = soilHum.toFixed(1) + "%";
  updateChart('chartSolo', soilHum);
  
  document.getElementById("arValue").textContent = airHum.toFixed(1) + "%";
  updateChart('chartAr', airHum);
  
  document.getElementById("tempSoloValue").textContent = soilTemp.toFixed(1) + "°C";
  updateChart('chartTempSolo', soilTemp);

  updateClimateChart(soilHum, airHum, soilTemp);
  
  // ━━ Setpoints
  const setpoints = data.setpoints || {};
  if (setpoints.Setpoint_Umidade_solo != null) {
    document.getElementById("soloSp").value = setpoints.Setpoint_Umidade_solo.toFixed(1);
  }
  if (setpoints.Setpoint_Umidade_Ar != null) {
    document.getElementById("arSp").value = setpoints.Setpoint_Umidade_Ar.toFixed(1);
  }
  if (setpoints.Setpoint_temp != null) {
    document.getElementById("tempSoloSp").value = setpoints.Setpoint_temp.toFixed(1);
  }
  
  // ━━ Controles
  updateToggle("toggleCooling", controls.cooler_status);
  updateToggle("toggleHeating", controls.Aquecimento_status);
  updateToggle("toggleIrrigation", controls.irrigacao_status);
  updateToggle("toggleLamp", controls.lamp_status);

  updateLastEvents(controls, data);
};

ws.onerror = (error) => {
  console.error("✗ Erro WebSocket:", error);
};

ws.onclose = () => {
  console.log("✗ WebSocket desconectado");
  document.getElementById("statusBar").className = "status-bar disconnected";
  document.getElementById("statusBar").textContent = "● Reconectando...";
  setTimeout(() => location.reload(), 5000);
};

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// CONTROLES
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function updateToggle(elementId, state) {
  const element = document.getElementById(elementId);
  if (state) {
    element.classList.add("on");
    element.classList.remove("off");
    element.textContent = "Ligado";
  } else {
    element.classList.add("off");
    element.classList.remove("on");
    element.textContent = "Desligado";
  }
}

// Últimos eventos de atuadores + alarmes
function updateLastEvents(controls, data) {
  const now = new Date().toLocaleTimeString();

  if (lastControls) {
    if (!lastControls.cooler_status && controls.cooler_status) {
      document.getElementById("lastCooling").textContent = "Ligado às " + now;
    }
    if (!lastControls.Aquecimento_status && controls.Aquecimento_status) {
      document.getElementById("lastHeating").textContent = "Ligado às " + now;
    }
    if (lastControls.irrigacao_status !== undefined &&
        controls.irrigacao_status !== undefined &&
        !lastControls.irrigacao_status && controls.irrigacao_status) {
      document.getElementById("lastIrrigation").textContent = "Iniciada às " + now;
    }
  }

  // Se no futuro você mandar alarmes pelo backend:
  if (data.last_alarm) {
    document.getElementById("lastAlarm").textContent = data.last_alarm;
  }

  lastControls = { ...controls };
}

// Setpoints
document.getElementById("soloSp").addEventListener("change", (e) => {
  const value = parseFloat(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Setpoint_Umidade_solo", value: value })
  });
});

document.getElementById("arSp").addEventListener("change", (e) => {
  const value = parseFloat(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Setpoint_Umidade_Ar", value: value })
  });
});

document.getElementById("tempSoloSp").addEventListener("change", (e) => {
  const value = parseFloat(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Setpoint_temp", value: value })
  });
});
</script>
</body>
</html>
"""
    return HTMLResponse(html_content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
