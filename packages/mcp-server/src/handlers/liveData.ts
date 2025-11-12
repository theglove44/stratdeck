import { applyChainSnapshot, subscribeLegs } from "@stratdeck/data-live";
import { ChainsSnapshotArgs, ChainsSnapshotResult, StreamSubscribeArgs, StreamSubscribeResult } from "../schemas";

export async function streamSubscribe(args: unknown) {
    const parsed = StreamSubscribeArgs.parse(args ?? {});
    const result = subscribeLegs(parsed.legs, {
        fixturePath: parsed.fixturePath,
        preloadFixture: parsed.preloadFixture,
    });
    return StreamSubscribeResult.parse(result);
}

export async function chainsSnapshot(args: unknown) {
    const parsed = ChainsSnapshotArgs.parse(args ?? {});
    const result = applyChainSnapshot({
        underlying: parsed.underlying,
        asOf: parsed.asOf,
        price: parsed.price,
        quotes: parsed.quotes,
        fixturePath: parsed.fixturePath,
    });
    return ChainsSnapshotResult.parse(result);
}
