import asyncio
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException
import json
import os
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURAÇÃO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MODBUS_HOST = os.getenv("MODBUS_HOST", "host.docker.internal")
MODBUS_PORT = int(os.getenv("MODBUS_PORT", "502"))
MODBUS_UNIT_ID = int(os.getenv("MODBUS_UNIT_ID", "1"))

# Mapeamento de variáveis CODESYS → Holding Registers
# Ajuste conforme suas variáveis no CODESYS
VARIABLES = {
    "soil_humidity": {"register": 0, "type": "float", "scale": 1, "unit": "%"},
    "air_humidity": {"register": 1, "type": "float", "scale": 1, "unit": "%"},
    "soil_temp": {"register": 2, "type": "float", "scale": 1, "unit": "°C"},
    "air_temp": {"register": 3, "type": "float", "scale": 1, "unit": "°C"},
    "cooling_status": {"register": 4, "type": "bool", "scale": 1, "unit": ""},
    "heating_status": {"register": 5, "type": "bool", "scale": 1, "unit": ""},
    "lamp_status": {"register": 6, "type": "bool", "scale": 1, "unit": ""},
}

# Setpoints (meta de valores) - Holding Registers para escrita
SETPOINTS = {
    "soil_humidity_sp": {"register": 100, "type": "float"},
    "air_humidity_sp": {"register": 101, "type": "float"},
    "soil_temp_sp": {"register": 102, "type": "float"},
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ESTADO GLOBAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

current_values = {var: 0 for var in VARIABLES}
current_setpoints = {sp: 0 for sp in SETPOINTS}
connected = False
connection_attempts = 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLIENTE MODBUS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

modbus_client = AsyncModbusTcpClient(
    host=MODBUS_HOST,
    port=MODBUS_PORT,
    timeout=5
)

async def connect_modbus():
    """Tenta conectar ao servidor Modbus com retry"""
    global connected, connection_attempts
    
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            logger.info(f"[Tentativa {attempt+1}/{max_attempts}] Conectando a {MODBUS_HOST}:{MODBUS_PORT}")
            connected = await modbus_client.connect()
            
            if connected:
                logger.info("✓ Conectado ao CODESYS/Modbus com sucesso!")
                connection_attempts = 0
                return True
            
        except ConnectionException as e:
            logger.warning(f"✗ Erro de conexão: {e}")
            await asyncio.sleep(2)
        
        except Exception as e:
            logger.error(f"✗ Erro inesperado: {e}")
            await asyncio.sleep(2)
    
    logger.error("✗ Falha ao conectar ao Modbus após 3 tentativas")
    connected = False
    return False

async def read_modbus_values():
    """
    Lê valores continuamente do CODESYS.
    Reconecta automaticamente se desconectar.
    """
    global current_values, current_setpoints, connected
    
    while True:
        try:
            # Verifica e reconecta se necessário
            if not connected:
                await connect_modbus()
            
            # Se ainda não conectado, aguarda e tenta novamente
            if not connected:
                await asyncio.sleep(5)
                continue
            
            # ━━ LÊ VALORES (holding registers 0-10)
            result = await modbus_client.read_holding_registers(
                address=0,
                count=20,
                slave=MODBUS_UNIT_ID
            )
            
            if not result.isError():
                for var_name, var_config in VARIABLES.items():
                    reg_addr = var_config["register"]
                    if reg_addr < len(result.registers):
                        raw_value = result.registers[reg_addr]
                        scale = var_config.get("scale", 1)
                        
                        # Conversão de tipo
                        if var_config["type"] == "float":
                            current_values[var_name] = (raw_value / 100.0) * scale  # Assume 2 casas decimais
                        elif var_config["type"] == "bool":
                            current_values[var_name] = bool(raw_value)
                        else:  # int
                            current_values[var_name] = int(raw_value * scale)
                        
                        logger.debug(f"📊 {var_name}: {current_values[var_name]}")
            else:
                logger.warning("⚠ Erro ao ler holding registers")
                connected = False
            
            # ━━ LÊ SETPOINTS (holding registers 100-110)
            result_sp = await modbus_client.read_holding_registers(
                address=100,
                count=10,
                slave=MODBUS_UNIT_ID
            )
            
            if not result_sp.isError():
                for sp_name, sp_config in SETPOINTS.items():
                    reg_addr = sp_config["register"] - 100  # Offset
                    if reg_addr < len(result_sp.registers):
                        raw_value = result_sp.registers[reg_addr]
                        current_setpoints[sp_name] = raw_value / 100.0
            
        except ConnectionException:
            logger.error("✗ Conexão perdida com Modbus")
            connected = False
        
        except Exception as e:
            logger.error(f"✗ Erro na leitura: {e}")
            connected = False
        
        await asyncio.sleep(1)  # Lê a cada 1 segundo

async def write_modbus_register(address: int, value: int):
    """Escreve um valor em um register Modbus"""
    try:
        if not connected:
            logger.error("✗ Não conectado ao Modbus")
            return False
        result = await modbus_client.write_register(
            address=address,
            value=value,
            slave=MODBUS_UNIT_ID
        )
                
        if not result.isError():
            logger.info(f"✓ Escrito register {address}: {value}")
            return True
        else:
            logger.error(f"✗ Erro ao escrever register {address}")
            return False
    
    except Exception as e:
        logger.error(f"✗ Erro: {e}")
        return False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FASTAPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(title="Greenhouse Observer", version="1.0")

# Serve arquivos estáticos (se tiver CSS, JS externos)
# app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def startup_event():
    """Inicia leitura de Modbus ao iniciar o servidor"""
    logger.info("🚀 Iniciando servidor...")
    asyncio.create_task(read_modbus_values())

@app.on_event("shutdown")
async def shutdown_event():
    """Desconecta do Modbus ao desligar"""
    logger.info("🛑 Desligando servidor...")
    await modbus_client.close()

@app.get("/")
async def get_dashboard():
    """Serve o HTML do dashboard"""
    html_content = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>IHM Clean Dashboard</title>
<style>
body {
  margin: 0;
  padding: 0;
  background: #1a1a1a;
  font-family: Arial, sans-serif;
  color: #eaeaea;
  width: 1024px;
  height: 768px;
  overflow: hidden;
}

.screen {
  width: 1024px;
  height: 768px;
  padding: 10px;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: auto auto;
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
</style>
</head>
<body>
<div class="screen">
  <!-- BLOCO 1: Umidade do Solo -->
  <div class="block">
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
  <div class="block">
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
  <div class="block">
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
  <div class="block">
    <div class="title">⚙️ Controles</div>
    <div class="line">
      <span>Refrigeração</span>
      <div class="toggle off" id="toggleCooling">Desligado</div>
    </div>
    <div class="line">
      <span>Aquecimento</span>
      <div class="toggle off" id="toggleHeating">Ligado</div>
    </div>
    <div class="line">
      <span>Lâmpada</span>
      <div class="toggle off" id="toggleLamp">Desligado</div>
    </div>
    <div class="status-bar" id="statusBar">● Conectando...</div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// GRÁFICOS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const chartInstances = {};
const dataHistories = {
  soil_humidity: [],
  air_humidity: [],
  soil_temp: [],
};

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
        pointBackgroundColor: '#4caf50',
        pointBorderColor: '#fff',
        pointHoverRadius: 4,
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
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,0.7)',
          titleColor: '#fff',
          bodyColor: '#fff',
          callbacks: {
            label: function(context) {
              return context.parsed.y.toFixed(2);
            }
          }
        }
      }
    }
  });
}

