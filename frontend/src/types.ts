export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string;
  progress: number;
  result: Record<string, unknown> | null;
  error: { code: string; params: Record<string, unknown> } | null;
}

export interface ColumnProfile {
  id: string;
  name: string;
  type: string;
  convention?: string | null;
  date_order?: string | null;
  currency?: string | null;
  currencies?: string[];
  roles: string[];
  nulls: number;
  invalid: number;
  invalid_samples?: { row: number; raw?: string }[];
  formula_missing: number;
  ocr_low: number;
  distinct: number;
  min?: string;
  max?: string;
  samples: string[];
  top?: { value: string; count: number }[];
  cancel_values?: string[];
}

export interface Note {
  code: string;
  params: Record<string, unknown>;
}

export interface TableQuestion {
  key: string;
  kind: string;
  params: Record<string, unknown>;
}

export interface TableInfo {
  id: string;
  name: string;
  kind: string;
  source_file_id: string | null;
  version: number;
  n_rows: number;
  n_cols: number;
  columns: ColumnProfile[];
  coverage: Record<string, { min: string; max: string; months: number; missing_months: string[] }>;
  duplicates: { groups: number; rows: number };
  excluded: Record<string, number>;
  notes: Note[];
  questions: TableQuestion[];
  header_row: number;
  has_images: boolean;
}

export interface SourceFile {
  id: string;
  name: string;
  size: number;
  ext: string;
  status: string;
  error: { code: string; params: Record<string, unknown> } | null;
  options: Record<string, unknown>;
}

export interface Project {
  id: string;
  name: string;
  state: Record<string, unknown>;
  files: SourceFile[];
  tables: TableInfo[];
  active_jobs: Job[];
  plans: { id: string; status: string; origin: string; table_id: string; request: { text: string } | null }[];
}

export interface Question {
  key: string;
  kind: string;
  params: Record<string, unknown>;
  options: string[];
}

export interface PlanIssue {
  code: string;
  severity: string;
  metric?: string | null;
  params: Record<string, unknown>;
}

export interface AggJson {
  kind: "agg";
  fn: string;
  field: string | null;
  per: string | null;
  filters: FilterJson[];
}
export interface ConstJson {
  kind: "const";
  value: string;
}
export interface OpJson {
  kind: "op";
  op: string;
  left: ExprJson;
  right: ExprJson;
}
export type ExprJson = AggJson | ConstJson | OpJson;

export interface FilterJson {
  field: string;
  op: string;
  values: string[];
}

export interface MetricJson {
  id: string;
  label: string;
  unit: string;
  expression: ExprJson;
  filters: FilterJson[];
  compare: string;
  rate_rule?: string | null;
  decimals?: number | null;
}

export interface PlanJson {
  metrics: MetricJson[];
  charts: { id: string; type: string; metric: string; dimension: string; top_n: number | null; title: string }[];
  period_field: string | null;
  time_grain: string;
  filter_dimensions: string[];
  global_filters: FilterJson[];
}

export interface Plan {
  id: string;
  project_id: string;
  table_id: string;
  dataset_version: number;
  plan: PlanJson | null;
  status: string;
  origin: string;
  model: string | null;
  questions: Question[];
  answers: Record<string, string>;
  issues: PlanIssue[];
  request: { text: string; ui_lang: string; mode: string } | null;
}

export interface CardValue {
  currency: string | null;
  value: string | null;
  reason: string | null;
}

export interface Card {
  id: string;
  label: string;
  unit: string;
  decimals: number | null;
  status: string;
  reason?: string;
  values: CardValue[];
  currency_split: boolean;
  rows_in_scope: number;
  nulls_ignored: Record<string, number>;
  variation?: {
    status: string;
    reason?: string;
    rule?: string;
    current?: string[];
    previous?: string[];
    values?: { currency: string | null; current: string | null; previous: string | null; delta?: string | null; rate?: string | null; status: string; reason?: string }[];
  };
}

export interface ChartData {
  id: string;
  type: string;
  metric: string;
  title: string;
  unit: string;
  dimension: string;
  dimension_label: string;
  decimals: number | null;
  status: string;
  reason?: string;
  series: { currency: string | null; points: { x: string; y: string | null; reason: string | null }[]; others_hidden: number }[];
}

export interface Detail {
  columns: { id: string; label: string; type: string }[];
  rows: { row: number; src: string; values: unknown[] }[];
  total: number;
  offset: number;
}

export interface Dashboard {
  cards: Card[];
  charts: ChartData[];
  filters: {
    period: { field: string; label: string; min: string; max: string } | null;
    dims: { field: string; label: string; values: { value: string | null; count: number }[] }[];
  };
  active_filters: Filters;
  global_filters: FilterJson[];
  rows_in_scope: number;
  coverage: string[] | null;
  detail: Detail;
  plan: PlanJson;
  dataset_version: number;
  quality: {
    excluded_rows: Record<string, number>;
    duplicates: number;
    columns: { id: string; name: string; invalid: number; nulls: number; formula_missing: number; ocr_low: number; convention?: string | null; date_order?: string | null; currency?: string | null; currencies: string[] }[];
    notes: Note[];
  };
  issues: PlanIssue[];
}

export interface Filters {
  period_from?: string | null;
  period_to?: string | null;
  dims?: Record<string, string[]>;
}

export interface Meta {
  version: string;
  languages: string[];
  formats: string[];
  limits: Record<string, number>;
  ai: { provider: string; reachable: boolean; model: string; model_present: boolean; test_double?: boolean };
  ocr: { available: boolean };
  worker: boolean;
  examples: { name: string; size: number }[];
}
