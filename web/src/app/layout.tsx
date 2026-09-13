import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "星桥 · 企业 Agent 与策略进化",
  description: "协同企业任务，从反馈聚类发现共同问题，通过对照实验改进执行策略",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
