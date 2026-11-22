import fs from "fs";
import path from "path";
import Link from "next/link";
import { ArrowLeft, BookOpen, ChevronRight, FileText, Layers, Rocket, Settings, Wrench } from "lucide-react";

interface DocItem {
  title: string;
  slug: string;
  description?: string;
  category: string;
}

const FEATURED_DOCS: DocItem[] = [
  {
    category: "Voice Agent & Nodes",
    title: "Start Call Node",
    slug: "voice-agent/start-call",
    description: "Initialize inbound and outbound voice calls with dynamic greetings.",
  },
  {
    category: "Voice Agent & Nodes",
    title: "Agent Node",
    slug: "voice-agent/agent",
    description: "Configuring LLM prompts, state extraction, and conversational behaviors.",
  },
  {
    category: "Voice Agent & Nodes",
    title: "End Call Node",
    slug: "voice-agent/end-call",
    description: "Terminal node logic, call disposition tagging, and goodbye speech.",
  },
  {
    category: "Voice Agent & Nodes",
    title: "Add to Website & Widgets",
    slug: "voice-agent/add-to-website",
    description: "Floating, inline, and headless widget embedding with allowed domains.",
  },
  {
    category: "Configurations",
    title: "Inference & Model Providers",
    slug: "configurations/inference-providers",
    description: "Configure Gemini realtime, Groq, Deepgram, and ElevenLabs credentials.",
  },
  {
    category: "Core Concepts",
    title: "Context and Template Variables",
    slug: "core-concepts/context-and-variables",
    description: "Inject runtime CRM context and dynamic variables into workflow nodes.",
  },
  {
    category: "Tools & Integrations",
    title: "Tools Introduction",
    slug: "voice-agent/tools/introduction",
    description: "Custom functions, HTTP APIs, MCP tool integration, and knowledge bases.",
  },
  {
    category: "Getting Started",
    title: "Platform Overview & Prerequisites",
    slug: "getting-started",
    description: "First-run setup, LiveKit voice engine, and environment architecture.",
  },
];

export default function DocsIndexPage() {
  const categories = Array.from(new Set(FEATURED_DOCS.map((d) => d.category)));

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Top Header */}
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            href="/workflow"
            className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Workflows
          </Link>
          <span className="text-muted-foreground/40 text-xs">/</span>
          <span className="text-xs font-semibold text-foreground">Documentation Center</span>
        </div>
        <div>
          <span className="text-xs px-2.5 py-1 rounded-full bg-primary/10 text-primary border border-primary/20 font-medium">
            SPX Voice Native Docs
          </span>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-5xl mx-auto px-6 py-12">
        <div className="border-b pb-8 mb-10">
          <div className="flex items-center gap-2 text-primary font-medium text-sm mb-2">
            <BookOpen className="w-4 h-4" />
            <span>Developer & Operator Guide</span>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
            SPX Voice Platform Documentation
          </h1>
          <p className="mt-3 text-base text-muted-foreground max-w-2xl leading-relaxed">
            Complete technical guides for designing workflows, configuring Gemini realtime voice agents, connecting telephony carriers, and embedding interactive widgets.
          </p>
        </div>

        {/* Categories */}
        <div className="space-y-10">
          {categories.map((cat) => {
            const docs = FEATURED_DOCS.filter((d) => d.category === cat);
            return (
              <section key={cat} className="space-y-4">
                <h2 className="text-lg font-bold text-foreground border-b pb-2 flex items-center gap-2">
                  <Layers className="w-4 h-4 text-primary" />
                  <span>{cat}</span>
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {docs.map((doc) => (
                    <Link
                      key={doc.slug}
                      href={`/docs/${doc.slug}`}
                      className="group border rounded-xl p-5 bg-card hover:border-primary/50 hover:shadow-sm transition-all flex flex-col justify-between"
                    >
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <h3 className="font-semibold text-foreground group-hover:text-primary transition-colors flex items-center gap-1.5">
                            <FileText className="w-4 h-4 text-muted-foreground group-hover:text-primary" />
                            {doc.title}
                          </h3>
                          <ChevronRight className="w-4 h-4 text-muted-foreground/50 group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                        </div>
                        {doc.description && (
                          <p className="text-xs text-muted-foreground leading-relaxed">
                            {doc.description}
                          </p>
                        )}
                      </div>
                      <div className="mt-4 pt-3 border-t text-[11px] font-mono text-muted-foreground flex items-center gap-1">
                        <span>/docs/{doc.slug}</span>
                      </div>
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </main>
    </div>
  );
}