function updateChart(id, value) {
  const chart = chartInstances[id];
  if (!chart) return;
  
  // Mantém últimos 30 pontos
  chart.data.labels.push(new Date().toLocaleTimeString().split(' ')[0]);
  chart.data.datasets[0].data.push(value);
  
  if (chart.data.labels.length > 30) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  
  chart.update('none'); // Atualiza sem animação
}

// Inicializa gráficos
createChart('chartSolo', 'Umidade do Solo');
createChart('chartAr', 'Umidade do Ar');
createChart('chartTempSolo', 'Temperatura do Solo');

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// WEBSOCKET
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const ws = new WebSocket("ws://" + window.location.host + "/ws/live");

ws.onopen = () => {
  console.log("✓ WebSocket conectado");
  document.getElementById("statusBar").className = "status-bar connected";
  document.getElementById("statusBar").textContent = "● Conectado ao CODESYS";
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  // ━━ Atualiza status de conexão
  const statusBar = document.getElementById("statusBar");
  if (data.connected) {
    statusBar.className = "status-bar connected";
    statusBar.textContent = "● Conectado ao CODESYS";
  } else {
    statusBar.className = "status-bar disconnected";
    statusBar.textContent = "● Desconectado do CODESYS";
  }
  
  // ━━ Atualiza valores no display
  const values = data.values;
  
  // Umidade Solo
  const soloHum = values.soil_humidity || 0;
  document.getElementById("soloValue").textContent = soloHum.toFixed(1) + "%";
  updateChart('chartSolo', soloHum);
  
  // Umidade Ar
  const airHum = values.air_humidity || 0;
  document.getElementById("arValue").textContent = airHum.toFixed(1) + "%";
  updateChart('chartAr', airHum);
  
  // Temperatura Solo
  const tempSolo = values.soil_temp || 0;
  document.getElementById("tempSoloValue").textContent = tempSolo.toFixed(1) + "°C";
  updateChart('chartTempSolo', tempSolo);
  
  // ━━ Atualiza toggles (status dos equipamentos)
  updateToggle("toggleCooling", values.cooling_status);
  updateToggle("toggleHeating", values.heating_status);
  updateToggle("toggleLamp", values.lamp_status);
};

