"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useAuth } from "@/lib/auth";
import { readSseStream } from "@/lib/sseStream";

export interface DashboardEvent<T = any> {
  id: number;
  organization_id: number;
  event_type: string;
  resource_type: string;
  resource_id: string;
  data: T;
  created_at: string;
}

export interface OwnerActionAck {
  status: "acknowledged";
  action_id: string;
  action_type: string;
  target_id: string;
  execution_status: "SUCCESS" | "FAILED";
  executed_at: string;
  result: Record<string, any>;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "reconnecting";

export function useRealtimeDashboard() {
  const { user, getAccessToken } = useAuth();
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [lastEvent, setLastEvent] = useState<DashboardEvent | null>(null);
  const lastEventIdRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listenersRef = useRef<Map<string, Set<(event: DashboardEvent) => void>>>(new Map());
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Subscribe to specific event types
  const subscribe = useCallback((eventType: string, handler: (event: DashboardEvent) => void) => {
    if (!listenersRef.current.has(eventType)) {
      listenersRef.current.set(eventType, new Set());
    }
    listenersRef.current.get(eventType)!.add(handler);

    return () => {
      listenersRef.current.get(eventType)?.delete(handler);
    };
  }, []);

  // Execute authenticated owner action with two-phase ack
  const executeAction = useCallback(
    async (
      actionType: string,
      targetId: string,
      payload: Record<string, any> = {}
    ): Promise<OwnerActionAck> => {
      const token = await getAccessToken();
      const actionId = `act_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;

      const res = await fetch("/api/v1/business/actions/execute", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          action_id: actionId,
          action_type: actionType,
          target_id: targetId,
          payload,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Action execution failed" }));
        throw new Error(err.detail || "Action execution failed");
      }

      return await res.json();
    },
    [getAccessToken]
  );

  useEffect(() => {
    let isCancelled = false;

    const connectSSE = async () => {
      if (isCancelled) return;
      setStatus("connecting");

      try {
        const jwt = await getAccessToken();
        let sseTicket: string | null = null;
        if (jwt) {
          try {
            const ticketRes = await fetch("/api/v1/business/events/ticket", {
              method: "POST",
              headers: {
                "Authorization": `Bearer ${jwt}`,
                "Content-Type": "application/json",
              },
            });
            if (ticketRes.ok) {
              const ticketData = await ticketRes.json();
              sseTicket = ticketData.ticket;
            }
          } catch (tErr) {
            console.debug("[RealtimeDashboard] Ticket fetch error:", tErr);
          }
        }

        let url = "/api/v1/business/events/stream";
        const params = new URLSearchParams();

        if (sseTicket) {
          params.append("ticket", sseTicket);
        }
        if (lastEventIdRef.current !== null && lastEventIdRef.current > 0) {
          params.append("last_event_id", lastEventIdRef.current.toString());
        }

        const queryString = params.toString();
        if (queryString) {
          url += `?${queryString}`;
        }

        if (isCancelled) return; // unmounted while the token/ticket requests were in flight
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;

        // The server sends NAMED events (`event: <type>`). EventSource.onmessage never receives those and
        // EventSource has no wildcard, so the stream is read directly (see lib/sseStream.ts).
        const handleData = (data: string) => {
          if (isCancelled || !data) return;
          try {
            const parsed: DashboardEvent = JSON.parse(data);
            if (parsed.id) {
              lastEventIdRef.current = parsed.id;
            }
            setLastEvent(parsed);

            // Notify specific listeners
            const specificListeners = listenersRef.current.get(parsed.event_type);
            if (specificListeners) {
              specificListeners.forEach((fn) => fn(parsed));
            }
            // Notify global wildcard listeners
            const allListeners = listenersRef.current.get("*");
            if (allListeners) {
              allListeners.forEach((fn) => fn(parsed));
            }
          } catch (err) {
            console.debug("[RealtimeDashboard] Failed to parse SSE event:", err);
          }
        };

        void readSseStream(url, controller.signal, {
          onOpen: () => {
            if (!isCancelled) setStatus("connected");
          },
          onFrame: (frame) => handleData(frame.data),
        }).catch(() => {
          if (isCancelled || controller.signal.aborted) return;
          setStatus("reconnecting");
          // Reconnect resumes from the last seen event id (server replays what was missed).
          if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = setTimeout(() => {
            connectSSE();
          }, 3000);
        });
      } catch (err) {
        if (!isCancelled) {
          setStatus("disconnected");
          if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = setTimeout(() => {
            connectSSE();
          }, 5000);
        }
      }
    };

    connectSSE();

    // The browser knows before any timer does: reconnect the moment connectivity returns.
    const onOnline = () => {
      if (isCancelled) return;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      void connectSSE();
    };
    window.addEventListener("online", onOnline);

    return () => {
      window.removeEventListener("online", onOnline);
      isCancelled = true;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [user, getAccessToken]);

  return {
    status,
    lastEvent,
    subscribe,
    executeAction,
    isConnected: status === "connected",
  };
}

