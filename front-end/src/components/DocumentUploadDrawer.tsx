import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  App,
  Button,
  Drawer,
  Flex,
  Form,
  Input,
  Select,
  Typography,
  Upload,
  type UploadFile,
} from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import { FileCard } from '@ant-design/x';
import { api, ApiError } from '@/api/client';
import type { DocCategory, IngestJob, StationId } from '@/api/types';
import type { DocListItem } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { can, ROLE_LABEL } from '@/auth/permissions';
import { CATEGORIES, CATEGORY, FILE_ICON } from '@/theme/tokens';
import { crew, stations } from '@/api/lookups';
import { IngestSteps } from './IngestSteps';
import { useIngestJob } from './useIngestJob';

const ACCEPT = '.pdf,.docx,.pptx,.xlsx,.md,.txt,.png,.jpg,.jpeg';
const MAX_BYTES = 20 * 1024 * 1024;

interface Values {
  title: string;
  category: DocCategory;
  station: StationId;
  ownerId: string;
  summary?: string;
  tags?: string[];
}

export function DocumentUploadDrawer({
  open,
  onClose,
  replace,
}: {
  open: boolean;
  onClose: () => void;
  replace?: DocListItem | null;
}) {
  const actor = useActor();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [form] = Form.useForm<Values>();
  const [file, setFile] = useState<UploadFile | null>(null);
  const [raw, setRaw] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [result, setResult] = useState<{ docId: string; jobId: string } | null>(null);
  const job = useIngestJob(result?.jobId);
  const station = Form.useWatch('station', form) ?? actor.station ?? 'prep';

  const categoryOptions = CATEGORIES.map((c) => ({
    value: c,
    label: CATEGORY[c].label,
    disabled: !can.uploadCategory(actor, c, station),
  }));

  const mutation = useMutation({
    mutationFn: (v: Values) => {
      const form = new FormData();
      form.append('file', raw as File, raw?.name);
      if (replace)
        return api<{ document: DocListItem; job: IngestJob }>(
          `/api/documents/${replace.id}/replace`,
          { form },
        );
      form.append('title', v.title.trim());
      form.append('category', v.category);
      form.append('station', v.station);
      form.append('ownerId', v.ownerId);
      form.append('summary', v.summary?.trim() ?? '');
      form.append('tags', (v.tags ?? []).join(','));
      return api<{ document: DocListItem; job: IngestJob }>('/api/documents', { form });
    },
    onSuccess: (r) => {
      setResult({ docId: r.document.id, jobId: r.job.id });
      qc.invalidateQueries({ queryKey: ['documents'] });
      qc.invalidateQueries({ queryKey: ['document', r.document.id] });
      message.success(
        replace
          ? `New version of ${r.document.id} queued for ingestion`
          : `${r.document.id} uploaded — ingestion started`,
      );
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Upload failed'),
  });

  const reset = () => {
    form.resetFields();
    setFile(null);
    setRaw(null);
    setFileError(null);
    setResult(null);
    onClose();
  };

  const submit = async () => {
    if (!file || !raw) {
      setFileError('Choose a file to upload');
      return;
    }
    const v = replace ? ({} as Values) : await form.validateFields();
    mutation.mutate(v);
  };

  const ext = (file?.name.split('.').pop() ?? 'pdf')
    .toLowerCase()
    .replace('jpeg', 'jpg') as keyof typeof FILE_ICON;

  return (
    <Drawer
      title={replace ? `Replace file · ${replace.id}` : 'Upload to the knowledge base'}
      open={open}
      onClose={reset}
      size={560}
      destroyOnHidden
      extra={
        result ? (
          <Button
            type="primary"
            onClick={() => {
              const id = result.docId;
              reset();
              navigate(`/documents/${id}`);
            }}
          >
            View document
          </Button>
        ) : (
          <Button type="primary" onClick={submit} loading={mutation.isPending}>
            {replace ? 'Replace & re-index' : 'Upload & ingest'}
          </Button>
        )
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title={`${ROLE_LABEL[actor.role]} upload rules`}
        description={
          actor.role === 'kitchen_manager'
            ? 'You can publish any category for any station, including SOPs.'
            : `You can upload Recipes, Onboarding and Incident Reports for ${stations().find((s) => s.id === actor.station)?.name}. SOPs are published by the Kitchen Manager.`
        }
      />
      {replace && (
        <Typography.Paragraph type="secondary">
          The old vectors are deleted by content hash and the new file is parsed, chunked and
          embedded again. The review date resets to today.
        </Typography.Paragraph>
      )}
      <Upload.Dragger
        height={200}
        accept={ACCEPT}
        maxCount={1}
        fileList={file ? [file] : []}
        beforeUpload={(f) => {
          const okType = new RegExp(
            `\\.(${ACCEPT.replace(/\./g, '').split(',').join('|')})$`,
            'i',
          ).test(f.name);
          if (!okType) {
            setFileError('Unsupported file type. Use PDF, DOCX, PPTX, XLSX, MD, TXT, PNG or JPG.');
            return Upload.LIST_IGNORE;
          }
          if (f.size > MAX_BYTES) {
            setFileError(`${f.name} is ${(f.size / 1048576).toFixed(1)} MB — the limit is 20 MB.`);
            return Upload.LIST_IGNORE;
          }
          setFileError(null);
          setFile({ uid: f.uid, name: f.name, size: f.size, type: f.type, status: 'done' });
          setRaw(f);
          if (!replace && !form.getFieldValue('title'))
            form.setFieldValue('title', f.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' '));
          return false;
        }}
        onChange={({ fileList }) => {
          if (fileList.length === 0) {
            setFile(null);
            setRaw(null);
          }
        }}
        onRemove={() => {
          setFile(null);
          setRaw(null);
        }}
        showUploadList={false}
        disabled={!!result}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">Click or drag a file here</p>
        <p className="ant-upload-hint">
          PDF, Word, PowerPoint, Excel, Markdown, text or a photo · max 20 MB
        </p>
      </Upload.Dragger>
      {fileError && <Alert type="error" showIcon title={fileError} style={{ marginTop: 12 }} />}
      {file && (
        <div style={{ marginTop: 12 }}>
          <FileCard
            name={file.name}
            byte={file.size}
            icon={FILE_ICON[ext] ?? 'default'}
            description={
              ext === 'png' || ext === 'jpg'
                ? 'Photo · text read with OCR and embedded with nomic-embed-vision'
                : 'Parsed with unstructured, chunked and embedded'
            }
          />
        </div>
      )}

      {!replace && (
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          disabled={!!result}
          initialValues={{
            station: actor.station ?? 'prep',
            category: actor.role === 'kitchen_manager' ? 'sop' : 'recipe',
            ownerId: actor.crewMemberId ?? undefined,
          }}
        >
          <Form.Item
            name="title"
            label="Title"
            rules={[
              { required: true, min: 5, message: 'Give the document a title (5+ characters)' },
            ]}
          >
            <Input placeholder="e.g. Brisket rub — 2026 update" />
          </Form.Item>
          <Flex gap={12} wrap>
            <Form.Item
              name="station"
              label="Station"
              style={{ flex: '1 1 200px' }}
              rules={[{ required: true }]}
            >
              <Select
                disabled={actor.role !== 'kitchen_manager'}
                options={stations().map((s) => ({ value: s.id, label: s.name }))}
                onChange={() => form.validateFields(['category']).catch(() => undefined)}
              />
            </Form.Item>
            <Form.Item
              name="category"
              label="Category"
              style={{ flex: '1 1 200px' }}
              rules={[
                { required: true },
                {
                  validator: (_r, v: DocCategory) =>
                    can.uploadCategory(actor, v, form.getFieldValue('station'))
                      ? Promise.resolve()
                      : Promise.reject(new Error('Your role cannot publish this category here')),
                },
              ]}
              extra={actor.role === 'sous_chef' ? 'SOP is disabled for Sous Chefs' : undefined}
            >
              <Select options={categoryOptions} />
            </Form.Item>
          </Flex>
          <Form.Item name="ownerId" label="Document owner" rules={[{ required: true }]}>
            <Select
              showSearch={{ optionFilterProp: 'label' }}
              options={crew().map((c) => ({ value: c.id, label: `${c.name} · ${c.title}` }))}
            />
          </Form.Item>
          <Form.Item
            name="summary"
            label="Summary"
            extra="Shown in search results and used as the chunk header."
          >
            <Input.TextArea rows={3} placeholder="One or two sentences on what this covers" />
          </Form.Item>
          <Form.Item name="tags" label="Tags">
            <Select mode="tags" placeholder="cooling, allergens, fryer…" />
          </Form.Item>
        </Form>
      )}

      {job.data && (
        <div style={{ marginTop: 16 }}>
          <Typography.Title level={5}>Ingestion · {job.data.id}</Typography.Title>
          <IngestSteps job={job.data} />
        </div>
      )}
    </Drawer>
  );
}
