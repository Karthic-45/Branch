import { useEffect, useState } from "react";
import { fetchConfig } from "../lib/api";
import type { Config } from "../lib/types";

export function useConfig(): Config | null {
  const [config, setConfig] = useState<Config | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchConfig()
      .then((c) => { if (!cancelled) setConfig(c); })
      .catch(() => { /* system strip just stays empty; not fatal */ });
    return () => { cancelled = true; };
  }, []);

  return config;
}
