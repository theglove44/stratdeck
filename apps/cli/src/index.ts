import readline from 'node:readline';
import { stdin as input, stdout as output } from 'node:process';
import chalk from 'chalk';
import ora from 'ora';
import pino from 'pino';
import { z } from 'zod';
import { listPositions, addJournalNote, PositionRow, listSpreads, listJournalNotes, JournalEntryRow } from '@stratdeck/data';
import { rollThresholds } from '@stratdeck/config';
import { SpreadsScanResult, RollSimResult } from '@stratdeck/mcp-server/schemas.js';
import { dteFromISO, isIC } from './utils.js';
import { CliMcpClient } from './mcpClient.js';

const log = pino({ level: 'info' });

let mcpClient: CliMcpClient | null = null;

async function getMcpClient() {
  if (!mcpClient) {
    mcpClient = await CliMcpClient.create();
  }
  return mcpClient;
}

function banner() {
  output.write('\n');
  output.write(chalk.bold.cyan('StratDeck Copilot CLI ') + chalk.dim('(v0.1 – Local Brainstem)') + '\n');
  output.write(chalk.dim('Type ') + chalk.bold('help') + chalk.dim(' for commands. ') + chalk.dim('Type ') + chalk.bold('quit') + chalk.dim(' to exit.') + '\n\n');
}

function help() {
  output.write(chalk.bold('Commands:\n'));
  output.write('  ' + chalk.green('show ics <N> dte') + '           List iron condors with DTE < N\n');
  output.write('  ' + chalk.green('positions open <N> dte') + '      List open positions inside DTE limit\n');
  output.write('  ' + chalk.green('scan <pcs|ccs|ic> <min>-<max> dte [filters]') + '  Scan spreads with optional filters\n');
  output.write('      filters: minCredit <amt> maxWidth <width>\n');
  output.write('  ' + chalk.green('mcp scan <pcs|ccs|ic> <min>-<max> dte') + '  Run MCP-ranked scan with journaling\n');
  output.write('  ' + chalk.green('whatif <strategyId> <targetDTE> dte width <W>') + '  Simulate roll width/DTE\n');
  output.write('  ' + chalk.green('mcp roll <strategyId> <targetDTE>') + ' Recommend or skip a roll idea\n');
  output.write('  ' + chalk.green('journal add <strategyId> [tag:foo] <note...>') + ' Add a note to a strategy\n');
  output.write('  ' + chalk.green('journal last <N>') + '            Show most recent journal notes\n');
  output.write('  ' + chalk.green('help') + '                        Show this help\n');
  output.write('  ' + chalk.green('quit') + '                        Exit\n\n');
}

function renderICs(rows: PositionRow[], dteLimit: number) {
  const icRows = rows.filter(r => isIC(r.strategy_type) && dteFromISO(r.expiration) < dteLimit);
  if (icRows.length === 0) {
    output.write(chalk.yellow('No iron condors found within ') + chalk.bold(String(dteLimit)) + chalk.yellow(' DTE.') + '\n\n');
    return;
  }

  const headers = ['Strategy ID', 'Underlying', 'DTE', 'Credit', 'Status'];
  const widths = [28, 12, 5, 8, 10];
  const pad = (s: string, w: number) => s.padEnd(w);

  output.write(chalk.bold(headers.map((h, i) => pad(h, widths[i])).join('  ')) + '\n');
  output.write(chalk.dim('-'.repeat(70)) + '\n');
  for (const r of icRows) {
    const dte = dteFromISO(r.expiration).toString();
    const line = [
      pad(r.strategy_id, widths[0]),
      pad(r.underlying, widths[1]),
      pad(dte, widths[2]),
      pad((r.credit_received ?? 0).toFixed(2), widths[3]),
      pad(r.status, widths[4]),
    ].join('  ');
    output.write(line + '\n');
  }
  output.write('\n');
}

