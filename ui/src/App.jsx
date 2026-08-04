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
  idle:       { w: 96, h: 32 },
  activating: { w: 220, h: 44 },
  listening:  { w: 320, h: 88 },
  processing: { w: 420, h: 68 },
  thinking:   { w: 180, h: 48 },
  speaking:   { w: 420, h: 128 },
  text:       { w: 620, h: 96 },
  music:      { w: 620, h: 96 },
  download:   { w: 620, h: 96 },
  volume:     { w: 520, h: 80 },
  weather:    { w: 520, h: 80 },
  success:    { w: 340, h: 60 },
  warning:    { w: 340, h: 60 },
  error:      { w: 420, h: 88 },
  dismissing: { w: 220, h: 44 },
};

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

function Pill({ size, color, children, onClick }) {
  return (
    <div
      id="island-pill"
      onClick={onClick}
      style={{
        width: "100%",
        height: "100%",
        borderRadius: size.h / 2,
        background: "rgba(18, 18, 22, 0.72)",
        backdropFilter: "blur(18px)",
        WebkitBackdropFilter: "blur(18px)",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: `0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06), 0 0 24px ${color}22`,
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: 12,
        padding: "0 18px",
        overflow: "hidden",
        color: "#e6edf3",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', sans-serif",
        cursor: "pointer",
        position: "relative"
      }}
    >
      {/* Subtle top-edge highlight to fake a glass catch-light */}
      <div style={{
          position: "absolute",
          top: 0, left: 16, right: 16, height: 1,
          background: "rgba(255, 255, 255, 0.15)"
      }} />
      {children}
    </div>
  );
}

function Label({ children, sub }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0, justifyContent: "center" }}>
      <span style={{ fontSize: 14, fontWeight: 600, whiteSpace: "nowrap" }}>{children}</span>
      {sub && (
        <span style={{ fontSize: 11.5, color: "#8b949e", whiteSpace: "nowrap" }}>{sub}</span>
      )}
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

function StatePill({ view, text, onClick }) {
  const c = COLORS[view] || COLORS.idle;

  switch (view) {
    case "idle":
      return (
        <Pill size={SIZES.idle} color={c} onClick={onClick}>
          <Orb color={c} size={16} />
          <div style={{ marginLeft: "auto", width: 6, height: 6, borderRadius: "50%", background: c }} />
        </Pill>
      );

    case "activating":
      return (
        <Pill size={SIZES.activating} color={c} onClick={onClick}>
          <Orb color={c} size={18} />
          <Label>Activating...</Label>
          <DotPulse color={c} />
        </Pill>
      );

    case "dismissing":
      return (
        <Pill size={SIZES.dismissing} color={c} onClick={onClick}>
          <Orb color={c} size={16} pulse={false} />
          <Label>Closing...</Label>
        </Pill>
      );

    case "listening":
      return (
        <Pill size={SIZES.listening} color={c} onClick={onClick}>
          <Orb color={c} size={20} icon={<Mic size={11} color="#0a0a0c" />} />
          <Label>Listening...</Label>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={c} count={26} energy={1} speed={1.1} />
          </div>
        </Pill>
      );

    case "processing":
      return (
        <Pill size={SIZES.processing} color={c} onClick={onClick}>
          <Orb color={c} size={20} pulse={false} spin icon={<Loader2 size={13} color="#0a0a0c" />} />
          <Label sub="Working on it">Processing</Label>
          <div style={{ marginLeft: "auto" }}>
            <ScanBar color={c} width={140} />
          </div>
        </Pill>
      );

    case "thinking":
      return (
        <Pill size={SIZES.thinking} color={c} onClick={onClick}>
          <Orb color={c} size={20} icon={<Brain size={12} color="#0a0a0c" />} />
          <Label>Processing...</Label>
          <div style={{ marginLeft: "auto" }}>
            <AudioGraph color={c} count={22} energy={0.5} speed={0.55} />
          </div>
        </Pill>
      );

    case "speaking":
      return (
        <Pill size={SIZES.speaking} color={c} onClick={onClick}>
          <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', padding: '4px 0' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <Orb color={c} size={22} icon={<AudioWaveform size={12} color="#0a0a0c" />} />
              <Label>BYTE AI</Label>
            </div>
            <div style={{ display: "flex", justifyContent: "center", alignItems: "center", flex: 1, padding: "0 10px" }}>
              <span style={{ fontSize: 13.5, lineHeight: 1.35, textAlign: "center", color: "#e6edf3" }}>
                {text || "..."}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <AudioGraph color={c} count={34} energy={1} speed={1.3} />
            </div>
          </div>
        </Pill>
      );

    case "success":
      return (
        <Pill size={SIZES.success} color={c} onClick={onClick}>
          <CheckCircle2 size={20} color={c} />
          <Label sub="Everything is ready!">Task completed</Label>
        </Pill>
      );

    case "error":
      return (
        <Pill size={SIZES.error} color={c} onClick={onClick}>
          <XCircle size={20} color={c} />
          <Label sub="Please try again.">{text || "Something went wrong"}</Label>
        </Pill>
      );

    default:
      return null;
  }
}

export default function ByteAIIsland() {
  const [view, setView] = useState("idle");
  const [text, setText] = useState("");

  useEffect(() => {
    document.body.style.backgroundColor = "transparent";
    document.documentElement.style.backgroundColor = "transparent";
    
    // Check if QWebChannel is injected
    if (window.QWebChannel && window.qt && window.qt.webChannelTransport) {
      new window.QWebChannel(window.qt.webChannelTransport, function(channel) {
        window.backend = channel.objects.backend;
        
        // Connect signals from Python
        window.backend.state_changed.connect(function(state) {
          setView(state);
        });
        
        window.backend.text_updated.connect(function(msg) {
          setText(msg);
        });

        function sendMask() {
          const el = document.getElementById("island-pill");
          if (el && window.backend) {
            const rect = el.getBoundingClientRect();
            // Math.ceil/floor to ensure we perfectly envelop the pill
            window.backend.update_mask(
              Math.floor(rect.x), 
              Math.floor(rect.y), 
              Math.ceil(rect.width), 
              Math.ceil(rect.height)
            );
          }
          requestAnimationFrame(sendMask);
        }
        requestAnimationFrame(sendMask);
      });
    }
  }, []);

  const handleIslandClick = () => {
    if (window.backend) {
      window.backend.island_clicked();
    } else {
        // Fallback for browser testing
        console.log("Island clicked");
        setView(v => v === "idle" ? "listening" : "idle");
    }
  };

  // 14px top margin when active, tuck away when idle so only 10px peeks
  const idle_peek = 10;
  const current_h = SIZES[view] ? SIZES[view].h : SIZES.idle.h;
  const target_y = view === "idle" ? -(current_h - idle_peek) : 14;

  return (
    <div
      style={{
        height: "100vh",
        width: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        overflow: "hidden", // Important so the tucked away part doesn't cause scroll
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
      }}
    >
      <style>{`
        * {
          box-sizing: border-box;
        }
        body, html {
          margin: 0;
          padding: 0;
          background: transparent !important;
          overflow: hidden;
        }
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

    <div style={{
      width: "100%",
      height: "100%",
      display: "flex",
      alignItems: "center",
      justifyContent: "center"
    }}>
      <div 
        style={{ 
          width: "100%",
          height: "100%",
          display: "flex", 
          justifyContent: "center",
        }}
      >
        <StatePill view={view} text={text} onClick={handleIslandClick} />
      </div>
    </div>
    </div>
  );
}
