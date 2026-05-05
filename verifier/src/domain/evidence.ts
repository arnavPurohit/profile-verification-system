export interface Evidence {
  signal: string;
  weight: number;
  value: string | number | boolean | null;
  note?: string;
}