function renderPositionsOpen(rows: PositionRow[], dteLimit: number) {
  const filtered = rows
    .filter(r => r.status === 'OPEN')
    .filter(r => dteFromISO(r.expiration) < dteLimit)
    .map(r => ({ ...r, dte: dteFromISO(r.expiration) }))
    .sort((a, b) => a.dte - b.dte);

  if (filtered.length === 0) {
    output.write(chalk.yellow('No open positions under ') + chalk.bold(String(dteLimit)) + chalk.yellow(' DTE.') + '\n\n');
    return;
  }

  const headers = ['Strategy ID', 'Type', 'Underlying', 'DTE', 'Credit'];
  const widths = [28, 6, 12, 5, 8];
  const pad = (value: string | number, width: number) => value.toString().padEnd(width);

  output.write(chalk.bold(headers.map((h, i) => pad(h, widths[i])).join('  ')) + '\n');
  output.write(chalk.dim('-'.repeat(70)) + '\n');
  for (const r of filtered) {
    const line = [
      pad(r.strategy_id, widths[0]),
      pad(r.strategy_type, widths[1]),
      pad(r.underlying, widths[2]),
      pad(r.dte, widths[3]),
      pad((r.credit_received ?? 0).toFixed(2), widths[4]),
    ].join('  ');
    output.write(line + '\n');
  }
  output.write('\n');
}

function renderScan(type: string, rows: any[], minDte: number, maxDte: number) {
  const headers = ['Type', 'Strategy ID', 'Underlying', 'DTE', 'Width', 'Credit', 'Status'];
  const widths = [6, 28, 12, 5, 7, 8, 10];
  const pad = (value: string | number, width: number) => value.toString().padEnd(width);

  const filtered = rows.filter(r => {
    const dte = dteFromISO(r.expiration);
    return dte >= minDte && dte <= maxDte;
  });

  if (filtered.length === 0) {
    output.write(chalk.yellow(`No ${type.toUpperCase()} candidates between ${minDte}-${maxDte} DTE.`) + '\n\n');
    return;
  }

  output.write(chalk.bold(headers.map((h, i) => pad(h, widths[i])).join('  ')) + '\n');
  output.write(chalk.dim('-'.repeat(85)) + '\n');
  for (const r of filtered) {
    const line = [
      pad(type.toUpperCase(), widths[0]),
      pad(r.strategy_id, widths[1]),
      pad(r.underlying, widths[2]),
      pad(dteFromISO(r.expiration), widths[3]),
      pad(r.width?.toFixed(2) ?? '-', widths[4]),
      pad((r.credit_received ?? 0).toFixed(2), widths[5]),
      pad(r.status, widths[6]),
    ].join('  ');
    output.write(line + '\n');
  }
  output.write('\n');
}

function renderMcpScan(result: z.infer<typeof SpreadsScanResult>) {
  const rows = result.spreads;
  if (rows.length === 0) {
    output.write(chalk.yellow('No MCP-ranked spreads returned.') + '\n\n');
    return;
  }

  output.write(chalk.bold(`spreads.scan v${result.meta.version} | sort: ${result.meta.sort.join(', ')}`) + '\n');
  output.write(chalk.dim('-'.repeat(120)) + '\n');
  output.write(chalk.bold('Rank  Strategy ID                 Credit  Width  DTE  Score   B/A%   Delta  IV   OI  Status   Reason') + '\n');
  for (const r of rows) {
    const baPct = r.baPct !== undefined ? r.baPct.toFixed(1).padEnd(6) : '--'.padEnd(6);
    const delta = r.delta !== undefined ? r.delta.toFixed(2).padEnd(6) : '--'.padEnd(6);
    const iv = r.ivr !== undefined ? r.ivr.toFixed(2).padEnd(5) : '--'.padEnd(5);
    const oi = r.oiOk === undefined ? '--'.padEnd(3) : (r.oiOk ? 'OK '.padEnd(3) : 'low');
    const line = [
      r.rank.toString().padEnd(5),
      r.strategy_id.padEnd(28),
      (r.credit_received ?? 0).toFixed(2).padEnd(7),
      (r.width ?? 0).toFixed(2).padEnd(6),
      r.dte.toString().padEnd(4),
      r.score.toFixed(3).padEnd(7),
      baPct,
      delta,
      iv,
      oi,
      r.status.padEnd(8),
      r.rank_reason
    ].join(' ');
    output.write(line + '\n');
  }
  output.write('\n');
}

type RollCandidate = z.infer<typeof RollSimResult>['candidates'][number];

