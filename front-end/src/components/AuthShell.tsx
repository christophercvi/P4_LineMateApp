import type { ReactNode } from 'react';
import { Col, Flex, Row, Typography } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { LOGO_URL } from '@/theme/tokens';

const POINTS = [
  'Answers cite the SOP and show how fresh it is',
  'Stale procedures and mis-assigned tickets surface automatically',
  'Supply orders and escalations wait for human approval',
];

/** Two-column frame shared by Sign in and Create account: brand story on the left, the form on the right. */
export default function AuthShell({ children }: { children: ReactNode }) {
  return (
    <Row style={{ minHeight: '100vh' }}>
      <Col
        xs={0}
        lg={10}
        style={{
          background: 'linear-gradient(160deg, #1C1917 0%, #292524 55%, #7C2D12 100%)',
          padding: '56px 56px 40px',
          color: '#FAF7F2',
        }}
      >
        <Flex vertical justify="space-between" style={{ height: '100%' }}>
          <Flex align="center" gap={12}>
            <img
              src={LOGO_URL}
              alt="LineMate logo"
              width={44}
              height={44}
              style={{ borderRadius: 10 }}
            />
            <Typography.Title level={3} style={{ color: '#FAF7F2', margin: 0 }}>
              LineMate
            </Typography.Title>
          </Flex>
          <div>
            <Typography.Title
              style={{ color: '#FAF7F2', fontSize: 38, lineHeight: 1.15, marginBottom: 16 }}
            >
              A sous chef who has read every SOP.
            </Typography.Title>
            <Typography.Paragraph
              style={{ color: 'rgba(250,247,242,0.75)', fontSize: 16, maxWidth: 460 }}
            >
              Ask cited questions about recipes and procedures, track kitchen issues, and let the
              agent triage the shift — with the Kitchen Manager approving anything that changes the
              world.
            </Typography.Paragraph>
            <Flex vertical gap={10} style={{ marginTop: 24 }}>
              {POINTS.map((t) => (
                <Flex key={t} gap={10} align="center">
                  <CheckOutlined style={{ color: '#FB923C' }} />
                  <Typography.Text style={{ color: 'rgba(250,247,242,0.85)' }}>{t}</Typography.Text>
                </Flex>
              ))}
            </Flex>
          </div>
          <Typography.Text style={{ color: 'rgba(250,247,242,0.45)', fontSize: 12 }}>
            Hearthline Restaurant Group · kitchen operations
          </Typography.Text>
        </Flex>
      </Col>
      <Col xs={24} lg={14}>
        <Flex
          vertical
          justify="center"
          style={{
            minHeight: '100vh',
            padding: '32px clamp(16px, 5vw, 72px)',
            maxWidth: 640,
            margin: '0 auto',
          }}
        >
          <Flex align="center" gap={10} className="lm-show-mobile" style={{ marginBottom: 24 }}>
            <img src={LOGO_URL} alt="" width={36} height={36} style={{ borderRadius: 8 }} />
            <Typography.Title level={4} style={{ margin: 0 }}>
              LineMate
            </Typography.Title>
          </Flex>
          {children}
        </Flex>
      </Col>
    </Row>
  );
}
