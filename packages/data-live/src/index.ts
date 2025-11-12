import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type CallPut = "CALL" | "PUT";
export type LegSide = "SHORT" | "LONG";

export interface OptionQuote {
  symbol: string;
  bid?: number | null;
  ask?: number | null;
  mid?: number | null;
  last?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  iv?: number | null;
  openInterest?: number | null;
  volume?: number | null;
  source?: string;
  updatedAt?: string;
}

export interface SubscribeLeg {
  symbol?: string;
  underlying?: string;
  expiration?: string;
  strike?: number;
  callPut?: CallPut;
  side?: LegSide;
}

export interface SpreadLegDescriptor {
  id: string;
  side: LegSide;
  call_put: CallPut;
  strike: number;
  expiration: string;
  qty: number;
}

export interface LiveLegQuote {
  legId: string;
  side: LegSide;
  callPut: CallPut;
  strike: number;
  expiration: string;
  symbol: string;
  quote?: OptionQuote;
}

export interface SpreadLiveSummary {
  shortLegs: LiveLegQuote[];
  longLegs: LiveLegQuote[];
  underlyingPrice?: number;
  asOf?: string;
  missing?: string[];
}

export interface SpreadMetrics {
  baPct?: number;
  oiOk?: boolean;
  delta?: number;
  ivr?: number;
}

export interface SpreadMetricConfig {
  minOpenInterest: number;
  maxBidAskPct: number;
  idealDelta: number;
  deltaTolerance: number;
}

export interface SubscribeOptions {
  fixturePath?: string;
  preloadFixture?: boolean;
}

export interface SubscribeResult {
  symbols: string[];
  appliedFixture: number;
  cached: number;
  fixturePath?: string;
}

const optionQuotes = new Map<string, OptionQuote>();
const underlyingPrices = new Map<string, { price: number; asOf?: string }>();
const registeredSymbols = new Set<string>();

function tryNumber(value: unknown): number | undefined {
  if (value === null || value === undefined) return undefined;
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : undefined;
}

function toYYMMDD(expiration: string): string {
  const trimmed = expiration.trim();
  if (/^\d{6}$/.test(trimmed)) return trimmed;
  if (/^\d{8}$/.test(trimmed)) return trimmed.slice(2);
  const parts = trimmed.split(/[/-]/);
  if (parts.length === 3) {
    const [year, month, day] = parts;
    return `${year.slice(-2)}${month.padStart(2, "0")}${day.padStart(2, "0")}`;
  }
  throw new Error(`Unsupported expiration format: ${expiration}`);
}

function strikeToCode(strike: number): string {
  return Math.round(strike * 1000).toString().padStart(8, "0");
}

export function normalizeOccSymbol(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/\s+/g, "");
}

export function occSymbolFromLeg(underlying: string, expiration: string, callPut: CallPut, strike: number): string {
  const root = underlying.toUpperCase();
  const exp = toYYMMDD(expiration);
  const cp = callPut[0].toUpperCase();
  const strikeCode = strikeToCode(strike);
  const paddedRoot = root.padEnd(6, " ");
  return normalizeOccSymbol(`${paddedRoot}${exp}${cp}${strikeCode}`);
}

export function applyQuote(entry: OptionQuote): OptionQuote {
  const symbol = normalizeOccSymbol(entry.symbol);
  const existing = optionQuotes.get(symbol) ?? { symbol };
  const merged: OptionQuote = {
    ...existing,
    ...entry,
    symbol,
  };
  const bid = tryNumber(merged.bid);
  const ask = tryNumber(merged.ask);
  const last = tryNumber(merged.last);
  const mid = merged.mid ?? (bid !== undefined && ask !== undefined ? Number(((bid + ask) / 2).toFixed(4)) : last ?? bid ?? ask ?? null);
  merged.bid = bid ?? existing.bid ?? null;
  merged.ask = ask ?? existing.ask ?? null;
  merged.last = last ?? existing.last ?? null;
  merged.mid = mid ?? existing.mid ?? null;
  merged.delta = tryNumber(merged.delta) ?? existing.delta ?? null;
  merged.gamma = tryNumber(merged.gamma) ?? existing.gamma ?? null;
  merged.theta = tryNumber(merged.theta) ?? existing.theta ?? null;
  merged.vega = tryNumber(merged.vega) ?? existing.vega ?? null;
  merged.iv = tryNumber(merged.iv) ?? existing.iv ?? null;
  merged.openInterest = tryNumber(merged.openInterest) ?? existing.openInterest ?? null;
  merged.volume = tryNumber(merged.volume) ?? existing.volume ?? null;
  merged.updatedAt = entry.updatedAt ?? existing.updatedAt ?? new Date().toISOString();
  optionQuotes.set(symbol, merged);
  registeredSymbols.add(symbol);
  return merged;
}

