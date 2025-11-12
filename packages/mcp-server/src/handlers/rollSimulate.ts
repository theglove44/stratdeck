import { addDays, formatISO } from "date-fns";
import { addJournalNote } from "@stratdeck/data";
import { rollThresholds } from "@stratdeck/config";
import { RollSimArgs, RollSimResult } from "../schemas";

export async function rollSimulate(args: unknown) {
    const a = RollSimArgs.parse(args ?? {});
    const today = new Date();
    const newExp = formatISO(addDays(today, a.targetDTE), { representation: "date" });
    const factor = Math.min(1.3, Math.max(0.6, a.targetDTE / 30));
    const estCredit = Number((a.width * 0.08 * factor).toFixed(2));
    const popShift = 2.7;
    const meetsThreshold = estCredit >= rollThresholds.minCredit && popShift >= rollThresholds.minPopShift;
    const rationaleNote = meetsThreshold
        ? `Meets guardrails: estCredit ${estCredit.toFixed(2)} >= ${rollThresholds.minCredit.toFixed(2)}, popShift ${popShift.toFixed(1)} >= ${rollThresholds.minPopShift.toFixed(1)}.`
        : `Fails guardrails: estCredit ${estCredit.toFixed(2)} ${estCredit < rollThresholds.minCredit ? `< ${rollThresholds.minCredit.toFixed(2)}` : `>= ${rollThresholds.minCredit.toFixed(2)}`}, popShift ${popShift.toFixed(1)} ${popShift < rollThresholds.minPopShift ? `< ${rollThresholds.minPopShift.toFixed(1)}` : `>= ${rollThresholds.minPopShift.toFixed(1)}`}.`;
    const res = {
        meta: { tool: "roll.simulate", version: "0.1.0" },
        strategyId: a.strategyId,
        candidates: [{
            newExpiration: newExp,
            newWidth: a.width,
            estCredit,
            popShift,
            thetaShift: 7.9,
            breakevens: [-1.8, 1.6],
            meetsThreshold,
            rationale: `Target ${a.targetDTE} DTE; width ${a.width}; prefer=${a.prefer}. ${rationaleNote}`
        }]
    };
    const statusLabel = meetsThreshold ? 'ok' : 'review';
    await addJournalNote(a.strategyId, `[roll.simulate v0.1.0] width=${a.width} target=${a.targetDTE}DTE estCredit=${estCredit.toFixed(2)} popShift=${popShift.toFixed(1)} status=${statusLabel}`, 'roll-sim');
    return RollSimResult.parse(res);
}
