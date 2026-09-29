import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Col, Form, Input, Row, Select, Typography } from 'antd';
import { IdcardOutlined, LockOutlined, MailOutlined, UserOutlined } from '@ant-design/icons';
import { api, ApiError } from '@/api/client';
import type { Station, StationId } from '@/api/types';
import type { TokenResponse } from '@/api/views';
import { useSession } from '@/auth/store';
import AuthShell from '@/components/AuthShell';

export interface RegisterValues {
  displayName: string;
  username: string;
  email: string;
  station: StationId;
  password: string;
  confirm: string;
}

const USERNAME = /^[a-z0-9][a-z0-9._-]+$/;

export default function Register() {
  const token = useSession((s) => s.token);
  const login = useSession((s) => s.login);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [form] = Form.useForm<RegisterValues>();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const stations = useQuery({
    queryKey: ['public-stations'],
    queryFn: () => api<Station[]>('/api/auth/stations'),
  });

  if (token) return <Navigate to="/" replace />;

  const submit = async ({ confirm: _confirm, ...v }: RegisterValues) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api<TokenResponse>('/api/auth/register', {
        body: { ...v, username: v.username.trim().toLowerCase(), email: v.email.trim() },
      });
      qc.clear();
      login(res.token);
      message.success(
        `Account created — welcome to the line, ${res.user.displayName.split(' ')[0]}`,
      );
      navigate('/', { replace: true });
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : 'Could not create the account. Check that the back-end is running.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell>
      <Typography.Title level={2} style={{ marginBottom: 4 }}>
        Create account
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        New accounts join as a Line Cook at your station. The Kitchen Manager or an Admin can change
        your role later.
      </Typography.Paragraph>
      <Card>
        <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} disabled={busy}>
          <Form.Item
            name="displayName"
            label="Full name"
            rules={[
              {
                required: true,
                whitespace: true,
                min: 2,
                max: 80,
                message: 'Enter your name (2–80 characters)',
              },
            ]}
          >
            <Input
              prefix={<IdcardOutlined />}
              placeholder="e.g. Rosa Delgado"
              autoComplete="name"
            />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="username"
                label="Username"
                normalize={(v: string) => v?.toLowerCase()}
                rules={[
                  { required: true, message: 'Choose a username' },
                  { min: 3, max: 32, message: 'Use 3–32 characters' },
                  {
                    pattern: USERNAME,
                    message: 'Lowercase letters, numbers, dot, dash or underscore',
                  },
                ]}
              >
                <Input prefix={<UserOutlined />} placeholder="e.g. rosa" autoComplete="username" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="station"
                label="Station"
                rules={[{ required: true, message: 'Pick your station' }]}
              >
                <Select
                  placeholder="Choose a station"
                  loading={stations.isLoading}
                  options={(stations.data ?? []).map((s) => ({ value: s.id, label: s.name }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="email"
            label="Work email"
            rules={[{ required: true, type: 'email', message: 'Enter a valid email address' }]}
          >
            <Input
              prefix={<MailOutlined />}
              placeholder="you@hearthline-kitchen.com"
              autoComplete="email"
            />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="password"
                label="Password"
                hasFeedback
                rules={[
                  { required: true, message: 'Choose a password' },
                  { min: 10, message: 'At least 10 characters' },
                  {
                    pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/,
                    message: 'Use at least one letter and one number',
                  },
                ]}
              >
                <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="confirm"
                label="Confirm password"
                dependencies={['password']}
                hasFeedback
                rules={[
                  { required: true, message: 'Repeat the password' },
                  ({ getFieldValue }) => ({
                    validator: (_, value) =>
                      !value || getFieldValue('password') === value
                        ? Promise.resolve()
                        : Promise.reject(new Error('Passwords do not match')),
                  }),
                ]}
              >
                <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
              </Form.Item>
            </Col>
          </Row>
          {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
          <Button type="primary" htmlType="submit" block size="large" loading={busy}>
            Create account
          </Button>
        </Form>
      </Card>
      <Typography.Paragraph type="secondary" style={{ marginTop: 20, textAlign: 'center' }}>
        Already have an account? <Link to="/login">Sign in</Link>
      </Typography.Paragraph>
    </AuthShell>
  );
}
