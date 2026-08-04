import React, { useState, useEffect, useRef } from "react";
import {
  Mic, Brain, AudioWaveform, Download, Volume2, Cloud, Loader2,
  CheckCircle2, AlertTriangle, XCircle, Music2, Play, Pause,
  SkipBack, SkipForward, Code2
} from "lucide-react";

// ---- Palette, lifted straight from the spec sheet ----
const COLORS = {
  idle:       "#7C5CFF",
  listening:  "#00D1FF",
  processing: "#3AA2FF",
  thinking:   "#7C5CFF",
  speaking:   "#39D98A",
  success:    "#39D98A",
  warning:    "#FFB347",
  error:      "#FF5C5C",
};

// ---- Size table (px), matching the spec's "Size: ~WxH" annotations ----
const SIZES = {
  idle:       { w: 180, h: 42 },
  activating: { w: 220, h: 44 },
  listening:  { w: 420, h: 68 },
  processing: { w: 420, h: 68 },
  thinking:   { w: 420, h: 68 },
  speaking:   { w: 520, h: 88 },
  text:       { w: 620, h: 96 },
  music:      { w: 620, h: 96 },
  download:   { w: 620, h: 96 },
  volume:     { w: 520, h: 80 },
  weather:    { w: 520, h: 80 },
  success:    { w: 340, h: 60 },
  warning:    { w: 340, h: 60 },
  error:      { w: 340, h: 60 },
  dismissing: { w: 220, h: 44 },
};

const STATE_GROUPS = [
  { label: "Idle / Lifecycle", items: ["idle", "activating", "dismissing"] },
  { label: "Active", items: ["listening", "processing", "thinking", "speaking"] },
  { label: "Expanded content", items: ["text", "music", "download", "volume", "weather"] },
  { label: "Completion", items: ["success", "warning", "error"] },
];

// ---- Real audio graph: random-walk per-bar targets shaped by a frequency-style
// envelope (more energy in the low/mid bars, tapering at the edges), smoothed
// frame to frame so it reads like an actual spectrum analyzer, not a sine toy.
function AudioGraph({ color, count = 28, energy = 1, speed = 1 }) {
  const [heights, setHeights] = useState(() => Array(count).fill(3));
  const valuesRef = useRef(Array(count).fill(3));
  const targetsRef = useRef(Array(count).fill(3));

  useEffect(() => {
    const id = setInterval(() => {
      for (let i = 0; i < count; i++) {
        if (Math.random() < 0.28 * speed) {
          const envelope = Math.sin((i / count) * Math.PI); // bell curve — quiet at edges
          const base = 4 + envelope * 26 * energy;
          targetsRef.current[i] = base * (0.3 + Math.random() * 0.9);
        }
        valuesRef.current[i] += (targetsRef.current[i] - valuesRef.current[i]) * 0.35;
      }
      setHeights([...valuesRef.current]);
    }, 55);
    return () => clearInterval(id);
  }, [count, energy, speed]);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, height: 32 }}>
      {heights.map((h, i) => (
        <div
          key={i}
          style={{
            width: 2.5,
            height: `${Math.max(h, 2)}px`,
            borderRadius: 2,
            background: `linear-gradient(180deg, ${color}, ${color}55)`,
            transition: "height 55ms linear",
          }}
        />
      ))}
    </div>
  );
}

// Indeterminate scan bar — for "working on it" states with no real percentage to show.
function ScanBar({ color, width = 140 }) {
  return (
    <div
      style={{
        width, height: 5, borderRadius: 3,
        background: "rgba(255,255,255,0.08)",
        overflow: "hidden", position: "relative",
      }}
    >
      <div
        className="scan-bar-fill"
        style={{
          position: "absolute", top: 0, bottom: 0, width: "36%", borderRadius: 3,
          background: `linear-gradient(90deg, transparent, ${color}, transparent)`,
        }}
      />
    </div>
  );
}

function Orb({ color, size = 20, pulse = true, icon = null, spin = false }) {
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    if (!pulse) return;
    const id = setInterval(() => setPhase((p) => p + 0.15), 60);
    return () => clearInterval(id);
  }, [pulse]);
  const r = size / 2 + (pulse ? Math.sin(phase) * 1.2 : 0);

  return (
    <div
      style={{
        position: "relative",
        width: size + 16,
        height: size + 16,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          position: "absolute",
          width: (size + 16),
          height: (size + 16),
          borderRadius: "50%",
          background: `radial-gradient(circle, ${color}55 0%, ${color}00 70%)`,
        }}
      />
      <div
        style={{
          width: r,
          height: r,
          borderRadius: "50%",
          background: `radial-gradient(circle at 35% 30%, ${color}ff 0%, ${color}dd 45%, ${color}88 100%)`,
          boxShadow: `0 0 12px ${color}aa`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div className={spin ? "spin-icon" : ""} style={{ display: "flex" }}>
          {icon}
        </div>
      </div>
    </div>
  );
}

function Pill({ size, color, children }) {
  return (
    <div
      style={{
        width: size.w,
        height: size.h,
        borderRadius: size.h / 2,
        background: "rgba(15, 15, 20, 0.72)",
        backdropFilter: "blur(18px)",
        WebkitBackdropFilter: "blur(18px)",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: `0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06), 0 0 24px ${color}22`,
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: 12,
        padding: "0 18px",
        transition: "width 420ms cubic-bezier(0.22, 1, 0.36, 1), height 420ms cubic-bezier(0.22, 1, 0.36, 1)",
        overflow: "hidden",
        color: "#e6edf3",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', sans-serif",
      }}
    >
      {children}
    </div>
  );
}

function Label({ children, sub }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <span style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap" }}>{children}</span>
      {sub && (
        <span style={{ fontSize: 11.5, color: "#8b949e", whiteSpace: "nowrap" }}>{sub}</span>
      )}
    </div>
  );
}

