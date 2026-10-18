"""Controlled Gemini Live VAD check using generated PCM only; no microphone audio is read or stored."""

import asyncio
import json
import math
import struct
import time

from app.config import Settings
from app.services.gemini import GeminiService


def generated_voice_like_pcm(seconds: float, rate: int = 16000) -> bytes:
    samples = []
    for index in range(int(seconds * rate)):
        t = index / rate
        envelope = min(1.0, t * 8, (seconds - t) * 8) * (0.72 + 0.28 * math.sin(2 * math.pi * 3.1 * t))
        value = envelope * (
            0.35 * math.sin(2 * math.pi * 120 * t)
            + 0.22 * math.sin(2 * math.pi * 700 * t)
            + 0.14 * math.sin(2 * math.pi * 1200 * t)
            + 0.08 * math.sin(2 * math.pi * 2600 * t)
        )
        samples.append(struct.pack("<h", int(max(-1, min(1, value)) * 32767)))
    return b"".join(samples)


async def main() -> None:
    settings = Settings()
    result = {"setup": False, "opening_complete": False, "activity_start": False, "activity_end": False, "input_transcription": False, "response_after_audio": False}
    async with GeminiService(settings).live("You are a concise interview test. Ask one question, then acknowledge the candidate's audio and ask the next question.") as session:
        result["setup"] = True
        await session.send_client_content(turns={"role": "user", "parts": [{"text": "Begin with one short question."}]}, turn_complete=True)
        async with asyncio.timeout(30):
            async for message in session.receive():
                content = getattr(message, "server_content", None)
                if content and getattr(content, "turn_complete", False):
                    result["opening_complete"] = True
                    break
        audio = generated_voice_like_pcm(2.2) + bytes(int(1.4 * 16000) * 2)
        started = time.monotonic()
        for offset in range(0, len(audio), 640):
            await session.send_realtime_input(audio=audio[offset:offset + 640])
            await asyncio.sleep(0.02)
        async with asyncio.timeout(20):
            async for message in session.receive():
                activity = getattr(message, "voice_activity", None)
                activity_type = str(getattr(activity, "type", ""))
                result["activity_start"] |= "START" in activity_type
                result["activity_end"] |= "END" in activity_type
                content = getattr(message, "server_content", None)
                if content:
                    result["input_transcription"] |= bool(getattr(getattr(content, "input_transcription", None), "text", None))
                    model_turn = getattr(content, "model_turn", None)
                    result["response_after_audio"] |= bool(getattr(model_turn, "parts", None))
                    if getattr(content, "turn_complete", False):
                        break
        result["response_seconds_after_send"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