function renderMcpRoll(strategyId: string, targetDte: number, candidate: RollCandidate) {
  const creditThreshold = rollThresholds.minCredit;
  const popThreshold = rollThresholds.minPopShift;
  const qualifies = candidate.estCredit >= creditThreshold && candidate.popShift >= popThreshold;
  const status = qualifies ? chalk.green.bold('RECOMMEND') : chalk.yellow.bold('SKIP');
  output.write(`${status} roll for ${strategyId} → ${candidate.newExpiration} (${targetDte} DTE)\n`);
  output.write(`  credit ${candidate.estCredit.toFixed(2)} | popShift ${candidate.popShift.toFixed(1)} | theta ${candidate.thetaShift.toFixed(1)}\n`);
  output.write(`  width ${candidate.newWidth.toFixed(2)} | breakevens ${candidate.breakevens[0]} / ${candidate.breakevens[1]}\n`);
  if (!qualifies) {
    const reasons = [
      candidate.estCredit < creditThreshold ? `estCredit < ${creditThreshold.toFixed(2)}` : null,
      candidate.popShift < popThreshold ? `popShift < ${popThreshold.toFixed(1)}` : null
    ].filter(Boolean).join(', ');
    output.write(chalk.dim(`  Guardrails triggered (${reasons}).`) + '\n');
  }
  output.write(chalk.dim(`  ${candidate.rationale}`) + '\n\n');
}

function renderJournalEntries(entries: JournalEntryRow[]) {
  if (entries.length === 0) {
    output.write(chalk.yellow('No journal entries yet.') + '\n\n');
    return;
  }
  output.write(chalk.bold('Recent Journal Entries') + '\n');
  output.write(chalk.dim('-'.repeat(80)) + '\n');
  for (const entry of entries) {
    const ts = new Date(entry.created_at);
    const stamp = Number.isNaN(ts.getTime()) ? entry.created_at : ts.toISOString().replace('T', ' ').slice(0, 16);
    const strategyPart = entry.strategy_id ? chalk.cyan(entry.strategy_id) + ' ' : '';
    const tagPart = entry.tag ? chalk.magenta(`[${entry.tag}] `) : '';
    output.write(`${chalk.dim(stamp)} ${strategyPart}${tagPart}${entry.content}\n`);
  }
  output.write('\n');
}

