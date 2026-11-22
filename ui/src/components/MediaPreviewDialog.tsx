'use client';

import { Headphones, Loader2 } from 'lucide-react';
import posthog from 'posthog-js';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { PostHogEvent } from '@/constants/posthog-events';
import { downloadFile, getSignedUrl } from '@/lib/files';

export function MediaPreviewDialog() {
    const [isOpen, setIsOpen] = useState(false);
    const [audioSignedUrl, setAudioSignedUrl] = useState<string | null>(null);
    const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [recordingKey, setRecordingKey] = useState<string | null>(null);
    const [transcriptKey, setTranscriptKey] = useState<string | null>(null);
    const [mediaLoading, setMediaLoading] = useState(false);
    const [playbackRate, setPlaybackRate] = useState(1);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    // Keep the chosen speed when a new recording is loaded into the player.
    useEffect(() => {
        if (audioRef.current) audioRef.current.playbackRate = playbackRate;
    }, [playbackRate, audioSignedUrl]);

    const openPreview = useCallback(
        async (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => {
            if (!recordingUrl && !transcriptUrl) return;
            setMediaLoading(true);
            setAudioSignedUrl(null);
            setTranscriptContent(null);
            setRecordingKey(recordingUrl);
            setTranscriptKey(transcriptUrl);
            setSelectedRunId(runId);
            setIsOpen(true);

            const [audioResult, transcriptResult] = await Promise.all([
                recordingUrl ? getSignedUrl(recordingUrl) : null,
                transcriptUrl ? getSignedUrl(transcriptUrl, true) : null,
            ]);

            if (audioResult) {
                setAudioSignedUrl(audioResult);
            }

            if (transcriptResult) {
                try {
                    const response = await fetch(transcriptResult);
                    const text = await response.text();
                    setTranscriptContent(text);
                    posthog.capture(PostHogEvent.TRANSCRIPT_VIEWED, {
                        run_id: runId,
                        source: 'media_preview_dialog',
                        transcript_length: text.length,
                    });
                } catch (error) {
                    console.error('Error fetching transcript:', error);
                }
            }

            setMediaLoading(false);
        },
        [],
    );

    /** Play a short-lived URL that the server already authorised and verified. */
    const openPreviewUrl = useCallback((url: string, runId: number) => {
        setMediaLoading(false);
        setAudioSignedUrl(url);
        setTranscriptContent(null);
        setRecordingKey(null);
        setTranscriptKey(null);
        setSelectedRunId(runId);
        setIsOpen(true);
    }, []);

    return {
        openPreview,
        openPreviewUrl,
        dialog: (
            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            Run Preview
                            {selectedRunId && ` - Run #${selectedRunId}`}
                        </DialogTitle>
                    </DialogHeader>

                    {mediaLoading && (
                        <div className="flex items-center justify-center py-8 space-x-2">
                            <Loader2 className="h-6 w-6 animate-spin" />
                            <span>Loading...</span>
                        </div>
                    )}

                    {!mediaLoading && audioSignedUrl && (
                        <div className="mt-4 space-y-2">
                        <audio
                            ref={audioRef}
                            src={audioSignedUrl}
                            controls
                            autoPlay
                            className="w-full"
                            onLoadedMetadata={() => {
                                if (audioRef.current) audioRef.current.playbackRate = playbackRate;
                            }}
                            onPlay={() => posthog.capture(PostHogEvent.RECORDING_PLAYED, {
                                run_id: selectedRunId,
                                source: 'media_preview_dialog',
                            })}
                        />
                        <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            Speed
                            <select
                                aria-label="Playback speed"
                                className="rounded border bg-background px-1.5 py-0.5 text-foreground"
                                value={playbackRate}
                                onChange={(e) => setPlaybackRate(Number(e.target.value))}
                            >
                                {[0.75, 1, 1.25, 1.5, 2].map((r) => (
                                    <option key={r} value={r}>
                                        {r}x
                                    </option>
                                ))}
                            </select>
                        </label>
                        </div>
                    )}

                    {!mediaLoading && transcriptContent && (
                        <pre className="w-full h-[60vh] overflow-auto border rounded-md mt-4 p-4 bg-muted text-sm whitespace-pre-wrap font-mono">
                            {transcriptContent}
                        </pre>
                    )}

                    {!mediaLoading && !audioSignedUrl && !transcriptContent && (
                        <div className="flex items-center justify-center py-8 text-muted-foreground">
                            No recording or transcript available.
                        </div>
                    )}

                    <DialogFooter className="pt-4">
                        <DialogClose asChild>
                            <Button variant="secondary">Close</Button>
                        </DialogClose>
                        <div className="flex gap-2">
                            {recordingKey && (
                                <Button variant="outline" onClick={() => downloadFile(recordingKey)}>
                                    Download Recording
                                </Button>
                            )}
                            {transcriptKey && (
                                <Button variant="outline" onClick={() => downloadFile(transcriptKey)}>
                                    Download Transcript
                                </Button>
                            )}
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        ),
    };
}

interface MediaPreviewButtonProps {
    recordingUrl: string | null | undefined;
    transcriptUrl: string | null | undefined;
    runId: number;
    onOpenPreview: (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => void;
    onSelect?: (runId: number) => void;
}

export function MediaPreviewButton({
    recordingUrl,
    transcriptUrl,
    runId,
    onOpenPreview,
    onSelect,
}: MediaPreviewButtonProps) {
    if (!recordingUrl && !transcriptUrl) {
        return (
            <Button
                variant="ghost"
                size="icon"
                disabled
                title="No audio recording or transcript captured for this run"
                aria-label="No audio recording available"
                className="opacity-35 cursor-not-allowed h-8 w-8"
            >
                <Headphones className="h-3.5 w-3.5 text-muted-foreground" />
            </Button>
        );
    }

    const handleOpen = () => {
        onSelect?.(runId);
        onOpenPreview(recordingUrl ?? null, transcriptUrl ?? null, runId);
    };

    return (
        <Button
            variant="outline"
            size="icon"
            onClick={handleOpen}
            title={recordingUrl ? "Listen to call recording & view transcript" : "View conversation transcript"}
            aria-label={recordingUrl ? "Listen to call recording" : "View transcript"}
            className="h-8 w-8 hover:border-primary/50 transition-colors"
        >
            <Headphones className="h-3.5 w-3.5 text-primary" />
        </Button>
    );
}
