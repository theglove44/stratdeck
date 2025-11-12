export function dteFromISO(exp: string): number {
    const today = new Date();
    const e = new Date(exp);
    const ms = e.setHours(0, 0, 0, 0) - today.setHours(0, 0, 0, 0);
    return Math.max(0, Math.round(ms / 86400000));
}

export async function safe<T>(tool: string, fn: () => Promise<T>): Promise<T> {
    const timer = new Promise<never>((_, rej) =>
        setTimeout(() => rej({ code: "TIMEOUT", message: `${tool} exceeded 2000ms` }), 2000)
    );
    try { return await Promise.race([fn(), timer]); }
    catch (e: any) {
        if (e?.code === "TIMEOUT") throw e;
        throw { code: "VALIDATION_OR_RUNTIME", message: String(e?.message ?? e), data: { tool } };
    }
}
