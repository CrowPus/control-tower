import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api";

export type Kpi = Schemas["InsightKpi"];
export type SeriesOut = Schemas["InsightSeriesOut"];
export type Group = Schemas["InsightGroupOut"];
export type AlertRow = Schemas["InsightAlertOut"];

/** The period lives in the address (?days=30), so it stays the same across tabs and reloads. */
export function usePeriod(name = "days", fallback = 30): [number, (value: number) => void] {
  const [params, setParams] = useSearchParams();
  const value = Number(params.get(name)) || fallback;
  return [
    value,
    (next: number) => {
      const copy = new URLSearchParams(params);
      copy.set(name, String(next));
      setParams(copy, { replace: true });
    },
  ];
}

type GroupPath = "/admin-api/insights/agents" | "/admin-api/insights/money" | "/admin-api/insights/platform";

/** One page's numbers; the last answer stays on screen while the next loads (no flashing). */
export function useGroup(path: GroupPath, days: number) {
  return useQuery({
    queryKey: ["insights", path, days],
    queryFn: async () => unwrap(await api.GET(path, { params: { query: { days } } })),
    placeholderData: keepPreviousData,
    refetchInterval: 5 * 60_000,
  });
}

export function values(out: SeriesOut, metric: string, dimension = ""): number[] {
  return out.series.find((s) => s.metric === metric && s.dimension === dimension)?.values ?? out.days.map(() => 0);
}

export function kpiOf(list: Kpi[], key: string): Kpi | undefined {
  return list.find((k) => k.key === key);
}

export const exportHref = (metric: string, days: number) => `/admin-api/insights/export?metric=${encodeURIComponent(metric)}&days=${days}`;
