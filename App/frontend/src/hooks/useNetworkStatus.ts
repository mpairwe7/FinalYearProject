"use client";

import { useEffect, useState } from "react";

export type EffectiveConnectionType = "slow-2g" | "2g" | "3g" | "4g" | "unknown";

interface NetworkInformation extends EventTarget {
  effectiveType?: EffectiveConnectionType;
  saveData?: boolean;
  downlink?: number;
  rtt?: number;
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
}

function getConnection(): NetworkInformation | null {
  if (typeof navigator === "undefined") return null;
  const nav = navigator as Navigator & {
    connection?: NetworkInformation;
    mozConnection?: NetworkInformation;
    webkitConnection?: NetworkInformation;
  };
  return nav.connection || nav.mozConnection || nav.webkitConnection || null;
}

export function useNetworkStatus() {
  const [isOnline, setIsOnline] = useState<boolean>(
    typeof navigator !== "undefined" ? navigator.onLine : true
  );
  const [effectiveType, setEffectiveType] = useState<EffectiveConnectionType>("unknown");
  const [saveData, setSaveData] = useState<boolean>(false);
  const [downlink, setDownlink] = useState<number | null>(null);
  const [rtt, setRtt] = useState<number | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const syncStatus = () => {
      setIsOnline(navigator.onLine);
      const conn = getConnection();
      if (conn) {
        setEffectiveType(conn.effectiveType || "unknown");
        setSaveData(Boolean(conn.saveData));
        setDownlink(typeof conn.downlink === "number" ? conn.downlink : null);
        setRtt(typeof conn.rtt === "number" ? conn.rtt : null);
      }
    };

    syncStatus();

    const handleOnline = () => syncStatus();
    const handleOffline = () => syncStatus();

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    const conn = getConnection();
    if (conn) {
      conn.addEventListener("change", syncStatus);
    }

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      if (conn) {
        conn.removeEventListener("change", syncStatus);
      }
    };
  }, []);

  const isLowBandwidth =
    saveData || effectiveType === "slow-2g" || effectiveType === "2g";

  return {
    isOnline,
    isOffline: !isOnline,
    effectiveType,
    saveData,
    isLowBandwidth,
    downlink,
    rtt,
  };
}
