'use client';

import { FileText, Info, Loader2, Sparkles, Upload, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import {
  describePreviewApiV1KnowledgeBaseDescribePreviewPost,
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
import type { DocumentUploadResponseSchema } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAppConfig } from '@/context/AppConfigContext';
import logger from '@/lib/logger';

interface DocumentUploadProps {
  onUploadSuccess: () => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPTED_FILE_TYPES = ['.pdf', '.docx', '.doc', '.txt', '.json'];

const DOC_TYPE_OPTIONS = [
  { value: 'contract', label: 'Contract' },
  { value: 'policy', label: 'Policy' },
  { value: 'pricing', label: 'Pricing' },
  { value: 'faq', label: 'FAQ' },
  { value: 'script', label: 'Script' },
  { value: 'other', label: 'Other' },
] as const;

const MIN_DESCRIPTION_LENGTH = 20;

export default function DocumentUpload({ onUploadSuccess }: DocumentUploadProps) {
  const { config } = useAppConfig();
  const isOSS = config?.deploymentMode === 'oss';
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [retrievalMode, setRetrievalMode] = useState<string>('full_document');
  const [docType, setDocType] = useState<string>('');
  const [intendedUse, setIntendedUse] = useState<{ inbound: boolean; outbound: boolean }>({
    inbound: false,
    outbound: false,
  });
  const [userDescription, setUserDescription] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const [isGeneratingDescription, setIsGeneratingDescription] = useState(false);
  const [descriptionGenerated, setDescriptionGenerated] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const intendedUseSelected = intendedUse.inbound || intendedUse.outbound;
  const descriptionValid = userDescription.trim().length >= MIN_DESCRIPTION_LENGTH;
  const formComplete = selectedFile && docType && intendedUseSelected && descriptionValid;

  const ossNotice = isOSS ? (
    <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/50 dark:bg-amber-950/30">
      <Info className="h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
      <div className="text-xs text-amber-900 dark:text-amber-200">
        <p className="font-medium">Processed by an external service</p>
        <p className="mt-1">
          Uploaded documents are sent to Speakly&apos;s managed Model Proxy Service for
          parsing and chunking. Speakly Model Proxy Service does not store or read your documents -
          the extracted text and embeddings are returned and stored locally in your
          self-hosted database.
        </p>
      </div>
    </div>
  ) : null;

  const validateFile = (file: File): boolean => {
    const fileExtension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_TYPES.includes(fileExtension)) {
      toast.error(`Please select a supported file type: ${ACCEPTED_FILE_TYPES.join(', ')}`);
      return false;
    }

    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 5MB');
      return false;
    }

    return true;
  };

  const handleFileSelected = (file: File) => {
    if (!validateFile(file)) {
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }
    setSelectedFile(file);
  };

  const clearSelectedFile = () => {
    setSelectedFile(null);
    setRetrievalMode('full_document');
    setDocType('');
    setIntendedUse({ inbound: false, outbound: false });
    setUserDescription('');
    setDescriptionGenerated(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleAutoDescribe = async () => {
    if (!selectedFile) return;
    setIsGeneratingDescription(true);
    try {
      const response = await describePreviewApiV1KnowledgeBaseDescribePreviewPost({
        body: {
          file: selectedFile,
          doc_type: docType || undefined,
          intended_use:
            intendedUse.inbound || intendedUse.outbound
              ? [
                  ...(intendedUse.inbound ? ['inbound'] : []),
                  ...(intendedUse.outbound ? ['outbound'] : []),
                ]
              : undefined,
        },
      });
      if (response.error || !response.data) {
        const detail =
          (response.error as { detail?: string } | undefined)?.detail ?? 'unknown_error';
        const message =
          detail === 'parse_failed'
            ? "Couldn't read the document. Try writing a description manually."
            : detail === 'llm_failed'
              ? 'Auto-describe failed — please write one yourself.'
              : 'Could not generate description.';
        toast.error(message);
        return;
      }
      setUserDescription(response.data.description);
      setDescriptionGenerated(true);
      toast.success('Description generated');
    } catch (error) {
      logger.error('Auto-describe failed:', error);
      toast.error('Could not generate description.');
    } finally {
      setIsGeneratingDescription(false);
    }
  };

  const uploadFile = async () => {
    if (!selectedFile || !formComplete) return;

    setUploading(true);
    setUploadProgress(0);

    try {
      logger.info('Requesting presigned upload URL for:', selectedFile.name);
      const uploadUrlResponse = await getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
        body: {
          filename: selectedFile.name,
          mime_type: selectedFile.type || 'application/octet-stream',
          custom_metadata: {
            original_filename: selectedFile.name,
            uploaded_at: new Date().toISOString(),
          },
        },
      });

      if (uploadUrlResponse.error || !uploadUrlResponse.data) {
        throw new Error('Failed to get upload URL');
      }

      const uploadData: DocumentUploadResponseSchema = uploadUrlResponse.data;
      setUploadProgress(25);

      const uploadResponse = await fetch(uploadData.upload_url, {
        method: 'PUT',
        body: selectedFile,
        headers: {
          'Content-Type': selectedFile.type || 'application/octet-stream',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error('Failed to upload file to storage');
      }

      setUploadProgress(75);

      const intendedUseValues: string[] = [];
      if (intendedUse.inbound) intendedUseValues.push('inbound');
      if (intendedUse.outbound) intendedUseValues.push('outbound');

      const processResponse = await processDocumentApiV1KnowledgeBaseProcessDocumentPost({
        body: {
          document_uuid: uploadData.document_uuid,
          s3_key: uploadData.s3_key,
          retrieval_mode: retrievalMode,
          doc_type: docType,
          intended_use: intendedUseValues,
          user_description: userDescription.trim(),
        },
      });

      if (processResponse.error) {
        throw new Error('Failed to trigger processing');
      }

      setUploadProgress(100);
      toast.success(`File uploaded: ${selectedFile.name}. Processing started.`);
      clearSelectedFile();
      onUploadSuccess();
    } catch (error) {
      logger.error('Error uploading document:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload document');
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      handleFileSelected(file);
    }
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const file = e.dataTransfer.files?.[0];
    if (file) {
      handleFileSelected(file);
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  // Step 2: File selected — show form fields and retrieval mode choice
  if (selectedFile && !uploading) {
    return (
      <div className="space-y-4">
        {ossNotice}
        {/* Selected file info */}
        <div className="flex items-center gap-3 p-3 border rounded-lg bg-muted/30">
          <FileText className="w-8 h-8 text-primary flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-medium truncate">{selectedFile.name}</p>
            <p className="text-xs text-muted-foreground">
              {(selectedFile.size / 1024).toFixed(1)} KB
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={clearSelectedFile}>
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Document type */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">
            Document type <span className="text-destructive">*</span>
          </Label>
          <Select value={docType} onValueChange={setDocType}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select a document type" />
            </SelectTrigger>
            <SelectContent>
              {DOC_TYPE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Intended use */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">
            Intended use <span className="text-destructive">*</span>
          </Label>
          <p className="text-xs text-muted-foreground">Select at least one.</p>
          <div className="flex items-center gap-6">
            <label htmlFor="use-inbound" className="flex items-center gap-2 cursor-pointer">
              <Checkbox
                id="use-inbound"
                checked={intendedUse.inbound}
                onCheckedChange={(checked) =>
                  setIntendedUse((prev) => ({ ...prev, inbound: checked === true }))
                }
              />
              <span className="text-sm">Inbound</span>
            </label>
            <label htmlFor="use-outbound" className="flex items-center gap-2 cursor-pointer">
              <Checkbox
                id="use-outbound"
                checked={intendedUse.outbound}
                onCheckedChange={(checked) =>
                  setIntendedUse((prev) => ({ ...prev, outbound: checked === true }))
                }
              />
              <span className="text-sm">Outbound</span>
            </label>
          </div>
        </div>

        {/* Description */}
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label className="text-sm font-medium">
              Describe this document <span className="text-destructive">*</span>
            </Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={!selectedFile || isGeneratingDescription}
              onClick={handleAutoDescribe}
            >
              {isGeneratingDescription ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
              {isGeneratingDescription
                ? 'Generating…'
                : descriptionGenerated
                  ? 'Regenerate'
                  : 'Auto-write'}
            </Button>
          </div>
          <Textarea
            value={userDescription}
            onChange={(e) => setUserDescription(e.target.value)}
            placeholder="Explain what this document contains and how the agent should use it (min 20 characters)"
            className="min-h-20 max-h-40 w-full resize-none break-words"
          />
          <div className="flex justify-end">
            <span
              className={`text-xs ${
                userDescription.trim().length >= MIN_DESCRIPTION_LENGTH
                  ? 'text-muted-foreground'
                  : 'text-destructive'
              }`}
            >
              {userDescription.trim().length}/{MIN_DESCRIPTION_LENGTH} min characters
            </span>
          </div>
        </div>

        {/* Retrieval mode selection */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">How should the agent use this document?</Label>
          <RadioGroup value={retrievalMode} onValueChange={setRetrievalMode}>
            <label
              htmlFor="full_document"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'full_document' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="full_document" id="full_document" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">Full Document</p>
                <p className="text-xs text-muted-foreground">
                  The entire document is provided to the agent on each retrieval.
                  Best for menus, price lists, FAQs, and other small reference documents.
                </p>
              </div>
            </label>
            <label
              htmlFor="chunked"
              className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                retrievalMode === 'chunked' ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
              }`}
            >
              <RadioGroupItem value="chunked" id="chunked" className="mt-0.5" />
              <div>
                <p className="font-medium text-sm">Chunked Search</p>
                <p className="text-xs text-muted-foreground">
                  The document is split into chunks and the most relevant ones are retrieved.
                  Better for large documents like manuals or policies.
                </p>
              </div>
            </label>
          </RadioGroup>
        </div>

        {/* Upload button */}
        <Button onClick={uploadFile} className="w-full" disabled={!formComplete}>
          Upload & Process
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {ossNotice}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPTED_FILE_TYPES.join(',')}
        onChange={handleFileSelect}
        className="hidden"
        disabled={uploading}
      />

      {/* Drag and Drop Area */}
      <div
        className={`
          border-2 border-dashed rounded-lg p-8 text-center transition-colors
          ${dragActive ? 'border-primary bg-primary/5' : 'border-muted-foreground/25'}
          ${uploading ? 'opacity-50 pointer-events-none' : 'cursor-pointer hover:border-primary hover:bg-muted/50'}
        `}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        onClick={handleButtonClick}
      >
        <Upload className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <p className="text-lg font-medium mb-2">
          {uploading ? 'Uploading...' : 'Drop your document here'}
        </p>
        <p className="text-sm text-muted-foreground mb-4">
          or click to browse
        </p>
        <p className="text-xs text-muted-foreground">
          Supported formats: {ACCEPTED_FILE_TYPES.join(', ')} (Max 5MB)
        </p>
      </div>

      {/* Upload Progress */}
      {uploading && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>Uploading...</span>
            <span>{uploadProgress}%</span>
          </div>
          <Progress value={uploadProgress} />
        </div>
      )}

      {/* Manual Upload Button */}
      {!uploading && (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="outline"
            onClick={handleButtonClick}
          >
            Choose File
          </Button>
        </div>
      )}
    </div>
  );
}
