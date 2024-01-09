#!/usr/bin/env python3
from gevent import pywsgi
from gevent import monkey
monkey.patch_all(ssl=False)
from flask import Flask, request, jsonify
from flask_sock import Sock
from werkzeug.routing import Map, Rule
import time, struct, math, json, io, traceback
import vonage
from gtts import gTTS
from pydub import AudioSegment
app = Flask(__name__)
sock = Sock(app)
PORT = 3003

client = vonage.Client(
    application_id="APPLICATION_ID",
    private_key="private.key",
)

SHORT_NORMALIZE = (1.0/32768.0)
swidth = 2
Threshold = 10
TIMEOUT_LENGTH = 0.5 #The silent length we allow before cutting recognition

def rms(frame): #Root mean Square: a function to check if the audio is silent. Commonly used in Audio stuff
    count = len(frame) / swidth
    format = "%dh" % (count) 
    shorts = struct.unpack(format, frame) #unpack a frame into individual Decimal Value
    #print(shorts)
    sum_squares = 0.0
    for sample in shorts:
        n = sample * SHORT_NORMALIZE #get the level of a sample and normalize it a bit (increase levels)
        sum_squares += n * n #get square of level
    rms = math.pow(sum_squares / count, 0.5) #summ all levels and get mean
    return rms * 1000 #raise value a bit so it's easy to read 

@app.route("/webhooks/answer")
def answer_call():
    print("Ringing",request.host)
    uuid = request.args.get("conversation_uuid")
    ncco = [
        {
            "action": "talk",
            "text": "This is a Voice Echo test. Speak after the Ding.",
        },
        {
            "action": "record",
            "eventMethod": "GET",
            "eventUrl": [
            f'https://{request.host}/webhooks/record-event'
            ]
        },
        {
            "action": "connect",
            "from": "Vonage",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": f'wss://{request.host}/socket'.format(request.host),
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "uuid": uuid}
                }
            ],
        }

    ]
    return jsonify(ncco)


@app.route("/webhooks/call-event", methods=["POST"])
def events():
    request_body = request.data.decode("utf-8")  # Assuming it's a text-based request body
    print("Request Body:", request_body)
    return "200"

@app.route("/webhooks/record-event", methods=["GET"])
def record_events():
    recording_url = request.args.get("recording_url")
    print("Recording URL", recording_url)
    if recording_url == "":
        return "200"
    print("Using Recording URL", recording_url)
    response = client.voice.get_recording(recording_url)
    filename = f'recording_{str(int(time.time()))}.mp3'

    with open("recordings/"+filename, "wb") as binary_file:   
        # Write bytes to file
        binary_file.write(response)
        binary_file.close()   
    print("Recording saved")
    return "200"

@sock.route("/socket")
def echo_socket(ws):
  rec = []
  current = 1
  end = 0
  uuid = ''

  #This part sends a wav file called ding.wav
  #we open the wav file
  with open("./ding.wav", "rb") as file:
    buffer = file.read()
  
  #we then chunk it out
  for i in range(0, len(buffer), 640):
    chunk = (buffer[i:i+640])
    ws.send(bytes(chunk))
  

  #!!!This part will echo whatever the user says if it detects a pause!!!
  while True:
    audio = ws.receive()
    if isinstance(audio, str):
        print("STR", audio)
        data = json.loads(audio)
        uuid = data["uuid"]
        continue #if this is a string, we don't handle it
    rms_val = rms(audio)

    #If audio is loud enough, set the current timeout to now and end timeout to now + TIMEOUT_LENGTH
    #This will start the next part that stores the audio until it's quiet again
    if rms_val > Threshold and not current <= end :
      print("Heard Something")
      current = time.time()
      end = time.time() + TIMEOUT_LENGTH

    #If levels are higher than threshold add audio to record array and move the end timeout to now + TIMEOUT_LENGTH
    #When the levels go lower than threshold, continue recording until timeout. 
    #By doing this, we only capture relevant audio and not continously call our STT/NLP with nonsensical sounds
    #By adding a trailing TIMEOUT_LENGTH we can capture natural pauses and make things not sound robotic
    if current <= end: 
      if rms_val >= Threshold: end = time.time() + TIMEOUT_LENGTH
      current = time.time()
      rec.append(audio)

    #process audio if we have an array of non-silent audio
    else:
      if len(rec)>0: 
        
        #Do TTS
        tmp = io.BytesIO()        
        tts = gTTS(text='I heard you say...', lang='en')  
        tts.write_to_fp(tmp)
        tmp.seek(0)                       
        sound = AudioSegment.from_mp3(tmp)
        tmp.close()
        #you have to assign the set_frame_rate to a variable as it does not modify in place
        sound = sound.set_frame_rate(16000)
        #we get the converted bytes
        out = sound.export(format="wav")
        tts_dat = out.read()
        out.close()
        # chunk it and send it out
        for i in range(0, len(tts_dat), 640):
            chunk = (tts_dat[i:i+640])
            ws.send(bytes(chunk))

        #ECHO Audio
        print("Echoing Audio", uuid)

        output_audio = b''.join(rec) #get whatever we heard
        #chunk it and send it out
        for i in range(0, len(output_audio), 640):
            chunk = (output_audio[i:i+640])
            ws.send(bytes(chunk))
        
        rec = [] #reset audio array to blank   

if __name__ == "__main__":
    server = pywsgi.WSGIServer(("0.0.0.0", PORT), app)
    server.serve_forever()
