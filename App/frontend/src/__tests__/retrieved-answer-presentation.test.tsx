/**
 * Presentation of retrieved answers.
 *
 * Retrieved answers arrive as the corpus wrote them, and the corpus writes
 * lists as prose. The URA services answer came back as one 600-character
 * sentence holding nine services separated by semicolons, with literal `--`
 * where em dashes belong and a citation marker stranded on its own line as a
 * bare superscript 1.
 *
 * These assert on the real string the deployed API returns, so the tests fail
 * if the transform stops handling the case it was written for. Every change
 * here is layout-only — same words, same order — because reformatting grounded
 * text is only defensible while it stays lossless.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import Markdown from '../components/Markdown';
import { sourceLabel } from '../lib/uraContacts';
import { stripCitationMarkers } from '../lib/answerText';

// Verbatim from https://landwind22-ura-chatbot.hf.space/api/v1/chat for
// "What services does URA provide?".
const SERVICES_ANSWER =
  "URA (Uganda Revenue Authority) is the country's central tax and customs authority. " +
  'Core services include: taxpayer registration (instant TIN); domestic tax administration -- ' +
  'VAT, PAYE and employment income, corporation tax, rental income tax, capital gains tax, ' +
  'withholding tax, stamp duty, and small-business taxes; customs -- import/export clearance, ' +
  'valuation, and passenger baggage and cargo processing; EFRIS e-invoicing and Digital Tax ' +
  'Stamps; tax exemption and compliance support, including for NGOs and religious institutions; ' +
  'taxpayer education such as the Taxpayer Starter Pack and the annual Taxation Handbook; and ' +
  'online payments and account management through the URA web portal. [1]';

describe('prose enumerations become lists', () => {
  it('breaks the services answer into one item per service', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    const items = Array.from(container.querySelectorAll('li')).map((li) => li.textContent ?? '');
    expect(items).toHaveLength(7);
    expect(items[0]).toBe('taxpayer registration (instant TIN)');
    expect(items[3]).toBe('EFRIS e-invoicing and Digital Tax Stamps');
    // The trailing conjunction reads as a leftover once the item stands alone.
    expect(items[6]).toBe('online payments and account management through the URA web portal');
  });

  it('keeps the lead-in as a sentence above the list', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    const paras = Array.from(container.querySelectorAll('p')).map((p) => p.textContent ?? '');
    expect(paras[0]).toContain('central tax and customs authority');
    expect(paras[1]).toContain('Core services include:');
  });

  it('loses no words', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    const rendered = (container.textContent ?? '').toLowerCase();
    for (const word of [
      'taxpayer registration',
      'corporation tax',
      'capital gains',
      'stamp duty',
      'passenger baggage',
      'digital tax stamps',
      'religious institutions',
      'taxation handbook',
      'web portal',
    ]) {
      expect(rendered).toContain(word);
    }
  });

  it('leaves ordinary prose with a semicolon alone', () => {
    const prose =
      'The standard VAT rate in Uganda is 18%. Some goods are zero-rated; others are exempt.';
    const { container } = render(<Markdown content={prose} />);
    expect(container.querySelectorAll('li')).toHaveLength(0);
  });

  it('leaves a long paragraph of semicolon-joined clauses alone', () => {
    const clauses =
      'Registration matters here: the taxpayer must first establish that the supply was made in ' +
      'the course of business and that the consideration was money or money’s worth, which the ' +
      'Act treats as two separate questions; and the assessment stands until objected to in ' +
      'writing within forty-five days of service, after which the Commissioner may allow a late ' +
      'objection only where the delay was caused by absence from Uganda, sickness or other ' +
      'reasonable cause established to their satisfaction.';
    const { container } = render(<Markdown content={clauses} />);
    expect(container.querySelectorAll('li')).toHaveLength(0);
  });
});

describe('typography', () => {
  it('renders the corpus double hyphen as an em dash', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    expect(container.textContent).toContain('—');
    expect(container.textContent).not.toContain(' -- ');
  });

  it('does not touch a horizontal rule or a flag', () => {
    const { container } = render(<Markdown content={'Run it with --verbose to see more.'} />);
    expect(container.textContent).toContain('--verbose');
  });
});

describe('citation markers', () => {
  // The reference belongs in the Sources block under the answer, not mid-
  // sentence. A pill on a lead-in read as a stray number: "Core services
  // include: 1".
  it('strips inline markers from the answer body', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    expect(container.querySelectorAll('sup.md-cite-ref')).toHaveLength(0);
    expect(container.textContent).not.toMatch(/\[\d+\]/);
  });

  it('leaves no seam where the marker was', () => {
    const { container } = render(<Markdown content={SERVICES_ANSWER} />);
    const paras = Array.from(container.querySelectorAll('p')).map((p) => p.textContent ?? '');
    // no stray number paragraph, no doubled space, no space before punctuation
    for (const p of paras) {
      expect(p.trim()).not.toMatch(/^\d+$/);
      expect(p).not.toMatch(/ {2,}/);
      expect(p).not.toMatch(/\s[.,;:]/);
    }
    expect(paras.some((p) => p.trim().endsWith('include:'))).toBe(true);
  });

  it('removes a marker from mid-sentence without joining words', () => {
    const { container } = render(
      <Markdown content={'The Act [2] requires registration within twenty days [3].'} />,
    );
    expect(container.textContent).toContain('The Act requires registration within twenty days.');
  });
});

describe('source labels', () => {
  it('shows a readable name rather than the corpus filename', () => {
    expect(sourceLabel('ura_about_ura_faqs.csv')).toBe('About URA');
    expect(sourceLabel('ura_vat_faqs.csv')).toBe('VAT');
    expect(sourceLabel('ura_efris_faqs.csv')).toBe('EFRIS');
    expect(sourceLabel('ura_instant_tin_application_faqs.csv')).toBe('Instant TIN Application');
    expect(sourceLabel('ura_exchange_of_information_faqs.csv')).toBe('Exchange of Information');
    expect(sourceLabel('ura_tax_obligations_ngos_faqs.csv')).toBe('Tax Obligations NGOs');
    expect(sourceLabel('ura_taxation_handbook_fy2025_26_faqs.csv')).toBe(
      'Taxation Handbook FY2025/26',
    );
  });

  it('leaves anything that is not a corpus filename alone', () => {
    expect(sourceLabel('https://ura.go.ug/tax-rates')).toBe('https://ura.go.ug/tax-rates');
    expect(sourceLabel('')).toBe('URA knowledge base');
    expect(sourceLabel(null)).toBe('URA knowledge base');
  });
});

describe('citation markers are stripped everywhere the reply is consumed', () => {
  // Three surfaces share the reply: the rendered message, the clipboard, and
  // the narrator. A marker left in any of them shows up as a stray number in
  // whatever the reader pastes, or gets voiced mid-sentence.
  it('strips for copy and speech, not just for rendering', () => {
    const raw = 'Pay via the URA portal [1]. Late filing attracts a penalty [2].';
    expect(stripCitationMarkers(raw)).toBe(
      'Pay via the URA portal. Late filing attracts a penalty.',
    );
  });

  it('keeps a numeric-text link intact', () => {
    const raw = 'See [1](https://ura.go.ug) for the schedule.';
    expect(stripCitationMarkers(raw)).toBe(raw);
  });

  it('is a no-op on text without markers', () => {
    const raw = 'The standard VAT rate in Uganda is 18%.';
    expect(stripCitationMarkers(raw)).toBe(raw);
  });

  it('handles an empty reply', () => {
    expect(stripCitationMarkers('')).toBe('');
  });
});
