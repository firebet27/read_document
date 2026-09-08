import type { Metadata } from 'next';
import { IBM_Plex_Mono, Inter } from 'next/font/google';
import './globals.css';

const inter = Inter({
  variable: '--font-atlas-sans',
  subsets: ['latin', 'vietnamese'],
});

const plexMono = IBM_Plex_Mono({
  variable: '--font-atlas-mono',
  weight: ['400', '500', '600'],
  subsets: ['latin', 'vietnamese'],
});

export const metadata: Metadata = {
  title: 'DocAtlas — Thư viện kỹ thuật local',
  description: 'Đọc, tìm kiếm và hỏi AI trên thư viện tài liệu kỹ thuật Alex.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <body className={`${inter.variable} ${plexMono.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
