import type { ReactNode } from "react";
import { AppBrand } from "./AppBrand";

/** Shared, content-first entry surface; authentication remains in each page. */
export function AuthPageLayout({ children }: { children: ReactNode }) {
  return (
    <main className="min-h-dvh bg-background px-5 py-7 font-sans text-foreground sm:px-10 sm:py-10 lg:flex lg:items-center">
      <div className="mx-auto w-full max-w-6xl">
        <h1 className="sr-only">品成 BIM 知识库账号入口</h1>
        <header className="mb-8 lg:mb-12">
          <AppBrand subtitle="企业文档 · 知识检索" />
        </header>
        <div className="grid items-center gap-12 lg:grid-cols-[1.15fr_1fr] lg:gap-20">
          <section className="hidden min-w-0 lg:block" aria-labelledby="knowledge-entry-title">
            <p className="mb-5 flex items-center gap-3 text-ui-xs font-medium tracking-widest text-muted-foreground">
              <span className="h-px w-8 bg-primary" aria-hidden="true" />品成 · 企业知识工作台
            </p>
            <h2 id="knowledge-entry-title" className="text-4xl font-semibold leading-[1.4] tracking-tight xl:text-5xl">
              让项目经验，<br />成为团队的共同知识。
            </h2>
            <p className="mt-6 max-w-md text-ui-base leading-8 text-muted-foreground">
              从行业规范到项目资料，在企业文档中查找依据，让每一次提问都有迹可循。
            </p>
            <dl className="mt-10 max-w-md divide-y divide-border border-y border-border">
              <div className="flex gap-6 py-4">
                <dt className="shrink-0 text-ui-sm font-semibold">标准与规范</dt>
                <dd className="text-ui-sm leading-6 text-muted-foreground">行业规范、客户标准、公司标准</dd>
              </div>
              <div className="flex gap-6 py-4">
                <dt className="shrink-0 text-ui-sm font-semibold">经验与方法</dt>
                <dd className="text-ui-sm leading-6 text-muted-foreground">项目资料、培训视频转写稿</dd>
              </div>
            </dl>
          </section>
          <div className="mx-auto w-full max-w-md lg:mx-0">{children}</div>
        </div>
        <footer className="mt-8 text-center text-ui-xs leading-6 text-muted-foreground lg:mt-12 lg:text-left">
          品成 BIM 知识库 · 企业内部知识协作
        </footer>
      </div>
    </main>
  );
}
