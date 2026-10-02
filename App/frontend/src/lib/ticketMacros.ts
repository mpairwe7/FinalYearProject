/** Catalog canned replies for the staff composer. Staff-facing only. */

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
    label: "TIN in order",
    body: "Your TIN is active. No further action is needed on registration. If a return or payment is outstanding, say which tax type and the period and I will outline the steps.",
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
    label: "Request document upload",
    body: "Please attach your invoice, assessment notice, or payment receipt directly in this chat (PDF, Word, Excel, CSV, or Image up to 40 MB). Our automated document inspection will extract the figures and verify compliance immediately.",
  },
  {
    id: "efris_verified",
    label: "EFRIS invoice verified",
    body: "I have audited your attached invoice. The supplier and buyer TINs are valid, the 18% standard VAT calculation reconciles with URA records, and the EFRIS fiscal signature is authentic. Your input tax credit claim is compliant.",
  },
  {
    id: "objection_recorded",
    label: "Objection lodged under Sec 24",
    body: "Your notice of objection has been recorded under Section 24 of the Tax Procedures Code Act 2014. Your grounds and supporting documentation have been attached to the case. An official objection decision will be issued within the 90-day statutory period.",
  },
  {
    id: "wht_credit_ok",
    label: "WHT credit verified",
    body: "Your Withholding Tax credit certificate has been verified. The 6% withholding tax credit and Payment Registration Number (PRN) have been reconciled against URA ledgers and can be offset against your periodic Income Tax return.",
  },
  {
    id: "customs_doc_check",
    label: "Customs declaration cleared",
    body: "Customs documentation review complete: your commercial invoice, Bill of Lading, and Single Customs Territory declaration have been matched. Customs valuation is based on the CIF transaction value in accordance with EACCMA regulations.",
  },
];
