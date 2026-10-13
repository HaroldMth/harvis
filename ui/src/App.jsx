import React, { useState, useEffect, useRef, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Mic, MicOff, Send, Volume2, VolumeX, Loader2, Copy, Check, Eye, Code2, Maximize2, X, Play, RefreshCw, ChevronDown, ChevronRight, Shield, Wrench, Terminal, Image as ImageIcon } from "lucide-react";

import * as Fa6 from "react-icons/fa6";
import * as Si from "react-icons/si";
import * as Md from "react-icons/md";
import * as Hi2 from "react-icons/hi2";
import * as Lu from "react-icons/lu";
import * as Tb from "react-icons/tb";

// ── Design tokens ─────────────────────────────────────────────────────────
// Grounded in a terminal / systemd-log aesthetic (Harold builds Linux distros
// and works from a shell all day) instead of the generic dark-purple-glass
// "AI product" default. Amber phosphor accent, sharp corners, no gradients,
// no blur, hairline borders. Monospace throughout — this is a console, not
// a chat app that happens to be dark mode.
const C = {
  bg:         "#0a0a0a",
  surface:    "#111111",
  surface2:   "#161616",
  surface3:   "#1c1c1c",
  border:     "#242424",
  borderHi:   "#3a3a3a",
  text:       "#d9d8d3",
  textMuted:  "#7a7a74",
  textDim:    "#454540",
  amber:      "#e8a33d",
  amberDim:   "#6b5227",
  green:      "#5fbf6f",
  greenDim:   "#2f4a34",
  red:        "#d9635a",
  redDim:     "#4a2f2c",
  mono: "'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace",
};

const STATE_CONFIG = {
  idle:      { label: "idle",      color: C.textMuted },
  listening: { label: "listening", color: C.amber },
  thinking:  { label: "thinking",  color: C.amber },
  speaking:  { label: "speaking",  color: C.green },
  error:     { label: "error",     color: C.red },
};

// ── Icon alias resolver (unchanged behaviour) ────────────────────────────
const ALIAS_MAP = {
  terminal: Fa6.FaTerminal, console: Fa6.FaTerminal, bash: Fa6.FaTerminal, shell: Fa6.FaTerminal,
  python: Si.SiPython, react: Si.SiReact, arch: Si.SiArchlinux, linux: Si.SiLinux,
  docker: Si.SiDocker, git: Si.SiGit, github: Si.SiGithub, youtube: Si.SiYoutube,
  folder: Fa6.FaFolder, file: Fa6.FaFileCode, code: Fa6.FaCode, check: Fa6.FaCheck,
  check_circle: Fa6.FaCircleCheck, error: Fa6.FaCircleXmark, warning: Fa6.FaTriangleExclamation,
  info: Fa6.FaCircleInfo, gear: Fa6.FaGear, settings: Fa6.FaGear, search: Fa6.FaMagnifyingGlass,
  star: Fa6.FaStar, rocket: Fa6.FaRocket, sun: Fa6.FaSun, cloud: Fa6.FaCloud,
  volume: Fa6.FaVolumeHigh, music: Fa6.FaMusic, cpu: Fa6.FaMicrochip, memory: Fa6.FaMemory,
  disk: Fa6.FaHardDrive, link: Fa6.FaLink, lock: Fa6.FaLock, user: Fa6.FaUser, bot: Fa6.FaRobot,
  home: Fa6.FaHouse, play: Fa6.FaPlay, pause: Fa6.FaPause, mic: Fa6.FaMicrophone,
};

function getIconComponent(name) {
  if (!name) return null;
  const clean = name.trim().replace(/^:|:$/g, "");
  const lower = clean.toLowerCase();
  if (ALIAS_MAP[lower]) return ALIAS_MAP[lower];
  return Fa6[clean] || Si[clean] || Md[clean] || Hi2[clean] || Lu[clean] || Tb[clean] || null;
}

function renderTextWithIcons(children) {
  if (children === null || children === undefined) return children;

  if (typeof children === "string") {
    const regex = /:([A-Za-z0-9_-]+):/g;
    if (!regex.test(children)) return children;
    regex.lastIndex = 0;
    const parts = [];
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(children)) !== null) {
      const IconComp = getIconComponent(match[1]);
      if (IconComp) {
        if (match.index > lastIndex) parts.push(children.slice(lastIndex, match.index));
        parts.push(
          <IconComp key={`icon-${match.index}-${match[1]}`} style={{
            display: "inline-block", verticalAlign: "-0.15em", margin: "0 3px",
            color: C.amber, fontSize: "1em",
          }} />
        );
        lastIndex = regex.lastIndex;
      }
    }
    if (lastIndex < children.length) parts.push(children.slice(lastIndex));
    return parts.length > 0 ? parts : children;
  }

  if (Array.isArray(children)) return React.Children.map(children, child => renderTextWithIcons(child));

  if (React.isValidElement(children)) {
    if (children.type === "code" || children.type === "pre") return children;
    if (children.props && children.props.children) {
      return React.cloneElement(children, { children: renderTextWithIcons(children.props.children) });
    }
  }
  return children;
}

