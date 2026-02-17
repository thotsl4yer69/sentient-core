# Web Chat Interface - Visual Guide

## Interface Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ CORTANA                                                          │
│ SENTIENT NEURAL INTERFACE v1.0                                  │
│                                                                  │
│ NEURAL LINK: [●] ONLINE    EMOTION: [■] NEUTRAL    TIME: 14:23 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  [C] │ Hello! I'm Cortana.                      │               │
│      │ How can I assist you today?              │  14:15        │
│                                                                  │
│                          │ Can you check system status? │  [U]  │
│                          │                              │  14:16│
│                                                                  │
│  [C] │ System status: All nodes operational.    │               │
│      │ CPU: 23%, Memory: 45%, Temp: 58°C       │  14:16        │
│                                                                  │
│  [◆] CORTANA IS PROCESSING                                      │
│      // Analyzing sensor data...                                │
│      ▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░                                   │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────┐  0/2000    │
│ │ TRANSMIT MESSAGE TO CORTANA...                  │             │
│ │                                                 │             │
│ └─────────────────────────────────────────────────┘             │
│                                                                  │
│    [🎤] VOICE      [🔊] TTS      [▶] TRANSMIT                   │
└─────────────────────────────────────────────────────────────────┘
```

## Design Elements

### Color Palette

```css
Background: Pure Black #000000
Surface:    Near Black #0f0f0f
Primary:    Cyan       #00ffff (electric glow)
Secondary:  Magenta    #ff00ff
Accent:     Green      #00ff00
Warning:    Orange     #ffaa00
Danger:     Hot Pink   #ff0066
```

### Typography

- **Headers:** Orbitron (heavy geometric sans)
- **Body:** Share Tech Mono (technical monospace)
- **Size:** 14px base, scales to device

### Visual Effects

1. **Scanlines** - Horizontal lines that scroll, CRT monitor effect
2. **Vignette** - Dark edges, draws focus to center
3. **Glowing borders** - Cyan neon glow on active elements
4. **Clip-path shapes** - Geometric cut corners on message bubbles
5. **Flicker animation** - Subtle on logo (simulates unstable power)
6. **Pulse animations** - Status dots and emotion indicators

## Component Breakdown

### Header Bar

```
┌─────────────────────────────────────────────────────┐
│ CORTANA          SENTIENT NEURAL INTERFACE v1.0     │
│                                                      │
│ NEURAL LINK      EMOTION STATE      SYSTEM TIME     │
│ [●] ONLINE       [■] NEUTRAL        14:23:45        │
└─────────────────────────────────────────────────────┘
```

**Status Indicators:**
- **Connecting** - Yellow/orange pulsing dot
- **Online** - Green solid glow
- **Offline** - Red blinking dot

**Emotion Colors:**
- Neutral: Cyan
- Happy: Green
- Curious: Magenta
- Concerned: Orange
- Sad: Blue
- Angry: Red

### Message Bubbles

**User Messages (Right-aligned):**
```
                        ┌────────────────────┐
                        │ Hello Cortana!     │ [U]
                        │                    │ 14:23
                        └────────────────────┘
```
- Gray background
- Clipped top-right corner
- Right-aligned timestamp

**Cortana Messages (Left-aligned):**
```
[C] ┌────────────────────────────┐
    │ Hello! How can I help?     │
    │                            │ 14:23
    └────────────────────────────┘
```
- Near-black background
- Cyan glowing border
- Clipped bottom-left corner
- Pulsing avatar

### Thinking Indicator

```
┌─────────────────────────────────────────────┐
│ [◆] CORTANA IS PROCESSING                   │
│     // Analyzing system metrics...          │
│ ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░                  │
└─────────────────────────────────────────────┘
```

- Diamond icon pulses (brain shape)
- Shows current processing stage
- Animated progress bar flows left-to-right

### Input Area

```
┌──────────────────────────────────────────┐
│ TRANSMIT MESSAGE TO CORTANA...           │
│                                          │
└──────────────────────────────────────────┘
           [🎤]    [🔊]    [▶ TRANSMIT]
