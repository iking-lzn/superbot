import websocket
import hashlib
import base64
import hmac
import json
import pyaudio
import sys
import re
from ollama import Client
import pyttsx3
from typing import Dict, List
import _thread as thread
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time
from time import mktime
from datetime import datetime
import ssl

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

class VoiceAssistant:
    def __init__(self, endpoint, mode, model_name):
        self.ollama_endpoint = endpoint
        self.mode = mode
        self.history: List[Dict] = []
        self.model_name = model_name
        self.username = None
        self.is_first_interaction = True
        self.presets = {
            "text": {"system": "你是一个有帮助的助手，请用非常简单直接的方式回答"},
            "json": {
                "system": "请始终用JSON格式回答，包含'response'和'sentiment'字段",
                "response_template": {"response": "", "sentiment": ""}
            }
        }
        
        self.tts_engine = pyttsx3.init()
        self.setup_voice_engine()
        self.ws_param = None

    def setup_voice_engine(self):
        """配置语音引擎参数"""
        voices = self.tts_engine.getProperty('voices')
        self.tts_engine.setProperty('voice', voices[0].id)
        self.tts_engine.setProperty('rate', 150)
        
    def update_ws_param(self, ws_param, username="AI助手"):
        self.ws_param = ws_param
        self.presets = {
            "text": {"system": "你的名字叫" + username + "，用户的名字叫主人，每次回答加上用户的名字，并且用非常简短直接的方式回答"},
            "json": {
                "system": "请始终用JSON格式回答，包含'response'和'sentiment'字段",
                "response_template": {"response": "", "sentiment": ""}
            }
        }
    def update_username(self, username="AI助手"):
        self.presets = {
            "text": {"system": "你的名字叫" + username + "，每次回答请带上自己的名字，并且用非常简短直接的方式回答"},
            "json": {
                "system": "请始终用JSON格式回答，包含'response'和'sentiment'字段",
                "response_template": {"response": "", "sentiment": ""}
            }
        }

class Ws_Param:
    def __init__(self, APPID, APIKey, APISecret, vad_eos=10000):
        self.APPID = APPID
        self.APIKey = APIKey
        self.APISecret = APISecret
        self.CommonArgs = {"app_id": self.APPID}
        self.BusinessArgs = {
            "domain": "iat", "language": "zh_cn", 
            "accent": "mandarin", "vad_eos": int(vad_eos)
        }

    def create_url(self):
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))
        signature_origin = "host: ws-api.xfyun.cn\ndate: {}\nGET /v2/iat HTTP/1.1".format(date)
        signature_sha = hmac.new(self.APISecret.encode('utf-8'), signature_origin.encode('utf-8'),
                                 digestmod=hashlib.sha256).digest()
        signature_sha = base64.b64encode(signature_sha).decode('utf-8')
        authorization_origin = 'api_key="{}", algorithm="hmac-sha256", headers="host date request-line", signature="{}"'.format(
            self.APIKey, signature_sha)
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')
        v = {"authorization": authorization, "date": date, "host": "ws-api.xfyun.cn"}
        return 'wss://ws-api.xfyun.cn/v2/iat?' + urlencode(v)

def generate_response(prompt: str, assistant: VoiceAssistant) -> str:
    """处理命令并生成响应"""
    # 命令处理
    if re.search(r"切换(到|为)JSON模式", prompt):
        assistant.mode = "json"
        return "已切换到JSON模式"
    elif re.search(r"切换(到|为)文本模式", prompt):
        assistant.mode = "text"
        return "已切换到文本模式"

    # 正常响应生成
    current_preset = assistant.presets[assistant.mode]
    messages = [
        {"role": "system", "content": current_preset["system"]},
        *assistant.history[-5:],
        {"role": "user", "content": prompt}
    ]

    try:
        client = Client(host=assistant.ollama_endpoint)
        response = client.chat(model=assistant.model_name, messages=messages)
        return response["message"]["content"]
    except Exception as e:
        return f"请求API出错: {str(e)}"

def parse_response(response: str, mode: str) -> str:
    """解析不同模式的响应"""
    s1 = "<think>"
    s2 = "</think>"
    new_response = deleteByStartAndEnd(response, s1, s2)
    if mode == "json":
        try:
            data = json.loads(new_response)
            return data.get("response", "无效的JSON格式")
        except json.JSONDecodeError:
            return "响应解析失败"
    return new_response
