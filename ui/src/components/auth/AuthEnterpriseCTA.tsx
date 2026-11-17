"use client";

// Enterprise call-to-action rendered inside the auth brand panel (shared by the
// Stack Auth handler and the local/OSS auth pages). Speakly sends enquiries to
// WhatsApp instead of upstream's Dograh lead form.

import { Button } from "@/components/ui/button";
import { SPEAKLY_WHATSAPP_URL } from "@/constants/speakly";

export function AuthEnterpriseCTA() {
  return (
    <Button
      variant="outline"
      asChild
      className="w-full border-white/20 bg-white/5 text-zinc-100 hover:bg-white/10 hover:text-white"
    >
      <a href={SPEAKLY_WHATSAPP_URL} target="_blank" rel="noopener noreferrer">
        Enterprise Enquiry
      </a>
    </Button>
  );
}
