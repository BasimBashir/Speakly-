import { cn } from "@/lib/utils";

// Speakly text wordmark (replaces upstream's Dograh logo images). `inverse`
// forces light text on an always-dark surface; `mark` renders the short mark.
// Callers size it via className (e.g. "h-7").
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center font-bold tracking-tight select-none",
        mark ? "text-base" : "text-xl",
        inverse ? "text-white" : "text-foreground",
        className,
      )}
    >
      {mark ? "S" : "Speakly"}
    </span>
  );
}
