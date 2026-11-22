import fs from "fs";
import path from "path";
import Link from "next/link";
import {
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ExternalLink,
  FileText,
  HelpCircle,
  Info,
  AlertTriangle,
  Lightbulb,
} from "lucide-react";
import { notFound } from "next/navigation";

interface DocsPageProps {
  params: Promise<{ slug: string[] }>;
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/--+/g, "-")
    .trim();
}

function getHeadingAliases(slug: string): string[] {
  const aliases = new Set<string>([slug]);
  if (slug.includes("floating-widget")) {
    aliases.add("floating-widget");
  }
  if (slug.includes("inline-component")) {
    aliases.add("inline-component");
  }
  if (slug.includes("headless")) {
    aliases.add("headless-mode");
    aliases.add("headless");
  }
  if (slug.includes("allowed-domain")) {
    aliases.add("allowed-domains");
    aliases.add("allowed-domains-and-origin-validation");
  }
  if (slug.includes("microphon")) {
    aliases.add("microphone-permissions");
    aliases.add("microphone-permissions-and-https");
    aliases.add("mic-permissions");
  }
  if (slug.includes("lead-capture") || slug.includes("crm")) {
    aliases.add("lead-capture");
    aliases.add("website-lead-capture-and-crm-tracking");
    aliases.add("website-lead-capture");
  }
  if (slug.includes("model") || slug.includes("voice-engine")) {
    aliases.add("model-voice-selection");
    aliases.add("model-and-voice-engine-selection");
    aliases.add("voice-engines");
  }
  if (slug.includes("troubleshoot")) {
    aliases.add("troubleshooting");
    aliases.add("troubleshooting-and-faqs");
  }
  if (slug.includes("lifecycle")) {
    aliases.add("lifecycle-callbacks");
    aliases.add("lifecycle-callbacks-all-modes");
  }
  return Array.from(aliases);
}

