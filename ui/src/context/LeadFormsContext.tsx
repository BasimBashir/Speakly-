"use client";

import posthog from "posthog-js";
import { createContext, type ReactNode,useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { getWorkflowCountApiV1WorkflowCountGet } from "@/client/sdk.gen";
import type { LeadSource } from "@/components/lead-forms/leadFieldOptions";
import { OnboardingModal } from "@/components/lead-forms/OnboardingModal";
import { PostHogEvent } from "@/constants/posthog-events";
import { SPEAKLY_WHATSAPP_URL } from "@/constants/speakly";
import { useOnboarding } from "@/context/OnboardingContext";
import { useAuth } from "@/lib/auth";
import { trackMetaCompleteRegistration } from "@/lib/metaPixel";

interface LeadFormsContextValue {
  openHireExpert: (source: LeadSource) => void;
  openEnterprise: (source: LeadSource, prefill?: { company?: string }) => void;
  // True once the hire modal has been opened this session (used to suppress the builder nudge).
  hasOpenedHireRef: React.MutableRefObject<boolean>;
}

const LeadFormsContext = createContext<LeadFormsContextValue | null>(null);

export function LeadFormsProvider({ children }: { children: ReactNode }) {
  const hasOpenedHireRef = useRef(false);

  // ---- Post-signup onboarding gate ----
  // Show the onboarding form ONCE per user, and ONLY to genuinely new users:
  //   (a) the completion/skip flag is unset (server-backed onboarding state,
  //       cross-device), AND
  //   (b) the user has zero workflows (grandfathers out all existing users —
  //       they already have workflows, so they never see this modal).
  const { user, loading: authLoading } = useAuth();
  const {
    loading: onboardingLoading,
    onboardingCompletedAt,
    onboardingSkipped,
    markOnboardingCompleted,
  } = useOnboarding();
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  // Guard so the one-time workflow-count check runs at most once per mount.
  const onboardingCheckedRef = useRef(false);
  // Live view of the gate for the post-await re-check below.
  const onboardingDoneRef = useRef(false);
  onboardingDoneRef.current = Boolean(onboardingCompletedAt) || onboardingSkipped;

  useEffect(() => {
    if (authLoading || onboardingLoading || !user || onboardingCheckedRef.current) {
      return;
    }

    onboardingCheckedRef.current = true;
    if (onboardingDoneRef.current) return; // already done — never show

    // Only brand-new users (no workflows yet) see the form. The count is
    // org-scoped (the user's selected organization), so a new user joining an
    // org that already has workflows is correctly grandfathered out. This costs
    // one lightweight count query per session for users whose flag is still
    // unset — an accepted trade for a server-authoritative, cross-device gate.
    (async () => {
      try {
        const res = await getWorkflowCountApiV1WorkflowCountGet();
        // Re-check the flag after the await: a completion elsewhere (another
        // tab) may have stamped it while the count was in flight.
        if (res.data?.total === 0 && !onboardingDoneRef.current) {
          setOnboardingOpen(true);
          posthog.capture(PostHogEvent.ONBOARDING_SHOWN);
          // Brand-new user detected (0 workflows, onboarding not yet done) — the
          // app's canonical "just registered" signal. Meta standard event.
          trackMetaCompleteRegistration();
        }
      } catch {
        // If the count can't be fetched, do NOT show the modal — fail closed so
        // existing users are never disrupted.
      }
    })();
  }, [authLoading, onboardingLoading, user]);

  const completeOnboarding = useCallback((skipped: boolean) => {
    // Dismiss immediately, then persist the flag through OnboardingContext
    // (optimistic local state closes the gate even if the server write lags;
    // the write itself is best-effort and cross-device).
    setOnboardingOpen(false);
    markOnboardingCompleted({ skipped });
  }, [markOnboardingCompleted]);

  // Speakly: expert/enterprise enquiries go to WhatsApp, not upstream's Dograh lead forms.
  const openHireExpert = useCallback((source: LeadSource) => {
    hasOpenedHireRef.current = true;
    posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source });
    window.open(SPEAKLY_WHATSAPP_URL, "_blank", "noopener,noreferrer");
  }, []);

  const openEnterprise = useCallback((source: LeadSource) => {
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source });
    window.open(SPEAKLY_WHATSAPP_URL, "_blank", "noopener,noreferrer");
  }, []);

  const value = useMemo(
    () => ({ openHireExpert, openEnterprise, hasOpenedHireRef }),
    [openHireExpert, openEnterprise],
  );

  return (
    <LeadFormsContext.Provider value={value}>
      {children}
      <OnboardingModal
        open={onboardingOpen}
        onComplete={completeOnboarding}
      />
    </LeadFormsContext.Provider>
  );
}

export function useLeadForms(): LeadFormsContextValue {
  const ctx = useContext(LeadFormsContext);
  if (!ctx) throw new Error("useLeadForms must be used within a LeadFormsProvider");
  return ctx;
}