function ProgressBar({ pct, color, width = 140 }) {
  return (
    <div style={{ width, height: 5, borderRadius: 3, background: "rgba(255,255,255,0.1)" }}>
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          borderRadius: 3,
          background: color,
          transition: "width 500ms ease-out",
        }}
      />
    </div>
  );
}

function DotPulse({ color }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((v) => (v + 1) % 3), 350);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
      {[0, 1, 2].map((d) => (
        <div
          key={d}
          style={{
            width: 5, height: 5, borderRadius: "50%",
            background: color,
            opacity: i === d ? 1 : 0.3,
            transition: "opacity 200ms ease",
          }}
        />
      ))}
    </div>
  );
}

function StatePill({ view }) {
  const c = COLORS[view] || COLORS.idle;

  switch (view) {
    case "idle":
      return (
        <Pill size={SIZES.idle} color={c}>
          <Orb color={c} size={16} />
          <Label>BYTE AI</Label>
          <div style={{ marginLeft: "auto", width: 6, height: 6, borderRadius: "50%", background: c }} />
        </Pill>
      );

    case "activating":
      return (
        <Pill size={SIZES.activating} color={c}>
          <Orb color={c} size={18} />
          <Label>Activating...</Label>
          <DotPulse color={c} />
        </Pill>
      );

    case "dismissing":
      return (
        <Pill size={SIZES.dismissing} color={c}>
          <Orb color={c} size={16} pulse={false} />
          <Label>Closing...</Label>
        </Pill>
      );

    case "listening":
      return (
        <Pill size={SIZES.listening} color={c}>
          <Orb color={c} size={20} icon={<Mic size={11} color="#0a0a0c" />} />
          <Label>Listening...</Label>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={c} count={26} energy={1} speed={1.1} />
          </div>
        </Pill>
      );

    // Redesigned: no percentage — an indeterminate scan bar + spinning loader icon,
    // since "processing" here means indexing/working, not a measurable progress %.
    case "processing":
      return (
        <Pill size={SIZES.processing} color={c}>
          <Orb color={c} size={20} pulse={false} spin icon={<Loader2 size={13} color="#0a0a0c" />} />
          <Label sub="Working on it">Processing</Label>
          <div style={{ marginLeft: "auto" }}>
            <ScanBar color={c} width={140} />
          </div>
        </Pill>
      );

    // Redesigned: Brain icon + a calm, low-energy audio graph — representing an
    // internal "hum" rather than actual sound output.
    case "thinking":
      return (
        <Pill size={SIZES.thinking} color={c}>
          <Orb color={c} size={20} icon={<Brain size={12} color="#0a0a0c" />} />
          <Label>Thinking...</Label>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={c} count={22} energy={0.5} speed={0.55} />
          </div>
        </Pill>
      );

    // Redesigned: energetic, wider-range audio graph — this one represents real
    // TTS output, so it reads noticeably more "alive" than Thinking's hum.
    case "speaking":
      return (
        <Pill size={SIZES.speaking} color={c}>
          <Orb color={c} size={22} icon={<AudioWaveform size={12} color="#0a0a0c" />} />
          <Label>Speaking...</Label>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={c} count={34} energy={1} speed={1.3} />
          </div>
        </Pill>
      );

    case "text":
      return (
        <Pill size={SIZES.text} color={COLORS.speaking}>
          <Orb color={COLORS.speaking} size={22} icon={<Code2 size={12} color="#0a0a0c" />} />
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 13.5, lineHeight: 1.35 }}>I've opened Visual Studio Code.</span>
            <span style={{ fontSize: 13.5, lineHeight: 1.35, color: "#c9d1d9" }}>You can start coding now.</span>
          </div>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={COLORS.speaking} count={14} energy={0.6} speed={0.8} />
          </div>
        </Pill>
      );

    case "music":
      return (
        <Pill size={SIZES.music} color={"#3AA2FF"}>
          <div
            style={{
              width: 44, height: 44, borderRadius: 10, flexShrink: 0,
              background: "linear-gradient(135deg, #2b3a67, #0f1226)",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <Music2 size={18} color="#9aa9ff" />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 130 }}>
            <span style={{ fontSize: 13.5, fontWeight: 600 }}>Interstellar</span>
            <span style={{ fontSize: 11.5, color: "#8b949e" }}>Hans Zimmer</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, marginLeft: 8 }}>
            <ProgressBar pct={38} color="#3AA2FF" width="100%" />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "#8b949e" }}>
              <span>2:35</span><span>4:18</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, marginLeft: 8 }}>
            <SkipBack size={15} color="#c9d1d9" />
            <Pause size={16} color="white" />
            <SkipForward size={15} color="#c9d1d9" />
          </div>
        </Pill>
      );

    case "download":
      return (
        <Pill size={SIZES.download} color={"#3AA2FF"}>
          <Orb color="#3AA2FF" size={22} icon={<Download size={12} color="#0a0a0c" />} />
          <div style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1 }}>
            <span style={{ fontSize: 13.5, fontWeight: 600 }}>Downloading Ubuntu 24.04 ISO</span>
            <ProgressBar pct={72} color="#3AA2FF" width="100%" />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "#8b949e" }}>
              <span>2.1 GB / 2.9 GB · 8.4 MB/s</span><span>72%</span>
            </div>
          </div>
        </Pill>
      );

    case "volume":
      return (
        <Pill size={SIZES.volume} color={"#FFB347"}>
          <Orb color="#FFB347" size={22} icon={<Volume2 size={12} color="#0a0a0c" />} />
          <div style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1 }}>
            <span style={{ fontSize: 13.5, fontWeight: 600 }}>Volume</span>
            <ProgressBar pct={80} color="#FFB347" width="100%" />
          </div>
          <span style={{ fontSize: 13, color: "#8b949e" }}>80%</span>
        </Pill>
      );

    case "weather":
      return (
        <Pill size={SIZES.weather} color={"#8b949e"}>
          <Cloud size={26} color="#c9d1d9" />
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 18, fontWeight: 700 }}>24°C</span>
            <span style={{ fontSize: 11.5, color: "#8b949e" }}>Partly Cloudy</span>
          </div>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#8b949e" }}>Abidjan, CI</span>
        </Pill>
      );

    case "success":
      return (
        <Pill size={SIZES.success} color={c}>
          <CheckCircle2 size={20} color={c} />
          <Label sub="Everything is ready!">Task completed</Label>
        </Pill>
      );

    case "warning":
      return (
        <Pill size={SIZES.warning} color={c}>
          <AlertTriangle size={20} color={c} />
          <Label sub="Permission denied.">Couldn't complete the task</Label>
        </Pill>
      );

    case "error":
      return (
        <Pill size={SIZES.error} color={c}>
          <XCircle size={20} color={c} />
          <Label sub="Please try again.">Something went wrong</Label>
        </Pill>
      );

    default:
      return null;
  }
}