function renderInline(text: string) {
  if (!text) return null;

  // Split by inline code first
  const codeParts = text.split(/(`[^`]+`)/g);

  return codeParts.map((codePart, i) => {
    if (codePart.startsWith("`") && codePart.endsWith("`")) {
      return (
        <code
          key={i}
          className="bg-muted px-1.5 py-0.5 rounded text-[11px] font-mono font-medium text-foreground border border-border/50"
        >
          {codePart.slice(1, -1)}
        </code>
      );
    }

    // Handle markdown links [label](url)
    const linkParts = codePart.split(/(\[[^\]]+\]\([^)]+\))/g);
    return linkParts.map((linkPart, j) => {
      const linkMatch = linkPart.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        const [, label, href] = linkMatch;
        const isExternal = href.startsWith("http://") || href.startsWith("https://");
        return isExternal ? (
          <a
            key={`${i}-${j}`}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline hover:opacity-80 inline-flex items-center gap-0.5"
          >
            {label}
            <ExternalLink className="w-3 h-3 inline ml-0.5" />
          </a>
        ) : (
          <a
            key={`${i}-${j}`}
            href={href}
            className="text-primary underline hover:opacity-80"
          >
            {label}
          </a>
        );
      }

      // Handle bold **text**
      const boldParts = linkPart.split(/(\*\*[^*]+\*\*)/g);
      return boldParts.map((boldPart, k) => {
        if (boldPart.startsWith("**") && boldPart.endsWith("**")) {
          return (
            <strong key={`${i}-${j}-${k}`} className="font-semibold text-foreground">
              {boldPart.slice(2, -2)}
            </strong>
          );
        }
        return boldPart;
      });
    });
  });
}

function parseMdx(content: string) {
  let title = "Documentation";
  let description = "";
  let body = content;

  // Extract frontmatter
  if (content.startsWith("---")) {
    const endIdx = content.indexOf("---", 3);
    if (endIdx !== -1) {
      const frontmatter = content.slice(3, endIdx).trim();
      body = content.slice(endIdx + 3).trim();

      const titleMatch = frontmatter.match(/title:\s*["']?([^"'\n\r]+)["']?/i);
      if (titleMatch) title = titleMatch[1].trim();

      const descMatch = frontmatter.match(/description:\s*["']?([^"'\n\r]+)["']?/i);
      if (descMatch) description = descMatch[1].trim();
    }
  }

  return { title, description, body };
}

export default async function DocsPage({ params }: DocsPageProps) {
  const { slug } = await params;
  if (!slug || slug.length === 0) {
    notFound();
  }

  const docsDir = path.resolve(process.cwd(), "..", "docs");
  const rel = slug.join(path.sep);

  const candidates = [
    path.join(docsDir, rel + ".mdx"),
    path.join(docsDir, rel + ".md"),
    path.join(docsDir, rel, "index.mdx"),
    path.join(docsDir, rel, "index.md"),
    path.join(docsDir, rel, "overview.mdx"),
  ];

  let rawContent: string | null = null;

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      rawContent = fs.readFileSync(candidate, "utf-8");
      break;
    }
  }

  if (!rawContent) {
    return (
      <div className="min-h-screen bg-background text-foreground flex flex-col items-center justify-center p-6">
        <div className="max-w-md w-full bg-card border rounded-xl p-8 text-center shadow-sm">
          <BookOpen className="w-12 h-12 text-primary mx-auto mb-4 opacity-80" />
          <h1 className="text-xl font-bold mb-2">Doc Page Not Found</h1>
          <p className="text-sm text-muted-foreground mb-6">
            Requested document <code className="bg-muted px-1.5 py-0.5 rounded text-xs">{slug.join("/")}</code> could not be located in the local documentation repository.
          </p>
          <div className="flex gap-3 justify-center">
            <Link
              href="/docs"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
            >
              Browse Docs
            </Link>
            <Link
              href="/workflow"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium hover:bg-muted transition-colors"
            >
              Back to Workflows
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const { title, description, body } = parseMdx(rawContent);

  // Split into raw blocks
  const rawBlocks = body.split(/\n\s*\n/);

  return (
    <div className="min-h-screen bg-background text-foreground scroll-smooth">
      {/* Top Navigation */}
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur px-6 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            href="/docs"
            className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            All Documentation
          </Link>
          <span className="text-muted-foreground/40 text-xs">/</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-xs">{title}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 font-medium">
            SPX Voice Native Docs
          </span>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="max-w-4xl mx-auto px-6 py-10">
        <article className="space-y-6">
          <header className="border-b pb-6">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground mb-2">
              <FileText className="w-3.5 h-3.5" />
              <span>docs/{slug.join("/")}</span>
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight text-foreground">{title}</h1>
            {description && (
              <p className="mt-2.5 text-base text-muted-foreground leading-relaxed">
                {description}
              </p>
            )}
          </header>

          <div className="pt-2 text-sm leading-7 space-y-4">
            {rawBlocks.map((block, idx) => {
              const trimmed = block.trim();
              if (!trimmed) return null;

              // Callout tags: <Note>, <Tip>, <Warning>, <Info>
              const calloutMatch = trimmed.match(/^<(Note|Tip|Warning|Info|Card)>([\s\S]*?)<\/\1>$/i);
              if (calloutMatch) {
                const type = calloutMatch[1].toLowerCase();
                const content = calloutMatch[2].trim();
                const isWarn = type === "warning";
                const isTip = type === "tip";
                return (
                  <div
                    key={idx}
                    className={`my-4 p-4 rounded-xl border flex gap-3 ${
                      isWarn
                        ? "bg-destructive/10 border-destructive/30 text-destructive-foreground"
                        : isTip
                        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-900 dark:text-emerald-200"
                        : "bg-primary/5 border-primary/20 text-foreground"
                    }`}
                  >
                    {isWarn ? (
                      <AlertTriangle className="w-5 h-5 text-destructive shrink-0 mt-0.5" />
                    ) : isTip ? (
                      <Lightbulb className="w-5 h-5 text-emerald-500 shrink-0 mt-0.5" />
                    ) : (
                      <Info className="w-5 h-5 text-primary shrink-0 mt-0.5" />
                    )}
                    <div className="text-xs leading-relaxed">
                      {renderInline(content)}
                    </div>
                  </div>
                );
              }

              // Code block
              if (trimmed.startsWith("```")) {
                const codeLines = trimmed.split("\n");
                const lang = codeLines[0].replace(/```/, "").trim();
                const code = codeLines.slice(1, -1).join("\n");
                return (
                  <div key={idx} className="my-4 rounded-lg overflow-hidden border bg-muted/40 font-mono text-xs">
                    {lang && (
                      <div className="px-4 py-1.5 border-b bg-muted/60 text-muted-foreground font-semibold flex items-center justify-between">
                        <span>{lang}</span>
                      </div>
                    )}
                    <pre className="p-4 overflow-x-auto text-foreground/90">
                      <code>{code}</code>
                    </pre>
                  </div>
                );
              }

              // Markdown Table: lines starting with "|"
              if (trimmed.startsWith("|") && trimmed.includes("\n|")) {
                const lines = trimmed
                  .split("\n")
                  .map((l) => l.trim())
                  .filter((l) => l.startsWith("|"));

                if (lines.length >= 2) {
                  const parseCells = (line: string) =>
                    line
                      .slice(1, line.endsWith("|") ? -1 : undefined)
                      .split("|")
                      .map((c) => c.trim());

                  const headers = parseCells(lines[0]);
                  // line 1 is separator |---|---|
                  const rows = lines.slice(2).map(parseCells);

                  return (
                    <div key={idx} className="my-6 overflow-x-auto rounded-lg border bg-card shadow-sm">
                      <table className="w-full text-left text-xs divide-y divide-border">
                        <thead className="bg-muted/60 font-semibold text-foreground">
                          <tr>
                            {headers.map((h, hi) => (
                              <th key={hi} className="px-4 py-3 border-r last:border-r-0">
                                {renderInline(h)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                          {rows.map((row, ri) => (
                            <tr key={ri} className="hover:bg-muted/30 transition-colors">
                              {row.map((cell, ci) => (
                                <td key={ci} className="px-4 py-3 border-r last:border-r-0 leading-relaxed text-muted-foreground">
                                  {renderInline(cell)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  );
                }
              }

              // Headings with Anchor IDs
              if (trimmed.startsWith("#### ")) {
                const headingText = trimmed.replace(/^####\s+/, "").trim();
                const primarySlug = slugify(headingText);
                const aliases = getHeadingAliases(primarySlug);
                return (
                  <div key={idx} className="relative mt-6 mb-2">
                    {aliases.map((a) => (
                      <span key={a} id={a} className="relative -top-24 block invisible" />
                    ))}
                    <h4 className="group text-base font-semibold text-foreground flex items-center gap-2">
                      <span>{renderInline(headingText)}</span>
                      <a href={`#${primarySlug}`} className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-primary transition-opacity text-xs font-mono">
                        #
                      </a>
                    </h4>
                  </div>
                );
              }
              if (trimmed.startsWith("### ")) {
                const headingText = trimmed.replace(/^###\s+/, "").trim();
                const primarySlug = slugify(headingText);
                const aliases = getHeadingAliases(primarySlug);
                return (
                  <div key={idx} className="relative mt-8 mb-3">
                    {aliases.map((a) => (
                      <span key={a} id={a} className="relative -top-24 block invisible" />
                    ))}
                    <h3 className="group text-lg font-bold text-foreground flex items-center gap-2">
                      <span>{renderInline(headingText)}</span>
                      <a href={`#${primarySlug}`} className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-primary transition-opacity text-sm font-mono">
                        #
                      </a>
                    </h3>
                  </div>
                );
              }
              if (trimmed.startsWith("## ")) {
                const headingText = trimmed.replace(/^##\s+/, "").trim();
                const primarySlug = slugify(headingText);
                const aliases = getHeadingAliases(primarySlug);
                return (
                  <div key={idx} className="relative mt-10 mb-4 border-b pb-1.5">
                    {aliases.map((a) => (
                      <span key={a} id={a} className="relative -top-24 block invisible" />
                    ))}
                    <h2 className="group text-xl font-bold text-foreground flex items-center gap-2">
                      <span>{renderInline(headingText)}</span>
                      <a href={`#${primarySlug}`} className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-primary transition-opacity text-sm font-mono">
                        #
                      </a>
                    </h2>
                  </div>
                );
              }
              if (trimmed.startsWith("# ")) {
                const headingText = trimmed.replace(/^#\s+/, "").trim();
                const primarySlug = slugify(headingText);
                const aliases = getHeadingAliases(primarySlug);
                return (
                  <div key={idx} className="relative mt-10 mb-4">
                    {aliases.map((a) => (
                      <span key={a} id={a} className="relative -top-24 block invisible" />
                    ))}
                    <h1 className="group text-2xl font-bold text-foreground flex items-center gap-2">
                      <span>{renderInline(headingText)}</span>
                      <a href={`#${primarySlug}`} className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-primary transition-opacity text-sm font-mono">
                        #
                      </a>
                    </h1>
                  </div>
                );
              }

              // Bullet list
              if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
                const items = trimmed.split("\n").filter((l) => l.trim().startsWith("- ") || l.trim().startsWith("* "));
                return (
                  <ul key={idx} className="list-disc list-inside space-y-1.5 my-3 text-muted-foreground text-xs leading-relaxed">
                    {items.map((it, itemIdx) => (
                      <li key={itemIdx}>
                        <span className="text-foreground">{renderInline(it.replace(/^[-*]\s+/, ""))}</span>
                      </li>
                    ))}
                  </ul>
                );
              }

              // Numbered list
              if (/^\d+\.\s/.test(trimmed)) {
                const items = trimmed.split("\n").filter((l) => /^\d+\.\s/.test(l.trim()));
                return (
                  <ol key={idx} className="list-decimal list-inside space-y-1.5 my-3 text-muted-foreground text-xs leading-relaxed">
                    {items.map((it, itemIdx) => (
                      <li key={itemIdx}>
                        <span className="text-foreground">{renderInline(it.replace(/^\d+\.\s+/, ""))}</span>
                      </li>
                    ))}
                  </ol>
                );
              }

              // Images: ![alt](src)
              const imgMatch = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
              if (imgMatch) {
                const [, altText] = imgMatch;
                return (
                  <div key={idx} className="my-4 p-4 rounded-xl border bg-muted/20 text-center">
                    <div className="inline-flex items-center gap-2 text-xs font-medium text-muted-foreground">
                      <FileText className="w-4 h-4 text-primary" />
                      <span>{altText || "Image Diagram"}</span>
                    </div>
                  </div>
                );
              }

              // Normal paragraph
              return (
                <p key={idx} className="text-foreground/90 leading-relaxed text-xs">
                  {renderInline(trimmed)}
                </p>
              );
            })}
          </div>
        </article>
      </main>
    </div>
  );
}
