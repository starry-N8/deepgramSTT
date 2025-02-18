from fastapi import FastAPI, WebSocket
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
import asyncio
import os

app = FastAPI()

DEEPGRAM_API_KEY = "7519c2bc0ada5ee88dfc7878b33c54ff64c2534a"

@app.websocket("/ws/transcribe")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    deepgram = DeepgramClient(DEEPGRAM_API_KEY)
    dg_connection = deepgram.listen.websocket.v("1")

    def on_message(result):
        """Handle incoming transcription messages."""
        sentence = result.channel.alternatives[0].transcript
        if sentence:
            asyncio.create_task(websocket.send_text(sentence))

    dg_connection.on(LiveTranscriptionEvents.Transcript, lambda _, result, **kwargs: on_message(result))

    options = LiveOptions(
        model="nova-2",
        language="en-US",
        smart_format=True,
        interim_results=True,
        encoding="linear16",
        channels=1,
        sample_rate=16000
    )

    dg_connection.start(options)

    try:
        while True:
            data = await websocket.receive_bytes()
            dg_connection.send(data)
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        dg_connection.finish()
