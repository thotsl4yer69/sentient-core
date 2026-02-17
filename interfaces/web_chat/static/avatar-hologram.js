/**
 * SENTIENT CORE - HOLOGRAPHIC AVATAR VISUALIZATION
 * Pure Canvas2D procedural hologram for AI assistant
 * Dark cyberpunk aesthetic - lightweight replacement for 3D GLB model
 */

(function() {
    'use strict';

    // ============================================================================
    // CONFIGURATION & CONSTANTS
    // ============================================================================

    const TARGET_FPS = 30;
    const FRAME_TIME = 1000 / TARGET_FPS;

    const MOOD_COLORS = {
        neutral:    { r: 0, g: 255, b: 255 },      // cyan
        joy:        { r: 255, g: 215, b: 0 },      // gold
        curiosity:  { r: 155, g: 89, b: 255 },     // purple
        affection:  { r: 255, g: 105, b: 180 },    // pink
        sadness:    { r: 65, g: 105, b: 225 },     // royal blue
        anger:      { r: 255, g: 68, b: 68 },      // red
        fear:       { r: 255, g: 140, b: 0 },      // orange
        surprise:   { r: 0, g: 206, b: 209 },      // teal
        confidence: { r: 0, g: 255, b: 136 },      // green
        playful:    { r: 255, g: 0, b: 255 },      // magenta
        happy:      { r: 255, g: 215, b: 0 },      // gold (alias)
        pleased:    { r: 255, g: 215, b: 0 },      // gold (alias)
        thinking:   { r: 155, g: 89, b: 255 },     // purple (alias)
        concerned:  { r: 65, g: 105, b: 225 },     // blue (alias)
        calm:       { r: 0, g: 255, b: 255 },      // cyan (alias)
        excited:    { r: 255, g: 215, b: 0 },      // gold (alias)
    };

    // ============================================================================
    // AVATAR RENDERER CLASS
    // ============================================================================

    class HolographicAvatar {
        constructor(canvas) {
            this.canvas = canvas;
            this.ctx = canvas.getContext('2d', { alpha: true });

            // State
            this.currentEmotion = 'neutral';
            this.startColor = { ...MOOD_COLORS.neutral };
            this.currentColor = { ...MOOD_COLORS.neutral };
            this.targetColor = { ...MOOD_COLORS.neutral };
            this.colorTransition = 0;
            this.colorTransitionDuration = 60; // frames

            this.isSpeaking = false;
            this.isThinking = false;
            this.attentionState = 'relaxed'; // 'focused', 'listening', 'alert', 'relaxed'
            this._speakTimeout = null;

            // Animation time
            this.time = 0;
            this.lastFrameTime = 0;
            this.fps = 0;
            this.fpsFrames = 0;
            this.fpsLastTime = 0;

            // Mouse tracking for iris
            this.mouseX = 0;
            this.mouseY = 0;
            this.irisX = 0;
            this.irisY = 0;
            this.lastMouseMoveTime = 0;
            this.mouseInactive = false;

            // Center point (calculated on resize)
            this.centerX = 0;
            this.centerY = 0;

            // Animation frame handle
            this.animationFrame = null;

            // Waveform animation
            this.waveformOpacity = 0;
            this.waveformBars = [];
            for (let i = 0; i < 48; i++) {
                this.waveformBars.push({
                    angle: (i / 48) * Math.PI * 2,
                    height: 0,
                    targetHeight: 0,
                    phase: Math.random() * Math.PI * 2
                });
            }

            // Particles
            this.particles = [];
            for (let i = 0; i < 30; i++) {
                this.particles.push({
                    angle: Math.random() * Math.PI * 2,
                    radius: 50 + Math.random() * 90,
                    speed: 0.1 + Math.random() * 0.4,
                    size: 1.5 + Math.random() * 1.5,
                    opacity: 0.3 + Math.random() * 0.5,
                    baseOpacity: 0.3 + Math.random() * 0.5,
                    twinklePhase: Math.random() * Math.PI * 2,
                    radialDrift: 0.05 + Math.random() * 0.1,
                    driftDirection: Math.random() > 0.5 ? 1 : -1
                });
            }

            // Orbital rings
            this.rings = [
                { radiusX: 90, radiusY: 70, rotation: 0, speed: Math.PI * 2 / (18 * 60), tilt: 25, strokeWidth: 0.5, dashed: false, dots: this._createRingDots(3) },
                { radiusX: 70, radiusY: 55, rotation: 0, speed: Math.PI * 2 / (12 * 60), tilt: -15, strokeWidth: 0.5, dashed: true, dots: this._createRingDots(4) },
                { radiusX: 110, radiusY: 85, rotation: 0, speed: Math.PI * 2 / (25 * 60), tilt: 40, strokeWidth: 0.3, dashed: false, dots: this._createRingDots(5) }
            ];

            // Scan line
            this.scanLineY = 0;
            this.scanLineSpeed = 1 / (5 * 60); // Full sweep in 5 seconds at 60fps

            // Data streams
            this.dataStreams = [
                { x: 0.15, speed: 1.2, chars: this._generateDataChars(12), offset: 0 },
                { x: 0.85, speed: 0.8, chars: this._generateDataChars(15), offset: 0 },
                { x: 0.92, speed: 1.5, chars: this._generateDataChars(10), offset: 0 }
            ];

            // Loading ring (for thinking state)
            this.loadingRingAngle = 0;
            this.loadingRingOpacity = 0;

            // Core pulse
            this.coreRadius = 50;
            this.corePulsePhase = 0;

            // --- Micro-expression state ---

            // Blinking
            this.blinkTimer = 0;
            this.blinkInterval = this._randomBlinkInterval(); // frames until next blink
            this.isBlinking = false;
            this.blinkFrame = 0;
            this.blinkDuration = Math.round(150 / FRAME_TIME); // ~5 frames at 30fps

            // Micro-drift (idle head movement)
            this.microDriftX = 0;
            this.microDriftY = 0;
            this._driftTargetX = 0;
            this._driftTargetY = 0;
            this._driftTimer = 0;
            this._driftInterval = this._randomDriftInterval();

            // Processing state (token streaming)
            this.currentState = 'idle'; // 'idle' | 'processing'
            this._processingRampFrames = 0;
            this._processingRampDuration = Math.round(2000 / FRAME_TIME); // 2 seconds
            this._ringSpeedMultiplier = 1.0; // lerps toward target
            this._ringSpeedTarget = 1.0;
            this._particleSpeedBoost = 1.0;

            // Token pulse (brief brightness flash on core)
            this._tokenPulseAlpha = 0; // extra alpha on core, decays
            this._tokenPulseDuration = Math.round(50 / FRAME_TIME); // ~2 frames
            this._tokenPulseDecay = 0.2 / (this._tokenPulseDuration || 1);

            // Confusion wobble
            this._confusionWobble = 0;   // current wobble angle offset (degrees)
            this._confusionPhase = 0;    // drives oscillation

            // Excitement expansion
            this._excitementScale = 1.0; // multiplier on coreRadius and particle orbit

            this.setupCanvas();
            this.setupEventListeners();
            this.start();
        }

        _createRingDots(count) {
            const dots = [];
            for (let i = 0; i < count; i++) {
                dots.push({
                    angle: (i / count) * Math.PI * 2 + Math.random() * 0.5,
                    speed: 0.3 + Math.random() * 0.4
                });
            }
            return dots;
        }

        _generateDataChars(count) {
            const chars = '0123456789ABCDEF';
            const result = [];
            for (let i = 0; i < count; i++) {
                result.push(chars.charAt(Math.floor(Math.random() * chars.length)));
            }
            return result;
        }

        // Random blink interval: 3-5 seconds in frames
        _randomBlinkInterval() {
            return Math.round((3000 + Math.random() * 2000) / FRAME_TIME);
        }

        // Random micro-drift interval: 2-3 seconds in frames
        _randomDriftInterval() {
            return Math.round((2000 + Math.random() * 1000) / FRAME_TIME);
        }

        setupCanvas() {
            const rect = this.canvas.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;

            this.canvas.width = rect.width * dpr;
            this.canvas.height = rect.height * dpr;

            this.canvas.style.width = rect.width + 'px';
            this.canvas.style.height = rect.height + 'px';

            this.ctx.scale(dpr, dpr);

            this.centerX = rect.width / 2;
            this.centerY = rect.height / 2;

            // Initialize iris at center
            this.irisX = this.centerX;
            this.irisY = this.centerY;
        }

        setupEventListeners() {
            this.boundMouseMove = this.onMouseMove.bind(this);
            this.boundResize = this.onResize.bind(this);

            this.canvas.addEventListener('mousemove', this.boundMouseMove);
            window.addEventListener('resize', this.boundResize);
        }

        onMouseMove(e) {
            const rect = this.canvas.getBoundingClientRect();
            this.mouseX = e.clientX - rect.left;
            this.mouseY = e.clientY - rect.top;
            this.lastMouseMoveTime = this.time;
            this.mouseInactive = false;
        }

        onResize() {
            this.setupCanvas();
        }

        start() {
            this.lastFrameTime = performance.now();
            this.fpsLastTime = this.lastFrameTime;
            this.animate(this.lastFrameTime);
        }

        animate(timestamp) {
            // FPS throttling
            const elapsed = timestamp - this.lastFrameTime;

            if (elapsed >= FRAME_TIME) {
                this.lastFrameTime = timestamp;

                // FPS calculation
                this.fpsFrames++;
                if (timestamp - this.fpsLastTime >= 1000) {
                    this.fps = Math.round((this.fpsFrames * 1000) / (timestamp - this.fpsLastTime));
                    this.fpsFrames = 0;
                    this.fpsLastTime = timestamp;
                }

                this.update();
                this.render();
            }

            this.animationFrame = requestAnimationFrame(this.animate.bind(this));
        }

        update() {
            this.time += 1;

            // Color transition (interpolate from startColor to targetColor)
            if (this.colorTransition < this.colorTransitionDuration) {
                this.colorTransition++;
                const t = this.colorTransition / this.colorTransitionDuration;
                const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; // ease in-out quad

                this.currentColor.r = this.startColor.r + (this.targetColor.r - this.startColor.r) * ease;
                this.currentColor.g = this.startColor.g + (this.targetColor.g - this.startColor.g) * ease;
                this.currentColor.b = this.startColor.b + (this.targetColor.b - this.startColor.b) * ease;
            }

            // Thinking state from emotion
            this.isThinking = (this.currentEmotion === 'thinking' || this.currentEmotion === 'analyzing' || this.currentEmotion === 'focused');

            // --- BLINK ---
            if (!this.isBlinking) {
                this.blinkTimer++;
                if (this.blinkTimer >= this.blinkInterval) {
                    this.isBlinking = true;
                    this.blinkFrame = 0;
                    this.blinkTimer = 0;
                    this.blinkInterval = this._randomBlinkInterval();
                }
            } else {
                this.blinkFrame++;
                if (this.blinkFrame >= this.blinkDuration) {
                    this.isBlinking = false;
                    this.blinkFrame = 0;
                }
            }

            // --- MICRO DRIFT ---
            this._driftTimer++;
            if (this._driftTimer >= this._driftInterval) {
                this._driftTargetX = (Math.random() - 0.5) * 4; // ±2px
                this._driftTargetY = (Math.random() - 0.5) * 4;
                this._driftTimer = 0;
                this._driftInterval = this._randomDriftInterval();
            }
            this.microDriftX = this.lerp(this.microDriftX, this._driftTargetX, 0.02);
            this.microDriftY = this.lerp(this.microDriftY, this._driftTargetY, 0.02);

            // --- PROCESSING STATE ring speed ramp ---
            if (this.currentState === 'processing') {
                this._ringSpeedTarget = 2.5;
                this._particleSpeedBoost = this.lerp(this._particleSpeedBoost, 2.0, 0.02);
            } else {
                this._ringSpeedTarget = 1.0;
                this._particleSpeedBoost = this.lerp(this._particleSpeedBoost, 1.0, 0.04);
            }
            this._ringSpeedMultiplier = this.lerp(this._ringSpeedMultiplier, this._ringSpeedTarget, 0.02);

            // --- TOKEN PULSE decay ---
            if (this._tokenPulseAlpha > 0) {
                this._tokenPulseAlpha = Math.max(0, this._tokenPulseAlpha - 0.12);
            }

            // --- CONFUSION WOBBLE ---
            if (this.currentEmotion === 'confused') {
                this._confusionPhase += Math.PI * 2 / (0.5 * 30); // 0.5s period at 30fps
                this._confusionWobble = Math.sin(this._confusionPhase) * 3; // ±3 degrees
            } else {
                this._confusionWobble = this.lerp(this._confusionWobble, 0, 0.1);
                this._confusionPhase = 0;
            }

            // --- EXCITEMENT SCALE ---
            if (this.currentEmotion === 'excited' || this.currentEmotion === 'joy' || this.currentEmotion === 'happy' || this.currentEmotion === 'pleased') {
                this._excitementScale = this.lerp(this._excitementScale, 1.15, 0.04);
                this._ringSpeedTarget = Math.max(this._ringSpeedTarget, 1.5);
            } else {
                this._excitementScale = this.lerp(this._excitementScale, 1.0, 0.04);
            }

            // Iris tracking
            if (this.time - this.lastMouseMoveTime > 60) { // 2 seconds at 30fps
                this.mouseInactive = true;
            }

            if (this.mouseInactive) {
                // Drift back to center
                this.irisX = this.lerp(this.irisX, this.centerX, 0.02);
                this.irisY = this.lerp(this.irisY, this.centerY, 0.02);
            } else {
                // Track mouse
                const targetX = this.centerX + (this.mouseX - this.centerX) * 0.15;
                const targetY = this.centerY + (this.mouseY - this.centerY) * 0.15;

                // Constrain to core orb
                const dx = targetX - this.centerX;
                const dy = targetY - this.centerY;
                const dist = Math.sqrt(dx * dx + dy * dy);
                const maxDist = this.coreRadius - 10;

                if (dist > maxDist) {
                    const angle = Math.atan2(dy, dx);
                    this.irisX = this.lerp(this.irisX, this.centerX + Math.cos(angle) * maxDist, 0.06);
                    this.irisY = this.lerp(this.irisY, this.centerY + Math.sin(angle) * maxDist, 0.06);
                } else {
                    this.irisX = this.lerp(this.irisX, targetX, 0.06);
                    this.irisY = this.lerp(this.irisY, targetY, 0.06);
                }
            }

            // Waveform fade
            if (this.isSpeaking) {
                this.waveformOpacity = Math.min(1, this.waveformOpacity + 0.1);
            } else {
                this.waveformOpacity = Math.max(0, this.waveformOpacity - 0.05);
            }

            // Waveform bars
            for (let i = 0; i < this.waveformBars.length; i++) {
                const bar = this.waveformBars[i];
                if (this.isSpeaking) {
                    // Multiple sine waves for pseudo-audio
                    const wave1 = Math.sin(this.time * 0.1 + bar.phase) * 0.4;
                    const wave2 = Math.sin(this.time * 0.15 + i * 0.2) * 0.3;
                    const wave3 = Math.sin(this.time * 0.08 + i * 0.5) * 0.3;
                    bar.targetHeight = 3 + (wave1 + wave2 + wave3 + 1) * 8.5;
                } else {
                    bar.targetHeight = 3;
                }
                bar.height = this.lerp(bar.height, bar.targetHeight, 0.2);
            }

            // Particles
            const speedMultiplier = this.isSpeaking ? 1.5 : 1.0;
            const clusterFactor = this.isThinking ? 0.95 : 1.0;
            // Excitement pushes particles outward; excitement scale also expands orbit bounds
            const excitedOrbitMax = Math.round(140 * this._excitementScale);

            for (const particle of this.particles) {
                particle.angle += particle.speed * 0.01 * speedMultiplier * this._particleSpeedBoost;
                particle.radius += particle.radialDrift * particle.driftDirection * 0.1;

                // Bounce radius (excitement expands outer bound)
                if (particle.radius < 50) {
                    particle.driftDirection = 1;
                } else if (particle.radius > excitedOrbitMax) {
                    particle.driftDirection = -1;
                }

                // Excitement nudges particles outward
                if (this._excitementScale > 1.01) {
                    particle.radius = this.lerp(particle.radius, particle.radius * this._excitementScale * 0.01 + particle.radius * 0.99, 0.02);
                }

                // Cluster effect when thinking
                if (this.isThinking) {
                    particle.radius *= clusterFactor;
                }

                // Twinkle
                particle.twinklePhase += 0.05;
                particle.opacity = particle.baseOpacity + Math.sin(particle.twinklePhase) * 0.3;
            }

            // Rings (apply speed multiplier)
            for (const ring of this.rings) {
                ring.rotation += ring.speed * this._ringSpeedMultiplier;
                for (const dot of ring.dots) {
                    dot.angle += dot.speed * 0.01 * this._ringSpeedMultiplier;
                }
            }

            // Scan line
            this.scanLineY += this.scanLineSpeed;
            if (this.scanLineY > 1) {
                this.scanLineY = 0;
            }

            // Data streams
            for (const stream of this.dataStreams) {
                stream.offset += stream.speed * 0.5;
                if (stream.offset > stream.chars.length * 12) {
                    stream.offset = 0;
                }
            }

            // Loading ring (thinking state)
            if (this.isThinking) {
                this.loadingRingAngle += Math.PI * 2 / 60; // One rotation per 2 seconds at 30fps
                this.loadingRingOpacity = Math.min(0.8, this.loadingRingOpacity + 0.05);
            } else {
                this.loadingRingOpacity = Math.max(0, this.loadingRingOpacity - 0.05);
            }
        }

        render() {
            const ctx = this.ctx;
            const w = this.canvas.width / (window.devicePixelRatio || 1);
            const h = this.canvas.height / (window.devicePixelRatio || 1);

            // Clear with transparency
            ctx.clearRect(0, 0, w, h);

            // Hexagonal grid background
            this.renderHexGrid(ctx, w, h);

            // Data streams
            this.renderDataStreams(ctx, w, h);

            // Particles
            this.renderParticles(ctx);

            // Orbital rings
            this.renderRings(ctx);

            // Waveform ring
            if (this.waveformOpacity > 0) {
                this.renderWaveform(ctx);
            }

            // Central core orb
            this.renderCore(ctx);

            // Iris/Eye
            this.renderIris(ctx);

            // Loading ring (thinking)
            if (this.loadingRingOpacity > 0) {
                this.renderLoadingRing(ctx);
            }

            // Scan line
            this.renderScanLine(ctx, w, h);

            // Status label
            this.renderStatusLabel(ctx);
        }

        renderHexGrid(ctx, w, h) {
            const hexSize = 25;
            const cols = Math.ceil(w / (hexSize * 1.5)) + 2;
            const rows = Math.ceil(h / (hexSize * Math.sqrt(3))) + 2;

            ctx.strokeStyle = this.rgba(this.currentColor, 0.04);
            ctx.lineWidth = 0.5;

            for (let row = 0; row < rows; row++) {
                for (let col = 0; col < cols; col++) {
                    const x = col * hexSize * 1.5;
                    const y = row * hexSize * Math.sqrt(3) + (col % 2) * hexSize * Math.sqrt(3) / 2;
                    this.drawHexagon(ctx, x, y, hexSize);
                }
            }
        }

        drawHexagon(ctx, x, y, size) {
            ctx.beginPath();
            for (let i = 0; i < 6; i++) {
                const angle = Math.PI / 3 * i;
                const hx = x + size * Math.cos(angle);
                const hy = y + size * Math.sin(angle);
                if (i === 0) {
                    ctx.moveTo(hx, hy);
                } else {
                    ctx.lineTo(hx, hy);
                }
            }
            ctx.closePath();
            ctx.stroke();
        }

        renderCore(ctx) {
            // Processing state: faster pulse period
            const pulsePeriod = (this.currentState === 'processing') ? 30 : 60; // frames per cycle
            this.corePulsePhase += Math.PI * 2 / pulsePeriod;

            const pulse = Math.sin(this.corePulsePhase) * 4 * this._excitementScale;
            const baseRadius = this.coreRadius * this._excitementScale;
            const radius = baseRadius + pulse;

            // Confusion flicker: random alpha jitter ±0.1
            const confusionJitter = (this.currentEmotion === 'confused')
                ? (Math.random() - 0.5) * 0.2
                : 0;

            const brightness = this.isSpeaking ? 1.3 : 1.0;
            const tokenBoost = this._tokenPulseAlpha; // 0..0.2 extra alpha

            // Outer glow ring
            ctx.beginPath();
            ctx.arc(this.centerX + this.microDriftX, this.centerY + this.microDriftY, radius + 3, 0, Math.PI * 2);
            ctx.strokeStyle = this.rgba(this.currentColor, Math.min(1, 0.4 * brightness + tokenBoost + confusionJitter));
            ctx.lineWidth = 2;
            ctx.stroke();

            // Multi-layer radial gradient
            const cx = this.centerX + this.microDriftX;
            const cy = this.centerY + this.microDriftY;
            const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
            gradient.addColorStop(0, this.rgba(this.currentColor, Math.min(1, 1.0 * brightness + tokenBoost + confusionJitter)));
            gradient.addColorStop(0.3, this.rgba(this.currentColor, Math.min(1, 0.8 * brightness + tokenBoost)));
            gradient.addColorStop(0.6, this.rgba(this.currentColor, Math.min(1, 0.4 * brightness + tokenBoost * 0.5)));
            gradient.addColorStop(1, this.rgba(this.currentColor, 0));

            ctx.beginPath();
            ctx.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();
        }

        renderIris(ctx) {
            // Blink: contract iris to 70% over blinkDuration frames then expand back
            let blinkScale = 1.0;
            if (this.isBlinking) {
                const half = this.blinkDuration / 2;
                const t = this.blinkFrame / this.blinkDuration; // 0..1
                // contract on first half, expand on second half
                blinkScale = t < 0.5
                    ? this.lerp(1.0, 0.3, t / 0.5)
                    : this.lerp(0.3, 1.0, (t - 0.5) / 0.5);
            }

            const irisRadius = 8 * blinkScale;
            const irisBrightness = this.attentionState === 'listening' ? 1.5 : 1.0;

            // Apply micro-drift to iris position as well
            const ix = this.irisX + this.microDriftX * 0.5;
            const iy = this.irisY + this.microDriftY * 0.5;

            // Iris glow
            const gradient = ctx.createRadialGradient(ix, iy, 0, ix, iy, irisRadius * 2);
            gradient.addColorStop(0, this.rgba(this.currentColor, 1.0 * irisBrightness));
            gradient.addColorStop(0.5, this.rgba(this.currentColor, 0.6 * irisBrightness));
            gradient.addColorStop(1, this.rgba(this.currentColor, 0));

            ctx.beginPath();
            ctx.arc(ix, iy, Math.max(0.5, irisRadius * 2), 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();

            // Iris core
            ctx.beginPath();
            ctx.arc(ix, iy, Math.max(0.5, irisRadius), 0, Math.PI * 2);
            ctx.fillStyle = this.rgba(this.currentColor, 1.0);
            ctx.fill();
        }

        renderRings(ctx) {
            const wobbleRad = this._confusionWobble * Math.PI / 180;
            for (const ring of this.rings) {
                ctx.save();
                ctx.translate(this.centerX + this.microDriftX, this.centerY + this.microDriftY);
                ctx.rotate(ring.rotation + wobbleRad);

                // Draw ellipse with tilt
                const tiltRad = ring.tilt * Math.PI / 180;
                ctx.scale(1, Math.cos(tiltRad));

                ctx.strokeStyle = this.rgba(this.currentColor, 0.3);
                ctx.lineWidth = ring.strokeWidth;

                if (ring.dashed) {
                    ctx.setLineDash([5, 10]);
                } else {
                    ctx.setLineDash([]);
                }

                ctx.beginPath();
                ctx.ellipse(0, 0, ring.radiusX, ring.radiusY, 0, 0, Math.PI * 2);
                ctx.stroke();

                // Draw dots
                for (const dot of ring.dots) {
                    const dotX = Math.cos(dot.angle) * ring.radiusX;
                    const dotY = Math.sin(dot.angle) * ring.radiusY;

                    ctx.beginPath();
                    ctx.arc(dotX, dotY, 2, 0, Math.PI * 2);
                    ctx.fillStyle = this.rgba(this.currentColor, 1.0);
                    ctx.fill();
                }

                ctx.restore();
            }

            ctx.setLineDash([]);
        }

        renderParticles(ctx) {
            for (const particle of this.particles) {
                const x = this.centerX + Math.cos(particle.angle) * particle.radius;
                const y = this.centerY + Math.sin(particle.angle) * particle.radius;

                // Glow
                const gradient = ctx.createRadialGradient(x, y, 0, x, y, particle.size * 2);
                gradient.addColorStop(0, this.rgba(this.currentColor, particle.opacity));
                gradient.addColorStop(1, this.rgba(this.currentColor, 0));

                ctx.beginPath();
                ctx.arc(x, y, particle.size * 2, 0, Math.PI * 2);
                ctx.fillStyle = gradient;
                ctx.fill();

                // Core
                ctx.beginPath();
                ctx.arc(x, y, particle.size, 0, Math.PI * 2);
                ctx.fillStyle = this.rgba(this.currentColor, particle.opacity);
                ctx.fill();
            }
        }

        renderWaveform(ctx) {
            const waveRadius = 65;
            const baseOpacity = this.waveformOpacity * 0.8;

            for (let i = 0; i < this.waveformBars.length; i++) {
                const bar = this.waveformBars[i];
                const angle = bar.angle;

                const x1 = this.centerX + Math.cos(angle) * waveRadius;
                const y1 = this.centerY + Math.sin(angle) * waveRadius;
                const x2 = this.centerX + Math.cos(angle) * (waveRadius + bar.height);
                const y2 = this.centerY + Math.sin(angle) * (waveRadius + bar.height);

                ctx.strokeStyle = this.rgba(this.currentColor, baseOpacity);
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(x1, y1);
                ctx.lineTo(x2, y2);
                ctx.stroke();

                // Tip glow
                ctx.beginPath();
                ctx.arc(x2, y2, 1.5, 0, Math.PI * 2);
                ctx.fillStyle = this.rgba(this.currentColor, baseOpacity * 1.5);
                ctx.fill();
            }
        }

        renderScanLine(ctx, w, h) {
            const y = this.scanLineY * h;

            const gradient = ctx.createLinearGradient(0, y - 10, 0, y + 10);
            gradient.addColorStop(0, this.rgba(this.currentColor, 0));
            gradient.addColorStop(0.5, this.rgba(this.currentColor, 0.08));
            gradient.addColorStop(1, this.rgba(this.currentColor, 0));

            ctx.fillStyle = gradient;
            ctx.fillRect(0, y - 10, w, 20);
        }

        renderDataStreams(ctx, w, h) {
            const dataOpacity = this.isSpeaking ? 0.12 : 0.06;
            ctx.font = '8px monospace';
            ctx.fillStyle = this.rgba(this.currentColor, dataOpacity);

            for (const stream of this.dataStreams) {
                const x = stream.x * w;

                for (let i = 0; i < stream.chars.length; i++) {
                    const y = (i * 12 - stream.offset) % (h + 50);
                    if (y > -10 && y < h + 10) {
                        ctx.fillText(stream.chars[i], x, y);
                    }
                }
            }
        }

        renderLoadingRing(ctx) {
            const radius = this.coreRadius + 12;
            const arcLength = Math.PI * 0.5; // 90 degrees

            ctx.strokeStyle = this.rgba(this.currentColor, this.loadingRingOpacity);
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(
                this.centerX,
                this.centerY,
                radius,
                this.loadingRingAngle,
                this.loadingRingAngle + arcLength
            );
            ctx.stroke();
        }

        renderStatusLabel(ctx) {
            let label = 'NEURAL LINK ACTIVE';
            if (this.isSpeaking) {
                label = 'TRANSMITTING...';
            } else if (this.isThinking) {
                label = 'PROCESSING...';
            }

            ctx.font = '10px monospace';
            ctx.letterSpacing = '2px';
            ctx.textAlign = 'center';
            ctx.fillStyle = this.rgba(this.currentColor, 0.6);
            ctx.fillText(label, this.centerX, this.centerY + this.coreRadius + 35);
        }

        // ========================================================================
        // PUBLIC API
        // ========================================================================

        setEmotion(emotion, intensity = 1.0) {
            const normalizedEmotion = emotion.toLowerCase();
            const color = MOOD_COLORS[normalizedEmotion] || MOOD_COLORS.neutral;

            // Store start color for proper interpolation
            this.startColor = { ...this.currentColor };
            this.targetColor = { ...color };
            this.colorTransition = 0;
            this.currentEmotion = normalizedEmotion;
        }

        setSpeaking(active, text = '') {
            this.isSpeaking = active;
        }

        setState(state) {
            this.currentState = state;
        }

        onStreamToken() {
            this._tokenPulseAlpha = 0.2;
        }

        setAttentionState(state) {
            // 'focused', 'listening', 'alert', 'relaxed'
            this.attentionState = state;

            if (state === 'listening') {
                this.coreRadius = 55; // Expand slightly
            } else {
                this.coreRadius = 50;
            }
        }

        updateFromChatMessage(role, text) {
            if (role === 'user') {
                this.setAttentionState('listening');
                this.isSpeaking = false;
            } else if (role === 'assistant') {
                this.setAttentionState('relaxed');
                this.isSpeaking = true;

                // Auto-stop speaking after estimated reading time
                if (this._speakTimeout) clearTimeout(this._speakTimeout);
                const wordCount = (text || '').split(/\s+/).length;
                const readTimeMs = Math.max(2000, wordCount * 200);
                this._speakTimeout = setTimeout(() => {
                    if (this.isSpeaking) {
                        this.isSpeaking = false;
                    }
                }, readTimeMs);
            }
        }

        processPhonemes(data) {
            // Modulate waveform based on phoneme intensity
            if (data && data.phonemes && Array.isArray(data.phonemes)) {
                for (let i = 0; i < Math.min(data.phonemes.length, this.waveformBars.length); i++) {
                    const phoneme = data.phonemes[i];
                    const intensity = phoneme.intensity || 0.5;
                    this.waveformBars[i].targetHeight = 3 + intensity * 17;
                }
            }
        }

        getDiagnostics() {
            return {
                modelLoaded: true,
                renderer: 'Canvas2D',
                fps: this.fps,
                currentEmotion: this.currentEmotion,
                isSpeaking: this.isSpeaking,
                isThinking: this.isThinking,
                attentionState: this.attentionState,
                particleCount: this.particles.length,
                waveformActive: this.waveformOpacity > 0.1,
                verdict: 'ANIMATING'
            };
        }

        destroy() {
            if (this.animationFrame) {
                cancelAnimationFrame(this.animationFrame);
                this.animationFrame = null;
            }
            if (this._speakTimeout) {
                clearTimeout(this._speakTimeout);
                this._speakTimeout = null;
            }

            this.canvas.removeEventListener('mousemove', this.boundMouseMove);
            window.removeEventListener('resize', this.boundResize);
        }

        // ========================================================================
        // UTILITIES
        // ========================================================================

        lerp(a, b, t) {
            return a + (b - a) * t;
        }

        rgba(color, alpha) {
            return `rgba(${Math.round(color.r)}, ${Math.round(color.g)}, ${Math.round(color.b)}, ${alpha})`;
        }
    }

    // ============================================================================
    // INITIALIZATION
    // ============================================================================

    function init() {
        const canvas = document.getElementById('avatar-canvas');
        if (!canvas) {
            console.error('[HolographicAvatar] Canvas element #avatar-canvas not found');
            return;
        }

        const avatar = new HolographicAvatar(canvas);

        // Expose global API
        window.avatarRenderer = {
            setEmotion: avatar.setEmotion.bind(avatar),
            setSpeaking: avatar.setSpeaking.bind(avatar),
            setState: avatar.setState.bind(avatar),
            onStreamToken: avatar.onStreamToken.bind(avatar),
            setAttentionState: avatar.setAttentionState.bind(avatar),
            updateFromChatMessage: avatar.updateFromChatMessage.bind(avatar),
            processPhonemes: avatar.processPhonemes.bind(avatar),
            getDiagnostics: avatar.getDiagnostics.bind(avatar),
            destroy: avatar.destroy.bind(avatar)
        };

        console.log('[HolographicAvatar] Initialized - Canvas2D procedural hologram');
    }

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