export function getQuote(symbol: string): OptionQuote | undefined {
  return optionQuotes.get(normalizeOccSymbol(symbol));
}

export function setUnderlyingPrice(underlying: string, price: number, asOf?: string): void {
  underlyingPrices.set(underlying.toUpperCase(), { price, asOf });
}

export function getUnderlyingPrice(underlying: string): { price: number; asOf?: string } | undefined {
  return underlyingPrices.get(underlying.toUpperCase());
}

export function subscribeLegs(legs: SubscribeLeg[], options?: SubscribeOptions): SubscribeResult {
  if (legs.length === 0) {
    return { symbols: [], appliedFixture: 0, cached: 0 };
  }
  const symbols: string[] = [];
  for (const leg of legs) {
    if (leg.symbol) {
      symbols.push(normalizeOccSymbol(leg.symbol));
      continue;
    }
    if (!leg.underlying || !leg.expiration || !leg.callPut || leg.strike === undefined) {
      throw new Error("Leg descriptor requires either symbol or (underlying, expiration, callPut, strike)");
    }
    const symbol = occSymbolFromLeg(leg.underlying, leg.expiration, leg.callPut, leg.strike);
    symbols.push(symbol);
  }
  for (const symbol of symbols) registeredSymbols.add(symbol);
  const preload = options?.preloadFixture ?? true;
  let applied = 0;
  const fixturePath = options?.fixturePath ?? process.env.STRATDECK_STREAM_FIXTURE;
  if (preload && fixturePath) {
    applied = loadFixtureQuotes(fixturePath, symbols);
  }
  const cached = symbols.reduce((acc, sym) => acc + (optionQuotes.has(sym) ? 1 : 0), 0);
  return { symbols, appliedFixture: applied, cached, fixturePath: fixturePath ? resolve(fixturePath) : undefined };
}

export function loadFixtureQuotes(fixturePath: string, filterSymbols?: string[]): number {
  const resolved = resolve(process.cwd(), fixturePath);
  const raw = readFileSync(resolved, "utf8");
  const parsed = JSON.parse(raw);
  const symbolsSet = filterSymbols && filterSymbols.length > 0 ? new Set(filterSymbols.map(symbol => normalizeOccSymbol(symbol))) : undefined;
  const quotes: any[] = Array.isArray(parsed)
    ? parsed
    : Array.isArray(parsed.quotes)
      ? parsed.quotes
      : [];
  let count = 0;
  for (const entry of quotes) {
    const symbolRaw: string | undefined = entry.symbol ?? entry.optionSymbol ?? entry.occ ?? entry.OCCSymbol;
    if (!symbolRaw) continue;
    const symbol = normalizeOccSymbol(symbolRaw);
    if (symbolsSet && !symbolsSet.has(symbol)) continue;
    const quote: OptionQuote = {
      symbol,
      bid: tryNumber(entry.bid ?? entry.bidPrice ?? entry.bestBid),
      ask: tryNumber(entry.ask ?? entry.askPrice ?? entry.bestAsk),
      mid: tryNumber(entry.mid ?? entry.mark),
      last: tryNumber(entry.last ?? entry.lastPrice),
      delta: tryNumber(entry.delta),
      gamma: tryNumber(entry.gamma),
      theta: tryNumber(entry.theta),
      vega: tryNumber(entry.vega),
      iv: tryNumber(entry.iv ?? entry.impliedVolatility ?? entry.ivr),
      openInterest: tryNumber(entry.openInterest ?? entry.open_int),
      volume: tryNumber(entry.volume),
      source: entry.source ?? "fixture",
      updatedAt: entry.updatedAt ?? entry.asOf ?? entry.timestamp ?? new Date().toISOString(),
    };
    applyQuote(quote);
    count += 1;
  }
  const underlyings: any[] = Array.isArray(parsed.underlyings) ? parsed.underlyings : [];
  for (const entry of underlyings) {
    if (!entry.symbol || entry.price === undefined) continue;
    const price = tryNumber(entry.price);
    if (price === undefined) continue;
    setUnderlyingPrice(entry.symbol, price, entry.asOf ?? entry.updatedAt);
  }
  return count;
}

export function clearLiveCache(): void {
  optionQuotes.clear();
  underlyingPrices.clear();
  registeredSymbols.clear();
}

