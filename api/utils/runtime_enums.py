from enum import Enum


class RealtimeFeedbackType(str, Enum):
    USER_TRANSCRIPTION = "rtf-user-transcription"
    BOT_TEXT = "rtf-bot-text"
    FUNCTION_CALL_START = "rtf-function-call-start"
    FUNCTION_CALL_END = "rtf-function-call-end"
    LATENCY_MEASURED = "rtf-latency-measured"
    TTFB_METRIC = "rtf-ttfb-metric"
    PIPELINE_ERROR = "rtf-pipeline-error"
    NODE_TRANSITION = "rtf-node-transition"
    BOT_STARTED_SPEAKING = "rtf-bot-started-speaking"
    BOT_STOPPED_SPEAKING = "rtf-bot-stopped-speaking"
    USER_MUTE_STARTED = "rtf-user-mute-started"
    USER_MUTE_STOPPED = "rtf-user-mute-stopped"


class EndTaskReason(str, Enum):
    USER_HANGUP = "user_hangup"
    USER_QUALIFIED = "user_qualified"
    END_CALL_TOOL_REASON = "end_call_tool"
    UNEXPECTED_ERROR = "unexpected_error"
    TRANSFER_CALL = "transfer_call"
    USER_IDLE_MAX_DURATION_EXCEEDED = "user_idle_max_duration_exceeded"
    CALL_DURATION_EXCEEDED = "call_duration_exceeded"
    PIPELINE_ERROR = "pipeline_error"
    VOICEMAIL_DETECTED = "voicemail_detected"
