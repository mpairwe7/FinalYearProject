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
];
