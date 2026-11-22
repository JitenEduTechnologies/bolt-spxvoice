'use client';

import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { getPresignedUploadUrlApiV1S3PresignedUploadUrlPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import logger from '@/lib/logger';
import { useAuth } from '@/lib/auth';

interface CsvUploadSelectorProps {
  onFileUploaded: (fileKey: string, fileName: string, mapping?: Record<string, string>) => void;
  selectedFileName?: string;
  onMappingChange?: (mapping: Record<string, string>) => void;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export default function CsvUploadSelector({
  onFileUploaded,
  selectedFileName,
  onMappingChange,
}: CsvUploadSelectorProps) {
  const { getAccessToken } = useAuth();
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [headers, setHeaders] = useState<string[]>([]);
  const [phoneCol, setPhoneCol] = useState<string>('');
  const [nameCol, setNameCol] = useState<string>('');
  const [rowCount, setRowCount] = useState<number | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.csv')) {
      toast.error('Please select a CSV file');
      return;
    }

    if (file.size > MAX_FILE_SIZE) {
      toast.error('File size must be less than 10MB');
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setPreviewError(null);

    try {
      logger.info('Requesting presigned upload URL for:', file.name);
      const { data: presignedData, error } = await getPresignedUploadUrlApiV1S3PresignedUploadUrlPost({
        body: {
          file_name: file.name,
          file_size: file.size,
          content_type: 'text/csv',
        },
      });

      if (error || !presignedData) {
        throw new Error('Failed to get upload URL');
      }

      logger.info('Received presigned URL, uploading file...');

      const uploadResponse = await fetch(presignedData.upload_url, {
        method: 'PUT',
        body: file,
        headers: {
          'Content-Type': 'text/csv',
        },
      });

      if (!uploadResponse.ok) {
        throw new Error(`Failed to upload file to storage (HTTP ${uploadResponse.status})`);
      }

      setUploadProgress(100);
      logger.info('File uploaded successfully, file_key:', presignedData.file_key);

      // Fetch CSV preview and column detection
      try {
        const token = await getAccessToken();
        const previewRes = await fetch('/api/v1/campaign/preview-csv', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: token ? `Bearer ${token}` : '',
          },
          body: JSON.stringify({ source_id: presignedData.file_key }),
        });

        if (previewRes.ok) {
          const previewData = await previewRes.json();
          if (!previewData.is_valid && previewData.error) {
            setPreviewError(previewData.error);
            toast.error(previewData.error);
          } else {
            const detectedHeaders: string[] = previewData.headers || [];
            setHeaders(detectedHeaders);
            setRowCount(previewData.total_rows_estimate || null);

            const inferredPhone = previewData.phone_column || detectedHeaders[0] || '';
            const inferredName = previewData.resolved_mapping?.customer_name || '';

            setPhoneCol(inferredPhone);
            setNameCol(inferredName);

            const mapping: Record<string, string> = {};
            if (inferredPhone) mapping.phone_number = inferredPhone;
            if (inferredName) mapping.customer_name = inferredName;

            onFileUploaded(presignedData.file_key, file.name, mapping);
            if (onMappingChange) onMappingChange(mapping);
            toast.success(`File uploaded: ${file.name} (${previewData.total_rows_estimate} rows)`);
          }
        } else {
          const err = await previewRes.json().catch(() => ({}));
          const errMsg = err.detail || 'Could not validate CSV headers';
          setPreviewError(errMsg);
          toast.error(errMsg);
        }
      } catch (prevErr) {
        logger.warn('Preview CSV failed:', prevErr);
        toast.error('Failed to validate CSV contents');
      }
    } catch (error) {
      logger.error('Error uploading CSV:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to upload CSV file');
    } finally {
      setUploading(false);
      setUploadProgress(0);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handlePhoneChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    setPhoneCol(val);
    const updated = { phone_number: val, ...(nameCol ? { customer_name: nameCol } : {}) };
    if (onMappingChange) onMappingChange(updated);
  };

  const handleNameChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    setNameCol(val);
    const updated = { ...(phoneCol ? { phone_number: phoneCol } : {}), ...(val ? { customer_name: val } : {}) };
    if (onMappingChange) onMappingChange(updated);
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="space-y-3">
      <Label>CSV Contact Source</Label>
      <div className="flex items-center gap-4">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          onChange={handleFileSelect}
          className="hidden"
        />
        <Button
          type="button"
          variant="outline"
          onClick={handleButtonClick}
          disabled={uploading}
        >
          {uploading ? `Uploading... ${uploadProgress}%` : 'Upload CSV File'}
        </Button>
        {selectedFileName && !uploading && (
          <div className="flex-1 text-sm">
            <span className="text-muted-foreground">Selected: </span>
            <span className="text-primary font-medium">{selectedFileName}</span>
            {rowCount !== null && (
              <span className="ml-2 text-xs text-muted-foreground">({rowCount} rows detected)</span>
            )}
          </div>
        )}
      </div>

      {previewError && (
        <div className="p-3 text-sm text-destructive bg-destructive/10 rounded-md border border-destructive/20">
          {previewError}
        </div>
      )}

      {headers.length > 0 && (
        <div className="p-4 rounded-lg border bg-card space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-foreground">CSV Field Mapping</span>
            <Badge variant="outline" className="text-xs">
              Auto-Detected
            </Badge>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs text-muted-foreground">Primary Phone Column *</Label>
              <select
                value={phoneCol}
                onChange={handlePhoneChange}
                className="w-full mt-1 px-2.5 py-1.5 rounded-md border bg-background text-sm text-foreground focus:ring-1 focus:ring-primary focus:outline-none"
              >
                {headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">Customer Name Column (Optional)</Label>
              <select
                value={nameCol}
                onChange={handleNameChange}
                className="w-full mt-1 px-2.5 py-1.5 rounded-md border bg-background text-sm text-foreground focus:ring-1 focus:ring-primary focus:outline-none"
              >
                <option value="">-- None / Use Default --</option>
                {headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Detected columns ({headers.join(', ')}) will be automatically injected into agent context variables.
          </p>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        Upload standard CRM contact lists (.csv). Common headers like <code>phone_number</code>, <code>phone</code>, <code>mobile</code>, <code>name</code>, <code>budget</code>, and <code>location</code> are supported. Max 10MB.
      </p>
    </div>
  );
}