ws.onerror = (error) => {
  console.error("✗ Erro WebSocket:", error);
  document.getElementById("statusBar").className = "status-bar disconnected";
  document.getElementById("statusBar").textContent = "✗ Erro de conexão";
};

ws.onclose = () => {
  console.log("✗ WebSocket desconectado");
  document.getElementById("statusBar").className = "status-bar disconnected";
  document.getElementById("statusBar").textContent = "● Aguardando reconexão...";
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

// Listeners para setpoints
document.getElementById("soloSp").addEventListener("change", (e) => {
  const value = parseInt(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "soil_humidity_sp", value: value * 100 })
  });
});

document.getElementById("arSp").addEventListener("change", (e) => {
  const value = parseInt(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "air_humidity_sp", value: value * 100 })
  });
});

document.getElementById("tempSoloSp").addEventListener("change", (e) => {
  const value = parseInt(e.target.value);
  fetch("/api/setpoint", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "soil_temp_sp", value: value * 100 })
  });
});

// Listeners para toggles
document.getElementById("toggleCooling").addEventListener("click", () => {
  fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "cooling_status", action: "toggle" })
  });
});

document.getElementById("toggleHeating").addEventListener("click", () => {
  fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "heating_status", action: "toggle" })
  });
});

document.getElementById("toggleLamp").addEventListener("click", () => {
  fetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "lamp_status", action: "toggle" })
  });
});
</script>
</body>
</html>"""
    return HTMLResponse(html_content)

@app.get("/api/status")
async def get_status():
    """Retorna status de conexão e valores atuais"""
    return {
        "connected": connected,
        "timestamp": datetime.now().isoformat(),
        "values": current_values,
        "setpoints": current_setpoints
    }

@app.post("/api/setpoint")
async def set_setpoint(payload: dict):
    """Escreve um setpoint no Modbus"""
    try:
        sp_name = payload.get("name")
        value = payload.get("value")
        
        if sp_name not in SETPOINTS:
            return {"error": f"Setpoint desconhecido: {sp_name}"}
        
        reg_addr = SETPOINTS[sp_name]["register"]
        success = await write_modbus_register(reg_addr, int(value))
        
        if success:
            current_setpoints[sp_name] = 0
            # current_setpoints[sp_name] = value / 100.0
            return {"status": "ok", "name": sp_name, "value": value}
        else:
            return {"error": "Falha ao escrever"}
    
    except Exception as e:
        logger.error(f"Erro em setpoint: {e}")
        return {"error": str(e)}

@app.post("/api/command")
async def send_command(payload: dict):
    """Envia comando para toggle de equipamentos"""
    try:
        name = payload.get("name")
        action = payload.get("action")
        
        if name not in VARIABLES or VARIABLES[name]["type"] != "bool":
            return {"error": f"Comando inválido: {name}"}
        
        reg_addr = VARIABLES[name]["register"]
        current_state = current_values.get(name, False)
        new_state = not current_state if action == "toggle" else bool(action)
        
        success = await write_modbus_register(reg_addr, int(new_state))
        
        if success:
            current_values[name] = new_state
            return {"status": "ok", "name": name, "state": new_state}
        else:
            return {"error": "Falha ao enviar comando"}
    
    except Exception as e:
        logger.error(f"Erro em comando: {e}")
        return {"error": str(e)}

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket para atualizações em tempo real"""
    await websocket.accept()
    try:
        while True:
            data = {
                "timestamp": datetime.now().isoformat(),
                "connected": connected,
                "values": current_values,
                "setpoints": current_setpoints
            }
            await websocket.send_json(data)
            await asyncio.sleep(1.0)  # Atualiza a cada 1000ms
    
    except Exception as e:
        logger.error(f"WebSocket erro: {e}")
    finally:
        await websocket.close()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# IMPORT NECESSÁRIO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from fastapi.responses import HTMLResponse
