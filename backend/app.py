"""API do TC-04. Um processo com quatro threads HTTP e um assinante MQTT."""
import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
import paho.mqtt.client as mqtt
from waitress import serve
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
EQUIPMENT_ID = 'TC-04'
TOPIC = os.getenv('MQTT_TOPIC', 'industria/TC-04/telemetria')
DB_PATH = os.getenv('DB_PATH', 'data/torno.sqlite3')
STALE_SECONDS = float(os.getenv('STALE_SECONDS','15'))
lock = threading.RLock()
state = {'mqtt_conectado': False}
app = Flask(__name__)
EQUIPMENT = {
    'id': EQUIPMENT_ID, 'nome': 'Torno Convencional 04', 'tipo': 'Torno convencional',
    'modelo_visual': 'ROMI TORMAX 30A (identificado na foto fornecida)',
    'setor': 'Usinagem - contexto didático', 'simulado': True,
    'manutencao': {'situacao': 'Inspeção didática programada',
                  'plano': 'Consultar manual do fabricante e procedimento da instituição.',
                  'observacao': 'Sem datas, limites ou periodicidades oficiais definidos neste protótipo.'}}

def db_open():
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute('CREATE TABLE IF NOT EXISTS ultimo (id TEXT PRIMARY KEY, recebido REAL NOT NULL, dados TEXT NOT NULL)')
    return db

def registrar(payload):
    dados = json.loads(payload)
    if dados.get('id') != EQUIPMENT_ID or dados.get('simulado') is not True:
        raise ValueError('Equipamento inválido ou amostra não didática')
    for key in ('temperatura','vibracao'):
        n = dados[key]
        if isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or n < 0:
            raise ValueError('Valor inválido')
    if dados['status'] not in ('operando','parado','manutencao'):
        raise ValueError('Status inválido')
    dt = datetime.fromisoformat(dados['atualizacao'].replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Data precisa de fuso horário')
    with lock, db_open() as db:
        db.execute('INSERT OR REPLACE INTO ultimo(id,recebido,dados) VALUES (?,?,?)',
                   (EQUIPMENT_ID,time.time(),json.dumps(dados)))
    return dados

def latest():
    with lock, db_open() as db:
        row = db.execute('SELECT recebido,dados FROM ultimo WHERE id=?',(EQUIPMENT_ID,)).fetchone()
    return row

@app.after_request
def cors(response):
    # API de leitura de dados simulados, sem credenciais no navegador.
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/health')
def health():
    return jsonify(servico='torno-api', mqtt_conectado=state['mqtt_conectado'])

@app.get('/api/equipamentos/<equipment_id>')
def equipamento(equipment_id):
    if equipment_id != EQUIPMENT_ID:
        return jsonify(erro='Equipamento não encontrado'),404
    row = latest()
    result = dict(EQUIPMENT)
    result['status'] = json.loads(row[1])['status'] if row else 'aguardando telemetria'
    result['mqtt_conectado'] = state['mqtt_conectado']
    return jsonify(result)

@app.get('/api/equipamentos/<equipment_id>/telemetria')
def telemetria(equipment_id):
    if equipment_id != EQUIPMENT_ID:
        return jsonify(erro='Equipamento não encontrado'),404
    row = latest()
    if not row:
        return jsonify(erro='Aguardando primeira telemetria MQTT.', simulado=True),503
    result = json.loads(row[1])
    idade = max(0,time.time()-row[0],time.time()-datetime.fromisoformat(result['atualizacao'].replace('Z','+00:00')).timestamp())
    result.update(idade_segundos=round(idade,1), desatualizado=idade > STALE_SECONDS,
                  mqtt_conectado=state['mqtt_conectado'], unidades={'temperatura':'°C','vibracao':'mm/s'})
    return jsonify(result)

FRONT = Path(__file__).resolve().parents[1] / 'frontend'

@app.get('/')
def index():
    return send_from_directory(FRONT, 'index.html')

@app.get('/target.png')
def target_image():
    return send_from_directory(FRONT, 'target.png')

@app.get('/target.mind')
def target_data():
    return send_from_directory(FRONT, 'target.mind')

def iniciar_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    def on_connect(c, userdata, flags, reason, properties):
        state['mqtt_conectado'] = reason == 0
        if reason == 0:
            c.subscribe(TOPIC,qos=1)
            logging.info('API conectada ao broker e inscrita em %s',TOPIC)
    def on_disconnect(c, userdata, flags, reason, properties):
        state['mqtt_conectado'] = False
        logging.warning('Broker desconectado; último dado preservado com aviso de idade.')
    def on_message(c, userdata, msg):
        try:
            registrar(msg.payload.decode('utf-8'))
        except (ValueError,KeyError,TypeError,UnicodeError):
            logging.warning('Mensagem MQTT inválida ignorada.')
    client.on_connect=on_connect
    client.on_disconnect=on_disconnect
    client.on_message=on_message
    client.reconnect_delay_set(1,15)
    client.connect_async(os.getenv('MQTT_HOST','localhost'),int(os.getenv('MQTT_PORT','1883')),60)
    client.loop_start()
    return client

if __name__ == '__main__':
    client = iniciar_mqtt()
    try:
        serve(app,host='0.0.0.0',port=5000,threads=4)
    finally:
        client.disconnect()
        client.loop_stop()
