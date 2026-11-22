"use client";

import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectSeparator,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import type { UnavailableProvider } from "@/lib/unavailableProviders";

interface ProviderSelectProps {
    service: string;
    value: string;
    onChange: (providerName: string) => void;
    available: string[];
    unavailable: UnavailableProvider[];
}

export function ProviderSelect({ service, value, onChange, available, unavailable }: ProviderSelectProps) {
    return (
        <Select value={value} onValueChange={onChange}>
            <SelectTrigger className="w-full">
                <SelectValue placeholder="Select provider" />
            </SelectTrigger>
            <SelectContent>
                {available.map((provider) => (
                    <SelectItem key={provider} value={provider}>
                        {provider}
                    </SelectItem>
                ))}
                {unavailable.length > 0 && (
                    <>
                        <SelectSeparator />
                        {/* Radix requires SelectLabel inside SelectGroup. */}
                        <SelectGroup>
                            <SelectLabel>Not available here</SelectLabel>
                            {unavailable.map((u) => (
                                <SelectItem
                                    key={u.id}
                                    value={`unavailable:${u.id}`}
                                    disabled
                                    data-testid={`unavailable-option-${service}-${u.id}`}
                                >
                                    {u.id}
                                </SelectItem>
                            ))}
                        </SelectGroup>
                    </>
                )}
            </SelectContent>
        </Select>
    );
}
