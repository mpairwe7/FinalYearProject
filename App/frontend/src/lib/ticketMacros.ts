/** Catalog canned replies for the staff composer. Staff-facing only. */

const PLACEHOLDER = /\[\[([^\]]+)\]\]/g;

/**
 * The parts of a reply still to fill in: text in [[double brackets]] (G636).
 * The case-reference chip's single brackets are not a placeholder.
 */
export function unfilledPlaceholders(text: string): string[] {
  return Array.from(text.matchAll(PLACEHOLDER), (match) => match[1].trim());
}

export interface TicketMacro {
  id: string;
  label: string;
  body: string;
}

export const TICKET_MACROS: readonly TicketMacro[] = [
  {
    id: "ack_wait",
    label: "Acknowledge the wait",
    body: "Thank you for waiting. I have your case and I am looking into it now. I will write back with a clear next step.",
  },
  {
    id: "need_tin",
    label: "Ask for TIN",
    body: "To continue I need your TIN (9 digits). Please reply with the TIN only — do not send a password or a one-time code.",
  },
  {
    id: "tin_ok",
    label: "TIN status",
    body: "I looked up your TIN [[TIN]] in [[the system you checked]]: [[what you found, e.g. active or suspended]]. If a return or payment is outstanding, say which tax type and the period and I will outline the steps.",
  },
  {
    id: "how_to_pay",
    label: "How to pay",
    body: "You can pay through the URA portal (e-Services), a bank, or a mobile-money agent using the PRN on your assessment. Keep the receipt. If you do not have a PRN, say which tax type and I will point you to the right form.",
  },
  {
    id: "office_visit",
    label: "Visit a station",
    body: "This needs a document check that I cannot finish in chat. Please visit your nearest URA station with your TIN, a national ID, and the notice or receipt you were given. Ask for the team that handles this tax type.",
  },
  {
    id: "request_document",
    label: "Request a document",
    // The taxpayer's composer takes no attachments while an officer has the
    // chat, so the document goes by email or to a station (G636).
    body: "Please send a copy of [[the document, e.g. your invoice, assessment notice or payment receipt]] to services@ura.go.ug, quoting your case reference, or bring it to your nearest URA station. Do not send passwords or one-time codes.",
  },
  // G636: these state a result only the officer can give. Each is a template:
  // the parts in [[double brackets]] are what the officer found, and the
  // composer will not send while any are left (`unfilledPlaceholders`).
  {
    id: "efris_verified",
    label: "EFRIS invoice check",
    body: "I checked invoice [[invoice number]] in EFRIS: [[what you found — the supplier and buyer TINs, the VAT on it, and whether the fiscal signature checked out]]. [[What this means for the input tax credit claim]].",
  },
  {
    id: "objection_recorded",
    label: "Objection (Sec 24)",
    body: "Your objection under Section 24 of the Tax Procedures Code Act is [[its status and reference]]. [[What happens next and by when]].",
  },
  {
    id: "wht_credit_ok",
    label: "WHT credit check",
    body: "I checked your withholding tax credit [[certificate or PRN]]: [[what you found]]. [[Whether and how it can be offset against your income tax return]].",
  },
  {
    id: "customs_doc_check",
    label: "Customs documents check",
    body: "I reviewed [[the documents you checked, e.g. commercial invoice, bill of lading, declaration]]: [[what you found]]. [[The next step]].",
  },
];
