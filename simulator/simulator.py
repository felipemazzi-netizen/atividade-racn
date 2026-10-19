"""Telemetria de torno fictícia: não define limites de uma máquina real."""
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
TOPIC = os.getenv('MQTT_TOPIC','industria/TC-04/telemetria')

def gerar_dados(ciclo):
    status = ['operando','parado','manutencao'][(ciclo//10)%3]
    return {'id':'TC-04','status':status,
            'temperatura':round(random.uniform(30,48) if status=='operando' else random.uniform(24,30),2),
            'vibracao':round(random.uniform(1,3) if status=='operando' else random.uniform(0,0.4),2),
            'atualizacao':datetime.now(timezone.utc).isoformat(),'simulado':True}

def main():
    online=threading.Event()
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    def on_connect(c,u,f,r,p):
        if r==0:
            online.set()
    def on_disconnect(c,u,f,r,p):
        online.clear()
    client.on_connect=on_connect
    client.on_disconnect=on_disconnect
    client.reconnect_delay_set(1,15)
    client.connect_async(os.getenv('MQTT_HOST','localhost'),int(os.getenv('MQTT_PORT','1883')),60)
    client.loop_start()
    ciclo=0
    try:
        while True:
            if online.is_set():
                dados=gerar_dados(ciclo)
                envio=client.publish(TOPIC,json.dumps(dados),qos=1,retain=True)
                logging.info('Publicação MQTT rc=%s: %s',envio.rc,dados)
                ciclo+=1
            else:
                logging.warning('Aguardando broker MQTT.')
            time.sleep(max(1,float(os.getenv('SIM_INTERVAL','3'))))
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()

if __name__=='__main__':
    main()
