"use client";

/**
 * Tax profile — the one part of settings that is stored server-side.
 *
 * These fields are not decoration: `/v1/me/profile` feeds retrieval filters,
 * tool visibility and prompt rendering, so "sole trader, retail, beginner"
 * changes the answers a person gets. That is also why the panel is honest about
 * needing an account — there is nowhere to keep a profile for an anonymous
 * browser, and pretending otherwise would lose the edits on reload.
 *
 * Editing is unsaved-until-Save: `edits` overlays the server copy rather than
 * replacing it, so nothing is written on a stray keystroke and the current
 * server state stays visible for comparison.
 */

import Link from "next/link";
import React, { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  accountApi,
  ApiError,
  type DetailLevel,
  type ProfilePatch,
  type TaxpayerType,
  type UserProfile,
} from "../../services/accountApi";
import {
  ActionButton,
  IdentityGate,
  SegmentedOption,
  SelectControl,
  Segmented,
  SettingsRow,
  SettingsSection,
  StatusNote,
  TextControl,
} from "./controls";

/** `TaxpayerType` in App/backend/app/auth/models.py, in reading order. */
const TAXPAYER_TYPES: readonly { value: TaxpayerType; label: string }[] = [
  { value: "unknown", label: "Not saying yet" },
  { value: "individual", label: "Individual (employed)" },
  { value: "sole_trader", label: "Sole trader" },
  { value: "company", label: "Company" },
  { value: "partnership", label: "Partnership" },
  { value: "ngo", label: "NGO / not-for-profit" },
  { value: "non_resident", label: "Non-resident" },
];

const DETAIL_LEVELS: readonly SegmentedOption<DetailLevel>[] = [
  { value: "beginner", label: "Plain" },
  { value: "intermediate", label: "Standard" },
  { value: "expert", label: "Technical" },
];

/**
 * The tax heads the memory extractor already recognises
 * (`app/memory/extractor.py::_REGISTRATION_PATTERNS`) — the same vocabulary, so
 * a profile set here and a fact learned from conversation agree.
 */
const TAX_HEADS: readonly { value: string; label: string }[] = [
  { value: "vat", label: "VAT" },
  { value: "paye", label: "PAYE" },
  { value: "wht", label: "Withholding tax" },
  { value: "cit", label: "Corporation tax" },
];

/** The backend profile stores only these two; the chat locale set is wider. */
const PROFILE_LANGUAGES = [
  { value: "en", label: "English" },
  { value: "lg", label: "Luganda" },
];

/**
 * What the panel promises, kept to what the backend actually does.
 *
 * `service.py::_personalization_block` reads exactly four of these fields into
 * the prompt — display_name, taxpayer_type, detail_level, registered_tax_types
 * — and only when personalization consent is granted (it returns early on
 * `snapshot.consent_granted`). `industry` and `primary_language` are stored and
 * returned in the data export, but nothing reads them into an answer today, so
 * this does not claim they change one.
 */
const DESCRIPTION =
  "Shapes the answers you get — which taxes are mentioned, how technical the " +
  "wording is — once \"Remember facts about me\" is granted under Privacy & data.";

const EMPTY: Omit<UserProfile, "user_id" | "updated_at"> = {
  taxpayer_type: "unknown",
  industry: "",
  primary_language: "en",
  detail_level: "intermediate",
  registered_tax_types: [],
  fiscal_year: "",
  display_name: "",
};