export default function ByteAIIsland() {
  const [view, setView] = useState("idle");

  return (
    <div
      style={{
        minHeight: 620,
        width: "100%",
        background:
          "radial-gradient(ellipse at top, #14141c 0%, #0a0a0e 60%)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        paddingTop: 48,
        paddingBottom: 32,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}
    >
      <style>{`
        @keyframes scanMove {
          0% { left: -36%; }
          100% { left: 100%; }
        }
        .scan-bar-fill {
          animation: scanMove 1.3s ease-in-out infinite;
        }
        @keyframes spinIcon {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .spin-icon {
          animation: spinIcon 900ms linear infinite;
        }
      `}</style>

      <div style={{ height: 100, display: "flex", alignItems: "flex-start", justifyContent: "center" }}>
        <StatePill view={view} />
      </div>

      <div style={{ marginTop: 64, width: "100%", maxWidth: 640, padding: "0 24px" }}>
        {STATE_GROUPS.map((group) => (
          <div key={group.label} style={{ marginBottom: 18 }}>
            <div
              style={{
                fontSize: 11,
                letterSpacing: "0.08em",
                color: "#565f69",
                marginBottom: 8,
                textTransform: "uppercase",
              }}
            >
              {group.label}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {group.items.map((item) => (
                <button
                  key={item}
                  onClick={() => setView(item)}
                  style={{
                    padding: "7px 14px",
                    borderRadius: 8,
                    fontSize: 12.5,
                    fontWeight: 500,
                    cursor: "pointer",
                    border:
                      view === item
                        ? `1px solid ${COLORS[item] || "#7C5CFF"}`
                        : "1px solid rgba(255,255,255,0.08)",
                    background:
                      view === item
                        ? `${(COLORS[item] || "#7C5CFF")}22`
                        : "rgba(255,255,255,0.03)",
                    color: view === item ? "#fff" : "#8b949e",
                    textTransform: "capitalize",
                    transition: "all 150ms ease",
                  }}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
