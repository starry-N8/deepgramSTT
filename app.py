import threading
import time
import queue
import sounddevice as sd
import numpy
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions,
)

# Audio settings
CHUNK = 8192
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000

RECORD_SECONDS = 30  # duration of recording

class AudioTranscriber:
    def __init__(self, api_key, message_queue=None):
        self.api_key = api_key
        self.exit_flag = False
        self.dg_connection = None
        self.message_queue = message_queue  # thread-safe queue for sending messages to the WS client
    
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
        self.exit_flag = True

    def _on_close(self):
        """Handle connection closure."""
        close_msg = "Connection closed"
        if self.message_queue:
            self.message_queue.put(close_msg)
        else:
            print(close_msg)
        self.exit_flag = True

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
            # Wait briefly to ensure the connection is established
            time.sleep(0.5)
            return True
        except Exception as e:
            err_msg = f"Error setting up Deepgram: {e}"
            if self.message_queue:
                self.message_queue.put(err_msg)
            else:
                print(err_msg)
            return False

    def record_and_transcribe(self):
        """Record audio from the microphone and stream it to Deepgram."""
        try:
            if not self.setup_deepgram():
                return
            
            def callback(indata, frames, time, status):
                if status:
                    print(status)
                if self.dg_connection:
                    self.dg_connection.send(indata.tobytes())

            with sd.InputStream(samplerate=RATE, channels=CHANNELS, callback=callback):
                import time
                start_time = time.time()
                while time.time() - start_time < RECORD_SECONDS and not self.exit_flag:
                    time.sleep(0.1)

            if self.dg_connection:
                self.dg_connection.finish()
            if self.message_queue:
                self.message_queue.put("Transcription ended")
        except Exception as e:
            err_msg = f"An error occurred: {e}"
            if self.message_queue:
                self.message_queue.put(err_msg)
            else:
                print(err_msg)

# Create the FastAPI app
app = FastAPI()

@app.websocket("/ws/transcribe")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # Create a thread-safe queue for inter-thread communication
    message_queue = queue.Queue()
    # Replace with your actual Deepgram API key or load from environment variables
    DEEPGRAM_API_KEY = '7519c2bc0ada5ee88dfc7878b33c54ff64c2534a'
    
    # Initialize the AudioTranscriber with the queue
    transcriber = AudioTranscriber(DEEPGRAM_API_KEY, message_queue)
    # Run the transcription in a background thread (since recording is blocking)
    transcription_thread = threading.Thread(target=transcriber.record_and_transcribe)
    transcription_thread.start()

    try:
        while True:
            try:
                # Wait for a new message from the transcription thread (with timeout)
                message = message_queue.get(timeout=0.5)
                await websocket.send_text(message)
                # End the loop if transcription is finished
                if message == "Transcription ended":
                    break
            except queue.Empty:
                # No message available yet; continue checking
                continue
    except WebSocketDisconnect:
        print("Client disconnected")
    finally:
        transcription_thread.join()
