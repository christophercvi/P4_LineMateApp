import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { XMarkdown, type ComponentProps } from '@ant-design/x-markdown';
import '@ant-design/x-markdown/themes/light.css';
import '@ant-design/x-markdown/themes/dark.css';
import { useUi } from '@/auth/store';

function InAppLink(props: ComponentProps & { href?: string; children?: ReactNode }) {
  const navigate = useNavigate();
  const href = props.href ?? '';
  if (href.startsWith('/'))
    return (
      <a
        href={href}
        onClick={(e) => {
          e.preventDefault();
          navigate(href);
        }}
      >
        {props.children}
      </a>
    );
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {props.children}
    </a>
  );
}

export function Md({
  content,
  streaming = false,
  citations = false,
}: {
  content: string;
  streaming?: boolean;
  citations?: boolean;
}) {
  const mode = useUi((s) => s.mode);
  const text = citations ? content.replace(/\[(\d{1,2})\](?!\()/g, '<sup>[$1]</sup>') : content;
  return (
    <XMarkdown
      content={text}
      className={mode === 'dark' ? 'x-markdown-dark' : 'x-markdown-light'}
      style={{ background: 'transparent' }}
      paragraphTag="div"
      components={{ a: InAppLink }}
      streaming={{ hasNextChunk: streaming, enableAnimation: true, tail: streaming }}
    />
  );
}
