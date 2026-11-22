import asyncio
import copy
from typing import Any, AsyncGenerator, Dict, List, Optional
from pipecat.frames.frames import (
    Frame, EndFrame, CancelFrame, StartFrame, SystemFrame, TextFrame,
    LLMTextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    FunctionCallsStartedFrame, FunctionCallInProgressFrame,
    FunctionCallResultFrame, EndTaskFrame, FunctionCallFromLLM,
    TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
)
try:
    from pipecat.frames.frames import FunctionCallsFromLLMInfoFrame
except ImportError:
    class FunctionCallsFromLLMInfoFrame(Frame):
        pass

from pipecat.services.llm_service import LLMService, FunctionCallParams
from pipecat.services.tts_service import TTSService
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from .mock_transport import MockTransport

class CaptureProcessor(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.frames: List[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)

class SleepFrame(Frame):
    def __init__(self, sleep: float = 0.0):
        super().__init__()
        self.sleep = sleep

async def run_test(processor_or_pipeline, frames_to_send=None, expected_down_frames=None, **kwargs):
    capture_down = CaptureProcessor()
    capture_up = CaptureProcessor()
    
    if isinstance(processor_or_pipeline, Pipeline):
        from pipecat.pipeline.pipeline import PipelineSource, PipelineSink
        processors = [
            p for p in processor_or_pipeline.processors
            if not isinstance(p, (PipelineSource, PipelineSink))
        ]
        pipeline = Pipeline([capture_up] + processors + [capture_down])
    elif isinstance(processor_or_pipeline, list):
        processors = [capture_up] + list(processor_or_pipeline) + [capture_down]
        pipeline = Pipeline(processors)
    else:
        pipeline = Pipeline([capture_up, processor_or_pipeline, capture_down])
        
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
    
    from pipecat.pipeline.runner import PipelineRunner
    runner = PipelineRunner()

    async def feeder():
        if frames_to_send:
            for f in frames_to_send:
                if isinstance(f, SleepFrame):
                    await asyncio.sleep(f.sleep)
                else:
                    await task.queue_frame(f)
                    await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        await task.queue_frame(EndFrame())
        
    async def runner_proc():
        try:
            await runner.run(task)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
            
    try:
        await asyncio.wait_for(
            asyncio.gather(runner_proc(), feeder(), return_exceptions=True),
            timeout=kwargs.get("timeout", 10.0),
        )
    except asyncio.TimeoutError:
        await task.cancel()
    
    down_frames = capture_down.frames
    up_frames = capture_up.frames
    return down_frames, up_frames

class MockLLMService(LLMService):
    def __init__(self, mock_chunks=None, mock_steps=None, chunk_delay=0.001, api_key=None, **kwargs):
        if "settings" not in kwargs:
            from pipecat.services.llm_service import LLMSettings
            kwargs["settings"] = LLMSettings(
                model="mock-model",
                system_instruction=None,
                temperature=None,
                max_tokens=None,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=None,
                user_turn_completion_config=None,
            )
        super().__init__(**kwargs)
        self.mock_chunks = mock_chunks or []
        self.mock_steps = mock_steps or []
        self.current_step = 0
        self.chunk_delay = chunk_delay
        
    def get_current_step(self) -> int:
        return self.current_step
        
    @staticmethod
    def create_function_call_chunks(function_name: str, arguments: dict, tool_call_id: str = 'call_123'):
        return [{'type': 'function_call', 'name': function_name, 'arguments': arguments, 'tool_call_id': tool_call_id}]
        
    @staticmethod
    def create_multiple_function_call_chunks(functions: list):
        return [{'type': 'function_call', 'name': fn['name'], 'arguments': fn.get('arguments', {}), 'tool_call_id': fn.get('tool_call_id', 'call_123')} for fn in functions]
        
    @staticmethod
    def create_text_chunks(text: str):
        return [{'type': 'text', 'text': text}]
        
    @staticmethod
    def create_mixed_chunks(text: str, function_name: str, arguments: dict, tool_call_id: str = 'call_123'):
        return [
            {'type': 'text', 'text': text},
            {'type': 'function_call', 'name': function_name, 'arguments': arguments, 'tool_call_id': tool_call_id}
        ]

    @staticmethod
    def create_multi_step_responses(
        first_step_chunks: list,
        num_text_steps: int = 1,
        step_prefix: str = "Response",
    ) -> list:
        steps = [first_step_chunks]
        for i in range(1, num_text_steps + 1):
            steps.append([{"type": "text", "text": f"{step_prefix} {i}"}])
        return steps

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        from pipecat.frames.frames import LLMContextFrame, LLMTextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, FunctionCallFromLLM
        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMFullResponseStartFrame(), direction)
            
            chunks_to_play = []
            if self.mock_steps and self.current_step < len(self.mock_steps):
                chunks_to_play = self.mock_steps[self.current_step]
                self.current_step += 1
            elif self.mock_steps:
                chunks_to_play = []
                self.current_step += 1
            elif self.mock_chunks:
                chunks_to_play = self.mock_chunks
                self.current_step += 1
            else:
                chunks_to_play = []
                self.current_step += 1
                
            fn_calls = []
            for chunk in chunks_to_play:
                if chunk.get('type') == 'text':
                    await self.push_frame(LLMTextFrame(chunk['text']), direction)
                elif chunk.get('type') == 'function_call':
                    fn_calls.append(FunctionCallFromLLM(
                        function_name=chunk['name'],
                        tool_call_id=chunk.get('tool_call_id', 'call_123'),
                        arguments=chunk.get('arguments', {}),
                        context=frame.context,
                    ))
                if self.chunk_delay:
                    await asyncio.sleep(self.chunk_delay)
                    
            if fn_calls:
                await self.run_function_calls(fn_calls)
                    
            await self.push_frame(LLMFullResponseEndFrame(), direction)
        else:
            await self.push_frame(frame, direction)