// ── Copy control ──────────────────────────────────────────────────────────
function CopyButton({ code }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard?.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1600); }}
      style={{
        background: "transparent", border: `1px solid ${C.border}`, borderRadius: 2,
        padding: "3px 8px", cursor: "pointer",
        color: copied ? C.green : C.textMuted, fontSize: 11, fontFamily: C.mono,
        display: "inline-flex", alignItems: "center", gap: 5,
      }}
    >
      {copied ? <><Check size={11} /> copied</> : <><Copy size={11} /> copy</>}
    </button>
  );
}

// ── Process trace (was "ActionTraceAccordion") ───────────────────────────
// Styled like a journalctl / build-log block rather than a card with emoji
// badges: monospace lines, bracket status tags, hairline dividers.
function ActionTraceAccordion({ actions }) {
  const [open, setOpen] = useState(false);
  if (!actions || actions.length === 0) return null;

  const stepWord = actions.length === 1 ? "step" : "steps";

  return (
    <div style={{ margin: "6px 0 10px", border: `1px solid ${C.border}`, borderLeft: `2px solid ${C.amberDim}`, background: C.surface2 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: "100%", border: "none", background: "transparent",
          padding: "7px 10px", cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          color: C.text, fontFamily: C.mono, fontSize: 12,
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <Terminal size={12} color={C.amber} />
          <span style={{ color: C.amber }}>trace</span>
          <span style={{ color: C.textMuted }}>({actions.length} {stepWord})</span>
        </span>
        {open ? <ChevronDown size={13} color={C.textMuted} /> : <ChevronRight size={13} color={C.textMuted} />}
      </button>

      {open && (
        <div style={{ borderTop: `1px solid ${C.border}` }}>
          {actions.map((act, i) => {
            const isSubagent = act.tool_name === "spawn_subagent";
            const agentName = isSubagent ? (act.tool_args?.agent || "subagent").toLowerCase() : null;
            const taskDesc = isSubagent ? (act.tool_args?.task || "execution") : JSON.stringify(act.tool_args || {});
            const isFriday = agentName === "friday";
            const isEdith = agentName === "edith";
            const AgentIcon = isFriday ? Shield : (isEdith ? Wrench : Terminal);
            const label = isFriday ? "friday" : (isEdith ? "edith" : act.tool_name);

            const statusText = act.status === "running" ? "RUNNING" : (act.status === "failed" ? "FAILED" : "OK");
            const statusColor = act.status === "running" ? C.amber : (act.status === "failed" ? C.red : C.green);

            return (
              <div key={i} style={{
                padding: "8px 10px",
                borderBottom: i === actions.length - 1 ? "none" : `1px solid ${C.border}`,
                fontFamily: C.mono, fontSize: 11.5,
              }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 6, color: C.text }}>
                    <AgentIcon size={11} color={C.textMuted} />
                    <span style={{ color: C.amber }}>{label}</span>
                  </span>
                  <span style={{ color: statusColor, fontWeight: 700, letterSpacing: 0.5 }}>[{statusText}]</span>
                </div>
                <p style={{ margin: "4px 0 0", color: C.textMuted, paddingLeft: 17 }}>{taskDesc}</p>

                {act.output && (
                  <details style={{ marginTop: 6, paddingLeft: 17 }}>
                    <summary style={{ fontSize: 10.5, color: C.textDim, cursor: "pointer" }}>output</summary>
                    <pre style={{
                      marginTop: 4, padding: "6px 8px", background: C.bg, border: `1px solid ${C.border}`,
                      fontSize: 11, lineHeight: 1.5, color: C.textMuted, overflowX: "auto", maxHeight: 180,
                    }}>
                      <code>{act.output}</code>
                    </pre>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Live artifact stage ───────────────────────────────────────────────────
function LiveArtifact({ lang, code }) {
  const [tab, setTab] = useState("preview");
  const [fullscreen, setFullscreen] = useState(false);
  const [key, setKey] = useState(0);

  const isReact = ["jsx", "react", "tsx"].includes((lang || "jsx").toLowerCase());
  const isHtml = ["html", "htm"].includes((lang || "").toLowerCase());
  const isSvg = (lang || "").toLowerCase() === "svg";

  const buildSrcDoc = useCallback(() => {
    if (isReact || (!isHtml && !isSvg)) {
      let cleanCode = code
        .replace(/import\s+.*?from\s+['"].*?['"];?/g, "")
        .replace(/export\s+default\s+function\s+([A-Za-z0-9_]+)/, "function $1")
        .replace(/export\s+default\s+/, "const App = ");

      return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone@7/babel.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; padding: 12px; background: #0a0a0a; color: #d9d8d3; font-family: system-ui, sans-serif; overflow-x: hidden; }
    #err-box { display: none; background: #1a0f0e; color: #d9635a; border: 1px solid #4a2f2c; padding: 12px; font-family: monospace; font-size: 12px; white-space: pre-wrap; margin-top: 10px; }
  </style>
</head>
<body>
  <div id="root"></div>
  <div id="err-box"></div>
  <script type="text/babel">
    window.onerror = function(msg, url, line) {
      const err = document.getElementById("err-box");
      if (err) { err.style.display = "block"; err.textContent = "Runtime error: " + msg + " (line " + line + ")"; }
    };
    try {
      const { useState, useEffect, useRef, useMemo, useCallback } = React;
      ${cleanCode}
      const Target = typeof App !== 'undefined' ? App : (typeof Component !== 'undefined' ? Component : null);
      if (Target) {
        ReactDOM.createRoot(document.getElementById('root')).render(<Target />);
      } else {
        document.getElementById('root').innerHTML = '<div style="color:#7a7a74;font-size:13px;padding:12px;">No App or Component export found.</div>';
      }
    } catch (e) {
      const err = document.getElementById("err-box");
      if (err) { err.style.display = "block"; err.textContent = "Render error: " + e.message; }
    }
  </script>
</body>
</html>`;
    }

    if (isHtml) {
      return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { margin: 0; padding: 12px; background: #0a0a0a; color: #d9d8d3; font-family: system-ui, sans-serif; overflow-x: hidden; }
  </style>
</head>
<body>${code}</body>
</html>`;
    }
    return "";
  }, [code, isReact, isHtml, isSvg]);

  const title = isReact ? "react artifact" : (isHtml ? "web artifact" : "svg");

  const shell = (fs) => (
    <div style={{ background: C.surface2, border: `1px solid ${C.border}`, overflow: "hidden", margin: fs ? 0 : "8px 0" }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "6px 10px", background: C.surface3, borderBottom: `1px solid ${C.border}`,
      }}>
        <span style={{ fontFamily: C.mono, fontSize: 11.5, color: C.amber, display: "flex", alignItems: "center", gap: 6 }}>
          <Play size={10} color={C.amber} /> {title}
        </span>

        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ display: "flex", border: `1px solid ${C.border}` }}>
            <button onClick={() => setTab("preview")} style={{
              background: tab === "preview" ? C.surface : "transparent", border: "none",
              padding: "3px 9px", color: tab === "preview" ? C.text : C.textMuted,
              fontSize: 11, fontFamily: C.mono, cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
            }}><Eye size={11} /> view</button>
            <button onClick={() => setTab("code")} style={{
              background: tab === "code" ? C.surface : "transparent", border: "none",
              padding: "3px 9px", color: tab === "code" ? C.text : C.textMuted,
              fontSize: 11, fontFamily: C.mono, cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
            }}><Code2 size={11} /> code</button>
          </div>
          {!fs && (
            <button title="refresh" onClick={() => setKey(k => k + 1)} style={{
              background: "transparent", border: `1px solid ${C.border}`, padding: 4, cursor: "pointer", color: C.textMuted,
              display: "flex", alignItems: "center",
            }}><RefreshCw size={11} /></button>
          )}
          <button title={fs ? "close" : "expand"} onClick={() => setFullscreen(f => !f)} style={{
            background: "transparent", border: `1px solid ${C.border}`, padding: 4, cursor: "pointer", color: C.textMuted,
            display: "flex", alignItems: "center",
          }}>{fs ? <X size={11} /> : <Maximize2 size={11} />}</button>
          <CopyButton code={code} />
        </div>
      </div>

      {tab === "preview" ? (
        isSvg ? (
          <div style={{ padding: 16, display: "flex", justifyContent: "center", overflowX: "auto", height: fs ? "100%" : undefined }}
            dangerouslySetInnerHTML={{ __html: code }} />
        ) : (
          <iframe key={key} title={title} srcDoc={buildSrcDoc()} sandbox="allow-scripts allow-modals allow-same-origin"
            style={{ width: "100%", height: fs ? "100%" : 340, border: "none", display: "block", background: C.bg }} />
        )
      ) : (
        <pre style={{
          background: C.bg, padding: "12px 14px", margin: 0, overflowX: "auto",
          fontSize: 12.5, lineHeight: 1.6, fontFamily: C.mono, color: C.text,
          maxHeight: fs ? "100%" : 360,
        }}><code>{code}</code></pre>
      )}
    </div>
  );

  return (
    <>
      {shell(false)}
      {fullscreen && (
        <div style={{ position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,0.92)", display: "flex", padding: 20 }}>
          <div style={{ flex: 1 }}>{shell(true)}</div>
        </div>
      )}
    </>
  );
}

// ── Markdown ──────────────────────────────────────────────────────────────
const MD_COMPONENTS = {
  code({ node, inline, className, children, ...props }) {
    const code = String(children).replace(/\n$/, "");
    const lang = (className || "").replace("language-", "");
    const isInline = inline !== undefined ? Boolean(inline) : (!className && !String(children).includes("\n"));

    if (isInline) {
      return (
        <code style={{
          background: C.surface3, border: `1px solid ${C.border}`, borderRadius: 2, padding: "1px 5px",
          fontFamily: C.mono, fontSize: "0.9em", color: C.amber,
        }}>{code}</code>
      );
    }

    if (["jsx", "react", "tsx", "html", "htm", "svg"].includes(lang.toLowerCase())) {
      return <LiveArtifact lang={lang} code={code} />;
    }

    return (
      <div style={{ marginBottom: 10 }}>
        {lang && (
          <div style={{
            background: C.surface3, borderBottom: `1px solid ${C.border}`,
            padding: "4px 10px", fontSize: 11, color: C.textMuted, fontFamily: C.mono,
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>{lang}</span>
            <CopyButton code={code} />
          </div>
        )}
        <pre className={className} style={{
          background: C.surface2, border: `1px solid ${C.border}`, borderTop: lang ? "none" : `1px solid ${C.border}`,
          padding: "12px 14px", overflowX: "auto", margin: 0,
          fontSize: 13, lineHeight: 1.6, fontFamily: C.mono,
        }}><code {...props}>{children}</code></pre>
      </div>
    );
  },

  h1: ({ children }) => <h1 style={{ fontSize: 18, fontWeight: 700, color: C.text, marginBottom: 8, marginTop: 16, borderBottom: `1px solid ${C.border}`, paddingBottom: 6 }}>{renderTextWithIcons(children)}</h1>,
  h2: ({ children }) => <h2 style={{ fontSize: 15.5, fontWeight: 700, color: C.text, marginBottom: 6, marginTop: 14 }}>{renderTextWithIcons(children)}</h2>,
  h3: ({ children }) => <h3 style={{ fontSize: 14, fontWeight: 700, color: C.amber, marginBottom: 4, marginTop: 12 }}>{renderTextWithIcons(children)}</h3>,
  p: ({ children }) => <p style={{ marginBottom: 8, lineHeight: 1.65 }}>{renderTextWithIcons(children)}</p>,
  ul: ({ children }) => <ul style={{ paddingLeft: 18, marginBottom: 8 }}>{renderTextWithIcons(children)}</ul>,
  ol: ({ children }) => <ol style={{ paddingLeft: 18, marginBottom: 8 }}>{renderTextWithIcons(children)}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 3, lineHeight: 1.65 }}>{renderTextWithIcons(children)}</li>,
  blockquote: ({ children }) => (
    <blockquote style={{ borderLeft: `2px solid ${C.amberDim}`, paddingLeft: 12, marginLeft: 0, marginBottom: 8, color: C.textMuted }}>
      {renderTextWithIcons(children)}
    </blockquote>
  ),
  hr: () => <hr style={{ border: "none", borderTop: `1px solid ${C.border}`, margin: "14px 0" }} />,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" style={{ color: C.amber, textDecoration: "underline", textUnderlineOffset: 2 }}>
      {renderTextWithIcons(children)}
    </a>
  ),
  table: ({ children }) => (
    <div style={{ overflowX: "auto", marginBottom: 12, border: `1px solid ${C.border}` }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>{children}</table>
    </div>
  ),
  th: ({ children }) => <th style={{ padding: "6px 10px", background: C.surface3, borderBottom: `1px solid ${C.borderHi}`, textAlign: "left", fontWeight: 700, color: C.text }}>{renderTextWithIcons(children)}</th>,
  td: ({ children }) => <td style={{ padding: "6px 10px", borderBottom: `1px solid ${C.border}`, color: C.text }}>{renderTextWithIcons(children)}</td>,
  tr: ({ children }) => <tr>{renderTextWithIcons(children)}</tr>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: C.text }}>{renderTextWithIcons(children)}</strong>,
  em: ({ children }) => <em style={{ fontStyle: "italic", color: C.textMuted }}>{renderTextWithIcons(children)}</em>,
  del: ({ children }) => <del style={{ textDecoration: "line-through", color: C.textDim }}>{renderTextWithIcons(children)}</del>,
};

function MarkdownContent({ content }) {
  const trimmed = (content || "").trim();
  if (trimmed.startsWith("export default") || trimmed.startsWith("function App") || trimmed.startsWith("<div")) {
    return <LiveArtifact lang="jsx" code={trimmed} />;
  }
  return <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={MD_COMPONENTS}>{content}</ReactMarkdown>;
}

// ── Status line ───────────────────────────────────────────────────────────
function StatusBar({ state, voiceOn, onToggleVoice, mode }) {
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle;
  const spinning = state === "thinking" || state === "listening";
  const modeColor = mode === "think" ? C.amber : (mode === "fast" ? C.green : C.textMuted);

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "9px 14px", borderBottom: `1px solid ${C.border}`, background: C.surface, flexShrink: 0,
      fontFamily: C.mono,
    }}>
      <span style={{ fontSize: 13, fontWeight: 700, color: C.amber, letterSpacing: 0.5 }}>harvis</span>

      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {spinning
            ? <Loader2 size={11} color={cfg.color} style={{ animation: "spin 1s linear infinite" }} />
            : <span style={{ width: 6, height: 6, background: cfg.color, borderRadius: 1 }} />
          }
          <span style={{ fontSize: 11.5, color: cfg.color }}>{cfg.label}</span>
        </span>
        <span style={{ color: C.borderHi }}>·</span>
        <span style={{ fontSize: 11.5, color: modeColor }}>mode:{mode || "normal"}</span>
      </span>

      <button
        id="voice-toggle-btn"
        onClick={onToggleVoice}
        title={voiceOn ? "disable voice" : "enable voice"}
        style={{
          background: "transparent", border: `1px solid ${voiceOn ? C.amberDim : C.border}`, borderRadius: 2,
          padding: "3px 8px", display: "flex", alignItems: "center", gap: 5, cursor: "pointer",
          color: voiceOn ? C.amber : C.textMuted, fontSize: 11, fontFamily: C.mono,
        }}
      >
        {voiceOn ? <Volume2 size={12} /> : <VolumeX size={12} />}
        voice:{voiceOn ? "on" : "off"}
      </button>
    </div>
  );
}

// ── Thinking indicator ────────────────────────────────────────────────────
function ThinkingLine() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontFamily: C.mono, fontSize: 12.5, color: C.textMuted }}>
      <span style={{ color: C.amber }}>#</span>
      <span>thinking</span>
      <span className="blink-cursor" style={{ display: "inline-block", width: 7, height: 13, background: C.amber }} />
    </div>
  );
}

// ── Log row (was "Bubble") ────────────────────────────────────────────────
function ChatRow({ role, content, actions }) {
  const isUser = role === "user";
  const isSystem = role === "system";
  const label = isUser ? "> harold" : (isSystem ? "# mode" : "# harvis");
  const labelColor = isUser ? C.textMuted : C.amber;

  // Extract any attached image from user message
  let displayContent = content;
  let attachedImage = null;

  if (isUser && typeof content === "string") {
    const imgMatch = content.match(/\[IMAGE:\s*(data:image\/[a-zA-Z]+;base64,[a-zA-Z0-9+/=]+)\]/);
    if (imgMatch) {
      attachedImage = imgMatch[1];
      displayContent = content.replace(/\[IMAGE:\s*data:image\/[a-zA-Z]+;base64,[a-zA-Z0-9+/=]+\]/g, "").trim();
      if (!displayContent) displayContent = "Attached image";
    }
  }

  return (
    <div style={{
      marginBottom: 14, paddingLeft: 10,
      borderLeft: `2px solid ${isUser ? C.borderHi : (isSystem ? C.borderHi : C.amberDim)}`,
    }}>
      <div style={{ fontFamily: C.mono, fontSize: 11, color: labelColor, marginBottom: 4, letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{
        fontSize: isSystem ? 13 : 14, lineHeight: 1.6,
        color: isSystem ? C.textMuted : C.text,
        whiteSpace: isUser || isSystem ? "pre-wrap" : undefined,
        fontFamily: isSystem ? C.mono : undefined,
      }}>
        {isUser ? (
          <>
            {attachedImage && (
              <div style={{ marginBottom: 8 }}>
                <img
                  src={attachedImage}
                  alt="attached"
                  style={{ maxWidth: 220, maxHeight: 180, borderRadius: 2, border: `1px solid ${C.borderHi}`, display: "block" }}
                />
              </div>
            )}
            <div>{displayContent}</div>
          </>
        ) : isSystem ? (
          displayContent
        ) : (
          <>
            {actions && actions.length > 0 && <ActionTraceAccordion actions={actions} />}
            {content && <MarkdownContent content={content} />}
          </>
        )}
      </div>
    </div>
  );
}

// ── Chat area ─────────────────────────────────────────────────────────────
function ChatArea({ items, isThinking, onSelectPrompt }) {
  const bottomRef = useRef(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [items, isThinking]);

  const suggestions = [
    "audit journalctl errors and check system health with friday",
    "run deep diagnostics on byteai.service with edith",
    "build an interactive task manager react component",
    "show weather today in a react artifact",
  ];

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "18px 16px 8px", fontFamily: C.mono }}>
      {items.length === 0 && !isThinking && (
        <div style={{ color: C.textMuted, fontSize: 13, lineHeight: 1.8 }}>
          <p style={{ color: C.amber, marginBottom: 2 }}>harvis — voice/text control for this machine</p>
          <p style={{ marginBottom: 14 }}>commands friday (system doctor) and edith (deep operator). every action is logged in a trace block below the reply.</p>
          <p style={{ color: C.textDim, marginBottom: 6 }}>modes (capable omniroute pools only):</p>
          <p style={{ marginBottom: 2 }}><span style={{ color: C.amber }}>/normal</span> <span style={{ color: C.textDim }}>auto/smart:pro — quality, no extra thinking loop</span></p>
          <p style={{ marginBottom: 2 }}><span style={{ color: C.amber }}>/think</span> <span style={{ color: C.textDim }}>auto/reasoning:pro + reasoning_effort=high</span></p>
          <p style={{ marginBottom: 2 }}><span style={{ color: C.amber }}>/coding</span> <span style={{ color: C.textDim }}>auto/coding:pro</span></p>
          <p style={{ marginBottom: 10 }}><span style={{ color: C.amber }}>/vision</span> <span style={{ color: C.textDim }}>/fast and /cheap alias /normal · /mode</span></p>
          <p style={{ color: C.textDim, marginBottom: 6 }}>try:</p>
          {suggestions.map(s => (
            <div key={s} onClick={() => onSelectPrompt(s)} style={{ cursor: "pointer", marginBottom: 4 }}>
              <span style={{ color: C.textDim }}>{"> "}</span>
              <span style={{ color: C.text, textDecoration: "underline", textDecorationColor: C.border }}>{s}</span>
            </div>
          ))}
        </div>
      )}

      {items.map((item, i) => <ChatRow key={i} role={item.role} content={item.content} actions={item.actions} />)}

      {isThinking && <ThinkingLine />}

      <div ref={bottomRef} />
    </div>
  );
}

