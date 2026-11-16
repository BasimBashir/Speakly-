'use client';

import { Info } from 'lucide-react';

// Speakly parses documents in-process (api/services/knowledge_base/local_document_parser.py),
// so nothing goes to Dograh's managed Model Proxy Service.
export default function ExternalProcessingNotice() {
  return (
    <div className="flex gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900/50 dark:bg-emerald-950/30">
      <Info className="h-4 w-4 flex-shrink-0 text-emerald-600 dark:text-emerald-400 mt-0.5" />
      <div className="text-xs text-emerald-900 dark:text-emerald-200">
        <p className="font-medium">Parsed locally on your server</p>
        <p className="mt-1">
          Uploaded documents are parsed and chunked locally on your self-hosted server.
          Nothing is sent to any external service - the extracted text and embeddings are
          stored only in your own database.
        </p>
      </div>
    </div>
  );
}
