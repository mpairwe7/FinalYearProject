import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { createRevealQueue, nextRevealIndex, revealBudget } from '../../lib/revealQueue';

describe('nextRevealIndex', () => {
  it('stops at a word boundary rather than mid-word', () => {
    const target = 'the standard VAT rate is eighteen percent';
    const i = nextRevealIndex(target, 0, 6);
    // 6 chars lands inside "standard"; the index runs on to the space after it.
    expect(target.slice(0, i)).toBe('the standard ');
  });

  it('reveals a long unbroken token whole rather than stalling', () => {
    const target = 'see https://ura.go.ug/very/long/path/without/spaces';
    const i = nextRevealIndex(target, 0, 5);
    expect(i).toBe(target.length);
  });

  it('never runs past the end', () => {
    expect(nextRevealIndex('short', 0, 999)).toBe(5);
    expect(nextRevealIndex('short', 5, 10)).toBe(5);
  });
});

describe('revealBudget', () => {
  it('grows with the backlog so a long reply still lands promptly', () => {
    expect(revealBudget(10)).toBeLessThan(revealBudget(2000));
  });

  it('stays within bounds at both extremes', () => {
    expect(revealBudget(1)).toBeGreaterThanOrEqual(2);
    expect(revealBudget(1_000_000)).toBeLessThanOrEqual(100);
  });
});

describe('createRevealQueue', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('reveals pushed text progressively rather than all at once', () => {
    const commits: string[] = [];
    const q = createRevealQueue({ onCommit: (t) => commits.push(t), tickMs: 10 });

    q.push('one two three four five six seven eight nine ten');
    vi.advanceTimersByTime(10);

    expect(commits.length).toBe(1);
    expect(commits[0].length).toBeGreaterThan(0);
    expect(commits[0].length).toBeLessThan(q.getTarget().length);
  });

  it('eventually commits the whole target', async () => {
    let latest = '';
    const q = createRevealQueue({ onCommit: (t) => { latest = t; }, tickMs: 1 });
    const text = 'the standard VAT rate in Uganda is 18% on taxable supplies';
    q.push(text);

    const settled = q.finish();
    await vi.advanceTimersByTimeAsync(2000);
    await settled;

    expect(latest).toBe(text);
  });

  it('keeps typing when set() extends what is already shown', async () => {
    let latest = '';
    const q = createRevealQueue({ onCommit: (t) => { latest = t; }, tickMs: 1 });
    q.push('hello there');
    vi.advanceTimersByTime(1);
    const shown = latest;

    q.set('hello there general kenobi');
    const settled = q.finish();
    await vi.advanceTimersByTimeAsync(2000);
    await settled;

    expect('hello there general kenobi'.startsWith(shown)).toBe(true);
    expect(latest).toBe('hello there general kenobi');
  });

  it('snaps instead of rewinding when set() diverges from what is shown', () => {
    const commits: string[] = [];
    const q = createRevealQueue({ onCommit: (t) => commits.push(t), tickMs: 1 });
    q.push('Okay, the user is asking about VAT. I should check the rate.');
    vi.advanceTimersByTime(1);

    // What `done` does: replace the streamed text with the cleaned reply,
    // which drops the chain-of-thought the reader has already seen.
    q.set('The standard VAT rate is 18%.');

    expect(commits.at(-1)).toBe('The standard VAT rate is 18%.');
  });

  it('finish() resolves only once the full text is committed', async () => {
    let latest = '';
    const q = createRevealQueue({ onCommit: (t) => { latest = t; }, tickMs: 1 });
    const text = 'alpha bravo charlie delta echo foxtrot golf hotel india juliet';
    q.push(text);

    let resolved = false;
    void q.finish().then(() => { resolved = true; });

    await vi.advanceTimersByTimeAsync(0);
    expect(resolved).toBe(false);

    await vi.advanceTimersByTimeAsync(3000);
    expect(resolved).toBe(true);
    expect(latest).toBe(text);
  });

  it('stop() commits what arrived and ignores anything after', () => {
    const commits: string[] = [];
    const q = createRevealQueue({ onCommit: (t) => commits.push(t), tickMs: 1 });
    q.push('partial answer so far');

    q.stop();
    expect(commits.at(-1)).toBe('partial answer so far');

    q.push(' more that should be dropped');
    vi.advanceTimersByTime(100);
    expect(commits.at(-1)).toBe('partial answer so far');
  });

  it('commits immediately when motion is reduced', () => {
    const commits: string[] = [];
    const q = createRevealQueue({ onCommit: (t) => commits.push(t), instant: true, tickMs: 1 });
    q.push('the whole thing at once');
    expect(commits).toEqual(['the whole thing at once']);
  });
});