export function getSpreadLiveSummary(underlying: string, legs: SpreadLegDescriptor[]): SpreadLiveSummary | undefined {
  if (legs.length === 0) return undefined;
  const shortLegs: LiveLegQuote[] = [];
  const longLegs: LiveLegQuote[] = [];
  const missing: string[] = [];
  for (const leg of legs) {
    const symbol = occSymbolFromLeg(underlying, leg.expiration, leg.call_put, leg.strike);
    const quote = optionQuotes.get(symbol);
    const liveLeg: LiveLegQuote = {
      legId: leg.id,
      side: leg.side,
      callPut: leg.call_put,
      strike: leg.strike,
      expiration: leg.expiration,
      symbol,
      quote,
    };
    if (leg.side === "SHORT") shortLegs.push(liveLeg);
    else longLegs.push(liveLeg);
    if (!quote) missing.push(symbol);
  }
  if (shortLegs.every(leg => !leg.quote) && longLegs.every(leg => !leg.quote)) {
    return undefined;
  }
  const underlyingInfo = underlyingPrices.get(underlying.toUpperCase());
  return {
    shortLegs,
    longLegs,
    underlyingPrice: underlyingInfo?.price,
    asOf: underlyingInfo?.asOf,
    missing: missing.filter(symbol => !optionQuotes.has(symbol)),
  };
}

export interface ChainSnapshotInput {
  underlying: string;
  asOf?: string;
  price?: number;
  quotes?: OptionQuote[];
  fixturePath?: string;
}

export function applyChainSnapshot(input: ChainSnapshotInput): SubscribeResult {
  if (input.price !== undefined) {
    setUnderlyingPrice(input.underlying, input.price, input.asOf);
  }
  let applied = 0;
  if (Array.isArray(input.quotes)) {
    for (const quote of input.quotes) {
      applyQuote(quote);
      applied += 1;
    }
  }
  if (input.fixturePath) {
    applied += loadFixtureQuotes(input.fixturePath);
  }
  const cached = Array.from(optionQuotes.keys()).filter(symbol => symbol.startsWith(input.underlying.toUpperCase())).length;
  return {
    symbols: Array.from(optionQuotes.keys()),
    appliedFixture: applied,
    cached,
    fixturePath: input.fixturePath ? resolve(input.fixturePath) : undefined,
  };
}

export function getRegisteredSymbols(): string[] {
  return Array.from(registeredSymbols.values());
}

export function evaluateSpreadMetrics(summary: SpreadLiveSummary, config: SpreadMetricConfig): SpreadMetrics {
  const metrics: SpreadMetrics = {};
  const baValues: number[] = [];
  const oiValues: number[] = [];
  const deltaValues: number[] = [];
  const ivValues: number[] = [];

  for (const leg of summary.shortLegs) {
    const quote = leg.quote;
    if (!quote) continue;

    const bid = tryNumber(quote.bid);
    const ask = tryNumber(quote.ask);
    const mid = quote.mid ?? (bid !== undefined && ask !== undefined ? (bid + ask) / 2 : undefined);
    if (bid !== undefined && ask !== undefined && mid && mid > 0) {
      const spread = Math.max(0, ask - bid);
      baValues.push(Number(((spread / mid) * 100).toFixed(2)));
    }

    if (quote.openInterest !== undefined && quote.openInterest !== null) {
      const oi = tryNumber(quote.openInterest);
      if (oi !== undefined) oiValues.push(oi);
    }

    if (quote.delta !== undefined && quote.delta !== null) {
      const delta = tryNumber(quote.delta);
      if (delta !== undefined) deltaValues.push(Math.abs(delta));
    }

    if (quote.iv !== undefined && quote.iv !== null) {
      const iv = tryNumber(quote.iv);
      if (iv !== undefined) ivValues.push(iv);
    }
  }

  if (baValues.length > 0) {
    const avgBa = baValues.reduce((acc, val) => acc + val, 0) / baValues.length;
    metrics.baPct = Number(avgBa.toFixed(2));
  }

  if (oiValues.length > 0) {
    const minOi = Math.min(...oiValues);
    metrics.oiOk = minOi >= config.minOpenInterest;
  }

  if (deltaValues.length > 0) {
    const avgDelta = deltaValues.reduce((acc, val) => acc + val, 0) / deltaValues.length;
    metrics.delta = Number(avgDelta.toFixed(3));
  }

  if (ivValues.length > 0) {
    const avgIv = ivValues.reduce((acc, val) => acc + val, 0) / ivValues.length;
    metrics.ivr = Number(avgIv.toFixed(3));
  }

  return metrics;
}
