import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

export default function Forbidden({ reason }: { reason?: string }) {
  const navigate = useNavigate();
  return (
    <Result
      status="403"
      title="403 — not available for your role"
      subTitle={
        reason ??
        'Your role does not include this screen. Ask the Kitchen Manager or an Admin if you need access.'
      }
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          Back to dashboard
        </Button>
      }
    />
  );
}
