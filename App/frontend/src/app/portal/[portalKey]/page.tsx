"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Image from "next/image";
import {
  AccessibilityIcon,
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  BellIcon,
  BotIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  CompassIcon,
  ExternalLinkIcon,
  LightbulbIcon,
  LockIcon,
  MenuIcon,
  PhoneIcon,
  SearchIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "../../../components/Icons";
import { CallScreen } from "../../../components/call/CallScreen";
import PortalCopilotCard from "../../../components/PortalCopilotCard";
import { useCallStore } from "../../../store/useCallStore";
import { useChatStore } from "../../../store/useChatStore";
import { LOCALE_OPTIONS } from "../../../lib/locales";
import { URA_CONTACTS, telDigits } from "../../../lib/uraContacts";
import {
  authHeaders,
  getAuthToken,
  getServerAuthToken,
  setAuthToken,
  subscribeAuthToken,
} from "../../../lib/authSession";
import { getChatSessionId } from "../../../lib/chatSession";
import { useTranslation, type TranslationKey } from "../../../lib/i18n";
import {
  consumePendingPortalContinuation,
  sanitizeContinuationFields,
  savePendingPortalContinuation,
} from "../../../lib/portalContinuation";
import type { PortalCopilotPayload } from "../../../store/useChatStore";
import "./portal.css";

interface PortalScreenField {
  name: string;
  label: string;
  labelKey?: TranslationKey;
  selectorId: string;
  type: "text" | "number" | "select" | "textarea" | "date";
  required: boolean;
  placeholder?: string;
  options?: { value: string; label: string; labelKey?: TranslationKey }[];
}

interface PortalScreenConfig {
  key: string;
  title: string;
  titleKey?: TranslationKey;
  formId: string;
  requiresAuth: boolean;
  taskId: string;
  stepTitle: string;
  stepTitleKey?: TranslationKey;
  statutoryCitation: string;
  fields: PortalScreenField[];
  submitLabel: string;
  submitLabelKey?: TranslationKey;
}

const PORTAL_SCREENS: Record<string, PortalScreenConfig> = {
  prn_payments: {
    key: "prn_payments",
    title: "URA e-Services — Payment Registration (PRN)",
    titleKey: "portalWorkspace.prn.title",
    formId: "URA-PAY-PRN-201",
    requiresAuth: false,
    taskId: "payment_generate_prn",
    stepTitle: "Step 1: Tax Head, Taxpayer TIN & Payment Mode",
    stepTitleKey: "portalWorkspace.prn.stepTitle",
    statutoryCitation: "TPCA Cap. 343 s. 37",
    submitLabel: "Generate Payment Registration Number (PRN)",
    submitLabelKey: "portalWorkspace.prn.submitLabel",
    fields: [
      {
        name: "tax_head",
        label: "Tax Head / Revenue Regime",
        labelKey: "portalWorkspace.prn.field.tax_head",
        selectorId: "ddlTaxHead",
        type: "select",
        required: true,
        options: [
          {
            value: "VAT_STANDARD",
            label: "0010 — Value Added Tax (VAT)",
            labelKey: "portalWorkspace.prn.opt.vat",
          },
          {
            value: "CORPORATION_TAX",
            label: "0011 — Income / Corporation Tax",
            labelKey: "portalWorkspace.prn.opt.corp",
          },
          {
            value: "PAYE",
            label: "0012 — Pay As You Earn (PAYE)",
            labelKey: "portalWorkspace.prn.opt.paye",
          },
          {
            value: "WITHHOLDING_TAX",
            label: "0013 — Withholding Tax (WHT)",
            labelKey: "portalWorkspace.prn.opt.wht",
          },
          {
            value: "RENTAL_TAX",
            label: "0014 — Rental Income Tax",
            labelKey: "portalWorkspace.prn.opt.rental",
          },
          {
            value: "PASSPORT_FEE",
            label: "0090 — Non-Tax Revenue (Passport / MDA Fee)",
            labelKey: "portalWorkspace.prn.opt.passport",
          },
        ],
      },
      {
        name: "tin",
        label: "10-Digit Taxpayer TIN",
        labelKey: "portalWorkspace.prn.field.tin",
        selectorId: "txtTIN",
        type: "text",
        required: true,
        placeholder: "100-XXX-XXXX",
      },
      {
        name: "amount_ugx",
        label: "Payment Amount (UGX)",
        labelKey: "portalWorkspace.prn.field.amount_ugx",
        selectorId: "txtAmount",
        type: "number",
        required: true,
        placeholder: "2500000",
      },
      {
        name: "payment_mode",
        label: "Bank / Mobile Money Gateway",
        labelKey: "portalWorkspace.prn.field.payment_mode",
        selectorId: "ddlPaymentMode",
        type: "select",
        required: true,
        options: [
          {
            value: "COMMERCIAL_BANK",
            label: "Commercial Bank Counter / EFT",
            labelKey: "portalWorkspace.prn.opt.bank",
          },
          {
            value: "MOBILE_MONEY",
            label: "Mobile Money (*165# MTN / *185# Airtel)",
            labelKey: "portalWorkspace.prn.opt.momo",
          },
          {
            value: "VISA_MASTERCARD",
            label: "Online Card (VISA / MasterCard)",
            labelKey: "portalWorkspace.prn.opt.card",
          },
        ],
      },
    ],
  },
  tin_registration: {
    key: "tin_registration",
    title: "URA e-Services — Instant Individual TIN Application",
    formId: "URA-REG-TIN-001",
    requiresAuth: false,
    taskId: "tin_apply_individual",
    stepTitle: "Step 1: Individual Applicant Identity & NIRA NIN Verification",
    statutoryCitation: "TPCA Cap. 343 s. 4",
    submitLabel: "Submit TIN Application Draft",
    fields: [
      {
        name: "entity_type",
        label: "Applicant Entity Category",
        selectorId: "ddlEntityType",
        type: "select",
        required: true,
        options: [{ value: "individual", label: "Individual Citizen (NIRA NIN)" }],
      },
      {
        name: "nin",
        label: "14-Character NIRA NIN",
        selectorId: "txtNIN",
        type: "text",
        required: true,
        placeholder: "CM-14-CHAR-NIN",
      },
      {
        name: "surname",
        label: "Surname",
        selectorId: "txtFullName",
        type: "text",
        required: true,
        placeholder: "Mukasa",
      },
      {
        name: "given_name",
        label: "Given Name",
        selectorId: "txtGivenName",
        type: "text",
        required: true,
        placeholder: "Sarah",
      },
      {
        name: "date_of_birth",
        label: "Date of Birth",
        selectorId: "txtDob",
        type: "date",
        required: true,
        placeholder: "1992-05-14",
      },
      {
        name: "mobile_number",
        label: "Mobile Telephone Number",
        selectorId: "txtMobile",
        type: "text",
        required: true,
        placeholder: "+256772123456",
      },
      {
        name: "email",
        label: "Email Address",
        selectorId: "txtEmail",
        type: "text",
        required: true,
        placeholder: "sarah.mukasa@example.ug",
      },
    ],
  },
  return_filing: {
    key: "return_filing",
    title: "URA e-Services — Monthly & Annual Return Filing",
    formId: "URA-DT-RET-101",
    requiresAuth: true,
    taskId: "return_filing_submit",
    stepTitle: "Step 2: Macro-Validated Schedule & Statutory Declaration",
    statutoryCitation: "TPCA Cap. 343 s. 16",
    submitLabel: "Personally Declare & Submit Return (TPCA s. 16)",
    fields: [
      {
        name: "tax_head",
        label: "Return Type",
        selectorId: "ddlReturnType",
        type: "select",
        required: true,
        options: [
          { value: "VAT_STANDARD", label: "Monthly VAT Return (Form DT-1014)" },
          { value: "PAYE", label: "Monthly PAYE Return" },
          { value: "CORPORATION_TAX", label: "Income Tax Return (Form DT-1001)" },
        ],
      },
      {
        name: "tin",
        label: "10-Digit Taxpayer TIN",
        selectorId: "txtTIN",
        type: "text",
        required: true,
        placeholder: "100-XXX-XXXX",
      },
      {
        name: "amount_ugx",
        label: "Declared Net Tax Payable (UGX)",
        selectorId: "txtNetTaxPayable",
        type: "number",
        required: true,
        placeholder: "1500000",
      },
    ],
  },
  tax_clearance: {
    key: "tax_clearance",
    title: "URA e-Services — Tax Clearance Certificate (TCC) Application",
    formId: "URA-TCC-301",
    requiresAuth: true,
    taskId: "tax_clearance_apply",
    stepTitle: "Step 1: Compliance Verification & TCC Purpose",
    statutoryCitation: "TPCA Cap. 343 s. 43",
    submitLabel: "Submit TCC Statutory Application",
    fields: [
      {
        name: "tin",
        label: "Applicant 10-Digit TIN",
        selectorId: "txtTccTin",
        type: "text",
        required: true,
        placeholder: "100-XXX-XXXX",
      },
      {
        name: "tcc_purpose",
        label: "Statutory Purpose of TCC",
        selectorId: "ddlTccPurpose",
        type: "select",
        required: true,
        options: [
          { value: "BIDDING_TENDER", label: "Public Procurement Bidding / Tender" },
          { value: "LICENSE_RENEWAL", label: "License Renewal" },
          { value: "WORK_PERMIT", label: "Work Permit / Immigration" },
          { value: "VEHICLE_TRANSFER", label: "Motor Vehicle Transfer" },
        ],
      },
    ],
  },
  efris: {
    key: "efris",
    title: "URA EFRIS — Electronic Fiscal Invoice & Stock Portal",
    formId: "URA-EFRIS-401",
    requiresAuth: true,
    taskId: "efris_fiscal_invoice",
    stepTitle: "Step 1: Buyer TIN Verification & UNSPSC Commodity Line",
    statutoryCitation: "TPCA Cap. 343 s. 73A",
    submitLabel: "Issue Fiscal Invoice (20-Digit FDN)",
    fields: [
      {
        name: "buyer_tin",
        label: "Buyer 10-Digit TIN",
        selectorId: "buyerTin",
        type: "text",
        required: true,
        placeholder: "100-YYY-YYYY",
      },
      {
        name: "commodity_code",
        label: "UNSPSC Commodity Code",
        selectorId: "commodityCode",
        type: "text",
        required: true,
        placeholder: "50202301",
      },
      {
        name: "quantity",
        label: "Quantity",
        selectorId: "txtQuantity",
        type: "number",
        required: true,
        placeholder: "10",
      },
    ],
  },
  dts: {
    key: "dts",
    title: "URA Digital Tax Stamps (DTS) — Kakasa Portal",
    formId: "URA-DTS-501",
    requiresAuth: true,
    taskId: "dts_order_stamps",
    stepTitle: "Step 1: Gazetted Commodity & Stamp Reel Quantity",
    statutoryCitation: "Tax Stamps Regulations",
    submitLabel: "Submit Digital Tax Stamp Order",
    fields: [
      {
        name: "tin",
        label: "Manufacturer / Importer 10-Digit TIN",
        selectorId: "txtTIN",
        type: "text",
        required: true,
        placeholder: "100-XXX-XXXX",
      },
      {
        name: "product_category",
        label: "Excise Product Category",
        selectorId: "ddlDtsCategory",
        type: "select",
        required: true,
        options: [
          { value: "BOTTLED_WATER", label: "Bottled Water (UGX 13 / stamp)" },
          { value: "SODA", label: "Soda / Non-Alcoholic Beverage" },
          { value: "BEER", label: "Beer (UGX 36 / stamp)" },
          { value: "SPIRITS", label: "Spirits (UGX 55 / stamp)" },
          { value: "CEMENT", label: "Cement (UGX 135 / stamp)" },
        ],
      },
      {
        name: "quantity",
        label: "Stamp Quantity",
        selectorId: "txtStampQuantity",
        type: "number",
        required: true,
        placeholder: "10000",
      },
    ],
  },
  objection_appeals: {
    key: "objection_appeals",
    title: "URA e-Services — Section 24 Tax Assessment Objection",
    formId: "URA-OBJ-601",
    requiresAuth: true,
    taskId: "objection_appeals_lodge",
    stepTitle: "Step 1: Assessment Reference & 45-Day Grounds of Objection",
    statutoryCitation: "TPCA Cap. 343 s. 24",
    submitLabel: "Lodge Formal Section 24 Objection",
    fields: [
      {
        name: "assessment_ref",
        label: "Assessment Registration Number (ARN)",
        selectorId: "txtAssessmentRef",
        type: "text",
        required: true,
        placeholder: "ASMT-2026-00912",
      },
      {
        name: "grounds",
        label: "Detailed Legal & Factual Grounds of Objection",
        selectorId: "txtObjectionGrounds",
        type: "textarea",
        required: true,
        placeholder: "State the specific grounds on which you dispute the assessment...",
      },
    ],
  },
  motor_vehicle: {
    key: "motor_vehicle",
    title: "URA Motor Vehicle & Customs SAD Portal",
    formId: "URA-MV-701",
    requiresAuth: true,
    taskId: "payment_verify_advance_tax",
    stepTitle: "Step 1: Vehicle Registration Plate, Customs SAD / VIN & Plate Category",
    statutoryCitation: "Traffic and Road Safety Act & EACCMA",
    submitLabel: "Proceed to Assessment",
    fields: [
      {
        name: "vehicle_registration_number",
        label: "Motor Vehicle Registration Plate",
        selectorId: "txtVehicleRegistrationNumber",
        type: "text",
        required: true,
        placeholder: "UBK 412A",
      },
      {
        name: "chassis_vin",
        label: "Customs SAD C-Number or 17-Character Chassis VIN",
        selectorId: "txtChassisVin",
        type: "text",
        required: true,
        placeholder: "JTEBU29J805012345",
      },
      {
        name: "plate_category",
        label: "Registration / ITMS Digital Plate Category",
        selectorId: "ddlVehicleCategory",
        type: "select",
        required: true,
        options: [
          { value: "STANDARD_FIRST_REG", label: "First Registration & ITMS Digital Plate" },
          { value: "OWNERSHIP_TRANSFER", label: "Motor Vehicle Ownership Transfer (Form TR VII)" },
        ],
      },
    ],
  },
  get_refund: {
    key: "get_refund",
    title: "URA e-Services — Tax Refund Claim (Section 42 TPCA)",
    formId: "URA-REF-801",
    requiresAuth: true,
    taskId: "get_refund_claim",
    stepTitle: "Step 1: Refund Tax Head, Claim Amount & EFT Bank Account",
    statutoryCitation: "TPCA Cap. 343 s. 42",
    submitLabel: "Submit Statutory Refund Claim",
    fields: [
      {
        name: "tin",
        label: "10-Digit Taxpayer TIN",
        selectorId: "txtTIN",
        type: "text",
        required: true,
        placeholder: "100-XXX-XXXX",
      },
      {
        name: "amount_ugx",
        label: "Refund Claim Amount (UGX)",
        selectorId: "txtRefundAmount",
        type: "number",
        required: true,
        placeholder: "5000000",
      },
    ],
  },
};

PORTAL_SCREENS.e_services = PORTAL_SCREENS.return_filing;
PORTAL_SCREENS.asycuda = PORTAL_SCREENS.motor_vehicle;

const SECRET_FIELD_RE = /(?:password|passwd|pwd|otp|pin|cvv|cvc|csc|secret|token|passcode)/i;

/**
 * The services, in the order of ura.go.ug's quick-links band ("Make a Payment |
 * Get a TIN | File a Return | EFRIS | DTS | …"), then the rest of this workspace.
 */
const PORTAL_TAB_KEYS: { key: string; labelKey: TranslationKey }[] = [
  { key: "prn_payments", labelKey: "portalWorkspace.tab.prn_payments" },
  { key: "tin_registration", labelKey: "portalWorkspace.tab.tin_registration" },
  { key: "return_filing", labelKey: "portalWorkspace.tab.return_filing" },
  { key: "efris", labelKey: "portalWorkspace.tab.efris" },
  { key: "dts", labelKey: "portalWorkspace.tab.dts" },
  { key: "tax_clearance", labelKey: "portalWorkspace.tab.tax_clearance" },
  { key: "get_refund", labelKey: "portalWorkspace.tab.get_refund" },
  { key: "objection_appeals", labelKey: "portalWorkspace.tab.objection_appeals" },
  { key: "motor_vehicle", labelKey: "portalWorkspace.tab.motor_vehicle" },
];

const URA_SITE = "https://ura.go.ug/en/";

/** A mega-menu entry: one of this workspace's screens, or a page on ura.go.ug. */
interface MenuLink {
  labelKey: TranslationKey;
  portalKey?: string;
  href?: string;
}

interface MainMenuItem {
  id: string;
  labelKey: TranslationKey;
  href?: string;
  /** Columns, as ura.go.ug lays its mega menus out. */
  mega?: MenuLink[][];
  /** The section this workspace belongs to; shown selected, as the site does. */
  current?: boolean;
}

/*
 * ura.go.ug's main navigation and its two mega menus, as the owner's
 * screenshots show them (2026-10-27). Entries this workspace serves open here;
 * the rest open the official page in a new tab. Every official URL below is a
 * page the project's own crawl of ura.go.ug (Data/crawl) fetched; entries whose
 * page it never saw (Whistle Blow, Voluntary Disclosure, Stamp Duty, Sitemap)
 * are left out rather than guessed.
 */
const DOMESTIC_TAXES_MEGA: MenuLink[][] = [
  [
    { labelKey: "portalWorkspace.tab.tin_registration", portalKey: "tin_registration" },
    { labelKey: "portalWorkspace.tab.return_filing", portalKey: "return_filing" },
    { labelKey: "portalWorkspace.tab.prn_payments", portalKey: "prn_payments" },
  ],
  [
    { labelKey: "portalWorkspace.tab.efris", portalKey: "efris" },
    { labelKey: "portalWorkspace.tab.dts", portalKey: "dts" },
    { labelKey: "portalWorkspace.tab.get_refund", portalKey: "get_refund" },
  ],
  [
    {
      labelKey: "portalWorkspace.mega.taxIncentives",
      href: "https://ura.go.ug/en/download/a-guide-on-tax-incentives-exemptions-available-to-the-uganda-investors/",
    },
    { labelKey: "portalWorkspace.tab.motor_vehicle", portalKey: "motor_vehicle" },
    { labelKey: "portalWorkspace.mega.chooseAgent", href: "https://ura.go.ug/en/choose-agents/" },
  ],
  [
    { labelKey: "portalWorkspace.tab.objection_appeals", portalKey: "objection_appeals" },
    { labelKey: "portalWorkspace.tab.tax_clearance", portalKey: "tax_clearance" },
  ],
];

const LEGAL_POLICY_MEGA: MenuLink[][] = [
  [
    { labelKey: "portalWorkspace.mega.laws", href: "https://ura.go.ug/download-category/laws-and-acts/" },
    {
      labelKey: "portalWorkspace.mega.dta",
      href: "https://ura.go.ug/en/category/legal-policy/double-taxation-agreements/",
    },
  ],
  [
    {
      labelKey: "portalWorkspace.mega.caseSummaries",
      href: "https://ura.go.ug/download-category/case-summery-reports/",
    },
    {
      labelKey: "portalWorkspace.mega.competentAuthority",
      href: "https://ura.go.ug/en/delegated-competent-authorities/",
    },
  ],
  [{ labelKey: "portalWorkspace.mega.debtCollections", href: "https://ura.go.ug/download-category/debt-collections/" }],
  [
    {
      labelKey: "portalWorkspace.mega.fia",
      href: "https://ura.go.ug/download-category/financial-intelligence-authority/",
    },
  ],
];

const MAIN_MENU: MainMenuItem[] = [
  { id: "domestic", labelKey: "portalWorkspace.nav.domesticTaxes", mega: DOMESTIC_TAXES_MEGA, current: true },
  { id: "import", labelKey: "portalWorkspace.nav.importExport", href: "https://ura.go.ug/en/import-export/" },
  { id: "legal", labelKey: "portalWorkspace.nav.legalPolicy", mega: LEGAL_POLICY_MEGA },
  { id: "education", labelKey: "portalWorkspace.nav.taxEducation", href: "https://ura.go.ug/en/category/tax-education/" },
  { id: "opportunities", labelKey: "portalWorkspace.nav.opportunities", href: "https://ura.go.ug/en/category/opportunities/" },
  { id: "research", labelKey: "portalWorkspace.nav.research", href: "https://ura.go.ug/en/category/publications/" },
];

/**
 * Interactive URA Portal Workspace with the Inbuilt AI Co-Pilot side rail (Approach 3)
 * and live DOM telemetry bridge (`portal_context`).
 */
export default function InbuiltPortalCopilotPage() {
  const t = useTranslation();
  const params = useParams<{ portalKey?: string }>();
  const searchParams = useSearchParams();
  const rawKey = String(params?.portalKey || "prn_payments").toLowerCase();
  const screen = PORTAL_SCREENS[rawKey] || PORTAL_SCREENS.prn_payments;

  const conversationId = searchParams?.get("conversation_id") || "default";
  const openCall = useCallStore((s) => s.openCall);
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const isSignedIn = Boolean(token);

  const [values, setValues] = useState<Record<string, string>>({});
  const [demoPlaceholders, setDemoPlaceholders] = useState<Record<string, string>>({});
  const [statusNotice, setStatusNotice] = useState("");
  const [errorBanner, setErrorBanner] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [spotlightField, setSpotlightField] = useState<string | null>(null);
  const [coachmarks, setCoachmarks] = useState<Record<string, string>>({});
  const [copilotCard, setCopilotCard] = useState<PortalCopilotPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [stalledMs, setStalledMs] = useState(0);
  const lastActivityRef = useRef<number>(Date.now());
  const autoFilledKeyRef = useRef<string | null>(null);
  const savedContinuationRef = useRef<Record<string, string>>({});
  const fieldRefs = useRef<Record<string, HTMLElement | null>>({});

  const localizedTitle = screen.titleKey ? t(screen.titleKey) : screen.title;
  const localizedStepTitle = screen.stepTitleKey ? t(screen.stepTitleKey) : screen.stepTitle;
  const localizedSubmitLabel = screen.submitLabelKey
    ? t(screen.submitLabelKey)
    : screen.submitLabel;

  const getFieldLabel = useCallback(
    (field: PortalScreenField) => (field.labelKey ? t(field.labelKey) : field.label),
    [t],
  );

  // Consume any pending continuation state once on arrival and reset transient errors on screen switch
  useEffect(() => {
    const pending = consumePendingPortalContinuation();
    if (pending?.collectedFields) {
      savedContinuationRef.current = {
        ...savedContinuationRef.current,
        ...pending.collectedFields,
      };
    }
    setValues((prev) => {
      const next: Record<string, string> = {};
      for (const f of screen.fields) {
        const candidate =
          pending?.collectedFields?.[f.name] ||
          savedContinuationRef.current[f.name] ||
          prev[f.name] ||
          "";
        const isAllowed =
          f.type !== "select" || (f.options ?? []).some((opt) => opt.value === candidate);
        next[f.name] = isAllowed ? candidate : "";
      }
      return next;
    });
    setDemoPlaceholders({});
    setStatusNotice("");
    setErrorBanner("");
    setFieldErrors({});
    setSpotlightField(null);
    setCoachmarks({});
    setCopilotCard(null);
  }, [screen.fields, screen.key]);

  const emptyRequiredFields = useMemo(
    () =>
      screen.fields
        .filter((f) => f.required && !String(values[f.name] || "").trim())
        .map((f) => f.name),
    [screen.fields, values],
  );

  // Real-time 15-second inactivity stall detector
  useEffect(() => {
    const timer = window.setInterval(() => {
      const elapsed = Date.now() - lastActivityRef.current;
      setStalledMs(elapsed);
    }, 3000);
    return () => window.clearInterval(timer);
  }, []);

  const recordFieldChange = useCallback((name: string, val: string) => {
    lastActivityRef.current = Date.now();
    setStalledMs(0);
    setStatusNotice("");
    setValues((prev) => ({ ...prev, [name]: val }));
  }, []);

  /**
   * Send live DOM telemetry (`portal_context`) to `/api/v1/chat` and execute returned DOM spotlight recipes.
   */
  const askCopilotWithDomTelemetry = useCallback(
    async (
      promptText: string,
      submitTask = false,
      overrideFields?: Record<string, string>,
      overrideFieldErrors?: Record<string, string>,
      overrideErrorBanner?: string,
    ) => {
      setBusy(true);
      try {
        const activeValues = overrideFields ?? values;
        const activeFieldErrors = overrideFieldErrors ?? fieldErrors;
        const activeErrorBanner = overrideErrorBanner ?? errorBanner;
        const activeEmptyRequired = screen.fields
          .filter((f) => f.required && !String(activeValues[f.name] || "").trim())
          .map((f) => f.name);

        const validationErrors = Object.entries(activeFieldErrors).map(([field, message]) => {
          const spec = screen.fields.find((s) => s.name === field);
          return {
            field,
            message,
            selector: spec ? `#${spec.selectorId}` : `[name='${field}']`,
          };
        });

        // Client-side defense-in-depth scrubbing of any secret/credential field names
        const scrubbedVisible: Record<string, string> = {};
        for (const [k, v] of Object.entries(activeValues)) {
          if (!SECRET_FIELD_RE.test(k) && String(v ?? "").trim() !== "") {
            scrubbedVisible[k] = v;
          }
        }

        const payload: Record<string, unknown> = {
          message: promptText,
          conversation_id: conversationId,
          portal_context: {
            portal_id: screen.key,
            current_route: `/portal/${screen.key}`,
            active_step: localizedStepTitle,
            form_id: screen.formId,
            visible_fields: scrubbedVisible,
            empty_required_fields: activeEmptyRequired.filter((k) => !SECRET_FIELD_RE.test(k)),
            validation_errors: validationErrors,
            error_banner: activeErrorBanner,
            stalled_duration_ms: Math.min(3_600_000, Math.max(stalledMs, 15000)),
            last_user_action: submitTask ? "task_execution_requested" : "user_clicked_help_me",
          },
          ...(submitTask
            ? {
                portal_task_submission: {
                  task_id: screen.taskId,
                  fields: scrubbedVisible,
                },
              }
            : {}),
        };

        const res = await fetch("/api/v1/chat", {
          method: "POST",
          headers: authHeaders({
            "Content-Type": "application/json",
            "X-Session-ID": getChatSessionId(),
          }),
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setCopilotCard(null);
          setErrorBanner(
            typeof data.detail === "string" && data.detail
              ? data.detail
              : t("portalWorkspace.requestErrorBanner"),
          );
          return;
        }
        const card = (data.portal_copilot ?? data.portalCopilot) as
          | PortalCopilotPayload
          | undefined;
        if (card) {
          setCopilotCard(card);
          const nextCoachmarks: Record<string, string> = {};
          let firstSpot: string | null = null;
          for (const act of card.dom_actions ?? []) {
            const matchedField = screen.fields.find(
              (f) =>
                act.selector.includes(f.selectorId) ||
                act.selector.includes(f.name) ||
                act.element.toLowerCase().includes(f.name.toLowerCase()),
            );
            if (matchedField) {
              nextCoachmarks[matchedField.name] = act.tooltip;
              if (!firstSpot && (act.action_type === "spotlight" || act.scroll_to)) {
                firstSpot = matchedField.name;
              }
            }
          }
          setCoachmarks(nextCoachmarks);
          if (firstSpot) {
            setSpotlightField(firstSpot);
            const node = fieldRefs.current[firstSpot];
            node?.scrollIntoView?.({ behavior: "smooth", block: "center" });
          }
        }
      } catch {
        setCopilotCard(null);
        setErrorBanner(t("portalWorkspace.requestErrorBanner"));
      } finally {
        setBusy(false);
      }
    },
    [
      conversationId,
      errorBanner,
      fieldErrors,
      localizedStepTitle,
      screen.fields,
      screen.formId,
      screen.key,
      screen.taskId,
      stalledMs,
      t,
      values,
    ],
  );

  /**
   * Restore user-supplied continuation fields into `values`, while keeping any example
   * placeholder hints strictly separate from executable form `values` (`demoPlaceholders`).
   */
  const handleAutoFillSafeFields = useCallback(() => {
    const userContinuationFields: Record<string, string> = {
      ...savedContinuationRef.current,
      ...sanitizeContinuationFields(copilotCard?.preserved_fields),
    };
    const examplePlaceholderHints: Record<string, string> = {
      tax_head: "VAT_STANDARD",
      payment_mode: "COMMERCIAL_BANK",
      entity_type: "individual",
      tcc_purpose: "BIDDING_TENDER",
      commodity_code: "50202301",
      product_category: "BOTTLED_WATER",
      plate_category: "STANDARD_FIRST_REG",
      vehicle_registration_number: "UBK 412A",
      chassis_vin: "JTEBU29J805012345",
      assessment_ref: "ASMT-2026-00912",
    };

    lastActivityRef.current = Date.now();
    setStalledMs(0);

    const restored: Record<string, string> = {};
    for (const f of screen.fields) {
      const candidate = userContinuationFields[f.name];
      const isAllowed =
        f.type !== "select" || (f.options ?? []).some((opt) => opt.value === candidate);
      if (!values[f.name] && candidate && isAllowed && !SECRET_FIELD_RE.test(f.name)) {
        restored[f.name] = candidate;
      }
    }
    const restoredCount = Object.keys(restored).length;
    if (restoredCount > 0) {
      setValues((prev) => {
        const next: Record<string, string> = { ...prev };
        for (const [name, val] of Object.entries(restored)) {
          if (!next[name]) next[name] = val;
        }
        return next;
      });
    }

    setDemoPlaceholders((prev) => {
      const next: Record<string, string> = { ...prev };
      for (const f of screen.fields) {
        if (examplePlaceholderHints[f.name]) {
          next[f.name] = examplePlaceholderHints[f.name];
        }
      }
      return next;
    });

    if (restoredCount === 0) {
      setStatusNotice(t("portalWorkspace.noSavedAutofillNotice"));
    } else {
      setStatusNotice("");
    }
    setFieldErrors({});
    setErrorBanner("");
  }, [copilotCard?.preserved_fields, screen.fields, t, values]);

  useEffect(() => {
    if (searchParams?.get("autofill") === "1" && autoFilledKeyRef.current !== screen.key) {
      autoFilledKeyRef.current = screen.key;
      handleAutoFillSafeFields();
    }
  }, [handleAutoFillSafeFields, screen.key, searchParams]);

  const handlePortalFormSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const errs: Record<string, string> = {};
      for (const f of screen.fields) {
        const v = String(values[f.name] || "").trim();
        const label = getFieldLabel(f);
        if (f.required && !v) {
          errs[f.name] = t("portalWorkspace.fieldMandatoryError", { field: label });
        } else if (f.name === "tin" && v && !/^\d{10}$/.test(v)) {
          errs[f.name] = t("portalWorkspace.tinFormatError");
        }
      }
      if (Object.keys(errs).length > 0) {
        const nextBanner = t("portalWorkspace.validationBanner");
        setFieldErrors(errs);
        setErrorBanner(nextBanner);
        const firstErrField = Object.keys(errs)[0];
        setSpotlightField(firstErrField);
        fieldRefs.current[firstErrField]?.scrollIntoView?.({ behavior: "smooth", block: "center" });
        void askCopilotWithDomTelemetry(
          "I hit a validation error on this portal form, help me fix it",
          false,
          values,
          errs,
          nextBanner,
        );
        return;
      }
      setFieldErrors({});
      setErrorBanner("");
      void askCopilotWithDomTelemetry(`Execute ${localizedTitle}`, true, values, {}, "");
    },
    [askCopilotWithDomTelemetry, getFieldLabel, localizedTitle, screen.fields, t, values],
  );

  const locale = useChatStore((s) => s.locale);
  const setLocale = useChatStore((s) => s.setLocale);
  const [largeText, setLargeText] = useState(false);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const navRef = useRef<HTMLElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const guideButtonRef = useRef<HTMLButtonElement | null>(null);

  const closeMenus = useCallback(() => {
    setOpenMenu(null);
    setMobileMenuOpen(false);
  }, []);

  // A mega menu closes on Escape and on a click outside the navigation.
  useEffect(() => {
    if (!openMenu) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    const onPointerDown = (event: PointerEvent) => {
      if (!navRef.current?.contains(event.target as Node)) setOpenMenu(null);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [openMenu]);

  /** The "Chat with us." launcher: bring the Co-Pilot rail into view and focus it. */
  const focusCopilot = useCallback(() => {
    railRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    guideButtonRef.current?.focus({ preventScroll: true });
  }, []);

  /** The header's search asks the Co-Pilot, in the context of this screen. */
  const handleSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const query = searchQuery.trim();
      if (!query) return;
      focusCopilot();
      void askCopilotWithDomTelemetry(query, false);
    },
    [askCopilotWithDomTelemetry, focusCopilot, searchQuery],
  );

  const handlePersistBeforeSignIn = useCallback(() => {
    savePendingPortalContinuation({
      conversationId,
      taskId: screen.taskId,
      portalKey: screen.key,
      collectedFields: sanitizeContinuationFields(values),
      returnMode: "portal",
    });
  }, [conversationId, screen.key, screen.taskId, values]);

  const handleDemoQuickSignIn = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/auth/dev-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: ["10000", "23456"].join(""),
          role: "verified_taxpayer",
          tenant_id: "default",
          granted_purposes: ["ura_account_access", "ura_actions"],
        }),
      });
      if (res.ok) {
        const data = await res.json();
        const issuedToken = data.token || data.access_token;
        if (issuedToken) {
          setAuthToken(issuedToken, "dev");
        }
      }
    } catch {
      // Ignore in production where dev-token is disabled
    }
  }, []);

  const currentPortalPath = `/portal/${screen.key}?conversation_id=${encodeURIComponent(conversationId)}&copilot=1`;
  const signInHref = `/signin?continue=signin&returnTo=${encodeURIComponent(currentPortalPath)}`;
  const chatHref = `/?conversation_id=${encodeURIComponent(conversationId)}`;
  const portalHref = (key: string) =>
    `/portal/${key}?conversation_id=${encodeURIComponent(conversationId)}&copilot=1`;
  const serviceTab = PORTAL_TAB_KEYS.find((tab) => tab.key === screen.key);
  const serviceLabel = serviceTab ? t(serviceTab.labelKey) : localizedTitle;

  return (
    <div
      className={`ura-portal-workspace${largeText ? " is-large-text" : ""}`}
      data-testid="inbuilt-portal-workspace"
    >
      <a className="urap-skip" href="#portal-screen-heading">
        {t("portalWorkspace.skipToForm")}
      </a>

      {/* ura.go.ug's header: the logo, the utility links, search, FAQs and language. */}
      <header className="urap-header">
        <div className="urap-shell urap-header-inner">
          <a
            className="urap-logo"
            href={URA_SITE}
            target="_blank"
            rel="noopener noreferrer"
            title={t("portalWorkspace.officialSite")}
          >
            <Image
              src="/ura-logo-wordmark.png"
              alt={t("portalWorkspace.brandTitle")}
              width={148}
              height={91}
              priority
            />
          </a>
          <div className="urap-header-right">
            <nav className="urap-utility" aria-label={t("portalWorkspace.utilityNavLabel")}>
              <a className="urap-utility-link has-dot" href="#urap-contact">
                {t("portalWorkspace.util.contact")}
              </a>
              <a
                className="urap-utility-link"
                href="https://ura.go.ug/en/about-ura/"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t("portalWorkspace.util.about")}
              </a>
              <a
                className="urap-utility-link"
                href="https://ura.go.ug/en/media-center/"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t("portalWorkspace.util.media")} <BellIcon size={15} />
              </a>
              <button
                type="button"
                className="urap-utility-link"
                aria-label={t("call.button")}
                onClick={openCall}
                data-testid="portal-call-ura-btn"
              >
                <PhoneIcon size={14} /> {t("call.button")}
              </button>
              {isSignedIn ? (
                <span className="urap-utility-link is-signed-in">
                  <CheckCircleIcon /> {t("portalWorkspace.util.signedIn")}
                </span>
              ) : (
                <Link
                  className="urap-utility-link has-dot is-login"
                  href={signInHref}
                  onClick={handlePersistBeforeSignIn}
                >
                  {t("portalWorkspace.util.login")}
                </Link>
              )}
            </nav>
            <div className="urap-tools">
              <form className="urap-search" role="search" onSubmit={handleSearch}>
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("portalWorkspace.search")}
                  aria-label={t("portalWorkspace.searchLabel")}
                />
                <button type="submit" aria-label={t("portalWorkspace.search")}>
                  <SearchIcon />
                </button>
              </form>
              <a
                className="urap-select"
                href="https://ura.go.ug/en/crm-faqs/"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t("portalWorkspace.faqs")} <ChevronDownIcon />
              </a>
              <label className="urap-select urap-language">
                <span className="sr-only">{t("portalWorkspace.language")}</span>
                <select value={locale} onChange={(e) => setLocale(e.target.value)}>
                  {LOCALE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.native}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </div>
      </header>

      {/* The blue section bar, its accessibility control and mega menus. */}
      <nav className="urap-mainnav" aria-label={t("portalWorkspace.mainNavLabel")} ref={navRef}>
        <div className="urap-mainnav-inner">
          <button
            type="button"
            className="urap-a11y"
            aria-pressed={largeText}
            aria-label={t("portalWorkspace.largeText")}
            title={t("portalWorkspace.largeText")}
            onClick={() => setLargeText((on) => !on)}
          >
            <AccessibilityIcon />
          </button>
          <button
            type="button"
            className="urap-menu-toggle"
            aria-expanded={mobileMenuOpen}
            aria-controls="urap-menu"
            onClick={() => setMobileMenuOpen((open) => !open)}
          >
            <MenuIcon /> {t("portalWorkspace.menu")}
          </button>
          <ul id="urap-menu" className={`urap-menu${mobileMenuOpen ? " is-open" : ""}`}>
            {MAIN_MENU.map((item) => {
              const isOpen = openMenu === item.id;
              return (
                <li
                  key={item.id}
                  className={`urap-menu-item${item.mega ? " has-mega" : ""}${isOpen ? " is-open" : ""}${
                    item.current ? " is-current" : ""
                  }`}
                  onPointerEnter={
                    item.mega
                      ? (e) => {
                          if (e.pointerType === "mouse") setOpenMenu(item.id);
                        }
                      : undefined
                  }
                  onPointerLeave={
                    item.mega
                      ? (e) => {
                          if (e.pointerType === "mouse") {
                            setOpenMenu((current) => (current === item.id ? null : current));
                          }
                        }
                      : undefined
                  }
                >
                  {item.mega ? (
                    <button
                      type="button"
                      className="urap-menu-link"
                      aria-expanded={isOpen}
                      aria-controls={`urap-mega-${item.id}`}
                      onClick={() => setOpenMenu(isOpen ? null : item.id)}
                    >
                      {t(item.labelKey)} <ChevronDownIcon />
                    </button>
                  ) : (
                    <a className="urap-menu-link" href={item.href} target="_blank" rel="noopener noreferrer">
                      {t(item.labelKey)}
                      <span className="sr-only"> {t("portalWorkspace.opensNewTab")}</span>
                    </a>
                  )}
                  {item.mega && isOpen ? (
                    <div id={`urap-mega-${item.id}`} className="urap-mega">
                      <div className="urap-shell urap-mega-grid">
                        {item.mega.map((column, columnIndex) => (
                          <ul key={columnIndex} className="urap-mega-col">
                            {column.map((link) => (
                              <li key={link.labelKey}>
                                {link.portalKey ? (
                                  <Link
                                    href={portalHref(link.portalKey)}
                                    className={`urap-mega-link${link.portalKey === screen.key ? " is-current" : ""}`}
                                    aria-current={link.portalKey === screen.key ? "page" : undefined}
                                    onClick={closeMenus}
                                  >
                                    {t(link.labelKey)}
                                  </Link>
                                ) : (
                                  <a
                                    href={link.href}
                                    className="urap-mega-link"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={closeMenus}
                                  >
                                    {t(link.labelKey)} <ExternalLinkIcon size={12} />
                                    <span className="sr-only"> {t("portalWorkspace.opensNewTab")}</span>
                                  </a>
                                )}
                              </li>
                            ))}
                          </ul>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      </nav>

      {/* An inner page's banner: breadcrumb and Back, the yellow title, the quick-links band. */}
      <section className="urap-banner" aria-label={serviceLabel}>
        <div className="urap-shell urap-banner-top">
          <nav aria-label={t("portalWorkspace.breadcrumbLabel")}>
            <ol className="urap-breadcrumb">
              <li>
                <a href={URA_SITE} target="_blank" rel="noopener noreferrer">
                  {t("portalWorkspace.breadcrumbHome")}
                </a>
              </li>
              <li>{t("portalWorkspace.nav.domesticTaxes")}</li>
              <li aria-current="page">{serviceLabel}</li>
            </ol>
          </nav>
          <Link href={chatHref} className="urap-back" data-testid="portal-back-to-chat-link">
            <ArrowLeftIcon size={14} /> {t("portalWorkspace.returnToChat")}
          </Link>
        </div>
        <div className="urap-shell urap-banner-body">
          <p className="urap-banner-title">{serviceLabel}</p>
          <p className="urap-banner-sub">
            {t("portalWorkspace.brandSubtitle", {
              formId: screen.formId,
              citation: screen.statutoryCitation,
            })}
          </p>
        </div>
        <nav className="urap-quicklinks" aria-label={t("portalWorkspace.servicesNavLabel")}>
          <div className="urap-shell urap-quicklinks-inner">
            {PORTAL_TAB_KEYS.map((tab) => (
              <Link
                key={tab.key}
                href={portalHref(tab.key)}
                className={`urap-quicklink${screen.key === tab.key ? " is-active" : ""}`}
                aria-current={screen.key === tab.key ? "page" : undefined}
              >
                {t(tab.labelKey)}
              </Link>
            ))}
          </div>
        </nav>
      </section>

      <div className="urap-shell urap-body">
        <main className="ura-portal-main">
          {screen.requiresAuth && !isSignedIn ? (
            <section
              className="ura-portal-auth-gate"
              data-testid="portal-auth-gate"
              role="region"
              aria-label={t("portalCopilot.stepUpBadge")}
            >
              <h2>
                <LockIcon size={18} /> {t("portalWorkspace.authGateTitle", { title: localizedTitle })}
              </h2>
              <p>
                {t("portalWorkspace.authGateBody", {
                  citation: screen.statutoryCitation,
                })}
              </p>
              <div className="ura-portal-auth-actions">
                <Link
                  href={signInHref}
                  onClick={handlePersistBeforeSignIn}
                  className="ura-copilot-btn is-primary"
                  data-testid="portal-gate-oidc-signin-btn"
                >
                  <LockIcon size={14} /> {t("portalWorkspace.authGateOidcBtn")} <ArrowRightIcon size={14} />
                </Link>
                <button
                  type="button"
                  className="ura-copilot-btn is-outline"
                  data-testid="portal-gate-demo-signin-btn"
                  onClick={() => void handleDemoQuickSignIn()}
                >
                  <ShieldCheckIcon size={14} /> {t("portalWorkspace.authGateDemoBtn")}
                </button>
              </div>
            </section>
          ) : null}

          <section className="ura-portal-form-card" aria-labelledby="portal-screen-heading">
            <div className="ura-portal-form-header">
              <div>
                <h1 id="portal-screen-heading" className="ura-portal-form-title">
                  {localizedTitle}
                </h1>
                <p className="ura-portal-form-step">{localizedStepTitle}</p>
              </div>
              <span className="ura-portal-form-code">{screen.formId}</span>
            </div>

            <div className="ura-portal-form-body">
              {errorBanner && (
                <div id="error-banner" className="ura-portal-error-banner" role="alert">
                  <AlertTriangleIcon size={16} /> {errorBanner}
                </div>
              )}

              {statusNotice && !errorBanner && (
                <div
                  className="ura-portal-coachmark is-banner"
                  data-testid="portal-autofill-status-notice"
                  role="status"
                >
                  <LightbulbIcon size={15} /> {statusNotice}
                </div>
              )}

              {stalledMs >= 15000 && emptyRequiredFields.length > 0 && !errorBanner && (
                <div
                  className="ura-portal-coachmark is-banner"
                  data-testid="portal-stall-detector-banner"
                  role="status"
                >
                  <LightbulbIcon size={15} />{" "}
                  <span>
                    {t("portalWorkspace.stallPrompt", { field: emptyRequiredFields[0] })}{" "}
                    <button
                      type="button"
                      className="ura-portal-link-btn"
                      onClick={() =>
                        void askCopilotWithDomTelemetry(
                          "I paused on this step, highlight the next field I need to fill",
                          false,
                        )
                      }
                    >
                      {t("portalWorkspace.stallHighlightBtn")}
                    </button>
                  </span>
                </div>
              )}

              <form onSubmit={handlePortalFormSubmit} noValidate>
                <div className="ura-portal-field-grid">
                  {screen.fields.map((f) => {
                    const isSpotlighted = spotlightField === f.name;
                    const err = fieldErrors[f.name];
                    const coach = coachmarks[f.name];
                    const errId = `${f.selectorId}-err`;
                    const fieldLabel = getFieldLabel(f);
                    const effectivePlaceholder = demoPlaceholders[f.name] || f.placeholder || "";
                    return (
                      <div
                        key={f.name}
                        ref={(el) => {
                          fieldRefs.current[f.name] = el;
                        }}
                        className={`ura-portal-field ${isSpotlighted ? "copilot-dom-spotlight" : ""}`}
                        data-testid={`portal-dom-field-${f.name}`}
                      >
                        <label htmlFor={f.selectorId}>
                          {fieldLabel}{" "}
                          {f.required && (
                            <span className="ura-portal-required" aria-hidden="true">
                              *
                            </span>
                          )}
                        </label>
                        {f.type === "select" ? (
                          <select
                            id={f.selectorId}
                            name={f.name}
                            value={values[f.name] || ""}
                            aria-invalid={Boolean(err)}
                            aria-describedby={err ? errId : undefined}
                            onChange={(e) => recordFieldChange(f.name, e.target.value)}
                          >
                            <option value="">{t("portalWorkspace.selectPlaceholder")}</option>
                            {(f.options ?? []).map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.labelKey ? t(opt.labelKey) : opt.label}
                              </option>
                            ))}
                          </select>
                        ) : f.type === "textarea" ? (
                          <textarea
                            id={f.selectorId}
                            name={f.name}
                            rows={3}
                            placeholder={effectivePlaceholder}
                            value={values[f.name] || ""}
                            aria-invalid={Boolean(err)}
                            aria-describedby={err ? errId : undefined}
                            onChange={(e) => recordFieldChange(f.name, e.target.value)}
                          />
                        ) : (
                          <input
                            id={f.selectorId}
                            name={f.name}
                            type={f.type}
                            placeholder={effectivePlaceholder}
                            value={values[f.name] || ""}
                            aria-invalid={Boolean(err)}
                            aria-describedby={err ? errId : undefined}
                            onChange={(e) => recordFieldChange(f.name, e.target.value)}
                          />
                        )}
                        {err && (
                          <span id={errId} className="ura-portal-field-error" role="alert">
                            <AlertTriangleIcon size={13} /> {err}
                          </span>
                        )}
                        {coach && (
                          <div className="ura-portal-coachmark" data-testid={`portal-coachmark-${f.name}`}>
                            <LightbulbIcon size={14} /> AI Co-Pilot: {coach}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div className="ura-portal-submit-bar">
                  <button
                    type="submit"
                    id="btnGeneratePRN"
                    className="ura-copilot-btn is-primary"
                    data-testid="portal-form-submit-btn"
                  >
                    <CheckCircleIcon /> {localizedSubmitLabel}
                  </button>
                  <button
                    type="button"
                    className="ura-copilot-btn is-secondary"
                    data-testid="portal-autofill-safe-btn"
                    onClick={handleAutoFillSafeFields}
                  >
                    <SparklesIcon /> {t("portalWorkspace.autofillBtn")}
                  </button>
                </div>
              </form>
            </div>
          </section>
        </main>

        {/* Embedded AI Co-Pilot Side Rail */}
        <aside
          id="urap-copilot"
          ref={railRef}
          className="ura-copilot-rail"
          aria-label={t("portalWorkspace.railTitle")}
        >
          <div className="ura-copilot-rail-head">
            <div>
              <strong>{t("portalWorkspace.railTitle")}</strong>
              <span className="ura-copilot-rail-sub">{t("portalWorkspace.railSubtitle")}</span>
            </div>
            <span className="ura-copilot-live-pill">{t("portalWorkspace.railActivePill")}</span>
          </div>

          <div className="ura-copilot-rail-body">
            <p className="ura-copilot-rail-desc">{t("portalWorkspace.railDescription")}</p>

            <div className="ura-copilot-rail-actions">
              <button
                ref={guideButtonRef}
                type="button"
                className="ura-copilot-btn is-primary"
                data-testid="copilot-rail-guide-me-btn"
                disabled={busy}
                onClick={() =>
                  void askCopilotWithDomTelemetry(
                    "I am stuck on this portal screen, highlight what I need to do next",
                    false,
                  )
                }
              >
                <CompassIcon size={15} />{" "}
                {busy ? t("portalWorkspace.railGuideBusy") : t("portalWorkspace.railGuideBtn")}
              </button>
              <button
                type="button"
                className="ura-copilot-btn is-secondary"
                data-testid="copilot-rail-complete-task-btn"
                disabled={busy}
                onClick={() => void askCopilotWithDomTelemetry(`Prepare and review ${localizedTitle}`, true)}
              >
                <ShieldCheckIcon size={15} /> {t("portalWorkspace.railExecuteBtn")}
              </button>
              <button type="button" className="ura-copilot-btn is-outline" onClick={handleAutoFillSafeFields}>
                <SparklesIcon /> {t("portalWorkspace.railPrefillBtn")}
              </button>
            </div>

            {copilotCard && (
              <div className="ura-copilot-card-wrap">
                <PortalCopilotCard
                  copilot={copilotCard}
                  conversationId={conversationId}
                  onSubmitInlineForm={(_taskId, submittedFields) => {
                    const merged = { ...values, ...submittedFields };
                    setValues(merged);
                    void askCopilotWithDomTelemetry(
                      `Submitted form for ${localizedTitle}`,
                      true,
                      merged,
                      {},
                      "",
                    );
                  }}
                />
              </div>
            )}
          </div>
        </aside>
      </div>

      <footer className="urap-footer" id="urap-contact">
        <div className="urap-shell urap-footer-inner">
          <div className="urap-footer-col">
            <p className="urap-footer-brand">{t("portalWorkspace.brandTitle")}</p>
            <p className="urap-footer-tagline">{t("portalWorkspace.footer.tagline")}</p>
          </div>
          <div className="urap-footer-col">
            <h2 className="urap-footer-title">{t("portalWorkspace.footer.contactTitle")}</h2>
            <ul className="urap-footer-list">
              <li>
                {t("portalWorkspace.footer.tollFree")}:{" "}
                {URA_CONTACTS.tollFree.map((number, index) => (
                  <React.Fragment key={number}>
                    {index > 0 && " / "}
                    <a href={`tel:${telDigits(number)}`}>{number}</a>
                  </React.Fragment>
                ))}
              </li>
              <li>
                {t("portalWorkspace.footer.whatsapp")}:{" "}
                <a href={`tel:${telDigits(URA_CONTACTS.whatsapp)}`}>{URA_CONTACTS.whatsapp}</a>
              </li>
              <li>
                {t("portalWorkspace.footer.website")}:{" "}
                <a href={URA_CONTACTS.website} target="_blank" rel="noopener noreferrer">
                  ura.go.ug
                </a>
              </li>
            </ul>
          </div>
          <div className="urap-footer-col">
            <h2 className="urap-footer-title">{t("portalWorkspace.footer.copilotTitle")}</h2>
            <p className="urap-footer-note">{t("portalWorkspace.footer.copilotNote")}</p>
          </div>
        </div>
      </footer>

      {/* ura.go.ug's "Chat with us." launcher, opening this workspace's own Co-Pilot. */}
      <button
        type="button"
        className="urap-chat-launcher"
        aria-label={t("portalWorkspace.chatWithUsLabel")}
        onClick={focusCopilot}
      >
        <span className="urap-chat-bubble" aria-hidden="true">
          {t("portalWorkspace.chatWithUs")}
        </span>
        <span className="urap-chat-avatar" aria-hidden="true">
          <BotIcon />
        </span>
      </button>
      <CallScreen />
    </div>
  );
}
