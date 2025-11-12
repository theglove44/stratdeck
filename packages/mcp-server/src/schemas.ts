// packages/mcp-server/src/schemas.ts
import { z } from "zod";

export const PositionsListArgs = z.object({
    type: z.enum(["IC", "PCS", "CCS"]).optional(),
    dte_lt: z.number().int().nonnegative().optional(),
});

export const Position = z.object({
    strategy_id: z.string(),
    underlying: z.string(),
    strategy_type: z.enum(["IC", "PCS", "CCS", "IFly", "Strangle"]),
    status: z.enum(["OPEN", "CLOSED", "ADJUSTED"]),
    expiration: z.string(), // ISO
    credit_received: z.number().nullable(),
    dte: z.number().int().nonnegative(),
});

export const PositionsListResult = z.object({
    meta: z.object({
        tool: z.literal("positions.list"),
        version: z.string(),
    }).optional(),
    positions: z.array(Position),
});

export const SpreadsScanArgs = z.object({
    type: z.enum(["IC", "PCS", "CCS"]),
    dte: z
        .object({
            min: z.number().int().min(0).default(0),
            max: z.number().int().min(0).optional(),
        })
        .optional(),
    minCredit: z.number().nonnegative().optional(),
    maxWidth: z.number().positive().optional(),
});

export const Spread = z.object({
    strategy_id: z.string(),
    underlying: z.string(),
    strategy_type: z.enum(["IC", "PCS", "CCS"]),
    status: z.enum(["OPEN", "CLOSED", "ADJUSTED"]),
    expiration: z.string(), // ISO
    credit_received: z.number(),
    width: z.number().nullable(),
    dte: z.number().int().nonnegative()
});

export const SpreadLiveQuote = z.object({
    symbol: z.string(),
    bid: z.number().nullable().optional(),
    ask: z.number().nullable().optional(),
    mid: z.number().nullable().optional(),
    last: z.number().nullable().optional(),
    delta: z.number().nullable().optional(),
    iv: z.number().nullable().optional(),
    openInterest: z.number().nullable().optional(),
    source: z.string().optional(),
    updatedAt: z.string().optional()
});

export const SpreadLiveLeg = z.object({
    legId: z.string(),
    side: z.enum(["SHORT", "LONG"]),
    callPut: z.enum(["CALL", "PUT"]),
    strike: z.number(),
    expiration: z.string(),
    symbol: z.string(),
    quote: SpreadLiveQuote.optional()
});

export const SpreadLiveData = z.object({
    shortLegs: z.array(SpreadLiveLeg),
    longLegs: z.array(SpreadLiveLeg).default([]),
    underlyingPrice: z.number().optional(),
    asOf: z.string().optional(),
    missing: z.array(z.string()).optional()
});

export const RankedSpread = Spread.extend({
    score: z.number(),
    rank: z.number().int().positive(),
    rank_reason: z.string(),
    baPct: z.number().optional(),
    oiOk: z.boolean().optional(),
    delta: z.number().optional(),
    ivr: z.number().optional(),
    live: SpreadLiveData.optional()
});

export const SpreadsScanResult = z.object({
    meta: z.object({
        tool: z.literal("spreads.scan"),
        version: z.string(),
        sort: z.array(z.string()),
    }),
    spreads: z.array(RankedSpread),
});

export const JournalWriteArgs = z.object({
    strategyId: z.string(),
    note: z.string().min(3),
    tag: z.string().min(1).max(24).optional(),
});
export const JournalWriteResult = z.object({ ok: z.literal(true) });

export const RollSimArgs = z.object({
    strategyId: z.string().min(1),
    width: z.number().positive(),
    targetDTE: z.number().int().positive(),
    prefer: z.enum(["credit", "probability", "neutral"]).default("credit"),
});

export const RollSimResult = z.object({
    meta: z.object({
        tool: z.literal("roll.simulate"),
        version: z.string(),
    }),
    strategyId: z.string(),
    candidates: z.array(
        z.object({
            newExpiration: z.string(),
            newWidth: z.number().positive(),
            estCredit: z.number(),
            popShift: z.number(),
            thetaShift: z.number(),
            breakevens: z.tuple([z.number(), z.number()]),
            rationale: z.string(),
        })
    ),
});

export const StreamSubscribeLeg = z.object({
    symbol: z.string().optional(),
    underlying: z.string().optional(),
    expiration: z.string().optional(),
    strike: z.number().optional(),
    callPut: z.enum(["CALL", "PUT"]).optional(),
    side: z.enum(["SHORT", "LONG"]).optional(),
});

export const StreamSubscribeArgs = z.object({
    legs: z.array(StreamSubscribeLeg).min(1),
    fixturePath: z.string().optional(),
    preloadFixture: z.boolean().optional(),
});

export const StreamSubscribeResult = z.object({
    symbols: z.array(z.string()),
    appliedFixture: z.number(),
    cached: z.number(),
    fixturePath: z.string().optional(),
});

export const ChainSnapshotQuote = z.object({
    symbol: z.string(),
    bid: z.number().optional(),
    ask: z.number().optional(),
    mid: z.number().optional(),
    last: z.number().optional(),
    delta: z.number().optional(),
    iv: z.number().optional(),
    openInterest: z.number().optional(),
    volume: z.number().optional(),
    source: z.string().optional(),
    updatedAt: z.string().optional(),
});

export const ChainsSnapshotArgs = z.object({
    underlying: z.string(),
    price: z.number().optional(),
    asOf: z.string().optional(),
    quotes: z.array(ChainSnapshotQuote).optional(),
    fixturePath: z.string().optional(),
});

export const ChainsSnapshotResult = StreamSubscribeResult;
