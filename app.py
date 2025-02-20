import asyncio
import time
import queue
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
import os

# Audio settings (must match client settings)
CHANNELS = 1
RATE = 16000

class AudioTranscriber:
    def __init__(self, api_key, message_queue=None):
        self.api_key = api_key
        self.dg_connection = None
        self.message_queue = message_queue  # Used to forward transcription messages to the client

    def _on_message(self, result):
        """Handle incoming transcription messages."""
        sentence = result.channel.alternatives[0].transcript
        if sentence:
            if self.message_queue:
                self.message_queue.put(sentence)
            else:
                print(sentence)

    def _on_error(self, error):
        """Handle error messages."""
        err_msg = f"Error occurred: {error}"
        if self.message_queue:
            self.message_queue.put(err_msg)
        else:
            print(err_msg)

    def _on_close(self):
        """Handle connection closure."""
        close_msg = "Connection closed"
        if self.message_queue:
            self.message_queue.put(close_msg)
        else:
            print(close_msg)

    def setup_deepgram(self):
        """Initialize Deepgram connection."""
        try:
            deepgram = DeepgramClient(self.api_key)
            self.dg_connection = deepgram.listen.websocket.v("1")

            self.dg_connection.on(
                LiveTranscriptionEvents.Transcript,
                lambda _, result, **kwargs: self._on_message(result)
            )
            self.dg_connection.on(
                LiveTranscriptionEvents.Error,
                lambda _, error, **kwargs: self._on_error(error)
            )
            self.dg_connection.on(
                LiveTranscriptionEvents.Close,
                lambda _: self._on_close()
            )

            options = LiveOptions(
                model="nova-2",
                language="en-US",
                smart_format=True,
                interim_results=True,
                encoding="linear16",
                channels=CHANNELS,
                sample_rate=RATE
            )

            self.dg_connection.start(options)
            # Brief pause to ensure the connection is established
            time.sleep(0.5)
            return True
        except Exception as e:
            err_msg = f"Error setting up Deepgram: {e}"
            if self.message_queue:
                self.message_queue.put(err_msg)
            else:
                print(err_msg)
            return False

    def finish(self):
        """Finish the Deepgram connection."""
        if self.dg_connection:
            self.dg_connection.finish()

app = FastAPI()

# Hardcoded token for verification
HARDCODED_TOKEN = "a09s8d7fg4kjcvz0ba679m0n8svbnm"

def verify_token(token: str) -> bool:
    """Simple verification against a hardcoded token."""
    return token == HARDCODED_TOKEN

@app.websocket("/ws/transcribe")
async def websocket_endpoint(
    websocket: WebSocket,
    record_seconds: int = Query(30, description="Recording duration in seconds"),
    token: str = Query(..., description="Authentication token")
):
    # Verify the token before accepting the connection
    if not verify_token(token):
        # Close the connection with a policy violation close code (1008)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    # Create a queue to forward transcription messages to the client
    message_queue = queue.Queue()
    # Replace with your actual Deepgram API key or load from environment variables
    DEEPGRAM_API_KEY = os.environ.get('DEEPGRAM_API_KEY')

    # Initialize and setup Deepgram transcription
    transcriber = AudioTranscriber(DEEPGRAM_API_KEY, message_queue)
    if not transcriber.setup_deepgram():
        await websocket.send_text("Error setting up transcription service.")
        return

    async def forward_transcriptions():
        """Forward transcription messages from Deepgram back to the client."""
        while True:
            try:
                msg = message_queue.get(timeout=0.5)
                await websocket.send_text(msg)
                if msg == "Connection closed":
                    break
            except queue.Empty:
                await asyncio.sleep(0.1)

    transcription_task = asyncio.create_task(forward_transcriptions())

    start_time = time.time()
    try:
        # Receive binary audio data from the client until record_seconds elapse
        while True:
            if time.time() - start_time >= record_seconds:
                break
            data = await websocket.receive_bytes()
            if data is None:
                break
            if transcriber.dg_connection:
                transcriber.dg_connection.send(data)
    except WebSocketDisconnect:
        print("Client disconnected")
    finally:
        transcriber.finish()
        transcription_task.cancel()

@app.get("/health")
async def health_check():
    """
    A simple health check endpoint.
    Returns:
        A JSON object with a status message.
    """
    return {"status": "healthy"}
