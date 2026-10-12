import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { escapeHtml, formatDate, formatValue } from "../format";
import type { ChartData } from "../types";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const PALETTE = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#4d7c0f"];

export default function Chart({ data, grain }: { data: ChartData; grain: string }) {
  const { t, i18n } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const lang = i18n.language;

  useEffect(() => {
    if (!ref.current || data.status !== "ok") return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const isPeriod = data.dimension === "__period__";
    const horizontal = data.type === "hbar";
    const first = data.series[0]?.points ?? [];
    const cats = first.map((p) => (isPeriod ? formatDate(p.x, lang, grain) : p.x));
    const fmt = (v: string | null, cur: string | null) => formatValue(v, lang, data.unit, cur, data.decimals) ?? t("dashboard.unavailable");
    const series = data.series.map((s, i) => {
      const values = s.points.map((p) => (p.y === null ? null : Number(p.y))); // Number : rendu graphique uniquement
      return {
        name: s.currency ?? data.title,
        type: data.type === "line" ? "line" : "bar",
        data: values,
        connectNulls: false, // a missing period stays a gap, never a zero
        showSymbol: true,
        itemStyle: { color: PALETTE[i % PALETTE.length] },
        barMaxWidth: 36,
      };
    });
    const valueAxis = {
      type: "value" as const,
      axisLabel: { formatter: (v: number) => formatValue(String(v), lang, data.unit === "currency" ? "number" : data.unit, null, 0) ?? "" },
    };
    const catAxis = {
      type: "category" as const,
      data: cats,
      inverse: horizontal,
      axisLabel: { width: horizontal ? 120 : 80, overflow: "truncate" as const, interval: isPeriod ? "auto" : 0, rotate: !horizontal && cats.length > 6 ? 30 : 0 },
    };
    chart.setOption({
      animation: false,
      color: PALETTE,
      grid: { left: horizontal ? 130 : 56, right: 16, top: data.series.length > 1 ? 32 : 12, bottom: 48, containLabel: false },
      legend: data.series.length > 1 ? { top: 0 } : undefined,
      tooltip: {
        trigger: "axis",
        // HTML content built only from escaped text
        formatter: (params: unknown) => {
          const list = (Array.isArray(params) ? params : [params]) as { dataIndex: number; seriesIndex: number; marker: string }[];
          if (!list.length) return "";
          const idx = list[0].dataIndex;
          const head = escapeHtml(cats[idx] ?? "");
          const lines = list.map((p) => {
            const s = data.series[p.seriesIndex];
            const pt = s.points[idx];
            const val = pt?.y === null ? `${t("dashboard.unavailable")}${pt?.reason ? ` (${t(`reasons.${pt.reason}`)})` : ""}` : fmt(pt.y, s.currency);
            return `${p.marker}${escapeHtml(s.currency ?? "")} ${escapeHtml(val)}`;
          });
          return [head, ...lines].join("<br/>");
        },
      },
      xAxis: horizontal ? valueAxis : catAxis,
      yAxis: horizontal ? catAxis : valueAxis,
      series,
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [data, lang, grain, t]);

  if (data.status !== "ok") {
    return (
      <div className="chart-empty">
        {t("dashboard.unavailable")} — {t(`reasons.${data.reason ?? "no_values"}`)}
      </div>
    );
  }
  const hidden = data.series[0]?.others_hidden ?? 0;
  const h = data.type === "hbar" ? Math.max(220, (data.series[0]?.points.length ?? 5) * 28 + 60) : 280;
  return (
    <>
      <div ref={ref} className="chart" style={{ height: h }} role="img" aria-label={data.title} />
      {hidden > 0 && <p className="muted small">{t("dashboard.others_hidden", { count: hidden })}</p>}
    </>
  );
}