export default function ProfileSection({ status }: { status: string }) {
  const signedIn = status === "signed-in";
  const queryClient = useQueryClient();
  const [edits, setEdits] = useState<ProfilePatch>({});
  const [saved, setSaved] = useState(false);

  const profileQuery = useQuery<UserProfile>({
    queryKey: ["profile"],
    queryFn: accountApi.profile,
    enabled: signedIn,
    staleTime: 60_000,
    retry: false,
  });

  const save = useMutation({
    mutationFn: (patch: ProfilePatch) => accountApi.updateProfile(patch),
    onSuccess: (fresh) => {
      queryClient.setQueryData(["profile"], fresh);
      setEdits({});
      setSaved(true);
    },
  });

  const value = useMemo(
    () => ({ ...EMPTY, ...(profileQuery.data ?? {}), ...edits }),
    [profileQuery.data, edits],
  );
  const dirty = Object.keys(edits).length > 0;

  const patch = useCallback(<K extends keyof ProfilePatch>(key: K, next: ProfilePatch[K]) => {
    setSaved(false);
    setEdits((prev) => ({ ...prev, [key]: next }));
  }, []);

  const toggleTaxHead = useCallback(
    (head: string) => {
      const current = value.registered_tax_types;
      patch(
        "registered_tax_types",
        current.includes(head) ? current.filter((h) => h !== head) : [...current, head],
      );
    },
    [patch, value.registered_tax_types],
  );

  if (!signedIn) {
    return (
      <SettingsSection title="Tax profile" description={DESCRIPTION}>
        <IdentityGate status={status} what="A profile">
          <Link href="/signin">Sign in</Link> or{" "}
          <Link href="/signup">create an account</Link>. The assistant answers
          questions either way.
        </IdentityGate>
      </SettingsSection>
    );
  }

  const loadError = profileQuery.error as Error | null;
  const notConfigured = loadError instanceof ApiError && loadError.status === 503;

  return (
    <SettingsSection
      title="Tax profile"
      description={DESCRIPTION}
    >
      {profileQuery.isPending && <StatusNote kind="info">Loading your profile…</StatusNote>}

      {loadError && (
        <StatusNote kind="error">
          {notConfigured
            ? "Accounts are not configured on this deployment, so there is no profile to load."
            : `Could not load your profile: ${loadError.message}`}
        </StatusNote>
      )}

      <SettingsRow label="Display name" hint="Used to address you in replies." htmlFor="setv2-name">
        <TextControl
          id="setv2-name"
          value={value.display_name}
          maxLength={128}
          placeholder="Optional"
          onChange={(next) => patch("display_name", next)}
        />
      </SettingsRow>

      <SettingsRow label="I file as" htmlFor="setv2-taxpayer">
        <SelectControl
          id="setv2-taxpayer"
          value={value.taxpayer_type}
          options={TAXPAYER_TYPES}
          onChange={(next) => patch("taxpayer_type", next as TaxpayerType)}
        />
      </SettingsRow>

      <SettingsRow
        label="Industry"
        hint="Stored on your profile and included in your data export. Not used to shape answers yet."
        htmlFor="setv2-industry"
      >
        <TextControl
          id="setv2-industry"
          value={value.industry}
          maxLength={100}
          placeholder="Optional"
          onChange={(next) => patch("industry", next)}
        />
      </SettingsRow>

      <SettingsRow
        label="Answer detail"
        hint="Plain avoids jargon; technical quotes the law and the schedules."
      >
        <Segmented
          label="Answer detail"
          value={value.detail_level}
          options={DETAIL_LEVELS}
          onChange={(next) => patch("detail_level", next)}
        />
      </SettingsRow>

      <SettingsRow
        label="Preferred language for records"
        hint="Stored on your profile; the language answers are written in is under General."
        htmlFor="setv2-profile-lang"
      >
        <SelectControl
          id="setv2-profile-lang"
          value={value.primary_language}
          options={PROFILE_LANGUAGES}
          onChange={(next) => patch("primary_language", next as "en" | "lg")}
        />
      </SettingsRow>

      <SettingsRow
        label="Registered for"
        hint="Answers lead with the taxes you actually file."
        stacked
      >
        <span className="setv2-checks" role="group" aria-label="Registered tax heads">
          {TAX_HEADS.map((head) => {
            const checked = value.registered_tax_types.includes(head.value);
            return (
              <label key={head.value} className={`setv2-check${checked ? " setv2-check-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleTaxHead(head.value)}
                />
                {head.label}
              </label>
            );
          })}
        </span>
      </SettingsRow>

      {value.fiscal_year && (
        <SettingsRow
          label="Filing year"
          hint="Recorded on your profile. Read-only here — the backend sets it."
        >
          <span className="setv2-static">{value.fiscal_year}</span>
        </SettingsRow>
      )}

      <div className="setv2-actions">
        <ActionButton
          variant="primary"
          onClick={() => save.mutate(edits)}
          disabled={!dirty || Boolean(loadError)}
          busy={save.isPending}
        >
          {save.isPending ? "Saving…" : "Save profile"}
        </ActionButton>
        {dirty && !save.isPending && (
          <ActionButton onClick={() => setEdits({})}>Discard changes</ActionButton>
        )}
      </div>

      {save.error && (
        <StatusNote kind="error">
          Could not save: {(save.error as Error).message}
        </StatusNote>
      )}
      {saved && !dirty && <StatusNote kind="ok">Profile saved.</StatusNote>}
    </SettingsSection>
  );
}