```

**Buttons:**
1. **Voice** - Microphone icon, turns red when recording
2. **TTS** - Speaker icon, glows green when enabled
3. **Transmit** - Large cyan button with arrow, primary action

### Responsive Breakpoints

- **Desktop (>768px):** Full status bar, all labels visible
- **Tablet (768px):** Status bar wraps, "TRANSMIT" text hidden
- **Mobile (<480px):** Vertical status bar, icon-only buttons

## Interaction Patterns

### 1. Sending Message

```
Type text → Press Enter OR Click Transmit
         ↓
Message appears immediately (right-aligned)
         ↓
Thinking indicator shows
         ↓
Cortana's response appears (left-aligned)
         ↓
Thinking indicator hides
```

### 2. Voice Input

```
Click microphone → Button turns red, recording starts
                ↓
Speak message → Audio captured
                ↓
Click again → Recording stops, processing notification
                ↓
STT converts → Message sent to Cortana
                ↓
Response received → Displays as text
```

### 3. Voice Output

```
Click speaker → Button glows green, TTS enabled
             ↓
Cortana responds → Text displayed AND spoken
                ↓
Audio plays → TTS status updates in real-time
```

## Animations

### Message Appearance
- Fade in from 0 to 100% opacity
- Slide up 10px
- Duration: 300ms ease-out

### Thinking Pulse
- Scale from 1.0 to 1.1
- Brightness from 1.0 to 1.5
- Duration: 1.5s infinite

### Border Flow
- Linear gradient moves left to right
- Duration: 3s infinite

### Scanline Scroll
- Vertical translation 4px
- Duration: 8s infinite linear

### Status Dot Pulse
- Scale 1.0 to 1.2
- Opacity 1.0 to 0.7
- Duration: 2s infinite

## Accessibility

- **Keyboard navigation:** Tab through inputs, Enter to send
- **Screen readers:** Semantic HTML with ARIA labels
- **Contrast:** WCAG AA compliant (cyan on black: 15.1:1)
- **Font scaling:** Relative units (rem) for text
- **Focus indicators:** Visible cyan outlines

## Performance

- **CSS animations only** - No JavaScript animations
- **Hardware acceleration** - transform/opacity properties
- **Lazy rendering** - Messages virtualized at 100+ count
- **WebSocket** - Minimal latency, no polling
- **Auto-scroll** - Smooth scroll to latest message

## Customization

### Change Color Scheme

Edit `static/styles.css`:

```css
:root {
    --color-primary: #ff00ff;  /* Change to magenta */
    --color-accent: #ffaa00;   /* Change to orange */
}
```

### Change Fonts

Replace in CSS:

```css
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@700&family=IBM+Plex+Mono&display=swap');

:root {
    --font-mono: 'IBM Plex Mono', monospace;
    --font-display: 'Rajdhani', sans-serif;
}
```

### Adjust Glow Intensity

```css
:root {
    --glow-primary: 0 0 30px var(--color-primary-glow);  /* Increase blur */
}
```

### Disable Effects

```css
/* Remove scanlines */
.scanlines { display: none; }

/* Remove vignette */
.vignette { display: none; }

/* Disable animations */
* { animation: none !important; }
```

## Browser Support

- **Chrome/Edge:** Full support
- **Firefox:** Full support
- **Safari:** Full support (iOS 14.5+)
- **Opera:** Full support

**Required APIs:**
- WebSocket
- MediaRecorder (for voice input)
- Web Audio API (for TTS)
- Flexbox/Grid layout
- CSS Custom Properties

## Easter Eggs

1. **Glitch effect** - Logo flickers at 42 seconds mark
2. **Matrix rain** - Hold Shift+Ctrl+M (not implemented yet)
3. **Theme toggle** - Click logo 5 times fast (not implemented yet)

---

**The interface is designed to feel ALIVE - not just functional, but visceral.**