class ContextCapturingMockLLM(MockLLMService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured_contexts: List[Dict[str, Any]] = []
        self.captured_prompts: List[str] = []

    def get_current_step(self) -> int:
        return self.current_step

    def get_system_prompt_at_step(self, step: int) -> str:
        if 0 <= step < len(self.captured_prompts):
            return self.captured_prompts[step]
        return ''

    def get_context_at_step(self, step: int) -> Optional[Dict[str, Any]]:
        if 0 <= step < len(self.captured_contexts):
            return self.captured_contexts[step]
        return None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        from pipecat.frames.frames import LLMContextFrame
        if isinstance(frame, LLMContextFrame):
            context_obj = frame.context
            messages = []
            if hasattr(context_obj, 'get_messages'):
                messages = copy.deepcopy(context_obj.get_messages())
            elif hasattr(context_obj, 'messages'):
                messages = copy.deepcopy(context_obj.messages)
            elif isinstance(context_obj, dict):
                messages = copy.deepcopy(context_obj.get('messages', []))

            sys_prompt = ''
            for msg in messages:
                if isinstance(msg, dict) and msg.get('role') == 'system':
                    sys_prompt = msg.get('content', '')
                    break
            if not sys_prompt and hasattr(self, '_settings') and hasattr(self._settings, 'system_instruction'):
                sys_prompt = str(self._settings.system_instruction or '')

            self.captured_contexts.append({'messages': messages})
            self.captured_prompts.append(sys_prompt)

        await super().process_frame(frame, direction)

class MockTTSService(TTSService):
    def __init__(self, mock_audio_duration_ms=40, frame_delay=0, **kwargs):
        super().__init__(**kwargs)
        self.spoken_texts: List[str] = []
        self.mock_audio_duration_ms = mock_audio_duration_ms
        self.frame_delay = frame_delay

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        self.spoken_texts.append(text)
        yield TTSStartedFrame(context_id=context_id)
        chunk_ms = 20
        chunk_bytes = int(16 * 2 * chunk_ms)
        total_bytes = int(16 * 2 * self.mock_audio_duration_ms)
        offset = 0
        while offset < total_bytes:
            current_chunk = min(chunk_bytes, total_bytes - offset)
            yield TTSAudioRawFrame(
                audio=b'\x00' * current_chunk,
                sample_rate=16000,
                num_channels=1,
                context_id=context_id,
            )
            offset += current_chunk
            if self.frame_delay > 0:
                await asyncio.sleep(self.frame_delay)

    async def _push_tts_frames(self, src_frame, *args, **kwargs):
        await super()._push_tts_frames(src_frame, *args, **kwargs)
        if src_frame.context_id and src_frame.context_id in self._tts_contexts:
            await self.append_to_audio_context(
                src_frame.context_id, TTSStoppedFrame(context_id=src_frame.context_id)
            )