// ── Input ─────────────────────────────────────────────────────────────────
function InputBar({ onSubmit, onVoice, state }) {
  const [value, setValue] = useState("");
  const inputRef = useRef(null);
  const isListening = state === "listening";
  const isBusy = state === "thinking" || state === "speaking";

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text) return;
    onSubmit(text);
    setValue("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    inputRef.current?.focus();
  }, [value, onSubmit]);

  const onKey = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } };

  const handlePaste = useCallback(async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.indexOf('image') !== -1) {
        e.preventDefault();
        
        const file = item.getAsFile();
        if (file) {
          const reader = new FileReader();
          reader.onload = (event) => {
            const base64 = event.target.result.split(',')[1];
            const imageMarker = `\n\n[IMAGE: data:${file.type};base64,${base64}]`;
            setValue(prev => prev + imageMarker);
          };
          reader.readAsDataURL(file);
        }
        break;
      }
    }
  }, []);

  return (
    <div style={{ padding: "10px 12px 12px", borderTop: `1px solid ${C.border}`, background: C.surface, flexShrink: 0 }}>
      <div style={{
        display: "flex", alignItems: "flex-end", gap: 8,
        background: C.bg, border: `1px solid ${C.borderHi}`, borderRadius: 2, padding: "8px 8px 8px 12px",
      }}>
        <span style={{ fontFamily: C.mono, fontSize: 13, color: C.amber, paddingBottom: 2, userSelect: "none" }}>{">"}</span>
        <textarea
          ref={inputRef}
          id="chat-input"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={onKey}
          onPaste={handlePaste}
          placeholder={isBusy ? "harvis is running (type to queue follow-up)…" : "message harvis · /think /normal /coding · paste images"}
          disabled={false}
          rows={1}
          style={{
            flex: 1, resize: "none", border: "none", outline: "none", background: "transparent",
            color: C.text, fontSize: 13.5, lineHeight: 1.5, fontFamily: C.mono,
            minHeight: 20, maxHeight: 160, overflowY: "auto", caretColor: C.amber, opacity: 1,
          }}
          onInput={e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; }}
        />
        <button 
          onClick={() => document.getElementById('image-upload')?.click()} 
          title="upload image" 
          style={{
            width: 28, height: 28, borderRadius: 2, flexShrink: 0,
            border: `1px solid ${C.border}`,
            background: "transparent", display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "pointer", color: C.textMuted,
          }}
        >
          <ImageIcon size={14} />
        </button>
        <input 
          id="image-upload" 
          type="file" 
          accept="image/*" 
          style={{ display: 'none' }}
          onChange={(e) => {
            const file = e.target.files[0];
            if (file) {
              const reader = new FileReader();
              reader.onload = (event) => {
                const base64 = event.target.result.split(',')[1];
                const imageMarker = `\n\n[IMAGE: data:${file.type};base64,${base64}]`;
                setValue(prev => prev + imageMarker);
              };
              reader.readAsDataURL(file);
            }
          }}
        />
        <button id="mic-btn" onClick={onVoice} title={isListening ? "stop" : "voice input"} style={{
          width: 28, height: 28, borderRadius: 2, flexShrink: 0,
          border: `1px solid ${isListening ? C.amber : C.border}`,
          background: "transparent", display: "flex", alignItems: "center", justifyContent: "center",
          cursor: "pointer", color: isListening ? C.amber : C.textMuted,
        }}>{isListening ? <MicOff size={14} /> : <Mic size={14} />}</button>
        <button id="send-btn" onClick={submit} disabled={!value.trim()} style={{
          width: 28, height: 28, borderRadius: 2, flexShrink: 0, border: `1px solid ${value.trim() ? C.amber : C.border}`,
          background: "transparent", display: "flex", alignItems: "center", justifyContent: "center",
          cursor: value.trim() ? "pointer" : "default", color: value.trim() ? C.amber : C.textDim,
        }}><Send size={13} /></button>
      </div>
      <p style={{ fontSize: 10.5, color: C.textDim, marginTop: 6, fontFamily: C.mono }}>/think /normal /coding persist · /think q one-shot · enter to send · f9 to toggle</p>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────
