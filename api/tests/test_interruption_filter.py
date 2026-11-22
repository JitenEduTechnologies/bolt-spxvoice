import pytest
from api.services.livekit.interruption_filter import (
    InterruptionDecision,
    InterruptionFilter,
)


def test_transient_noise_is_filtered():
    filter_ = InterruptionFilter(min_duration_seconds=0.45)
    # 0.10s cough with no text
    decision = filter_.evaluate(duration_seconds=0.10, transcript="")
    assert decision == InterruptionDecision.NOISE
    assert filter_.stats["noise_filtered"] == 1


def test_english_backchannel_words_filtered():
    filter_ = InterruptionFilter()

    # Short acoustic nods should not interrupt agent
    for word in ["yeah", "okay", "ok", "uh-huh", "mhm", "got it", "right", "sure"]:
        decision = filter_.evaluate(duration_seconds=0.35, transcript=word)
        assert decision == InterruptionDecision.BACKCHANNEL, f"Failed for {word}"

    assert filter_.stats["backchannels"] == 8
    assert filter_.stats["interruptions"] == 0


def test_hindi_backchannel_words_filtered():
    filter_ = InterruptionFilter()

    for phrase in ["ha", "haan", "theek", "theek hai", "achha", "sahi", "ji haan"]:
        decision = filter_.evaluate(duration_seconds=0.40, transcript=phrase)
        assert decision == InterruptionDecision.BACKCHANNEL, f"Failed for {phrase}"

    assert filter_.stats["backchannels"] == 7
    assert filter_.stats["interruptions"] == 0


def test_telugu_backchannel_words_filtered():
    filter_ = InterruptionFilter()

    for phrase in ["avunu", "sare", "alage", "alaage", "anthey", "cheppandi", "haa"]:
        decision = filter_.evaluate(duration_seconds=0.40, transcript=phrase)
        assert decision == InterruptionDecision.BACKCHANNEL, f"Failed for {phrase}"

    assert filter_.stats["backchannels"] == 7
    assert filter_.stats["interruptions"] == 0


def test_code_mixed_backchannel_particles():
    filter_ = InterruptionFilter()

    # Mixed Hinglish / Tenglish
    for phrase in ["yeah theek hai", "okay avunu", "ha okay"]:
        decision = filter_.evaluate(duration_seconds=0.45, transcript=phrase)
        assert decision == InterruptionDecision.BACKCHANNEL, f"Failed for {phrase}"


def test_genuine_barge_in_on_full_statement():
    filter_ = InterruptionFilter()

    decision = filter_.evaluate(
        duration_seconds=1.2,
        transcript="Wait, what is the price for the plan?",
    )
    assert decision == InterruptionDecision.GENUINE_BARGE_IN
    assert filter_.stats["interruptions"] == 1


def test_genuine_barge_in_on_hindi_and_telugu_questions():
    filter_ = InterruptionFilter()

    d_hi = filter_.evaluate(
        duration_seconds=1.1,
        transcript="Rukiye mujhe fees ke baare mein bataiye",
    )
    assert d_hi == InterruptionDecision.GENUINE_BARGE_IN

    d_te = filter_.evaluate(
        duration_seconds=1.3,
        transcript="Aagandi, naku course details kavali",
    )
    assert d_te == InterruptionDecision.GENUINE_BARGE_IN

    assert filter_.stats["interruptions"] == 2


def test_disabled_backchannel_filter_treats_all_as_barge_in():
    filter_ = InterruptionFilter(backchannel_filter_enabled=False)

    decision = filter_.evaluate(duration_seconds=0.6, transcript="yeah")
    assert decision == InterruptionDecision.GENUINE_BARGE_IN


def test_custom_backchannel_words():
    filter_ = InterruptionFilter(custom_backchannel_words={"pakka", "bilkul"})

    assert filter_.evaluate(0.3, "pakka") == InterruptionDecision.BACKCHANNEL
    assert filter_.evaluate(0.3, "bilkul") == InterruptionDecision.BACKCHANNEL