async function main() {
  banner();
  const rl = readline.createInterface({ input, output, terminal: true });
  rl.setPrompt(chalk.cyan('> '));
  rl.prompt();

  rl.on('line', async (line) => {
    const cmd = line.trim();

    if (cmd === 'help') {
      help();
      rl.prompt();
      return;
    }
    if (cmd === 'quit' || cmd === 'exit') {
      rl.close();
      return;
    }

    // positions open <N> dte
    let m = cmd.match(/^positions\s+open\s+<(\d+)\s+dte$/i);
    if (m) {
      const limit = Number(m[1]);
      const spinner = ora('Loading positions...').start();
      try {
        const rows = await listPositions();
        spinner.stop();
        renderPositionsOpen(rows, limit);
      } catch (e: any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // scan <pcs|ccs|ic> <min>-<max> dte [filters]
    m = cmd.match(/^scan\s+(pcs|ccs|ic)\s+(\d+)-(\d+)\s+dte(?:\s+minCredit\s+(\d+(?:\.\d+)?))?(?:\s+maxWidth\s+(\d+(?:\.\d+)?))?$/i);
    if (m) {
      const type = m[1].toUpperCase();
      const minDte = Number(m[2]);
      const maxDte = Number(m[3]);
      const minCredit = m[4] ? Number(m[4]) : undefined;
      const maxWidth = m[5] ? Number(m[5]) : undefined;
      const filterLabel = [
        `${minDte}-${maxDte} DTE`,
        minCredit !== undefined ? `minCredit ${minCredit.toFixed(2)}` : null,
        maxWidth !== undefined ? `maxWidth ${maxWidth}` : null,
      ].filter(Boolean).join(', ');
      const spinner = ora(`Scanning ${type} (${filterLabel})...`).start();
      try {
        const rows = await listSpreads(type as any);
        spinner.stop();
        let filtered = rows as any[];
        if (minCredit !== undefined) filtered = filtered.filter(r => (r.credit_received ?? 0) >= minCredit);
        if (maxWidth !== undefined) filtered = filtered.filter(r => (r.width ?? Infinity) <= maxWidth);
        renderScan(type, filtered, minDte, maxDte);
      } catch (e:any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // mcp scan <pcs|ccs|ic> <min>-<max> dte
    m = cmd.match(/^mcp\s+scan\s+(pcs|ccs|ic)\s+(\d+)-(\d+)\s+dte$/i);
    if (m) {
      const type = m[1].toUpperCase();
      const minDte = Number(m[2]);
      const maxDte = Number(m[3]);
      const spinner = ora(`MCP scanning ${type} between ${minDte}-${maxDte} DTE...`).start();
      try {
        const client = await getMcpClient();
        const result = await client.callToolParsed('spreads.scan', { type, dte: { min: minDte, max: maxDte } }, SpreadsScanResult);
        spinner.stop();
        renderMcpScan(result);
      } catch (e:any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // whatif <strategyId> <targetDTE> dte width <W>
    m = cmd.match(/^whatif\s+(\S+)\s+(\d+)\s+dte\s+width\s+(\d+(?:\.\d+)?)$/i);
    if (m) {
      const strategyId = m[1];
      const targetDte = Number(m[2]);
      const width = Number(m[3]);
      const spinner = ora(`Simulating ${strategyId} @ ${targetDte} DTE, width ${width}...`).start();
      try {
        const client = await getMcpClient();
        const result = await client.callToolParsed('roll.simulate', { strategyId, width, targetDTE: targetDte, prefer: 'credit' }, RollSimResult);
        spinner.stop();
        if (result.candidates.length === 0) {
          output.write(chalk.yellow('No what-if candidates returned.') + '\n\n');
        } else {
          renderMcpRoll(strategyId, targetDte, result.candidates[0]);
        }
      } catch (e: any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // mcp roll <strategyId> <targetDTE>
    m = cmd.match(/^mcp\s+roll\s+(\S+)\s+(\d+)$/i);
    if (m) {
      const strategyId = m[1];
      const targetDte = Number(m[2]);
      const spinner = ora(`Simulating roll for ${strategyId} to ${targetDte} DTE...`).start();
      try {
        const positions = await listPositions();
        const position = positions.find(p => p.strategy_id === strategyId);
        if (!position) {
          throw new Error('Strategy not found.');
        }
        const spreads = await listSpreads(position.strategy_type as any);
        const detail = spreads.find(s => s.strategy_id === strategyId);
        if (!detail || detail.width == null) {
          throw new Error('Spread width unavailable for roll simulation.');
        }
        const client = await getMcpClient();
        const result = await client.callToolParsed('roll.simulate', { strategyId, width: detail.width, targetDTE: targetDte, prefer: 'credit' }, RollSimResult);
        spinner.stop();
        if (result.candidates.length === 0) {
          output.write(chalk.yellow('No roll candidates returned.') + '\n\n');
        } else {
          renderMcpRoll(strategyId, targetDte, result.candidates[0]);
        }
      } catch (e:any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // show ics <N> dte
    m = cmd.match(/^show\s+ics\s+<(\d+)\s+dte$/i);
    if (m) {
      const limit = Number(m[1]);
      const spinner = ora('Loading positions...').start();
      try {
        const rows = await listPositions();
        spinner.stop();
        renderICs(rows, limit);
      } catch (e:any) {
        spinner.stop();
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // journal add STRAT_ID note...
    m = cmd.match(/^journal\s+add\s+(\S+)(?:\s+tag:(\w+))?\s+(.+)$/i);
    if (m) {
      const strategyId = m[1];
      const tag = m[2];
      const note = m[3];
      try {
        await addJournalNote(strategyId, note, tag ?? 'manual');
        output.write(chalk.green('Note added.') + '\n\n');
      } catch (e:any) {
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    // journal last N
    m = cmd.match(/^journal\s+last\s+(\d+)$/i);
    if (m) {
      const limit = Number(m[1]);
      try {
        const entries = await listJournalNotes(limit);
        renderJournalEntries(entries);
      } catch (e: any) {
        output.write(chalk.red('Error: ') + e.message + '\n');
      }
      rl.prompt();
      return;
    }

    output.write(chalk.yellow('Unrecognized command. Type ') + chalk.bold('help') + chalk.yellow('.\n\n'));
    rl.prompt();
  });

  rl.on('close', () => {
    output.write(chalk.dim('\nBye.\n'));
    void (async () => {
      if (mcpClient) {
        await mcpClient.dispose();
        mcpClient = null;
      }
      process.exit(0);
    })();
  });
}

main().catch((e) => {
  log.error(e, 'Fatal error');
  process.exit(1);
});