export default function App() {
  const [timeline, setTimeline] = useState([]);
  const [state, setState] = useState("idle");
  const [voiceOn, setVoiceOn] = useState(true);
  const [chatMode, setChatMode] = useState("normal");
  const pendingActionsRef = useRef([]);

  useEffect(() => {
    function connect() {
      if (window.QWebChannel && window.qt?.webChannelTransport) {
        new window.QWebChannel(window.qt.webChannelTransport, ch => {
          window.backend = ch.objects.backend;
          window.backend.state_changed.connect(s => setState(s));
          if (window.backend.mode_changed) window.backend.mode_changed.connect(m => setChatMode(m));
          if (window.backend.get_mode) {
            try {
              const current = window.backend.get_mode((m) => { if (m) setChatMode(m); });
              if (typeof current === "string" && current) setChatMode(current);
            } catch (e) { /* QWebChannel return style varies */ }
          }

          window.backend.message_received.connect((role, rawContent) => {
            if (role === "user") {
              pendingActionsRef.current = [];
              setTimeline(prev => [...prev, { role: "user", content: rawContent }]);
            } else if (role === "system") {
              setTimeline(prev => [...prev, { role: "system", content: rawContent }]);
            } else if (role === "tool_call") {
              try {
                const data = JSON.parse(rawContent);
                pendingActionsRef.current.push({ tool_name: data.tool_name, tool_args: data.tool_args, status: "running", output: "" });
                setTimeline(prev => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last && last.role === "assistant" && last.isPending) {
                    last.actions = [...pendingActionsRef.current];
                    return [...copy];
                  }
                  return [...copy, { role: "assistant", isPending: true, actions: [...pendingActionsRef.current], content: "" }];
                });
              } catch (e) { console.error("tool_call parse error:", e); }
            } else if (role === "tool_result") {
              try {
                const data = JSON.parse(rawContent);
                const idx = pendingActionsRef.current.findIndex(a => a.tool_name === data.tool_name && a.status === "running");
                if (idx !== -1) {
                  pendingActionsRef.current[idx].status = data.status;
                  pendingActionsRef.current[idx].output = data.output;
                } else {
                  pendingActionsRef.current.push({ tool_name: data.tool_name, tool_args: data.tool_args, status: data.status, output: data.output });
                }
                setTimeline(prev => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last && last.role === "assistant" && last.isPending) { last.actions = [...pendingActionsRef.current]; return [...copy]; }
                  return copy;
                });
              } catch (e) { console.error("tool_result parse error:", e); }
            } else if (role === "assistant_chunk") {
              setTimeline(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.role === "assistant" && last.isPending) { last.content = (last.content || "") + rawContent; return [...copy]; }
                return [...copy, { role: "assistant", isPending: true, content: rawContent, actions: [...pendingActionsRef.current] }];
              });
            } else if (role === "assistant") {
              const currentActions = [...pendingActionsRef.current];
              pendingActionsRef.current = [];
              setTimeline(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.role === "assistant" && last.isPending) { last.isPending = false; last.content = rawContent; last.actions = currentActions; return [...copy]; }
                return [...copy, { role: "assistant", content: rawContent, actions: currentActions }];
              });
            }
          });
        });
      } else {
        setTimeout(connect, 100);
      }
    }
    connect();
  }, []);

  const handleSubmit = useCallback(text => {
    if (window.backend) {
      window.backend.submit_text(text);
      return;
    }

    const trimmed = text.trim();
    const m = trimmed.match(/^\/(think|fast|normal|mode|reasoning|quick|default|chat|coding|code|cheap|auto|vision|image|see|look)(?:\s+([\s\S]+))?$/i);
    if (m && !m[2]) {
      const cmd = m[1].toLowerCase();
      if (cmd === "mode") {
        setTimeline(prev => [...prev, { role: "system", content: `mode: ${chatMode}` }]);
        return;
      }
      const next = cmd === "think" || cmd === "reasoning" ? "think"
        : (cmd === "fast" || cmd === "quick" || cmd === "cheap" ? "normal"
        : (cmd === "coding" || cmd === "code" ? "coding"
        : (cmd === "vision" || cmd === "image" || cmd === "see" || cmd === "look" ? "vision" : "normal")));
      setChatMode(next);
      setTimeline(prev => [...prev, { role: "system", content: `mode → ${next}. all new messages use this chain until you switch.` }]);
      return;
    }

      pendingActionsRef.current = [
        { tool_name: "spawn_subagent", tool_args: { agent: "friday", task: "audit journalctl errors & system health" }, status: "success", output: "system metrics nominal. 0 journalctl errors." },
        { tool_name: "spawn_subagent", tool_args: { agent: "edith", task: "diagnose byteai.service" }, status: "success", output: "deep diagnostics complete. 0 active tracebacks." },
      ];
      setTimeline(prev => [
        ...prev,
        { role: "user", content: text },
        {
          role: "assistant",
          actions: [...pendingActionsRef.current],
          content: `\`\`\`jsx\nexport default function App() {\n  return (\n    <div style={{padding:24,background:'#0a0a0a',border:'1px solid #242424',color:'#d9d8d3',fontFamily:'monospace'}}>\n      <p style={{color:'#e8a33d',fontWeight:700,marginBottom:8}}>harvis // subagent report</p>\n      <p style={{fontSize:13,color:'#7a7a74'}}>friday + edith checks passed. all clear, harold.</p>\n    </div>\n  );\n}\n\`\`\``,
        },
      ]);
  }, [chatMode]);

  const handleVoice = useCallback(() => {
    if (window.backend) window.backend.voice_activate();
    else setState(s => s === "listening" ? "idle" : "listening");
  }, []);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        html, body, #root { width: 100%; height: 100%; background: ${C.bg}; font-family: ${C.mono}; color: ${C.text}; overflow: hidden; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
        .blink-cursor { animation: blink 1s step-end infinite; }
        textarea::placeholder { color: ${C.textDim}; }
        button:focus-visible { outline: 1px solid ${C.amber}; outline-offset: 1px; }
        .hljs { background: transparent !important; padding: 0 !important; }
        .hljs-keyword, .hljs-selector-tag { color: #c9a86a; }
        .hljs-string, .hljs-attr { color: #9fbf7f; }
        .hljs-number { color: #d99a6c; }
        .hljs-comment { color: #5a5a54; font-style: italic; }
        .hljs-function, .hljs-title { color: ${C.amber}; }
        .hljs-built_in { color: ${C.green}; }
        .hljs-type { color: #cba15a; }
      `}</style>

      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", background: C.bg }}>
        <StatusBar state={state} voiceOn={voiceOn} mode={chatMode} onToggleVoice={() => setVoiceOn(v => !v)} />
        <ChatArea items={timeline} isThinking={state === "thinking"} onSelectPrompt={handleSubmit} />
        <InputBar onSubmit={handleSubmit} onVoice={handleVoice} state={state} />
      </div>
    </>
  );
}
