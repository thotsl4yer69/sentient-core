/**
 * Neural Network Particle Background
 * Animated particle system for Sentient Core web chat interface
 * Optimized for Jetson Orin Nano (30fps cap)
 */

(function() {
    'use strict';

    // Configuration
    const CONFIG = {
        particleCount: 25,
        connectionDistance: 100,
        baseSpeed: 0.2,
        pulseSpeed: 1.5,
        pulseDuration: 500,
        fps: 30,
        canvasOpacity: 0.25,
        colors: {
            cyan: '#00d4ff',
            magenta: '#a855f7',
            white: '#e2e8f0'
        }
    };

    let canvas, ctx;
    let particles = [];
    let lastFrameTime = 0;
    let frameInterval = 1000 / CONFIG.fps;
    let isPulsing = false;
    let pulseStartTime = 0;

    class Particle {
        constructor() {
            this.x = Math.random() * window.innerWidth;
            this.y = Math.random() * window.innerHeight;
            this.vx = (Math.random() - 0.5) * CONFIG.baseSpeed;
            this.vy = (Math.random() - 0.5) * CONFIG.baseSpeed;
            this.radius = 1 + Math.random() * 2; // 1-3px

            // Color distribution: 50% cyan, 30% magenta, 20% white
            const rand = Math.random();
            if (rand < 0.5) {
                this.color = CONFIG.colors.cyan;
            } else if (rand < 0.8) {
                this.color = CONFIG.colors.magenta;
            } else {
                this.color = CONFIG.colors.white;
            }
        }

        update(speedMultiplier = 1) {
            // Update position
            this.x += this.vx * speedMultiplier;
            this.y += this.vy * speedMultiplier;

            // Wrap around edges
            if (this.x < 0) this.x = window.innerWidth;
            if (this.x > window.innerWidth) this.x = 0;
            if (this.y < 0) this.y = window.innerHeight;
            if (this.y > window.innerHeight) this.y = 0;
        }

        draw(brightnessMultiplier = 1) {
            // Create radial gradient for glow effect
            const gradient = ctx.createRadialGradient(
                this.x, this.y, 0,
                this.x, this.y, this.radius * 3
            );

            // Parse color and apply brightness multiplier
            const alpha = 0.8 * brightnessMultiplier;
            gradient.addColorStop(0, this.color + Math.floor(alpha * 255).toString(16).padStart(2, '0'));
            gradient.addColorStop(0.5, this.color + Math.floor(alpha * 128).toString(16).padStart(2, '0'));
            gradient.addColorStop(1, this.color + '00');

            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.radius * 3, 0, Math.PI * 2);
            ctx.fill();

            // Draw solid center
            ctx.fillStyle = this.color;
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    function initCanvas() {
        canvas = document.getElementById('neural-bg');
        if (!canvas) {
            canvas = document.createElement('canvas');
            canvas.id = 'neural-bg';
            document.body.prepend(canvas);
        }
        // Styles are in CSS now, but ensure opacity override
        canvas.style.opacity = CONFIG.canvasOpacity;
        ctx = canvas.getContext('2d');

        resizeCanvas();
    }

    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function initParticles() {
        particles = [];
        for (let i = 0; i < CONFIG.particleCount; i++) {
            particles.push(new Particle());
        }
    }

    function drawConnections(brightnessMultiplier = 1) {
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < CONFIG.connectionDistance) {
                    // Opacity inversely proportional to distance
                    const opacity = (1 - distance / CONFIG.connectionDistance) * 0.3 * brightnessMultiplier;
                    ctx.strokeStyle = `${CONFIG.colors.cyan}${Math.floor(opacity * 255).toString(16).padStart(2, '0')}`;
                    ctx.lineWidth = 0.5;
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.stroke();
                }
            }
        }
    }

    function animate(currentTime) {
        requestAnimationFrame(animate);

        // Throttle to target FPS
        const elapsed = currentTime - lastFrameTime;
        if (elapsed < frameInterval) return;
        lastFrameTime = currentTime - (elapsed % frameInterval);

        // Calculate pulse effects
        let speedMultiplier = 1;
        let brightnessMultiplier = 1;

        if (isPulsing) {
            const pulseElapsed = currentTime - pulseStartTime;
            if (pulseElapsed < CONFIG.pulseDuration) {
                // Ease out effect
                const progress = pulseElapsed / CONFIG.pulseDuration;
                const easeOut = 1 - Math.pow(1 - progress, 3);
                speedMultiplier = 1 + (CONFIG.pulseSpeed - 1) * (1 - easeOut);
                brightnessMultiplier = 1 + 0.5 * (1 - easeOut);
            } else {
                isPulsing = false;
            }
        }

        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Update and draw
        drawConnections(brightnessMultiplier);
        particles.forEach(particle => {
            particle.update(speedMultiplier);
            particle.draw(brightnessMultiplier);
        });
    }

    function neuralPulse() {
        isPulsing = true;
        pulseStartTime = performance.now();
    }

    // Initialize on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        initCanvas();
        initParticles();
        requestAnimationFrame(animate);
    });

    // Handle window resize
    window.addEventListener('resize', () => {
        resizeCanvas();
        // Optionally reinitialize particles to maintain density
        // Skipping for performance - existing particles will naturally spread
    });

    // Export global function for message pulse effect
    window.neuralPulse = neuralPulse;

    // Mood-reactive color presets
    const MOOD_COLORS = {
        neutral:    { cyan: '#00d4ff', magenta: '#a855f7', white: '#e2e8f0' },
        joy:        { cyan: '#22c55e', magenta: '#86efac', white: '#f0fdf4' },
        curiosity:  { cyan: '#a855f7', magenta: '#7c3aed', white: '#ede9fe' },
        affection:  { cyan: '#ec4899', magenta: '#f472b6', white: '#fce7f3' },
        sadness:    { cyan: '#3b82f6', magenta: '#6366f1', white: '#dbeafe' },
        anger:      { cyan: '#ef4444', magenta: '#dc2626', white: '#fee2e2' },
        fear:       { cyan: '#f59e0b', magenta: '#d97706', white: '#fef3c7' },
        surprise:   { cyan: '#06b6d4', magenta: '#0891b2', white: '#cffafe' },
        confidence: { cyan: '#22c55e', magenta: '#16a34a', white: '#dcfce7' },
        playful:    { cyan: '#d946ef', magenta: '#c026d3', white: '#fae8ff' },
    };

    window.setNeuralMood = function(emotion) {
        const colors = MOOD_COLORS[emotion] || MOOD_COLORS.neutral;
        CONFIG.colors.cyan = colors.cyan;
        CONFIG.colors.magenta = colors.magenta;
        CONFIG.colors.white = colors.white;
        // Gradually recolor existing particles
        particles.forEach(p => {
            const rand = Math.random();
            if (rand < 0.5) p.color = colors.cyan;
            else if (rand < 0.8) p.color = colors.magenta;
            else p.color = colors.white;
        });
    };

})();
