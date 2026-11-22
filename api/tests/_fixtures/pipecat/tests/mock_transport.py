import asyncio
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelTaskFrame,
    EndFrame,
    Frame,
    OutputAudioRawFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import BaseTransport, TransportParams


class MockTransportInput(FrameProcessor):
    def __init__(self, transport: "MockTransport", params: TransportParams = None, **kwargs):
        super().__init__(**kwargs)
        self._transport = transport
        self._params = params or TransportParams()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            # Notify event handlers that client is connected
            await self._transport._call_event_handler("on_client_connected", None)
        await self.push_frame(frame, direction)


class MockTransportOutput(FrameProcessor):
    def __init__(
        self,
        transport: "MockTransport",
        params: TransportParams = None,
        audio_write_succeeds: bool = True,
        fail_after_n_frames: int = 0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._transport = transport
        self._params = params or TransportParams()
        self._audio_write_succeeds = audio_write_succeeds
        self._fail_after_n_frames = fail_after_n_frames
        self._write_attempts: int = 0
        self._frames_written: int = 0
        self._consecutive_failures: int = 0
        self._bot_speaking: bool = False

    async def write_audio_frame(self, frame: Frame) -> bool:
        self._write_attempts += 1
        if self._audio_write_succeeds:
            self._frames_written += 1
            self._consecutive_failures = 0
            return True

        if self._fail_after_n_frames > 0 and self._frames_written < self._fail_after_n_frames:
            self._frames_written += 1
            self._consecutive_failures = 0
            return True

        self._consecutive_failures += 1
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (OutputAudioRawFrame, TTSAudioRawFrame)):
            success = await self.write_audio_frame(frame)
            if success:
                if not self._bot_speaking:
                    self._bot_speaking = True
                    await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
                    await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
                await self.push_frame(frame, direction)
            else:
                max_failures = getattr(self._params, "audio_out_max_consecutive_failures", 2)
                if self._consecutive_failures == max_failures:
                    self._bot_speaking = False
                    await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
                    await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
                    await self.push_frame(CancelTaskFrame(), FrameDirection.UPSTREAM)
                return
        elif isinstance(frame, TTSStoppedFrame):
            if self._bot_speaking:
                self._bot_speaking = False
                await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
                await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await self.push_frame(frame, direction)
        elif isinstance(frame, EndFrame):
            if self._bot_speaking:
                self._bot_speaking = False
                await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
                await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)


class MockTransport(BaseTransport):
    def __init__(
        self,
        params: TransportParams = None,
        generate_audio: bool = False,
        audio_write_succeeds: bool = True,
        fail_after_n_frames: int = 0,
        **kwargs,
    ):
        super().__init__(name=kwargs.get("name"))
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")
        self._params = params or TransportParams()
        self._generate_audio = generate_audio
        self._audio_write_succeeds = audio_write_succeeds
        self._fail_after_n_frames = fail_after_n_frames
        self._input = MockTransportInput(self, params=self._params)
        self._output = MockTransportOutput(
            self,
            params=self._params,
            audio_write_succeeds=audio_write_succeeds,
            fail_after_n_frames=fail_after_n_frames,
        )

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output

    async def queue_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.UPSTREAM):
        await self._input.push_frame(frame, direction)
