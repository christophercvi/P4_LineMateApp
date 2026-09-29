import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { api, ApiError } from '@/api/client';
import type { TokenResponse } from '@/api/views';
import { useSession } from '@/auth/store';
import AuthShell from '@/components/AuthShell';

export default function Login() {
  const token = useSession((s) => s.token);
  const login = useSession((s) => s.login);
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/';
  if (token) return <Navigate to={from} replace />;

  const signIn = async ({ username, password }: { username: string; password: string }) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api<TokenResponse>('/api/auth/login', {
        body: { username: username.trim(), password },
      });
      qc.clear();
      login(res.token);
      message.success(`Welcome back, ${res.user.displayName.split(' ')[0]}`);
      navigate(from, { replace: true });
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : 'Sign-in failed. Check that the back-end is running.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell>
      <Typography.Title level={2} style={{ marginBottom: 4 }}>
        Sign in
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Use your Hearthline kitchen account.
      </Typography.Paragraph>
      <Card>
        <Form layout="vertical" requiredMark={false} onFinish={signIn} disabled={busy}>
          <Form.Item
            name="username"
            label="Username"
            rules={[{ required: true, message: 'Enter your username' }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="e.g. priya"
              autoComplete="username"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="Password"
            rules={[{ required: true, message: 'Enter your password' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="Password"
              autoComplete="current-password"
              size="large"
            />
          </Form.Item>
          {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
          <Button type="primary" htmlType="submit" block size="large" loading={busy}>
            Sign in
          </Button>
        </Form>
      </Card>
      <Typography.Paragraph type="secondary" style={{ marginTop: 20, textAlign: 'center' }}>
        New to the line? <Link to="/register">Create an account</Link>
      </Typography.Paragraph>
    </AuthShell>
  );
}
