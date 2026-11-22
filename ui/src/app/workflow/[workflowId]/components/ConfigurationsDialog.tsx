import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { AmbientNoiseConfiguration, TurnStopStrategy, WorkflowConfigurations } from "@/types/workflow-configurations";

interface ConfigurationsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowConfigurations: WorkflowConfigurations | null;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}

const DEFAULT_AMBIENT_NOISE_CONFIG: AmbientNoiseConfiguration = {
    enabled: false,
    volume: 0.3,
};

export const ConfigurationsDialog = ({
    open,
    onOpenChange,
    workflowConfigurations,
    workflowName,
    onSave
}: ConfigurationsDialogProps) => {
    const [name, setName] = useState<string>(workflowName);
    const [ambientNoiseConfig, setAmbientNoiseConfig] = useState<AmbientNoiseConfiguration>(
        workflowConfigurations?.ambient_noise_configuration || DEFAULT_AMBIENT_NOISE_CONFIG
    );
    const [maxCallDuration, setMaxCallDuration] = useState<number>(
        workflowConfigurations?.max_call_duration || 600  // Default 10 minutes
    );
    const [maxUserIdleTimeout, setMaxUserIdleTimeout] = useState<number>(
        workflowConfigurations?.max_user_idle_timeout || 10  // Default 10 seconds
    );
    const [smartTurnStopSecs, setSmartTurnStopSecs] = useState<number>(
        workflowConfigurations?.smart_turn_stop_secs || 2  // Default 2 seconds
    );
    const [turnStopStrategy, setTurnStopStrategy] = useState<TurnStopStrategy>(
        workflowConfigurations?.turn_stop_strategy || 'transcription'
    );
    const [contextCompactionEnabled, setContextCompactionEnabled] = useState<boolean>(
        workflowConfigurations?.context_compaction_enabled ?? false
    );
    const [silenceStage1, setSilenceStage1] = useState<number>(
        workflowConfigurations?.silence_config?.stage1_seconds ?? workflowConfigurations?.silence_stage1_seconds ?? 4.0
    );
    const [silenceStage2, setSilenceStage2] = useState<number>(
        workflowConfigurations?.silence_config?.stage2_seconds ?? workflowConfigurations?.silence_stage2_seconds ?? 8.0
    );
    const [silenceStage3, setSilenceStage3] = useState<number>(
        workflowConfigurations?.silence_config?.stage3_seconds ?? workflowConfigurations?.silence_stage3_seconds ?? 14.0
    );
    const [interruptionMinDuration, setInterruptionMinDuration] = useState<number>(
        workflowConfigurations?.interruption_config?.min_duration_seconds ?? workflowConfigurations?.min_interruption_duration ?? 0.45
    );
    const [interruptionMinWords, setInterruptionMinWords] = useState<number>(
        workflowConfigurations?.interruption_config?.min_words ?? workflowConfigurations?.min_interruption_words ?? 2
    );
    const [backchannelFilterEnabled, setBackchannelFilterEnabled] = useState<boolean>(
        workflowConfigurations?.interruption_config?.backchannel_filter_enabled ?? workflowConfigurations?.backchannel_filter_enabled ?? true
    );
    const [isSaving, setIsSaving] = useState(false);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave({
                ...workflowConfigurations,
                ambient_noise_configuration: ambientNoiseConfig,
                max_call_duration: maxCallDuration,
                max_user_idle_timeout: maxUserIdleTimeout,
                smart_turn_stop_secs: smartTurnStopSecs,
                turn_stop_strategy: turnStopStrategy,
                context_compaction_enabled: contextCompactionEnabled,
                silence_config: {
                    stage1_seconds: silenceStage1,
                    stage2_seconds: silenceStage2,
                    stage3_seconds: silenceStage3,
                    custom_prompts: workflowConfigurations?.silence_config?.custom_prompts,
                },
                interruption_config: {
                    min_duration_seconds: interruptionMinDuration,
                    min_words: interruptionMinWords,
                    backchannel_filter_enabled: backchannelFilterEnabled,
                    custom_backchannel_words: workflowConfigurations?.interruption_config?.custom_backchannel_words,
                },
            }, name);
            onOpenChange(false);
        } catch (error) {
            console.error("Failed to save configurations:", error);
        } finally {
            setIsSaving(false);
        }
    };

    // Sync state with props when dialog opens
    useEffect(() => {
        if (open) {
            setName(workflowName);
            setAmbientNoiseConfig(workflowConfigurations?.ambient_noise_configuration || DEFAULT_AMBIENT_NOISE_CONFIG);
            setMaxCallDuration(workflowConfigurations?.max_call_duration || 600);
            setMaxUserIdleTimeout(workflowConfigurations?.max_user_idle_timeout || 10);
            setSmartTurnStopSecs(workflowConfigurations?.smart_turn_stop_secs || 2);
            setTurnStopStrategy(workflowConfigurations?.turn_stop_strategy || 'transcription');
            setContextCompactionEnabled(workflowConfigurations?.context_compaction_enabled ?? false);
            setSilenceStage1(workflowConfigurations?.silence_config?.stage1_seconds ?? workflowConfigurations?.silence_stage1_seconds ?? 4.0);
            setSilenceStage2(workflowConfigurations?.silence_config?.stage2_seconds ?? workflowConfigurations?.silence_stage2_seconds ?? 8.0);
            setSilenceStage3(workflowConfigurations?.silence_config?.stage3_seconds ?? workflowConfigurations?.silence_stage3_seconds ?? 14.0);
            setInterruptionMinDuration(workflowConfigurations?.interruption_config?.min_duration_seconds ?? workflowConfigurations?.min_interruption_duration ?? 0.45);
            setInterruptionMinWords(workflowConfigurations?.interruption_config?.min_words ?? workflowConfigurations?.min_interruption_words ?? 2);
            setBackchannelFilterEnabled(workflowConfigurations?.interruption_config?.backchannel_filter_enabled ?? workflowConfigurations?.backchannel_filter_enabled ?? true);
        }
    }, [open, workflowName, workflowConfigurations]);

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Configurations</DialogTitle>
                </DialogHeader>

                <div className="space-y-6">
                    {/* Workflow Name Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Agent Name</h3>
                            <p className="text-xs text-muted-foreground">
                                The name of your agent
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="workflow_name" className="text-xs">
                                Name
                            </Label>
                            <Input
                                id="workflow_name"
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="Enter Agent name"
                            />
                        </div>
                    </div>

                    {/* Ambient Noise Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Ambient Noise</h3>
                            <p className="text-xs text-muted-foreground">
                                Add background office ambient noise to make the conversation sound more natural.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <Label htmlFor="ambient-noise-enabled" className="text-sm">
                                    Use Ambient Noise
                                </Label>
                                <Switch
                                    id="ambient-noise-enabled"
                                    checked={ambientNoiseConfig.enabled}
                                    onCheckedChange={(checked) =>
                                        setAmbientNoiseConfig(prev => ({ ...prev, enabled: checked }))
                                    }
                                />
                            </div>

                            {ambientNoiseConfig.enabled && (
                                <div className="space-y-2">
                                    <Label htmlFor="ambient-volume" className="text-xs">
                                        Volume
                                    </Label>
                                    <Input
                                        id="ambient-volume"
                                        type="number"
                                        step="0.1"
                                        min="0"
                                        max="1"
                                        value={ambientNoiseConfig.volume}
                                        onChange={(e) => {
                                            const value = parseFloat(e.target.value);
                                            if (!isNaN(value)) {
                                                setAmbientNoiseConfig(prev => ({ ...prev, volume: value }));
                                            }
                                        }}
                                    />
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Turn Detection Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Turn Detection</h3>
                            <p className="text-xs text-muted-foreground">
                                Configure how the agent detects when the user has finished speaking.
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="turn_stop_strategy" className="text-xs">
                                Detection Strategy
                            </Label>
                            <Select
                                value={turnStopStrategy}
                                onValueChange={(value: TurnStopStrategy) => setTurnStopStrategy(value)}
                            >
                                <SelectTrigger id="turn_stop_strategy">
                                    <SelectValue placeholder="Select strategy" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="transcription">
                                        Transcription-based
                                    </SelectItem>
                                    <SelectItem value="turn_analyzer">
                                        Smart Turn Analyzer
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                {turnStopStrategy === 'transcription'
                                    ? "Best for short responses (1-2 word statements). Ends turn when transcription indicates completion."
                                    : "Best for longer responses with natural pauses. Uses ML model to detect end of turn."}
                            </p>
                        </div>

                        {turnStopStrategy === 'turn_analyzer' && (
                            <div className="space-y-2">
                                <Label htmlFor="smart_turn_stop_secs" className="text-xs">
                                    Incomplete Turn Timeout (seconds)
                                </Label>
                                <Input
                                    id="smart_turn_stop_secs"
                                    type="number"
                                    step="0.5"
                                    min="0.5"
                                    max="10"
                                    value={smartTurnStopSecs}
                                    onChange={(e) => {
                                        const value = parseFloat(e.target.value);
                                        if (!isNaN(value) && value >= 0.5) {
                                            setSmartTurnStopSecs(value);
                                        }
                                    }}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Max silence duration before ending an incomplete turn. Default: 2 seconds
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Context Management Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Context Compaction</h3>
                            <p className="text-xs text-muted-foreground">
                                Automatically summarize conversation context when transitioning between nodes. Removes stale tool calls and keeps the context clean for the new node.
                            </p>
                        </div>

                        <div className="flex items-center justify-between">
                            <Label htmlFor="context-compaction-enabled" className="text-sm">
                                Enable Context Compaction
                            </Label>
                            <Switch
                                id="context-compaction-enabled"
                                checked={contextCompactionEnabled}
                                onCheckedChange={setContextCompactionEnabled}
                            />
                        </div>
                    </div>

                    {/* Call Management Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Call Management</h3>
                            <p className="text-xs text-muted-foreground">
                                Configure call duration limits and idle timeout settings.
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="max_call_duration" className="text-xs">
                                    Max Call Duration (seconds)
                                </Label>
                                <Input
                                    id="max_call_duration"
                                    type="number"
                                    step="1"
                                    min="1"
                                    value={maxCallDuration}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        if (!isNaN(value) && value > 0) {
                                            setMaxCallDuration(value);
                                        }
                                    }}
                                />
                                <p className="text-xs text-muted-foreground">Default: 600 (10 minutes)</p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="max_user_idle_timeout" className="text-xs">
                                    Max User Idle Timeout (seconds)
                                </Label>
                                <Input
                                    id="max_user_idle_timeout"
                                    type="number"
                                    step="1"
                                    min="1"
                                    value={maxUserIdleTimeout}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        if (!isNaN(value) && value > 0) {
                                            setMaxUserIdleTimeout(value);
                                        }
                                    }}
                                />
                                <p className="text-xs text-muted-foreground">Default: 10 seconds</p>
                            </div>
                        </div>
                    </div>

                    {/* Silence Management Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Silence Management</h3>
                            <p className="text-xs text-muted-foreground">
                                Progressive 3-stage silence recovery. Prompts caller gently before clean disconnect.
                            </p>
                        </div>
                        <div className="grid grid-cols-3 gap-3">
                            <div className="space-y-2">
                                <Label htmlFor="silence_stage1" className="text-xs">
                                    Stage 1: Nudge (s)
                                </Label>
                                <Input
                                    id="silence_stage1"
                                    type="number"
                                    step="0.5"
                                    min="1"
                                    max="30"
                                    value={silenceStage1}
                                    onChange={(e) => {
                                        const v = parseFloat(e.target.value);
                                        if (!isNaN(v) && v > 0) setSilenceStage1(v);
                                    }}
                                />
                                <p className="text-[10px] text-muted-foreground">Default: 4.0s</p>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="silence_stage2" className="text-xs">
                                    Stage 2: Clarify (s)
                                </Label>
                                <Input
                                    id="silence_stage2"
                                    type="number"
                                    step="0.5"
                                    min="2"
                                    max="60"
                                    value={silenceStage2}
                                    onChange={(e) => {
                                        const v = parseFloat(e.target.value);
                                        if (!isNaN(v) && v > 0) setSilenceStage2(v);
                                    }}
                                />
                                <p className="text-[10px] text-muted-foreground">Default: 8.0s</p>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="silence_stage3" className="text-xs">
                                    Stage 3: Close (s)
                                </Label>
                                <Input
                                    id="silence_stage3"
                                    type="number"
                                    step="0.5"
                                    min="3"
                                    max="120"
                                    value={silenceStage3}
                                    onChange={(e) => {
                                        const v = parseFloat(e.target.value);
                                        if (!isNaN(v) && v > 0) setSilenceStage3(v);
                                    }}
                                />
                                <p className="text-[10px] text-muted-foreground">Default: 14.0s</p>
                            </div>
                        </div>
                    </div>

                    {/* Interruption & Barge-In Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Interruption & Barge-In</h3>
                            <p className="text-xs text-muted-foreground">
                                Control how sensitive the agent is to caller speech during AI playback.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="interruption_min_duration" className="text-xs">
                                    Min Duration (seconds)
                                </Label>
                                <Input
                                    id="interruption_min_duration"
                                    type="number"
                                    step="0.05"
                                    min="0.1"
                                    max="3"
                                    value={interruptionMinDuration}
                                    onChange={(e) => {
                                        const v = parseFloat(e.target.value);
                                        if (!isNaN(v) && v > 0) setInterruptionMinDuration(v);
                                    }}
                                />
                                <p className="text-xs text-muted-foreground">Default: 0.45s</p>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="interruption_min_words" className="text-xs">
                                    Min Words for Barge-In
                                </Label>
                                <Input
                                    id="interruption_min_words"
                                    type="number"
                                    step="1"
                                    min="1"
                                    max="10"
                                    value={interruptionMinWords}
                                    onChange={(e) => {
                                        const v = parseInt(e.target.value);
                                        if (!isNaN(v) && v > 0) setInterruptionMinWords(v);
                                    }}
                                />
                                <p className="text-xs text-muted-foreground">Default: 2 words</p>
                            </div>
                        </div>

                        <div className="flex items-center justify-between pt-1">
                            <div className="space-y-0.5">
                                <Label htmlFor="backchannel-filter-enabled" className="text-sm">
                                    Backchannel Filter
                                </Label>
                                <p className="text-xs text-muted-foreground">
                                    Suppresses short nods (&quot;yeah&quot;, &quot;theek hai&quot;, &quot;avunu&quot;) from interrupting AI speech.
                                </p>
                            </div>
                            <Switch
                                id="backchannel-filter-enabled"
                                checked={backchannelFilterEnabled}
                                onCheckedChange={setBackchannelFilterEnabled}
                            />
                        </div>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={isSaving}>
                        {isSaving ? "Saving..." : "Save"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

