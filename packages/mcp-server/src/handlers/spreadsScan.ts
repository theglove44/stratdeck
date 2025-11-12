import { addJournalNote, listSpreads, SpreadRow } from "@stratdeck/data";
import { evaluateSpreadMetrics, getSpreadLiveSummary, SpreadLiveSummary } from "@stratdeck/data-live";
import { spreadsScanThresholds } from "@stratdeck/config";
import { SpreadsScanArgs, SpreadsScanResult } from "../schemas";
import { dteFromISO } from "../util";

type SpreadWithLive = Omit<SpreadRow, "legs"> & {
    dte: number;
    live?: SpreadLiveSummary;
    baPct?: number;
    oiOk?: boolean;
    delta?: number;
    ivr?: number;
};

function score(spread: SpreadWithLive) {
    const cfg = spreadsScanThresholds;
    const w = Math.max(1, spread.width ?? 1);
    const creditPerWidth = spread.credit_received / w;
    const dtePenalty = Math.abs(spread.dte - cfg.dteTarget) / cfg.dtePenaltyDivisor;
    const baPenalty = spread.baPct !== undefined
        ? Math.min(1.5, Math.max(0, spread.baPct) / cfg.maxBidAskPct) * cfg.baPenaltyWeight
        : 0;
    const deltaPenalty = spread.delta !== undefined
        ? Math.min(2, Math.abs(spread.delta - cfg.idealDelta) / Math.max(cfg.deltaTolerance, 0.01)) * cfg.deltaPenaltyWeight
        : 0;
    const oiBonus = spread.oiOk ? cfg.oiBonus : 0;
    const ivrBonus = spread.ivr !== undefined ? Math.min(spread.ivr, 1) * cfg.ivrWeight : 0;
    const statusBonus = spread.status === "OPEN" ? cfg.statusBonus : 0;
    return creditPerWidth - dtePenalty - baPenalty - deltaPenalty + statusBonus + oiBonus + ivrBonus;
}

export async function spreadsScan(args: unknown) {
    const a = SpreadsScanArgs.parse(args ?? {});
    const base = await listSpreads(a.type);
    let rows: SpreadWithLive[] = base.map((row) => {
        const live = getSpreadLiveSummary(row.underlying, row.legs);
        const { legs, ...rest } = row;
        const withDte: SpreadWithLive = { ...rest, dte: dteFromISO(row.expiration) };
        if (live) {
            withDte.live = live;
            const metrics = evaluateSpreadMetrics(live, {
                minOpenInterest: spreadsScanThresholds.minOpenInterest,
                maxBidAskPct: spreadsScanThresholds.maxBidAskPct,
                idealDelta: spreadsScanThresholds.idealDelta,
                deltaTolerance: spreadsScanThresholds.deltaTolerance,
            });
            if (metrics.baPct !== undefined) withDte.baPct = metrics.baPct;
            if (metrics.oiOk !== undefined) withDte.oiOk = metrics.oiOk;
            if (metrics.delta !== undefined) withDte.delta = metrics.delta;
            if (metrics.ivr !== undefined) withDte.ivr = metrics.ivr;
        }
        return withDte;
    });

    if (a.dte) {
        const { min, max } = a.dte;
        rows = rows.filter(r => r.dte >= min && (max === undefined ? true : r.dte <= max));
    }
    const minCredit = a.minCredit;
    if (minCredit !== undefined) rows = rows.filter(r => r.credit_received >= minCredit);
    const maxWidth = a.maxWidth;
    if (maxWidth !== undefined) rows = rows.filter(r => (r.width ?? Infinity) <= maxWidth);

    const ranked = rankSpreads(rows);

    const result = SpreadsScanResult.parse({
        meta: { tool: "spreads.scan", version: "0.2.0", sort: ["score desc", "dte asc"] },
        spreads: ranked
    });
    if (ranked.length > 0) {
        const top = ranked[0];
        await addJournalNote(top.strategy_id, `[spreads.scan v0.2.0] rank=1 score=${top.score.toFixed(3)} ${top.rank_reason}`, 'scan-top');
    }
    return result;
}

export function rankSpreads(rows: SpreadWithLive[]) {
    return rows
        .map(r => ({ ...r, score: score(r) }))
        .sort((a, b) => b.score - a.score)
        .map((r, i) => ({ ...r, rank: i + 1, rank_reason: "credit/width, DTE target, bid/ask, delta, OI" }));
}
