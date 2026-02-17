/**
 * Neural Network Particle Background
 * Animated particle system for Sentient Core web chat interface
 * Optimized for Jetson Orin Nano (30fps cap)
 */

(function() {
    'use strict';

    // Configuration
    const CONFIG = {
        particleCount: 50,
        connectionDistance: 120,
        baseSpeed: 0.3,
        pulseSpeed: 1.5,
        pulseDuration: 500,
        fps: 30,
        canvasOpacity: 0.4,
        colors: {
            cyan: '#00ffff',
            magenta: '#ff00ff',
            white: '#ffffff'
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
        canvas = document.createElement('canvas');
        canvas.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 1;
            pointer-events: none;
            opacity: ${CONFIG.canvasOpacity};
        `;
        document.body.prepend(canvas);
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
        neutral:    { cyan: '#00ffff', magenta: '#ff00ff', white: '#ffffff' },
        joy:        { cyan: '#ffff00', magenta: '#ff8800', white: '#ffffcc' },
        curiosity:  { cyan: '#aa66ff', magenta: '#6644ff', white: '#ddccff' },
        affection:  { cyan: '#ff66aa', magenta: '#ff3388', white: '#ffccdd' },
        sadness:    { cyan: '#4488cc', magenta: '#335599', white: '#aabbcc' },
        anger:      { cyan: '#ff3333', magenta: '#cc0000', white: '#ffaaaa' },
        fear:       { cyan: '#ff6600', magenta: '#cc4400', white: '#ffcc88' },
        surprise:   { cyan: '#00ffaa', magenta: '#00cc88', white: '#ccffee' },
        confidence: { cyan: '#00ff66', magenta: '#00cc44', white: '#ccffcc' },
        playful:    { cyan: '#ff00ff', magenta: '#ff66ff', white: '#ffccff' },
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