def deleteByStartAndEnd(s, start, end):
    # 找出两个字符串在原始字符串中的位置，开始位置是：开始始字符串的最左边第一个位置，结束位置是：结束字符串的最右边的第一个位置
    x1 = s.index(start)
    x2 = s.index(end) + len(end)  # s.index()函数算出来的是字符串的最左边的第一个位置
    # 找出两个字符串之间的内容
    x3 = s[x1:x2]
    # 将内容替换为控制符串
    result = s.replace(x3, "")
    return result
def speak(text: str, assistant: VoiceAssistant):
    """文本转语音输出"""
    print(f"{assistant.username}回答: {text}")
    assistant.tts_engine.say(text)
    assistant.tts_engine.runAndWait()
    start_listening(assistant)  # 语音输出完成后重新开始监听

def on_message(ws, message, assistant):
    try:
        result = json.loads(message)
        if result['code'] == 0:
            text = ''.join([w['w'] for item in result['data']['result']['ws'] for w in item['cw']])
            print(f"用户输入: {text}")
            if re.search(r"退出|再见|关闭|结束|停止|拜拜", text):
                ws.close()
                sys.exit(0)
            elif assistant.is_first_interaction:
                assistant.username = text.strip() or "AI助手"
                response = f"感谢您为我命名，现在我叫{assistant.username}，请问有什么可以帮您？"
                assistant.is_first_interaction = False
                assistant.update_username(assistant.username)
            else:
                raw_response = generate_response(text, assistant)
                response = parse_response(raw_response, assistant.mode)
                
            speak(response, assistant)
    except Exception as e:
        print("处理错误:", e)

def on_error(ws, error):
    print(f"### 错误: {error}")
    if "SSL" in str(error) or "EOF" in str(error):
        print("检测到SSL错误，尝试重新连接...")
        start_listening(ws.assistant)  # 需要确保assistant对象可以通过ws访问

def on_close(ws, close_status_code, close_msg):
    print(f"### 连接关闭 ### 状态码: {close_status_code}, 消息: {close_msg}")

def on_open(ws):
    print("### 连接已打开 ###")
    def run(*args):
        audio_generator = record_audio()
        send_audio(ws, audio_generator)
    thread.start_new_thread(run, ())

def send_audio(ws, audio_generator):
    status = STATUS_FIRST_FRAME
    print("开始发送音频...")
    try:
        for chunk in audio_generator:
            if not ws.sock or not ws.sock.connected:  # 新增连接状态检查
                print("连接已断开，停止发送音频")
                break
            
            # 原有发送逻辑保持不变...
            data = {
                "common": ws.ws_param.CommonArgs,
                "business": ws.ws_param.BusinessArgs,
                "data": {
                    "status": STATUS_FIRST_FRAME if status == 0 else STATUS_CONTINUE_FRAME,
                    "format": "audio/L16;rate=16000",
                    "audio": base64.b64encode(chunk).decode('utf-8')
                }
            }
            ws.send(json.dumps(data))
            status = STATUS_CONTINUE_FRAME
        if ws.sock and ws.sock.connected:
            ws.send(json.dumps({"data": {"status": STATUS_LAST_FRAME}}))
    except Exception as e:
        print("发送错误:", e)
        ws.close()  # 确保关闭失效连接

def record_audio(rate=16000, chunk_size=1024):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1,
                    rate=rate, input=True, frames_per_buffer=chunk_size)
    try:
        while True:
            yield stream.read(chunk_size)
    except KeyboardInterrupt:
        print("停止录音")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

def start_listening(assistant):
    """启动新的语音识别会话"""
    print("开始新的语音识别会话...")
    websocket.enableTrace(False)
    ws_url = assistant.ws_param.create_url() 
    ws = websocket.WebSocketApp(ws_url,
                                on_message=lambda ws, msg: on_message(ws, msg, assistant),
                                on_error=on_error,
                                on_close=on_close)
    ws.ws_param = assistant.ws_param
    ws.on_open = on_open
    # 修改运行参数
    ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
if __name__ == "__main__":
    with open("config.json") as f:
        config = json.load(f)
    
    # 初始化语音助手
    voice_assistant = VoiceAssistant(
        endpoint=config["endpoint"],
        mode=config["mode"],
        model_name=config["model_name"]
    )
    
    # 配置语音识别参数
    ws_param = Ws_Param(
        APPID=config["APPID"],
        APIKey=config["APIKey"],
        APISecret=config["APISecret"],
        vad_eos=config["vad_eos"]
    )
    voice_assistant.update_ws_param(ws_param)
    
    # 初始问候
    voice_assistant.tts_engine.say("您好，请为我命名")
    voice_assistant.tts_engine.runAndWait()
    
    # 开始首次监听
    start_listening(voice_assistant)
    
    # 保持主线程运行
    while True:
        pass