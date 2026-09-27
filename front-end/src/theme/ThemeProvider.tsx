import type { ReactNode } from 'react';
import { App as AntApp } from 'antd';
import { XProvider } from '@ant-design/x';
import enUS from 'antd/locale/en_US';
import { useUi } from '@/auth/store';
import { buildTheme } from './tokens';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const mode = useUi((s) => s.mode);
  return (
    <XProvider theme={buildTheme(mode)} locale={enUS}>
      <AntApp>{children}</AntApp>
    </XProvider>
  );
}
